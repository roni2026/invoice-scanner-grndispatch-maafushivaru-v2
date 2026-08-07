#!/usr/bin/env python3
"""
launch_hub.py
=============
Cross-platform launcher for the Maafushivaru Document Processing Hub
(https://github.com/roni2026/invoice-scanner-grndispatch-maafushivaru-v2).

What it does:
  1. Detects your OS (Windows / macOS / Linux).
  2. Shows a small window with a "Convert" button.
  3. When you click Convert, it patches the parts of the project that are
     hardcoded for Windows (currently: the tesseract_cmd path in config.json)
     so they work on your OS instead. It does NOT touch scan_tab.py's
     pywin32 usage — that tab is already wrapped in a try/except in
     maafushivaru_hub.py, so on macOS/Linux it just silently disables
     itself instead of crashing the app.
  4. Once conversion is done, click "Run App" to launch
     maafushivaru_hub.py with your system's Python.

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
import tkinter as tk
from tkinter import messagebox, scrolledtext

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
HUB_SCRIPT = os.path.join(APP_DIR, "maafushivaru_hub.py")

# Common tesseract locations per OS, checked in order.
TESSERACT_CANDIDATES = {
    "Darwin": [  # macOS
        "/opt/homebrew/bin/tesseract",   # Apple Silicon Homebrew
        "/usr/local/bin/tesseract",      # Intel Homebrew
        "/opt/local/bin/tesseract",      # MacPorts
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

PIP_PACKAGES = [
    "pytesseract", "PyMuPDF", "opencv-python", "numpy",
    "pillow", "rapidfuzz", "openpyxl", "watchdog", "plyer",
]


def detect_os():
    system = platform.system()  # 'Darwin', 'Linux', 'Windows'
    label = {"Darwin": "macOS", "Linux": "Linux", "Windows": "Windows"}.get(system, system)
    return system, label


def find_tesseract(system):
    """Return a working tesseract path, checking PATH first, then known locations."""
    on_path = shutil.which("tesseract")
    if on_path:
        return on_path
    for candidate in TESSERACT_CANDIDATES.get(system, []):
        if os.path.isfile(candidate):
            return candidate
    return None


class LauncherGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.system, self.os_label = detect_os()
        self.title("Maafushivaru Hub Launcher")
        self.geometry("560x420")
        self.resizable(False, False)

        tk.Label(
            self, text="Maafushivaru Document Processing Hub",
            font=("Helvetica", 14, "bold")
        ).pack(pady=(16, 4))

        tk.Label(
            self, text=f"Detected OS: {self.os_label}",
            font=("Helvetica", 11)
        ).pack(pady=(0, 12))

        tk.Label(
            self,
            text=("This app was built for Windows. Click Convert to patch\n"
                  "OS-specific settings (like the Tesseract OCR path) for\n"
                  f"{self.os_label}, then click Run App to launch it."),
            justify="center"
        ).pack(pady=(0, 12))

        btn_frame = tk.Frame(self)
        btn_frame.pack(pady=6)

        self.convert_btn = tk.Button(
            btn_frame, text="Convert for " + self.os_label, width=20,
            command=self.convert, bg="#2d6cdf", fg="white",
            font=("Helvetica", 11, "bold")
        )
        self.convert_btn.grid(row=0, column=0, padx=6)

        self.run_btn = tk.Button(
            btn_frame, text="Run App", width=20,
            command=self.run_app, state=tk.DISABLED,
            bg="#2da44e", fg="white", font=("Helvetica", 11, "bold")
        )
        self.run_btn.grid(row=0, column=1, padx=6)

        self.log = scrolledtext.ScrolledText(self, height=14, width=68, state=tk.DISABLED)
        self.log.pack(padx=12, pady=12)

        self._write(f"Ready. Working folder: {APP_DIR}")
        if not os.path.isfile(HUB_SCRIPT):
            self._write("WARNING: maafushivaru_hub.py not found in this folder.")
            self._write("Place launch_hub.py in the same folder as the repo files.")

    def _write(self, msg):
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)
        self.update_idletasks()

    # ------------------------------------------------------------------
    def convert(self):
        self.convert_btn.configure(state=tk.DISABLED)
        self._write("")
        self._write(f"--- Converting for {self.os_label} ---")

        # 1. Check / install pip packages
        self._write("Checking required Python packages...")
        missing = []
        for pkg in PIP_PACKAGES:
            mod_name = {
                "PyMuPDF": "fitz", "opencv-python": "cv2", "pillow": "PIL",
            }.get(pkg, pkg.replace("-", "_"))
            try:
                __import__(mod_name)
            except ImportError:
                missing.append(pkg)

        if missing:
            self._write(f"Installing missing packages: {', '.join(missing)}")
            try:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", *missing],
                    check=True
                )
                self._write("Packages installed.")
            except subprocess.CalledProcessError as e:
                self._write(f"pip install failed: {e}")
                messagebox.showerror("Install failed", str(e))
        else:
            self._write("All required packages already installed.")

        # 2. pywin32 / scanner tab note (Windows-only, safe to skip elsewhere)
        if self.system != "Windows":
            self._write("Skipping pywin32 (Windows-only). The Scan tab (direct")
            self._write("scanner capture) will be unavailable — it's already")
            self._write("wrapped in a try/except in maafushivaru_hub.py, so the")
            self._write("rest of the app still runs. Use the SCANNED/ folder")
            self._write("drop-in workflow instead of the Scan tab.")

        # 3. Patch config.json's tesseract_cmd for this OS
        self._write("Locating Tesseract OCR binary...")
        tpath = find_tesseract(self.system)
        if tpath:
            self._write(f"Found tesseract at: {tpath}")
        else:
            self._write("Tesseract not found on this system.")
            if self.system == "Darwin":
                self._write("Install it with:  brew install tesseract")
            elif self.system == "Linux":
                self._write("Install it with:  sudo apt install tesseract-ocr")
            else:
                self._write("Download it from: https://github.com/UB-Mannheim/tesseract/wiki")

        if os.path.isfile(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                old = cfg.get("tesseract_cmd")
                if tpath and old != tpath:
                    cfg["tesseract_cmd"] = tpath
                    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                        json.dump(cfg, f, indent=4)
                    self._write(f"Updated config.json tesseract_cmd:")
                    self._write(f"  was: {old}")
                    self._write(f"  now: {tpath}")
                elif tpath:
                    self._write("config.json tesseract_cmd already correct.")
                else:
                    self._write("Left config.json untouched (no tesseract found yet).")
                    self._write("The app will also auto-detect tesseract on PATH at runtime.")
            except Exception as e:
                self._write(f"Could not update config.json: {e}")
        else:
            self._write("config.json not found — skipping tesseract path patch.")

        self._write("")
        self._write("--- Conversion complete ---")
        self.run_btn.configure(state=tk.NORMAL)
        self.convert_btn.configure(state=tk.NORMAL, text="Re-run Convert")

    # ------------------------------------------------------------------
    def run_app(self):
        if not os.path.isfile(HUB_SCRIPT):
            messagebox.showerror("Not found", f"Could not find {HUB_SCRIPT}")
            return
        self._write("")
        self._write("Launching maafushivaru_hub.py ...")
        self.update_idletasks()
        try:
            subprocess.Popen([sys.executable, HUB_SCRIPT], cwd=APP_DIR)
            self._write("App launched in a separate window.")
        except Exception as e:
            self._write(f"Failed to launch: {e}")
            messagebox.showerror("Launch failed", str(e))


if __name__ == "__main__":
    LauncherGUI().mainloop()
