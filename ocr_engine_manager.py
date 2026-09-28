# ocr_engine_manager.py
# Maafushivaru - Document Processing Hub
#
# Central place that knows
#   * which OCR engines exist (local/offline engines + the online OCR.space API),
#   * whether each one is actually usable on THIS machine,
#   * how to install the missing Python packages / system binaries,
#   * the persisted "first-run setup already finished" state.
#
# Design rules
#   * Import-safe: this module NEVER imports a heavy OCR engine at import time
#     (paddleocr / easyocr / torch can take 10+ seconds and pull in a lot of
#     memory). Availability is checked with importlib.util.find_spec, and the
#     real import happens inside a worker thread only when the engine is used.
#   * Everything is optional: if this file is missing the application still
#     runs and falls back to the legacy behaviour (see maafushivaru_hub.py).
#   * Installers stream their output line by line so the UI can show live
#     progress instead of freezing.

from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import json
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

LogFn = Callable[[str], None]


def _noop_log(_msg: str) -> None:  # pragma: no cover - trivial
    pass


# ---------------------------------------------------------------------------
# ENGINE CATALOG
# ---------------------------------------------------------------------------
# kind:
#   "offline" -> runs on this PC, needs local packages/binaries
#   "online"  -> cloud API, needs an internet connection + API key
#
# packages   : pip packages required
# modules    : python modules that must be importable for the engine to work
# system     : human readable description of the non-pip requirement
# size_mb    : rough download size (used to warn the user before installing)
ENGINE_SPECS: "Dict[str, Dict[str, object]]" = {
    "tesseract": {
        "key": "tesseract",
        "label": "Tesseract OCR",
        "short": "Tesseract",
        "kind": "offline",
        "modules": ["pytesseract"],
        "packages": ["pytesseract"],
        "system": "Tesseract program (tesseract.exe / tesseract binary)",
        "size_mb": 60,
        "recommended": True,
        "description": (
            "Built-in local engine. Fast, no internet needed and no upload limit. "
            "Needs the Tesseract program installed on this PC."
        ),
    },
    "paddleocr": {
        "key": "paddleocr",
        "label": "PaddleOCR",
        "short": "PaddleOCR",
        "kind": "offline",
        "modules": ["paddleocr", "paddle"],
        "packages": ["paddlepaddle", "paddleocr"],
        "system": "Dedicated venv at C:\\PaddleOCR\\venv (configurable)",
        "size_mb": 500,
        "recommended": False,
        "description": (
            "Local deep-learning engine. Very good on small printed text; the "
            "first run downloads its language models, so it needs internet once."
        ),
    },
    "easyocr": {
        "key": "easyocr",
        "label": "EasyOCR",
        "short": "EasyOCR",
        "kind": "offline",
        "modules": ["easyocr", "torch"],
        "packages": ["easyocr"],
        "system": "None (installs PyTorch automatically - large download)",
        "size_mb": 2500,
        "recommended": False,
        "description": (
            "Local deep-learning engine (PyTorch). Accurate but heavy: the "
            "PyTorch download is ~2 GB and the first run fetches its models."
        ),
    },
    "ocr_space": {
        "key": "ocr_space",
        "label": "OCR.space API",
        "short": "OCR.space",
        "kind": "online",
        "modules": ["requests"],
        "packages": ["requests"],
        "system": "Internet connection + OCR.space API key",
        "size_mb": 0,
        "recommended": True,
        "description": (
            "Cloud OCR used by the ONLINE mode. Three API engines are "
            "selectable (1 Default, 2 Enhanced, 3 Extra Accurate)."
        ),
    },
}

# Offline engines in the order they should be offered in the interface.
ENGINE_ORDER: "List[str]" = ["tesseract", "paddleocr", "easyocr"]

# OCR.space engine numbers and their friendly names.
OCR_SPACE_ENGINES: "List[Tuple[int, str]]" = [
    (1, "Engine 1 (Default)"),
    (2, "Engine 2 (Enhanced)"),
    (3, "Engine 3 (Extra Accurate)"),
]

ONLINE_ENGINE_KEY = "ocr_space"

# Core (non-OCR) dependencies the application itself needs.
CORE_DEPENDENCIES: "List[Tuple[str, str, str, bool]]" = [
    # (import name, pip name, label, required)
    ("pymupdf", "PyMuPDF", "PyMuPDF (PDF rendering)", True),
    ("cv2", "opencv-python", "OpenCV (image preprocessing)", True),
    ("numpy", "numpy", "NumPy (numeric core)", True),
    ("PIL", "Pillow", "Pillow (image handling / previews)", True),
    ("pytesseract", "pytesseract", "pytesseract (Tesseract wrapper)", True),
    ("openpyxl", "openpyxl", "openpyxl (Excel export)", False),
    ("rapidfuzz", "rapidfuzz", "rapidfuzz (fast fuzzy matching)", False),
    ("watchdog", "watchdog", "watchdog (auto-ingest folder watcher)", False),
    ("plyer", "plyer", "plyer (desktop notifications)", False),
]

# `fitz` is the legacy import name of PyMuPDF; both work.
_MODULE_ALIASES = {"pymupdf": ("pymupdf", "fitz"), "cv2": ("cv2",), "PIL": ("PIL",)}

_MODULE_CACHE: "Dict[str, bool]" = {}
_BINARY_CACHE: "Dict[str, Optional[str]]" = {}
_CACHE_LOCK = threading.Lock()

# PaddleOCR is intentionally isolated from the application's Python runtime.
# The default Windows location is C:\PaddleOCR\venv; users can change the base
# folder in config.json later without changing any code.
_RUNTIME_CONFIG: dict = {}
_PADDLE_PATHS_ADDED = False


def configure_runtime(cfg: Optional[dict]) -> None:
    """Provide app config and expose configured venv packages to this process."""
    global _RUNTIME_CONFIG, _PADDLE_PATHS_ADDED
    _RUNTIME_CONFIG = cfg if isinstance(cfg, dict) else {}
    _PADDLE_PATHS_ADDED = False
    if paddleocr_venv_ready():
        _add_paddle_site_packages_to_path()
        invalidate_cache()


def paddleocr_base_dir(cfg: Optional[dict] = None) -> str:
    """Return the user-facing PaddleOCR folder, not the venv itself."""
    c = cfg if isinstance(cfg, dict) else _RUNTIME_CONFIG
    settings = (c or {}).setdefault("app_settings", {})
    configured = str(settings.get("paddleocr_install_dir", "") or "").strip()
    if configured:
        return os.path.expandvars(os.path.expanduser(configured))
    if platform.system() == "Windows":
        return r"C:\PaddleOCR"
    return os.path.join(os.path.expanduser("~"), "PaddleOCR")


def paddleocr_venv_dir(cfg: Optional[dict] = None) -> str:
    """Return the dedicated PaddleOCR virtual-environment directory."""
    c = cfg if isinstance(cfg, dict) else _RUNTIME_CONFIG
    settings = (c or {}).setdefault("app_settings", {})
    configured = str(settings.get("paddleocr_venv_dir", "") or "").strip()
    return (os.path.expandvars(os.path.expanduser(configured)) if configured
            else os.path.join(paddleocr_base_dir(c), "venv"))


def paddleocr_python(cfg: Optional[dict] = None) -> str:
    """Return the Python executable inside PaddleOCR's dedicated venv."""
    venv = paddleocr_venv_dir(cfg)
    if platform.system() == "Windows":
        return os.path.join(venv, "Scripts", "python.exe")
    return os.path.join(venv, "bin", "python")


def _paddle_site_packages(cfg: Optional[dict] = None) -> List[str]:
    venv = paddleocr_venv_dir(cfg)
    if platform.system() == "Windows":
        candidates = [os.path.join(venv, "Lib", "site-packages")]
    else:
        candidates = [str(p) for p in Path(venv, "lib").glob("python*/site-packages")]
    return [p for p in candidates if os.path.isdir(p)]


def paddleocr_venv_version(cfg: Optional[dict] = None) -> "Optional[Tuple[int, int]]":
    """(major, minor) the PaddleOCR venv was built with, or None if unknown."""
    import re
    cfg_file = os.path.join(paddleocr_venv_dir(cfg), "pyvenv.cfg")
    try:
        with open(cfg_file, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                m = re.match(r"\s*version(?:_info)?\s*=\s*(\d+)\.(\d+)", line, re.I)
                if m:
                    return int(m.group(1)), int(m.group(2))
    except Exception:
        pass
    return None


def paddleocr_venv_matches_runtime(cfg: Optional[dict] = None) -> bool:
    """False when the venv was built with a different Python than the one running.

    Paddle contains compiled code, so a venv from another Python version cannot
    be loaded into this process.
    """
    ver = paddleocr_venv_version(cfg)
    return ver is None or tuple(ver) == tuple(sys.version_info[:2])


def _add_paddle_site_packages_to_path(cfg: Optional[dict] = None) -> None:
    global _PADDLE_PATHS_ADDED
    if _PADDLE_PATHS_ADDED:
        return
    if not paddleocr_venv_matches_runtime(cfg):
        return
    for site in reversed(_paddle_site_packages(cfg)):
        if site not in sys.path:
            sys.path.insert(0, site)
    _PADDLE_PATHS_ADDED = True


def paddleocr_venv_ready(cfg: Optional[dict] = None) -> bool:
    """True when the dedicated venv exists and has PaddleOCR plus Paddle."""
    if not os.path.isfile(paddleocr_python(cfg)):
        return False
    if not paddleocr_venv_matches_runtime(cfg):
        return False
    _add_paddle_site_packages_to_path(cfg)
    return module_available("paddleocr") and module_available("paddle")


def paddleocr_location_text(cfg: Optional[dict] = None) -> str:
    return paddleocr_venv_dir(cfg)


# ---------------------------------------------------------------------------
# AVAILABILITY CHECKS
# ---------------------------------------------------------------------------
def module_available(module: str) -> bool:
    """True when `module` can be imported without importing it."""
    if module in ("paddleocr", "paddle"):
        _add_paddle_site_packages_to_path()
    names = _MODULE_ALIASES.get(module, (module,))
    for name in names:
        with _CACHE_LOCK:
            cached = _MODULE_CACHE.get(name)
        if cached is True:
            return True
    for name in names:
        found = False
        try:
            found = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError, AttributeError):
            found = False
        with _CACHE_LOCK:
            _MODULE_CACHE[name] = found
        if found:
            return True
    return False


def invalidate_cache() -> None:
    """Forget cached lookups (called after an install finishes)."""
    with _CACHE_LOCK:
        _MODULE_CACHE.clear()
        _BINARY_CACHE.clear()
    try:
        importlib.invalidate_caches()
    except Exception:
        pass


def tesseract_binary() -> Optional[str]:
    """Full path of the Tesseract program, or None when it is not installed.

    Checks the same places maafushivaru_hub._configure_tesseract uses so both
    agree about whether Tesseract is usable.
    """
    with _CACHE_LOCK:
        if "tesseract" in _BINARY_CACHE:
            return _BINARY_CACHE["tesseract"]

    candidates: "List[str]" = ["tesseract", "/usr/bin/tesseract", "/usr/local/bin/tesseract",
                               "/opt/homebrew/bin/tesseract"]
    if platform.system() == "Windows":
        candidates += [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ]
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(os.path.join(local, "Programs", "Tesseract-OCR", "tesseract.exe"))
    # An explicit path in config.json wins (handled by the caller); here we only
    # probe PATH + the usual install locations.
    found: Optional[str] = None
    for cand in candidates:
        try:
            if os.path.isfile(cand):
                found = cand
                break
            which = shutil.which(cand)
            if which:
                found = which
                break
        except Exception:
            continue

    with _CACHE_LOCK:
        _BINARY_CACHE["tesseract"] = found
    return found


def tesseract_version() -> str:
    """Return the Tesseract version string, or '' when it cannot be started."""
    exe = tesseract_binary()
    if not exe:
        return ""
    try:
        out = subprocess.run(
            [exe, "--version"],
            capture_output=True, text=True, timeout=15,
            creationflags=_creation_flags(),
        )
        first = (out.stdout or out.stderr or "").strip().splitlines()
        return first[0] if first else ""
    except Exception:
        return ""


def engine_installed(key: str) -> bool:
    """True when the engine can actually be used right now.

    Tesseract needs BOTH the python wrapper and the program; the deep-learning
    engines need their python packages; OCR.space only needs `requests`.
    """
    spec = ENGINE_SPECS.get(key)
    if not spec:
        return False
    for mod in spec.get("modules", []):  # type: ignore[union-attr]
        if not module_available(str(mod)):
            return False
    if key == "tesseract":
        return tesseract_binary() is not None
    return True


def engine_status_text(key: str) -> str:
    """Short, human readable status for the settings screen."""
    spec = ENGINE_SPECS.get(key)
    if not spec:
        return "Unknown engine"
    if engine_installed(key):
        if key == "tesseract":
            ver = tesseract_version()
            return f"Installed · {ver}" if ver else "Installed"
        if key == "paddleocr":
            return f"Installed · venv: {paddleocr_location_text()}"
        return "Installed"
    missing = [str(m) for m in spec.get("modules", []) if not module_available(str(m))]  # type: ignore[union-attr]
    if key == "tesseract" and not missing:
        return "Program not found (tesseract.exe)"
    if key == "paddleocr" and not os.path.isfile(paddleocr_python()):
        return f"Not installed · venv missing: {paddleocr_location_text()}"
    if key == "paddleocr" and not paddleocr_venv_matches_runtime():
        v = paddleocr_venv_version() or ("?", "?")
        return (f"Not usable · venv is Python {v[0]}.{v[1]} but the app runs on "
                f"{sys.version_info[0]}.{sys.version_info[1]} - press Install to rebuild it")
    if missing:
        return "Not installed — missing: " + ", ".join(missing)
    return "Not installed"


def missing_packages(key: str) -> "List[str]":
    """pip packages that still have to be installed for this engine."""
    spec = ENGINE_SPECS.get(key, {})
    packages: "List[str]" = []
    for pkg in spec.get("packages", []):  # type: ignore[union-attr]
        packages.append(str(pkg))
    return packages


def engine_label(key: str) -> str:
    return str(ENGINE_SPECS.get(key, {}).get("label", key))  # type: ignore[arg-type]


def engine_short(key: str) -> str:
    return str(ENGINE_SPECS.get(key, {}).get("short", key))  # type: ignore[arg-type]


def engine_kind(key: str) -> str:
    return str(ENGINE_SPECS.get(key, {}).get("kind", "offline"))  # type: ignore[arg-type]


def offline_engine_keys() -> "List[str]":
    return [k for k in ENGINE_ORDER if k in ENGINE_SPECS]


def installed_offline_engines() -> "List[str]":
    return [k for k in offline_engine_keys() if engine_installed(k)]


# ---------------------------------------------------------------------------
# CHOICE LABELS (shared by the GRN Dispatch combo and the debug window)
# ---------------------------------------------------------------------------
def offline_choice_label(key: str, with_status: bool = False) -> str:
    label = f"Offline — {engine_short(key)} (Local)"
    if with_status and not engine_installed(key):
        label += "  ·  not installed"
    return label


def online_choice_label(num: int) -> str:
    name = dict(OCR_SPACE_ENGINES).get(int(num), f"Engine {num}")
    return f"Online — OCR.space {name}"


def choice_labels(with_status: bool = True) -> "List[str]":
    """Every selectable engine, ONLINE ones first (same order as before)."""
    labels = [online_choice_label(num) for num, _n in OCR_SPACE_ENGINES]
    labels += [offline_choice_label(k, with_status=with_status) for k in offline_engine_keys()]
    return labels


def parse_choice(label: str) -> "Tuple[str, object]":
    """Map a combo label back to ('online', engine_num) or ('offline', key).

    Unknown labels fall back to ('offline', 'tesseract') so a stale saved value
    can never crash the interface.
    """
    text = (label or "").strip()
    if text.lower().startswith("online"):
        for num, name in OCR_SPACE_ENGINES:
            if f"engine {num}" in text.lower() or name.lower() in text.lower():
                return ("online", num)
        return ("online", 2)
    for key in offline_engine_keys():
        if engine_short(key).lower() in text.lower():
            return ("offline", key)
    return ("offline", "tesseract")


def label_for_spec(kind: str, value: object) -> str:
    if kind == "online":
        try:
            return online_choice_label(int(value))  # type: ignore[arg-type]
        except Exception:
            return online_choice_label(2)
    return offline_choice_label(str(value))


# ---------------------------------------------------------------------------
# COMMAND HELPERS
# ---------------------------------------------------------------------------
def _creation_flags() -> int:
    """Hide the console window on Windows when spawning pip."""
    if platform.system() == "Windows":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def pip_base_command() -> "Optional[List[str]]":
    """Best pip invocation for this installation, or None when unavailable."""
    if not getattr(sys, "frozen", False):
        return [sys.executable, "-m", "pip"]
    # Frozen (PyInstaller) build: use whatever Python is on PATH.
    for cand in ("python", "python3", "py"):
        exe = shutil.which(cand)
        if exe:
            return [exe, "-m", "pip"]
    return None


def run_streaming(cmd: "Sequence[str]", log: LogFn = _noop_log,
                  cancel: "Optional[threading.Event]" = None,
                  timeout: float = 3600.0) -> "Tuple[int, str]":
    """Run a command and stream its output into `log`. Returns (rc, output)."""
    log(f"$ {' '.join(str(c) for c in cmd)}")
    try:
        proc = subprocess.Popen(
            list(cmd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            creationflags=_creation_flags(),
        )
    except Exception as exc:
        log(f"!! Could not start the command: {exc}")
        return 1, str(exc)

    lines: "List[str]" = []
    started = time.time()
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            if cancel is not None and cancel.is_set():
                try:
                    proc.kill()
                except Exception:
                    pass
                log("!! Cancelled by the user.")
                return 130, "\n".join(lines)
            line = raw.rstrip("\n")
            if line.strip():
                lines.append(line)
                log(line)
            if time.time() - started > timeout:
                try:
                    proc.kill()
                except Exception:
                    pass
                log("!! Timed out.")
                return 124, "\n".join(lines)
        proc.wait(timeout=30)
    except Exception as exc:
        log(f"!! Command error: {exc}")
        try:
            proc.kill()
        except Exception:
            pass
        return 1, "\n".join(lines)
    return proc.returncode or 0, "\n".join(lines)


# ---------------------------------------------------------------------------
# INSTALLERS
# ---------------------------------------------------------------------------
def _create_paddle_venv(log: LogFn = _noop_log,
                        cancel: Optional[threading.Event] = None) -> Tuple[bool, str]:
    r"""Create C:\PaddleOCR\venv, or the configured equivalent."""
    base_dir = paddleocr_base_dir()
    venv_dir = paddleocr_venv_dir()
    try:
        os.makedirs(base_dir, exist_ok=True)
    except Exception as exc:
        return False, f"Could not create PaddleOCR folder {base_dir}: {exc}"
    py = paddleocr_python()
    if os.path.isfile(py) and not paddleocr_venv_matches_runtime():
        # Old venv built by another Python: keep it as a backup, build a new one.
        v = paddleocr_venv_version() or ("?", "?")
        backup = f"{venv_dir}_py{v[0]}{v[1]}_backup"
        n = 1
        while os.path.exists(backup):
            n += 1
            backup = f"{venv_dir}_py{v[0]}{v[1]}_backup{n}"
        log(f"Existing PaddleOCR venv uses Python {v[0]}.{v[1]}, the app runs on "
            f"{sys.version_info[0]}.{sys.version_info[1]}. Moving it to {backup}")
        try:
            os.rename(venv_dir, backup)
        except Exception as exc:
            return False, f"Could not move the old PaddleOCR venv ({venv_dir}): {exc}"
        invalidate_cache()
    if not os.path.isfile(py):
        log(f"Creating dedicated PaddleOCR virtual environment: {venv_dir} "
            f"(Python {sys.version_info[0]}.{sys.version_info[1]}, {sys.executable})")
        rc, out = run_streaming(
            [sys.executable, "-m", "venv", venv_dir],
            log=log, cancel=cancel, timeout=900,
        )
        if rc != 0 or not os.path.isfile(py):
            return False, f"Could not create PaddleOCR venv at {venv_dir}.\n{out[-1200:]}"
    return True, venv_dir


def install_paddleocr(log: LogFn = _noop_log,
                      cancel: Optional[threading.Event] = None) -> Tuple[bool, str]:
    """Install PaddlePaddle and PaddleOCR only inside the dedicated venv."""
    settings = _RUNTIME_CONFIG.setdefault("app_settings", {})
    settings.setdefault("paddleocr_install_dir", paddleocr_base_dir())
    settings.setdefault("paddleocr_venv_dir", paddleocr_venv_dir())
    ok, location = _create_paddle_venv(log=log, cancel=cancel)
    if not ok:
        return False, location
    py = paddleocr_python()
    cmd = [py, "-m", "pip", "install", "--upgrade", "--disable-pip-version-check",
           "--no-input", "paddlepaddle", "paddleocr"]
    log(f"Installing PaddleOCR into {location}")
    rc, out = run_streaming(cmd, log=log, cancel=cancel, timeout=3600)
    if rc != 0:
        return False, f"PaddleOCR installation failed in {location}.\n{out[-1800:]}"
    _add_paddle_site_packages_to_path()
    invalidate_cache()
    if engine_installed("paddleocr"):
        return True, f"PaddleOCR installed in {location}."
    return False, (
        f"PaddleOCR venv was created at {location}, but the engine is not importable yet. "
        "Restart the app and try again."
    )


def install_packages(packages: "Sequence[str]", log: LogFn = _noop_log,
                     cancel: "Optional[threading.Event]" = None) -> "Tuple[bool, str]":
    """pip-install `packages`, streaming output. Returns (ok, message)."""
    pkgs = [p for p in packages if p]
    if not pkgs:
        return True, "Nothing to install."

    base = pip_base_command()
    if not base:
        msg = ("Python/pip could not be found on this machine. Install Python 3 "
               "from python.org (tick 'Add python.exe to PATH'), then install "
               f"manually: pip install {' '.join(pkgs)}")
        log("!! " + msg)
        return False, msg

    cmd = base + ["install", "--upgrade", "--disable-pip-version-check", "--no-input", *pkgs]
    log(f"Installing: {', '.join(pkgs)}")
    rc, out = run_streaming(cmd, log=log, cancel=cancel)
    if rc != 0:
        # A very common cause on Windows is a missing C++ build tool; retry with
        # the pure-python/binary-only wheel flag before giving up.
        log("First attempt failed — retrying with --only-binary=:all: …")
        cmd2 = base + ["install", "--upgrade", "--only-binary=:all:",
                       "--disable-pip-version-check", "--no-input", *pkgs]
        rc2, out2 = run_streaming(cmd2, log=log, cancel=cancel)
        if rc2 != 0:
            return False, (
                f"pip failed (exit code {rc2}). Install manually:\n"
                f"  pip install {' '.join(pkgs)}\n\n"
                f"Last output:\n{out2[-1500:]}"
            )
    invalidate_cache()
    return True, "Packages installed."


def install_tesseract_program(log: LogFn = _noop_log,
                              cancel: "Optional[threading.Event]" = None) -> "Tuple[bool, str]":
    """Install the Tesseract PROGRAM (not just the pytesseract wrapper)."""
    system = platform.system()
    if system == "Windows":
        winget = shutil.which("winget")
        if winget:
            rc, _ = run_streaming(
                [winget, "install", "-e", "--id", "UB-Mannheim.TesseractOCR",
                 "--accept-package-agreements", "--accept-source-agreements",
                 "--disable-interactivity"],
                log=log, cancel=cancel,
            )
            if rc == 0:
                invalidate_cache()
                if tesseract_binary():
                    return True, "Tesseract installed via winget."
        choco = shutil.which("choco")
        if choco:
            rc, _ = run_streaming([choco, "install", "tesseract", "-y"], log=log, cancel=cancel)
            if rc == 0:
                invalidate_cache()
                if tesseract_binary():
                    return True, "Tesseract installed via Chocolatey."
        url = "https://github.com/UB-Mannheim/tesseract/wiki"
        return False, (
            "Could not install the Tesseract program automatically.\n\n"
            f"Download it here: {url}\n"
            "Install it, then either add its folder to PATH or set \"tesseract_cmd\" "
            "in config.json to the full path of tesseract.exe "
            "(usually C:\\Program Files\\Tesseract-OCR\\tesseract.exe)."
        )
    if system == "Darwin":
        brew = shutil.which("brew")
        if brew:
            rc, _ = run_streaming([brew, "install", "tesseract"], log=log, cancel=cancel)
            if rc == 0:
                invalidate_cache()
                return True, "Tesseract installed via Homebrew."
        return False, "Install Tesseract with:  brew install tesseract"

    # Linux: apt first, then dnf/pacman.
    apt = shutil.which("apt-get") or shutil.which("apt")
    if apt:
        prefix: "List[str]" = []
        if os.geteuid() != 0:  # type: ignore[attr-defined]
            sudo = shutil.which("sudo")
            if sudo:
                prefix = [sudo, "-n"]  # non-interactive: never hang on a password prompt
        rc, _ = run_streaming(prefix + [apt, "install", "-y", "tesseract-ocr"], log=log, cancel=cancel)
        if rc == 0:
            invalidate_cache()
            return True, "Tesseract installed with apt."
    for mgr, args in (("dnf", ["install", "-y", "tesseract"]),
                      ("yum", ["install", "-y", "tesseract"]),
                      ("pacman", ["-S", "--noconfirm", "tesseract"])):
        exe = shutil.which(mgr)
        if exe:
            rc, _ = run_streaming([exe] + args, log=log, cancel=cancel)
            if rc == 0:
                invalidate_cache()
                return True, f"Tesseract installed with {mgr}."
    return False, "Install Tesseract with your package manager (e.g. sudo apt install tesseract-ocr)."


def install_engine(key: str, log: LogFn = _noop_log,
                   cancel: "Optional[threading.Event]" = None) -> "Tuple[bool, str]":
    """Install everything one engine needs. Returns (ok, message)."""
    spec = ENGINE_SPECS.get(key)
    if not spec:
        return False, f"Unknown engine: {key}"

    label = engine_label(key)
    log(f"=== Installing {label} ===")

    # PaddleOCR never installs into the application's environment. It gets a
    # separate venv so Paddle/Python dependencies cannot break the hub.
    if key == "paddleocr":
        return install_paddleocr(log=log, cancel=cancel)

    packages = [str(p) for p in spec.get("packages", [])]  # type: ignore[union-attr]
    ok_pkg, msg_pkg = install_packages(packages, log=log, cancel=cancel)
    if not ok_pkg:
        return False, f"{label}: {msg_pkg}"

    if key == "tesseract" and not tesseract_binary():
        log("Tesseract program not found — installing it now…")
        ok_bin, msg_bin = install_tesseract_program(log=log, cancel=cancel)
        if not ok_bin:
            return False, f"{label}: {msg_bin}"

    invalidate_cache()
    if engine_installed(key):
        log(f"=== {label} is ready ===")
        return True, f"{label} installed successfully."
    return False, (
        f"{label} was installed but is still not usable.\n"
        f"Status: {engine_status_text(key)}\n"
        "Restart the application and check Settings → OCR Engines."
    )


# ---------------------------------------------------------------------------
# FIRST-RUN / PERSISTED STATE
# ---------------------------------------------------------------------------
_SETUP_FLAG = "engine_setup_completed"
_STATE_KEY = "engine_setup_state"


def first_run_needed(cfg: dict) -> bool:
    """True only the very first time (or after the user resets the setup)."""
    settings = cfg.setdefault("app_settings", {})
    return not bool(settings.get(_SETUP_FLAG, False))


def engine_state(cfg: dict) -> dict:
    settings = cfg.setdefault("app_settings", {})
    state = settings.get(_STATE_KEY)
    if not isinstance(state, dict):
        state = {}
        settings[_STATE_KEY] = state
    return state


def remember_choice(cfg: dict, key: str, installed: bool) -> None:
    state = engine_state(cfg)
    state[key] = {
        "installed": bool(installed),
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def was_declined(cfg: dict, key: str) -> bool:
    """True when the user explicitly chose to skip this engine during setup."""
    entry = engine_state(cfg).get(key)
    return bool(entry) and not entry.get("installed", False)


def mark_setup_done(cfg: dict, installed_keys: "Sequence[str]" = ()) -> None:
    settings = cfg.setdefault("app_settings", {})
    settings[_SETUP_FLAG] = True
    for key in offline_engine_keys():
        remember_choice(cfg, key, engine_installed(key) or key in installed_keys)
    remember_choice(cfg, ONLINE_ENGINE_KEY, module_available("requests"))


def reset_setup(cfg: dict) -> None:
    """Let the user run the first-time setup again from Settings."""
    settings = cfg.setdefault("app_settings", {})
    settings[_SETUP_FLAG] = False


def missing_core_dependencies() -> "List[Tuple[str, str, str, bool]]":
    """Core dependencies that are not importable right now."""
    missing = []
    for mod, pip_name, label, required in CORE_DEPENDENCIES:
        if not module_available(mod):
            missing.append((mod, pip_name, label, required))
    return missing


def core_package_names(missing: "Sequence[Tuple[str, str, str, bool]]") -> "List[str]":
    names = []
    for _mod, pip_name, _label, _required in missing:
        if pip_name == "opencv-python" and platform.system() == "Linux":
            # Headless build is the correct wheel on servers/containers.
            names.append("opencv-python-headless")
        else:
            names.append(pip_name)
    return names


def dependency_summary() -> str:
    """One-line summary used by the About screen and the status bar."""
    installed = [k for k in offline_engine_keys() if engine_installed(k)]
    names = ", ".join(engine_short(k) for k in installed) or "none"
    return f"Local engines ready: {names}"
