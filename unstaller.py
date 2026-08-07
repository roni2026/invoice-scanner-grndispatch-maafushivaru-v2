#!/usr/bin/env python3
"""
uninstall_hub.py
=================
Companion to launch_hub.py. Removes everything that launch_hub.py
created, so you're left with a clean checkout of the repo:

  - .venv/                 the isolated virtual environment (all pip
                            packages live here — nothing was ever
                            installed system-wide, so deleting this
                            folder IS the "uninstall dependencies" step)
  - __pycache__/ folders    compiled bytecode caches, anywhere in the repo
  - *.pyc / *.pyo files     compiled bytecode, anywhere in the repo
  - pip's global download cache (optional, asked separately)

It does NOT touch:
  - config.json, maafushivaru_hub.py, or any of your data/output files
  - Tesseract, Homebrew, or anything installed outside this repo folder
  - your system/global Python — nothing was ever installed there

Run it with:
    python3 uninstall_hub.py
from inside the cloned repo folder.
"""

import os
import platform
import shutil
import subprocess
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(APP_DIR, ".venv")


def _supports_color():
    if os.environ.get("NO_COLOR"):
        return False
    if platform.system() == "Windows":
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


def ask_yes_no(prompt, default=False):
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


def dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def find_pycache_dirs(root):
    hits = []
    for dirpath, dirnames, _filenames in os.walk(root):
        if ".venv" in dirnames:
            dirnames.remove(".venv")  # handled/removed as a whole
        if os.path.basename(dirpath) == "__pycache__":
            hits.append(dirpath)
    return hits


def find_pyc_files(root):
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        if ".venv" in dirnames:
            dirnames.remove(".venv")
        if os.path.basename(dirpath) == "__pycache__":
            continue  # already counted as part of the pycache dir above
        for f in filenames:
            if f.endswith((".pyc", ".pyo")):
                hits.append(os.path.join(dirpath, f))
    return hits


def main():
    header("Maafushivaru Document Processing Hub — Uninstaller")
    print(f"  Working folder:   {C.DIM}{APP_DIR}{C.RESET}")

    header("Scanning for files this launcher created")

    venv_exists = os.path.isdir(VENV_DIR)
    if venv_exists:
        size = dir_size(VENV_DIR)
        ok(f".venv/  {C.DIM}({human_size(size)} — all pip packages live here){C.RESET}")
    else:
        info(".venv/  not found — dependencies were never installed, or already removed.")

    pycache_dirs = find_pycache_dirs(APP_DIR)
    pyc_files = find_pyc_files(APP_DIR)
    if pycache_dirs or pyc_files:
        ok(f"{len(pycache_dirs)} __pycache__/ folder(s), {len(pyc_files)} loose .pyc/.pyo file(s)")
    else:
        info("No __pycache__ folders or .pyc/.pyo files found.")

    if not venv_exists and not pycache_dirs and not pyc_files:
        print()
        ok("Nothing to uninstall — this folder is already clean.")
        return

    header("This will permanently delete the above")
    warn("Your config.json, maafushivaru_hub.py, and any scanned/output")
    warn("files are NOT touched. Only the items listed above are removed.")
    print()

    if not ask_yes_no("Proceed with uninstall?", default=False):
        info("Cancelled — nothing was deleted.")
        return

    if venv_exists:
        print()
        info("Removing .venv/ ...")
        try:
            shutil.rmtree(VENV_DIR)
            ok(".venv/ removed — all installed packages are gone with it.")
        except Exception as e:
            bad(f"Could not remove .venv/: {e}")
            info(f"Try manually: rm -rf {VENV_DIR}")

    if pycache_dirs or pyc_files:
        print()
        info("Removing bytecode caches ...")
        removed, failed = 0, 0
        for d in pycache_dirs:
            try:
                shutil.rmtree(d)
                removed += 1
            except Exception as e:
                failed += 1
                bad(f"Could not remove {d}: {e}")
        for f in pyc_files:
            try:
                os.remove(f)
                removed += 1
            except Exception as e:
                failed += 1
                bad(f"Could not remove {f}: {e}")
        if removed:
            ok(f"Removed {removed} cache item(s).")
        if failed:
            warn(f"{failed} item(s) could not be removed (see above).")

    header("Optional: pip's global download cache")
    info("This is pip's shared download cache on your whole machine — not")
    info("specific to this project. Clearing it just frees disk space; it's")
    info("safe to skip, and pip will simply re-download next time it's needed.")
    print()
    if ask_yes_no("Also clear pip's global download cache?", default=False):
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "cache", "purge"],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                ok("pip cache purged.")
            else:
                warn("pip cache purge reported an issue:")
                info(result.stderr.strip() or result.stdout.strip())
        except Exception as e:
            warn(f"Could not purge pip cache: {e}")

    header("Done")
    ok("Uninstall complete. This folder is back to a clean repo checkout.")
    info("Run launch_hub.py again any time to reinstall and relaunch.")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{C.YELLOW}Cancelled.{C.RESET}")
        sys.exit(1)
