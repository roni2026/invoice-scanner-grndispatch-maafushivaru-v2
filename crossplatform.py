#!/usr/bin/env python3
"""
launch_hub.py
=============
Terminal launcher for the Maafushivaru Document Processing Hub
(https://github.com/roni2026/invoice-scanner-grndispatch-maafushivaru-v2).

No GUI toolkit required (no tkinter) — everything happens in the terminal.

What it does:
  1. Detects your OS (Windows / macOS / Linux).
  2. Live-checks every required dependency and prints a colored report.
  3. If anything is missing, asks once: install now? [Y/n]
  4. Installs missing packages with pip.
  5. On Windows: runs natively, no conversion needed (the app was built
     for Windows).
     On macOS/Linux: converts config.json for this OS — points
     tesseract_cmd at the local binary, rewrites any Windows-style
     backslash paths, and disables the pywin32-only scanner feature.
  6. Asks: run the app now? [Y/n] — and launches maafushivaru_hub.py.

Run it with:
    python3 launch_hub.py
from inside the cloned repo folder (same folder as maafushivaru_hub.py).
"""

import json
import os
import platform
import shutil
import subprocess
import sys

# ----------------------------------------------------------------------
# Colors (ANSI). Auto-disabled on terminals that don't support them
# (e.g. some Windows cmd.exe without VT100 enabled, or piped output).
# ----------------------------------------------------------------------
def _supports_color():
    if os.environ.get("NO_COLOR"):
        return False
    if platform.system() == "Windows":
        # Enable VT100 processing on modern Windows terminals; fall back
        # to no-color if that fails (old cmd.exe).
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
            return True
        except Exception:
            return False
    return sys.stdout.isatty()


class C:
    ON = _supports_color()
    RESET = "\033[0m" if ON else ""
    BOLD = "\033[1m" if ON else ""
    DIM = "\033[2m" if ON else ""
    RED = "\033[31m" if ON else ""
    GREEN = "\033[32m" if ON else ""
    YELLOW = "\033[33m" if ON else ""
    BLUE = "\033[34m" if ON else ""
    MAGENTA = "\033[35m" if ON else ""
    CYAN = "\033[36m" if ON else ""


def ok(msg):
    print(f"  {C.GREEN}✔{C.RESET} {msg}")


def bad(msg):
    print(f"  {C.RED}✘{C.RESET} {msg}")


def warn(msg):
    print(f"  {C.YELLOW}!{C.RESET} {msg}")


def info(msg):
    print(f"  {C.DIM}·{C.RESET} {msg}")


def header(msg):
    width = max(60, len(msg) + 4)
    print()
    print(f"{C.BOLD}{C.CYAN}{'─' * width}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}  {msg}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}{'─' * width}{C.RESET}")


def ask_yes_no(prompt, default=True):
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        try:
            reply = input(f"{C.BOLD}{prompt} {suffix} {C.RESET}").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        if reply == "":
            return default
        if reply in ("y", "yes"):
            return True
        if reply in ("n", "no"):
            return False
        print(f"  {C.YELLOW}Please answer y or n.{C.RESET}")


APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
HUB_SCRIPT = os.path.join(APP_DIR, "maafushivaru_hub.py")
VENV_DIR = os.path.join(APP_DIR, ".venv")
_RELAUNCH_FLAG = "_LAUNCH_HUB_VENV"


def _in_venv():
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def _venv_python_path():
    if platform.system() == "Windows":
        return os.path.join(VENV_DIR, "Scripts", "python.exe")
    return os.path.join(VENV_DIR, "bin", "python3")


def ensure_venv_and_relaunch():
    """
    Homebrew (and many Linux) Pythons refuse system-wide `pip install`
    with an 'externally-managed-environment' error (PEP 668). Rather than
    forcing --break-system-packages (which can damage a Homebrew/distro
    Python install), create a private virtual environment next to this
    script the first time it runs, then re-exec the launcher inside it.
    From that point on sys.executable IS the venv's python, so every pip
    install and the eventual app launch happen inside it automatically —
    completely invisible after this one-time setup.
    """
    if os.environ.get(_RELAUNCH_FLAG) == "1" or _in_venv():
        return  # already inside our venv (or the user's own) — nothing to do

    vpy = _venv_python_path()
    if not os.path.isfile(vpy):
        header("Setting up an isolated Python environment")
        info(f"Creating: {VENV_DIR}")
        info("(one-time setup, avoids the 'externally-managed-environment' error)")
        try:
            subprocess.run([sys.executable, "-m", "venv", VENV_DIR], check=True)
            ok("Virtual environment created.")
        except Exception as e:
            bad(f"Could not create a virtual environment: {e}")
            warn("Continuing with the system Python — if package installs fail")
            warn("with 'externally-managed-environment', either:")
            info(f"    {sys.executable} -m pip install --break-system-packages <pkgs>")
            info("  or install python3-venv (Linux) and re-run this script.")
            return

    print()
    info("Relaunching inside the virtual environment...")
    os.environ[_RELAUNCH_FLAG] = "1"
    os.execv(vpy, [vpy, os.path.abspath(__file__)] + sys.argv[1:])

TESSERACT_CANDIDATES = {
    "Darwin": [
        "/opt/homebrew/bin/tesseract",
        "/usr/local/bin/tesseract",
        "/opt/local/bin/tesseract",
    ],
    "Linux": [
        "/usr/bin/tesseract",
        "/usr/local/bin/tesseract",
    ],
    "Windows": [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ],
}

# pip package name -> module name actually imported (they often differ)
PIP_PACKAGES = {
    "pytesseract":   "pytesseract",
    "PyMuPDF":       "fitz",
    "opencv-python": "cv2",
    "numpy":         "numpy",
    "pillow":        "PIL",
    "rapidfuzz":     "rapidfuzz",
    "openpyxl":      "openpyxl",
    "watchdog":      "watchdog",
    "plyer":         "plyer",
    "PyPDF2":        "PyPDF2",
    "rich":          "rich",
}

# Optional OCR engines — not auto-installed (large downloads), just reported.
OPTIONAL_PACKAGES = {
    "paddleocr":    "paddleocr",
    "paddlepaddle": "paddle",
    "easyocr":      "easyocr",
}

# Windows-only. scan_tab.py's direct scanner capture depends on this; it's
# already wrapped in try/except in maafushivaru_hub.py, so the app runs
# fine without it on macOS/Linux — the Scan tab just disables itself.
WINDOWS_ONLY_PACKAGES = {"pywin32": "win32com.client"}


def detect_os():
    system = platform.system()
    label = {"Darwin": "macOS", "Linux": "Linux", "Windows": "Windows"}.get(system, system)
    return system, label


def module_available(mod_name):
    try:
        __import__(mod_name)
        return True
    except ImportError:
        return False


def find_tesseract(system):
    on_path = shutil.which("tesseract")
    if on_path:
        return on_path
    for candidate in TESSERACT_CANDIDATES.get(system, []):
        if os.path.isfile(candidate):
            return candidate
    return None


def install_packages(pip_names):
    print()
    info(f"Installing: {', '.join(pip_names)}")
    info("(this can take a minute, especially opencv-python / numpy)")
    print()
    proc = subprocess.Popen(
        [sys.executable, "-m", "pip", "install", *pip_names],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    tail = []
    for line in proc.stdout:
        line = line.rstrip()
        tail.append(line)
        tail = tail[-15:]
        print(f"  {C.DIM}{line}{C.RESET}")
    proc.wait()
    return proc.returncode == 0, tail


def _normalize_windows_path_value(value):
    """
    Turn a Windows-style path string ('C:\\Program Files\\...',
    'SCANNED\\in') into something sane on macOS/Linux. Drive letters are
    stripped (there's no C:\\ on macOS/Linux) and backslashes become
    forward slashes. Only touches strings that actually look like
    Windows paths — leaves everything else untouched.
    """
    if not isinstance(value, str) or "\\" not in value:
        return value, False
    new_value = value
    # Strip a leading drive letter like "C:\" — no equivalent on posix.
    if len(new_value) >= 3 and new_value[1:3] == ":\\":
        new_value = new_value[3:]
    new_value = new_value.replace("\\", "/")
    return new_value, (new_value != value)


def convert_for_os(system, os_label, tpath):
    """
    macOS/Linux only. Rewrites config.json so the app — which was built
    and tested on Windows — runs correctly here:
      - points tesseract_cmd at the local binary (or clears a bad
        Windows-style path if none was found)
      - converts any other Windows-style backslash paths (input/output/
        watch folders, etc.) to posix-style paths
      - disables the scanner-capture feature flag if config.json exposes
        one, since it depends on pywin32 which isn't installed here
    Leaves config.json completely alone if nothing needs changing.
    """
    if not os.path.isfile(CONFIG_PATH):
        warn("config.json not found — nothing to convert.")
        return

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        bad(f"Could not read config.json: {e}")
        return

    changed = False

    # 1. tesseract_cmd
    old_tess = cfg.get("tesseract_cmd")
    if tpath and old_tess != tpath:
        cfg["tesseract_cmd"] = tpath
        ok("tesseract_cmd:")
        info(f"was: {old_tess}")
        info(f"now: {tpath}")
        changed = True
    elif tpath:
        ok("tesseract_cmd already correct.")
    else:
        warn("No local tesseract found yet — leaving tesseract_cmd as-is.")
        info("The app will also auto-detect tesseract on PATH at runtime.")

    # 2. Any other top-level string values that look like Windows paths
    #    (input/output/watch folders, log paths, etc.) — schema-agnostic
    #    so this doesn't break if config.json's keys change upstream.
    for key, value in list(cfg.items()):
        if key == "tesseract_cmd":
            continue
        new_value, was_changed = _normalize_windows_path_value(value)
        if was_changed:
            cfg[key] = new_value
            ok(f"{key}:")
            info(f"was: {value}")
            info(f"now: {new_value}")
            changed = True

    # 3. Scanner-capture feature flag (pywin32-dependent), if present
    for flag_key in ("scanner_enabled", "enable_scan_tab", "use_scanner"):
        if flag_key in cfg and cfg[flag_key] not in (False, "false"):
            cfg[flag_key] = False
            ok(f"{flag_key} → disabled (requires pywin32, Windows-only).")
            changed = True

    if changed:
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=4)
            print()
            ok("config.json converted and saved.")
        except Exception as e:
            bad(f"Could not write config.json: {e}")
    else:
        ok("config.json already compatible with " + os_label + " — no changes needed.")


def main():
    ensure_venv_and_relaunch()  # no-op / silent if already inside a venv

    system, os_label = detect_os()

    header("Maafushivaru Document Processing Hub — Launcher")
    if _in_venv():
        print(f"  Environment:      {C.DIM}virtual env ({VENV_DIR}){C.RESET}")
    print(f"  Detected OS:      {C.BOLD}{os_label}{C.RESET}")
    print(f"  Working folder:   {C.DIM}{APP_DIR}{C.RESET}")
    print(f"  Python:           {C.DIM}{sys.executable}{C.RESET}")

    if not os.path.isfile(HUB_SCRIPT):
        print()
        warn("maafushivaru_hub.py not found in this folder.")
        info("Place launch_hub.py in the same folder as the repo files.")

    # ---- Dependency check -------------------------------------------------
    header("Checking dependencies")
    missing_required = []
    for pip_name, mod_name in PIP_PACKAGES.items():
        if module_available(mod_name):
            ok(pip_name)
        else:
            bad(f"{pip_name}  {C.DIM}(missing){C.RESET}")
            missing_required.append(pip_name)

    print()
    for pip_name, mod_name in OPTIONAL_PACKAGES.items():
        if module_available(mod_name):
            ok(f"{pip_name}  {C.DIM}(optional, installed){C.RESET}")
        else:
            info(f"{pip_name}  (optional, not installed — only needed if enabled in Settings)")

    if system != "Windows":
        print()
        warn("pywin32 skipped (Windows-only). The Scan tab (direct scanner")
        info("capture) will be unavailable; the rest of the app still runs.")
        info("Use the SCANNED/ folder drop-in workflow instead.")

    # ---- Tesseract check ----------------------------------------------------
    header("Checking Tesseract OCR")
    tpath = find_tesseract(system)
    if tpath:
        ok(f"Found: {tpath}")
    else:
        bad("Tesseract not found on this system.")
        if system == "Darwin":
            info("Install it with:  brew install tesseract")
        elif system == "Linux":
            info("Install it with:  sudo apt install tesseract-ocr")
        else:
            info("Download it from: https://github.com/UB-Mannheim/tesseract/wiki")

    # ---- Install prompt -----------------------------------------------------
    if missing_required:
        header("Action needed")
        print(f"  {len(missing_required)} required package(s) missing: "
              f"{C.YELLOW}{', '.join(missing_required)}{C.RESET}")
        print()
        if ask_yes_no("Install missing packages now?", default=True):
            success, tail = install_packages(missing_required)
            print()
            if success:
                ok("All missing packages installed successfully.")
            else:
                bad("pip install failed. Last output above.")
                if "externally-managed-environment" in "\n".join(tail):
                    warn("Even the virtual environment reported "
                         "'externally-managed-environment' —")
                    info("this is unusual. Try deleting the venv and re-running:")
                    info(f"    rm -rf {VENV_DIR}")
                    info(f"    python3 {os.path.abspath(__file__)}")
                else:
                    info("Try running manually:")
                    info(f"    {sys.executable} -m pip install {' '.join(missing_required)}")
                sys.exit(1)
        else:
            warn("Skipped install. The app may fail to start without these packages.")
    else:
        header("Action needed")
        ok("All required packages already installed — nothing to do.")

    # ---- Convert / patch config -----------------------------------------------
    if system == "Windows":
        header("Running natively on Windows")
        ok("This app was built for Windows — no conversion needed.")
    else:
        header(f"Converting for {os_label}")
        info("This app was built for Windows. Patching config.json so it")
        info(f"runs correctly on {os_label}...")
        print()
        convert_for_os(system, os_label, tpath)

    # ---- Run prompt -----------------------------------------------------------
    header("Ready")
    if not os.path.isfile(HUB_SCRIPT):
        bad(f"Cannot launch — {HUB_SCRIPT} not found.")
        sys.exit(1)

    if ask_yes_no("Run the app now?", default=True):
        print()
        info("Launching maafushivaru_hub.py ...")
        try:
            subprocess.Popen([sys.executable, HUB_SCRIPT], cwd=APP_DIR)
            ok("App launched.")
        except Exception as e:
            bad(f"Failed to launch: {e}")
            sys.exit(1)
    else:
        info("Not launching. Run it later with:")
        info(f"    {sys.executable} {HUB_SCRIPT}")

    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{C.YELLOW}Cancelled.{C.RESET}")
        sys.exit(1)
