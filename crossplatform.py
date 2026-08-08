#!/usr/bin/env python3
"""
<<<<<<< HEAD
crossplatform.py
================
Cross-platform launcher for the Maafushivaru Document Processing Hub.

No GUI toolkit and no third-party packages required — everything in this
script uses only the Python standard library, because it runs BEFORE the
app's dependencies are installed.

What it does, in order:
  1. Detects your OS (Windows / macOS / Linux) and, on Linux, your package
     manager (apt / dnf / pacman / zypper / apk). On macOS it also finds
     Homebrew even when it is not on PATH (/usr/local or /opt/homebrew).
  2. Creates (or reuses) a private virtual environment in .venv/ and
     relaunches itself inside it, so package installs never hit the
     "externally-managed-environment" error on modern macOS/Linux.
  3. Checks every required Python package, the tkinter GUI toolkit, and
     the Tesseract OCR program, and prints a colored report.
  4. For anything missing it asks once, per group, with a [Y/n] prompt:
       - Python packages  -> installed with pip, ONE AT A TIME, each with
         its own live download bar showing percentage, downloaded size,
         speed and remaining time (ETA), plus an overall progress bar.
       - tkinter          -> installed automatically with your
         confirmation: Homebrew python-tk on macOS, the distro package
         (python3-tk / python3-tkinter / tk) on Linux. On Windows it is
         part of Python itself, so a repair hint is shown instead.
       - Tesseract OCR    -> installed automatically with your
         confirmation via Homebrew (macOS), the distro package manager
         (Linux) or winget/chocolatey (Windows).
  5. On macOS/Linux: converts config.json for this OS — backs it up first,
     points tesseract_cmd at the real binary, rewrites Windows-style
     backslash paths to POSIX paths (recursively), and disables the
     pywin32-only scanner feature. On Windows nothing needs converting.
  6. Makes sure the working folders from config.json exist, asks
     "run the app now? [Y/n]" and launches maafushivaru_hub.py, checking
     that it did not crash immediately.

Run it with:
    python3 crossplatform.py            # interactive
    python3 crossplatform.py --yes      # answer yes to all prompts
    python3 crossplatform.py --check-only   # just report, change nothing

from inside the folder that contains maafushivaru_hub.py.
"""

import argparse
import importlib
=======
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

>>>>>>> 5f9889c6ac069d498b62d3e60d0f5e205c269590
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time

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
<<<<<<< HEAD
    print(f"  {C.GREEN}OK{C.RESET}  {msg}")


def bad(msg):
    print(f"  {C.RED}XX {C.RESET} {msg}")


def warn(msg):
    print(f"  {C.YELLOW}!  {C.RESET} {msg}")
=======
    print(f"  {C.GREEN}✔{C.RESET} {msg}")


def bad(msg):
    print(f"  {C.RED}✘{C.RESET} {msg}")


def warn(msg):
    print(f"  {C.YELLOW}!{C.RESET} {msg}")
>>>>>>> 5f9889c6ac069d498b62d3e60d0f5e205c269590


def info(msg):
    print(f"  {C.DIM}·{C.RESET} {msg}")


def header(msg):
    width = max(60, len(msg) + 4)
    print()
    print(f"{C.BOLD}{C.CYAN}{'─' * width}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}  {msg}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}{'─' * width}{C.RESET}")


<<<<<<< HEAD
def ask_yes_no(prompt, default=True, assume_yes=False):
    """One [Y/n] confirmation prompt. Returns default on empty answer,
    False on Ctrl+C/EOF, and `default` without asking when assume_yes
    (--yes flag) is set."""
    if assume_yes:
        print(f"{C.BOLD}{prompt} [Y/n] {C.RESET}Y {C.DIM}(auto-answered by --yes){C.RESET}")
        return True
=======
def ask_yes_no(prompt, default=True):
>>>>>>> 5f9889c6ac069d498b62d3e60d0f5e205c269590
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
<<<<<<< HEAD
_RELAUNCH_FLAG = "_CROSSPLATFORM_VENV"
=======
_RELAUNCH_FLAG = "_LAUNCH_HUB_VENV"
>>>>>>> 5f9889c6ac069d498b62d3e60d0f5e205c269590


def _in_venv():
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def _venv_python_path():
    if platform.system() == "Windows":
        return os.path.join(VENV_DIR, "Scripts", "python.exe")
    return os.path.join(VENV_DIR, "bin", "python3")


<<<<<<< HEAD
# ----------------------------------------------------------------------
# OS / package-manager detection
# ----------------------------------------------------------------------
def detect_os():
    system = platform.system()
    label = {"Darwin": "macOS", "Linux": "Linux", "Windows": "Windows"}.get(system, system)
    return system, label


def find_brew():
    """Locate Homebrew even when it is not on PATH (common on fresh Macs
    and in non-interactive shells). Returns the path to the brew binary
    or None. Also puts brew's folder on PATH so later shutil.which()
    lookups (e.g. for tesseract) see brew-installed programs."""
    on_path = shutil.which("brew")
    if on_path:
        return on_path
    for candidate in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew"):
        if os.path.isfile(candidate):
            os.environ["PATH"] = os.path.dirname(candidate) + os.pathsep + os.environ.get("PATH", "")
            return candidate
    return None


def detect_linux_pm():
    """Return (name, install_cmd_builder, refresh_cmd) for the first
    package manager found on this Linux system, or None."""
    managers = [
        # name, binary, install command (pkg appended), optional refresh command
        ("apt",    "apt-get", ["apt-get", "install", "-y"], ["apt-get", "update"]),
        ("dnf",    "dnf",     ["dnf", "install", "-y"],      None),
        ("pacman", "pacman",  ["pacman", "-S", "--noconfirm", "--needed"], None),
        ("zypper", "zypper",  ["zypper", "--non-interactive", "install"], None),
        ("apk",    "apk",     ["apk", "add"],                None),
    ]
    for name, binary, install_cmd, refresh_cmd in managers:
        if shutil.which(binary):
            return {"name": name, "install": install_cmd, "refresh": refresh_cmd}
    return None


def _have_sudo():
    if not hasattr(os, "geteuid"):
        return False
    if os.geteuid() == 0:
        return True
    return shutil.which("sudo") is not None


def _sudo_prefix():
    """['sudo'] when we are not root and sudo exists, else []."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return []
    if shutil.which("sudo"):
        return ["sudo"]
    return []


# Package names per ecosystem, for the two system dependencies we manage.
def tkinter_package_name(pm_name):
    return {
        "apt": "python3-tk",
        "dnf": "python3-tkinter",
        "pacman": "tk",
        "zypper": "python3-tk",
        "apk": "python3-tkinter",
    }.get(pm_name)


def tesseract_package_name(pm_name):
    return {
        "apt": "tesseract-ocr",
        "dnf": "tesseract",
        "pacman": "tesseract",
        "zypper": "tesseract-ocr",
        "apk": "tesseract-ocr",
    }.get(pm_name)

=======
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
>>>>>>> 5f9889c6ac069d498b62d3e60d0f5e205c269590

TESSERACT_CANDIDATES = {
    "Darwin": [
        "/opt/homebrew/bin/tesseract",
        "/usr/local/bin/tesseract",
        "/opt/local/bin/tesseract",
    ],
    "Linux": [
        "/usr/bin/tesseract",
        "/usr/local/bin/tesseract",
<<<<<<< HEAD
        "/snap/bin/tesseract",
=======
>>>>>>> 5f9889c6ac069d498b62d3e60d0f5e205c269590
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

<<<<<<< HEAD
# Optional, Windows-only: lets the Scan tab's "HP software" capture method
# press buttons inside modern Store apps (HP Smart / HP Scan and Capture).
# Classic HP Scan works with pywin32 alone, so this stays report-only.
OPTIONAL_WINDOWS_PACKAGES = {
    "pywinauto": "pywinauto",
}


# ----------------------------------------------------------------------
# Basic checks
# ----------------------------------------------------------------------
def module_available(mod_name):
    try:
        importlib.import_module(mod_name)
        return True
    except Exception:
        return False


def tkinter_available():
    # Broad except: a Homebrew python without the python-tk formula raises
    # ImportError deep inside tkinter/__init__.py; some broken installs
    # raise OSError instead. Both mean "not usable".
    try:
        importlib.import_module("tkinter")
        return True
    except Exception:
=======
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
>>>>>>> 5f9889c6ac069d498b62d3e60d0f5e205c269590
        return False


def find_tesseract(system):
    on_path = shutil.which("tesseract")
    if on_path:
        return on_path
    for candidate in TESSERACT_CANDIDATES.get(system, []):
        if os.path.isfile(candidate):
            return candidate
    return None


def check_internet():
    import socket
    try:
        socket.setdefaulttimeout(5)
        socket.gethostbyname("pypi.org")
        return True
    except Exception:
        return False


<<<<<<< HEAD
# ----------------------------------------------------------------------
# Virtual-environment bootstrap (runs first; re-execs this script inside
# .venv so pip installs never fight PEP 668 "externally-managed-environment")
# ----------------------------------------------------------------------
def ensure_venv_and_relaunch():
    if os.environ.get(_RELAUNCH_FLAG) == "1" or _in_venv():
        return  # already inside our venv (or the user's own)

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
            if platform.system() == "Linux":
                warn("On Debian/Ubuntu this usually means python3-venv is missing.")
                warn(f"Install it with:  sudo apt install python3-venv")
                warn("then run this script again.")
            else:
                warn("Continuing with the system Python — package installs may fail")
                warn("with 'externally-managed-environment'.")
            return

    print()
    info("Relaunching inside the virtual environment...")
    os.environ[_RELAUNCH_FLAG] = "1"
    os.execv(vpy, [vpy, os.path.abspath(__file__)] + sys.argv[1:])


# ----------------------------------------------------------------------
# Size / speed formatting and bar drawing
# ----------------------------------------------------------------------
_UNIT_FACTORS = {
    "b": 1.0,
    "kb": 1024.0,
    "mb": 1024.0 ** 2,
    "gb": 1024.0 ** 3,
    "tb": 1024.0 ** 4,
}


def _unit_factor(unit):
    u = (unit or "b").lower()
    if u.endswith("/s"):      # speed units like "kB/s" -> "kb"
        u = u[:-2]
    return _UNIT_FACTORS.get(u, 1.0)


def _fmt_bytes(n):
    n = max(0.0, float(n))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
    return f"{n:.1f} TB"


def render_hash_bar(frac, width=30):
    """rpm/apt-style hash bar: [#################-----------]  57%"""
    frac = max(0.0, min(1.0, frac))
    filled = int(width * frac)
    bar = "#" * filled + "-" * (width - filled)
    pct = int(100 * frac)
=======
_PROGRESS_LINE_HINTS = ("B/s", "MB", "kB", "GB", "eta")


def _looks_like_pip_progress_line(line):
    """
    True for pip's own live download-progress refreshes (the
    '|#######    | 4.1/20.6 MB 5.3 MB/s eta 0:00:03' style lines),
    False for ordinary informational lines ('Collecting numpy', ...).
    pip writes progress refreshes with a bare '\\r' rather than '\\n';
    Python's universal-newline translation turns each of those '\\r'
    refreshes into its own line when we iterate proc.stdout below, so
    we see them one at a time and can redraw them ourselves live.
    """
    s = line.strip()
    return bool(s) and ("#" in s or "|" in s) and any(h in s for h in _PROGRESS_LINE_HINTS)


def render_hash_bar(current, total, width=30):
    """rpm/apt-style hash bar: [#################-----------]  57%"""
    total = max(total, 1)
    current = max(0, min(current, total))
    filled = int(width * current / total)
    pct = int(100 * current / total)
    bar = "#" * filled + "-" * (width - filled)
>>>>>>> 5f9889c6ac069d498b62d3e60d0f5e205c269590
    color = C.GREEN if C.ON else ""
    reset = C.RESET if C.ON else ""
    return f"[{color}{bar}{reset}] {pct:3d}%"


<<<<<<< HEAD
# ----------------------------------------------------------------------
# Parsing pip's live download progress.
# pip 26 (rich) draws:  ━━━━━━━━━━━━━━━╺━━━━━ 4.1/20.6 MB 5.3 MB/s eta 0:00:03
#   - the final render drops "eta":  ━━━━━━━━━ 20.6/20.6 MB 5.3 MB/s  0:00:04
#   - unknown time shows as --:--
# Older pip drew:       |#######    | 4.1/20.6 MB 5.3 MB/s eta 0:00:03
# When the total size is unknown pip shows only:  45 kB 230 kB/s
# ----------------------------------------------------------------------
_PIP_PROGRESS_RE = re.compile(
    r"(?P<cur>[0-9]+(?:\.[0-9]+)?)\s*(?P<cur_unit>[kKmMgGtT]?B)?\s*/\s*"
    r"(?P<tot>[0-9]+(?:\.[0-9]+)?)\s*(?P<tot_unit>[kKmMgGtT]?B)?"
    r"(?:\s+(?P<speed>[0-9]+(?:\.[0-9]+)?)\s*(?P<speed_unit>[kKmMgGtT]?B/s))?"
    r"(?:\s+(?:(?P<eta_word>eta)\s+)?(?P<eta>[0-9]+:[0-9]{2}(?::[0-9]{2})?|--:--))?",
    re.IGNORECASE,
)

_PIP_PARTIAL_RE = re.compile(
    r"(?P<cur>[0-9]+(?:\.[0-9]+)?)\s*(?P<cur_unit>[kKmMgGtT]?B)\s+"
    r"(?P<speed>[0-9]+(?:\.[0-9]+)?)\s*(?P<speed_unit>[kKmMgGtT]?B/s)",
    re.IGNORECASE,
)

# Characters pip draws bars with (old and rich styles), used by the
# fallback detector that passes pip's own progress text through.
_BAR_CHARS = set("#|━╸╺─")


def parse_pip_progress(text):
    """
    Parse one pip output chunk. Returns a dict:
      {"cur": bytes, "tot": bytes|None, "speed": bytes/s|None,
       "eta": str|None, "eta_is_estimate": bool}
    or None when the chunk is not a progress refresh.
    """
    m = _PIP_PROGRESS_RE.search(text)
    if m and "/" in text:
        cur_unit = m.group("cur_unit") or m.group("tot_unit") or "B"
        tot_unit = m.group("tot_unit") or cur_unit
        return {
            "cur": float(m.group("cur")) * _unit_factor(cur_unit),
            "tot": float(m.group("tot")) * _unit_factor(tot_unit),
            "speed": (float(m.group("speed")) * _unit_factor(m.group("speed_unit")))
                     if m.group("speed") else None,
            "eta": m.group("eta"),
            "eta_is_estimate": bool(m.group("eta_word")),
        }
    m = _PIP_PARTIAL_RE.search(text)
    if m:
        return {
            "cur": float(m.group("cur")) * _unit_factor(m.group("cur_unit")),
            "tot": None,
            "speed": float(m.group("speed")) * _unit_factor(m.group("speed_unit")),
            "eta": None,
            "eta_is_estimate": False,
        }
    return None


def _looks_like_pip_progress_line(line):
    """Fallback detector for pip progress text in a format we don't parse:
    bar-drawing characters plus size units on the same line."""
    s = line.strip()
    if not s:
        return False
    has_bar = any(ch in s for ch in _BAR_CHARS)
    has_units = ("B/s" in s) or (" MB" in s) or (" kB" in s) or (" GB" in s)
    return has_bar and has_units


def draw_download_bar(stats, width=32):
    """One live line: bar, %, downloaded/total, speed, time left."""
    cur, tot = stats["cur"], stats["tot"]
    speed, eta = stats["speed"], stats["eta"]

    if tot and tot > 0:
        frac = max(0.0, min(1.0, cur / tot))
        filled = int(width * frac)
        bar = "#" * filled + "-" * (width - filled)
        size_txt = f"{_fmt_bytes(cur)} / {_fmt_bytes(tot)}"
        pct_txt = f"{100 * frac:6.2f}%"
    else:
        # Total size unknown: slide a small block so it's clearly alive.
        block = "###"
        pos = int(time.monotonic() * 10) % (width - len(block) + 1)
        bar = "-" * pos + block + "-" * (width - len(block) - pos)
        size_txt = f"{_fmt_bytes(cur)} downloaded"
        pct_txt = "  --.- %"

    speed_txt = f"{_fmt_bytes(speed)}/s" if speed else "?"
    if eta:
        eta_txt = f"left {eta}" if stats["eta_is_estimate"] else f"time {eta}"
    else:
        eta_txt = ""

    green = C.GREEN if C.ON else ""
    reset = C.RESET if C.ON else ""
    return (
        f"\r  {C.CYAN}Downloading{C.RESET} [{green}{bar}{reset}] "
        f"{pct_txt}  {size_txt}  {speed_txt}  {eta_txt}"
    )


# ----------------------------------------------------------------------
# Streaming subprocess output.
# Both pip and brew/apt write live progress refreshes separated by a bare
# carriage return; Python's universal-newline mode hands each refresh to
# us as its own "line", so we can redraw in place.
# ----------------------------------------------------------------------
def _popen_stream(cmd):
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )


def install_pip_packages(pip_names, max_attempts=3):
    """
    Install packages ONE AT A TIME, retrying each on failure — one flaky
    download must not wipe out the whole batch (pip aborts everything on
    the first failure when given a list).

    Per package you get:
      - a live DOWNLOAD bar with %, size, speed and remaining time
        (parsed from pip's own progress, forced on with --progress-bar on)
      - an OVERALL bar of how many packages are done
    After each install the module is re-imported to prove it really works.

    Returns (succeeded, failed) — lists of pip package names.
=======
def install_packages(pip_names, max_attempts=3):
    """
    Install packages ONE AT A TIME, retrying each one on failure. This
    matters because a single flaky download (DNS blip, dropped wifi,
    a package needing to build from source) shouldn't wipe out an
    otherwise-successful batch install of ten other packages — pip
    aborts the whole invocation on the first failure when given a list.

    Shows two live hash-style progress bars while it works, similar to
    apt/rpm/yum on Linux:
      - a per-file DOWNLOAD bar, which is pip's own real progress
        (enabled with --progress-bar on and parsed into a custom live bar
        showing percentage, downloaded size, speed, and ETA)
      - an OVERALL bar tracking how many of the requested packages
        have completed so far

    Returns (succeeded, failed) — both lists of pip package names.
>>>>>>> 5f9889c6ac069d498b62d3e60d0f5e205c269590
    """
    succeeded, failed = [], []
    total = len(pip_names)

    for idx, pip_name in enumerate(pip_names, start=1):
<<<<<<< HEAD
        mod_name = PIP_PACKAGES.get(pip_name, pip_name)
        print()
        print(f"  {C.BOLD}Overall{C.RESET}  {render_hash_bar((idx - 1) / total)}  "
              f"{C.DIM}({idx - 1}/{total} packages){C.RESET}")
        info(f"Installing {pip_name} ...")

        installed_ok = False
        last_tail = []
=======
        print()
        print(f"  {C.BOLD}Overall{C.RESET}  {render_hash_bar(idx - 1, total)}  "
              f"{C.DIM}({idx-1}/{total} packages){C.RESET}")
        info(f"Installing {pip_name} ...")

        last_tail = []
        installed_ok = False
>>>>>>> 5f9889c6ac069d498b62d3e60d0f5e205c269590

        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                delay = 3 * (attempt - 1)
                info(f"  retry {attempt}/{max_attempts} in {delay}s ...")
                time.sleep(delay)

<<<<<<< HEAD
            try:
                proc = _popen_stream(
                    [sys.executable, "-m", "pip", "install",
                     "--retries", "3", "--timeout", "30",
                     "--progress-bar", "on", pip_name]
                )
            except Exception as e:
                bad(f"Could not start pip: {e}")
                break
=======
            proc = subprocess.Popen(
                [sys.executable, "-m", "pip", "install",
                 "--retries", "3", "--timeout", "20",
                 "--progress-bar", "on", pip_name],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
>>>>>>> 5f9889c6ac069d498b62d3e60d0f5e205c269590

            tail = []
            bar_open = False

<<<<<<< HEAD
            for raw_line in proc.stdout:
                line = raw_line.rstrip("\n")
                stripped = line.strip()
                if not stripped:
                    continue

                stats = parse_pip_progress(stripped)
                if stats:
                    sys.stdout.write(draw_download_bar(stats)[:150].ljust(155))
=======
            # pip's progress text normally contains something like:
            #   |########----| 4.1/20.6 MB 5.3 MB/s eta 0:00:03
            # or:
            #   42.3/100.0 MB 8.1 MB/s eta 0:00:07
            progress_re = re.compile(
                r"(?P<current>[0-9]+(?:\\.[0-9]+)?)\\s*"
                r"(?P<current_unit>[kKmMgG]?[bB])?\\s*/\\s*"
                r"(?P<total>[0-9]+(?:\\.[0-9]+)?)\\s*"
                r"(?P<total_unit>[kKmMgG]?[bB])?"
                r".*?(?P<speed>[0-9]+(?:\\.[0-9]+)?)\\s*"
                r"(?P<speed_unit>[kKmMgG]?[bB]/s)"
                r".*?(?:eta\\s+)?(?P<eta>[0-9]+:[0-9]{2}(?::[0-9]{2})?)",
                re.IGNORECASE,
            )

            def _unit_factor(unit):
                if not unit:
                    return 1.0
                unit = unit.lower()
                return {
                    "b": 1.0,
                    "kb": 1024.0,
                    "mb": 1024.0 ** 2,
                    "gb": 1024.0 ** 3,
                }.get(unit, 1.0)

            def _fmt_size(value, unit):
                if not unit:
                    return f"{value:.1f} B"
                return f"{value:.1f} {unit.upper()}"

            def _draw_download_bar(current, total, current_unit, total_unit,
                                   speed, speed_unit, eta, width=34):
                current_bytes = current * _unit_factor(current_unit)
                total_bytes = total * _unit_factor(total_unit or current_unit)
                pct = 0 if total_bytes <= 0 else min(100.0, current_bytes / total_bytes * 100)
                filled = int(width * pct / 100)
                bar = "#" * filled + "-" * (width - filled)

                current_text = _fmt_size(current, current_unit or "")
                total_text = _fmt_size(total, total_unit or current_unit or "")
                speed_text = f"{speed:.1f} {speed_unit.upper()}"
                return (
                    f"\\r  {C.CYAN}Downloading{C.RESET}  "
                    f"[{C.GREEN if C.ON else ''}{bar}{C.RESET if C.ON else ''}] "
                    f"{pct:6.2f}%  {current_text} / {total_text}  "
                    f"{speed_text}  ETA {eta}"
                )

            for raw_line in proc.stdout:
                line = raw_line.rstrip("\n")
                stripped = line.strip()
                match = progress_re.search(stripped)

                if match:
                    current = float(match.group("current"))
                    total = float(match.group("total"))
                    speed = float(match.group("speed"))
                    eta = match.group("eta")
                    current_unit = match.group("current_unit") or match.group("total_unit") or "B"
                    total_unit = match.group("total_unit") or current_unit
                    speed_unit = match.group("speed_unit")

                    text = _draw_download_bar(
                        current, total, current_unit, total_unit,
                        speed, speed_unit, eta
                    )
                    sys.stdout.write(text[:150].ljust(155))
>>>>>>> 5f9889c6ac069d498b62d3e60d0f5e205c269590
                    sys.stdout.flush()
                    bar_open = True
                    continue

                if _looks_like_pip_progress_line(stripped):
<<<<<<< HEAD
                    # Unparseable format — still show pip's own live text.
                    sys.stdout.write("\r  " + C.CYAN + "Downloading" + C.RESET +
                                     "  " + stripped[:145].ljust(145))
=======
                    # Fallback for pip progress formats that don't match the
                    # numeric parser above. Still show the live pip line.
                    sys.stdout.write(
                        "\r  " + C.CYAN + "Downloading" + C.RESET +
                        "  " + stripped[:145].ljust(145)
                    )
>>>>>>> 5f9889c6ac069d498b62d3e60d0f5e205c269590
                    sys.stdout.flush()
                    bar_open = True
                    continue

                if bar_open:
                    sys.stdout.write("\n")
<<<<<<< HEAD
                    sys.stdout.flush()
                    bar_open = False

                if stripped.startswith("[notice]"):
                    continue  # pip's self-upgrade nag — not useful here
                info(stripped)
=======
                    bar_open = False

                if stripped:
                    info(stripped)
>>>>>>> 5f9889c6ac069d498b62d3e60d0f5e205c269590
                tail.append(stripped)
                tail = tail[-8:]

            if bar_open:
                sys.stdout.write("\n")
<<<<<<< HEAD
                sys.stdout.flush()
=======
>>>>>>> 5f9889c6ac069d498b62d3e60d0f5e205c269590
            proc.wait()
            last_tail = tail

            if proc.returncode == 0:
<<<<<<< HEAD
                importlib.invalidate_caches()
                if module_available(mod_name):
                    ok(f"{pip_name} installed and importable.")
                    succeeded.append(pip_name)
                    installed_ok = True
                    break
                warn(f"pip reported success but '{mod_name}' still won't import.")
                tail.append("post-install import check failed")

        if not installed_ok:
            bad(f"{pip_name} failed after {max_attempts} attempt(s).")
=======
                # The actual "installing collected package" step pip does
                # after downloading is near-instant and reports no progress
                # of its own, so give it a quick rpm/apt-style hash fill —
                # purely cosmetic, but matches the visual you're after.
                width = 30
                for filled in range(0, width + 1, 3):
                    bar = "#" * filled + "-" * (width - filled)
                    sys.stdout.write(f"\r  {C.CYAN}installing{C.RESET}   "
                                      f"[{bar}] {pip_name}".ljust(90))
                    sys.stdout.flush()
                    time.sleep(0.02)
                sys.stdout.write("\n")
                ok(f"{pip_name} installed.")
                succeeded.append(pip_name)
                installed_ok = True
                break

        if not installed_ok:
            bad(f"{pip_name} failed after {max_attempts} attempts.")
>>>>>>> 5f9889c6ac069d498b62d3e60d0f5e205c269590
            for line in last_tail:
                print(f"    {C.DIM}{line}{C.RESET}")
            failed.append(pip_name)

    print()
<<<<<<< HEAD
    print(f"  {C.BOLD}Overall{C.RESET}  {render_hash_bar(1.0)}  "
          f"{C.DIM}({total}/{total} packages processed){C.RESET}")
    return succeeded, failed


def run_system_install(label, cmd, extra_path=None):
    """
    Run a system-package-manager install (brew / apt / dnf / ...) while
    streaming its output. These tools don't expose percentage/speed/ETA
    the way pip does, so we pass their own live progress text through:
    transient refresh chunks (percent meters, '#'-bars, '==>' brew
    headers) are redrawn in place, everything else prints as a dim line.
    Returns True on exit code 0.
    """
    info("Command: " + " ".join(cmd))
    print()
    env = os.environ.copy()
    if extra_path:
        env["PATH"] = extra_path + os.pathsep + env.get("PATH", "")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            env=env,
        )
    except FileNotFoundError:
        bad(f"Command not found: {cmd[0]}")
        return False
    except Exception as e:
        bad(f"Could not start installer: {e}")
        return False

    started = time.monotonic()
    live_open = False
    tail = []
    for raw_line in proc.stdout:
        line = raw_line.rstrip("\n")
        stripped = line.strip()
        if not stripped:
            continue
        transient = ("%" in stripped) or ("==>" in stripped) or (
            any(ch in stripped for ch in _BAR_CHARS) and len(stripped) < 200)
        if transient:
            elapsed = int(time.monotonic() - started)
            sys.stdout.write(f"\r  {C.CYAN}{label}{C.RESET} "
                             f"{C.DIM}{elapsed:>3d}s{C.RESET}  {stripped[:130].ljust(135)}")
            sys.stdout.flush()
            live_open = True
        else:
            if live_open:
                sys.stdout.write("\n")
                live_open = False
            info(stripped)
            tail.append(stripped)
            tail = tail[-8:]
    if live_open:
        sys.stdout.write("\n")
        sys.stdout.flush()
    proc.wait()

    if proc.returncode == 0:
        ok(f"{label} finished.")
        return True
    bad(f"{label} failed (exit code {proc.returncode}).")
    for line in tail:
        print(f"    {C.DIM}{line}{C.RESET}")
    return False


# ----------------------------------------------------------------------
# System-software installers (tkinter, Tesseract) — each one asks for
# confirmation [Y/n] before touching the system.
# ----------------------------------------------------------------------
def install_tkinter(system, brew, linux_pm, assume_yes):
    """Try to make tkinter importable. Returns True if it works afterwards."""
    print()
    warn("tkinter (the toolkit the app's window is built with) is missing.")

    if system == "Darwin":
        maj, minor = sys.version_info[:2]
        formula = f"python-tk@{maj}.{minor}"
        base = getattr(sys, "base_prefix", sys.prefix)
        looks_homebrew = brew and ("/Cellar/" in base or "/homebrew/" in base.lower()
                                   or "/usr/local/opt/" in base or "/opt/homebrew/" in base)
        if brew and looks_homebrew:
            print()
            info(f"Your Python {maj}.{minor} comes from Homebrew, and Homebrew ships")
            info(f"tkinter as a separate add-on package called {formula}.")
            if not ask_yes_no(f"Install tkinter now? (runs: brew install {formula})",
                              default=True, assume_yes=assume_yes):
                warn("Skipped. The app cannot open its window without tkinter.")
                return False
            ok_brew = run_system_install(f"Installing {formula}", [brew, "install", formula],
                                         extra_path=os.path.dirname(brew))
            if ok_brew and tkinter_available():
                ok("tkinter is now available.")
                return True
            if ok_brew:
                # brew linked it for the base python; give the import one retry
                importlib.invalidate_caches()
                if tkinter_available():
                    ok("tkinter is now available.")
                    return True
            bad("tkinter still is not importable after the install.")
            info("Try closing and reopening the terminal, then run this script again.")
            return False
        if brew:
            # brew exists but this python isn't from Homebrew
            warn("This Python did not come from Homebrew, so a brew install would not fix it.")
            info("Easiest fix: install Python from https://www.python.org/downloads/")
            info("(its installer includes tkinter), then run this script with it.")
            return False
        warn("Homebrew is not installed, and this Python has no tkinter.")
        info("Easiest fix: install Python from https://www.python.org/downloads/")
        info("(its installer includes tkinter), then run this script with it.")
        return False

    if system == "Linux":
        if linux_pm:
            pkg = tkinter_package_name(linux_pm["name"])
            if not pkg:
                warn(f"I don't know the tkinter package name for {linux_pm['name']}.")
                return False
            if not _have_sudo():
                warn("Installing it needs administrator rights, but sudo was not found.")
                info(f"Ask an administrator to run:  {' '.join(linux_pm['install'])} {pkg}")
                return False
            cmd = _sudo_prefix() + linux_pm["install"] + [pkg]
            print()
            info(f"On this system tkinter comes in the '{pkg}' package.")
            if not ask_yes_no(f"Install tkinter now? (runs: {' '.join(cmd)})",
                              default=True, assume_yes=assume_yes):
                warn("Skipped. The app cannot open its window without tkinter.")
                return False
            if run_system_install(f"Installing {pkg}", cmd) and tkinter_available():
                ok("tkinter is now available.")
                return True
            bad("tkinter still is not importable after the install.")
            return False
        warn("No supported package manager found (apt/dnf/pacman/zypper/apk).")
        info("Install your distribution's tkinter package, e.g. python3-tk.")
        return False

    # Windows
    warn("On Windows, tkinter is part of the official Python installer.")
    info("Fix: re-run your Python installer, choose 'Modify', and tick")
    info("'tcl/tk and IDLE'. Then run this script again.")
    return False


def install_tesseract(system, brew, linux_pm, assume_yes):
    """Try to install the Tesseract OCR program. Returns path or None."""
    print()
    warn("Tesseract OCR (the program that reads text from scans) is missing.")

    if system == "Darwin":
        if not brew:
            warn("Homebrew is not installed, so Tesseract can't be installed automatically.")
            info("Install Homebrew from https://brew.sh and re-run this script,")
            info("or install Tesseract by hand.")
            return None
        print()
        info("Tesseract will be installed with Homebrew (this can take a few minutes).")
        if not ask_yes_no("Install Tesseract now? (runs: brew install tesseract)",
                          default=True, assume_yes=assume_yes):
            warn("Skipped. OCR of scanned documents will not work without it.")
            return None
        if run_system_install("Installing tesseract", [brew, "install", "tesseract"],
                              extra_path=os.path.dirname(brew)):
            path = find_tesseract(system)
            if path:
                ok(f"Tesseract found at: {path}")
                return path
        bad("Tesseract still not found after the install.")
        info("Try closing and reopening the terminal, then run this script again.")
        return None

    if system == "Linux":
        if linux_pm:
            pkg = tesseract_package_name(linux_pm["name"])
            if not _have_sudo():
                warn("Installing it needs administrator rights, but sudo was not found.")
                info(f"Ask an administrator to run:  {' '.join(linux_pm['install'])} {pkg}")
                return None
            # Refresh the package index first for apt so a stale cache
            # doesn't fail the install with 404s.
            cmds = []
            if linux_pm["refresh"]:
                cmds.append(_sudo_prefix() + linux_pm["refresh"])
            cmds.append(_sudo_prefix() + linux_pm["install"] + [pkg])
            print()
            info(f"Tesseract comes in the '{pkg}' package on this system.")
            if not ask_yes_no(f"Install Tesseract now? (runs: {' && '.join(' '.join(c) for c in cmds)})",
                              default=True, assume_yes=assume_yes):
                warn("Skipped. OCR of scanned documents will not work without it.")
                return None
            for cmd in cmds:
                if not run_system_install(f"Installing {pkg}", cmd):
                    return None
            path = find_tesseract(system)
            if path:
                ok(f"Tesseract found at: {path}")
                return path
            bad("Tesseract still not found after the install.")
            return None
        warn("No supported package manager found (apt/dnf/pacman/zypper/apk).")
        info("Install your distribution's Tesseract package by hand.")
        return None

    # Windows
    winget = shutil.which("winget")
    choco = shutil.which("choco")
    print()
    if winget:
        cmd = [winget, "install", "-e", "--id", "UB-Mannheim.TesseractOCR",
               "--silent", "--accept-package-agreements", "--accept-source-agreements"]
        if not ask_yes_no("Install Tesseract now? (runs: winget install UB-Mannheim.TesseractOCR)",
                          default=True, assume_yes=assume_yes):
            warn("Skipped. OCR of scanned documents will not work without it.")
            return None
        if run_system_install("Installing Tesseract", cmd):
            path = find_tesseract(system)
            if path:
                ok(f"Tesseract found at: {path}")
                return path
    elif choco:
        cmd = [choco, "install", "tesseract", "-y"]
        if not ask_yes_no("Install Tesseract now? (runs: choco install tesseract)",
                          default=True, assume_yes=assume_yes):
            warn("Skipped. OCR of scanned documents will not work without it.")
            return None
        if run_system_install("Installing Tesseract", cmd):
            path = find_tesseract(system)
            if path:
                ok(f"Tesseract found at: {path}")
                return path
    else:
        info("Download the installer from:")
        info("  https://github.com/UB-Mannheim/tesseract/wiki")
        info("Install it, then run this script again.")
    return find_tesseract(system)


# ----------------------------------------------------------------------
# config.json conversion for macOS / Linux
# ----------------------------------------------------------------------
_PATH_KEY_HINTS = ("_path", "_dir", "_folder", "_file", "_cmd")
_PATH_KEYS = {"base", "scanned", "processed", "archive", "failed", "logs",
              "input", "output", "watch", "watch_folder"}


=======
    print(f"  {C.BOLD}Overall{C.RESET}  {render_hash_bar(total, total)}  "
          f"{C.DIM}({total}/{total} packages){C.RESET}")
    return succeeded, failed


>>>>>>> 5f9889c6ac069d498b62d3e60d0f5e205c269590
def _normalize_windows_path_value(value):
    """
    Turn a Windows-style path string ('C:\\Program Files\\...',
    'SCANNED\\in') into something sane on macOS/Linux. Drive letters are
<<<<<<< HEAD
    stripped and backslashes become forward slashes. Only touches strings
    that actually look like Windows paths.
=======
    stripped (there's no C:\\ on macOS/Linux) and backslashes become
    forward slashes. Only touches strings that actually look like
    Windows paths — leaves everything else untouched.
>>>>>>> 5f9889c6ac069d498b62d3e60d0f5e205c269590
    """
    if not isinstance(value, str) or "\\" not in value:
        return value, False
    new_value = value
<<<<<<< HEAD
=======
    # Strip a leading drive letter like "C:\" — no equivalent on posix.
>>>>>>> 5f9889c6ac069d498b62d3e60d0f5e205c269590
    if len(new_value) >= 3 and new_value[1:3] == ":\\":
        new_value = new_value[3:]
    new_value = new_value.replace("\\", "/")
    return new_value, (new_value != value)


<<<<<<< HEAD
def _walk_normalize(value, key=None):
    """Recursively normalize path-like strings in dicts/lists. Only keys
    whose name looks like it holds a path are touched, so supplier names,
    regex patterns etc. are never altered."""
    if isinstance(value, dict):
        out, changed = {}, False
        for k, v in value.items():
            nv, ch = _walk_normalize(v, k)
            out[k] = nv
            changed = changed or ch
        return out, changed
    if isinstance(value, list):
        out, changed = [], False
        for item in value:
            nv, ch = _walk_normalize(item, key)
            out.append(nv)
            changed = changed or ch
        return out, changed
    key_l = (key or "").lower()
    pathish = key_l in _PATH_KEYS or any(h in key_l for h in _PATH_KEY_HINTS)
    if not pathish:
        return value, False
    return _normalize_windows_path_value(value)


def convert_for_os(system, os_label, tpath):
    """
    macOS/Linux only. Rewrites config.json so the app — built and tested
    on Windows — runs correctly here. A timestamped backup is written
    first, and the whole thing is safe to run repeatedly (idempotent).
=======
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
>>>>>>> 5f9889c6ac069d498b62d3e60d0f5e205c269590
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

<<<<<<< HEAD
    # 2. Windows-style paths anywhere under path-like keys (recursive).
    new_cfg, walk_changed = _walk_normalize(cfg)
    if walk_changed:
        for k in new_cfg:
            if new_cfg[k] != cfg[k]:
                ok(f"{k}: Windows-style paths rewritten for {os_label}.")
        cfg = new_cfg
        changed = True

    # 3. Scanner-capture feature flag (pywin32-dependent), if present.
    def _disable_flags(node):
        nonlocal changed
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("scanner_enabled", "enable_scan_tab", "use_scanner") \
                        and v not in (False, "false"):
                    node[k] = False
                    ok(f"{k} -> disabled (requires pywin32, Windows-only).")
                    changed = True
                else:
                    _disable_flags(v)
        elif isinstance(node, list):
            for item in node:
                _disable_flags(item)

    _disable_flags(cfg)

    if changed:
        backup = CONFIG_PATH + ".windows-backup-" + time.strftime("%Y%m%d-%H%M%S")
        try:
            shutil.copy2(CONFIG_PATH, backup)
            info(f"Backup of the original Windows config: {os.path.basename(backup)}")
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=4)
=======
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
>>>>>>> 5f9889c6ac069d498b62d3e60d0f5e205c269590
            ok("config.json converted and saved.")
        except Exception as e:
            bad(f"Could not write config.json: {e}")
    else:
        ok("config.json already compatible with " + os_label + " — no changes needed.")


<<<<<<< HEAD
def ensure_runtime_folders():
    """Create the working folders from config.json (SCANNED/, PROCESSED/,
    ...) if they don't exist yet, so the app doesn't trip on first run."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return
    folders = cfg.get("folders", {})
    if not isinstance(folders, dict):
        return
    base = folders.get("base", ".") or "."
    for key, rel in folders.items():
        if key == "base" or not isinstance(rel, str) or not rel:
            continue
        path = os.path.join(APP_DIR, base, rel) if not os.path.isabs(rel) else rel
        try:
            os.makedirs(path, exist_ok=True)
        except Exception:
            pass


# ----------------------------------------------------------------------
# Main flow
# ----------------------------------------------------------------------
def parse_args(argv):
    p = argparse.ArgumentParser(
        description="Cross-platform launcher for the Maafushivaru Document Processing Hub.")
    p.add_argument("--yes", "-y", action="store_true",
                   help="answer YES to all install/run prompts (non-interactive)")
    p.add_argument("--check-only", action="store_true",
                   help="only check and report; install and run nothing")
    p.add_argument("--no-install", action="store_true",
                   help="never install anything, even if confirmed")
    p.add_argument("--no-run", action="store_true",
                   help="never launch the app at the end")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])

    # Put Homebrew on PATH early (fresh Macs often don't have it in
    # non-interactive shells) so every later shutil.which() sees it.
    brew = find_brew() if platform.system() == "Darwin" else None

    ensure_venv_and_relaunch()  # no-op when already inside a venv

    system, os_label = detect_os()
    linux_pm = detect_linux_pm() if system == "Linux" else None

    header("Maafushivaru Document Processing Hub — cross-platform setup")
    if _in_venv():
        print(f"  Environment:      {C.DIM}virtual env ({VENV_DIR}){C.RESET}")
    print(f"  Detected OS:      {C.BOLD}{os_label}{C.RESET}")
    if linux_pm:
        print(f"  Package manager:  {C.DIM}{linux_pm['name']}{C.RESET}")
    if brew:
        print(f"  Homebrew:         {C.DIM}{brew}{C.RESET}")
    print(f"  Working folder:   {C.DIM}{APP_DIR}{C.RESET}")
    print(f"  Python:           {C.DIM}{sys.executable} ({platform.python_version()}){C.RESET}")
=======
def main():
    ensure_venv_and_relaunch()  # no-op / silent if already inside a venv

    system, os_label = detect_os()

    header("Maafushivaru Document Processing Hub — Launcher")
    if _in_venv():
        print(f"  Environment:      {C.DIM}virtual env ({VENV_DIR}){C.RESET}")
    print(f"  Detected OS:      {C.BOLD}{os_label}{C.RESET}")
    print(f"  Working folder:   {C.DIM}{APP_DIR}{C.RESET}")
    print(f"  Python:           {C.DIM}{sys.executable}{C.RESET}")
>>>>>>> 5f9889c6ac069d498b62d3e60d0f5e205c269590

    if not os.path.isfile(HUB_SCRIPT):
        print()
        warn("maafushivaru_hub.py not found in this folder.")
<<<<<<< HEAD
        info("Put crossplatform.py in the same folder as the app files.")

    online = check_internet()
    if not online:
        print()
        warn("No internet connection detected — downloads will fail.")
        info("Connect to the internet and run this script again if anything is missing.")

    # ---- Python package check ---------------------------------------------
    header("Checking Python packages")
=======
        info("Place launch_hub.py in the same folder as the repo files.")

    # ---- Dependency check -------------------------------------------------
    header("Checking dependencies")
>>>>>>> 5f9889c6ac069d498b62d3e60d0f5e205c269590
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
<<<<<<< HEAD
            info(f"{pip_name}  (optional — only needed if enabled in the app's Settings)")

    if system == "Windows":
        print()
        for pip_name, mod_name in OPTIONAL_WINDOWS_PACKAGES.items():
            if module_available(mod_name):
                ok(f"{pip_name}  {C.DIM}(optional, installed){C.RESET}")
            else:
                info(f"{pip_name}  (optional — lets the Scan tab press buttons in "
                     f"HP Smart / Store apps)")
    else:
        print()
        info("pywin32 is Windows-only and is skipped here — the Scan tab's")
        info("direct scanner capture stays off; use the SCANNED/ folder instead.")

    # ---- tkinter check ------------------------------------------------------
    header("Checking tkinter (app window)")
    have_tk = tkinter_available()
    if have_tk:
        ok("tkinter is available.")
    else:
        bad("tkinter is missing — the app's window cannot open without it.")

    # ---- Tesseract check ------------------------------------------------------
=======
            info(f"{pip_name}  (optional, not installed — only needed if enabled in Settings)")

    if system != "Windows":
        print()
        warn("pywin32 skipped (Windows-only). The Scan tab (direct scanner")
        info("capture) will be unavailable; the rest of the app still runs.")
        info("Use the SCANNED/ folder drop-in workflow instead.")

    # ---- Tesseract check ----------------------------------------------------
>>>>>>> 5f9889c6ac069d498b62d3e60d0f5e205c269590
    header("Checking Tesseract OCR")
    tpath = find_tesseract(system)
    if tpath:
        ok(f"Found: {tpath}")
    else:
        bad("Tesseract not found on this system.")
<<<<<<< HEAD

    if args.check_only:
        header("Check-only summary")
        if missing_required or not have_tk or not tpath:
            warn("Some dependencies are missing (see above).")
            info("Run without --check-only to be asked [Y/n] before each install.")
            sys.exit(1)
        ok("Everything is installed.")
        sys.exit(0)

    # ---- Install: Python packages -------------------------------------------
    if missing_required and not args.no_install:
        header("Installing missing Python packages")
        print(f"  {len(missing_required)} package(s) missing: "
              f"{C.YELLOW}{', '.join(missing_required)}{C.RESET}")
        print()
        if not online:
            bad("Offline — cannot download packages.")
            sys.exit(1)
        if ask_yes_no("Install these Python packages now?", default=True,
                      assume_yes=args.yes):
            succeeded, failed = install_pip_packages(missing_required)
            print()
            if not failed:
                ok("All missing Python packages installed successfully.")
            else:
                bad(f"Failed to install: {', '.join(failed)}")
                tail_joined = " ".join(failed)
                if "externally-managed-environment" in tail_joined:
                    info("Try deleting the venv and re-running:")
                    info(f"    rm -rf {VENV_DIR}")
                    info(f"    python3 {os.path.abspath(__file__)}")
                else:
                    info("Try installing them by hand:")
                    info(f"    {sys.executable} -m pip install {' '.join(failed)}")
                sys.exit(1)
        else:
            warn("Skipped. The app may fail to start without these packages.")
    elif missing_required:
        header("Python packages")
        warn("Missing packages were found but --no-install was given.")
    else:
        header("Python packages")
        ok("All required packages already installed — nothing to do.")

    # ---- Install: tkinter ------------------------------------------------------
    if not have_tk and not args.no_install:
        header("Installing tkinter")
        have_tk = install_tkinter(system, brew, linux_pm, args.yes)

    # ---- Install: Tesseract ------------------------------------------------------
    if not tpath and not args.no_install:
        header("Installing Tesseract OCR")
        tpath = install_tesseract(system, brew, linux_pm, args.yes)

    # ---- Final readiness ------------------------------------------------------
    header("Readiness summary")
    still_missing = [p for p, m in PIP_PACKAGES.items() if not module_available(m)]
    if still_missing:
        bad(f"Python packages still missing: {', '.join(still_missing)}")
    else:
        ok("All required Python packages are importable.")
    if tkinter_available():
        ok("tkinter is available.")
    else:
        bad("tkinter still missing — the app window will not open.")
    if tpath:
        ok(f"Tesseract: {tpath}")
    else:
        warn("Tesseract still missing — scanned-document OCR will not work.")

    # ---- Convert config for this OS -------------------------------------------
=======
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
>>>>>>> 5f9889c6ac069d498b62d3e60d0f5e205c269590
    if system == "Windows":
        header("Running natively on Windows")
        ok("This app was built for Windows — no conversion needed.")
    else:
        header(f"Converting for {os_label}")
<<<<<<< HEAD
        info("This app was built for Windows. Adjusting config.json so it")
=======
        info("This app was built for Windows. Patching config.json so it")
>>>>>>> 5f9889c6ac069d498b62d3e60d0f5e205c269590
        info(f"runs correctly on {os_label}...")
        print()
        convert_for_os(system, os_label, tpath)

<<<<<<< HEAD
    ensure_runtime_folders()

    # ---- Run prompt -------------------------------------------------------------
=======
    # ---- Run prompt -----------------------------------------------------------
>>>>>>> 5f9889c6ac069d498b62d3e60d0f5e205c269590
    header("Ready")
    if not os.path.isfile(HUB_SCRIPT):
        bad(f"Cannot launch — {HUB_SCRIPT} not found.")
        sys.exit(1)
<<<<<<< HEAD
    if still_missing or not tkinter_available():
        bad("The app cannot start properly while dependencies are missing.")
        info("Fix the items marked XX above and run this script again.")
        sys.exit(1)

    if args.no_run:
        info(f"--no-run given. Start the app later with:")
        info(f"    {sys.executable} {HUB_SCRIPT}")
        print()
        return

    if ask_yes_no("Run the app now?", default=True, assume_yes=args.yes):
        print()
        info("Launching maafushivaru_hub.py ...")
        try:
            proc = subprocess.Popen([sys.executable, HUB_SCRIPT], cwd=APP_DIR)
        except Exception as e:
            bad(f"Failed to launch: {e}")
            sys.exit(1)
        # Give it a few seconds — if it crashed immediately (bad import,
        # syntax error...), say so instead of pretending all is well.
        time.sleep(3)
        rc = proc.poll()
        if rc is None:
            ok("App launched and is running.")
        else:
            bad(f"The app exited right away (code {rc}).")
            info("Run it by hand to see the error:")
            info(f"    {sys.executable} {HUB_SCRIPT}")
            sys.exit(1)
=======

    if ask_yes_no("Run the app now?", default=True):
        print()
        info("Launching maafushivaru_hub.py ...")
        try:
            subprocess.Popen([sys.executable, HUB_SCRIPT], cwd=APP_DIR)
            ok("App launched.")
        except Exception as e:
            bad(f"Failed to launch: {e}")
            sys.exit(1)
>>>>>>> 5f9889c6ac069d498b62d3e60d0f5e205c269590
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
