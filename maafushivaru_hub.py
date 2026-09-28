# maafushivaru_hub.py
# Maafushivaru - Document Processing Hub
# v5.2 - Watchdog auto-ingest, desktop notifications, confidence scoring,
#         supplier learning, scroll fix, live status bar, professional UI

from __future__ import annotations

# ---------------------------------------------------------------------------
# PYTHON INTERPRETER BOOTSTRAP
# ---------------------------------------------------------------------------
# PaddleOCR only works on Python 3.12 here, but many PCs have several Pythons
# installed and plain `python` may point at the wrong one (packages then look
# "not installed" because pip installed them into a different interpreter).
# On start-up this block picks ONE interpreter for the whole application:
#   1. MAAFUSHIVARU_PYTHON environment variable, or "python_exe" under
#      "app_settings" in config.json (full path to python.exe), if set;
#   2. otherwise Python 3.12 (found via the `py` launcher / usual folders);
#   3. otherwise the interpreter that started the script.
# If that is not the running interpreter the script re-launches itself with it,
# so installs, checks and PaddleOCR all use the same Python.
_PREFERRED_PYTHON = (3, 12)


def _bootstrap_python():
    import os, sys, json, shutil, subprocess
    if os.environ.get("MAAFUSHIVARU_BOOTSTRAPPED") == "1" or getattr(sys, "frozen", False):
        return
    script = os.path.abspath(__file__)

    def probe(exe):
        """(major, minor, real_path) of a python executable, or None."""
        try:
            out = subprocess.run(
                [exe, "-c", "import sys;print(sys.version_info[0],sys.version_info[1]);print(sys.executable)"],
                capture_output=True, text=True, timeout=25)
            lines = (out.stdout or "").strip().splitlines()
            if out.returncode == 0 and len(lines) >= 2:
                ma, mi = lines[0].split()
                return int(ma), int(mi), lines[1].strip()
        except Exception:
            pass
        return None

    def same(a, b):
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))

    # 1. explicit override
    target = os.environ.get("MAAFUSHIVARU_PYTHON", "").strip()
    if not target:
        for cfg_path in (os.path.join(os.getcwd(), "config.json"),
                         os.path.join(os.path.dirname(script), "config.json")):
            try:
                with open(cfg_path, encoding="utf-8") as fh:
                    target = str(json.load(fh).get("app_settings", {}).get("python_exe", "")).strip()
                if target:
                    break
            except Exception:
                continue
    if target and not os.path.isfile(target):
        print(f"[BOOT] Configured Python not found: {target} - ignoring it.", flush=True)
        target = ""

    # 2. Python 3.12
    if not target:
        if sys.version_info[:2] == _PREFERRED_PYTHON:
            print(f"[BOOT] Python {sys.version.split()[0]} at {sys.executable}", flush=True)
            return
        want = "%d.%d" % _PREFERRED_PYTHON
        candidates = []
        if os.name == "nt":
            if shutil.which("py"):
                try:
                    r = subprocess.run(["py", f"-{want}", "-c", "import sys;print(sys.executable)"],
                                       capture_output=True, text=True, timeout=25)
                    if r.returncode == 0 and r.stdout.strip():
                        candidates.append(r.stdout.strip().splitlines()[0])
                except Exception:
                    pass
            nodot = want.replace(".", "")
            for base in (os.environ.get("LOCALAPPDATA", "") and os.path.join(os.environ["LOCALAPPDATA"], "Programs", "Python"),
                         os.environ.get("ProgramFiles", r"C:\Program Files") and os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Python" + nodot),
                         "C:\\"):
                if base:
                    d = base if os.path.basename(base).lower().startswith("python" + nodot) else os.path.join(base, "Python" + nodot)
                    candidates.append(os.path.join(d, "python.exe"))
        else:
            for name in (f"python{want}", "python3"):
                w = shutil.which(name)
                if w:
                    candidates.append(w)
        for cand in candidates:
            if cand and os.path.isfile(cand):
                info = probe(cand)
                if info and info[:2] == _PREFERRED_PYTHON:
                    target = info[2]
                    break

    if not target:
        print(f"[BOOT] Python {sys.version.split()[0]} at {sys.executable}", flush=True)
        print("[BOOT] WARNING: Python %d.%d was not found. PaddleOCR needs it - install it from "
              "python.org, or set \"python_exe\" in config.json." % _PREFERRED_PYTHON, flush=True)
        return
    if same(target, sys.executable):
        print(f"[BOOT] Python {sys.version.split()[0]} at {sys.executable}", flush=True)
        return

    print(f"[BOOT] Started with Python {sys.version.split()[0]} ({sys.executable}).", flush=True)
    print(f"[BOOT] Switching to {target} so every package is installed and checked in the "
          "same Python...", flush=True)
    env = dict(os.environ, MAAFUSHIVARU_BOOTSTRAPPED="1")
    try:
        rc = subprocess.call([target, script] + sys.argv[1:], env=env)
    except KeyboardInterrupt:
        rc = 130
    except Exception as exc:
        print(f"[BOOT] Could not switch interpreter ({exc}); continuing here.", flush=True)
        return
    sys.exit(rc)


_bootstrap_python()

import os
import re
import io
import sys
import json
import shutil
import queue
import logging
import threading
import traceback
import concurrent.futures
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Tuple
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import supplier_learning as sl

# Optional dependencies — graceful degradation if missing
try:
    import pytesseract
    PYTESSERACT_AVAILABLE = True
except ImportError:
    PYTESSERACT_AVAILABLE = False
    pytesseract = None

try:
    import pymupdf as fitz  # PyMuPDF — new import name (avoids deprecation warning)
    FITZ_AVAILABLE = True
except ImportError:
    try:
        import fitz  # PyMuPDF — legacy import name (older pymupdf versions)
        FITZ_AVAILABLE = True
    except ImportError:
        FITZ_AVAILABLE = False
        fitz = None

try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    cv2 = None
    np = None

try:
    from PIL import Image, ImageTk, Image as PILImage
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    Image = ImageTk = PILImage = None

try:
    from rapidfuzz import process, fuzz
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
    process = fuzz = None

try:
    from difflib import SequenceMatcher
except ImportError:
    SequenceMatcher = None

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment
    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False
    Workbook = Font = Alignment = None

# ---------------------------------------------------------------------------
# OPTIONAL DEPENDENCIES
# ---------------------------------------------------------------------------
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False

try:
    from plyer import notification as plyer_notification
    PLYER_AVAILABLE = True
except ImportError:
    PLYER_AVAILABLE = False
# New matching strategies
try:
    from supplier_matcher import dispatch_match, STRATEGY_LABELS, STRATEGY_FUNCTIONS
    SUPPLIER_MATCHER_AVAILABLE = True
except ImportError:
    SUPPLIER_MATCHER_AVAILABLE = False
    STRATEGY_LABELS = {}
    STRATEGY_FUNCTIONS = {}

# AI supplier detection
try:
    from ai_supplier_matcher import AISupplierMatcher, PROVIDERS, STATUS_CONNECTED, \
        STATUS_DISCONNECTED, STATUS_LOW_CREDIT, STATUS_OFFLINE
    AI_MATCHER_AVAILABLE = True
except ImportError:
    AI_MATCHER_AVAILABLE = False
    PROVIDERS = {}
    STATUS_CONNECTED    = "connected"
    STATUS_DISCONNECTED = "disconnected"
    STATUS_LOW_CREDIT   = "low_credit"
    STATUS_OFFLINE      = "offline"
# OCR word corrector
try:
    from ocr_word_corrector import correct_supplier_in_text, correct_invoice_number
    OCR_CORRECTOR_AVAILABLE = True
except ImportError:
    OCR_CORRECTOR_AVAILABLE = False
    def correct_supplier_in_text(text, suppliers, aliases):
        return text, None
    def correct_invoice_number(raw, supplier):
        return raw

# Smart cross-matcher
try:
    from smart_cross_matcher import infer_invoice_from_supplier, infer_supplier_from_invoice
    CROSS_MATCHER_AVAILABLE = True
except ImportError:
    CROSS_MATCHER_AVAILABLE = False
    def infer_invoice_from_supplier(raw, supplier):
        return raw
    def infer_supplier_from_invoice(text, suppliers, aliases):
        return None, 0.0

# Central OCR engine / dependency manager (availability checks, installers and
# the persisted first-run setup state). The application keeps working without
# it, but then no engine can be installed from inside the interface.
try:
    import ocr_engine_manager as oem
    ENGINE_MANAGER_AVAILABLE = True
except ImportError:
    oem = None
    ENGINE_MANAGER_AVAILABLE = False

# ---------------------------------------------------------------------------
# CONSTANTS / COLORS
# ---------------------------------------------------------------------------
APP_TITLE   = "Maafushivaru - Document Processing Hub"
APP_VERSION = "v5.4"

# Color palette — professional dark theme
BG       = "#0A0F1E"          # deepest background
PANEL    = "#111827"          # card / panel background
PANEL2   = "#1F2937"          # slightly lighter panel
PANEL3   = "#374151"          # hover / border
TEXT     = "#F9FAFB"          # primary text
MUTED    = "#9CA3AF"          # secondary text
ACCENT   = "#3B82F6"          # primary blue
ACCENT_H = "#2563EB"          # hover blue
ACCENT2  = "#8B5CF6"          # purple
SUCCESS  = "#10B981"          # green
WARNING  = "#F59E0B"          # amber
ERROR    = "#EF4444"          # red
BORDER   = "#1F2937"          # subtle border
WATCHER  = "#06B6D4"          # cyan for watcher badge

ENGINE_LABELS  = {"tesseract": "Tesseract OCR", "paddleocr": "PaddleOCR", "easyocr": "EasyOCR"}
ENGINE_INSTALL = {
    "tesseract": "Built-in binary",
    "paddleocr": "pip install paddlepaddle paddleocr",
    "easyocr":   "pip install easyocr",
}
ENGINE_MODULES = {"tesseract": "pytesseract", "paddleocr": "paddleocr", "easyocr": "easyocr"}
_ENGINE_CACHE  = {}
NUMBER_FMT = "#,##0.00"

# ---------------------------------------------------------------------------
# GRN DISPATCH ENGINE CHOICES
# ---------------------------------------------------------------------------
# ONLINE  -> OCR.space API, with the three API engines (1 Default, 2 Enhanced,
#            3 Extra Accurate) individually selectable.
# OFFLINE -> a LOCAL engine (Tesseract / PaddleOCR / EasyOCR), selectable in
#            Settings so the offline pipeline is no longer hard-wired to one
#            engine. The choice is persisted and honoured by both manual and
#            auto-ingest runs.
GRN_ONLINE = "online"
GRN_OFFLINE = "offline"


def _engine_mgr():
    """The OCR engine manager when available (install/availability helpers)."""
    return oem if ENGINE_MANAGER_AVAILABLE else None


def local_engine_keys() -> "List[str]":
    """Keys of the local (offline) OCR engines."""
    mgr = _engine_mgr()
    if mgr is not None:
        return mgr.offline_engine_keys()
    return ["tesseract"]


def local_engine_label(key: str) -> str:
    mgr = _engine_mgr()
    if mgr is not None:
        return mgr.engine_short(key)
    return ENGINE_LABELS.get(key, key)


def local_engine_installed(key: str) -> bool:
    """Deep availability check for a local engine (python module + program)."""
    mgr = _engine_mgr()
    if mgr is not None:
        try:
            return bool(mgr.engine_installed(key))
        except Exception:
            pass
    try:
        __import__(ENGINE_MODULES.get(key, key))
        if key == "tesseract" and pytesseract is not None:
            pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def grn_engine_choices(with_status: bool = True) -> "List[str]":
    """Every selectable GRN Dispatch engine (ONLINE first, then the local ones).

    `with_status=False` drops the "not installed" annotation; the debugger uses
    that form because it reports each engine's status separately.
    """
    mgr = _engine_mgr()
    if mgr is not None:
        return mgr.choice_labels(with_status=with_status)
    return [
        "Online — OCR.space Engine 1 (Default)",
        "Online — OCR.space Engine 2 (Enhanced)",
        "Online — OCR.space Engine 3 (Extra Accurate)",
        "Offline — Tesseract (Local)",
    ]


def grn_choice_label(mode: str, value) -> str:
    """Label for a saved (mode, value) pair, tolerating old config files."""
    mgr = _engine_mgr()
    if mgr is not None:
        return mgr.label_for_spec(mode, value)
    if mode == GRN_ONLINE:
        num = value if value in (1, 2, 3) else 2
        return {
            1: "Online — OCR.space Engine 1 (Default)",
            2: "Online — OCR.space Engine 2 (Enhanced)",
            3: "Online — OCR.space Engine 3 (Extra Accurate)",
        }[num]
    return f"Offline — {ENGINE_LABELS.get(str(value), 'Tesseract OCR')} (Local)"


def grn_choice_parts(label: str):
    """Split a combo label back into (mode, value).

    `value` is the OCR.space engine number for ONLINE, or the local engine key
    for OFFLINE. Unknown labels fall back to OFFLINE + Tesseract so a stale
    saved value can never break the pipeline.
    """
    mgr = _engine_mgr()
    if mgr is not None:
        return mgr.parse_choice(label)
    text = (label or "").lower()
    if text.startswith("online"):
        for num in (1, 2, 3):
            if f"engine {num}" in text:
                return GRN_ONLINE, num
        return GRN_ONLINE, 2
    for key in ("easyocr", "paddleocr"):
        if key in text:
            return GRN_OFFLINE, key
    return GRN_OFFLINE, "tesseract"

ZONE_OCR_REGIONS = {
    # Each entry is (top_pct, bottom_pct, left_pct, right_pct)
    # These cover the areas where GRN, PO, supplier, date and invoice
    # data consistently appear in Birchstreet receiving reports.
    "header_top":    (0.00, 0.08, 0.00, 1.00),   # "Receiving Record #" and "Purchase Order #" line
    "header_meta":   (0.08, 0.22, 0.00, 1.00),   # Received by / date / PO status block
    "invoice_block": (0.22, 0.38, 0.00, 1.00),   # Invoice number / subtotal / total block
    "supplier_col":  (0.22, 0.38, 0.60, 1.00),   # Right column: Supplier name area
}

# Minimum characters a zone must return before we accept it as valid.
_ZONE_MIN_CHARS = 20

# How many zones must succeed for us to trust zone results over full-page OCR.
_ZONE_MIN_SUCCESS = 2

# Scanned (image-only) pages are OCR'd at no less than this many DPI. The old
# behaviour rendered at image_scale_factor x 72 DPI (2 -> 144 DPI), which is far
# too small for Birchstreet printouts (tiny table text): Tesseract returned
# little or nothing. 300 DPI matches the scanner's native resolution.
_OCR_MIN_DPI = 300

# A page whose OCR text contains this many recognisable report/invoice words
# (or a GRN / PO marker) is considered "read correctly".
_OCR_GOOD_HITS = 6
_OCR_VOCAB = frozenset("""
RECEIVING RECORD REPORT PURCHASE ORDER INVOICE SUPPLIER BUYER STOREROOM
DEPARTMENT SUBTOTAL TOTAL TAX FREIGHT DISCOUNT AMOUNT DATE NUMBER DELIVERY
CUSTOMER DESCRIPTION QTY UNIT RATE ITEM CODE PRICE STATUS COMPLETE NOTES
PRODUCT GST MVR USD EUR GBP SGD SHOP CASH BILL PAYMENT DUE REFERENCE
""".split())
_GRN_MARK_RE = re.compile(r"RC[-\s]*MAM[-\s]*\d{3,}")
_PO_MARK_RE = re.compile(r"PO[-\s]*MAM[-\s]*\d{3,}")


class OCRUnavailableError(RuntimeError):
    """Raised when Tesseract itself cannot run (binary/module missing).

    This used to be swallowed and turned into an empty string, which showed up
    in the UI as 'no text extracted' with no explanation.
    """

# ---------------------------------------------------------------------------
# THREAD-LOCAL SUPPLIER TRACKING
# ---------------------------------------------------------------------------
_thread_local = threading.local()

def _get_last_good_supplier():
    return getattr(_thread_local, "last_good_supplier", None)

def _set_last_good_supplier(value):
    _thread_local.last_good_supplier = value

def _reset_last_good_supplier():
    _thread_local.last_good_supplier = None

# ---------------------------------------------------------------------------
# CURRENCY MAPS
# ---------------------------------------------------------------------------
_CURRENCY_MAP = {
    "$": "USD", "US$": "USD", "USD": "USD",
    "MVR": "MVR", "RF": "MVR", "MRF": "MVR",
    "MYR": "MVR",   # OCR sometimes reads MVR as MYR — treat as MVR
    "EUR": "EUR", "€": "EUR",
    "GBP": "GBP", "£": "GBP",
    "SGD": "SGD", "S$": "SGD",
}
_INVOICE_CURRENCY_TOKENS = {
    "USD", "MVR", "RF", "MRF", "EUR", "GBP", "SGD", "MYR",
    "US$", "S$", "$", "€", "£"
}

# Invoice-total OCR parser.  OCR engines frequently damage currency symbols and
# thousands separators, so invoice totals are deliberately parsed in two stages:
# 1) find the complete "Invoice Total" line;
# 2) independently repair/parse the currency and numeric value.
#
# Examples intentionally supported:
#   Invoice Total: S1,296.00      -> USD 1296.00   ($ misread as S)
#   Invoice Total: S1,194.48      -> USD 1194.48
#   Invoice Total: MVR1.720.00    -> MVR 1720.00   (OCR used . for ,)
#   Invoice Total: MVR8.444.00    -> MVR 8444.00
#   Invoice Total: $ 1,296.00     -> USD 1296.00
#   Invoice Total: MVR 8,444.00   -> MVR 8444.00
# OCR-tolerant invoice-total label matcher.
# Handles common OCR variants such as "Involce Total" / "Invo1ce Total".
_REPORT_INVOICE_TOTAL_LINE_PAT = re.compile(
    r"(?im)^\s*(?:"
    r"I?NVOICE|INVO1CE|1NVOICE|INVOLCE|INVOlCE"
    r")\s*"
    r"(?:TOTAL|T0TAL|T0TAI|T0TA)\s*[:\-]?\s*(.*?)\s*$"
)

# Currency spellings that OCR commonly produces.  A lone S immediately before
# an invoice-total amount is treated as the dollar sign -> USD.  SGD must still
# be written as SGD/S$ so we do not confuse it with USD.
_OCR_CURRENCY_PAT = re.compile(
    r"(?i)^(?P<cur>US\s*\$|S\s*\$|USD|MVR|MYR|MRF|RF|SGD|EUR|GBP|\$|€|£|S)\s*"
)
_OCR_CURRENCY_AFTER_PAT = re.compile(
    r"(?i)\s*(?P<cur>USD|MVR|MYR|MRF|RF|SGD|EUR|GBP|S\s*\$|US\s*\$|\$|€|£)\s*$"
)

# Amount characters most often confused by OCR.  These replacements are made
# ONLY inside the numeric token, never in the surrounding invoice text.
_OCR_AMOUNT_DIGIT_FIX = str.maketrans({
    "O": "0", "Q": "0", "D": "0",
    "I": "1", "L": "1", "|": "1", "!": "1",
    "Z": "2", "E": "3", "A": "4", "S": "5", "G": "6",
    "T": "7", "J": "7", "B": "8",
})


def _normalize_ocr_currency_token(cur: str) -> str:
    """Return our canonical currency code from an OCR currency token."""
    if not cur:
        return ""
    c = re.sub(r"\s+", "", cur.upper())
    if c in ("$", "US$", "USD", "S"):
        return "USD"
    if c in ("MVR", "MYR", "MRF", "RF"):
        return "MVR"
    if c in ("EUR", "€"):
        return "EUR"
    if c in ("GBP", "£"):
        return "GBP"
    if c in ("SGD", "S$"):
        return "SGD"
    return _CURRENCY_MAP.get(c, "")


def _parse_ocr_invoice_amount(raw_amount: str) -> Optional[float]:
    """Parse an OCR-damaged money amount without trusting its separators.

    OCR can turn a thousands comma into a dot, e.g. 8,444.00 -> 8.444.00.
    It can also produce repeated separators or spaces.  The final separator is
    considered the decimal separator when it has 1-2 trailing digits; all
    earlier separators are thousands separators.  A single separator followed
    by exactly three digits is treated as a thousands separator.
    """
    if not raw_amount:
        return None

    s = raw_amount.upper().strip()
    # Remove currency-like residue that may have remained attached to the amount.
    s = re.sub(r"[^0-9A-Z.,\s]", "", s)
    s = s.translate(_OCR_AMOUNT_DIGIT_FIX)
    s = re.sub(r"\s+", "", s)
    if not s:
        return None

    # Keep only numeric/separator material after OCR character repair.
    s = re.sub(r"[^0-9.,]", "", s)
    if not re.search(r"\d", s):
        return None

    # If there are multiple separators, the last one is the decimal separator
    # when it has one or two digits after it.  Everything before it is grouping.
    dot_positions = [m.start() for m in re.finditer(r"\.", s)]
    comma_positions = [m.start() for m in re.finditer(r",", s)]
    all_positions = sorted(dot_positions + comma_positions)

    if not all_positions:
        try:
            return float(s)
        except ValueError:
            return None

    last_pos = all_positions[-1]
    trailing = s[last_pos + 1:]
    last_sep = s[last_pos]

    if len(trailing) in (1, 2) and trailing.isdigit():
        # Last separator is decimal.  Earlier separators are thousands.
        whole = re.sub(r"[.,]", "", s[:last_pos]) or "0"
        decimal = trailing
        normalized = whole + "." + decimal
    elif len(trailing) == 3 and len(all_positions) == 1:
        # Classic 1,296 / 1.296 thousands grouping.
        normalized = re.sub(r"[.,]", "", s)
    else:
        # No convincing decimal part: treat every separator as grouping.
        normalized = re.sub(r"[.,]", "", s)

    try:
        return float(normalized)
    except ValueError:
        return None


def _extract_ocr_invoice_totals(raw: str) -> List[Tuple[str, float, str]]:
    """Extract invoice totals with aggressive, context-limited OCR repair.

    Returns (currency, numeric_value, repaired_line).  Parsing is restricted to
    lines labelled Invoice Total so ordinary item numbers elsewhere in the OCR
    text cannot accidentally become totals.
    """
    results = []
    if not raw:
        return results

    for match in _REPORT_INVOICE_TOTAL_LINE_PAT.finditer(raw):
        payload = (match.group(1) or "").strip()
        if not payload:
            continue

        currency = ""
        amount_part = payload

        # Currency before amount: handles $, S, MVR, MYR, RF, etc., with or
        # without a space between currency and amount.
        cm = _OCR_CURRENCY_PAT.match(amount_part)
        if cm:
            currency = _normalize_ocr_currency_token(cm.group("cur"))
            amount_part = amount_part[cm.end():].strip()

        # Currency after amount is also accepted.
        if not currency:
            cm_after = _OCR_CURRENCY_AFTER_PAT.search(amount_part)
            if cm_after:
                currency = _normalize_ocr_currency_token(cm_after.group("cur"))
                amount_part = amount_part[:cm_after.start()].strip()

        # OCR sometimes inserts junk between the currency and the number.  Do
        # not let arbitrary letters become part of the amount; locate the first
        # plausible numeric run and trim the rest to that run.
        if not re.search(r"\d", amount_part):
            continue
        nm = re.search(r"[0-9OQDI L|!ZEA SGBTJ,\.]+", amount_part, re.IGNORECASE)
        if not nm:
            continue
        numeric_raw = re.sub(r"\s+", "", nm.group(0))
        value = _parse_ocr_invoice_amount(numeric_raw)
        if value is None:
            continue

        # If OCR dropped the currency entirely, do not guess: the caller can
        # continue safely without inventing a currency.
        if not currency:
            continue

        repaired = f"{currency}{value:.2f}"
        results.append((currency, value, repaired))

    return results


def _fix_ocr_currency_amount(raw: str) -> str:
    """Repair common OCR currency/amount errors before legacy matching.

    This function remains for compatibility with the rest of the application,
    while the invoice-total parser above performs the stronger amount parsing.
    """
    if not raw:
        return raw

    fixed = raw
    fixed = re.sub(r"\bMYR\b", "MVR", fixed, flags=re.IGNORECASE)

    # MVR I,380.14 / USD l23.50 -> MVR 1,380.14 / USD 123.50
    fixed = re.sub(
        r"\b(MVR|MYR|USD|EUR|GBP|SGD|RF|MRF)\s*([Il|!])(?=[\d,\.])",
        lambda m: ("MVR" if m.group(1).upper() == "MYR" else m.group(1).upper()) + "1",
        fixed,
        flags=re.IGNORECASE,
    )

    # A lone OCR 'S' directly before a money amount is commonly a damaged '$'.
    # Restrict this to Invoice Total lines so ordinary text is never changed.
    fixed = re.sub(
        r"(?im)(^\s*(?:I?NVOICE|INVO1CE|1NVOICE)\s*TOTAL\s*[:\-]?\s*)S(?=\s*\d)",
        r"\1$",
        fixed,
    )

    return fixed

SUPPLIER_INVOICE_HINTS = {
    "BEST BUY":     [r"BB[-\/]\d{3,10}", r"\d{5,10}"],
    "BESTBUY":      [r"BB[-\/]\d{3,10}", r"\d{5,10}"],
    "STANDARD":     [r"SI[-\/]\d{5,10}", r"IS[-\/]\d{5,10}", r"\d{5,10}"],
    "ORIGIN":       [r"SI[-\/]\d{5,10}", r"IS[-\/]\d{5,10}", r"\d{5,10}"],
    "EURO MARKET":  [r"[A-Z]{2,4}[-\/]\d{4,10}", r"\d{5,10}"],
    "LYCORN":       [r"\d{5,10}", r"[A-Z]{2,4}[-\/]\d{4,10}"],
    "COSMO":        [r"[A-Z]{2,4}[-\/]\d{4,10}\/\d{4}", r"[A-Z]{2,4}[-\/]\d{4,10}"],
    "EMPARAL":      [r"\d{5,10}", r"[A-Z]{2,4}[-\/]\d{4,10}"],
    "HAPPY MARKET": [r"\d{5,10}"],
    "SAWHNEY":      [r"[A-Z]{2,4}\/\d{1,10}\/\d{4}", r"[A-Z]{2,4}[-]\d{4,10}"],
    "EASTERN":      [r"[A-Z]{2,4}[-\/]\d{4,10}", r"\d{5,10}"],
    "FOOD SPECIAL": [r"\d{5,10}", r"[A-Z]{2,4}[-\/]\d{4,10}"],
    "CHEF":         [r"[A-Z]{2,4}[-\/]\d{4,10}", r"\d{5,10}"],
    "SEAFOOD":      [r"\d{5,10}", r"[A-Z]{2,4}[-\/]\d{4,10}"],
}

_INVOICE_LABEL_PATS = [
    r"INVOICE\s*(?:NUMBER|NUM(?:BER)?|NO\.?|#|NR\.?)\s*[:\-]",
    r"INV\.?\s*(?:NO\.?|#|NUMBER)\s*[:\-]",
    r"TAX\s*INVOICE\s*(?:NO\.?|#|NUMBER)?\s*[:\-]",
    r"BILL\s*(?:NO\.?|NUMBER|#)\s*[:\-]",
    r"CREDIT\s*NOTE\s*(?:NO\.?|#|NUMBER)?\s*[:\-]",
]


_INVOICE_STOP_WORDS = [
    "DATE", "GRN", "RECEIVING", "SUPPLIER", "VENDOR", "PURCHASE ORDER",
    "SUBTOTAL", "TOTAL", "AMOUNT", "QTY", "QUANTITY", "DESCRIPTION", "TAX",
]


def _find_invoice_value(raw: str) -> Optional[str]:
    for label_pat in _INVOICE_LABEL_PATS:
        lm = re.search(label_pat, raw)
        if not lm:
            continue
        rest = raw[lm.end(): lm.end() + 200]

        # Cut off at the next line break, or the next field label found on
        # the same line (so we don't swallow "DATE: ..." etc into the value)
        cut_pos = len(rest)
        nl_pos = rest.find("\n")
        if nl_pos != -1:
            cut_pos = min(cut_pos, nl_pos)
        for sw in _INVOICE_STOP_WORDS:
            m_sw = re.search(r"\b" + sw + r"\b", rest[:cut_pos])
            if m_sw:
                cut_pos = min(cut_pos, m_sw.start())

        value = rest[:cut_pos]
        # Collapse any double/triple spaces from OCR into single spaces,
        # but keep the FULL value instead of stopping at the first token.
        value = re.sub(r"\s+", " ", value).strip(" -:.")
        if value and len(value) >= 2:
            return value
    return None


def _clean_invoice_token(token: str) -> str:
    if not token:
        return ""
    t = token.strip().upper()
    if t in _INVOICE_CURRENCY_TOKENS:
        return ""
    for cur in sorted(_INVOICE_CURRENCY_TOKENS, key=len, reverse=True):
        t = t.replace(cur, " ")
    t = re.sub(r"\s+", " ", t).strip(" -:./\\")
    return t


# ---------------------------------------------------------------------------
# OCR ROBUSTNESS HELPERS
#   - fuzzy label detection (handles staple holes / smudged characters)
#   - digit confusion fixing for RC-MAM GRN numbers
#   - invoice series-prefix correction for underline misreads (I -> L / 1)
# ---------------------------------------------------------------------------

# Letters most commonly produced when an underline merges with a digit's glyph,
# mapped back to the digit they should be. Applied ONLY to the numeric region
# of an RC-MAM code, never to the 'RC'/'MAM' prefix.
_OCR_DIGIT_FIX = str.maketrans({
    "O": "0", "Q": "0", "D": "0",
    "I": "1", "L": "1", "|": "1", "!": "1",
    "Z": "2",
    "E": "3",
    "A": "4",
    "S": "5",
    "G": "6",
    "T": "7", "J": "7",
    "B": "8",
})


def _levenshtein(a: str, b: str) -> int:
    """Plain edit distance for short tokens (label words)."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * lb
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[lb]


def _label_present(text: str, label: str, max_dist: int = 2) -> bool:
    """True if every word of `label` appears (in order, consecutively) inside
    `text`, allowing up to `max_dist` single-character OCR errors per word.

    This is what lets the app still recognise 'RECEIVING RECORD' when a staple
    hole or smudge makes one or more characters unreadable, e.g. 'RECEIVING
    RECORO', 'RECE1VING RECORD', 'RECEIVNG RECORD'.
    """
    words = re.findall(r"[A-Z0-9]+", (text or "").upper())
    label_words = label.upper().split()
    n = len(label_words)
    W = len(words)
    if n == 0 or W == 0:
        return False

    def _word_ok(w: str, lw: str) -> bool:
        return abs(len(w) - len(lw)) <= 1 and _levenshtein(w, lw) <= max_dist

    # Match label words in order. Each label word may match a single input word
    # OR two adjacent input words joined together - this covers staple holes that
    # split a word in two (e.g. 'RECEIV NG' -> 'RECEIVING').
    for start in range(W):
        wi = start
        ok = True
        for lw in label_words:
            if wi >= W:
                ok = False
                break
            if _word_ok(words[wi], lw):
                wi += 1
            elif wi + 1 < W and _word_ok(words[wi] + words[wi + 1], lw):
                wi += 2
            else:
                ok = False
                break
        if ok:
            return True
    return False


def _fix_invoice_prefix(token: str, fixes: dict) -> str:
    """Correct an OCR-misread invoice *series prefix* using a config map.

    Underlined invoice codes like 'MSI-282910' are frequently read as
    'MSL-282910' (the underline turns the 'I' into an 'L') or 'MS1-282910'.
    `fixes` maps the wrong prefix to the right one, e.g. {'MSL': 'MSI'}.
    Only the leading alphabetic/numeric prefix before the first separator is
    touched, so the digits and everything else are preserved exactly.
    """
    if not token or not fixes:
        return token
    t = token.strip()
    m = re.match(r"^([A-Z0-9]{2,6})([\-/ ].*)?$", t.upper())
    if not m:
        return t
    prefix = m.group(1)
    rest = m.group(2) or ""
    fixed = fixes.get(prefix)
    if fixed:
        return fixed + rest
    return t


def _build_unique_word_index(suppliers: list, aliases: dict) -> dict:
    from collections import Counter
    all_cands = list(suppliers)
    for sub in aliases.values():
        all_cands.extend(sub)

    word_freq = Counter()
    cand_words = {}
    for c in all_cands:
        words = set(re.sub(r"[^A-Z0-9 ]", " ", c.upper()).split())
        words = {w for w in words if len(w) >= 3}
        cand_words[c] = words
        word_freq.update(words)

    unique_index = {}
    for c, words in cand_words.items():
        unique_index[c] = {w for w in words if word_freq[w] == 1}

    return unique_index


def _fmt_currency(val) -> str:
    if val in ("", None):
        return ""
    try:
        f = float(str(val).replace(",", ""))
        return f"{f:.2f}"
    except (ValueError, TypeError):
        return str(val)


# ---------------------------------------------------------------------------
# DESKTOP NOTIFICATION HELPER
# ---------------------------------------------------------------------------
def send_desktop_notification(title: str, message: str, timeout: int = 8):
    """Send a system notification. Silently no-ops if plyer not installed."""
    if not PLYER_AVAILABLE:
        return
    try:
        plyer_notification.notify(
            title=title,
            message=message,
            app_name="Maafushivaru Hub",
            timeout=timeout,
        )
    except Exception as e:
        logging.warning(f"Desktop notification failed: {e}")


# ---------------------------------------------------------------------------
# WATCHDOG FILE HANDLER
# ---------------------------------------------------------------------------
class ScannedFolderHandler:
    """Wraps watchdog logic; calls `on_new_pdf(path)` when a new PDF is stable."""

    def __init__(self, on_new_pdf, debounce_seconds: float = 3.0):
        self._on_new_pdf = on_new_pdf
        self._debounce = debounce_seconds
        self._pending: Dict[str, threading.Timer] = {}
        self._lock = threading.Lock()
        self._stopped = False

    def _schedule(self, path: str):
        with self._lock:
            if self._stopped:
                return
            if path in self._pending:
                self._pending[path].cancel()
            t = threading.Timer(self._debounce, self._fire, args=(path,))
            t.daemon = True
            self._pending[path] = t
            t.start()

    def _fire(self, path: str):
        with self._lock:
            self._pending.pop(path, None)
        if self._stopped:
            return
        if os.path.exists(path) and path.lower().endswith(".pdf"):
            self._on_new_pdf(path)

    def dispatch(self, event):
        """Called by watchdog observer for any filesystem event."""
        if event.is_directory:
            return
        src = getattr(event, "src_path", "")
        if src.lower().endswith(".pdf"):
            self._schedule(src)

    def shutdown(self):
        """Cancel all pending debounce timers. Call before discarding the handler."""
        with self._lock:
            self._stopped = True
            for timer in self._pending.values():
                timer.cancel()
            self._pending.clear()


if WATCHDOG_AVAILABLE:
    class _WatchdogEventAdapter(FileSystemEventHandler):
        def __init__(self, handler: ScannedFolderHandler):
            super().__init__()
            self._h = handler

        def on_created(self, event):
            self._h.dispatch(event)

        def on_modified(self, event):
            self._h.dispatch(event)

        def on_moved(self, event):
            # treat the destination as new
            class _Fake:
                def __init__(self, p):
                    self.src_path = p
                    self.is_directory = False
            self._h.dispatch(_Fake(getattr(event, "dest_path", "")))


# ---------------------------------------------------------------------------
# OCR / EXTRACTION WORKER MIXIN
# ---------------------------------------------------------------------------
class OCRWorkerMixin:

    # ------------- ENGINE HELPERS -------------
    @staticmethod
    def _check_engine(key):
        """True when the engine is really usable (module AND program present).

        `paddleocr`/`easyocr` previously reported "Installed" as soon as the
        python package was importable, even when the engine could not run.
        The deep check lives in ocr_engine_manager so the Settings screen and
        the pipeline always agree.
        """
        if key == "tesseract":
            if pytesseract is None:
                return False
            try:
                pytesseract.get_tesseract_version()
                return True
            except Exception:
                return False
        if key in ("paddleocr", "easyocr"):
            return local_engine_installed(key)
        try:
            __import__(ENGINE_MODULES.get(key, key))
            return True
        except ImportError:
            return False

    def _get_engine_instance(self, key):
        """Create (and cache) a heavy local engine instance, or raise.

        The import happens HERE, inside the worker thread, never at startup:
        importing easyocr/paddleocr loads PyTorch/PaddlePaddle which takes
        several seconds and a lot of memory.
        """
        if key in _ENGINE_CACHE:
            return _ENGINE_CACHE[key]
        if key == "paddleocr":
            from paddleocr import PaddleOCR
            # PaddleOCR 3.x replaced the deprecated `use_angle_cls` option
            # with `use_textline_orientation`. Keep a fallback for older
            # PaddleOCR 2.x installations.
            try:
                _ENGINE_CACHE[key] = PaddleOCR(
                    use_textline_orientation=True,
                    lang="en",
                )
            except (TypeError, ValueError):
                try:
                    _ENGINE_CACHE[key] = PaddleOCR(
                        use_angle_cls=True,
                        lang="en",
                        show_log=False,
                    )
                except TypeError:
                    _ENGINE_CACHE[key] = PaddleOCR(
                        use_angle_cls=True,
                        lang="en",
                    )
        elif key == "easyocr":
            import easyocr
            _ENGINE_CACHE[key] = easyocr.Reader(["en"], verbose=False)
        elif key == "tesseract":
            self._ensure_tesseract()
        return _ENGINE_CACHE.get(key)

    def _run_local_engine_ocr(self, arr, engine_key: str) -> str:
        """Run one local (offline) engine on a grayscale array.

        Raises OCRUnavailableError with a readable message when the engine is
        not installed, so the UI can say "engine not found" instead of
        silently returning nothing.
        """
        if engine_key == "tesseract":
            self._ensure_tesseract()
            psm = self.cfg["app_settings"].get("ocr_psm", 6)
            oem_val = self.cfg["app_settings"].get("ocr_oem", 3)
            try:
                return self._fix_ocr_tokens(
                    pytesseract.image_to_string(arr, config=f"--oem {oem_val} --psm {psm}")
                ).upper()
            except Exception as e:
                self._last_ocr_error = f"Tesseract OCR failed: {e}"
                if isinstance(e, getattr(pytesseract, "TesseractNotFoundError", ())):
                    raise OCRUnavailableError(
                        "Tesseract OCR is not installed (program not found). "
                        "Install it from https://github.com/UB-Mannheim/tesseract/wiki, "
                        "or open Settings → OCR Engines and press Install."
                    )
                return ""

        if not self._check_engine(engine_key):
            raise OCRUnavailableError(
                f"{ENGINE_LABELS.get(engine_key, engine_key)} is not installed on this "
                f"computer. Open Settings → OCR Engines and press Install, or select "
                f"Tesseract OCR / an Online engine instead."
            )
        try:
            instance = self._get_engine_instance(engine_key)
        except Exception as e:
            raise OCRUnavailableError(
                f"{ENGINE_LABELS.get(engine_key, engine_key)} could not be started: {e}"
            )
        if instance is None:
            raise OCRUnavailableError(
                f"{ENGINE_LABELS.get(engine_key, engine_key)} engine is not available."
            )
        if engine_key == "paddleocr":
            # PaddleOCR 3.x uses `predict()` and returns Result objects whose
            # JSON data contains `rec_texts`. Older 2.x releases use `ocr()`.
            if hasattr(instance, "predict"):
                results = instance.predict(arr)
                texts = []
                for res in results:
                    data = getattr(res, "json", None)
                    if callable(data):
                        data = data()
                    if isinstance(data, str):
                        try:
                            data = json.loads(data)
                        except Exception:
                            data = None
                    if isinstance(data, dict):
                        data = data.get("res", data)
                        rec_texts = data.get("rec_texts", []) if isinstance(data, dict) else []
                        if rec_texts:
                            texts.extend(str(t) for t in rec_texts if str(t).strip())
                return " ".join(texts).upper()
            r = instance.ocr(arr, cls=True)
            return " ".join(l[1][0] for l in r[0]).upper() if r and r[0] else ""
        if engine_key == "easyocr":
            return " ".join(instance.readtext(arr, detail=0)).upper()
        return ""

    # ------------- IMAGE / OCR -------------
    @staticmethod
    def _remove_table_lines(gray):
        """Erase the long horizontal/vertical ruling lines of Birchstreet tables.

        Tesseract treats grid lines as glyph strokes: they glue to characters
        ("MVR1,060.00" -> "MVRI.060.00"), split cells into ragged fragments and
        push it into the wrong layout mode. OCR.space removes them internally;
        we do the same here with morphological opening, scaled to the DPI.
        """
        try:
            h, w = gray.shape[:2]
            k = max(0.5, w / 2480.0)
            bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                       cv2.THRESH_BINARY_INV, 41, 15)
            hor = cv2.morphologyEx(bw, cv2.MORPH_OPEN,
                                   cv2.getStructuringElement(cv2.MORPH_RECT, (max(25, int(80 * k)), 1)))
            ver = cv2.morphologyEx(bw, cv2.MORPH_OPEN,
                                   cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(25, int(60 * k)))))
            mask = cv2.dilate(cv2.bitwise_or(hor, ver), np.ones((3, 3), np.uint8))
            out = gray.copy()
            out[mask > 0] = 255
            return out
        except Exception as e:
            logging.debug(f"Table-line removal skipped: {e}")
            return gray

    def _preprocess_image(self, pix, denoise: Optional[bool] = None):
        """Return a clean GRAYSCALE image for OCR.

        The old version always finished with a hard Otsu black/white threshold.
        On 300 DPI scans that eats thin strokes and merges neighbours (wrong
        characters everywhere). Tesseract's own LSTM binariser (Leptonica) does
        a much better job on grayscale, so we no longer threshold unless
        app_settings["ocr_binarize"] is explicitly turned on.
        """
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
        if pix.n == 4:
            gray = cv2.cvtColor(img, cv2.COLOR_RGBA2GRAY)
        elif pix.n == 3:
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        else:
            gray = img.squeeze() if img.ndim == 3 else img
        gray = np.ascontiguousarray(gray)

        s = self.cfg["app_settings"]
        if s.get("enhance_images", False):
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            gray = clahe.apply(gray)
            if denoise is None:
                denoise = max(gray.shape[:2]) < 2400
            if denoise:
                gray = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)

        if s.get("ocr_remove_table_lines", True):
            gray = self._remove_table_lines(gray)

        if s.get("ocr_binarize", False):
            _, gray = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return gray

    def _correct_rotation(self, arr: np.ndarray, full_page: bool = True) -> np.ndarray:
        """
        Correct 90/180/270 rotations (Tesseract OSD) and small deskew angles.

        OSD is only trusted on a FULL page image and only when Tesseract reports
        a confident orientation. On the header/zone crops the old code
        (a) let OSD guess from a few lines of text, which flipped upright
        strips upside-down, and (b) when OSD raised "too few characters" it
        rotated every wide crop 90 degrees. Both destroyed the text and were the
        reason zone OCR returned nothing usable. Crops are now never rotated.
        """
        if full_page and min(arr.shape[:2]) >= 800:
            try:
                osd = pytesseract.image_to_osd(arr, config="--psm 0 -c min_characters_to_try=50")
                m = re.search(r"Rotate:\s*(\d+)", osd)
                c = re.search(r"Orientation confidence:\s*([\d.]+)", osd)
                angle = int(m.group(1)) if m else 0
                conf = float(c.group(1)) if c else 0.0
                if angle and conf >= 3.0:
                    if angle == 90:
                        arr = cv2.rotate(arr, cv2.ROTATE_90_COUNTERCLOCKWISE)
                    elif angle == 180:
                        arr = cv2.rotate(arr, cv2.ROTATE_180)
                    elif angle == 270:
                        arr = cv2.rotate(arr, cv2.ROTATE_90_CLOCKWISE)
                    logging.info(f"OCR: rotated page by {angle} deg (OSD confidence {conf:.1f})")
            except Exception as e:
                # OSD unavailable / too little text: leave the page as it is.
                logging.debug(f"OSD skipped: {e}")

        # --- Step 2: fine deskew for small tilt angles (±15°) ---
        if full_page:
            try:
                arr = self._deskew(arr)
            except Exception as e:
                logging.debug(f"Deskew skipped: {e}")

        return arr

    def _deskew(self, arr: np.ndarray) -> np.ndarray:
        """
        Detect and correct small tilt angles in a grayscale image.
        Uses morphological operations + Hough line transform.
        Only applies correction for angles in the range ±15° to avoid
        false corrections on legitimate landscape content.
        """
        # Work on a copy; ensure grayscale
        gray = arr.copy()
        if gray.ndim == 3:
            gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)

        # Invert if background is mostly white (typical scanned document)
        mean_val = np.mean(gray)
        if mean_val > 127:
            gray = cv2.bitwise_not(gray)

        # Dilate horizontally to connect text into lines
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (30, 1))
        dilated = cv2.dilate(gray, kernel, iterations=2)

        # Find contours of the text "blocks"
        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        angles = []
        for cnt in contours:
            if cv2.contourArea(cnt) < 500:
                continue
            rect = cv2.minAreaRect(cnt)
            angle = rect[2]
            # minAreaRect returns angles in [-90, 0); normalise to (-45, 45]
            if angle < -45:
                angle += 90
            angles.append(angle)

        if not angles:
            return arr  # nothing to correct

        # Use median to be robust against outliers
        skew_angle = float(np.median(angles))

        # Only correct if tilt is meaningful but not a full rotation
        if abs(skew_angle) < 0.3 or abs(skew_angle) > 15:
            return arr

        h, w = arr.shape[:2]
        center = (w // 2, h // 2)
        M = cv2.getRotationMatrix2D(center, skew_angle, 1.0)

        # Use white (255) border fill for scanned docs
        border_val = 255 if mean_val > 127 else 0
        rotated = cv2.warpAffine(
            arr, M, (w, h),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=border_val,
        )
        return rotated
    # ------------- TESSERACT "OCR.space-style" REPORT PIPELINE -------------
    _OCR_DIGIT_FIX = str.maketrans({"O": "0", "o": "0", "Q": "0", "D": "0", "I": "1", "l": "1",
                                    "|": "1", "!": "1", "S": "5", "s": "5", "B": "8", "Z": "2"})
    _OCR_LOOKALIKE = "OoQDIl|!SsBZ"
    _MONEY_OK = re.compile(r"(?:[1-9]\d{0,2}(?:,\d{3})+|[1-9]\d*|0)\.\d{1,4}")

    @classmethod
    def _fix_ocr_tokens(cls, text: str) -> str:
        """Repair letter-for-digit slips in tokens that can ONLY be numeric.

        Only tightly-scoped shapes are touched, so ordinary words are safe:
          * amounts after a currency code   (MVRO.00 -> MVR0.00, MVRI75.68 -> MVR175.68)
            An ambiguous amount (leading O/I/l...) is resolved against the other,
            unambiguous amounts on the same page (a spurious extra letter, as in
            "MVRI175.68" for MVR175.68, is dropped when 175.68 appears elsewhere).
          * decimal comma slips              (2,00 -> 2.00, 0,00 -> 0.00)
          * TIN / GST registration numbers   (1142076GSTO01 -> 1142076GST001)
          * bracketed phone numbers          ((960)668-000! -> (960)668-0001)
        """
        if not text:
            return text

        def _val(s):
            try:
                return round(float(s.replace(",", "")), 4)
            except ValueError:
                return None

        known = set()
        for m in re.finditer(r"\b(?:MVR|USD|EUR|GBP|SGD)\s?(\d[\d,]*\.\d{1,4})\b", text):
            v = _val(m.group(1))
            if v is not None:
                known.add(v)
        for m in re.finditer(r"(?<![\w.,])(\d{1,3}(?:,\d{3})*\.\d{2})(?![\w.])", text):
            v = _val(m.group(1))
            if v is not None:
                known.add(v)

        lk = cls._OCR_LOOKALIKE

        def _money(m):
            cur, sp, tok = m.group(1), m.group(2), m.group(3)
            cands = [tok.translate(cls._OCR_DIGIT_FIX)]
            if tok[0] in lk and len(tok) > 1:
                cands.append(tok[1:].translate(cls._OCR_DIGIT_FIX))
            valid = [c for c in cands if cls._MONEY_OK.fullmatch(c)]
            if not valid:
                return m.group(0)
            pick = next((c for c in valid if _val(c) in known), valid[0])
            return cur + sp + pick

        text = re.sub(r"\b(MVR|USD|EUR|GBP|SGD)(\s?)([0-9OoQDIl|!SsBZ][0-9OoQDIl|!SsBZ,\.]*)", _money, text)
        # A decimal comma with exactly two digits is never a thousands separator.
        text = re.sub(r"(?<![\d,.])(\d{1,3}),(\d{2})(?![\d,])", r"\1.\2", text)
        text = re.sub(r"\b(\d{5,})(GST)([0-9OoSsIl|!]{3})\b",
                      lambda m: m.group(1) + m.group(2) + m.group(3).translate(cls._OCR_DIGIT_FIX), text)
        text = re.sub(r"(\(\d{3}\)\s?\d{3}-\d{3})([Il|!])(?!\w)", r"\g<1>1", text)
        return text

    _TABLE_START_RE = re.compile(r"PURCHASE\s+ORDERS|ITEM\s*(?:SKU|CODE)|ITEM\s+DESC", re.I)

    def _tess_data(self, gray, psm: int = 6):
        """Run Tesseract once with per-word boxes. Low-confidence purely numeric
        tokens (item codes, quantities such as "84555:" / "2,00") are re-read in
        isolation at 1.6x with a digits-only whitelist; only a same-length digit
        string is accepted, so a code is never lengthened or shortened."""
        from pytesseract import Output
        oem = self.cfg["app_settings"].get("ocr_oem", 3)
        d = pytesseract.image_to_data(gray, config=f"--oem {oem} --psm {psm}", output_type=Output.DICT)
        for i in range(len(d["text"])):
            w = (d["text"][i] or "").strip()
            try:
                conf = float(d["conf"][i])
            except (TypeError, ValueError):
                conf = -1
            if not w or conf >= 88 or not re.fullmatch(r"\d[\d.,]*[:;.,]?", w):
                continue
            x, y, ww, h = d["left"][i], d["top"][i], d["width"][i], d["height"][i]
            pad = int(h * 0.35)
            reg = gray[max(0, y - pad):y + h + pad, max(0, x - pad):x + ww + pad]
            if reg.size == 0:
                continue
            reads = []
            for fx in (1.6, 2.2):
                r2 = cv2.resize(reg, None, fx=fx, fy=fx, interpolation=cv2.INTER_CUBIC)
                r2 = cv2.copyMakeBorder(r2, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
                reads.append(pytesseract.image_to_string(
                    r2, config="--oem 3 --psm 8 -c tessedit_char_whitelist=0123456789.,").strip().strip(".,"))
            core = w.rstrip(":;.,")
            # two independent re-reads must agree, be numeric and keep the same length
            if reads[0] == reads[1] and re.fullmatch(r"\d[\d.,]*", reads[0]) \
                    and len(reads[0]) == len(core) and reads[0] != core:
                d["text"][i] = reads[0]
        return d

    def _layout_cells(self, d, img_w: int):
        """Turn Tesseract word boxes into OCR.space-style text lines.

        OCR.space prints the header of a Birchstreet report column by column, one
        label/value cell per line ("Invoice number: 14290", then "Invoice subtotal
        amount: ...", ...), and the stacked supplier name as two lines. The
        downstream field parsers (supplier band between "Buyer's Dept." and "Source
        document number", invoice number, totals...) rely on exactly that layout.
        Tesseract's --psm 6 glues neighbouring columns into one long row instead
        ("INVOICE NUMBER: 14290 PO DATE: 09/15/2026 SUPPLIER: ..."), which made the
        parsers pick "PERSONAL COMPUTERS" as supplier and "14290 PO" as invoice.

        Above the line-item table: find the empty vertical gutters that cut the
        header into columns, then emit each column top-to-bottom (left to right).
        From the table down: keep normal row-by-row lines.
        Returns a list of dicts: {text, conf, col, y, h}.
        """
        words = []
        for i in range(len(d["text"])):
            t = (d["text"][i] or "").strip()
            try:
                c = float(d["conf"][i])
            except (TypeError, ValueError):
                c = -1
            if not t or c < 0:
                continue
            words.append({"t": t, "x": d["left"][i], "y": d["top"][i], "w": d["width"][i],
                          "h": d["height"][i], "c": c})
        if not words:
            return []
        hs = sorted(w["h"] for w in words)
        mh = hs[len(hs) // 2] or 30

        def cluster(ws):
            ws = sorted(ws, key=lambda w: w["y"] + w["h"] / 2.0)
            rows, cys = [], []
            for w in ws:
                cy = w["y"] + w["h"] / 2.0
                if rows and abs(cy - cys[-1][0] / cys[-1][1]) <= 0.55 * mh:
                    rows[-1].append(w)
                    cys[-1][0] += cy
                    cys[-1][1] += 1
                else:
                    rows.append([w])
                    cys.append([cy, 1])
            return rows

        def _speck(w):
            a = len(re.findall(r"[A-Za-z0-9]", w["t"]))
            if a and w["h"] < 0.5 * mh and w["w"] < 0.8 * mh:
                return True                 # 4-5 px dust dot read as "i" / "5"
            if a > 2:
                return False
            if w["t"].isdigit() or re.fullmatch(r"\d[.,:]?", w["t"]):
                return w["c"] < 40          # real one-digit numbers keep a decent score
            return w["c"] < 60

        def clean_row(row, col):
            row = sorted(row, key=lambda w: w["x"])
            good = [w for w in row if not _speck(w)]
            # logo-stain specks ("I 5 J \u00abA, SIMDI CONSUME") are dropped anywhere in the
            # line, but only when real words remain - a line is never emptied by this
            if good:
                row = good
            if not row or max(w["c"] for w in row) < 45:
                return None                  # nothing here Tesseract believes in
            alpha = [w for w in row if len(re.findall(r"[A-Za-z]", w["t"])) >= 3]
            return {"text": " ".join(w["t"] for w in row),
                    "words": [(w["t"], w["c"]) for w in row],
                    "conf": (sum(w["c"] for w in alpha) / len(alpha)) if alpha else 0.0,
                    "n_alpha": len(alpha), "col": col,
                    "y": min(w["y"] for w in row), "h": max(w["h"] for w in row),
                    "x": row[0]["x"]}

        # where does the line-item table start?
        cut_y = None
        for row in cluster(words):
            txt = " ".join(w["t"] for w in sorted(row, key=lambda w: w["x"]))
            if self._TABLE_START_RE.search(txt):
                cut_y = min(w["y"] for w in row) - 2
                break

        out = []
        if cut_y is None:
            for row in cluster(words):
                r = clean_row(row, 0)
                if r:
                    out.append(r)
            return out

        head = [w for w in words if w["y"] + w["h"] / 2.0 < cut_y]
        body = [w for w in words if w["y"] + w["h"] / 2.0 >= cut_y]

        # vertical gutters across the header = column separators. A gutter may be
        # crossed by a few full-width items (a title, a long "Purchase Order #" line);
        # they must not glue the columns together, so a small vote is tolerated.
        cnt = np.zeros(img_w + 2, dtype=np.int32)
        for w in head:
            if w["h"] < 0.5 * mh or (w["c"] < 30 and len(re.findall(r"[A-Za-z0-9]", w["t"])) <= 2):
                continue                                    # specks must not bridge gutters
            cnt[max(0, w["x"]):min(img_w + 1, w["x"] + w["w"] + 1)] += 1
        thr = max(1, int(0.06 * max(1, len(cluster(head)))))
        cov = cnt > thr
        min_gap = max(int(self.cfg["app_settings"].get("ocr_column_gap_px", 0) or 0), 0) or max(40, int(1.4 * mh))
        bounds = []
        xs = np.where(cnt > 0)[0]
        first, last = (int(xs[0]), int(xs[-1])) if len(xs) else (0, 0)
        i = first
        while i <= last:
            if not cov[i]:
                j = i
                while j <= last and not cov[j]:
                    j += 1
                if j - i >= min_gap:
                    bounds.append((i + j) // 2)
                i = j
            else:
                i += 1

        def col_of(w):
            cx = w["x"] + w["w"] / 2.0
            return sum(1 for b in bounds if cx > b)

        # A phrase that straddles a gutter ("Purchase Order #: PO-MAM-000021414") must stay
        # in one piece: split each printed row only where the gap is wide, then send each
        # whole segment to the column its first word starts in.
        split_gap = max(20, int(1.0 * mh))
        cols = {}
        for row in cluster(head):
            row = sorted(row, key=lambda w: w["x"])
            seg = [row[0]]
            segs = []
            for prev, w in zip(row, row[1:]):
                if w["x"] - (prev["x"] + prev["w"]) > split_gap:
                    segs.append(seg)
                    seg = []
                seg.append(w)
            segs.append(seg)
            for sg in segs:
                cols.setdefault(col_of(sg[0]), []).extend(sg)
        for ci in sorted(cols):
            for row in cluster(cols[ci]):
                r = clean_row(row, ci)
                if r:
                    out.append(r)
        for row in cluster(body):
            r = clean_row(row, -1)
            if r:
                out.append(r)
        return out

    def _rescue_supplier_lines(self, lines, clean_gray, d):
        """Repair a garbled stacked supplier name. It is printed on two lines to the
        right of the "Supplier:" label and sits beside a logo stain, so it can come
        back as junk ("Supplier: ne aaa"). Only runs when the supplier line (or the
        name line right above it) has no confident word; the name is then re-read
        from a crop of the area to the right of the label."""
        try:
            idx = next((k for k, l in enumerate(lines)
                        if re.match(r"\s*SUPPLIER\b", l["text"], re.I)), None)
            if idx is None:
                return lines
            cur = lines[idx]
            rest_words = [(t, c) for t, c in cur.get("words", [])[1:]]
            prev = lines[idx - 1] if idx > 0 else None
            weak_cur = bool(rest_words) and (
                sum(c for _, c in rest_words) / len(rest_words) < 55
                or not re.search(r"[A-Za-z]{3,}", " ".join(t for t, _ in rest_words)))
            weak_prev = prev is not None and prev["col"] == cur["col"] and \
                (prev["n_alpha"] < 1 or prev["conf"] < 45) and prev is not None
            if not (weak_cur or weak_prev):
                return lines
            H, W = clean_gray.shape[:2]
            for i, t in enumerate(d["text"]):
                if (t or "").strip().lower().startswith("supplier"):
                    x, y, w, h = d["left"][i], d["top"][i], d["width"][i], d["height"][i]
                    reg = clean_gray[max(0, int(y - 2.4 * h)):int(y + 1.4 * h), min(W - 1, x + w):W]
                    if reg.size == 0:
                        continue
                    reg = cv2.copyMakeBorder(reg, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
                    raw = pytesseract.image_to_string(reg, config="--oem 3 --psm 6")
                    got = [ln.strip() for ln in raw.splitlines() if len(re.findall(r"[A-Za-z]", ln)) >= 3]
                    if not got:
                        return lines
                    cur["text"] = "Supplier: " + got[-1]
                    cur["words"], cur["conf"], cur["n_alpha"] = [("Supplier:", 90.0), (got[-1], 90.0)], 90.0, 2
                    nxt = lines[idx + 1] if idx + 1 < len(lines) else None
                    if nxt is not None and nxt["col"] == cur["col"] and nxt["text"].strip().upper() in got[-1].upper():
                        del lines[idx + 1]           # the name's last line was read twice
                    if len(got) >= 2 and prev is not None and prev["col"] == cur["col"]:
                        prev["text"] = " ".join(got[:-1])
                        prev["conf"], prev["n_alpha"] = 90.0, 1
                    return lines
        except Exception as e:
            logging.debug(f"Supplier rescue skipped: {e}")
        return lines

    _REPORT_LABELS = (
        "RECEIVING RECORD #", "PURCHASE ORDER #", "RECEIVED BY", "RECEIVED ON", "BUYER'S NAME",
        "BUYER'S PHONE", "BUYER'S DEPT.", "STOREROOM NAME", "PO STATUS", "INVOICE NUMBER",
        "INVOICE SUBTOTAL AMOUNT", "INVOICE FREIGHT AMOUNT", "INVOICE TAX AMOUNT",
        "INVOICE LESS DISCOUNT AMOUNT", "INVOICE TOTAL", "PO DATE", "PO SUBTOTAL",
        "PO FREIGHT AMOUNT", "PO TAX AMOUNT", "PO LESS DISCOUNT AMOUNT", "PO TOTAL", "SUPPLIER",
        "SOURCE DOCUMENT NUMBER", "TRACKING NUMBER", "BILL OF LADING NUMBER",
        "DELIVERY NOTE NUMBER", "DIRECT TOTAL AMOUNT", "RECEIVING NOTES", "PRODUCT DISBURSEMENT",
        "PICKED UP BY", "DELIVERED TO", "DEPARTMENT", "LOCATION", "DATE", "SIGNATURE",
        "INVOICE NO", "INVOICE DATE", "DUE DATE", "SALES ORDER NO", "SHOP TIN", "CUSTOMER",
        "CUSTOMER REFERENCE", "TIN", "AMOUNT IN WORDS", "PREPARED BY", "CHECKED BY",
    )

    @classmethod
    def _fix_report_labels(cls, text: str) -> str:
        """Snap a slightly misread field label back to the real Birchstreet/invoice
        label ("RECCIVED ON:" -> "RECEIVED ON:"). Different Tesseract model files
        (fast / best / the older bundled one) slip on different letters, and the
        field parsers look these labels up literally. Only the text before the first
        colon is touched and only when a known label is a very close match."""
        import difflib
        out = []
        for ln in text.splitlines():
            m = re.match(r"^(\s*)([A-Z][A-Z' .#/&-]{2,40}?)(\s*:.*)$", ln)
            if m:
                lab = m.group(2).strip()
                if lab not in cls._REPORT_LABELS:
                    c = difflib.get_close_matches(lab, cls._REPORT_LABELS, n=1, cutoff=0.86)
                    if c:
                        ln = m.group(1) + c[0] + m.group(3)
            out.append(ln)
        return "\n".join(out)

    @staticmethod
    def _drop_noise_lines(text: str) -> str:
        """Remove lines that hold no real word/number (logo stains, scanner specks)."""
        return "\n".join(ln for ln in text.splitlines() if re.search(r"[A-Za-z]{2,}|\d{2,}", ln))

    def _use_report_ocr(self, engine_override: Optional[str] = None) -> bool:
        """The specialised Birchstreet report pipeline (layout cells, label
        repair, supplier-line rescue) is Tesseract-only because it uses word
        boxes. Any other local engine is OCR'd with the generic path below.
        """
        eng = engine_override or self.cfg["app_settings"].get("ocr_engine", "tesseract")
        return eng == "tesseract" and pytesseract is not None

    def _ocr_page_report(self, page, scale: float, engine_override: Optional[str] = None) -> str:
        """Tesseract pipeline tuned for Birchstreet receiving reports + supplier
        invoices, producing OCR.space-style text. Returns "" when the text does not
        look like a real report so the caller can fall back to _ocr_page_robust.

        1. Render the top part of the page at >=300 DPI (line items and totals are
           always near the top). If ink touches the crop edge the table continues,
           so it is re-read with a taller crop instead of silently losing rows.
        2. Grayscale + table-line removal (no hard binarisation).
        3. --psm 6 word boxes, then the header is laid out column by column with one
           cell per line (see _layout_cells), exactly like OCR.space.
        4. Re-read shaky numbers, repair the supplier block, fix letter/digit slips.
        """
        eng = engine_override or self.cfg["app_settings"].get("ocr_engine", "tesseract")
        if eng != "tesseract":
            # Word-box layout needs Tesseract; other local engines use the
            # generic page OCR so the selected engine is still respected.
            return self._ocr_page_robust(page, scale, engine_override=engine_override)
        self._ensure_tesseract()
        s = self.cfg["app_settings"]
        try:
            first = float(s.get("tesseract_crop_top_percent", 60)) / 100.0
        except (TypeError, ValueError):
            first = 0.60
        first = min(max(first, 0.2), 0.93)
        best = ""
        for pct in ((first, 0.93) if first < 0.93 else (first,)):
            rect = page.rect
            clip = fitz.Rect(0, 0, rect.width, rect.height * pct)
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip)
            clean = self._preprocess_image(pix)
            d = self._tess_data(clean, psm=6)
            if s.get("ocr_layout_cells", True):
                lines = self._layout_cells(d, clean.shape[1])
                lines = self._rescue_supplier_lines(lines, clean, d)
                text = "\n".join(l["text"] for l in lines)
            else:
                text = pytesseract.image_to_string(clean, config="--oem 3 --psm 6")
            text = self._fix_report_labels(self._fix_ocr_tokens(self._drop_noise_lines(text)).upper())
            best = text
            band = clean[-max(4, int(clean.shape[0] * 0.02)):]
            if float((band < 128).mean()) <= 0.003 or pct >= 0.93:
                break
        if self._text_quality(best) < _OCR_GOOD_HITS:
            return ""
        return best

    # ------------- OCR HELPERS (offline / Tesseract) -------------
    def _ocr_scale(self) -> float:
        """Render scale for OCR. Never lower than _OCR_MIN_DPI (default 300 DPI)
        so small print on scanned Birchstreet reports stays readable."""
        s = self.cfg.get("app_settings", {})
        try:
            base = float(s.get("image_scale_factor", 2) or 2)
        except (TypeError, ValueError):
            base = 2.0
        try:
            min_dpi = float(s.get("ocr_min_dpi", _OCR_MIN_DPI))
        except (TypeError, ValueError):
            min_dpi = float(_OCR_MIN_DPI)
        return max(base, min_dpi / 72.0)

    def _ensure_tesseract(self):
        """Fail loudly (instead of returning empty text) when Tesseract cannot run."""
        if pytesseract is None:
            raise OCRUnavailableError(
                "Python package 'pytesseract' is not installed. Run: pip install pytesseract"
            )
        if getattr(self, "_tess_ready", False):
            return
        try:
            pytesseract.get_tesseract_version()
        except Exception:
            self._configure_tesseract()  # re-run discovery (PATH / Program Files / config.json)
            try:
                pytesseract.get_tesseract_version()
            except Exception as e:
                raise OCRUnavailableError(
                    "Tesseract OCR could not be started (" + str(e).strip().splitlines()[0][:160] + "). "
                    "Install it from https://github.com/UB-Mannheim/tesseract/wiki and either add it to "
                    "PATH or set \"tesseract_cmd\" in config.json to the full path of tesseract.exe."
                )
        self._tess_ready = True

    @staticmethod
    def _pix_to_gray(pix):
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
        if pix.n == 4:
            return cv2.cvtColor(img, cv2.COLOR_RGBA2GRAY)
        if pix.n == 3:
            return cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        return img.squeeze() if img.ndim == 3 else img

    @staticmethod
    def _text_quality(text: str) -> int:
        """How many recognisable report/invoice words (plus GRN/PO markers) the
        OCR text holds. Upside-down or garbled OCR scores ~0 even when long."""
        t = (text or "").upper()
        hits = len(_OCR_VOCAB.intersection(re.findall(r"[A-Z]{3,}", t)))
        if _GRN_MARK_RE.search(t):
            hits += 5
        if _PO_MARK_RE.search(t):
            hits += 5
        return hits

    def _ocr_page_robust(self, page, scale: float, engine_override: Optional[str] = None) -> str:
        """OCR one full page, retrying with different settings until the text
        actually looks like a receiving report / invoice.

        Pass 1: configured preprocessing at >=300 DPI.
        Pass 2: plain grayscale, page-segmentation mode 4 (tables / columns).
        Pass 3: only if the text is still garbage, try the other orientations.
        """
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
        enhanced = self._correct_rotation(self._preprocess_image(pix), full_page=True)
        best = self._run_ocr(enhanced, engine_override=engine_override).upper()
        best_q = self._text_quality(best)
        if best_q >= _OCR_GOOD_HITS:
            return best

        gray = self._pix_to_gray(pix)
        txt = self._run_ocr(gray, engine_override=engine_override, psm=4).upper()
        q = self._text_quality(txt)
        if q > best_q or (q == best_q and len(txt) > len(best)):
            best, best_q = txt, q
        if best_q >= _OCR_GOOD_HITS:
            return best

        if best_q < 3:
            for rot in (cv2.ROTATE_180, cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE):
                txt = self._run_ocr(cv2.rotate(gray, rot), engine_override=engine_override, psm=4).upper()
                q = self._text_quality(txt)
                if q > best_q:
                    best, best_q = txt, q
                if best_q >= _OCR_GOOD_HITS:
                    break
        logging.info(f"OCR page finished with low confidence (quality={best_q}, chars={len(best)})")
        return best

    def _ocr_zone(self, page, scale: float, top_pct: float, bottom_pct: float,
                  left_pct: float, right_pct: float, engine_override: Optional[str] = None) -> str:
        """
        OCR a rectangular sub-region of a fitz Page.
        Coordinates are given as fractions of the page dimensions (0.0-1.0).
        Returns upper-cased text, or empty string on failure.
        """
        try:
            rect = page.rect
            # A little vertical overlap so a zone edge never slices a text line in half.
            pad = 0.012
            top_pct = max(0.0, top_pct - (pad if top_pct > 0 else 0.0))
            bottom_pct = min(1.0, bottom_pct + (pad if bottom_pct < 1 else 0.0))
            clip = fitz.Rect(
                rect.width  * left_pct,
                rect.height * top_pct,
                rect.width  * right_pct,
                rect.height * bottom_pct,
            )
            if clip.width < 10 or clip.height < 10:
                return ""
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip)
            if pix.w == 0 or pix.h == 0:
                return ""
            arr = self._preprocess_image(pix)
            # Crops are never rotated/deskewed (see _correct_rotation docstring).
            arr = self._correct_rotation(arr, full_page=False)
            return self._run_ocr(arr, engine_override=engine_override).upper()
        except OCRUnavailableError:
            raise
        except Exception as e:
            logging.warning(f"Zone OCR failed: {e}")
            self._last_ocr_error = f"Zone OCR failed: {e}"
            return ""

    def _extract_text_zone(self, pdf_path: str, engine_override: Optional[str] = None) -> str:
        """
        Zone-based text extraction for Birchstreet receiving report PDFs.

        Strategy:
        1. For each page, try native PDF text first (fast, free).
        2. If native text is thin, OCR only the header region (top ~38% of the
           page) at >=300 DPI instead of the full page. The zones are OCR'd as
           ONE crop: slicing them into thin bands cut text lines in half and
           broke the stacked supplier name ("SIMDI CONSUMER" above the
           "Supplier:" label, "PRODUCTS" beside it) that Birchstreet prints.
        3. Accept the zone text ONLY if it really reads like a report (enough
           recognisable words / GRN / PO markers). Otherwise fall back to a
           robust full-page OCR (multiple settings / orientations).

        Returns the combined upper-cased text for all pages.
        """
        full = ""
        scale = self._ocr_scale()
        use_native_flag = self.cfg["app_settings"].get("extract_text_before_ocr", True)
        self._last_ocr_error = ""

        try:
            doc = fitz.open(pdf_path)
            try:
                for page in doc:
                    page_text = ""

                    # --- Step 1: try native PDF text ---
                    if use_native_flag:
                        native = page.get_text("text").upper()
                        if len(native.strip()) >= 50:
                            full += native + "\n"
                            continue   # Native text is good - skip OCR entirely

                    # --- Step 2a: OCR.space-style report pipeline (Tesseract only) ---
                    if self._use_report_ocr(engine_override):
                        rp = self._ocr_page_report(page, scale, engine_override=engine_override)
                        if rp.strip():
                            full += rp + "\n"
                            continue

                    # --- Step 2: OCR the header region (union of all zones) as one crop ---
                    zs = list(ZONE_OCR_REGIONS.values())
                    top = min(z[0] for z in zs)
                    bottom = max(z[1] for z in zs)
                    left = min(z[2] for z in zs)
                    right = max(z[3] for z in zs)
                    combined_zones = self._ocr_zone(
                        page, scale, top, bottom, left, right,
                        engine_override=engine_override,
                    )
                    success_count = 1 if len(combined_zones.strip()) >= _ZONE_MIN_CHARS else 0
                    zones_ok = (
                        success_count >= 1
                        and self._text_quality(combined_zones) >= _OCR_GOOD_HITS
                    )

                    # --- Step 3: robust full-page OCR when zones are sparse/garbled ---
                    if zones_ok:
                        page_text = combined_zones
                    else:
                        logging.info(
                            f"Zone OCR insufficient (zones={success_count}, "
                            f"quality={self._text_quality(combined_zones)}) - full-page OCR"
                        )
                        page_text = self._ocr_page_robust(page, scale, engine_override=engine_override)

                    full += page_text + "\n"
            finally:
                doc.close()
        except OCRUnavailableError:
            raise
        except Exception as e:
            logging.error(f"Zone text extraction failed [{pdf_path}]: {e}", exc_info=True)
            self._last_ocr_error = f"Text extraction failed: {e}"
        if not full.strip():
            logging.error(f"OCR returned NO text for [{pdf_path}] "
                          f"(last error: {getattr(self, '_last_ocr_error', '') or 'none'})")
        return full

    def _run_ocr(self, arr, engine_override: Optional[str] = None, psm: Optional[int] = None):
        """Run OCR with the configured engine or an explicit per-call override.

        The GRN Dispatch local pipeline passes ``tesseract`` here instead of
        changing the shared configuration while a background job is running.
        That keeps local and online jobs isolated and makes the selected engine
        deterministic for every document in a queued batch.

        The GRN Dispatch local pipeline passes the engine selected in Settings
        ("tesseract" / "paddleocr" / "easyocr"). A missing engine raises
        OCRUnavailableError, so the UI shows "engine not found" instead of a
        silent empty result, and never silently swaps in a different engine.

        `psm` is only meaningful for Tesseract; the deep-learning engines ignore
        it (their call sites still pass it, which is harmless).
        """
        eng = engine_override or self.cfg["app_settings"].get("ocr_engine", "tesseract")
        if eng not in ENGINE_LABELS:
            eng = "tesseract"

        if eng == "tesseract":
            return self._run_local_engine_ocr(arr, "tesseract")

        try:
            return self._run_local_engine_ocr(arr, eng)
        except OCRUnavailableError:
            # The engine the user selected is genuinely missing: report it
            # instead of pretending the document was blank.
            raise
        except Exception as e:
            logging.warning(f"{ENGINE_LABELS.get(eng, eng)} failed: {e}")
            self._last_ocr_error = f"{ENGINE_LABELS.get(eng, eng)} failed: {e}"
            if not self.cfg["app_settings"].get("ocr_fallback_to_tesseract", True):
                return ""
            logging.info("Falling back to Tesseract OCR")
            return self._run_local_engine_ocr(arr, "tesseract")

    def _extract_text(self, pdf_path: str, engine_override: Optional[str] = None) -> str:
        """
        Route to zone OCR or full OCR depending on the ocr_mode setting.
        'zone'  -> fast targeted extraction
        'full'  -> full-page extraction (legacy behaviour)
        """
        ocr_mode = self.cfg["app_settings"].get("ocr_mode", "full")
        if ocr_mode == "zone":
            return self._extract_text_zone(pdf_path, engine_override=engine_override)

        full = ""
        scale = self._ocr_scale()
        mode = self.cfg["app_settings"].get("extraction_source", "auto")
        use_native_flag = self.cfg["app_settings"].get("extract_text_before_ocr", True)
        self._last_ocr_error = ""

        try:
            doc = fitz.open(pdf_path)
            try:
                for page in doc:
                    pt = ""
                    use_native = (mode in ("auto", "pdf_text_only")) and use_native_flag
                    if use_native:
                        pt = page.get_text("text").upper()

                    need_ocr = False
                    if mode == "image_only":
                        need_ocr = True
                    elif mode == "auto":
                        if len(pt.strip()) < 50:
                            need_ocr = True
                    elif mode == "pdf_text_only":
                        need_ocr = False

                    if need_ocr:
                        scan_enabled = self.cfg["app_settings"].get("page_scan_region_enabled", False)
                        scan_pct     = self.cfg["app_settings"].get("page_scan_region_percent", 100)
                        if scan_enabled and scan_pct < 100:
                            rect  = page.rect
                            clip  = fitz.Rect(0, 0, rect.width, rect.height * scan_pct / 100.0)
                            pix   = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip)
                            arr = self._preprocess_image(pix)
                            arr = self._correct_rotation(arr, full_page=False)
                            pt = self._run_ocr(arr, engine_override=engine_override).upper()
                        else:
                            pt = ""
                            if self._use_report_ocr(engine_override):
                                pt = self._ocr_page_report(page, scale, engine_override=engine_override)
                            if not pt.strip():
                                pt = self._ocr_page_robust(page, scale, engine_override=engine_override)

                    full += (pt or "") + "\n"
            finally:
                doc.close()
        except OCRUnavailableError:
            raise
        except Exception as e:
            logging.error(f"Text extraction failed [{pdf_path}]: {e}", exc_info=True)
            self._last_ocr_error = f"Text extraction failed: {e}"
        if not full.strip():
            logging.error(f"OCR returned NO text for [{pdf_path}] "
                          f"(last error: {getattr(self, '_last_ocr_error', '') or 'none'})")
        return full

    def _normalize_text(self, t):
        return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", " ", t.upper())).strip()

    def _resolve_alias(self, name, aliases):
        for main, lst in aliases.items():
            if name == main or name in lst:
                return main
        return name
    def _append_ai_log(self, message: str):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {message}"
        with self._ai_log_lock:
            self._ai_log_lines.append(line)

        logging.info(f"[AI-LOG] {message}")

        if self._ai_log_text is not None:
            try:
                self.after(0, lambda l=line: self._append_ai_log_to_widget(l))
            except Exception:
                pass

    def _append_ai_log_to_widget(self, line: str):
        if self._ai_log_text is None:
            return
        self._ai_log_text.configure(state="normal")
        self._ai_log_text.insert("end", line + "\n")
        self._ai_log_text.see("end")
        self._ai_log_text.configure(state="disabled")

    def _open_ai_log_window(self):
        if self._ai_log_window is not None and self._ai_log_window.winfo_exists():
            self._ai_log_window.lift()
            return

        win = tk.Toplevel(self)
        win.title("AI Supplier Matching Log")
        win.geometry("980x620")
        win.configure(bg=BG)

        self._ai_log_window = win

        top = tk.Frame(win, bg=PANEL2, height=46)
        top.pack(fill=tk.X)
        top.pack_propagate(False)

        tk.Label(
            top,
            text="AI Supplier Matching Log",
            bg=PANEL2,
            fg=TEXT,
            font=("Segoe UI", 11, "bold"),
        ).pack(side=tk.LEFT, padx=14, pady=12)

        btns = tk.Frame(top, bg=PANEL2)
        btns.pack(side=tk.RIGHT, padx=10, pady=6)

        ttk.Button(btns, text="Clear Log", command=self._clear_ai_log).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Refresh", command=self._refresh_ai_log_window).pack(side=tk.LEFT, padx=4)

        body = tk.Frame(win, bg=BG)
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        txt = tk.Text(
            body,
            bg=PANEL,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            wrap="word",
            font=("Consolas", 9),
        )
        ys = ttk.Scrollbar(body, orient="vertical", command=txt.yview)
        xs = ttk.Scrollbar(body, orient="horizontal", command=txt.xview)
        txt.configure(yscrollcommand=ys.set, xscrollcommand=xs.set, wrap="none")

        txt.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        self._ai_log_text = txt
        self._refresh_ai_log_window()

        def _on_close():
            self._ai_log_text = None
            self._ai_log_window = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", _on_close)

    def _refresh_ai_log_window(self):
        if self._ai_log_text is None:
            return
        self._ai_log_text.configure(state="normal")
        self._ai_log_text.delete("1.0", "end")
        with self._ai_log_lock:
            for line in self._ai_log_lines:
                self._ai_log_text.insert("end", line + "\n")
        self._ai_log_text.see("end")
        self._ai_log_text.configure(state="disabled")

    def _clear_ai_log(self):
        with self._ai_log_lock:
            self._ai_log_lines.clear()
        if self._ai_log_text is not None:
            self._ai_log_text.configure(state="normal")
            self._ai_log_text.delete("1.0", "end")
            self._ai_log_text.configure(state="disabled")
        self._append_ai_log("AI log cleared by user.")

    # ------------- SUPPLIER MATCHING WITH CONFIDENCE -------------
    def _match_supplier_with_confidence(self, text, filename="") -> Tuple[str, float]:
        """Identify the supplier from OCR text, prioritising the OCR's supplier field.

        IMPORTANT DESIGN RULE:
        Supplier identity is established from the text that OCR actually extracted.
        Invoice-number regexes / invoice-series patterns are NOT used here.  They are
        only a later fallback when the supplier genuinely cannot be identified.

        OCR commonly changes 1-2 characters in company names, removes punctuation,
        joins words, or splits one word into two.  This matcher therefore combines:
          * exact/normalised matches
          * token overlap
          * character similarity / edit distance
          * best-line matching
          * aliases

        The score is deliberately conservative when two suppliers are similarly named.
        """
        suppliers = self.cfg.get("suppliers", [])
        aliases = self.cfg.get("aliases", {})
        cands = list(dict.fromkeys(suppliers + [a for sub in aliases.values() for a in sub]))
        if not cands:
            return "UNKNOWN SUPPLIER", 0.0

        raw = text or ""
        upper = raw.upper()
        unique_index = _build_unique_word_index(suppliers, aliases)

        # 1) FIRST PRIORITY: explicit OCR supplier/vendor field.
        field_match, field_conf = self._match_supplier_field_confidence(raw, cands, aliases)
        if field_match:
            return field_match, field_conf

        # 2) Strong exact/unique-word evidence anywhere in OCR text.
        best_unique_name = None
        best_unique_score = 0.0
        for c in cands:
            unique_words = unique_index.get(c, set())
            if not unique_words:
                continue
            hits = sum(1 for w in unique_words if re.search(r"\b" + re.escape(w) + r"\b", upper))
            if hits:
                score = hits / max(len(unique_words), 1)
                if score > best_unique_score:
                    best_unique_score = score
                    best_unique_name = c

        if best_unique_name and best_unique_score >= 0.50:
            conf = min(99.0, 70.0 + best_unique_score * 29.0)
            return self._resolve_alias(best_unique_name, aliases), conf

        # 3) Search individual OCR lines. This is deliberately text-first and does
        # not inspect invoice formats. Compare the useful first ~8 words of each line.
        best = None
        best_score = 0.0
        best_margin = 0.0
        for line in upper.splitlines():
            line = re.sub(r"\s+", " ", line).strip()
            if len(line) < 3:
                continue
            # Ignore obvious non-company lines.
            if re.fullmatch(r"[\d\s.,:/#\-]+", line):
                continue
            probe = re.sub(r"[^A-Z0-9& ]", " ", line)
            probe = re.sub(r"\s+", " ", probe).strip()
            if not probe:
                continue
            words = probe.split()
            probe = " ".join(words[:8])

            ranked = []
            for c in cands:
                score = self._supplier_candidate_score(probe, c)
                ranked.append((score, c))
            ranked.sort(reverse=True, key=lambda x: x[0])
            if ranked:
                score, cand = ranked[0]
                second = ranked[1][0] if len(ranked) > 1 else 0.0
                # Require a meaningful score and a margin when the top two are close.
                if score > best_score and score >= 82.0:
                    best = cand
                    best_score = score
                    best_margin = score - second

        if best:
            # High score + reasonable margin = reliable OCR correction.
            if best_score >= 94.0 or (best_score >= 88.0 and best_margin >= 4.0):
                conf = min(97.0, best_score)
                return self._resolve_alias(best, aliases), conf

        # 4) Filename is only a late fallback, never the primary supplier source.
        if filename:
            hint = re.sub(r"[_\-]", " ", re.split(r"GRN|RC-MAM", filename.upper())[0]).strip()
            if hint:
                ranked = sorted(
                    ((self._supplier_candidate_score(hint, c), c) for c in cands),
                    reverse=True,
                    key=lambda x: x[0],
                )
                if ranked and ranked[0][0] >= 94.0:
                    return self._resolve_alias(ranked[0][1], aliases), ranked[0][0]

        return "UNKNOWN SUPPLIER", 0.0

    @staticmethod
    def _supplier_normalize_for_match(value: str) -> str:
        """Normalise company text without destroying useful character information."""
        s = (value or "").upper()
        s = s.replace("&", " AND ")
        # Common OCR punctuation/spacing noise.
        s = re.sub(r"[^A-Z0-9 ]", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    def _supplier_candidate_score(self, extracted: str, candidate: str) -> float:
        """Score an OCR company-name fragment against one configured supplier.

        A 1-2 character OCR error is intentionally cheap, while unrelated company
        names with only one common word do not receive a high score.
        """
        a = self._supplier_normalize_for_match(extracted)
        b = self._supplier_normalize_for_match(candidate)
        if not a or not b:
            return 0.0
        if a == b:
            return 100.0

        # Direct substring is strong when OCR has added a prefix/suffix.
        if b in a or a in b:
            ratio = fuzz.ratio(a, b) if RAPIDFUZZ_AVAILABLE else 0.0
            return max(96.0, ratio)

        if not RAPIDFUZZ_AVAILABLE:
            # Fallback when rapidfuzz is unavailable.
            return SequenceMatcher(None, a, b).ratio() * 100.0 if SequenceMatcher else 0.0

        ratio = fuzz.ratio(a, b)
        wratio = fuzz.WRatio(a, b)
        token_set = fuzz.token_set_ratio(a, b)
        token_sort = fuzz.token_sort_ratio(a, b)

        # Character-level similarity is the most important signal for 1-2 OCR typos.
        score = max(ratio, 0.65 * wratio + 0.35 * token_sort)

        # Explicit edit-distance bonus for short OCR corruption.
        if len(a) >= 5 and len(b) >= 5:
            dist = _levenshtein(a.replace(" ", ""), b.replace(" ", ""))
            if dist <= 2 and abs(len(a.replace(" ", "")) - len(b.replace(" ", ""))) <= 2:
                score = max(score, 97.0 - dist * 2.0)

        # Token overlap helps with names such as "ABC TRADING COMPANY LTD" while
        # still requiring character similarity on the company-defining words.
        aw = a.split()
        bw = b.split()
        if aw and bw:
            common = 0
            for x in aw:
                if any(fuzz.ratio(x, y) >= 88 for y in bw):
                    common += 1
            overlap = common / max(len(bw), 1)
            if overlap >= 0.75:
                score = max(score, min(98.0, 82.0 + overlap * 16.0))

        return min(100.0, score)

    def _match_supplier_field_confidence(self, text, cands, aliases):
        """Match ONLY against configured suppliers/aliases using OCR around a supplier label.

        OCR layout is not trusted: a long supplier may be split before/after the label,
        or the fragments may be reversed. Unknown text is NEVER returned as a supplier.
        """
        if not text or not cands:
            return None, 0.0
        lines = [re.sub(r"\s+", " ", x).strip() for x in text.upper().splitlines()]
        lines = [x for x in lines if x]
        label_patterns = [r"SUPPLIE(?:R)?\s*(?:NAME)?", r"VENDOR\s*(?:NAME)?", r"BILL\s*(?:FROM|TO)", r"SOLD\s+BY"]
        stop_re = re.compile(r"^(?:SOURCE\s+DOCUMENT|TRACKING|BILL\s+OF\s+LADING|DELIVERY\s+NOTE|INVOICE|INVO1CE|DATE|PURCHASE\s+ORDER|PO\b|GRN\b|RC[- ]?MAM|AMOUNT|TOTAL|SUBTOTAL|TAX|FREIGHT|DISCOUNT|DEPARTMENT|LOCATION|SIGNATURE|BUYER'?S?|DEPT\b|PHONE|STOREROOM)\b", re.I)
        probes=[]
        for i,line in enumerate(lines):
            m=None
            for pat in label_patterns:
                m=re.search(pat,line,re.I)
                if m: break
            if not m:
                continue
            inline=line[m.end():].lstrip(' :#-–—\t')
            before=[lines[j] for j in range(max(0,i-2),i) if not stop_re.search(lines[j])]
            after=[]
            for j in range(i+1,min(len(lines),i+3)):
                if stop_re.search(lines[j]): break
                after.append(lines[j])
            parts=[]
            # Try normal, reversed and surrounding layouts.
            if inline: parts.append(inline)
            if before: parts.append(' '.join(before))
            if after: parts.append(' '.join(after))
            if before and inline: parts.append(' '.join(before+[inline]))
            if inline and after: parts.append(' '.join([inline]+after))
            if before and inline and after:
                parts += [' '.join(before+[inline]+after), ' '.join(after+[inline]+before), ' '.join(before+after+[inline])]
            elif before and after:
                parts += [' '.join(before+after), ' '.join(after+before)]
            for part in parts:
                q=self._clean_supplier_probe(part)
                if q and len(q)>=2: probes.append(q)
        # damaged SUPPLIER/VENDOR labels
        for i,line in enumerate(lines):
            words=re.findall(r"[A-Z0-9]+",line)
            if not words: continue
            first=words[0]
            if not any(_levenshtein(first,t)<=2 for t in ('SUPPLIER','VENDOR')): continue
            if re.search(r"(?:INVOICE|TOTAL|AMOUNT|PURCHASE|RECEIVING)",line): continue
            rem=re.sub(r"^[A-Z0-9]+\s*[:#-]?\s*",'',line,count=1)
            near=[]
            for j in range(max(0,i-1),min(len(lines),i+2)):
                if j!=i and stop_re.search(lines[j]): continue
                near.append(rem if j==i else lines[j])
            q=self._clean_supplier_probe(' '.join(near))
            if q: probes.append(q)
        best=(0.0,None,0.0)
        for probe in dict.fromkeys(probes):
            words=probe.split()
            variants=[probe]
            for n in range(min(10,len(words)),1,-1): variants.append(' '.join(words[:n]))
            for pr in variants:
                scores=[(self._supplier_candidate_score(pr,c),c) for c in cands]
                scores.sort(reverse=True,key=lambda x:x[0])
                if not scores: continue
                score,cand=scores[0]; second=scores[1][0] if len(scores)>1 else 0.0
                if score>best[0]: best=(score,cand,second)
        score,cand,second=best
        if not cand: return None,0.0
        margin=score-second
        if score>=96 and margin>=1: return self._resolve_alias(cand,aliases),min(99.5,score)
        if score>=91 and margin>=3: return self._resolve_alias(cand,aliases),min(98.0,score)
        if score>=87 and margin>=6: return self._resolve_alias(cand,aliases),min(95.0,score)
        return None,0.0

    @staticmethod
    def _clean_supplier_probe(value: str) -> str:
        """Remove obvious neighbouring field labels from an OCR supplier probe."""
        s = re.sub(r"\s+", " ", value or "").strip(" :#-–—")
        if not s:
            return ""
        # Stop at the next obvious report field.
        stop = re.search(
            r"\b(?:INVOICE|INVO1CE|GRN|RECEIVING|RECEIPT|DATE|PURCHASE\s+ORDER|P\.O\.|PO|"
            r"SUBTOTAL|TOTAL|AMOUNT|QTY|QUANTITY|DESCRIPTION|TAX|CURRENCY|"
            r"BUYER'?S?\s+DEPT|BUYER)\b",
            s,
            re.IGNORECASE,
        )
        if stop and stop.start() > 0:
            s = s[:stop.start()].strip(" :#-–—")
        # Remove leading boilerplate such as "NAME" if OCR kept it after the label.
        s = re.sub(r"^(?:NAME|NO|NUMBER)\s*[:#-]?\s*", "", s, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", s).strip()

    def _match_supplier(self, text, filename="") -> str:
        name, _ = self._match_supplier_with_confidence(text, filename)
        return name

    def _extract_supplier_from_field(self, text):
        """Compatibility wrapper. Never return an unconfigured/raw OCR supplier."""
        name, confidence = self._match_supplier_with_confidence(text or "")
        if name and name != "UNKNOWN SUPPLIER" and confidence > 0:
            return name
        return None

    def _extract_company_from_invoice_pages(self, pdf_path):
        suppliers = self.cfg.get("suppliers", [])
        aliases = self.cfg.get("aliases", {})
        cands = suppliers + [a for sub in aliases.values() for a in sub]
        thr = self.cfg["app_settings"].get("fuzzy_match_threshold", 85)
        scale = self.cfg["app_settings"].get("image_scale_factor", 2)
        use = self.cfg["app_settings"].get("extract_text_before_ocr", True)

        if not cands:
            return None

        try:
            doc = fitz.open(pdf_path)
            try:
                for page in doc:
                    pt = page.get_text("text").upper() if use else ""
                    if len(pt.strip()) < 50:
                        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
                        pt = self._run_ocr(self._preprocess_image(pix))

                    if (
                        "RECEIVING REPORT" not in pt
                        and "RECEIVING RECORD" not in pt
                        and "INVOICE" not in pt
                    ):
                        continue

                    rect = page.rect
                    h_limit = rect.height * 0.25
                    third_w = rect.width / 3.0

                    try:
                        words = page.get_text("words")
                        words_left   = [w[4] for w in words if w[1] < h_limit and w[0] < third_w]
                        words_center = [w[4] for w in words if w[1] < h_limit and third_w <= w[0] < 2 * third_w]
                        words_right  = [w[4] for w in words if w[1] < h_limit and w[0] >= 2 * third_w]
                        words_all    = [w[4] for w in words if w[1] < h_limit]
                    except Exception:
                        words_left = words_center = words_right = words_all = []

                    zones = [
                        ("above-left",   fitz.Rect(0, 0, third_w, h_limit),              " ".join(words_left)),
                        ("above-center", fitz.Rect(third_w, 0, 2 * third_w, h_limit),    " ".join(words_center)),
                        ("above-right",  fitz.Rect(2 * third_w, 0, rect.width, h_limit), " ".join(words_right)),
                        ("above-full",   fitz.Rect(0, 0, rect.width, h_limit),            " ".join(words_all)),
                    ]

                    for zone_label, clip, native_zone_text in zones:
                        zone_ocr_text = ""
                        try:
                            px = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip)
                            if px.h > 0 and px.w > 0:
                                zone_ocr_text = self._run_ocr(self._preprocess_image(px))
                        except Exception as e:
                            logging.warning(f"Zone render error [{zone_label}]:{e}")

                        combined = (zone_ocr_text + "\n" + native_zone_text).upper()
                        for line in combined.splitlines():
                            line = line.strip()
                            if len(line) < 4:
                                continue
                            mt = process.extractOne(line, cands, scorer=fuzz.token_sort_ratio)
                            if mt and mt[1] >= thr:
                                doc.close()
                                return self._resolve_alias(mt[0], aliases)
            finally:
                try:
                    doc.close()
                except Exception:
                    pass
        except Exception as e:
            logging.error(f"Invoice page extraction failed [{pdf_path}]:{e}", exc_info=True)

        return None

    # ------------- RECEIVING REPORT / GRN / PO / DATE -------------
    def _is_receiving_report_text(self, text: str) -> bool:
        u = (text or "").upper()
        if (
            "RECEIVING REPORT" in u
            or "RECEIVING RECORD" in u
            or ("RECEIVING" in u and "RECORD" in u)
        ):
            return True
        # Tolerant fallback for OCR-damaged labels (staple holes, smudges) where
        # one or more characters of the label are unreadable.
        if self.cfg.get("app_settings", {}).get("receiving_label_fuzzy", True):
            return (
                _label_present(u, "RECEIVING RECORD", max_dist=2)
                or _label_present(u, "RECEIVING REPORT", max_dist=2)
            )
        return False

    def _collect_receiving_report_pages(self, pdf_path: str) -> List[Dict]:
        pages = []
        mode = self.cfg["app_settings"].get("extraction_source", "auto")
        scale = self.cfg["app_settings"].get("image_scale_factor", 2)
        use_native_flag = self.cfg["app_settings"].get("extract_text_before_ocr", True)

        try:
            doc = fitz.open(pdf_path)
            try:
                for i, page in enumerate(doc):
                    native = ""
                    ocr_text = ""

                    use_native = (mode in ("auto", "pdf_text_only")) and use_native_flag
                    if use_native:
                        native = page.get_text("text").upper()

                    combined = native

                    need_ocr = False
                    if mode == "image_only":
                        need_ocr = True
                    elif mode == "auto":
                        if not self._is_receiving_report_text(native):
                            need_ocr = True
                    elif mode == "pdf_text_only":
                        need_ocr = False

                    if need_ocr:
                        rect = page.rect
                        header_clip = fitz.Rect(0, 0, rect.width, rect.height * 0.28)
                        try:
                            pix_header = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=header_clip)
                            if pix_header.w > 0 and pix_header.h > 0:
                                arr_header = self._preprocess_image(pix_header)
                                arr_header = self._correct_rotation(arr_header, full_page=False)
                                ocr_header = self._run_ocr(arr_header).upper()
                            else:
                                ocr_header = ""
                        except Exception:
                            ocr_header = ""

                        try:
                            pix_full = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
                            arr_full = self._preprocess_image(pix_full)
                            arr_full = self._correct_rotation(arr_full)
                            ocr_full = self._run_ocr(arr_full).upper()
                        except Exception:
                            ocr_full = ""

                        ocr_text = (ocr_header + "\n" + ocr_full).strip()

                    combined = (native + "\n" + ocr_text).upper() if ocr_text else native

                    if self._is_receiving_report_text(combined):
                        pages.append({
                            "index": i,
                            "text": combined,
                            "native_text": native,
                        })
            finally:
                doc.close()
        except Exception as e:
            logging.error(f"Receiving page collection failed [{pdf_path}]:{e}", exc_info=True)

        return pages

    def _extract_date_from_receiving_text(self, text: str) -> str:
        u = text.upper()
        patterns = [
            # OCR commonly reads "RECEIVED" as "RECELVED" (I -> L).
            # Accept the common OCR variants while keeping the field-specific match.
            r"RECE[I L1]VED\s*[OAU]N\s*[:\-]?\s*(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{2,4})",
            r"RECEIVED\s*DATE\s*[:\-]?\s*(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{2,4})",
            r"DATE\s*RECEIVED\s*[:\-]?\s*(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{2,4})",
            r"RECEIPT\s*DATE\s*[:\-]?\s*(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{2,4})",
        ]
        for pat in patterns:
            m = re.search(pat, u, re.IGNORECASE)
            if m:
                mm, dd, yyyy = m.group(1), m.group(2), m.group(3)
                if len(yyyy) == 2:
                    yyyy = "20" + yyyy
                return f"{dd.zfill(2)}.{mm.zfill(2)}.{yyyy}"
        return ""

    # Words that can legitimately follow a PO number on the same line/label
    # area - used to stop the extraction window before it slurps a
    # neighbouring field's digits into the PO number.
    _PO_WINDOW_STOP = r"\s{2,}|INVOICE|RECEIVING|GRN|\bDATE\b|SUPPLIER|\bTOTAL\b"

    def _extract_po_from_receiving_text(self, text: str) -> str:
        """Find the PO number after a 'PURCHASE ORDER' / 'P.O' label.

        The 'MAM-000' prefix never changes, so instead of trusting whatever
        OCR handed back for the whole token (which is how a mis-split digit
        run like 'MAM-0080' used to slip through untouched), we only take the
        digits found in a small window after the label and rebuild the code
        with '_reconstruct_po_digits' - forcing the constant prefix and
        keeping just the last 6 (real, changing) digits.
        """
        u = text.upper()
        pats = self.cfg.get("patterns", {})
        prefix = pats.get("po_prefix", "MAM-")

        label_pats = [
            r"PURCHASE\s*ORDER\s*(?:NO\.?|NUMBER|#)?\s*[:\-]?\s*",
            r"\bP\.?\s*O\.?\s*(?:NO\.?|NUMBER|#)?\s*[:\-]?\s*",
        ]
        for lp in label_pats:
            lm = re.search(lp, u, re.IGNORECASE)
            if not lm:
                continue
            window = u[lm.end(): lm.end() + 24]
            window = re.split(self._PO_WINDOW_STOP, window)[0]
            # Skip a leading 'MAM'-like token before pulling digits, so an
            # OCR letter-glitch inside the word itself (rare, but possible)
            # can't leak a stray digit into the reconstructed number.
            mam_m = re.match(r"\s*M[A4][AM4]?M?[-\s]*", window)
            if mam_m:
                window = window[mam_m.end():]
            d = self._reconstruct_po_digits(window)
            if d:
                return f"{prefix}{d}"

        # Tolerant fallback: no clean 'PURCHASE ORDER' label found (e.g. it
        # was damaged by a staple hole or OCR dropped it entirely) - look for
        # 'MAM' anywhere, allowing the usual OCR letter confusions, and
        # rebuild from whatever digits follow it.
        for m in re.finditer(r"M[A4]M[-\s]?", u):
            window = u[m.end(): m.end() + 18]
            window = re.split(self._PO_WINDOW_STOP, window)[0]
            d = self._reconstruct_po_digits(window)
            if d:
                return f"{prefix}{d}"

        return ""

    def _reconstruct_fixed_prefix_number(
        self, raw: str, total_len: int, variable_len: int, min_variable_digits: int = 2,
        max_intake_digits: int = 20,
    ) -> Optional[str]:
        """Rebuild a code of the form <fixed-zeros><variable digits> that is
        known to NEVER change its fixed-length leading-zero segment (e.g. the
        'RC-MAM-0000' / 'MAM-000' prefixes always have that many zeros before
        the real, changing number starts).

        Because the fixed part is constant by definition, we don't trust OCR
        to read it correctly at all - we simply force it - and only trust the
        RIGHTMOST `variable_len` digits actually seen as the real number
        (rightmost, because a dropped/garbled character is far more likely to
        happen at the start of a run than mid-number, and because the fixed
        zeros are also digits that would otherwise get counted). Short
        captures are zero-padded on the left (safe, since these are
        zero-padded sequential IDs), long/noisy captures are trimmed to the
        last `variable_len` digits.
        """
        digits = re.sub(r"\D", "", raw or "")
        if not digits:
            return None
        if len(digits) > max_intake_digits:
            digits = digits[:max_intake_digits]

        if len(digits) >= variable_len:
            tail = digits[-variable_len:]
        else:
            tail = digits
        if len(tail) < min_variable_digits:
            return None
        tail = tail.zfill(variable_len)

        fixed_len = max(0, total_len - variable_len)
        return ("0" * fixed_len) + tail

    def _reconstruct_rcmam_digits(self, raw_digits: str) -> Optional[str]:
        pats = self.cfg.get("patterns", {})
        target_len = int(pats.get("grn_digits", 9))
        variable_len = int(pats.get("grn_variable_digits", 5))
        min_len = int(pats.get("grn_min_digits", 2))
        return self._reconstruct_fixed_prefix_number(
            raw_digits, total_len=target_len, variable_len=variable_len, min_variable_digits=min_len,
        )

    def _reconstruct_po_digits(self, raw_digits: str) -> Optional[str]:
        pats = self.cfg.get("patterns", {})
        target_len = int(pats.get("po_digits", 9))
        variable_len = int(pats.get("po_variable_digits", 6))
        min_len = int(pats.get("po_min_digits", 3))
        return self._reconstruct_fixed_prefix_number(
            raw_digits, total_len=target_len, variable_len=variable_len, min_variable_digits=min_len,
        )

    def _extract_grn_candidates_from_receiving_text(self, text: str) -> List[int]:
        nums: List[int] = []
        u = (text or "").upper()

        clean_lines = []
        for raw_line in u.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            clean_lines.append(line)

        for line in clean_lines:
            if "RECEIVING" in line and "RECORD" in line:
                m = re.search(
                    r"RECEIVING\s*RECORD\s*#?\s*[:\-]?\s*RC[-\s]*MAM[-\s]*([0-9]{4,12})",
                    line
                )
                if m:
                    d = self._reconstruct_rcmam_digits(m.group(1))
                    if d:
                        nums.append(int(d))
                    continue

        for line in clean_lines:
            if "RECEIVING" in line and "RECORD" in line:
                cut = line
                po_pos = len(cut)
                for token in ["PURCHASE ORDER", "P.O", "PO NO", "PO#", "PO "]:
                    pos = cut.find(token)
                    if pos != -1:
                        po_pos = min(po_pos, pos)
                cut = cut[:po_pos]
                m = re.search(r"RC[-\s]*MAM[-\s]*([0-9]{4,12})", cut)
                if m:
                    d = self._reconstruct_rcmam_digits(m.group(1))
                    if d:
                        nums.append(int(d))

        for line in clean_lines:
            if "RECEIVING" not in line or "RECORD" not in line:
                continue
            cut = line
            po_pos = len(cut)
            for token in ["PURCHASE ORDER", "P.O", "PO NO", "PO#", "PO "]:
                pos = cut.find(token)
                if pos != -1:
                    po_pos = min(po_pos, pos)
            cut = cut[:po_pos]
            cut_ocr = cut.replace("O", "0")
            for m in re.finditer(r"R[C0][-\s]*M[A4]M[-\s]*([0-90]{4,12})", cut_ocr):
                raw = m.group(1).replace("O", "0")
                d = self._reconstruct_rcmam_digits(raw)
                if d:
                    nums.append(int(d))

        for line in clean_lines:
            if "RECEIVING" not in line or "RECORD" not in line:
                continue
            if "RC" not in line and "MAM" not in line:
                continue
            cut = line
            po_pos = len(cut)
            for token in ["PURCHASE ORDER", "P.O", "PO NO", "PO#", "PO "]:
                pos = cut.find(token)
                if pos != -1:
                    po_pos = min(po_pos, pos)
            cut = cut[:po_pos]
            m = re.search(r"(?:RC|R0)[-\s]*(?:MAM|MA4M|M4M)[-\s]*([0-9]{4,12})", cut)
            if m:
                d = self._reconstruct_rcmam_digits(m.group(1))
                if d:
                    nums.append(int(d))

        # Tolerant fallback: if the gated passes above found nothing (e.g. the
        # 'RECEIVING RECORD' label was damaged by a staple hole, or a digit in
        # the code was misread as a letter), scan the whole text for an RC-MAM
        # token with OCR-confusion repair on the numeric part.
        if not nums:
            nums.extend(self._extract_rcmam_tolerant(u))

        # Preserve the order the numbers appear in the document (do NOT sort) so
        # the GRN chain reflects which Receiving Record comes first on the page.
        return list(dict.fromkeys(nums))

    def _extract_rcmam_tolerant(self, text: str) -> List[int]:
        """Best-effort RC-MAM number recovery that tolerates OCR damage.

        Finds 'RC...MAM' (allowing common prefix confusions) and repairs the
        following number region by mapping look-alike letters back to digits
        (O->0, I/L->1, S->5, B->8, ...). Used only as a fallback so it never
        overrides a clean match.
        """
        out: List[int] = []
        u = (text or "").upper()
        for m in re.finditer(r"R[C0G6]\s*[-\s]?\s*M[A4]M", u):
            tail = u[m.end(): m.end() + 18]
            tail = re.sub(r"^[\s:\-#.]+", "", tail)
            region = tail[:14]
            # Stop the number region at a clear word break (2+ spaces) or a
            # following field label, so we don't slurp unrelated characters.
            region = re.split(r"\s{2,}|PURCHASE|\bPO\b|P\.O", region)[0]
            fixed = region.translate(_OCR_DIGIT_FIX)
            digits = re.sub(r"\D", "", fixed)
            d = self._reconstruct_rcmam_digits(digits)
            if d:
                out.append(int(d))
        return out

    def _extract_grn_from_filename_old(self, p):
        base = os.path.splitext(os.path.basename(p))[0].upper()
        m = re.search(r"(RC-MAM-\d+(?:-\d+)*)", base)
        return m.group(1) if m else ""

    def _build_grn_chain(self, nums: List[int]) -> str:
        if not nums:
            return ""
        pats = self.cfg.get("patterns", {})
        prefix = pats.get("grn_prefix", "RC-MAM-")
        target_len = int(pats.get("grn_digits", 9))
        # Keep document order (first-seen first); only drop duplicates. Example:
        # a PDF showing 19891 then 19890 -> RC-MAM-000019891-19890.
        nums = list(dict.fromkeys(nums))
        first = str(nums[0]).zfill(target_len)
        if len(nums) == 1:
            return f"{prefix}{first}"
        tail = "-".join(str(n) for n in nums[1:])
        return f"{prefix}{first}-{tail}"

    def _extract_grn_full(self, pdf_path: str, text: str) -> str:
        rr_pages = self._collect_receiving_report_pages(pdf_path)
        nums: List[int] = []
        for pg in rr_pages:
            nums.extend(self._extract_grn_candidates_from_receiving_text(pg.get("text", "")))
        if nums:
            return self._build_grn_chain(nums)
        fn_grn = self._extract_grn_from_filename_old(pdf_path)
        if fn_grn:
            return fn_grn
        return ""

    def _extract_receiving_report_fields(self, pdf_path: str) -> Dict:
        pages = self._collect_receiving_report_pages(pdf_path)
        return self._parse_receiving_report_pages(pages)

    def _extract_receiving_report_fields_from_text(self, text: str) -> Dict:
        """Fast path: parse receiving-report fields straight from text that has
        ALREADY been OCR'd (e.g. OCR.space output) without re-opening the PDF or
        running local OCR again. Used by the AI Extract / API auto-ingest flow.

        This is what makes API extraction fast: the previous code called
        _collect_receiving_report_pages(), which re-rendered every page and ran
        local OCR twice per page (header + full) at the configured scale - and it
        was invoked twice per file. Here we reuse the OCR.space text instead.
        """
        raw = text or ""
        # Use form-feed page breaks if the extractor provided them, else 1 page.
        chunks = raw.split("\f") if "\f" in raw else [raw]
        pages: List[Dict] = []
        for i, chunk in enumerate(chunks):
            up = chunk.upper()
            if up.strip():
                pages.append({"index": i, "text": up, "native_text": up})
        # Mirror offline behaviour: prefer pages detected as receiving reports,
        # but fall back to all pages if the detector matches none (OCR variance).
        rr_pages = [p for p in pages if self._is_receiving_report_text(p["text"])]
        return self._parse_receiving_report_pages(rr_pages if rr_pages else pages)

    def _parse_receiving_report_pages(self, pages: List[Dict]) -> Dict:
        all_nums: List[int] = []
        best_date = ""
        best_po = ""
        sums = {"USD": 0.0, "MVR": 0.0, "EUR": 0.0, "GBP": 0.0, "SGD": 0.0}

        def _apply(cur_pre, amount_str, cur_post):
            cur = (cur_pre or cur_post or "").upper().strip()
            currency = _CURRENCY_MAP.get(cur)
            if not currency:
                return
            try:
                val = float(amount_str.replace(",", ""))
                sums[currency] += val
            except ValueError:
                pass

        seen_totals = set()

        for pg in pages:
            txt = (pg.get("text") or "").upper()
            page_index = pg.get("index", 0)

            nums = self._extract_grn_candidates_from_receiving_text(txt)
            all_nums.extend(nums)

            if not best_date:
                best_date = self._extract_date_from_receiving_text(txt)
            if not best_po:
                best_po = self._extract_po_from_receiving_text(txt)

            # Strong OCR-aware invoice-total extraction.  This handles
            # currency-symbol confusion ($ -> S), missing spaces, and broken
            # thousands separators such as MVR8.444.00.
            for currency, val, repaired in _extract_ocr_invoice_totals(txt):
                dedup_key = (page_index, currency, f"{val:.2f}")
                if dedup_key in seen_totals:
                    continue
                seen_totals.add(dedup_key)
                sums[currency] += val

        formatted_totals = {}
        for k, v in sums.items():
            formatted_totals[k] = f"{v:.2f}" if v > 0 else ""

        return {
            "pages_found": len(pages),
            "grn": self._build_grn_chain(all_nums),
            "date": best_date,
            "po": best_po,
            "totals": formatted_totals,
        }

    # ------------- INVOICE EXTRACTION -------------
    def _extract_invoice_for_supplier(self, raw: str, supplier_hint: str) -> Optional[str]:
        """Match the invoice number against this supplier's KNOWN historical
        formats (mined from past GRN dispatch records, config["invoice_formats"]).

        Each format has a strict `regex` (exact literal prefix + flexible
        digit counts) and a tolerant `shape_regex` (same digit flexibility,
        but the literal letters are matched by length/class only, so an OCR
        letter-misread like 'MSI' -> 'MSL' still lines up). On a shape match
        the value is REBUILT from the format's canonical `template` plus the
        OCR'd digit groups - i.e. the known prefix is forced, exactly like
        the user asked for PO/GRN, because a supplier's invoice-series prefix
        does not change over time either.

        Formats are tried most-common-first. Returns None if this supplier
        has no config entry or nothing matches, so the caller can fall back
        to the existing generic extraction untouched.
        """
        fmt_entry = self.cfg.get("invoice_formats", {}).get(supplier_hint.upper())
        if not fmt_entry:
            return None
        formats = fmt_entry.get("formats", [])
        if not formats:
            return None

        label_ctxs = [
            r"INVOICE\s*(?:NUMBER|NUM(?:BER)?|NO\.?|#|NR\.?)?\s*[:\-]?\s*",
            r"(?:INV|BILL)\s*(?:NO\.?|#|NUMBER)?\s*[:\-]?\s*",
            r"TAX\s*INVOICE\s*(?:NO\.?|#|NUMBER)?\s*[:\-]?\s*",
            r"CREDIT\s*NOTE\s*(?:NO\.?|#|NUMBER)?\s*[:\-]?\s*",
        ]

        # Pass 1: strict match (literal prefix intact) near a label - most
        # reliable, tried across every format before loosening anything.
        for fmt in formats:
            pat = fmt.get("regex")
            if not pat:
                continue
            for ctx in label_ctxs:
                m = re.search(ctx + r"(" + pat + r")", raw)
                if m:
                    return _clean_invoice_token(m.group(1)).replace("/", " ").replace("\\", " ").strip()

        # Pass 2: shape match near a label (prefix letters garbled by OCR) -
        # rebuild from the canonical template + the OCR'd digits. Each digit
        # run in the format is its own capture group (mining time), so we
        # read the slot values straight off the match instead of re-scanning
        # the matched text for digits (which would misfire the moment a
        # garbled letter looks like a digit, e.g. 'INV' OCR'd as '1NV').
        for fmt in formats:
            pat = fmt.get("shape_regex")
            digit_lengths = fmt.get("digit_lengths", [])
            if not pat:
                continue
            for ctx in label_ctxs:
                m = re.search(ctx + pat, raw)
                if m and len(m.groups()) == len(digit_lengths):
                    v = self._rebuild_invoice_from_template(m.groups(), fmt)
                    if v:
                        return v

        # Pass 3: strict match anywhere in the text (no clean label found,
        # e.g. it was damaged) - still supplier-scoped, so far safer than the
        # fully generic whole-document fallback.
        for fmt in formats:
            pat = fmt.get("regex")
            if pat:
                m = re.search(pat, raw)
                if m:
                    return _clean_invoice_token(m.group(0)).replace("/", " ").replace("\\", " ").strip()

        return None

    def _repair_invoice_fixed_part(self, invoice_value: str, supplier_hint: str) -> str:
        """Repair only 1-2 OCR errors in a configured invoice format's fixed part.

        Critical rule: this function NEVER adds a configured prefix/year/etc.
        If the OCR value is just ``6541`` and the configured template is
        ``INV-2026-{0}``, ``6541`` remains ``6541``.

        When the OCR value already contains a fixed part, that fixed part can be
        corrected when it is within two edits of a configured fixed prefix. The
        variable digit portion is preserved exactly from OCR.
        """
        value = (invoice_value or "").strip()
        if not value or not supplier_hint:
            return value

        fmt_entry = self.cfg.get("invoice_formats", {}).get(supplier_hint.upper())
        if not fmt_entry:
            return value

        formats = fmt_entry.get("formats", [])
        if not formats:
            return value

        # Prefer the configured template prefix.  Only the literal text before
        # the first variable slot is considered; nothing is inserted when that
        # prefix is absent from the OCR value.
        for fmt in formats:
            template = str(fmt.get("template") or "")
            if not template or "{" not in template:
                continue
            fixed_prefix = template.split("{", 1)[0]
            if not fixed_prefix:
                continue

            # Normalize only for comparison; preserve the OCR value's original
            # separators when there is no repair to make.
            prefix_len = len(fixed_prefix)
            if len(value) < prefix_len:
                continue

            observed_prefix = value[:prefix_len]
            if _levenshtein(observed_prefix.upper(), fixed_prefix.upper()) > 2:
                continue

            # Do not turn a numeric-only OCR result into the configured format.
            # Also require at least one alphabetic/symbolic fixed character to
            # have actually been present in the OCR value.
            if not re.search(r"[A-Z/\\_-]", observed_prefix.upper()):
                continue

            repaired = fixed_prefix + value[prefix_len:]
            return repaired

        return value

    def _rebuild_invoice_from_template(self, digit_groups, fmt: dict) -> str:
        """Slot the digit groups matched by the format's own capture groups
        into its canonical template (e.g. 'INV-{0}' + ('0509456',) ->
        'INV-0509456'), forcing the known-correct literal prefix/separators
        regardless of what OCR actually rendered them as.
        """
        template = fmt.get("template", "")
        if not digit_groups or any(g is None for g in digit_groups):
            return ""
        try:
            return template.format(*digit_groups)
        except (IndexError, KeyError):
            return ""

    def _extract_invoice(self, text, supplier_hint=""):
        raw = text.upper()

        # IMPORTANT: if OCR already extracted an invoice value from an explicit
        # invoice-number label, that value is authoritative.  Do NOT rebuild it
        # from a learned regex/template.  For example, if the document says
        # "Invoice No: 6541", the result must stay exactly "6541" -- never
        # become "INV-2026-6541" merely because a historical invoice pattern
        # contains that prefix/year.
        #
        # Supplier-specific patterns remain a FALLBACK for cases where the raw
        # labelled value could not be extracted (or the label itself is damaged).
        raw_labeled = _find_invoice_value(raw)
        if raw_labeled:
            raw_labeled = _clean_invoice_token(raw_labeled)
            if raw_labeled:
                # RAW OCR is authoritative for the variable invoice number.
                # A configured format may ONLY repair a small OCR error in its
                # fixed/literal portion.  It must NEVER manufacture a prefix,
                # year, separator, or other characters that were not present in
                # the OCR value.  Example:
                #   OCR:    6541
                #   format: INV-2026-{0}
                #   result: 6541  (NOT INV-2026-6541)
                #
                # If OCR actually contains the fixed portion but has 1-2 bad
                # characters, e.g. INV-202S-6541, the fixed portion may be
                # corrected to INV-2026 while preserving the OCR invoice digits.
                raw_labeled = self._repair_invoice_fixed_part(
                    raw_labeled, supplier_hint
                )
                return raw_labeled.replace("/", " ").replace("\\", " ").strip()

        # Supplier-specific, data-driven format matching is only a fallback.
        # It may repair/reconstruct an invoice when the raw labelled extraction
        # is unavailable, but it must never override a clean raw invoice value.
        if supplier_hint:
            v = self._extract_invoice_for_supplier(raw, supplier_hint)
            if v:
                return v

        if supplier_hint:
            hint_upper = supplier_hint.upper()
            for key, patterns in SUPPLIER_INVOICE_HINTS.items():
                if key.upper() in hint_upper:
                    for pat_str in patterns:
                        for ctx in [
                            rf"INVOICE\s*(?:NUMBER|NUM(?:BER)?|NO\.?|#)?\s*[:\-]\s*({pat_str})",
                            rf"(?:INV|BILL)\s*(?:NO\.?|#)?\s*[:\-]\s*({pat_str})",
                        ]:
                            try:
                                m = re.search(ctx, raw)
                                if m:
                                    v = _clean_invoice_token(m.group(1))
                                    if v:
                                        v = _fix_invoice_prefix(v, self.cfg.get("invoice_prefix_fixes", {}))
                                        return v.replace("/", " ").replace("\\", " ").strip()
                            except re.error:
                                pass
                    break

        v = _find_invoice_value(raw)
        v = _clean_invoice_token(v) if v else ""
        if v:
            v = _fix_invoice_prefix(v, self.cfg.get("invoice_prefix_fixes", {}))
            return v.replace("/", " ").replace("\\", " ").strip()
        return None

    def _extract_invoice_old(self, text):
        raw = text.upper()
        v = _find_invoice_value(raw)
        v = _clean_invoice_token(v) if v else ""
        return v or ""

    # ------------- LEGACY HELPERS -------------
    def _legacy_load_suppliers(self):
        base = self.dirs.get("base", "")
        txt = os.path.join(base, "suppliers.txt")
        if os.path.exists(txt):
            with open(txt, "r", encoding="utf-8") as f:
                sups = [x.strip().upper() for x in f if x.strip()]
        else:
            sups = [s.upper() for s in self.cfg.get("suppliers", [])]
        return sups, self.cfg.get("aliases", {})

    def _legacy_normalize(self, t):
        return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", " ", t.upper())).strip()

    def _legacy_extract_text(self, pdf_path):
        full = ""
        try:
            doc = fitz.open(pdf_path)
            try:
                for page in doc:
                    rect = page.rect
                    crop = fitz.Rect(0, 0, rect.width, min(rect.height, 650))
                    pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), clip=crop)
                    img = PILImage.open(io.BytesIO(pix.tobytes("png")))
                    full += pytesseract.image_to_string(img, config="--psm 6 --oem 3") + "\n"
                    img.close()
            finally:
                doc.close()
        except Exception as e:
            logging.error(f"Legacy text extraction [{pdf_path}]:{e}")
        return full.upper()

    def _legacy_best_supplier_match(self, text, cands, aliases):
        text = self._legacy_normalize(text)
        words = set(text.split())
        best_name = None
        best_score = 0.0
        for c in cands:
            cn = self._legacy_normalize(c)
            if not cn:
                continue
            if cn in text:
                return self._resolve_alias(c, aliases)
            cn_w = set(cn.split())
            overlap = len(words & cn_w) / max(len(cn_w), 1)
            fuzzy = SequenceMatcher(None, text[:2000], cn).ratio()
            score = max(overlap, fuzzy)
            for alias in aliases.get(c, []):
                an = self._legacy_normalize(alias)
                if an and an in text:
                    return self._resolve_alias(c, aliases)
                an_w = set(an.split())
                score = max(
                    score,
                    len(words & an_w) / max(len(an_w), 1),
                    SequenceMatcher(None, text[:2000], an).ratio(),
                )
            if score > best_score:
                best_score = score
                best_name = c
        return self._resolve_alias(best_name, aliases) if best_score >= 0.25 and best_name else None

    def _legacy_extract_supplier(self, text, cands, aliases):
        m = re.search(r"SUPPLIE(?:R)?\s*[:\-]?\s*", text, re.IGNORECASE)
        if m:
            chunk = text[m.end(): m.end() + 600]
            for kw in ["INVOICE", "RECEIVING", "GRN", "DATE", "BILL", "AMOUNT", "SOURCE", "DIRCET", "DIRECT"]:
                pos = chunk.find(kw)
                if 0 < pos < len(chunk):
                    chunk = chunk[:pos]
            chunk = self._legacy_normalize(chunk.replace("\n", " "))
            r = self._legacy_best_supplier_match(chunk, cands, aliases)
            if r:
                return r
        return self._legacy_best_supplier_match(text, cands, aliases)

    # ------------- PROCESSING MODES -------------
    def _process_file_legacy(self, pdf_path):
        cands, aliases = self._legacy_load_suppliers()
        text = self._legacy_extract_text(pdf_path)
        supplier = self._legacy_extract_supplier(text, cands, aliases) or "UNKNOWN SUPPLIER"
        grn = self._extract_grn_full(pdf_path, text) or "NO-GRN"
        invoice = self._extract_invoice_old(text) or "NO-INVOICE"
        if supplier != "UNKNOWN SUPPLIER":
            _set_last_good_supplier(supplier)
        elif _get_last_good_supplier():
            supplier = _get_last_good_supplier()
        return {"supplier": supplier, "grn": grn, "invoice": invoice, "confidence": 70.0}

    def _process_file_custom(self, pdf_path, text):
        method = self.cfg.get("app_settings", {}).get("supplier_extraction_method", "both")
        supplier = None
        confidence = 0.0
        if method in ("receiving_report_field", "both"):
            supplier = self._extract_supplier_from_field(text)
            if supplier:
                confidence = 85.0
        if not supplier and method in ("header_company_name", "both"):
            supplier = self._extract_company_from_invoice_pages(pdf_path)
            if supplier:
                confidence = 80.0
        if not supplier:
            supplier, confidence = self._match_supplier_with_confidence(text, os.path.basename(pdf_path))
        if not supplier or supplier == "UNKNOWN SUPPLIER":
            supplier = _get_last_good_supplier() or "UNKNOWN SUPPLIER"
            confidence = 0.0
        else:
            _set_last_good_supplier(supplier)
        grn = self._extract_grn_full(pdf_path, text) or "NO-GRN"
        invoice = self._extract_invoice(text, supplier_hint=supplier) or "NO-INVOICE"
        return {"supplier": supplier, "grn": grn, "invoice": invoice, "confidence": confidence}

    def _process_file_mixed(self, pdf_path, text):
        method = self.cfg.get("app_settings", {}).get("supplier_extraction_method", "both")
        supplier = None
        confidence = 0.0
        if method in ("receiving_report_field", "both"):
            supplier = self._extract_supplier_from_field(text)
            if supplier:
                confidence = 85.0
        if not supplier and method in ("header_company_name", "both"):
            supplier = self._extract_company_from_invoice_pages(pdf_path)
            if supplier:
                confidence = 80.0
        if not supplier:
            supplier, confidence = self._match_supplier_with_confidence(text, os.path.basename(pdf_path))
        if not supplier or supplier == "UNKNOWN SUPPLIER":
            supplier = _get_last_good_supplier() or "UNKNOWN SUPPLIER"
            confidence = 0.0
        else:
            _set_last_good_supplier(supplier)
        grn = self._extract_grn_full(pdf_path, text) or "NO-GRN"
        legacy_text = self._legacy_extract_text(pdf_path)
        invoice = self._extract_invoice_old(legacy_text) or "NO-INVOICE"
        return {"supplier": supplier, "grn": grn, "invoice": invoice, "confidence": confidence}

    # ------------- FILENAME / SAFE NAME -------------
    @staticmethod
    def _safe_filename(n):
        n = re.sub(r"[/\\]", " ", n)
        n = re.sub(r"\s+", " ", n)
        return re.sub(r'[<>:"|?*]', "", n).strip()

    @staticmethod
    def _extract_scan_number(path: str) -> int:
        """
        Extract numeric order from filenames like:
        SCAN_0040.pdf -> 40
        SCAN-0039.pdf -> 39

        Files without a scan number go to the end.
        """
        name = os.path.basename(path).upper()
        m = re.search(r"SCAN[_\- ]*(\d+)", name)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass

        m = re.search(r"(\d+)", name)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass

        return -1

    def _get_pdf_files_strict_order(self, folder: str) -> List[str]:
        """
        Return PDFs in chronological filename-number order:
        SCAN_0001, SCAN_0002, ..., SCAN_0040.

        Both the online and local GRN Dispatch workers consume this one list
        serially, so a burst of scans retains its scanner sequence.
        """
        if not folder or not os.path.isdir(folder):
            return []

        files = [
            os.path.join(folder, f)
            for f in os.listdir(folder)
            if f.lower().endswith(".pdf")
        ]

        files.sort(
            key=lambda p: (self._extract_scan_number(p), os.path.basename(p).upper()),
        )
        return files

    def _extract_totals_from_pdf_old(self, pdf_path):
        rr = self._extract_receiving_report_fields(pdf_path)
        return rr.get("totals", {"USD": "", "MVR": "", "EUR": "", "GBP": "", "SGD": ""})

    # ------------- DUPLICATE INVOICE DETECTION -------------
    def _find_duplicate_invoice(self, invoice: str, processed_folder: str) -> List[str]:
        if not invoice or invoice == "NO-INVOICE":
            return []
        dupes = []
        try:
            inv_clean = invoice.strip().upper()
            for fn in os.listdir(processed_folder):
                if fn.lower().endswith(".pdf") and inv_clean in fn.upper():
                    dupes.append(fn)
        except Exception:
            pass
        return dupes

    # ------------- RENAME SINGLE FILE -------------
    def _rename_single_file(self, pdf_path, processed, failed, mode):
        fname = os.path.basename(pdf_path)
        rd = {
            "file": fname, "new_name": "", "supplier": "UNKNOWN SUPPLIER",
            "grn": "", "invoice": "", "status": "error",
            "dest_path": "", "duplicate_warning": "", "confidence": 0.0,
        }
        try:
            if mode == "legacy":
                fields = self._process_file_legacy(pdf_path)
            else:
                text = self._extract_text(pdf_path)
                if mode == "mixed":
                    fields = self._process_file_mixed(pdf_path, text)
                else:
                    fields = self._process_file_custom(pdf_path, text)

            sup  = fields["supplier"] or "UNKNOWN SUPPLIER"
            grn  = fields["grn"] or "RC-MAM-0000"
            inv  = fields["invoice"] or "NO-INVOICE"
            conf = fields.get("confidence", 0.0)

            filename_invoice = f"IN {inv}" if inv and inv != "NO-INVOICE" else "NO-INVOICE"

            dupes = self._find_duplicate_invoice(inv, processed)
            if dupes:
                rd["duplicate_warning"] = f"Invoice already exists: {dupes[0]}"
                logging.warning(f"Duplicate invoice [{inv}] found for [{fname}]: {dupes}")

            dry_run = self.cfg.get("app_settings", {}).get("dry_run", False)
            dest = os.path.join(processed, self._safe_filename(f"{sup} GRN {grn} {filename_invoice}.pdf"))

            cnt = 1
            base, ext = os.path.splitext(dest)
            while os.path.exists(dest) and not dry_run:
                dest = f"{base}_{cnt}{ext}"
                cnt += 1

            if not dry_run:
                _safe_file_move(pdf_path, dest)
                rd["status"] = "success"
                rd["dest_path"] = dest
            else:
                rd["status"] = "simulated"
                rd["dest_path"] = pdf_path

            rd["new_name"]   = os.path.basename(dest)
            rd["supplier"]   = sup
            rd["grn"]        = grn
            rd["invoice"]    = inv if inv != "NO-INVOICE" else ""
            rd["confidence"] = conf

        except Exception as e:
            logging.error(f"Rename failed [{fname}]:{e}", exc_info=True)
            rd["status"] = "error"
            try:
                _safe_file_move(pdf_path, os.path.join(failed, fname))
            except Exception:
                pass
        return rd

    # ------------- DISPATCH SINGLE FILE -------------
    def _dispatch_single_file(self, pdf_path, scan_index=0, mode=None):
        fn = os.path.basename(pdf_path)
        if mode is None:
            mode = self.cfg.get("app_settings", {}).get("processing_mode", "legacy")
        try:
            core = self._extract_core_fields_for_file(pdf_path, mode)
            return {
                "doc_id": f"{fn}|dispatch|{scan_index}",
                "file": fn,
                "date": core.get("date", ""),
                "supplier": core.get("supplier", "UNKNOWN SUPPLIER"),
                "po": core.get("po", "") or "MAM-0000",
                "invoice": core.get("invoice_dispatch", ""),
                "usd": core.get("usd", ""),
                "mvr": core.get("mvr", ""),
                "eur": core.get("eur", ""),
                "gbp": core.get("gbp", ""),
                "sgd": core.get("sgd", ""),
                "grn": core.get("grn", "RC-MAM-0000"),
                "confidence": core.get("confidence", 0.0),
                "is_valid": True, "errors": "",
                "raw_path": pdf_path, "scan_index": scan_index,
            }
        except Exception as e:
            logging.error(f"Dispatch error [{fn}]:{e}", exc_info=True)
            return {
                "doc_id": f"{fn}|dispatch|{scan_index}",
                "file": fn, "date": "", "supplier": "", "po": "MAM-0000",
                "invoice": "", "usd": "", "mvr": "", "eur": "", "gbp": "", "sgd": "",
                "grn": "", "confidence": 0.0, "is_valid": False, "errors": str(e),
                "raw_path": pdf_path, "scan_index": scan_index,
            }


# ---------------------------------------------------------------------------
# RESOURCE HELPER
# ---------------------------------------------------------------------------
def resource_path(relative_path):
    try:
        base = Path(sys._MEIPASS)
    except Exception:
        base = Path(__file__).parent
    return base / relative_path


def _safe_file_move(src: str, dest: str, max_retries: int = 3, retry_delay: float = 0.5) -> None:
    """
    Move a file with retry logic for locked files (common on Windows).

    Tries shutil.move first; on PermissionError or OSError (indicating the
    file may be locked by another process), retries with a small delay.
    Falls back to copy+delete if move keeps failing.
    """
    import time
    last_err = None
    for attempt in range(max_retries):
        try:
            shutil.move(src, dest)
            return
        except (PermissionError, OSError) as e:
            last_err = e
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (attempt + 1))
    # Last resort: copy then delete (works if the lock is on the move operation)
    try:
        shutil.copy2(src, dest)
        os.remove(src)
    except Exception as e:
        logging.error(f"Safe file move failed [{src} -> {dest}]: {e}")
        raise last_err if last_err else e


def _safe_file_copy(src: str, dest: str, max_retries: int = 3, retry_delay: float = 0.5) -> None:
    """
    Copy a file with retry logic for locked files (common on Windows).
    """
    import time
    last_err = None
    for attempt in range(max_retries):
        try:
            shutil.copy2(src, dest)
            return
        except (PermissionError, OSError) as e:
            last_err = e
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (attempt + 1))
    raise last_err if last_err else OSError(f"Failed to copy {src} -> {dest}")


def _validate_path(path: str, must_exist: bool = False, allow_relative: bool = True) -> str:
    """
    Validate a user-provided path. Returns the resolved absolute path.
    Raises ValueError if the path is empty, contains null bytes, or is outside
    the allowed base directory.
    """
    if not path or not isinstance(path, str):
        raise ValueError("Path must be a non-empty string")
    if "\x00" in path:
        raise ValueError("Path contains null bytes")
    p = Path(path)
    if not allow_relative and not p.is_absolute():
        raise ValueError(f"Path must be absolute: {path}")
    resolved = str(p.resolve() if p.exists() else p.absolute())
    if must_exist and not os.path.exists(resolved):
        raise ValueError(f"Path does not exist: {resolved}")
    return resolved


# File-level lock to prevent concurrent processing of the same PDF
_file_locks: Dict[str, threading.Lock] = {}
_file_locks_guard = threading.Lock()


def _get_file_lock(path: str) -> threading.Lock:
    """Get or create a per-file lock for preventing concurrent processing."""
    key = os.path.abspath(path)
    with _file_locks_guard:
        if key not in _file_locks:
            _file_locks[key] = threading.Lock()
        return _file_locks[key]


# ---------------------------------------------------------------------------
# GUI APPLICATION
# ---------------------------------------------------------------------------
class MaafushivaruHub(tk.Tk, OCRWorkerMixin):
    APP_VERSION = APP_VERSION

    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        try:
            ip = resource_path("logo.ico")
            if ip.exists():
                self.iconbitmap(str(ip))
        except Exception:
            pass
        _sw, _sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{min(1440, _sw - 80)}x{min(920, _sh - 120)}+20+20")
        self.minsize(min(1120, _sw - 80), min(720, _sh - 120))
        self.configure(bg=BG)
        # Start maximized on Windows; some Tk window managers (including the
        # Linux runtime used for verification) do not recognise "zoomed".
        try:
            self.state("zoomed")
        except tk.TclError:
            pass
        # ... rest of __init__ unchanged ...

        # State
        self._worker_running   = False
        self._dispatch_running = False
        self._cancel_requested = False
        self._rename_results:   List[Dict] = []
        self._dispatch_results: List[Dict] = []
        self._rename_queue   = queue.Queue()
        self._dispatch_queue = queue.Queue()
        self._rename_row_map:   Dict[str, Dict] = {}
        self._dispatch_row_map: Dict[str, Dict] = {}
        self._rename_all_rows:   List[str] = []
        self._dispatch_all_rows: List[str] = []

        # Watchdog
        self._watcher_observer: Optional["Observer"] = None
        self._watcher_handler:  Optional[ScannedFolderHandler] = None
        self._watcher_queue = queue.Queue()

        self.config_path = self._find_config()
        self.cfg = self._load_config()
        self._ensure_config_file()
        if ENGINE_MANAGER_AVAILABLE:
            # Expose the configured C:\PaddleOCR\venv site-packages before any
            # PaddleOCR runtime import occurs. The application itself continues
            # running from its normal Python environment.
            try:
                oem.configure_runtime(self.cfg)
            except Exception as e:
                logging.debug(f"PaddleOCR runtime configuration skipped: {e}")
        self.dirs = self._resolve_dirs()
        self._ensure_dirs()
        self._configure_tesseract()
        self._setup_logging()

        # Tk variables
        self._status_var = tk.StringVar(value="Ready — waiting for files.")
        self._engine_badge_var = tk.StringVar(value="")
        self._processing_mode_badge_var = tk.StringVar(value="")
        self._watcher_badge_var = tk.StringVar(value="")
        self._threads_var = tk.BooleanVar(
            value=self.cfg.get("app_settings", {}).get("enable_multi_threading", True)
        )
        self._dry_run_var = tk.BooleanVar(
            value=self.cfg.get("app_settings", {}).get("dry_run", False)
        )
        self._notify_var = tk.BooleanVar(
            value=self.cfg.get("app_settings", {}).get("desktop_notifications", True)
        )
        # GRN Dispatch has one explicit engine selector (ONLINE / OFFLINE) plus
        # the concrete engine inside each mode:
        #   ONLINE  -> OCR.space engine number 1 / 2 / 3
        #   OFFLINE -> local engine key: tesseract / paddleocr / easyocr
        # The legacy watcher booleans remain for compatibility, but are derived
        # from this choice so one PDF can never enter both pipelines.
        settings = self.cfg.get("app_settings", {})
        selected_grn_engine = settings.get("grn_processing_engine", "")
        if selected_grn_engine not in (GRN_ONLINE, GRN_OFFLINE):
            selected_grn_engine = GRN_ONLINE if settings.get("auto_ingest_api") else GRN_OFFLINE
        self._grn_engine_var = tk.StringVar(value=selected_grn_engine)

        # Concrete engine inside the OFFLINE mode (defaults to Tesseract).
        self._local_engine_var = tk.StringVar(
            value=settings.get("local_ocr_engine", "tesseract")
        )
        # Concrete engine inside the ONLINE mode (OCR.space API engine 1/2/3).
        self._online_engine_var = tk.IntVar(
            value=int(self.cfg.get("ocr_space", {}).get("OCREngine", 2) or 2)
        )
        # The single label shown in the GRN Dispatch combo.
        self._grn_choice_var = tk.StringVar(
            value=grn_choice_label(
                selected_grn_engine,
                self._local_engine_var.get()
                if selected_grn_engine == GRN_OFFLINE
                else self._online_engine_var.get(),
            )
        )
        # Engine-install activity guard (one install at a time).
        self._engine_install_running = False
        self._auto_ingest_enabled_var = tk.BooleanVar(
            value=bool(settings.get(
                "auto_ingest_enabled",
                settings.get("auto_ingest_offline", False) or settings.get("auto_ingest_api", False),
            ))
        )
        self._watcher_offline_var = tk.BooleanVar(
            value=self._auto_ingest_enabled_var.get() and selected_grn_engine == "offline"
        )
        self._watcher_api_var = tk.BooleanVar(
            value=self._auto_ingest_enabled_var.get() and selected_grn_engine == "online"
        )
        # One-shot hook the AI Extract poller calls when an auto run finishes.
        self._aix_on_complete = None
        self._conf_threshold_var = tk.IntVar(
            value=self.cfg.get("app_settings", {}).get("confidence_warn_threshold", 80)
        )

        # AI supplier matcher + log store
        self._ai_matcher = None
        self._ai_log_lines: List[str] = []
        self._ai_log_lock = threading.Lock()
        self._ai_log_window = None
        self._ai_log_text = None
        if AI_MATCHER_AVAILABLE:
            try:
                from ai_supplier_matcher import AISupplierMatcher
                self._ai_matcher = AISupplierMatcher(self.cfg, logger_func=self._append_ai_log)
            except Exception as e:
                logging.warning(f"AI matcher init failed: {e}")
        self._ai_status_var = tk.StringVar(value="")
        self._build_style()
        self._build_layout()
        self._refresh_engine_badge()
        self._refresh_processing_mode_badge()
        self._refresh_dashboard_stats()

        # Poll watcher queue
        self.after(500, self._poll_watcher_queue)

        # First-time setup: verify the application libraries, offer to install
        # the OCR engines and remember the answer. It runs BEFORE the watcher
        # starts so a fresh installation never fails on a missing engine.
        if self._engine_setup_required():
            self.after(400, lambda: self._run_engine_setup_wizard(first_run=True))
            self.after(900, self._start_watcher_if_enabled)
            self.after(1400, self._startup_auto_ingest)
        else:
            self.after(600, self._start_watcher_if_enabled)
            self.after(1100, self._startup_auto_ingest)

    def _start_watcher_if_enabled(self):
        """Start the auto-ingest watcher when it was enabled on the last run."""
        if self._watcher_offline_var.get() or self._watcher_api_var.get():
            self._start_watcher()

    def _startup_auto_ingest(self):
        """Process PDFs that were already waiting in SCANNED when the app opened.

        With auto-ingest ON the software must not wait for a NEW file to appear:
        whatever is already sitting in SCANNED is picked up on startup and run
        through the selected engine strictly in scan order — SCAN_0001 first,
        then SCAN_0002, and so on — exactly like a manual "Process New PDFs".
        """
        if self._active_ingest_mode() == "off":
            return
        try:
            scanned = self.dirs.get("scanned", "")
            pending = self._get_pdf_files_strict_order(scanned)
        except Exception as e:
            logging.warning(f"[STARTUP] Could not scan the SCANNED folder: {e}")
            return
        if not pending:
            self._set_status(
                f"Auto-ingest ({self._active_ingest_mode()}) is ON — watching SCANNED for new scans."
            )
            return

        names = ", ".join(os.path.basename(p) for p in pending[:5])
        more = f" … (+{len(pending) - 5} more)" if len(pending) > 5 else ""
        logging.info(
            "[STARTUP] Auto-ingest processing %d existing PDF(s) in order: %s",
            len(pending), [os.path.basename(p) for p in pending],
        )
        self._set_status(
            f"[AUTO-INGEST {self._active_ingest_mode().upper()}] Processing "
            f"{len(pending)} existing scan(s) in order: {names}{more}",
            ACCENT,
        )
        self._auto_ingest_grn_dispatch()

    # ------------------------------------------------------------------
    # CONFIG / PATH / LOGGING
    # ------------------------------------------------------------------
    def _find_config(self):
        cwd = Path.cwd() / "config.json"
        scr = Path(__file__).parent / "config.json"
        return cwd if cwd.exists() else scr

    def _load_config(self):
        default = {
            "folders": {
                "base": ".",
                "scanned": "SCANNED",
                "processed": "PROCESSED",
                "archive": "ARCHIVE",
                "failed": "FAILED",
                "logs": "LOGS"
            },
            "suppliers": [],
            "aliases": {},
            "invoice_prefix_fixes": {
                "MSL": "MSI",
                "MS1": "MSI"
            },
            "patterns": {
                "grn_prefix": "RC-MAM-",
                "grn_digits": 9,
                "grn_min_digits": 4,
                "grn_max_digits": 12,
                "po_prefix": "MAM-",
                "po_min_digits": 5
            },
            "app_settings": {
                "processing_mode": "custom",
                "supplier_extraction_method": "both",
                "dry_run": False,
                "desktop_notifications": True,
                "auto_ingest_watcher": False,
                "auto_ingest_offline": False,
                "auto_ingest_api": False,
                "auto_ingest_enabled": False,
                "grn_processing_engine": "offline",
                "local_ocr_engine": "tesseract",
                "online_ocr_engine": 2,
                "engine_setup_completed": False,
                "serial_scan_next": 1,
                "engine_setup_state": {},
                "receiving_label_fuzzy": True,
                "delete_temp_after_send": True,
                "confidence_warn_threshold": 80,
                "page_scan_region_enabled": False,
                "page_scan_region_percent": 100,
                "supplier_match_strategy": "combined",
                "ocr_mode": "zone",
                "ocr_word_correction_enabled": False,
                "smart_cross_match_enabled": False,
                "ocr_engine": "tesseract",
                "extract_text_before_ocr": True,
                "enhance_images": True,
                "ocr_fallback_to_tesseract": True,
                "enable_multi_threading": True,
                "max_threads": 4,
                "fuzzy_match_threshold": 85,
                "image_scale_factor": 2,
                "extraction_source": "auto"
            },
            "ai_settings": {
                "enabled": False,
                "provider": "openai",
                "model": "gpt-4o-mini",
                "api_key": "",
                "custom_base_url": "",
                "timeout_seconds": 20
            },
            "ocr_space": {
                "api_key": "",
                "language": "eng",
                "isOverlayRequired": False,
                "detectOrientation": True,
                "scale": True,
                "OCREngine": 2,
                "isTable": False,
                "filetype": "PDF",
                "timeout_seconds": 30,
                "max_upload_mb": 1.0
            }
        }

        if not self.config_path.exists():
            return default

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            logging.warning(f"Config load failed, using defaults: {e}")
            return default

        # --- Backward-compat migration (BEFORE the defaults are merged) ------
        # Merging the default values first would pre-fill every new key and
        # hide the legacy ones, which silently discarded an existing engine
        # choice and its auto-ingest switch. The migration therefore runs on
        # the file as it was actually saved.
        self._migrate_legacy_settings(cfg)

        for key, value in default.items():
            if key not in cfg:
                cfg[key] = value
            elif isinstance(value, dict):
                for subkey, subvalue in value.items():
                    if subkey not in cfg[key]:
                        cfg[key][subkey] = subvalue

        # --- Validate critical config values -------------------------------
        self._validate_config(cfg)

        return cfg

    def _migrate_legacy_settings(self, cfg: dict):
        """Bring an older config.json forward without losing the user's choices.

        Legacy -> current mapping:
          auto_ingest_watcher          -> auto_ingest_offline / auto_ingest_enabled
          ocr_engine                   -> local_ocr_engine (the OFFLINE engine)
          ocr_space.OCREngine          -> online_ocr_engine (the ONLINE engine)
          grn_processing_engine        -> GRN_ONLINE / GRN_OFFLINE
        """
        s = cfg.setdefault("app_settings", {})

        # 1) One visible mode selector (ONLINE / OFFLINE).
        if s.get("grn_processing_engine") not in (GRN_ONLINE, GRN_OFFLINE):
            s["grn_processing_engine"] = GRN_ONLINE if s.get("auto_ingest_api") else GRN_OFFLINE

        # 2) The old single watcher flag becomes the OFFLINE auto-ingest switch.
        if "auto_ingest_offline" not in s and "auto_ingest_api" not in s:
            s["auto_ingest_offline"] = bool(s.get("auto_ingest_watcher", False))
            s["auto_ingest_api"] = False
        if s.get("auto_ingest_offline") and s.get("auto_ingest_api"):
            # The two modes are mutually exclusive; the ONLINE one wins.
            s["auto_ingest_offline"] = False
        if "auto_ingest_enabled" not in s:
            s["auto_ingest_enabled"] = bool(
                s.get("auto_ingest_offline") or s.get("auto_ingest_api")
            )

        # 3) Concrete engine inside the OFFLINE mode.
        if s.get("local_ocr_engine") not in ENGINE_LABELS:
            legacy_engine = s.get("ocr_engine")
            s["local_ocr_engine"] = (legacy_engine if legacy_engine in ENGINE_LABELS
                                     else "tesseract")
        s["ocr_engine"] = s["local_ocr_engine"]

        # 4) Concrete engine inside the ONLINE mode (OCR.space 1 / 2 / 3).
        try:
            online = int(s.get("online_ocr_engine", 0) or 0)
        except (TypeError, ValueError):
            online = 0
        if online not in (1, 2, 3):
            try:
                online = int(cfg.get("ocr_space", {}).get("OCREngine", 2) or 2)
            except (TypeError, ValueError):
                online = 2
        if online not in (1, 2, 3):
            online = 2
        s["online_ocr_engine"] = online
        cfg.setdefault("ocr_space", {})["OCREngine"] = online

        # 5) The serial scan counter keeps numbering across restarts.
        try:
            nxt = int(s.get("serial_scan_next", 1) or 1)
        except (TypeError, ValueError):
            nxt = 1
        s["serial_scan_next"] = max(1, nxt)

    def _validate_config(self, cfg: dict):
        """Validate and sanitize config values on startup."""
        s = cfg.setdefault("app_settings", {})
        # Clamp numeric values to safe ranges
        try:
            s["max_threads"] = max(1, min(int(s.get("max_threads", 4)), 16))
        except (ValueError, TypeError):
            s["max_threads"] = 4
        try:
            s["image_scale_factor"] = max(1, min(int(s.get("image_scale_factor", 2)), 10))
        except (ValueError, TypeError):
            s["image_scale_factor"] = 2
        try:
            s["fuzzy_match_threshold"] = max(0, min(int(s.get("fuzzy_match_threshold", 85)), 100))
        except (ValueError, TypeError):
            s["fuzzy_match_threshold"] = 85
        try:
            s["confidence_warn_threshold"] = max(0, min(int(s.get("confidence_warn_threshold", 80)), 100))
        except (ValueError, TypeError):
            s["confidence_warn_threshold"] = 80
        try:
            s["page_scan_region_percent"] = max(1, min(int(s.get("page_scan_region_percent", 100)), 100))
        except (ValueError, TypeError):
            s["page_scan_region_percent"] = 100
        # Validate OCR engine name
        if s.get("ocr_engine", "tesseract") not in ENGINE_LABELS:
            s["ocr_engine"] = "tesseract"
        if s.get("local_ocr_engine", "tesseract") not in ENGINE_LABELS:
            s["local_ocr_engine"] = "tesseract"
        if s.get("grn_processing_engine", GRN_OFFLINE) not in (GRN_ONLINE, GRN_OFFLINE):
            s["grn_processing_engine"] = GRN_OFFLINE
        try:
            s["online_ocr_engine"] = int(s.get("online_ocr_engine", 2))
        except (TypeError, ValueError):
            s["online_ocr_engine"] = 2
        if s["online_ocr_engine"] not in (1, 2, 3):
            s["online_ocr_engine"] = 2
        s["auto_ingest_enabled"] = bool(s.get("auto_ingest_enabled", False))
        # Validate processing mode
        if s.get("processing_mode", "legacy") not in ("legacy", "custom"):
            s["processing_mode"] = "legacy"
        # Validate extraction source
        if s.get("extraction_source", "auto") not in ("auto", "pdf_text_only", "image_only"):
            s["extraction_source"] = "auto"
        # Validate ocr_mode
        if s.get("ocr_mode", "full") not in ("full", "zone"):
            s["ocr_mode"] = "full"
        # Ensure suppliers is a list
        if not isinstance(cfg.get("suppliers"), list):
            cfg["suppliers"] = []
        # Ensure aliases is a dict
        if not isinstance(cfg.get("aliases"), dict):
            cfg["aliases"] = {}
        # Ensure patterns has expected keys
        pats = cfg.setdefault("patterns", {})
        try:
            pats["grn_digits"] = int(pats.get("grn_digits", 9))
            pats["grn_min_digits"] = int(pats.get("grn_min_digits", 4))
            pats["grn_max_digits"] = int(pats.get("grn_max_digits", 12))
        except (ValueError, TypeError):
            pats["grn_digits"] = 9
            pats["grn_min_digits"] = 4
            pats["grn_max_digits"] = 12

        # Transfer destination for processed PDFs (Dashboard)
        s.setdefault("processed_transfer_path", s.get("processed_transfer_path", "") or "")
        try:
            s["grn_next_dispatch_no"] = int(s.get("grn_next_dispatch_no", 1) or 1)
            if s["grn_next_dispatch_no"] < 1:
                s["grn_next_dispatch_no"] = 1
        except (TypeError, ValueError):
            s["grn_next_dispatch_no"] = 1


    def _save_config(self):
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f, indent=4)
        self._set_status("Settings saved.")

    def _ensure_config_file(self):
        """Write config.json on a brand-new installation.

        Without this the file only appeared after the user pressed "Save All
        Settings", so the answers given in the first-time engine setup (and any
        auto-ingest switch flipped on the first run) were lost on exit.
        """
        try:
            if self.config_path.exists():
                return
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, indent=4)
            logging.info(f"Created a new configuration file: {self.config_path}")
        except Exception as e:
            logging.warning(f"Could not create the configuration file: {e}")

    def _resolve_base(self):
        base = self.cfg.get("folders", {}).get("base", ".")
        cd = self.config_path.parent.resolve()
        if not base or str(base).strip() in (".", "./", ".\\"):
            return str(cd)
        p = Path(base)
        return str(p) if p.is_absolute() else str(cd / base)

    def _resolve_dirs(self):
        base = self._resolve_base()
        f = self.cfg.get("folders", {})
        return {
            "base":      base,
            "scanned":   os.path.join(base, f.get("scanned",   "SCANNED")),
            "processed": os.path.join(base, f.get("processed", "PROCESSED")),
            "archive":   os.path.join(base, f.get("archive",   "ARCHIVE")),
            "failed":    os.path.join(base, f.get("failed",    "FAILED")),
            "logs":      os.path.join(base, f.get("logs",      "LOGS")),
        }

    def _ensure_dirs(self):
        for k in ("scanned", "processed", "archive", "failed", "logs"):
            os.makedirs(self.dirs[k], exist_ok=True)

    def _configure_tesseract(self):
        cmd = self.cfg.get("tesseract_cmd")
        if cmd and os.path.isfile(cmd):
            pytesseract.pytesseract.tesseract_cmd = cmd
            return
        else:
            # Try common install locations as fallback (non-Windows or alternate paths)
            candidates = [
                "tesseract",
                "/usr/bin/tesseract",
                "/usr/local/bin/tesseract",
                r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            ]
            # Per-user installs (winget/choco sometimes install here instead of Program Files)
            localappdata = os.environ.get("LOCALAPPDATA")
            if localappdata:
                candidates.append(os.path.join(localappdata, "Programs", "Tesseract-OCR", "tesseract.exe"))
            for candidate in candidates:
                try:
                    import shutil as _sh
                    found = _sh.which(candidate) if candidate == "tesseract" else (
                        candidate if os.path.isfile(candidate) else None
                    )
                    if found:
                        pytesseract.pytesseract.tesseract_cmd = found
                        logging.info(f"Tesseract configured from fallback: {found}")
                        return
                except Exception:
                    continue
            logging.warning(
                "Tesseract binary not found in config or common paths. OCR will fail "
                "until Tesseract is installed. Download it from "
                "https://github.com/UB-Mannheim/tesseract/wiki, then either add its "
                "install folder to your system PATH, or set the full path to "
                "tesseract.exe in config.json under \"tesseract_cmd\" "
                "(e.g. \"C:\\\\Program Files\\\\Tesseract-OCR\\\\tesseract.exe\")."
            )

    def _setup_logging(self):
        """Configure persistent timestamped logs.

        Policy:
        - Every application/action log is written with an exact timestamp.
        - Routine successful OCR engine chatter is NOT written at INFO level.
        - OCR failures/warnings/errors ARE retained so failed documents can be
          diagnosed.
        - The normal application log remains the single chronological history.
        """
        os.makedirs(self.dirs["logs"], exist_ok=True)

        class _HubLogFilter(logging.Filter):
            _OCR_INFO_MARKERS = (
                "OCR FAILED", "OCR error", "OCR.space error",
                "OCRSpace error", "OCR fallback failed", "OCR extraction failed",
            )

            def filter(self, record):
                # Never hide warnings/errors: failed OCR must remain visible.
                if record.levelno >= logging.WARNING:
                    return True
                msg = record.getMessage()
                upper = msg.upper()
                # Suppress verbose successful/local OCR engine chatter only.
                # Higher-level PROCESS/ACTION messages are kept separately by
                # the normal logging calls and AI Extract action log.
                if "OCR" in upper:
                    return any(marker.upper() in upper for marker in self._OCR_INFO_MARKERS)
                return True

        log_path = os.path.join(self.dirs["logs"], "maafushivaru_hub.log")
        root = logging.getLogger()
        root.setLevel(logging.INFO)

        # Avoid duplicate handlers if the application is reloaded in-process.
        target = os.path.abspath(log_path)
        for h in list(root.handlers):
            if isinstance(h, logging.FileHandler) and os.path.abspath(getattr(h, "baseFilename", "")) == target:
                h.setLevel(logging.INFO)
                h.setFormatter(logging.Formatter(
                    "%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                ))
                h.addFilter(_HubLogFilter())
                logging.info("Application started.")
                return

        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        handler.addFilter(_HubLogFilter())
        root.addHandler(handler)

        logging.info("Application started.")

    # ------------------------------------------------------------------
    # WATCHDOG AUTO-INGEST
    # ------------------------------------------------------------------
    def _start_watcher(self):
        if self._active_ingest_mode() == "off":
            return
        if not WATCHDOG_AVAILABLE:
            messagebox.showwarning(
                "Watchdog Not Installed",
                "Auto-ingest requires the watchdog library.\n\nInstall it with:\n  pip install watchdog"
            )
            self._watcher_offline_var.set(False)
            self._watcher_api_var.set(False)
            return

        scanned = self.dirs.get("scanned", "")
        if not os.path.isdir(scanned):
            messagebox.showerror("Watcher Error", f"SCANNED folder not found:\n{scanned}")
            self._watcher_offline_var.set(False)
            self._watcher_api_var.set(False)
            return

        if self._watcher_observer and self._watcher_observer.is_alive():
            return  # already running

        def on_new_pdf(path: str):
            logging.info(f"[WATCHER] New PDF detected: {path}")
            self._watcher_queue.put(path)

        self._watcher_handler = ScannedFolderHandler(on_new_pdf=on_new_pdf, debounce_seconds=3.0)
        adapter = _WatchdogEventAdapter(self._watcher_handler)

        self._watcher_observer = Observer()
        self._watcher_observer.schedule(adapter, scanned, recursive=False)
        self._watcher_observer.start()

        self._watcher_badge_var.set(f"● AUTO-INGEST: {self._active_ingest_mode().upper()}")
        self._set_status(
            f"Auto-ingest ({self._active_ingest_mode()}) watching: {scanned}"
        )
        logging.info(
            f"[WATCHER] Started watching {scanned} in {self._active_ingest_mode()} mode"
        )

    def _stop_watcher(self):
        if self._watcher_observer:
            try:
                self._watcher_observer.stop()
                self._watcher_observer.join(timeout=3)
            except Exception as e:
                logging.warning(f"[WATCHER] Stop error: {e}")
            self._watcher_observer = None
        if self._watcher_handler:
            try:
                self._watcher_handler.shutdown()
            except Exception as e:
                logging.warning(f"[WATCHER] Handler shutdown error: {e}")
            self._watcher_handler = None
        self._watcher_badge_var.set("")
        self._set_status("Auto-ingest stopped.")

    def _active_ingest_mode(self) -> str:
        """Return the currently selected auto-ingest mode: 'api', 'offline' or 'off'."""
        if not self._auto_ingest_enabled_var.get():
            return "off"
        if self._watcher_api_var.get():
            return "api"
        if self._watcher_offline_var.get():
            return "offline"
        return "off"

    def _grn_engine_description(self) -> str:
        """Short human description of the engine the next run will use."""
        mode = self._grn_engine_var.get()
        if mode == GRN_ONLINE:
            num = int(self._online_engine_var.get() or 2)
            name = {1: "Engine 1 (Default)", 2: "Engine 2 (Enhanced)",
                    3: "Engine 3 (Extra Accurate)"}.get(num, f"Engine {num}")
            return f"ONLINE · OCR.space {name}"
        return f"OFFLINE · {local_engine_label(self._local_engine_var.get())}"

    def _pending_ingest_files(self) -> List[str]:
        """Every PDF waiting to be auto-ingested, in strict scan order.

        The folder watcher only signals THAT something changed; the work list
        is always rebuilt from the SCANNED folder itself. Sorting by the scan
        number (SCAN_0001, SCAN_0002, ...) is what guarantees serial numbering:
        even if the scanner drops several files at once, or the application is
        opened with a backlog already sitting in SCANNED, SCAN_0001 is always
        processed before SCAN_0002.
        """
        try:
            pending = self._get_pdf_files_strict_order(self.dirs.get("scanned", ""))
        except Exception as e:
            logging.warning(f"[WATCHER] Could not list SCANNED: {e}")
            return []
        # Never auto-ingest a file that is already in the results table (the
        # user may have reviewed it) — but DO pick up the rest of the backlog.
        try:
            done = {
                os.path.basename(r.get("raw_path", "") or r.get("file", ""))
                for r in getattr(self, "_aix_results", [])
            }
        except Exception:
            done = set()
        return [p for p in pending if os.path.basename(p) not in done]

    def _next_serial_scan(self) -> str:
        """Describe the next scan to be processed, e.g. "SCAN_0003"."""
        try:
            nxt = int(self.cfg.get("app_settings", {}).get("serial_scan_next", 1) or 1)
        except (TypeError, ValueError):
            nxt = 1
        return f"SCAN_{max(1, nxt):04d}"

    def _advance_serial_scan(self, count: int = 1) -> None:
        """Remember how many scans have been auto-ingested so the next run
        continues the numbering (SCAN_0001, SCAN_0002, ...) instead of
        restarting at 1."""
        if count <= 0:
            return
        s = self.cfg.setdefault("app_settings", {})
        try:
            current = int(s.get("serial_scan_next", 1) or 1)
        except (TypeError, ValueError):
            current = 1
        s["serial_scan_next"] = current + count
        try:
            self._save_config()
        except Exception as e:
            logging.debug(f"[WATCHER] Could not persist serial scan counter: {e}")

    def _poll_watcher_queue(self):
        """Drain the watcher signals, then hand the queue to the active engine.

        OFFLINE -> the local engine selected in Settings (Tesseract/PaddleOCR/EasyOCR)
        ONLINE  -> OCR.space API with the selected API engine (1/2/3)

        The whole backlog is processed as ONE serial batch, so the numbering
        runs SCAN_0001, SCAN_0002, ... within the batch and continues from
        where it stopped on the next run.
        """
        changed = False
        try:
            while True:
                pdf_path = self._watcher_queue.get_nowait()
                changed = True
                if pdf_path and not os.path.exists(pdf_path):
                    logging.info(f"[WATCHER] Ignoring removed file: {pdf_path}")
        except queue.Empty:
            pass

        mode = self._active_ingest_mode()
        busy = (self._worker_running or self._dispatch_running
                or getattr(self, "_aix_running", False))

        if mode != "off" and not busy:
            pending = self._pending_ingest_files()
            if pending:
                names = [os.path.basename(p) for p in pending]
                first = names[0]
                logging.info(
                    "[WATCHER] %s%s | auto-ingest %d file(s) in order: %s",
                    "Change detected: " if changed else "Startup backlog: ",
                    mode, len(pending), names,
                )
                self._set_status(
                    f"[AUTO-INGEST {mode.upper()}] Processing {len(pending)} scan(s) in order "
                    f"— starting with {first}"
                    + (f" (next: {self._next_serial_scan()})" if pending else ""),
                    ACCENT,
                )
                self.after(200, self._auto_ingest_grn_dispatch)
            elif changed:
                logging.info("[WATCHER] Change detected but SCANNED has no new PDFs.")

        self.after(1500, self._poll_watcher_queue)

    def _auto_ingest_grn_dispatch(self):
        """Run the selected GRN Dispatch engine for the next queued batch.

        Online (OCR.space) and Offline (Tesseract) both feed the same review
        table and field-extraction logic. The worker processes its snapshot
        serially; PDFs arriving during a run remain queued for the next run.
        """
        if getattr(self, "_aix_running", False) or self._worker_running or self._dispatch_running:
            return
        try:
            from aiextracttab import _aix_start_process, _aix_log, _aix_maybe_prompt_sheet
        except Exception as e:
            logging.error(f"[AUTO-INGEST] Could not import GRN Dispatch functions: {e}", exc_info=True)
            self._set_status(f"[AUTO-INGEST] GRN Dispatch unavailable: {e}", ERROR)
            return

        engine_desc = self._grn_engine_description()
        pending = self._pending_ingest_files()
        if pending:
            first_no = self._next_serial_scan()
            _aix_log(
                self, "SYSTEM",
                f"Auto-ingest ({engine_desc}) triggered — {len(pending)} file(s), "
                f"serial order from {first_no}: "
                f"{[os.path.basename(p) for p in pending]}."
            )
        else:
            _aix_log(self, "SYSTEM", f"Auto-ingest ({engine_desc}) triggered by folder watcher.")

        # Results accumulate in the visible GRN Dispatch table until the user
        # reviews them and selects Process All, matching the manual workflow.
        # The completion hook reports how the batch went and advances the
        # serial scan counter so the next opened batch continues the numbering.
        batch_size = len(pending)

        def _on_complete():
            try:
                self._advance_serial_scan(batch_size)
            except Exception as e:
                logging.warning(f"[AUTO-INGEST] serial counter update failed: {e}")
            _aix_maybe_prompt_sheet(self)

        self._aix_on_complete = _on_complete
        _aix_start_process(self, auto=True)

    # Backward-compatible name used by older launches and integrations.
    def _auto_ingest_api(self):
        self._auto_ingest_grn_dispatch()

    def _on_grn_engine_changed(self, *_):
        """Persist the visible engine choice and update related UI safely.

        Keeps the OFFLINE (local engine) and ONLINE (OCR.space engine number)
        sub-choices in sync, mirrors them into the legacy watcher booleans, and
        warns when the selected engine is not installed on this machine.
        """
        selected = self._grn_engine_var.get()
        if selected not in (GRN_ONLINE, GRN_OFFLINE):
            selected = GRN_OFFLINE
            self._grn_engine_var.set(selected)
        enabled = self._auto_ingest_enabled_var.get()
        self._watcher_offline_var.set(enabled and selected == GRN_OFFLINE)
        self._watcher_api_var.set(enabled and selected == GRN_ONLINE)
        self._persist_ingest_modes()
        self._refresh_processing_mode_badge()
        self._apply_watcher_state()
        self._warn_if_engine_missing()

    def _warn_if_engine_missing(self, show_dialog: bool = True):
        """Report clearly when the selected engine cannot run on this PC."""
        missing, message = self._selected_engine_problem()
        if not missing:
            return False
        self._set_status(message, WARNING)
        if show_dialog:
            messagebox.showwarning("OCR Engine Not Found", message)
        return True

    def _selected_engine_problem(self):
        """Return (is_problem, message) for the currently selected engine."""
        mode = self._grn_engine_var.get()
        if mode == GRN_ONLINE:
            if not AI_MATCHER_AVAILABLE:
                return True, (
                    "The ONLINE engine (OCR.space) is not available: "
                    "ai_supplier_matcher.py could not be loaded. "
                    "Select an OFFLINE engine or restore that file."
                )
            return False, ""
        key = self._local_engine_var.get()
        if not local_engine_installed(key):
            label = local_engine_label(key)
            return True, (
                f"OCR engine not found: {label} is not installed on this computer.\n\n"
                f"Open Settings → OCR Engines and press “Install” next to {label}, "
                f"or select a different engine."
            )
        return False, ""

    def _on_auto_ingest_toggle(self):
        enabled = self._auto_ingest_enabled_var.get()
        selected = self._grn_engine_var.get()
        self._watcher_offline_var.set(enabled and selected == "offline")
        self._watcher_api_var.set(enabled and selected == "online")
        self._persist_ingest_modes()
        self._apply_watcher_state()

    def _on_watcher_offline_toggle(self):
        enabled = self._watcher_offline_var.get()
        if enabled:
            self._grn_engine_var.set("offline")
            self._auto_ingest_enabled_var.set(True)
            self._watcher_api_var.set(False)
        elif not self._watcher_api_var.get():
            self._auto_ingest_enabled_var.set(False)
        self._persist_ingest_modes()
        self._refresh_processing_mode_badge()
        self._apply_watcher_state()

    def _on_watcher_api_toggle(self):
        enabled = self._watcher_api_var.get()
        if enabled:
            self._grn_engine_var.set("online")
            self._auto_ingest_enabled_var.set(True)
            self._watcher_offline_var.set(False)
        elif not self._watcher_offline_var.get():
            self._auto_ingest_enabled_var.set(False)
        self._persist_ingest_modes()
        self._refresh_processing_mode_badge()
        self._apply_watcher_state()

    def _persist_ingest_modes(self):
        s = self.cfg.setdefault("app_settings", {})
        engine = self._grn_engine_var.get()
        enabled = bool(self._auto_ingest_enabled_var.get())
        s["grn_processing_engine"] = engine
        s["auto_ingest_enabled"] = enabled
        s["auto_ingest_offline"] = enabled and engine == GRN_OFFLINE
        s["auto_ingest_api"] = enabled and engine == GRN_ONLINE
        # Concrete engine inside OFFLINE mode. `_engine_var` is the Settings
        # radio selection; mirror it into `_local_engine_var` so the saved
        # configuration and the live GRN pipeline cannot disagree.
        local_engine = (
            self._engine_var.get()
            if hasattr(self, "_engine_var")
            else self._local_engine_var.get()
        )
        self._local_engine_var.set(local_engine)
        s["local_ocr_engine"] = local_engine
        s["ocr_engine"] = local_engine
        try:
            s["online_ocr_engine"] = int(self._online_engine_var.get())
        except (TypeError, ValueError):
            s["online_ocr_engine"] = 2
        self.cfg.setdefault("ocr_space", {})["OCREngine"] = s["online_ocr_engine"]
        # Keep legacy keys in sync so older code/exports still work.
        s["auto_ingest_watcher"] = enabled
        self._save_config()

    def _apply_watcher_state(self):
        """Start or stop the folder observer based on the active mode."""
        if self._active_ingest_mode() != "off":
            if self._watcher_observer and self._watcher_observer.is_alive():
                # Already running — just refresh the badge to the new mode.
                self._watcher_badge_var.set(f"● AUTO-INGEST: {self._active_ingest_mode().upper()}")
                self._set_status(
                    f"Auto-ingest mode set to {self._active_ingest_mode()}.", ACCENT
                )
            else:
                self._start_watcher()
        else:
            self._stop_watcher()

    # Backward-compat alias (kept in case other code references the old name).
    def _on_watcher_toggle(self):
        self._apply_watcher_state()

    # ------------------------------------------------------------------
    # QUEUE POLLING
    # ------------------------------------------------------------------
    def _poll_rename_queue(self):
        try:
            while True:
                item = self._rename_queue.get_nowait()
                if item is None:
                    self._worker_running = False
                    self._set_buttons_state("normal")
                    self._refresh_dashboard_stats()
                    return
                kind, data = item
                if kind == "row":
                    self._add_rename_tree_row(data)
                elif kind == "progress":
                    self._progress.configure(value=data)
                elif kind == "status":
                    self._status_var.set(data)
        except queue.Empty:
            pass
        if self._worker_running:
            self.after(60, self._poll_rename_queue)

    def _poll_dispatch_queue(self):
        try:
            while True:
                item = self._dispatch_queue.get_nowait()
                if item is None:
                    self._dispatch_running = False
                    self._set_buttons_state("normal")
                    return
                kind, data = item
                if kind == "row":
                    self._add_dispatch_tree_row(data)
                elif kind == "progress":
                    self._dispatch_progress.configure(value=data)
                elif kind == "status":
                    self._status_var.set(data)
        except queue.Empty:
            pass
        if self._dispatch_running:
            self.after(60, self._poll_dispatch_queue)

    # ------------------------------------------------------------------
    # BUTTON STATE MANAGEMENT
    # ------------------------------------------------------------------
    def _set_buttons_state(self, state: str):
        """Enable or disable process-trigger buttons."""
        btns = getattr(self, "_process_buttons", [])
        for btn in btns:
            try:
                btn.configure(state=state)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # STYLE
    # ------------------------------------------------------------------
    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        # Base
        style.configure("TFrame",         background=BG)
        style.configure("Panel.TFrame",   background=PANEL)
        style.configure("Panel2.TFrame",  background=PANEL2)

        # Labels
        style.configure("TLabel",        background=BG,    foreground=TEXT,  font=("Segoe UI", 10))
        style.configure("Muted.TLabel",  background=BG,    foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Title.TLabel",  background=BG,    foreground=TEXT,  font=("Segoe UI", 22, "bold"))
        style.configure("Subtitle.TLabel", background=BG,  foreground=MUTED, font=("Segoe UI", 10))
        style.configure("Panel.TLabel",  background=PANEL, foreground=TEXT,  font=("Segoe UI", 10))
        style.configure("Panel2.TLabel", background=PANEL2,foreground=TEXT,  font=("Segoe UI", 10))
        style.configure("CardTitle.TLabel", background=PANEL, foreground=MUTED,  font=("Segoe UI", 9,  "bold"))
        style.configure("CardValue.TLabel", background=PANEL, foreground=TEXT,   font=("Segoe UI", 30, "bold"))
        style.configure("Author.TLabel",    background=BG,    foreground=ACCENT, font=("Segoe UI", 13, "bold"))
        style.configure("SectionHead.TLabel", background=PANEL, foreground=TEXT, font=("Segoe UI", 12, "bold"))

        # Standard button
        style.configure(
            "TButton", font=("Segoe UI", 9, "bold"), padding=(12, 7),
            background=PANEL2, foreground=TEXT, borderwidth=0, focuscolor="none",
            relief="flat",
        )
        style.map("TButton",
            background=[("active", PANEL3), ("disabled", PANEL)],
            foreground=[("disabled", MUTED)],
        )

        # Accent button
        style.configure(
            "Accent.TButton", font=("Segoe UI", 9, "bold"), padding=(14, 6),
            background=ACCENT, foreground="white", borderwidth=0,
        )
        style.map("Accent.TButton", background=[("active", ACCENT_H), ("disabled", PANEL3)])

        # Success button
        style.configure(
            "Success.TButton", font=("Segoe UI", 9, "bold"), padding=(12, 7),
            background=SUCCESS, foreground="white", borderwidth=0,
        )
        style.map("Success.TButton", background=[("active", "#059669")])

        # Warning button
        style.configure(
            "Warning.TButton", font=("Segoe UI", 9, "bold"), padding=(12, 7),
            background=WARNING, foreground="#1a1a1a", borderwidth=0,
        )
        style.map("Warning.TButton", background=[("active", "#D97706")])

        # Notebook
        style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=[12, 8, 0, 0])
        style.configure(
            "TNotebook.Tab", padding=(22, 10), font=("Segoe UI", 10, "bold"),
            background=PANEL2, foreground=MUTED, borderwidth=0, focuscolor="none",
        )
        style.map("TNotebook.Tab",
            background=[("selected", ACCENT)],
            foreground=[("selected", "white")],
        )

        # Treeview
        style.configure(
            "Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT,
            rowheight=32, borderwidth=0, font=("Segoe UI", 9),
        )
        style.map("Treeview",
            background=[("selected", ACCENT)],
            foreground=[("selected", "white")],
        )
        style.configure(
            "Treeview.Heading", background=PANEL2, foreground=MUTED,
            font=("Segoe UI", 9, "bold"), borderwidth=0, padding=(0, 7),
        )
        style.map("Treeview.Heading", background=[("active", PANEL3)])

        # Progressbar
        style.configure(
            "Horizontal.TProgressbar", background=ACCENT,
            troughcolor=PANEL2, borderwidth=0, thickness=6,
        )

        # Scrollbar
        style.configure("TScrollbar", background=PANEL2, troughcolor=PANEL, borderwidth=0, arrowcolor=MUTED)
        style.map("TScrollbar", background=[("active", PANEL3)])

        # Radiobutton / Checkbutton
        style.configure("TRadiobutton", background=PANEL, foreground=TEXT, font=("Segoe UI", 10), focuscolor="none")
        style.configure("TCheckbutton", background=PANEL, foreground=TEXT, font=("Segoe UI", 10), focuscolor="none")
        style.map("TRadiobutton", background=[("active", PANEL2)])
        style.map("TCheckbutton", background=[("active", PANEL2)])

    # ------------------------------------------------------------------
    # LAYOUT
    # ------------------------------------------------------------------
    def _build_layout(self):
        self._build_header()

        # Build status bar FIRST so it anchors to the bottom before the notebook
        self._build_status_bar()

        # Notebook fills all remaining space between header and status bar
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 0))

        # Visible tab order: Dashboard -> Scan -> GRN Dispatch -> Settings ->
        # About. The former OCR Renamer and GRN Dispatch views are built and
        # hidden because their internal data structures are retained for
        # compatibility with the unified screen.
        self._build_dashboard_tab()
        try:
            from scan_tab import add_scan_tab
            add_scan_tab(self)
        except Exception as e:
            logging.error(f"Scan tab failed to load: {e}")
        self._build_renamer_tab()
        self._build_dispatch_tab()
        try:
            from aiextracttab import add_ai_extract_tab
            add_ai_extract_tab(self)
        except Exception as e:
            logging.error(f"GRN Dispatch tab failed to load: {e}", exc_info=True)
            messagebox.showerror("GRN Dispatch Tab Load Error", str(e))
        for legacy_frame in (
            getattr(self, "_legacy_renamer_tab", None),
            getattr(self, "_legacy_dispatch_tab", None),
        ):
            if legacy_frame is not None:
                try:
                    self.notebook.hide(legacy_frame)
                except tk.TclError:
                    pass
        self._build_settings_tab()
        self._build_about_tab()

        self._process_buttons = [
            getattr(self, "_aix_btn_process", None),
        ]
        self._process_buttons = [b for b in self._process_buttons if b]

    def _build_header(self):
        header = tk.Frame(self, bg=PANEL2, height=64)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        left = tk.Frame(header, bg=PANEL2)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(20, 0))

        lp = resource_path("logo.png")
        if lp.exists():
            try:
                img = Image.open(lp)
                img.thumbnail((44, 44))
                self._header_logo_img = ImageTk.PhotoImage(img)
                tk.Label(left, image=self._header_logo_img, bg=PANEL2).pack(side=tk.LEFT, padx=(0, 14), pady=10)
            except Exception:
                pass

        tb = tk.Frame(left, bg=PANEL2)
        tb.pack(side=tk.LEFT, fill=tk.Y)

        tk.Label(
            tb,
            text="OUTRIGGER MAAFUSHIVARU",
            bg=PANEL2,
            fg=TEXT,
            font=("Segoe UI", 15, "bold"),
        ).pack(anchor="w", pady=(12, 0))

        tk.Label(
            tb,
            text=f"Document Processing Hub  ·  {self.APP_VERSION}",
            bg=PANEL2,
            fg=MUTED,
            font=("Segoe UI", 9),
        ).pack(anchor="w")

        right = tk.Frame(header, bg=PANEL2)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=20)

        self._watcher_badge_lbl = tk.Label(
            right,
            textvariable=self._watcher_badge_var,
            bg=PANEL2,
            fg=WATCHER,
            font=("Segoe UI", 9, "bold"),
        )
        self._watcher_badge_lbl.pack(side=tk.RIGHT, padx=(12, 0), pady=20)

        self._processing_mode_badge_lbl = tk.Label(
            right,
            textvariable=self._processing_mode_badge_var,
            bg=PANEL2,
            fg=SUCCESS,
            font=("Segoe UI", 9, "bold"),
        )
        self._processing_mode_badge_lbl.pack(side=tk.RIGHT, padx=(12, 0), pady=20)

        tk.Label(
            right,
            textvariable=self._engine_badge_var,
            bg=PANEL2,
            fg=ACCENT,
            font=("Segoe UI", 9, "bold"),
        ).pack(side=tk.RIGHT, pady=20)

    def _build_status_bar(self):
        bar = tk.Frame(self, bg=PANEL2, height=44)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)

        self._status_indicator = tk.Canvas(bar, width=10, height=10, bg=PANEL2, highlightthickness=0)
        self._status_indicator.pack(side=tk.LEFT, padx=(14, 6), pady=13)
        self._status_dot = self._status_indicator.create_oval(1, 1, 9, 9, fill=SUCCESS, outline="")

        tk.Label(bar, textvariable=self._status_var, bg=PANEL2, fg=TEXT,
                 font=("Segoe UI", 9), anchor="w").pack(side=tk.LEFT, pady=8)

    def _refresh_engine_badge(self):
        """Header badge: shows the engine the pipeline will actually use."""
        eng = self.cfg.get("app_settings", {}).get("local_ocr_engine") or \
            self.cfg.get("app_settings", {}).get("ocr_engine", "tesseract")
        self._engine_badge_var.set(f"OCR: {local_engine_label(eng)}")

    def _refresh_processing_mode_badge(self):
        """Refresh the always-visible ONLINE/OFFLINE status in the header."""
        engine_var = getattr(self, "_grn_engine_var", None)
        selected = engine_var.get() if engine_var is not None else self.cfg.get(
            "app_settings", {}
        ).get("grn_processing_engine", GRN_OFFLINE)
        online = selected == GRN_ONLINE
        detail = self._grn_engine_description() if hasattr(self, "_grn_engine_var") else ""
        if online:
            num = int(getattr(self, "_online_engine_var", tk.IntVar(value=2)).get() or 2)
            text = f"● ONLINE — OCR.space Engine {num}"
        else:
            key = getattr(self, "_local_engine_var", tk.StringVar(value="tesseract")).get()
            text = f"● OFFLINE — {local_engine_label(key)}"
        self._processing_mode_badge_var.set(text)
        if hasattr(self, "_processing_mode_badge_lbl"):
            self._processing_mode_badge_lbl.configure(fg=ACCENT if online else SUCCESS)
        if hasattr(self, "_aix_mode_badge_var"):
            self._aix_mode_badge_var.set(detail or ("ONLINE · OCR.space API" if online
                                                    else "OFFLINE · Local Tesseract"))

    # ------------------------------------------------------------------
    # TAB HELPERS
    # ------------------------------------------------------------------
    def _make_tab(self, title):
        """Non-scrollable tab (for Renamer/Dispatch which have internal trees)."""
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text=f"  {title}  ")
        return frame

    def _make_scrollable_tab(self, title):
        outer = ttk.Frame(self.notebook)
        self.notebook.add(outer, text=f"  {title}  ")
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        wid = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_configure(_):
            canvas.configure(scrollregion=canvas.bbox("all"))
        inner.bind("<Configure>", _on_configure)

        def _on_canvas_configure(e):
            canvas.itemconfigure(wid, width=e.width)
        canvas.bind("<Configure>", _on_canvas_configure)
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        # Tag this tab's canvas so the single global router below can find
        # it, however deep in the widget tree the mouse cursor actually is.
        outer._scroll_canvas = canvas

        # ------------------------------------------------------------------
        # FIX: scrolling used to be wired up via <<NotebookTabChanged>>,
        # which never fires for whichever tab is selected by DEFAULT when
        # the app starts (no tab-change actually happens on first launch).
        # Since Dashboard is built first and lands as the initially-selected
        # tab, that meant its content was silently unscrollable — nothing
        # below the first screenful (System Directories, GRN Dispatch #,
        # etc.) was reachable — until you clicked away to another tab and
        # back once. A single app-wide wheel handler that looks up whichever
        # tab is ACTUALLY selected at scroll time sidesteps the timing issue
        # entirely and needs to be installed only once, no matter how many
        # scrollable tabs get built.
        # ------------------------------------------------------------------
        if not getattr(self, "_wheel_router_installed", False):
            self._wheel_router_installed = True

            def _route_wheel(delta_units):
                try:
                    sel = self.notebook.nametowidget(self.notebook.select())
                except Exception:
                    return
                target = getattr(sel, "_scroll_canvas", None)
                if target is not None:
                    target.yview_scroll(delta_units, "units")

            def _on_mousewheel(ev):
                if getattr(ev, "delta", 0):
                    _route_wheel(int(-1 * (ev.delta / 120)))
                return "break"

            def _on_linux_up(_ev):
                _route_wheel(-1)
                return "break"

            def _on_linux_down(_ev):
                _route_wheel(1)
                return "break"

            self.bind_all("<MouseWheel>", _on_mousewheel, add="+")
            self.bind_all("<Button-4>", _on_linux_up, add="+")
            self.bind_all("<Button-5>", _on_linux_down, add="+")

        return inner

    def _section(self, parent, title, subtitle=""):
        box = tk.Frame(parent, bg=PANEL, bd=0)
        box.pack(fill=tk.X, padx=16, pady=(0, 14))
        hdr = tk.Frame(box, bg=PANEL)
        hdr.pack(fill=tk.X, padx=20, pady=(16, 0))
        tk.Label(hdr, text=title, bg=PANEL, fg=TEXT, font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)
        if subtitle:
            tk.Label(hdr, text=f"  —  {subtitle}", bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(side=tk.LEFT)
        # Thin separator line
        sep = tk.Frame(box, bg=PANEL3, height=1)
        sep.pack(fill=tk.X, padx=20, pady=(8, 12))
        return box

    def _make_stat_card(self, parent, title, value, badge, color):
        card = tk.Frame(parent, bg=PANEL, bd=0)
        card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=6, pady=4)
        # Top accent bar
        accent_bar = tk.Frame(card, bg=color, height=3)
        accent_bar.pack(fill=tk.X)

        inner = tk.Frame(card, bg=PANEL)
        inner.pack(fill=tk.BOTH, expand=True, padx=18, pady=14)

        top = tk.Frame(inner, bg=PANEL)
        top.pack(fill=tk.X)
        tk.Label(top, text=badge, bg=PANEL, fg=color, font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)

        tk.Label(inner, text=title, bg=PANEL, fg=MUTED, font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(6, 0))
        var = tk.StringVar(value=str(value))
        tk.Label(inner, textvariable=var, bg=PANEL, fg=TEXT, font=("Segoe UI", 28, "bold")).pack(anchor="w")
        return var

    # ------------------------------------------------------------------
    # SEARCH BAR HELPER (fixed mousewheel)
    # ------------------------------------------------------------------
    def _make_search_bar(self, parent, tree_ref_getter, all_rows_ref_getter):
        bar = tk.Frame(parent, bg=PANEL)
        bar.pack(fill=tk.X, padx=20, pady=(4, 8))

        tk.Label(bar, text="Search:", bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 6))
        var = tk.StringVar()
        ent = tk.Entry(
            bar, textvariable=var, bg=PANEL2, fg=TEXT, insertbackground=TEXT,
            relief="flat", font=("Segoe UI", 10), bd=0,
            highlightthickness=1, highlightbackground=PANEL3, highlightcolor=ACCENT,
        )
        ent.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=5)

        def _clear():
            var.set("")
        ttk.Button(bar, text="✕", command=_clear, width=3).pack(side=tk.LEFT, padx=(6, 0))

        def _on_search(*_):
            tree = tree_ref_getter()
            q = var.get().strip().lower()
            for iid in tree.get_children():
                tree.detach(iid)
            for iid in all_rows_ref_getter():
                if not q:
                    tree.reattach(iid, "", "end")
                else:
                    vals = tree.item(iid, "values")
                    if any(q in str(v).lower() for v in vals):
                        tree.reattach(iid, "", "end")

        var.trace_add("write", _on_search)
        return var

    # ------------------------------------------------------------------
    # BIND MOUSEWHEEL TO TREE (FIX)
    # The key fix: we bind directly to the tree widget so scrolling works
    # when the cursor is inside it, regardless of outer canvas bindings.
    # ------------------------------------------------------------------
    def _bind_tree_mousewheel(self, tree: ttk.Treeview):
        def _on_mw(event):
            tree.yview_scroll(int(-1 * (event.delta / 120)), "units")
            return "break"

        tree.bind("<MouseWheel>", _on_mw)
        tree.bind("<Button-4>",  lambda e: tree.yview_scroll(-1, "units") or "break")   # Linux
        tree.bind("<Button-5>",  lambda e: tree.yview_scroll( 1, "units") or "break")   # Linux

    # ------------------------------------------------------------------
    # DASHBOARD TAB
    # ------------------------------------------------------------------
    def _build_dashboard_tab(self):
        frame = self._make_scrollable_tab("Dashboard")
        frame.pack_configure(padx=0)

        # Stats row
        sr = tk.Frame(frame, bg=BG)
        sr.pack(fill=tk.X, padx=16, pady=(20, 0))
        self._stat_waiting_var   = self._make_stat_card(sr, "Waiting",   "-", "SCAN",  WARNING)
        self._stat_processed_var = self._make_stat_card(sr, "Processed", "-", "DONE",  SUCCESS)
        self._stat_archived_var  = self._make_stat_card(sr, "Archived",  "-", "ARCH",  ACCENT2)
        self._stat_failed_var    = self._make_stat_card(sr, "Failed",    "-", "FAIL",  ERROR)

        # ------------------------------------------------------------------
        # Processed PDF Transfer — dedicated card (path picker always visible)
        # ------------------------------------------------------------------
        xfer = self._section(
            frame,
            "Processed PDF Transfer",
            "Choose where Transfer moves finished PDFs from PROCESSED",
        )

        # Path row: buttons packed on the RIGHT first so they never clip off-screen
        path_row = tk.Frame(xfer, bg=PANEL)
        path_row.pack(fill=tk.X, padx=20, pady=(0, 8))

        self._processed_transfer_var = tk.StringVar(
            value=self.cfg.get("app_settings", {}).get("processed_transfer_path", "")
        )

        btn_col = tk.Frame(path_row, bg=PANEL)
        btn_col.pack(side=tk.RIGHT)
        ttk.Button(
            btn_col, text="📂  Set Transfer Path", style="Accent.TButton",
            command=self._browse_transfer_path,
        ).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(btn_col, text="Open", command=self._open_transfer_path).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(btn_col, text="Clear", command=self._clear_transfer_path).pack(side=tk.LEFT, padx=(6, 0))

        left_col = tk.Frame(path_row, bg=PANEL)
        left_col.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        tk.Label(
            left_col, text="Destination folder",
            bg=PANEL, fg=MUTED, font=("Segoe UI", 9, "bold"), anchor="w",
        ).pack(fill=tk.X)
        entry_row = tk.Frame(left_col, bg=PANEL)
        entry_row.pack(fill=tk.X, pady=(4, 0))
        self._processed_transfer_entry = tk.Entry(
            entry_row, textvariable=self._processed_transfer_var,
            bg=PANEL2, fg=TEXT, insertbackground=TEXT, relief="flat",
            font=("Segoe UI", 10), bd=0, highlightthickness=1,
            highlightbackground=PANEL3, highlightcolor=ACCENT,
        )
        self._processed_transfer_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6)
        self._processed_transfer_entry.bind("<FocusOut>", lambda _e: self._save_transfer_path(quiet=True))
        self._processed_transfer_entry.bind("<Return>",   lambda _e: self._save_transfer_path())

        hint = tk.Label(
            xfer,
            text="Tip: click  Set Transfer Path  to pick a folder. Transfer moves every PDF out of PROCESSED into that folder.",
            bg=PANEL, fg=MUTED, font=("Segoe UI", 8), anchor="w", justify="left",
        )
        hint.pack(fill=tk.X, padx=20, pady=(0, 10))

        action_row = tk.Frame(xfer, bg=PANEL)
        action_row.pack(fill=tk.X, padx=20, pady=(0, 16))
        ttk.Button(
            action_row, text="📦  Transfer Processed PDFs Now", style="Success.TButton",
            command=self._transfer_processed_pdfs,
        ).pack(side=tk.LEFT)
        self._transfer_status_var = tk.StringVar(value=self._transfer_path_summary())
        tk.Label(
            action_row, textvariable=self._transfer_status_var,
            bg=PANEL, fg=ACCENT, font=("Segoe UI", 9), anchor="w",
        ).pack(side=tk.LEFT, padx=(14, 0), fill=tk.X, expand=True)

        # Quick actions
        qa = self._section(frame, "Quick Actions")
        row = tk.Frame(qa, bg=PANEL)
        row.pack(fill=tk.X, padx=20, pady=(0, 16))
        ttk.Button(row, text="↻  Refresh Stats",   command=self._refresh_dashboard_stats).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(row, text="📂  Open SCANNED",   command=lambda: self._open_folder(self.dirs["scanned"])).pack(side=tk.LEFT, padx=4)
        ttk.Button(row, text="📂  Open PROCESSED", command=lambda: self._open_folder(self.dirs["processed"])).pack(side=tk.LEFT, padx=4)
        ttk.Button(row, text="📂  Open FAILED",    command=lambda: self._open_folder(self.dirs["failed"])).pack(side=tk.LEFT, padx=4)
        ttk.Button(row, text="📋  Open Logs",      command=lambda: self._open_folder(self.dirs["logs"])).pack(side=tk.LEFT, padx=4)

        # Directories
        info = self._section(frame, "System Directories")
        for k in ("base", "scanned", "processed", "archive", "failed", "logs"):
            r = tk.Frame(info, bg=PANEL)
            r.pack(fill=tk.X, padx=20, pady=3)
            tk.Label(
                r, text=f"{k.upper()}:", bg=PANEL, fg=MUTED,
                font=("Segoe UI", 9, "bold"), width=12, anchor="w",
            ).pack(side=tk.LEFT)
            path_lbl = tk.Label(
                r, text=self.dirs.get(k, ""), bg=PANEL, fg=ACCENT,
                font=("Segoe UI", 9), anchor="w",
            )
            path_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
            ttk.Button(
                r, text="Open", width=6,
                command=lambda p=self.dirs.get(k, ""): self._open_folder(p),
            ).pack(side=tk.RIGHT, padx=(6, 0))

        # --- GRN Dispatch Note: next DISPATCH number (editable + persistent) ---
        gd = tk.Frame(info, bg=PANEL)
        gd.pack(fill=tk.X, padx=20, pady=(12, 3))
        tk.Label(
            gd, text="GRN DISPATCH NEXT NO:", bg=PANEL, fg=MUTED,
            font=("Segoe UI", 9, "bold"), width=22, anchor="w",
        ).pack(side=tk.LEFT)
        self._grn_dispatch_no_var = tk.StringVar(
            value=str(self.cfg.get("app_settings", {}).get("grn_next_dispatch_no", 1))
        )
        _gd_entry = tk.Entry(
            gd, textvariable=self._grn_dispatch_no_var,
            bg=PANEL2, fg=TEXT, insertbackground=TEXT, relief="flat",
            font=("Segoe UI", 9), bd=0, highlightthickness=1,
            highlightbackground=PANEL3, highlightcolor=ACCENT, width=12,
        )
        _gd_entry.pack(side=tk.LEFT, ipady=3, padx=(0, 6))
        _gd_entry.bind("<FocusOut>", lambda _e: self._save_grn_dispatch_no())
        _gd_entry.bind("<Return>",   lambda _e: self._save_grn_dispatch_no())
        ttk.Button(gd, text="Save", command=self._save_grn_dispatch_no).pack(side=tk.LEFT)
        tk.Label(
            gd,
            text='  Starting DISPATCH # for the next "Export Excel" — auto-advances after each export.',
            bg=PANEL, fg=MUTED, font=("Segoe UI", 8),
        ).pack(side=tk.LEFT, padx=(8, 0))

        tk.Frame(info, bg=PANEL).pack(pady=8)

    # ------------------------------------------------------------------
    # PROCESSED PDF TRANSFER
    # ------------------------------------------------------------------
    def _transfer_path_summary(self) -> str:
        raw = ""
        try:
            raw = (self._processed_transfer_var.get() or "").strip()
        except Exception:
            raw = (self.cfg.get("app_settings", {}) or {}).get("processed_transfer_path", "") or ""
        if not raw:
            return "No destination set — click Set Transfer Path"
        resolved = self._resolve_user_path(raw, must_exist=False)
        return f"Ready → {resolved}"

    def _resolve_user_path(self, path: str, must_exist: bool = False) -> str:
        """Resolve a user path (absolute or relative-to-base) to a normalized absolute path."""
        p = (path or "").strip()
        if not p:
            return ""
        p = os.path.expandvars(os.path.expanduser(p))
        if not os.path.isabs(p):
            base = self.dirs.get("base") or os.getcwd()
            p = os.path.join(base, p)
        p = os.path.normpath(p)
        if must_exist and not os.path.isdir(p):
            return ""
        return p

    def _save_transfer_path(self, quiet: bool = False):
        """Persist the Processed PDF Transfer destination to config."""
        path = (self._processed_transfer_var.get() or "").strip()
        # Prefer storing absolute paths so relaunch is stable
        resolved = self._resolve_user_path(path, must_exist=False) if path else ""
        if resolved:
            path = resolved
            try:
                self._processed_transfer_var.set(path)
            except Exception:
                pass
        self.cfg.setdefault("app_settings", {})["processed_transfer_path"] = path
        try:
            # Avoid recursive "Settings saved." noise from _save_config status
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, indent=4)
            if hasattr(self, "_transfer_status_var"):
                self._transfer_status_var.set(self._transfer_path_summary())
            if not quiet:
                self._set_status(f"Transfer path saved: {path or '(empty)'}", SUCCESS)
        except Exception as e:
            logging.error(f"Could not save transfer path: {e}", exc_info=True)
            self._set_status(f"Could not save transfer path: {e}", ERROR)

    def _clear_transfer_path(self):
        self._processed_transfer_var.set("")
        self._save_transfer_path()

    def _open_transfer_path(self):
        dest = self._resolve_user_path(
            (self._processed_transfer_var.get() or "").strip(), must_exist=False
        )
        if not dest:
            messagebox.showinfo(
                "Transfer Path",
                "No transfer destination is set yet.\n\nClick  Set Transfer Path  to choose a folder.",
            )
            return
        try:
            os.makedirs(dest, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Open Transfer Path", f"Could not open/create:\n{dest}\n\n{e}")
            return
        self._open_folder(dest)

    def _save_grn_dispatch_no(self):
        """Persist the starting DISPATCH # for the next GRN Excel export."""
        raw = (self._grn_dispatch_no_var.get() or "").strip()
        try:
            value = int(raw)
            if value < 1:
                raise ValueError("must be >= 1")
        except (TypeError, ValueError):
            messagebox.showwarning("GRN Dispatch Note", "Dispatch # must be a whole number (1 or higher).")
            self._grn_dispatch_no_var.set(str(self.cfg.get("app_settings", {}).get("grn_next_dispatch_no", 1)))
            return
        self.cfg.setdefault("app_settings", {})["grn_next_dispatch_no"] = value
        try:
            self._save_config()
            self._set_status(f"GRN Dispatch next # saved: {value}", SUCCESS)
        except Exception as e:
            logging.error(f"Could not save GRN dispatch no: {e}", exc_info=True)
            self._set_status(f"Could not save GRN dispatch no: {e}", ERROR)

    def _browse_transfer_path(self):
        """Open the OS folder picker to choose the Processed PDF Transfer destination.

        Relative / missing initialdir values break the native Windows dialog, so
        every candidate is resolved to an existing absolute directory first.
        """
        def _resolve_existing(p):
            if not p:
                return None
            p = os.path.expandvars(os.path.expanduser(str(p)))
            if not os.path.isabs(p):
                base = self.dirs.get("base") or os.getcwd()
                p = os.path.join(base, p)
            p = os.path.normpath(p)
            return p if os.path.isdir(p) else None

        candidates = [
            (self._processed_transfer_var.get() or "").strip(),
            self.dirs.get("processed", ""),
            self.dirs.get("base", ""),
            os.path.expanduser("~"),
            os.getcwd(),
        ]
        start = next((r for r in (_resolve_existing(c) for c in candidates) if r), os.path.expanduser("~"))

        try:
            # Parent the dialog to this window; mustexist=False lets user type a new folder name on some OS dialogs
            chosen = filedialog.askdirectory(
                parent=self,
                title="Select Processed PDF Transfer destination",
                initialdir=start,
                mustexist=True,
            )
        except Exception as e:
            logging.error(f"Could not open folder picker: {e}", exc_info=True)
            messagebox.showerror("Set Transfer Path", f"Could not open the folder picker:\n\n{e}")
            return

        if not chosen:
            return

        chosen = os.path.normpath(chosen)
        self._processed_transfer_var.set(chosen)
        self._save_transfer_path()
        self._set_status(f"Transfer destination set: {chosen}", SUCCESS)

    def _transfer_processed_pdfs(self):
        """Move every PDF from PROCESSED to the configured transfer destination."""
        if getattr(self, "_transfer_running", False):
            messagebox.showinfo("Transfer Processed PDFs", "A transfer is already in progress.")
            return

        dest_raw = (self._processed_transfer_var.get() or "").strip()
        if not dest_raw:
            if messagebox.askyesno(
                "Transfer Processed PDFs",
                "No transfer destination is set yet.\n\n"
                "Click Yes to choose a folder now, then transfer.",
            ):
                self._browse_transfer_path()
                dest_raw = (self._processed_transfer_var.get() or "").strip()
            if not dest_raw:
                return

        dest = self._resolve_user_path(dest_raw, must_exist=False)
        src = self.dirs.get("processed", "")
        if not src or not os.path.isdir(src):
            messagebox.showerror("Transfer Processed PDFs", f"PROCESSED folder not found:\n{src}")
            return

        try:
            os.makedirs(dest, exist_ok=True)
        except Exception as e:
            messagebox.showerror(
                "Transfer Processed PDFs",
                f"Could not create destination:\n{dest}\n\n{e}",
            )
            return

        try:
            if os.path.samefile(dest, src):
                messagebox.showwarning(
                    "Transfer Processed PDFs",
                    "Destination is the same as the PROCESSED folder.",
                )
                return
        except OSError:
            if os.path.abspath(dest) == os.path.abspath(src):
                messagebox.showwarning(
                    "Transfer Processed PDFs",
                    "Destination is the same as the PROCESSED folder.",
                )
                return

        try:
            pdfs = sorted(
                f for f in os.listdir(src)
                if f.lower().endswith(".pdf") and os.path.isfile(os.path.join(src, f))
            )
        except Exception as e:
            messagebox.showerror("Transfer Processed PDFs", f"Could not read PROCESSED folder:\n{e}")
            return

        if not pdfs:
            messagebox.showinfo("Transfer Processed PDFs", "No processed PDFs to transfer.")
            return

        if not messagebox.askyesno(
            "Transfer Processed PDFs",
            f"Move {len(pdfs)} processed PDF(s) to:\n\n{dest}\n\nContinue?",
        ):
            return

        # Persist dest in case user typed it without saving
        self._processed_transfer_var.set(dest)
        self._save_transfer_path(quiet=True)

        self._transfer_running = True
        self._set_status(f"Transferring {len(pdfs)} PDF(s)…", WARNING)
        if hasattr(self, "_transfer_status_var"):
            self._transfer_status_var.set(f"Transferring 0/{len(pdfs)}…")

        def _worker():
            moved, failed = 0, 0
            errors = []
            total = len(pdfs)
            for idx, fn in enumerate(pdfs, 1):
                s = os.path.join(src, fn)
                d = os.path.join(dest, fn)
                base_n, ext = os.path.splitext(d)
                cnt = 1
                while os.path.exists(d):
                    d = f"{base_n}_{cnt}{ext}"
                    cnt += 1
                try:
                    _safe_file_move(s, d)
                    moved += 1
                except Exception as e:
                    failed += 1
                    errors.append(f"{fn}: {e}")
                    logging.error(f"Transfer failed for {fn}: {e}", exc_info=True)
                if idx == 1 or idx == total or idx % 5 == 0:
                    def _prog(i=idx, t=total):
                        if hasattr(self, "_transfer_status_var"):
                            self._transfer_status_var.set(f"Transferring {i}/{t}…")
                    self.after(0, _prog)

            def _done():
                self._transfer_running = False
                self._refresh_dashboard_stats()
                if hasattr(self, "_transfer_status_var"):
                    self._transfer_status_var.set(
                        f"Last transfer: {moved} moved, {failed} failed → {dest}"
                    )
                self._set_status(
                    f"Transferred {moved} PDF(s) to {dest} ({failed} failed).",
                    SUCCESS if failed == 0 else WARNING,
                )
                detail = f"Done.\n\nMoved: {moved}\nFailed: {failed}\nDestination:\n{dest}"
                if errors:
                    detail += "\n\nErrors:\n" + "\n".join(errors[:8])
                    if len(errors) > 8:
                        detail += f"\n… and {len(errors) - 8} more (see log)"
                messagebox.showinfo("Transfer Processed PDFs", detail)

            self.after(0, _done)

        threading.Thread(target=_worker, daemon=True, name="pdf-transfer").start()

    def _build_renamer_tab(self):
        frame = self._make_tab("OCR Renamer")
        self._legacy_renamer_tab = frame
        frame.configure(style="TFrame")

        # Top control bar
        ctrl_bar = tk.Frame(frame, bg=PANEL2, height=52)
        ctrl_bar.pack(fill=tk.X)
        ctrl_bar.pack_propagate(False)

        self._btn_start_rename = ttk.Button(
            ctrl_bar, text="▶  Start OCR Rename", style="Accent.TButton",
            command=self._start_rename_worker,
        )
        self._btn_start_rename.pack(side=tk.LEFT, padx=(16, 6), pady=8)

        ttk.Button(ctrl_bar, text="⏹  Cancel",     command=self._cancel_worker).pack(side=tk.LEFT, padx=4, pady=8)
        ttk.Button(ctrl_bar, text="♻  Retry Failed", style="Warning.TButton",
                   command=self._retry_failed_files).pack(side=tk.LEFT, padx=4, pady=8)
        ttk.Button(ctrl_bar, text="🗑  Clear",       command=self._clear_rename_results).pack(side=tk.LEFT, padx=4, pady=8)
        ttk.Button(ctrl_bar, text="🔬  Debug PDF",   command=self._test_single_pdf_debug).pack(side=tk.LEFT, padx=4, pady=8)

        # Dry run toggle on right
        dr_frame = tk.Frame(ctrl_bar, bg=PANEL2)
        dr_frame.pack(side=tk.RIGHT, padx=16, pady=8)
        ttk.Checkbutton(
            dr_frame, text="Dry Run Preview", variable=self._dry_run_var,
            command=self._on_dry_run_toggle, style="TCheckbutton",
        ).pack(side=tk.RIGHT)

        # Progress bar
        prog_bar = tk.Frame(frame, bg=PANEL, height=6)
        prog_bar.pack(fill=tk.X)
        self._progress = ttk.Progressbar(prog_bar, orient="horizontal", mode="determinate")
        self._progress.pack(fill=tk.X)

        # Results label + search
        hdr_bar = tk.Frame(frame, bg=PANEL)
        hdr_bar.pack(fill=tk.X)
        tk.Label(hdr_bar, text="Rename Results", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT, padx=20, pady=(12, 4))
        tk.Label(hdr_bar, text="Double-click Supplier / GRN / Invoice to edit  ·  Double-click File to preview PDF",
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=8, pady=(12, 4))

        search_bar = tk.Frame(frame, bg=PANEL)
        search_bar.pack(fill=tk.X)
        self._make_search_bar(search_bar, lambda: self._rename_tree, lambda: self._rename_all_rows)

        # Tree frame — takes all remaining space
        tf = tk.Frame(frame, bg=PANEL)
        tf.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 16))

        rcols = ("File", "New Name", "Supplier", "Confidence", "GRN", "Invoice #", "Status", "Warning")
        cw = {
            "File": 165, "New Name": 230, "Supplier": 185, "Confidence": 85,
            "GRN": 195, "Invoice #": 125, "Status": 75, "Warning": 210,
        }
        self._rename_tree = ttk.Treeview(tf, columns=rcols, show="headings", height=18)
        for c in rcols:
            self._rename_tree.heading(c, text=c, anchor="center")
            self._rename_tree.column(c, width=cw.get(c, 100), anchor="center", stretch=False)

        yr = ttk.Scrollbar(tf, orient="vertical",   command=self._rename_tree.yview)
        xr = ttk.Scrollbar(tf, orient="horizontal", command=self._rename_tree.xview)
        self._rename_tree.configure(yscrollcommand=yr.set, xscrollcommand=xr.set)
        self._rename_tree.grid(row=0, column=0, sticky="nsew")
        yr.grid(row=0, column=1, sticky="ns")
        xr.grid(row=1, column=0, sticky="ew")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)

        self._bind_tree_mousewheel(self._rename_tree)

        def _rename_pre_edit(col_index, cur):
            if col_index == 4:  # GRN (shifted by Confidence column)
                return "RC-MAM-0000" if cur in ("NO-GRN", "", "NO GRN") else cur
            if col_index == 5:  # Invoice #
                c = str(cur).strip()
                return c[3:].strip() if c.upper().startswith("IN ") else c
            return cur

        self._make_tree_editable(
            self._rename_tree,
            on_edit_callback=self._on_rename_tree_edit,
            editable_cols={2, 4, 5},
            pre_edit_fn=_rename_pre_edit,
        )
        # Register PDF preview for column 0
        self._rename_tree._edit_on_preview_cb = self._on_rename_preview_by_rowid

    # ------------------------------------------------------------------
    # GRN DISPATCH TAB
    # ------------------------------------------------------------------
    def _build_dispatch_tab(self):
        frame = self._make_tab("GRN Dispatch")
        self._legacy_dispatch_tab = frame
        frame.configure(style="TFrame")

        # Top control bar
        ctrl_bar = tk.Frame(frame, bg=PANEL2, height=52)
        ctrl_bar.pack(fill=tk.X)
        ctrl_bar.pack_propagate(False)

        self._btn_start_dispatch = ttk.Button(
            ctrl_bar, text="▶  Run GRN Extraction", style="Accent.TButton",
            command=self._start_dispatch_worker,
        )
        self._btn_start_dispatch.pack(side=tk.LEFT, padx=(16, 6), pady=8)

        ttk.Button(ctrl_bar, text="⏹  Cancel", command=self._cancel_worker).pack(side=tk.LEFT, padx=4, pady=8)
        ttk.Button(ctrl_bar, text="✅  Validate GRN", command=self._validate_grn, style="Success.TButton").pack(side=tk.LEFT, padx=4, pady=8)
        ttk.Button(ctrl_bar, text="📊  Export Excel", command=self._export_dispatch_to_excel).pack(side=tk.LEFT, padx=4, pady=8)
        ttk.Button(ctrl_bar, text="🗑  Clear", command=self._clear_dispatch_results).pack(side=tk.LEFT, padx=4, pady=8)

        # Source folder on right
        sf_frame = tk.Frame(ctrl_bar, bg=PANEL2)
        sf_frame.pack(side=tk.RIGHT, padx=16, pady=8)
        tk.Label(sf_frame, text="Source:", bg=PANEL2, fg=MUTED, font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 6))
        self._dispatch_folder_var = tk.StringVar(value=self.dirs.get("processed", ""))
        ent = tk.Entry(
            sf_frame,
            textvariable=self._dispatch_folder_var,
            bg=PANEL,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=("Segoe UI", 9),
            bd=0,
            highlightthickness=1,
            highlightbackground=PANEL3,
            highlightcolor=ACCENT,
            width=36,
        )
        ent.pack(side=tk.LEFT, ipady=4)
        ttk.Button(sf_frame, text="Browse", command=self._browse_dispatch_folder).pack(side=tk.LEFT, padx=(6, 0))

        # Progress
        prog_bar = tk.Frame(frame, bg=PANEL, height=6)
        prog_bar.pack(fill=tk.X)
        self._dispatch_progress = ttk.Progressbar(prog_bar, orient="horizontal", mode="determinate")
        self._dispatch_progress.pack(fill=tk.X)

        # Header
        hdr_bar = tk.Frame(frame, bg=PANEL)
        hdr_bar.pack(fill=tk.X)
        tk.Label(
            hdr_bar,
            text="GRN Dispatch Results",
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI", 11, "bold"),
        ).pack(side=tk.LEFT, padx=20, pady=(12, 4))
        tk.Label(
            hdr_bar,
            text="Double-click any cell except File to edit",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 8),
        ).pack(side=tk.LEFT, padx=8, pady=(12, 4))

        search_bar = tk.Frame(frame, bg=PANEL)
        search_bar.pack(fill=tk.X)
        self._make_search_bar(search_bar, lambda: self._dispatch_tree, lambda: self._dispatch_all_rows)

        # Tree
        tf = tk.Frame(frame, bg=PANEL)
        tf.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 16))

        dcols = ("File", "Date", "Supplier", "Confidence", "PO #", "Invoice #", "USD", "MVR", "EUR", "GBP", "SGD", "GRN")
        dcw = {
            "File": 190, "Date": 90, "Supplier": 200, "Confidence": 80,
            "PO #": 115, "Invoice #": 130, "USD": 85, "MVR": 85,
            "EUR": 85, "GBP": 85, "SGD": 85, "GRN": 220,
        }

        self._dispatch_tree = ttk.Treeview(tf, columns=dcols, show="headings", height=20)
        for c in dcols:
            self._dispatch_tree.heading(c, text=c, anchor="center")
            self._dispatch_tree.column(c, width=dcw.get(c, 100), anchor="center", stretch=False)

        ys = ttk.Scrollbar(tf, orient="vertical", command=self._dispatch_tree.yview)
        xs = ttk.Scrollbar(tf, orient="horizontal", command=self._dispatch_tree.xview)
        self._dispatch_tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)

        self._dispatch_tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)

        self._bind_tree_mousewheel(self._dispatch_tree)

        self._make_tree_editable(
            self._dispatch_tree,
            on_edit_callback=self._on_dispatch_tree_edit,
            editable_cols=set(range(1, 12)),
        )

        self._dispatch_tree._edit_on_preview_cb = self._on_dispatch_preview_by_rowid

    def _on_dispatch_preview_by_rowid(self, row_id: str):
        result   = self._dispatch_row_map.get(row_id, {})
        pdf_path = result.get("raw_path", "")
        folder   = self._dispatch_folder_var.get().strip() or self.dirs.get("processed", "")
        if not pdf_path or not os.path.exists(pdf_path):
            fname    = result.get("file", "")
            pdf_path = os.path.join(folder, fname)
        self._show_pdf_preview(pdf_path)

    def _browse_dispatch_folder(self):
        f = filedialog.askdirectory(initialdir=self._dispatch_folder_var.get() or self.dirs["base"])
        if f:
            self._dispatch_folder_var.set(f)

    # ------------------------------------------------------------------
    # SETTINGS TAB
    # ------------------------------------------------------------------
    def _build_settings_tab(self):
        frame = self._make_scrollable_tab("Settings")
        # Used by the Debug Engines window's "Engine Settings" shortcut.
        # `frame.master` is the CANVAS (holding the scrollable content), but
        # the notebook tab itself is the canvas's parent, so select/move
        # operations must target that.
        self._settings_tab_frame = frame.master.master

        # Auto-ingest watcher
        wb = self._section(frame, "Auto-Ingest (Watched Folder)",
                           subtitle="Monitors SCANNED folder for new PDFs")
        wf = tk.Frame(wb, bg=PANEL)
        wf.pack(fill=tk.X, padx=20, pady=(0, 4))

        if not WATCHDOG_AVAILABLE:
            tk.Label(
                wf,
                text="⚠  watchdog library not installed.  Run:  pip install watchdog",
                bg=PANEL, fg=WARNING, font=("Segoe UI", 9),
            ).pack(anchor="w", pady=(0, 8))

        tk.Label(
            wf,
            text=(
                "The engine is selected on the GRN Dispatch tab. Every document is placed in one "
                "serial queue, so a rapid series of scans is processed one at a time without mixing "
                "their order. New files are debounced for 3 seconds while the scanner finishes writing.\n"
                "• Offline: the local engine chosen in Settings → OCR Engines; no internet or upload limit.\n"
                "• Online: OCR.space API (engine 1/2/3 selected in Settings); large files are copied/"
                "compressed into TEMP API PDFS. "
                "Original PDFs stay in SCANNED until you choose Process All."
            ),
            bg=PANEL, fg=MUTED, font=("Segoe UI", 9), wraplength=850, justify="left",
        ).pack(anchor="w", pady=(0, 12))

        ai_switch_row = tk.Frame(wf, bg=PANEL)
        ai_switch_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Checkbutton(
            ai_switch_row,
            text="Auto-Ingest ON — OFFLINE mode",
            variable=self._watcher_offline_var,
            command=self._on_watcher_offline_toggle,
            style="TCheckbutton",
        ).pack(side=tk.LEFT, padx=(0, 20))
        ttk.Checkbutton(
            ai_switch_row,
            text="Auto-Ingest ON — ONLINE mode",
            variable=self._watcher_api_var,
            command=self._on_watcher_api_toggle,
            style="TCheckbutton",
        ).pack(side=tk.LEFT)
        tk.Label(
            wf,
            text=("Each mode has its own switch. Only one can be ON at a time — turning one on "
                  "turns the other off, so the same PDF is never processed twice. The mode that "
                  "is ON also becomes the working mode on the GRN Dispatch tab."),
            bg=PANEL, fg=MUTED, font=("Segoe UI", 8), wraplength=880, justify="left",
        ).pack(anchor="w", pady=(2, 12))

        # Desktop notifications
        nb = self._section(frame, "Desktop Notifications")
        nf = tk.Frame(nb, bg=PANEL)
        nf.pack(fill=tk.X, padx=20, pady=(0, 4))

        if not PLYER_AVAILABLE:
            tk.Label(
                nf,
                text="⚠  plyer library not installed.  Run:  pip install plyer",
                bg=PANEL, fg=WARNING, font=("Segoe UI", 9),
            ).pack(anchor="w", pady=(0, 8))

        ttk.Checkbutton(
            nf,
            text="Show desktop notifications when a batch finishes or a file fails",
            variable=self._notify_var, command=self._on_notify_toggle,
        ).pack(anchor="w", pady=4)
        tk.Label(
            nf,
            text='Summary format: "23 done, 1 failed — LYCORN invoice unmatched."',
            bg=PANEL, fg=MUTED, font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(0, 12))

        # Confidence threshold
        cfb = self._section(frame, "Confidence Scoring", subtitle="Flags uncertain supplier matches")
        cf = tk.Frame(cfb, bg=PANEL)
        cf.pack(fill=tk.X, padx=20, pady=(0, 12))
        tk.Label(cf, text="Warn threshold (%):", bg=PANEL, fg=TEXT, font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Spinbox(cf, from_=0, to=100, textvariable=self._conf_threshold_var, width=6,
                    font=("Segoe UI", 10)).pack(side=tk.LEFT)
        tk.Label(cf, text="  — rows below this are highlighted in orange",
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=8)
                 
        # Page scan region
        psb = self._section(frame, "Page Scan Region",
                            subtitle="Limit OCR to a % of the page from the top")
        pf = tk.Frame(psb, bg=PANEL)
        pf.pack(fill=tk.X, padx=20, pady=(0, 12))

        self._page_scan_enabled_var = tk.BooleanVar(
            value=self.cfg.get("app_settings", {}).get("page_scan_region_enabled", False)
        )
        self._page_scan_percent_var = tk.IntVar(
            value=self.cfg.get("app_settings", {}).get("page_scan_region_percent", 100)
        )

        ttk.Checkbutton(
            pf,
            text="Enable page scan region (scan only top N% of each page)",
            variable=self._page_scan_enabled_var,
            style="TCheckbutton",
        ).pack(anchor="w", pady=(0, 8))

        pct_row = tk.Frame(pf, bg=PANEL)
        pct_row.pack(anchor="w")
        tk.Label(pct_row, text="Scan top:", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Spinbox(
            pct_row, from_=5, to=100,
            textvariable=self._page_scan_percent_var,
            width=6, font=("Segoe UI", 10),
        ).pack(side=tk.LEFT)
        tk.Label(pct_row, text="% of each page  (5–100)",
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=8)

        tk.Label(
            pf,
            text="Useful when supplier/invoice info always appears in the page header. "
                 "Set to 100% to scan the full page (same as disabled).",
            bg=PANEL, fg=MUTED, font=("Segoe UI", 9), wraplength=850,
        ).pack(anchor="w", pady=(8, 0))

        # ---- OCR Mode (Zone vs Full) ----
        ocr_mode_box = self._section(frame, "OCR Extraction Mode",
                                     subtitle="Zone OCR is faster; Full OCR is more thorough")
        self._ocr_mode_var = tk.StringVar(
            value=self.cfg.get("app_settings", {}).get("ocr_mode", "full")
        )
        ocr_mode_inner = tk.Frame(ocr_mode_box, bg=PANEL)
        ocr_mode_inner.pack(fill=tk.X, padx=20, pady=(0, 4))

        for val, lbl, desc in [
            (
                "full",
                "Full OCR  (Default, Recommended)",
                "Scans the entire page. Slower but catches all data including rotated or unusual layouts.",
            ),
            (
                "zone",
                "Zone OCR  (Fast)",
                "Scans only the top ~38% header region. 3–5× faster. Best for standard Birchstreet "
                "receiving reports where GRN, supplier and invoice data are always in the header.",
            ),
        ]:
            row = tk.Frame(ocr_mode_inner, bg=PANEL)
            row.pack(fill=tk.X, pady=4)
            ttk.Radiobutton(
                row, text=lbl, value=val,
                variable=self._ocr_mode_var,
                command=self._on_ocr_mode_changed,
                style="TRadiobutton",
            ).pack(anchor="w")
            tk.Label(
                row, text=f"    {desc}",
                bg=PANEL, fg=MUTED, font=("Segoe UI", 8), wraplength=820, justify="left",
            ).pack(anchor="w")

        # Info label showing current mode
        self._ocr_mode_info_var = tk.StringVar(
            value=self._ocr_mode_label(self.cfg.get("app_settings", {}).get("ocr_mode", "full"))
        )
        tk.Label(
            ocr_mode_inner,
            textvariable=self._ocr_mode_info_var,
            bg=PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", pady=(4, 12))
        
        # Processing mode
        pm_box = self._section(frame, "Processing Mode")
        self._processing_mode_var = tk.StringVar(
            value=self.cfg.get("app_settings", {}).get("processing_mode", "legacy")
        )
        pm_inner = tk.Frame(pm_box, bg=PANEL)
        pm_inner.pack(fill=tk.X, padx=20, pady=(0, 12))
        for val, lbl, desc in [
            ("legacy", "Legacy  (Default, Recommended)", "Original algorithm — 650px crop, 3× scale, Tesseract PSM 6."),
            ("mixed",  "Mixed",                          "Custom supplier/GRN + Legacy invoice number extraction."),
            ("custom", "Custom",                         "Enhanced multi-method extraction via fields and headers."),
        ]:
            row = tk.Frame(pm_inner, bg=PANEL)
            row.pack(fill=tk.X, pady=3)
            ttk.Radiobutton(row, text=lbl, value=val, variable=self._processing_mode_var,
                            command=self._on_processing_mode_changed, style="TRadiobutton").pack(anchor="w")
            tk.Label(row, text=f"    {desc}", bg=PANEL, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")

        # Supplier extraction method
        mb = self._section(frame, "Supplier Extraction Method", subtitle="Custom / Mixed mode only")
        self._supplier_method_var = tk.StringVar(
            value=self.cfg.get("app_settings", {}).get("supplier_extraction_method", "both")
        )
        mb_inner = tk.Frame(mb, bg=PANEL)
        mb_inner.pack(fill=tk.X, padx=20, pady=(0, 12))
        for val, lbl in [
            ("both",                  'Both (Recommended): Check "Supplier:" / "Vendor:" field first, then header'),
            ("receiving_report_field",'Field Only: Extract from "Supplier:" or "Vendor:" in Receiving Report'),
            ("header_company_name",   "Header Only: Extract from invoice header area"),
        ]:
            ttk.Radiobutton(mb_inner, text=lbl, value=val, variable=self._supplier_method_var,
                            command=self._on_supplier_method_changed, style="TRadiobutton").pack(anchor="w", pady=4)

        # ---- OCR engines: mode (ONLINE/OFFLINE), engine choice and installs ----
        eb = self._section(
            frame,
            "OCR Engines",
            subtitle="Choose the working engine and install any missing one here",
        )
        eb_inner = tk.Frame(eb, bg=PANEL)
        eb_inner.pack(fill=tk.X, padx=20, pady=(0, 12))

        self._engine_var = tk.StringVar(
            value=self.cfg.get("app_settings", {}).get("local_ocr_engine")
            or self.cfg.get("app_settings", {}).get("ocr_engine", "tesseract")
        )

        # --- Mode row: ONLINE / OFFLINE with auto-ingest on/off for each ---
        mode_row = tk.Frame(eb_inner, bg=PANEL)
        mode_row.pack(fill=tk.X, pady=(0, 8))
        tk.Label(mode_row, text="Working mode:", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9, "bold"), width=14, anchor="w").pack(side=tk.LEFT)
        ttk.Radiobutton(
            mode_row, text="ONLINE  (OCR.space API)", value=GRN_ONLINE,
            variable=self._grn_engine_var, command=self._on_engine_changed,
            style="TRadiobutton",
        ).pack(side=tk.LEFT, padx=(0, 18))
        ttk.Radiobutton(
            mode_row, text="OFFLINE  (local engine)", value=GRN_OFFLINE,
            variable=self._grn_engine_var, command=self._on_engine_changed,
            style="TRadiobutton",
        ).pack(side=tk.LEFT)

        # --- Auto-ingest on/off for each mode ---
        ai_row = tk.Frame(eb_inner, bg=PANEL)
        ai_row.pack(fill=tk.X, pady=(0, 4))
        tk.Label(ai_row, text="Auto-ingest:", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9, "bold"), width=14, anchor="w").pack(side=tk.LEFT)
        ttk.Checkbutton(
            ai_row,
            text="ON for OFFLINE mode (watch SCANNED, process locally)",
            variable=self._watcher_offline_var,
            command=self._on_watcher_offline_toggle,
            style="TCheckbutton",
        ).pack(side=tk.LEFT, padx=(0, 18))
        ttk.Checkbutton(
            ai_row,
            text="ON for ONLINE mode (watch SCANNED, upload to OCR.space)",
            variable=self._watcher_api_var,
            command=self._on_watcher_api_toggle,
            style="TCheckbutton",
        ).pack(side=tk.LEFT)
        tk.Label(
            eb_inner,
            text=(
                "Auto-ingest runs the engine selected below, in the mode selected above, for every new "
                "PDF dropped into SCANNED. Only ONE mode can be active at a time — turning one ON turns "
                "the other OFF. Scans are processed strictly in order: SCAN_0001, SCAN_0002, …"
            ),
            bg=PANEL, fg=MUTED, font=("Segoe UI", 8), wraplength=880, justify="left",
        ).pack(anchor="w", pady=(0, 10))

        # --- ONLINE engine choice (OCR.space engine 1/2/3) ---
        online_row = tk.Frame(eb_inner, bg=PANEL)
        online_row.pack(fill=tk.X, pady=(0, 2))
        tk.Label(online_row, text="Online engine:", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9, "bold"), width=14, anchor="w").pack(side=tk.LEFT)
        for num, name in ((1, "Engine 1 — Default"), (2, "Engine 2 — Enhanced"),
                          (3, "Engine 3 — Extra Accurate")):
            ttk.Radiobutton(
                online_row, text=name, value=num, variable=self._online_engine_var,
                command=self._on_engine_changed, style="TRadiobutton",
            ).pack(side=tk.LEFT, padx=(0, 14))
        self._online_engine_status_lbl = tk.Label(
            eb_inner, text="", bg=PANEL, fg=MUTED, font=("Segoe UI", 8),
        )
        self._online_engine_status_lbl.pack(anchor="w", pady=(0, 10))

        # --- LOCAL engine rows: radio + status + Install button ---
        tk.Label(eb_inner, text="Local engines (used by OFFLINE mode):", bg=PANEL,
                 fg=MUTED, font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 2))

        self._engine_rows = {}
        self._engine_status_lbls = {}
        self._engine_install_btns = {}
        self._engine_progress_vars = {}
        for key in local_engine_keys():
            row = tk.Frame(eb_inner, bg=PANEL)
            row.pack(fill=tk.X, pady=4)
            ttk.Radiobutton(
                row, text=ENGINE_LABELS.get(key, key), value=key,
                variable=self._engine_var, command=self._on_engine_changed,
                style="TRadiobutton",
            ).pack(side=tk.LEFT)
            st_lbl = tk.Label(row, text="", bg=PANEL, fg=MUTED,
                              font=("Segoe UI", 9, "bold"))
            st_lbl.pack(side=tk.LEFT, padx=14)
            btn = ttk.Button(
                row, text="⬇  Install", width=12,
                command=lambda k=key: self._install_engine(k),
            )
            btn.pack(side=tk.RIGHT)
            self._engine_rows[key] = row
            self._engine_status_lbls[key] = st_lbl
            self._engine_install_btns[key] = btn

        # Live install log (shared by every engine install)
        self._engine_install_status_var = tk.StringVar(value="")
        tk.Label(
            eb_inner, textvariable=self._engine_install_status_var, bg=PANEL,
            fg=ACCENT, font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", pady=(8, 2))
        log_wrap = tk.Frame(eb_inner, bg=PANEL)
        log_wrap.pack(fill=tk.X, pady=(0, 6))
        self._engine_install_log = tk.Text(
            log_wrap, height=6, bg=PANEL2, fg=TEXT, font=("Consolas", 8),
            relief="flat", borderwidth=0, wrap=tk.WORD,
        )
        _log_sb = ttk.Scrollbar(log_wrap, orient="vertical", command=self._engine_install_log.yview)
        self._engine_install_log.configure(yscrollcommand=_log_sb.set)
        self._engine_install_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        _log_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._engine_install_log.configure(state="disabled")

        act_row = tk.Frame(eb_inner, bg=PANEL)
        act_row.pack(fill=tk.X, pady=(2, 0))
        ttk.Button(act_row, text="↻  Re-check engines",
                   command=self._refresh_engine_rows).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(act_row, text="⬇  Install all missing",
                   command=self._install_all_missing_engines).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(act_row, text="🧙  Run first-time setup again",
                   command=self._run_engine_setup_wizard).pack(side=tk.LEFT)
        if not ENGINE_MANAGER_AVAILABLE:
            tk.Label(
                eb_inner,
                text="⚠  ocr_engine_manager.py not found — automatic installation is unavailable. "
                     "Place it next to maafushivaru_hub.py to enable in-app installs.",
                bg=PANEL, fg=WARNING, font=("Segoe UI", 9), wraplength=880, justify="left",
            ).pack(anchor="w", pady=(6, 0))

        self._refresh_engine_rows()
        # ── Supplier Match Strategy ──────────────────────────────────────
        smb = self._section(
            frame,
            "Supplier Match Strategy",
            subtitle="Algorithm used to identify the supplier name from extracted text",
        )
        self._supplier_strategy_var = tk.StringVar(
            value=self.cfg.get("app_settings", {}).get("supplier_match_strategy", "combined")
        )
        smb_inner = tk.Frame(smb, bg=PANEL)
        smb_inner.pack(fill=tk.X, padx=20, pady=(0, 12))

        if SUPPLIER_MATCHER_AVAILABLE:
            for val, lbl in STRATEGY_LABELS.items():
                ttk.Radiobutton(
                    smb_inner, text=lbl, value=val,
                    variable=self._supplier_strategy_var,
                    command=self._on_supplier_strategy_changed,
                    style="TRadiobutton",
                ).pack(anchor="w", pady=3)
        else:
            tk.Label(
                smb_inner,
                text="⚠  supplier_matcher.py not found — place it in the same folder as maafushivaru_hub.py",
                bg=PANEL, fg=WARNING, font=("Segoe UI", 9),
            ).pack(anchor="w", pady=(0, 8))

        # ── AI Supplier Match ─────────────────────────────────────────────
        aib = self._section(
            frame,
            "AI Supplier Matching",
            subtitle="Use a cloud AI (Claude, GPT, Gemini) to identify suppliers",
        )
        ai_inner = tk.Frame(aib, bg=PANEL)
        ai_inner.pack(fill=tk.X, padx=20, pady=(0, 12))

        if not AI_MATCHER_AVAILABLE:
            tk.Label(
                ai_inner,
                text="⚠  ai_supplier_matcher.py not found — place it in the same folder as maafushivaru_hub.py",
                bg=PANEL,
                fg=WARNING,
                font=("Segoe UI", 9),
            ).pack(anchor="w", pady=(0, 8))
        else:
            tk.Label(
                ai_inner,
                text="AI supplier module loaded successfully. OCR.space is available as the default API OCR backend.",
                bg=PANEL,
                fg=SUCCESS,
                font=("Segoe UI", 9),
            ).pack(anchor="w", pady=(0, 8))

        ai_cfg = self.cfg.get("ai_settings", {})

        self._ai_enabled_var = tk.BooleanVar(value=bool(ai_cfg.get("enabled", False)))
        ttk.Checkbutton(
            ai_inner,
            text="Enable AI supplier matching (falls back to rule-based if AI fails)",
            variable=self._ai_enabled_var,
            command=self._on_ai_enabled_toggle,
            style="TCheckbutton",
        ).pack(anchor="w", pady=(0, 8))

        prow = tk.Frame(ai_inner, bg=PANEL)
        prow.pack(fill=tk.X, pady=3)
        tk.Label(
            prow,
            text="Provider:",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9, "bold"),
            width=16,
            anchor="w",
        ).pack(side=tk.LEFT)

        self._ai_provider_var = tk.StringVar(value=ai_cfg.get("provider", "openai"))
        provider_names = {k: v["label"] for k, v in PROVIDERS.items()} if PROVIDERS else {
            "openai": "OpenAI GPT",
            "custom": "Custom / Local",
        }

        for val, lbl in provider_names.items():
            ttk.Radiobutton(
                prow,
                text=lbl,
                value=val,
                variable=self._ai_provider_var,
                command=self._on_ai_provider_changed,
                style="TRadiobutton",
            ).pack(side=tk.LEFT, padx=(0, 14))

        mrow = tk.Frame(ai_inner, bg=PANEL)
        mrow.pack(fill=tk.X, pady=3)
        tk.Label(
            mrow,
            text="Model:",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9, "bold"),
            width=16,
            anchor="w",
        ).pack(side=tk.LEFT)

        self._ai_model_var = tk.StringVar(value=ai_cfg.get("model", "gpt-4o-mini"))
        self._ai_model_entry = tk.Entry(
            mrow,
            textvariable=self._ai_model_var,
            bg=PANEL2,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=("Segoe UI", 10),
            bd=0,
            highlightthickness=1,
            highlightbackground=PANEL3,
            highlightcolor=ACCENT,
            width=36,
        )
        self._ai_model_entry.pack(side=tk.LEFT, ipady=4)

        krow = tk.Frame(ai_inner, bg=PANEL)
        krow.pack(fill=tk.X, pady=3)
        tk.Label(
            krow,
            text="API Key:",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9, "bold"),
            width=16,
            anchor="w",
        ).pack(side=tk.LEFT)

        self._ai_key_var = tk.StringVar(value=ai_cfg.get("api_key", ""))
        tk.Entry(
            krow,
            textvariable=self._ai_key_var,
            bg=PANEL2,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=("Segoe UI", 10),
            bd=0,
            highlightthickness=1,
            highlightbackground=PANEL3,
            highlightcolor=ACCENT,
            show="*",
            width=52,
        ).pack(side=tk.LEFT, ipady=4)

        urow = tk.Frame(ai_inner, bg=PANEL)
        urow.pack(fill=tk.X, pady=3)
        tk.Label(
            urow,
            text="Custom URL:",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9, "bold"),
            width=16,
            anchor="w",
        ).pack(side=tk.LEFT)

        self._ai_url_var = tk.StringVar(value=ai_cfg.get("custom_base_url", ""))
        tk.Entry(
            urow,
            textvariable=self._ai_url_var,
            bg=PANEL2,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=("Segoe UI", 10),
            bd=0,
            highlightthickness=1,
            highlightbackground=PANEL3,
            highlightcolor=ACCENT,
            width=52,
        ).pack(side=tk.LEFT, ipady=4)

        tk.Label(
            urow,
            text="  (for Custom/Local only)",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 8),
        ).pack(side=tk.LEFT, padx=6)

        trow = tk.Frame(ai_inner, bg=PANEL)
        trow.pack(fill=tk.X, pady=3)
        tk.Label(
            trow,
            text="Timeout (sec):",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9, "bold"),
            width=16,
            anchor="w",
        ).pack(side=tk.LEFT)

        self._ai_timeout_var = tk.IntVar(value=int(ai_cfg.get("timeout_seconds", 20)))
        ttk.Spinbox(
            trow,
            from_=5,
            to=120,
            textvariable=self._ai_timeout_var,
            width=6,
            font=("Segoe UI", 10),
        ).pack(side=tk.LEFT)

        ocr_space_cfg = self.cfg.get("ocr_space", {})

        okrow = tk.Frame(ai_inner, bg=PANEL)
        okrow.pack(fill=tk.X, pady=3)
        tk.Label(
            okrow,
            text="OCR.space Key:",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9, "bold"),
            width=16,
            anchor="w",
        ).pack(side=tk.LEFT)

        self._ocr_space_key_var = tk.StringVar(value=ocr_space_cfg.get("api_key", "K88109865088957"))
        tk.Entry(
            okrow,
            textvariable=self._ocr_space_key_var,
            bg=PANEL2,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=("Segoe UI", 10),
            bd=0,
            highlightthickness=1,
            highlightbackground=PANEL3,
            highlightcolor=ACCENT,
            show="*",
            width=52,
        ).pack(side=tk.LEFT, ipady=4)

        oerow = tk.Frame(ai_inner, bg=PANEL)
        oerow.pack(fill=tk.X, pady=3)
        tk.Label(
            oerow,
            text="OCR.space Engine:",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9, "bold"),
            width=16,
            anchor="w",
        ).pack(side=tk.LEFT)

        # Same Tk variable as the ONLINE engine radio in Settings → OCR Engines:
        # both controls always show the same engine, and the choice is stored in
        # app_settings.online_ocr_engine (mirrored into ocr_space.OCREngine).
        self._ocr_space_engine_var = getattr(
            self, "_online_engine_var", tk.IntVar(value=int(ocr_space_cfg.get("OCREngine", 2)))
        )
        for eng_num in (1, 2, 3):
            ttk.Radiobutton(
                oerow,
                text=str(eng_num),
                value=eng_num,
                variable=self._ocr_space_engine_var,
                style="TRadiobutton",
            ).pack(side=tk.LEFT, padx=(0, 14))

        srow = tk.Frame(ai_inner, bg=PANEL)
        srow.pack(fill=tk.X, pady=3)
        tk.Label(
            srow,
            text="Max Upload:",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9, "bold"),
            width=16,
            anchor="w",
        ).pack(side=tk.LEFT)

        self._ocr_space_max_mb_var = tk.StringVar(value=str(ocr_space_cfg.get("max_upload_mb", 1.0)))
        tk.Entry(
            srow,
            textvariable=self._ocr_space_max_mb_var,
            bg=PANEL2,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=("Segoe UI", 10),
            bd=0,
            highlightthickness=1,
            highlightbackground=PANEL3,
            highlightcolor=ACCENT,
            width=8,
        ).pack(side=tk.LEFT, ipady=4)

        tk.Label(
            srow,
            text="MB  (OCR upload will be compressed below this limit; recommended 1.0)",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 8),
        ).pack(side=tk.LEFT, padx=8)

        strow = tk.Frame(ai_inner, bg=PANEL)
        strow.pack(fill=tk.X, pady=(8, 0))

        self._ai_status_lbl = tk.Label(
            strow,
            textvariable=self._ai_status_var,
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9),
        )
        self._ai_status_lbl.pack(side=tk.LEFT, padx=(0, 12))

        ttk.Button(
            strow,
            text="🔌  Test Connection",
            command=self._test_ai_connection,
        ).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(
            strow,
            text="📜  View AI Log",
            command=self._open_ai_log_window,
        ).pack(side=tk.LEFT)

        tk.Label(
            ai_inner,
            text="API keys are stored in config.json. Keep that file secure and out of version control.",
            bg=PANEL,
            fg=ERROR,
            font=("Segoe UI", 8),
            wraplength=850,
        ).pack(anchor="w", pady=(6, 0))

        # Processing parameters
        sb = self._section(frame, "Processing Parameters")
        self._extract_text_var      = tk.BooleanVar(value=self.cfg.get("app_settings", {}).get("extract_text_before_ocr", True))
        self._enhance_var           = tk.BooleanVar(value=self.cfg.get("app_settings", {}).get("enhance_images", True))
        self._fallback_var          = tk.BooleanVar(value=self.cfg.get("app_settings", {}).get("ocr_fallback_to_tesseract", True))
        self._extraction_source_var = tk.StringVar(value=self.cfg.get("app_settings", {}).get("extraction_source", "auto"))
        self._max_threads_var       = tk.IntVar(value=self.cfg.get("app_settings", {}).get("max_threads", 4))
        self._threshold_var         = tk.IntVar(value=self.cfg.get("app_settings", {}).get("fuzzy_match_threshold", 85))
        self._scale_var             = tk.IntVar(value=self.cfg.get("app_settings", {}).get("image_scale_factor", 2))

        sb_inner = tk.Frame(sb, bg=PANEL)
        sb_inner.pack(fill=tk.X, padx=20, pady=(0, 12))

        checks = [
            ("Extract native PDF text before OCR",      self._extract_text_var),
            ("Enhance images before OCR",               self._enhance_var),
            ("Fallback to Tesseract if engine fails",   self._fallback_var),
            ("Enable parallel processing (multi-threading)", self._threads_var),
        ]
        for lbl, var in checks:
            ttk.Checkbutton(sb_inner, text=lbl, variable=var, style="TCheckbutton").pack(anchor="w", pady=3)

        tk.Label(sb_inner, text="Extraction source:", bg=PANEL, fg=MUTED, font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(10, 2))
        for val, lbl in [
            ("auto",          "Auto — PDF text first, then image OCR if needed"),
            ("pdf_text_only", "PDF text only — no image OCR"),
            ("image_only",    "Image OCR only — ignore embedded text"),
        ]:
            ttk.Radiobutton(sb_inner, text=lbl, value=val, variable=self._extraction_source_var,
                            style="TRadiobutton").pack(anchor="w", padx=20, pady=2)

        g = tk.Frame(sb_inner, bg=PANEL)
        g.pack(fill=tk.X, pady=(12, 0))
        for r, (lbl, var, lo, hi) in enumerate([
            ("Max threads:",                       self._max_threads_var,  1, 12),
            ("Fuzzy match threshold:",             self._threshold_var,   50, 100),
            ("Image scale factor (Custom mode):",  self._scale_var,        1,  5),
        ]):
            tk.Label(g, text=lbl, bg=PANEL, fg=TEXT, font=("Segoe UI", 10), width=34, anchor="w").grid(row=r, column=0, sticky="w", pady=6)
            ttk.Spinbox(g, from_=lo, to=hi, textvariable=var, width=8, font=("Segoe UI", 10)).grid(row=r, column=1, sticky="w", padx=8)

        # ── OCR Word Correction ───────────────────────────────────────────
        ocr_corr_box = self._section(
            frame,
            "OCR Word Correction",
            subtitle="Fixes broken OCR words by fuzzy-matching against known supplier names",
        )
        ocr_corr_inner = tk.Frame(ocr_corr_box, bg=PANEL)
        ocr_corr_inner.pack(fill=tk.X, padx=20, pady=(0, 12))

        self._ocr_word_correction_var = tk.BooleanVar(
            value=self.cfg.get("app_settings", {}).get("ocr_word_correction_enabled", False)
        )
        ttk.Checkbutton(
            ocr_corr_inner,
            text="Enable OCR Word Correction — fix garbled supplier words (e.g. EASRN → EASTERN)",
            variable=self._ocr_word_correction_var,
            style="TCheckbutton",
        ).pack(anchor="w", pady=4)
        tk.Label(
            ocr_corr_inner,
            text=(
                "Allows 1–2 wrong characters per word (longer words allow slightly more).\n"
                "Turn OFF if you get false supplier matches. Requires ocr_word_corrector.py."
            ),
            bg=PANEL, fg=MUTED, font=("Segoe UI", 9), wraplength=850, justify="left",
        ).pack(anchor="w", pady=(0, 4))

        if not OCR_CORRECTOR_AVAILABLE:
            tk.Label(
                ocr_corr_inner,
                text="⚠  ocr_word_corrector.py not found in the application folder.",
                bg=PANEL, fg=WARNING, font=("Segoe UI", 9),
            ).pack(anchor="w")

        # ── Smart Cross-Matching ──────────────────────────────────────────
        xcm_box = self._section(
            frame,
            "Smart Supplier ↔ Invoice Cross-Matching",
            subtitle="Infer supplier from invoice format, and vice versa",
        )
        xcm_inner = tk.Frame(xcm_box, bg=PANEL)
        xcm_inner.pack(fill=tk.X, padx=20, pady=(0, 12))

        self._smart_cross_match_var = tk.BooleanVar(
            value=self.cfg.get("app_settings", {}).get("smart_cross_match_enabled", False)
        )
        ttk.Checkbutton(
            xcm_inner,
            text="Enable Smart Cross-Matching — identify supplier from invoice number pattern, and vice versa",
            variable=self._smart_cross_match_var,
            style="TCheckbutton",
        ).pack(anchor="w", pady=4)
        tk.Label(
            xcm_inner,
            text=(
                "When enabled:\n"
                "  • If the supplier is found, the invoice number is cleaned using that supplier's known format.\n"
                "  • If the supplier is NOT found, the invoice number pattern is used to identify the supplier.\n"
                "  • When OFF: the system works exactly as before — no cross-matching.\n"
                "Edit SUPPLIER_INVOICE_XREF in smart_cross_matcher.py to add your invoice formats."
            ),
            bg=PANEL, fg=MUTED, font=("Segoe UI", 9), wraplength=850, justify="left",
        ).pack(anchor="w", pady=(0, 4))

        if not CROSS_MATCHER_AVAILABLE:
            tk.Label(
                xcm_inner,
                text="⚠  smart_cross_matcher.py not found in the application folder.",
                bg=PANEL, fg=WARNING, font=("Segoe UI", 9),
            ).pack(anchor="w")

        # Base path
        pb = self._section(frame, "Base Folder Path")
        self._base_var = tk.StringVar(value=self.dirs["base"])
        pb_row = tk.Frame(pb, bg=PANEL)
        pb_row.pack(fill=tk.X, padx=20, pady=(0, 16))
        tk.Label(pb_row, text="Base folder:", bg=PANEL, fg=MUTED, font=("Segoe UI", 9, "bold"), width=14, anchor="w").pack(side=tk.LEFT)
        ent_base = tk.Entry(pb_row, textvariable=self._base_var, bg=PANEL2, fg=TEXT,
                            insertbackground=TEXT, relief="flat", font=("Segoe UI", 10), bd=0,
                            highlightthickness=1, highlightbackground=PANEL3, highlightcolor=ACCENT)
        ent_base.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6, ipady=5)
        ttk.Button(pb_row, text="Browse", command=self._browse_base_folder).pack(side=tk.LEFT, padx=(6, 0))

        # Actions
        ab = self._section(frame, "Configuration Actions")
        ab_row = tk.Frame(ab, bg=PANEL)
        ab_row.pack(fill=tk.X, padx=20, pady=(0, 20))
        ttk.Button(ab_row, text="🔧  Test Engine",       command=self._test_current_engine).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(ab_row, text="↺  Reload Config",      command=self._reload_settings).pack(side=tk.LEFT, padx=4)
        ttk.Button(ab_row, text="💾  Save All Settings", style="Accent.TButton",
                   command=self._apply_settings).pack(side=tk.RIGHT)

        # Suppliers
        sup_box = self._section(frame, "Suppliers & Aliases")
        tk.Label(
            sup_box,
            text="Add or remove suppliers. Aliases are alternative names that map to the same supplier.",
            bg=PANEL, fg=MUTED, font=("Segoe UI", 9), wraplength=900,
        ).pack(anchor="w", padx=20, pady=(0, 6))

        # --- Search bar for supplier tree ---
        sup_search_bar = tk.Frame(sup_box, bg=PANEL)
        sup_search_bar.pack(fill=tk.X, padx=20, pady=(0, 6))
        tk.Label(
            sup_search_bar, text="Search:", bg=PANEL, fg=MUTED, font=("Segoe UI", 9),
        ).pack(side=tk.LEFT, padx=(0, 6))
        self._sup_search_var = tk.StringVar()
        sup_search_ent = tk.Entry(
            sup_search_bar,
            textvariable=self._sup_search_var,
            bg=PANEL2, fg=TEXT, insertbackground=TEXT,
            relief="flat", font=("Segoe UI", 10), bd=0,
            highlightthickness=1, highlightbackground=PANEL3, highlightcolor=ACCENT,
        )
        sup_search_ent.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=5)

        def _clear_sup_search():
            self._sup_search_var.set("")
        ttk.Button(sup_search_bar, text="✕", command=_clear_sup_search, width=3).pack(side=tk.LEFT, padx=(6, 0))

        # --- Supplier treeview ---
        sup_tf = tk.Frame(sup_box, bg=PANEL)
        sup_tf.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 8))

        sup_cols = ("Supplier Name", "Aliases (comma-separated)")
        self._sup_tree = ttk.Treeview(sup_tf, columns=sup_cols, show="headings", height=10)
        self._sup_tree.heading("Supplier Name",              text="Supplier Name",              anchor="w")
        self._sup_tree.heading("Aliases (comma-separated)",  text="Aliases (comma-separated)",  anchor="w")
        self._sup_tree.column("Supplier Name",             width=220, anchor="w")
        self._sup_tree.column("Aliases (comma-separated)", width=520, anchor="w")

        sup_ys = ttk.Scrollbar(sup_tf, orient="vertical", command=self._sup_tree.yview)
        self._sup_tree.configure(yscrollcommand=sup_ys.set)
        self._sup_tree.grid(row=0, column=0, sticky="nsew")
        sup_ys.grid(row=0, column=1, sticky="ns")
        sup_tf.rowconfigure(0, weight=1)
        sup_tf.columnconfigure(0, weight=1)
        self._bind_tree_mousewheel(self._sup_tree)

        self._populate_supplier_tree()
        self._make_tree_editable(
            self._sup_tree,
            on_edit_callback=self._on_sup_tree_edit,
            editable_cols={0, 1},
            pre_edit_fn=None,
        )

        # Supplier tree column 0 is editable, not preview-only
        self._sup_tree._edit_on_preview_cb = None

        # Wire up live search against the supplier tree
        def _on_sup_search(*_):
            q = self._sup_search_var.get().strip().lower()
            # Re-populate filtered view
            for iid in self._sup_tree.get_children():
                self._sup_tree.detach(iid)
            # Keep a master list of all row ids
            for iid in getattr(self, '_sup_all_rows', []):
                if not q:
                    self._sup_tree.reattach(iid, "", "end")
                else:
                    v = self._sup_tree.item(iid, "values")
                    if any(q in str(x).lower() for x in v):
                        self._sup_tree.reattach(iid, "", "end")

        self._sup_search_var.trace_add("write", _on_sup_search)

        sup_ctrl = tk.Frame(sup_box, bg=PANEL)
        sup_ctrl.pack(fill=tk.X, padx=20, pady=(0, 20))
        ttk.Button(sup_ctrl, text="+ Add",    command=self._add_supplier_row).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(sup_ctrl, text="✕ Remove", command=self._remove_supplier_row).pack(side=tk.LEFT, padx=4)
        ttk.Button(
            sup_ctrl, text="💾  Save", style="Accent.TButton",
            command=self._save_suppliers,
        ).pack(side=tk.RIGHT)

        # ------------------------------------------------------------------
        # INVOICE PATTERNS (search / add / deactivate — mirrors Suppliers &
        # Aliases above). A pattern is never hard-deleted here: "Remove"
        # deactivates it (count/history kept, just stops being matched) so a
        # faulty regex can always be told apart from one you're testing.
        # ------------------------------------------------------------------
        inv_box = self._section(frame, "Invoice Patterns",
                                 "Search, add, or deactivate the regex pattern each supplier's invoice numbers are matched against")
        tk.Label(
            inv_box,
            text="Deactivating a pattern keeps its match history but stops it being used — use this if a "
                 "pattern for a supplier starts matching wrong (or another supplier's invoices).",
            bg=PANEL, fg=MUTED, font=("Segoe UI", 9), wraplength=900,
        ).pack(anchor="w", padx=20, pady=(0, 6))

        inv_search_bar = tk.Frame(inv_box, bg=PANEL)
        inv_search_bar.pack(fill=tk.X, padx=20, pady=(0, 6))
        tk.Label(
            inv_search_bar, text="Search:", bg=PANEL, fg=MUTED, font=("Segoe UI", 9),
        ).pack(side=tk.LEFT, padx=(0, 6))
        self._inv_search_var = tk.StringVar()
        inv_search_ent = tk.Entry(
            inv_search_bar,
            textvariable=self._inv_search_var,
            bg=PANEL2, fg=TEXT, insertbackground=TEXT,
            relief="flat", font=("Segoe UI", 10), bd=0,
            highlightthickness=1, highlightbackground=PANEL3, highlightcolor=ACCENT,
        )
        inv_search_ent.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=5)

        def _clear_inv_search():
            self._inv_search_var.set("")
        ttk.Button(inv_search_bar, text="✕", command=_clear_inv_search, width=3).pack(side=tk.LEFT, padx=(6, 0))

        inv_tf = tk.Frame(inv_box, bg=PANEL)
        inv_tf.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 8))

        inv_cols = ("Supplier", "Pattern", "Regex", "Status", "Matches")
        self._inv_tree = ttk.Treeview(inv_tf, columns=inv_cols, show="headings", height=10)
        widths = {"Supplier": 220, "Pattern": 160, "Regex": 360, "Status": 80, "Matches": 70}
        for c in inv_cols:
            self._inv_tree.heading(c, text=c, anchor="w")
            self._inv_tree.column(c, width=widths.get(c, 120), anchor="w")

        inv_ys = ttk.Scrollbar(inv_tf, orient="vertical", command=self._inv_tree.yview)
        self._inv_tree.configure(yscrollcommand=inv_ys.set)
        self._inv_tree.grid(row=0, column=0, sticky="nsew")
        inv_ys.grid(row=0, column=1, sticky="ns")
        inv_tf.rowconfigure(0, weight=1)
        inv_tf.columnconfigure(0, weight=1)
        self._bind_tree_mousewheel(self._inv_tree)

        self._inv_tree.tag_configure("active", foreground=SUCCESS)
        self._inv_tree.tag_configure("inactive", foreground=MUTED)

        self._populate_invoice_pattern_tree()

        def _on_inv_search(*_):
            q = self._inv_search_var.get().strip().lower()
            for iid in self._inv_tree.get_children():
                self._inv_tree.detach(iid)
            for iid in getattr(self, "_inv_all_rows", []):
                if not q:
                    self._inv_tree.reattach(iid, "", "end")
                else:
                    v = self._inv_tree.item(iid, "values")
                    if any(q in str(x).lower() for x in v):
                        self._inv_tree.reattach(iid, "", "end")

        self._inv_search_var.trace_add("write", _on_inv_search)

        inv_ctrl = tk.Frame(inv_box, bg=PANEL)
        inv_ctrl.pack(fill=tk.X, padx=20, pady=(0, 20))
        ttk.Button(inv_ctrl, text="+ Add Pattern", command=self._add_invoice_pattern_dialog).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(inv_ctrl, text="⏻ Toggle Active", command=self._toggle_invoice_pattern_active).pack(side=tk.LEFT, padx=4)
        ttk.Button(inv_ctrl, text="↻ Refresh", command=self._populate_invoice_pattern_tree).pack(side=tk.LEFT, padx=4)

    # ------------------------------------------------------------------
    # ABOUT TAB
    # ------------------------------------------------------------------
    def _build_about_tab(self):
        frame = self._make_scrollable_tab("About")
        center = tk.Frame(frame, bg=BG)
        center.pack(expand=True, pady=30, fill=tk.X)

        alp = resource_path("logo2.png")
        if alp.exists():
            try:
                img = Image.open(alp)
                img.thumbnail((480, 160))
                self._about_logo_img = ImageTk.PhotoImage(img)
                tk.Label(center, image=self._about_logo_img, bg=BG).pack(pady=(0, 16))
            except Exception:
                pass

        tk.Label(center, text="Document Processing Hub", bg=BG, fg=TEXT, font=("Segoe UI", 20, "bold")).pack()
        tk.Label(center, text=self.APP_VERSION, bg=BG, fg=MUTED, font=("Segoe UI", 11)).pack(pady=(4, 4))
        tk.Label(center, text="Author: Roni", bg=BG, fg=ACCENT, font=("Segoe UI", 13, "bold")).pack(pady=(0, 20))

        if ENGINE_MANAGER_AVAILABLE:
            tk.Label(
                center, text=oem.dependency_summary(), bg=BG, fg=SUCCESS,
                font=("Segoe UI", 9, "bold"),
            ).pack(pady=(0, 10))

        items = [
            ("Auto-Ingest",       "watchdog monitors SCANNED — new PDFs process automatically (toggle in Settings)"),
            ("Notifications",     "plyer sends desktop alerts when batches complete or files fail"),
            ("Confidence Score",  "Every supplier match shows % confidence; rows below threshold are highlighted"),
            ("Supplier Learning", "Manual corrections offer to save as aliases for future auto-matching"),
            ("Dry Run Preview",   "Proposes filenames in a popup before any files are moved"),
            ("Retry Failed",      "One-click retry for all files in the FAILED folder"),
            ("Duplicate Guard",   "Warns when an invoice number already exists in PROCESSED"),
            ("Live Search",       "Filter bars above both result tables update rows as you type"),
            ("PDF Preview",       "Double-click a filename in results to see the first page thumbnail"),
            ("Linked Views",      "Corrections in Renamer sync to Dispatch and vice-versa"),
            ("Multi-GRN",         "Chains multiple GRNs: RC-MAM-000019581-19582"),
        ]

        grid = tk.Frame(center, bg=PANEL, padx=24, pady=20)
        grid.pack(fill=tk.X, padx=20)
        for i, (lbl, val) in enumerate(items):
            tk.Label(grid, text=lbl + ":", bg=PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"),
                     width=20, anchor="w").grid(row=i, column=0, sticky="w", pady=4, padx=(0, 12))
            tk.Label(grid, text=val, bg=PANEL, fg=MUTED, font=("Segoe UI", 9),
                     anchor="w").grid(row=i, column=1, sticky="w", pady=4)

        deps = [
            ("watchdog",  "watchdog",             WATCHDOG_AVAILABLE),
            ("plyer",     "plyer",                PLYER_AVAILABLE),
            ("easyocr",   "easyocr",              local_engine_installed("easyocr")),
            ("paddleocr", "paddlepaddle paddleocr", local_engine_installed("paddleocr")),
        ]
        dep_frame = tk.Frame(center, bg=PANEL, padx=24, pady=16)
        dep_frame.pack(fill=tk.X, padx=20, pady=(12, 0))
        tk.Label(dep_frame, text="Optional Dependencies", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 8))
        for name, install, ok in deps:
            r = tk.Frame(dep_frame, bg=PANEL)
            r.pack(fill=tk.X, pady=2)
            dot_color = SUCCESS if ok else MUTED
            tk.Label(r, text="●", bg=PANEL, fg=dot_color, font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(0, 8))
            tk.Label(r, text=name, bg=PANEL, fg=TEXT, font=("Consolas", 9), width=14, anchor="w").pack(side=tk.LEFT)
            tk.Label(r, text="installed" if ok else f"pip install {install}", bg=PANEL,
                     fg=SUCCESS if ok else MUTED, font=("Consolas", 9)).pack(side=tk.LEFT)
            if not ok and ENGINE_MANAGER_AVAILABLE:
                if name in ENGINE_LABELS:
                    _cmd = (lambda n=name: self._install_engine(n))
                else:
                    _cmd = (lambda n=name, spec=install: self._install_extra_dependency(n, spec))
                ttk.Button(r, text="⬇  Install", width=11, command=_cmd).pack(side=tk.RIGHT)

    # ------------------------------------------------------------------
    # STATUS / FOLDERS / DASHBOARD
    # ------------------------------------------------------------------
    def _set_status(self, text: str, dot_color: str = None):
        logging.info(text)
        try:
            if hasattr(self, "_status_var"):
                self.after(0, lambda t=text: self._status_var.set(t))
            if dot_color and hasattr(self, "_status_dot"):
                self.after(0, lambda c=dot_color: self._status_indicator.itemconfig(self._status_dot, fill=c))
        except Exception:
            pass

    def _open_folder(self, path):
        try:
            if not path:
                raise ValueError("No folder path provided")
            os.makedirs(path, exist_ok=True)
            if hasattr(os, "startfile"):
                os.startfile(path)  # Windows
            elif sys.platform == "darwin":
                import subprocess
                subprocess.Popen(["open", path])
            else:
                import subprocess
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            messagebox.showerror("Open Folder Failed", str(e))

    def _refresh_dashboard_stats(self):
        try:
            def cpdf(f):
                return len([x for x in os.listdir(f) if x.lower().endswith(".pdf")]) if f and os.path.exists(f) else 0
            v = {
                "waiting":   cpdf(self.dirs.get("scanned")),
                "processed": cpdf(self.dirs.get("processed")),
                "archived":  cpdf(self.dirs.get("archive") or self.dirs.get("archived")),
                "failed":    cpdf(self.dirs.get("failed")),
            }
            for attr, key in [
                ("_stat_waiting_var",   "waiting"),
                ("_stat_processed_var", "processed"),
                ("_stat_archived_var",  "archived"),
                ("_stat_failed_var",    "failed"),
            ]:
                if hasattr(self, attr):
                    getattr(self, attr).set(str(v[key]))
        except Exception as e:
            logging.error(f"Dashboard refresh failed: {e}")

    # ------------------------------------------------------------------
    # TREE EDITING (GENERIC)
    # ------------------------------------------------------------------
    def _make_tree_editable(self, tree, on_edit_callback=None, editable_cols=None, pre_edit_fn=None):
        """
        Make Treeview cells editable by double-clicking.

        Column 0 can be used for PDF preview when a preview callback
        is assigned to tree._edit_on_preview_cb.
        """

    def _make_tree_editable(self, tree, on_edit_callback=None, editable_cols=None, pre_edit_fn=None,
                             extra_menu_builder=None):
        """
        Make Treeview cells editable by double-clicking or right-clicking.

        Column 0 can be used for PDF preview when a preview callback
        is assigned to tree._edit_on_preview_cb.

        extra_menu_builder(menu, tree, row_id, col_id), if given, is called
        right before the right-click context menu is shown so a caller can
        append its own items (e.g. "Remove Entry") without affecting every
        other tree that shares this same editing behaviour.
        """

        tree._edit_editable_cols = editable_cols
        tree._edit_on_edit_cb = on_edit_callback
        tree._edit_pre_edit_fn = pre_edit_fn
        tree._edit_extra_menu_builder = extra_menu_builder
        tree._edit_entry = None
        tree._edit_active_row = None
        tree._edit_active_col = None

        def close_editor():
            entry = getattr(tree, "_edit_entry", None)
            if entry is not None:
                try:
                    if entry.winfo_exists():
                        entry.destroy()
                except Exception:
                    pass
            tree._edit_entry = None
            tree._edit_active_row = None
            tree._edit_active_col = None

        def open_editor(row_id, col_id, from_event="double"):
            """Open an inline Entry editor for a specific tree cell."""
            if not tree.winfo_exists():
                return

            ci = int(col_id[1:]) - 1

            # Column 0 = PDF preview when a preview callback exists.
            if ci == 0:
                preview_cb = getattr(tree, "_edit_on_preview_cb", None)
                if preview_cb is not None:
                    preview_cb(row_id)
                    return

            # Check whether this column is editable.
            if (
                tree._edit_editable_cols is not None
                and ci not in tree._edit_editable_cols
            ):
                return

            values = list(tree.item(row_id, "values"))
            if ci >= len(values):
                return

            current_value = str(values[ci])

            # If an editor is already open on the SAME cell, don't re-create it.
            if tree._edit_active_row == row_id and tree._edit_active_col == ci:
                entry = getattr(tree, "_edit_entry", None)
                if entry and entry.winfo_exists():
                    entry.focus_force()
                    return

            def create_editor():
                if not tree.winfo_exists():
                    return

                bbox = tree.bbox(row_id, col_id)
                if not bbox:
                    return

                close_editor()

                bx, by, bw, bh = bbox
                display_value = current_value

                if tree._edit_pre_edit_fn:
                    try:
                        display_value = tree._edit_pre_edit_fn(ci, current_value)
                    except Exception:
                        display_value = current_value

                var = tk.StringVar(value=display_value)

                entry = tk.Entry(
                    tree,
                    textvariable=var,
                    background=PANEL2,
                    foreground=TEXT,
                    insertbackground=TEXT,
                    relief="flat",
                    font=("Segoe UI", 9, "bold"),
                    bd=2,
                    highlightthickness=1,
                    highlightbackground=ACCENT,
                    highlightcolor=ACCENT,
                )

                tree._edit_entry = entry
                tree._edit_active_row = row_id
                tree._edit_active_col = ci

                entry.place(x=bx, y=by, width=bw, height=bh)
                entry.focus_force()
                entry.select_range(0, tk.END)

                finished = [False]

                def commit(event=None):
                    if finished[0]:
                        return "break"
                    finished[0] = True
                    new_value = var.get().strip()
                    close_editor()

                    if new_value == display_value:
                        return "break"
                    if new_value == "":
                        return "break"

                    callback = tree._edit_on_edit_cb
                    if callback:
                        callback(row_id, ci, current_value, new_value)
                    else:
                        values[ci] = new_value
                        tree.item(row_id, values=values)
                    return "break"

                def cancel(event=None):
                    finished[0] = True
                    close_editor()
                    return "break"

                entry.bind("<Return>", commit)
                entry.bind("<KP_Enter>", commit)
                entry.bind("<Escape>", cancel)

            tree.after_idle(create_editor)

        def on_double_click(event):
            if tree.identify("region", event.x, event.y) != "cell":
                return "break"
            col_id = tree.identify_column(event.x)
            row_id = tree.identify_row(event.y)
            if not row_id or not col_id:
                return "break"
            open_editor(row_id, col_id, from_event="double")
            return "break"

        def on_right_click(event):
            """Show a context menu on right-click with Edit / Preview options."""
            if tree.identify("region", event.x, event.y) != "cell":
                return
            col_id = tree.identify_column(event.x)
            row_id = tree.identify_row(event.y)
            if not row_id or not col_id:
                return

            ci = int(col_id[1:]) - 1
            menu = tk.Menu(tree, tearoff=0, bg=PANEL2, fg=TEXT,
                           activebackground=ACCENT, activeforeground="white",
                           font=("Segoe UI", 9))

            # Preview option for column 0
            preview_cb = getattr(tree, "_edit_on_preview_cb", None)
            if ci == 0 and preview_cb is not None:
                menu.add_command(label="👁 Preview PDF", command=lambda: preview_cb(row_id))
                menu.add_separator()

            # Edit option for editable columns
            is_editable = (
                tree._edit_editable_cols is None
                or ci in tree._edit_editable_cols
            )
            if is_editable:
                menu.add_command(label="✏️ Edit", command=lambda: open_editor(row_id, col_id, from_event="right"))
            else:
                menu.add_command(label="🔒 Read-only", state="disabled")

            extra_builder = getattr(tree, "_edit_extra_menu_builder", None)
            if callable(extra_builder):
                try:
                    extra_builder(menu, tree, row_id, col_id)
                except Exception:
                    logging.error("extra context-menu builder failed", exc_info=True)

            menu.tk_popup(event.x_root, event.y_root)

        # Use add="+" so this doesn't destroy any other Treeview bindings.
        tree.bind("<Double-1>", on_double_click, add="+")
        tree.bind("<Button-3>", on_right_click, add="+")   # Windows / Linux right-click
        tree.bind("<Button-2>", on_right_click, add="+")   # macOS right-click (Ctrl+click)

        # Clicking somewhere else finishes the edit cleanly.
        def click_elsewhere(event):
            entry = getattr(tree, "_edit_entry", None)
            if entry is None:
                return
            if event.widget is entry:
                return
            # Don't close if we're clicking on the same cell that has the editor.
            try:
                if tree.identify("region", event.x, event.y) == "cell":
                    rid = tree.identify_row(event.y)
                    cid = tree.identify_column(event.x)
                    if rid and cid:
                        ci = int(cid[1:]) - 1
                        if rid == tree._edit_active_row and ci == tree._edit_active_col:
                            return
            except Exception:
                pass
            tree.after_idle(close_editor)

        tree.bind("<Button-1>", click_elsewhere, add="+")

    # ------------------------------------------------------------------
    # DRY RUN PREVIEW DIALOG
    # ------------------------------------------------------------------
    def _show_dry_run_preview(self, previews) -> bool:
        """
        Show a preview dialog listing proposed file renames before any
        files are actually moved/renamed. Returns True if the user
        clicks Proceed, False if they cancel.
        """
        win = tk.Toplevel(self)
        win.title("Dry Run Preview")
        win.geometry("1200x700")
        win.configure(bg=BG)

        tk.Label(win,
                 text="Dry Run Preview — Review Before Processing",
                 bg=BG, fg=TEXT, font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=20, pady=(16, 2))
        tk.Label(win,
                 text="Click  ✓ Proceed  to move files, or  ✕ Cancel  to abort. Orange rows have duplicate invoice warnings.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=20, pady=(0, 10))

        tf = tk.Frame(win, bg=BG)
        tf.pack(fill=tk.BOTH, expand=True, padx=16, pady=4)
        cols = ("Original File", "Proposed New Name", "Supplier", "Conf %", "GRN", "Invoice #", "Duplicate?")
        cw = {"Original File": 200, "Proposed New Name": 260, "Supplier": 180,
              "Conf %": 60, "GRN": 170, "Invoice #": 115, "Duplicate?": 175}
        tree = ttk.Treeview(tf, columns=cols, show="headings", height=14)
        for c in cols:
            tree.heading(c, text=c)
            tree.column(c, width=cw.get(c, 100), anchor="center", stretch=False)

        tree.tag_configure("dupe", foreground=WARNING)
        tree.tag_configure("ok",   foreground=SUCCESS)
        tree.tag_configure("err",  foreground=ERROR)

        ys = ttk.Scrollbar(tf, orient="vertical",   command=tree.yview)
        xs = ttk.Scrollbar(tf, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)
        self._bind_tree_mousewheel(tree)

        for p in previews:
            dupe = p.get("duplicate_warning", "")
            err  = "ERROR" in p.get("proposed_name", "")
            tag  = "err" if err else ("dupe" if dupe else "ok")
            conf_disp = f"{p.get('confidence', 0.0):.0f}%" if not err else "—"
            tree.insert("", "end", tags=(tag,), values=(
                p.get("file", ""),
                p.get("proposed_name", ""),
                p.get("supplier", ""),
                conf_disp,
                p.get("grn", ""),
                p.get("invoice", ""),
                dupe or "—",
            ))

        result = [False]

        def proceed():
            result[0] = True
            win.destroy()

        btn_row = tk.Frame(win, bg=BG)
        btn_row.pack(fill=tk.X, padx=20, pady=(8, 16))
        ttk.Button(btn_row, text="✓  Proceed — Move Files Now", style="Accent.TButton",
                   command=proceed).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Button(btn_row, text="✕  Cancel", command=win.destroy).pack(side=tk.LEFT)
        n_dupe = sum(1 for p in previews if p.get("duplicate_warning"))
        tk.Label(btn_row, text=f"{len(previews)} files  ·  {n_dupe} duplicate invoice(s)",
                 bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(side=tk.RIGHT, padx=8)

        win.wait_window()
        return result[0]

    # ------------------------------------------------------------------
    # PDF PREVIEW PANEL
    # ------------------------------------------------------------------
    def _show_pdf_preview(self, pdf_path: str):
        if not pdf_path or not os.path.exists(pdf_path):
            messagebox.showwarning("Preview", f"File not found:\n{pdf_path or '(no path)'}")
            return

        win = tk.Toplevel(self)
        win.title(f"Preview — {os.path.basename(pdf_path)}")
        win.geometry("900x980")
        win.configure(bg=BG)

        top = tk.Frame(win, bg=PANEL2, height=46)
        top.pack(fill=tk.X)
        top.pack_propagate(False)

        tk.Label(
            top,
            text=os.path.basename(pdf_path),
            bg=PANEL2,
            fg=TEXT,
            font=("Segoe UI", 10, "bold"),
        ).pack(side=tk.LEFT, padx=14, pady=12)

        page_info_var = tk.StringVar(value="Loading...")
        tk.Label(
            top,
            textvariable=page_info_var,
            bg=PANEL2,
            fg=MUTED,
            font=("Segoe UI", 9),
        ).pack(side=tk.RIGHT, padx=14)

        toolbar = tk.Frame(win, bg=PANEL, height=42)
        toolbar.pack(fill=tk.X)
        toolbar.pack_propagate(False)

        canvas = tk.Canvas(win, bg=BG, highlightthickness=0, cursor="fleur")
        sb_y = ttk.Scrollbar(win, orient="vertical", command=canvas.yview)
        sb_x = ttk.Scrollbar(win, orient="horizontal", command=canvas.xview)
        canvas.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)

        sb_y.pack(side=tk.RIGHT, fill=tk.Y)
        sb_x.pack(side=tk.BOTTOM, fill=tk.X)
        canvas.pack(fill=tk.BOTH, expand=True)

        state = {
            "doc": None,
            "page_index": 0,
            "page_count": 0,
            "zoom": 1.0,
            "fit_zoom": 1.0,
            "photo": None,
            "page_width": 0,
            "page_height": 0,
        }

        def render_page():
            try:
                if not state["doc"]:
                    return
                page = state["doc"][state["page_index"]]
                z = max(0.2, state["zoom"])
                pix = page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
                img_data = pix.tobytes("png")
                pil_img = PILImage.open(io.BytesIO(img_data))
                photo = ImageTk.PhotoImage(pil_img)

                canvas.delete("all")
                canvas.create_image(0, 0, anchor="nw", image=photo, tags="page")
                canvas.configure(scrollregion=(0, 0, pil_img.width, pil_img.height))

                state["photo"] = photo
                state["page_width"] = pil_img.width
                state["page_height"] = pil_img.height

                zoom_pct = int(state["zoom"] * 100)
                page_info_var.set(
                    f"Page {state['page_index'] + 1} of {state['page_count']}  ·  {zoom_pct}%  ·  {pil_img.width}x{pil_img.height}"
                )
            except Exception as e:
                page_info_var.set(f"Error: {e}")

        def fit_to_window():
            if not state["doc"]:
                return
            page = state["doc"][state["page_index"]]
            rect = page.rect
            canvas.update_idletasks()
            cw = max(canvas.winfo_width() - 20, 100)
            ch = max(canvas.winfo_height() - 20, 100)
            zx = cw / rect.width
            zy = ch / rect.height
            state["fit_zoom"] = min(zx, zy)
            state["zoom"] = state["fit_zoom"]
            render_page()

        def zoom_in():
            state["zoom"] = min(state["zoom"] * 1.2, 5.0)
            render_page()

        def zoom_out():
            state["zoom"] = max(state["zoom"] / 1.2, 0.2)
            render_page()

        def zoom_100():
            state["zoom"] = 1.0
            render_page()

        def zoom_fit():
            fit_to_window()

        def prev_page():
            if state["doc"] and state["page_index"] > 0:
                state["page_index"] -= 1
                fit_to_window()

        def next_page():
            if state["doc"] and state["page_index"] < state["page_count"] - 1:
                state["page_index"] += 1
                fit_to_window()

        ttk.Button(toolbar, text="Fit", command=zoom_fit).pack(side=tk.LEFT, padx=(10, 4), pady=7)
        ttk.Button(toolbar, text="100%", command=zoom_100).pack(side=tk.LEFT, padx=4, pady=7)
        ttk.Button(toolbar, text="-", command=zoom_out, width=3).pack(side=tk.LEFT, padx=4, pady=7)
        ttk.Button(toolbar, text="+", command=zoom_in, width=3).pack(side=tk.LEFT, padx=4, pady=7)
        ttk.Button(toolbar, text="Prev", command=prev_page).pack(side=tk.LEFT, padx=(18, 4), pady=7)
        ttk.Button(toolbar, text="Next", command=next_page).pack(side=tk.LEFT, padx=4, pady=7)

        def start_pan(event):
            canvas.scan_mark(event.x, event.y)

        def do_pan(event):
            canvas.scan_dragto(event.x, event.y, gain=1)

        canvas.bind("<ButtonPress-1>", start_pan)
        canvas.bind("<B1-Motion>", do_pan)

        def on_mousewheel(event):
            if event.state & 0x0004:  # Ctrl pressed
                if event.delta > 0:
                    zoom_in()
                else:
                    zoom_out()
                return "break"
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            return "break"

        canvas.bind("<MouseWheel>", on_mousewheel)
        canvas.bind("<Button-4>", lambda e: (canvas.yview_scroll(-1, "units"), "break"))
        canvas.bind("<Button-5>", lambda e: (canvas.yview_scroll(1, "units"), "break"))

        def load():
            try:
                doc = fitz.open(pdf_path)
                state["doc"] = doc
                state["page_count"] = doc.page_count
                win.after(0, fit_to_window)
            except Exception as e:
                win.after(0, lambda: page_info_var.set(f"Error: {e}"))

        def on_resize(_event):
            if state["doc"]:
                pass

        def _on_preview_close():
            """Ensure the fitz document is closed when the preview window is destroyed."""
            doc = state.get("doc")
            if doc:
                try:
                    doc.close()
                except Exception:
                    pass
                state["doc"] = None
            win.destroy()

        canvas.bind("<Configure>", on_resize)
        win.protocol("WM_DELETE_WINDOW", _on_preview_close)
        threading.Thread(target=load, daemon=True).start()

    def _on_rename_preview_by_rowid(self, row_id: str):
        result   = self._rename_row_map.get(row_id, {})
        pdf_path = result.get("dest_path", "")
        if not pdf_path or not os.path.exists(pdf_path):
            fname    = result.get("file", "")
            pdf_path = os.path.join(self.dirs.get("scanned", ""), fname)
        self._show_pdf_preview(pdf_path)

    def _on_rename_tree_double_click(self, event):
        if self._rename_tree.identify("region", event.x, event.y) != "cell":
            return
        col_id = self._rename_tree.identify_column(event.x)
        ci = int(col_id[1:]) - 1
        if ci != 0:
            return
        row_id = self._rename_tree.identify_row(event.y)
        if not row_id:
            return
        result = self._rename_row_map.get(row_id, {})
        pdf_path = result.get("dest_path", "")
        if not pdf_path or not os.path.exists(pdf_path):
            fname = result.get("file", "")
            pdf_path = os.path.join(self.dirs.get("scanned", ""), fname)
        self._show_pdf_preview(pdf_path)

    def _on_dispatch_tree_double_click(self, event):
        if self._dispatch_tree.identify("region", event.x, event.y) != "cell":
            return
        col_id = self._dispatch_tree.identify_column(event.x)
        ci = int(col_id[1:]) - 1
        if ci != 0:
            return
        row_id = self._dispatch_tree.identify_row(event.y)
        if not row_id:
            return
        result = self._dispatch_row_map.get(row_id, {})
        pdf_path = result.get("raw_path", "")
        folder = self._dispatch_folder_var.get().strip() or self.dirs.get("processed", "")
        if not pdf_path or not os.path.exists(pdf_path):
            fname = result.get("file", "")
            pdf_path = os.path.join(folder, fname)
        self._show_pdf_preview(pdf_path)

    # ------------------------------------------------------------------
    # RETRY FAILED FILES
    # ------------------------------------------------------------------
    def _retry_failed_files(self):
        failed_dir  = self.dirs.get("failed", "")
        scanned_dir = self.dirs.get("scanned", "")
        if not os.path.isdir(failed_dir):
            messagebox.showinfo("Retry Failed", "FAILED folder not found.")
            return
        failed_pdfs = [f for f in os.listdir(failed_dir) if f.lower().endswith(".pdf")]
        if not failed_pdfs:
            messagebox.showinfo("Retry Failed", "No PDF files in FAILED folder.")
            return
        preview = "\n".join(failed_pdfs[:10]) + ("\n..." if len(failed_pdfs) > 10 else "")
        if not messagebox.askyesno(
            "Retry Failed Files",
            f"Move {len(failed_pdfs)} file(s) from FAILED → SCANNED and re-process?\n\n{preview}",
        ):
            return
        moved = 0
        for fn in failed_pdfs:
            try:
                _safe_file_move(os.path.join(failed_dir, fn), os.path.join(scanned_dir, fn))
                moved += 1
            except Exception as e:
                logging.error(f"Retry move failed [{fn}]: {e}")
        self._set_status(f"Moved {moved} file(s) to SCANNED — starting rename...", SUCCESS)
        self._refresh_dashboard_stats()
        self.after(300, self._start_rename_worker)

    # ------------------------------------------------------------------
    # SETTINGS CALLBACKS
    # ------------------------------------------------------------------
    def _on_engine_changed(self):
        """Called by every engine radio button (mode, online engine, local engine).

        Persists the whole choice, refreshes every badge and warns when the
        selected engine is not installed on this machine.
        """
        # `_engine_var` is the Settings radio-button variable while
        # `_local_engine_var` is the live GRN Dispatch variable. They must be
        # synchronized immediately; otherwise `_persist_ingest_modes()` can
        # overwrite a newly selected PaddleOCR/EasyOCR choice with stale
        # Tesseract and the header will continue to show Tesseract.
        selected_local = self._engine_var.get()
        self._local_engine_var.set(selected_local)
        self.cfg.setdefault("app_settings", {})["ocr_engine"] = selected_local
        mode = self._grn_engine_var.get()
        if mode == GRN_ONLINE:
            self._grn_choice_var.set(grn_choice_label(GRN_ONLINE, self._online_engine_var.get()))
        else:
            self._grn_choice_var.set(grn_choice_label(GRN_OFFLINE, self._engine_var.get()))
        self._persist_ingest_modes()
        self._refresh_engine_badge()
        self._refresh_engine_rows()
        self._refresh_processing_mode_badge()
        self._warn_if_engine_missing()

    # ------------------------------------------------------------------
    # ENGINE STATUS / INSTALLATION
    # ------------------------------------------------------------------
    def _refresh_engine_rows(self):
        """Re-check every local engine and repaint its status/Install button."""
        if not hasattr(self, "_engine_status_lbls"):
            return
        # During shutdown the <Destroy> handlers of open dialogs can still fire,
        # so never touch widgets once the main window is gone.
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        for key, lbl in self._engine_status_lbls.items():
            try:
                installed = local_engine_installed(key)
                if ENGINE_MANAGER_AVAILABLE:
                    text = oem.engine_status_text(key)
                else:
                    text = "Installed" if installed else "Not installed"
                lbl.configure(text=text, fg=SUCCESS if installed else ERROR)
                btn = self._engine_install_btns.get(key)
                if btn is not None:
                    btn.configure(
                        text=("↻  Reinstall" if installed else "⬇  Install"),
                        state="disabled" if self._engine_install_running else "normal",
                    )
            except Exception as e:
                logging.debug(f"Engine row refresh failed [{key}]: {e}")

        # Online engine row
        if hasattr(self, "_online_engine_status_lbl"):
            if AI_MATCHER_AVAILABLE:
                self._online_engine_status_lbl.configure(
                    text="OCR.space API module loaded — ONLINE mode is ready "
                         "(needs an internet connection and a valid API key).",
                    fg=SUCCESS,
                )
            else:
                self._online_engine_status_lbl.configure(
                    text="⚠  ai_supplier_matcher.py could not be loaded — ONLINE mode is unavailable.",
                    fg=ERROR,
                )
        # Selected engine warning (status bar only, no popup while painting)
        self._warn_if_engine_missing(show_dialog=False)

    def _engine_install_log_line(self, text: str):
        """Append one line to the Settings install log (thread-safe via after)."""
        # Echo to the console (PowerShell/cmd) so progress is visible there too.
        try:
            print(f"[SETUP] {text}", flush=True)
        except Exception:
            pass

        def _do(msg=text):
            try:
                widget = getattr(self, "_engine_install_log", None)
                if widget is None:
                    return
                widget.configure(state="normal")
                widget.insert(tk.END, msg + "\n")
                widget.see(tk.END)
                widget.configure(state="disabled")
            except Exception:
                pass
        try:
            self.after(0, _do)
        except Exception:
            pass

    def _engine_install_status_var_set(self, text: str):
        try:
            self.after(0, lambda t=text: self._engine_install_status_var.set(t))
        except Exception:
            pass

    def _install_engine(self, key):
        """Install one OCR engine (pip packages + the system program if needed)."""
        if self._engine_install_running:
            messagebox.showinfo(
                "Installation Running",
                "Another engine installation is already running. Please wait for it to finish.",
            )
            return
        if not ENGINE_MANAGER_AVAILABLE:
            self._install_engine_legacy_hint(key)
            return

        label = ENGINE_LABELS.get(key, key)
        spec = oem.ENGINE_SPECS.get(key, {})
        already = local_engine_installed(key)
        size = spec.get("size_mb", 0)
        size_text = f"\n\nDownload size: about {size} MB." if size else ""
        extra = ("\nIt is already installed — this will reinstall / upgrade it." if already else "")
        if not messagebox.askyesno(
            "Install " + label,
            f"Install {label} now?\n\n"
            f"Packages: {', '.join(spec.get('packages', [])) or '—'}\n"
            f"Also required: {spec.get('system', '—')}{size_text}\n\n"
            f"The download runs in the background and its progress is shown in "
            f"Settings → OCR Engines.{extra}",
        ):
            return

        self._engine_install_running = True
        self._engine_install_status_var.set(f"Installing {label} … this can take a few minutes.")
        self._engine_install_log_line(f"===== Installing {label} =====")
        self._refresh_engine_rows()
        self._set_status(f"Installing {label}…", ACCENT)
        cancel = threading.Event()
        self._engine_install_cancel = cancel

        def worker():
            try:
                ok, msg = oem.install_engine(key, log=self._engine_install_log_line, cancel=cancel)
            except Exception as exc:
                ok, msg = False, f"Unexpected installer error: {exc}"

            def finish():
                self._engine_install_running = False
                oem.invalidate_cache()
                self._refresh_engine_rows()
                if ok:
                    self._engine_install_status_var.set(f"{label} installed successfully.")
                    self._engine_install_log_line(f"===== {label} ready =====")
                    self._set_status(f"{label} installed successfully.", SUCCESS)
                    try:
                        self.cfg.setdefault("app_settings", {}).setdefault(
                            "engine_setup_state", {}
                        )[key] = {
                            "installed": True,
                            "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        }
                        self._save_config()
                    except Exception:
                        pass
                    messagebox.showinfo("Installation Complete", msg)
                else:
                    self._engine_install_status_var.set(f"{label} installation failed.")
                    self._engine_install_log_line(f"!! {msg}")
                    self._set_status(f"{label} installation failed.", ERROR)
                    messagebox.showerror("Installation Failed", msg)

            try:
                self.after(0, finish)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True, name=f"install-{key}").start()

    def _install_all_missing_engines(self):
        """Install every local engine that is currently missing."""
        if not ENGINE_MANAGER_AVAILABLE:
            messagebox.showwarning(
                "Installer Unavailable",
                "ocr_engine_manager.py was not found, so the automatic installer is unavailable.",
            )
            return
        missing = [k for k in local_engine_keys() if not local_engine_installed(k)]
        if not missing:
            messagebox.showinfo("OCR Engines", "Every local OCR engine is already installed.")
            return
        names = ", ".join(ENGINE_LABELS.get(k, k) for k in missing)
        if not messagebox.askyesno(
            "Install All Missing Engines",
            f"Install these OCR engines now?\n\n{names}\n\n"
            f"This can take a while (EasyOCR downloads PyTorch, ~2 GB). "
            f"Progress is shown below and the application stays usable.",
        ):
            return

        self._engine_install_running = True
        self._refresh_engine_rows()
        cancel = threading.Event()
        self._engine_install_cancel = cancel

        def worker():
            results = []
            for key in missing:
                label = ENGINE_LABELS.get(key, key)
                self._engine_install_status_var_set(f"Installing {label} …")
                self._engine_install_log_line(f"===== Installing {label} =====")
                try:
                    ok, msg = oem.install_engine(
                        key, log=self._engine_install_log_line, cancel=cancel
                    )
                except Exception as exc:
                    ok, msg = False, str(exc)
                results.append((label, ok, msg))
            summary = "\n".join(f"{'✓' if ok else '✗'} {lbl}" for lbl, ok, _m in results)

            def finish():
                self._engine_install_running = False
                oem.invalidate_cache()
                self._refresh_engine_rows()
                self._engine_install_status_var.set("Finished installing missing engines.")
                self._set_status("Engine installation finished.", SUCCESS)
                messagebox.showinfo("OCR Engines", summary or "Nothing installed.")

            try:
                self.after(0, finish)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True, name="install-all-engines").start()

    # ------------------------------------------------------------------
    # FIRST-RUN ENGINE SETUP WIZARD
    # ------------------------------------------------------------------
    def _engine_setup_required(self) -> bool:
        """True when the first-time engine setup has never been completed."""
        if not ENGINE_MANAGER_AVAILABLE:
            return False
        try:
            return oem.first_run_needed(self.cfg)
        except Exception:
            return False

    def _run_engine_setup_wizard(self, first_run: bool = False):
        """Ask which OCR engines to install, then install the chosen ones.

        Runs once on the very first start (before the auto-ingest watcher is
        started) and can be re-opened any time from Settings. The answers are
        remembered, so a skipped engine is never asked about again.
        """
        if not ENGINE_MANAGER_AVAILABLE:
            if not first_run:
                messagebox.showwarning(
                    "Setup Unavailable",
                    "ocr_engine_manager.py was not found, so automatic setup is unavailable.",
                )
            return

        win = tk.Toplevel(self)
        win.title("First-Time Setup — OCR Engines")
        win.configure(bg=BG)
        win.transient(self)
        win.resizable(False, False)
        try:
            win.grab_set()
        except Exception:
            pass

        tk.Label(
            win, text="Which OCR engines should be installed?", bg=BG, fg=TEXT,
            font=("Segoe UI", 14, "bold"),
        ).pack(anchor="w", padx=22, pady=(20, 4))
        tk.Label(
            win,
            text=("Everything is installed automatically from here. Tick only what you need — "
                  "your choice is remembered, so you will not be asked again.\n"
                  "More engines can be installed later: Settings → OCR Engines → Install."),
            bg=BG, fg=MUTED, font=("Segoe UI", 9), justify="left",
        ).pack(anchor="w", padx=22, pady=(0, 14))

        tk.Label(
            win, text=f"Running on Python {sys.version.split()[0]}  —  {sys.executable}",
            bg=BG, fg=MUTED, font=("Segoe UI", 8), wraplength=640, justify="left",
        ).pack(anchor="w", padx=22, pady=(0, 8))
        _wrap = tk.Frame(win, bg=PANEL)
        _wrap.pack(fill=tk.X, padx=22)
        _cv = tk.Canvas(_wrap, bg=PANEL, highlightthickness=0)
        _vsb = ttk.Scrollbar(_wrap, orient="vertical", command=_cv.yview)
        _cv.configure(yscrollcommand=_vsb.set)
        _vsb.pack(side=tk.RIGHT, fill=tk.Y)
        _cv.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        body = tk.Frame(_cv, bg=PANEL, padx=18, pady=14)
        _cv.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda e: _cv.configure(scrollregion=_cv.bbox("all")))
        _cv.bind("<Enter>", lambda e: _cv.bind_all(
            "<MouseWheel>", lambda ev: _cv.yview_scroll(int(-ev.delta / 120), "units")))
        _cv.bind("<Leave>", lambda e: _cv.unbind_all("<MouseWheel>"))

        core_missing = oem.missing_core_dependencies()
        core_var = tk.BooleanVar(value=bool(core_missing))
        if core_missing:
            names = ", ".join(lbl for _m, _p, lbl, _r in core_missing)
            tk.Label(
                body, text="Required application libraries", bg=PANEL, fg=TEXT,
                font=("Segoe UI", 10, "bold"),
            ).pack(anchor="w")
            ttk.Checkbutton(
                body,
                text=f"Install missing application libraries  ({names})",
                variable=core_var, style="TCheckbutton",
            ).pack(anchor="w", pady=(2, 8))
        else:
            tk.Label(
                body, text="✓  All required application libraries are already installed.",
                bg=PANEL, fg=SUCCESS, font=("Segoe UI", 9, "bold"),
            ).pack(anchor="w", pady=(0, 8))

        tk.Label(
            body, text="OCR engines", bg=PANEL, fg=TEXT, font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w")

        engine_vars = {}
        for key in local_engine_keys():
            spec = oem.ENGINE_SPECS.get(key, {})
            installed = local_engine_installed(key)
            var = tk.BooleanVar(value=installed)
            engine_vars[key] = var
            row = tk.Frame(body, bg=PANEL)
            row.pack(fill=tk.X, pady=4)
            ttk.Checkbutton(
                row, text=str(spec.get("label", key)), variable=var, style="TCheckbutton",
            ).pack(anchor="w")
            state = "already installed" if installed else f"~{spec.get('size_mb', 0)} MB download"
            tk.Label(
                row,
                text=f"    {spec.get('description', '')}  [{state}]",
                bg=PANEL, fg=MUTED, font=("Segoe UI", 8), wraplength=620, justify="left",
            ).pack(anchor="w")

        online_var = tk.BooleanVar(value=True)
        orow = tk.Frame(body, bg=PANEL)
        orow.pack(fill=tk.X, pady=(6, 0))
        ttk.Checkbutton(
            orow, text="OCR.space (ONLINE mode support)", variable=online_var,
            style="TCheckbutton",
        ).pack(anchor="w")
        tk.Label(
            orow,
            text=f"    {oem.ENGINE_SPECS.get('ocr_space', {}).get('description', '')}",
            bg=PANEL, fg=MUTED, font=("Segoe UI", 8), wraplength=620, justify="left",
        ).pack(anchor="w")

        # Cap the list height so the window (and its buttons) always fit the screen.
        win.update_idletasks()
        _max_h = max(200, win.winfo_screenheight() - 420)
        _cv.configure(width=body.winfo_reqwidth(),
                      height=min(body.winfo_reqheight(), _max_h))

        status_var = tk.StringVar(value="")
        tk.Label(win, textvariable=status_var, bg=BG, fg=ACCENT,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=22, pady=(10, 0))

        btns = tk.Frame(win, bg=BG)
        btns.pack(fill=tk.X, padx=22, pady=(10, 18))
        cancel_event = threading.Event()

        def _finish_setup(installed_keys):
            """Remember the answers so this dialog never appears again."""
            try:
                oem.invalidate_cache()
                oem.mark_setup_done(self.cfg, installed_keys)
                # Keep the concrete engine selections valid: if the configured
                # local engine is missing but another one was installed, switch.
                s = self.cfg.setdefault("app_settings", {})
                if not local_engine_installed(s.get("local_ocr_engine", "tesseract")):
                    for cand in local_engine_keys():
                        if local_engine_installed(cand):
                            s["local_ocr_engine"] = cand
                            s["ocr_engine"] = cand
                            try:
                                self._local_engine_var.set(cand)
                                self._engine_var.set(cand)
                            except Exception:
                                pass
                            break
                self._save_config()
            except Exception as e:
                logging.error(f"Could not persist engine setup: {e}", exc_info=True)
            self._refresh_engine_rows()
            self._refresh_engine_badge()
            self._refresh_processing_mode_badge()

        def _start_install():
            wanted = [
                k for k, v in engine_vars.items()
                if v.get() and not local_engine_installed(k)
            ]
            core_pkgs = oem.core_package_names(core_missing) if core_var.get() else []
            if not wanted and not core_pkgs:
                _finish_setup([])
                win.destroy()
                if not first_run:
                    messagebox.showinfo(
                        "OCR Engines",
                        "Nothing needed installing — every selected engine is already available.",
                    )
                return

            start_btn.configure(state="disabled")
            skip_btn.configure(state="disabled")
            status_var.set(
                "Installing… please wait (progress is also written to the Settings install log)."
            )

            def worker():
                installed = []
                if core_pkgs:
                    self._engine_install_log_line("===== Installing application libraries =====")
                    ok, msg = oem.install_packages(
                        core_pkgs, log=self._engine_install_log_line, cancel=cancel_event
                    )
                    self._engine_install_log_line(("✓ " if ok else "✗ ") + msg.splitlines()[0])
                for key in wanted:
                    label = ENGINE_LABELS.get(key, key)
                    self._engine_install_status_var_set(f"Installing {label} …")
                    self._engine_install_log_line(f"===== Installing {label} =====")
                    try:
                        ok, _msg = oem.install_engine(
                            key, log=self._engine_install_log_line, cancel=cancel_event
                        )
                    except Exception as exc:
                        ok = False
                        self._engine_install_log_line(f"!! {exc}")
                    if ok:
                        installed.append(key)

                def done():
                    _finish_setup(installed)
                    ok_names = ", ".join(ENGINE_LABELS.get(k, k) for k in installed)
                    status_var.set(
                        "Setup complete." + (f" Installed: {ok_names}." if ok_names else "")
                    )
                    self._engine_install_status_var.set("First-time setup finished.")
                    win.destroy()
                    if not first_run:
                        messagebox.showinfo(
                            "First-Time Setup",
                            "Engine setup finished.\n\n"
                            + (f"Installed: {ok_names}\n" if ok_names else "")
                            + "More engines can be installed any time from "
                              "Settings → OCR Engines.",
                        )

                try:
                    self.after(0, done)
                except Exception:
                    pass

            threading.Thread(target=worker, daemon=True, name="first-run-setup").start()

        def _skip_all():
            cancel_event.set()
            _finish_setup([])
            win.destroy()

        start_btn = ttk.Button(
            btns, text="⬇  Install selected", style="Accent.TButton", command=_start_install,
        )
        start_btn.pack(side=tk.RIGHT)
        skip_btn = ttk.Button(
            btns,
            text=("Continue without installing" if first_run else "Cancel"),
            command=_skip_all,
        )
        skip_btn.pack(side=tk.RIGHT, padx=(0, 8))
        win.protocol("WM_DELETE_WINDOW", _skip_all)

        # Whatever closes this window (button, title-bar X, or the application
        # shutting down) the answers are remembered, so the setup question is
        # only ever asked once.
        _setup_finalised = {"done": False}

        def _finalise_once(event=None):
            # <Destroy> also fires for every CHILD widget as the window is torn
            # down, so only react when the window itself is the target —
            # otherwise the refresh touches widgets that no longer exist.
            if event is not None and getattr(event, "widget", None) is not win:
                return
            if _setup_finalised["done"]:
                return
            _setup_finalised["done"] = True
            try:
                _finish_setup([])
            except Exception as e:
                # The application may already be shutting down; the answers were
                # written to config.json when they were given, so failing here
                # must never break the exit path.
                logging.debug(f"Engine setup finalisation skipped during teardown: {e}")

        win.bind("<Destroy>", _finalise_once)

        win.update_idletasks()
        try:
            x = self.winfo_rootx() + max(0, (self.winfo_width() - win.winfo_width()) // 2)
            y = self.winfo_rooty() + max(0, (self.winfo_height() - win.winfo_height()) // 3)
            win.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def _install_engine_legacy_hint(self, key):
        """Manual instructions shown when the automatic installer is missing."""
        messagebox.showinfo(
            "Install Engine",
            f"To install {ENGINE_LABELS.get(key, key)}, run:\n\n"
            f"  {ENGINE_INSTALL.get(key, '')}",
        )

    def _install_extra_dependency(self, name: str, pip_spec: str):
        """Install a small optional dependency (watchdog / plyer) in the background."""
        if not ENGINE_MANAGER_AVAILABLE:
            messagebox.showinfo("Install", f"Run manually:\n\n  pip install {pip_spec}")
            return
        if self._engine_install_running:
            messagebox.showinfo(
                "Installation Running",
                "Another installation is already running. Please wait for it to finish.",
            )
            return
        if not messagebox.askyesno(
            "Install " + name,
            f"Install {name} now?\n\nCommand: pip install {pip_spec}\n\n"
            f"Progress is shown in Settings → OCR Engines.",
        ):
            return
        self._engine_install_running = True
        self._engine_install_status_var.set(f"Installing {name} …")
        self._engine_install_log_line(f"===== Installing {name} =====")

        def worker():
            try:
                ok, msg = oem.install_packages([pip_spec], log=self._engine_install_log_line)
            except Exception as exc:
                ok, msg = False, str(exc)

            def finish():
                self._engine_install_running = False
                self._refresh_engine_rows()
                if ok:
                    self._engine_install_status_var.set(f"{name} installed — restart to activate.")
                    self._set_status(
                        f"{name} installed — restart the application to use it.", SUCCESS
                    )
                    messagebox.showinfo(
                        "Installation Complete",
                        f"{name} was installed.\n\nRestart the application so it can be loaded.",
                    )
                else:
                    self._engine_install_status_var.set(f"{name} installation failed.")
                    messagebox.showerror("Installation Failed", msg)

            try:
                self.after(0, finish)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True, name=f"install-{name}").start()


    def _browse_base_folder(self):
        f = filedialog.askdirectory(initialdir=self._base_var.get() or self.dirs["base"])
        if f:
            self._base_var.set(f)

    def _apply_settings(self):
        s = self.cfg.setdefault("app_settings", {})
        s["ocr_engine"] = self._engine_var.get()
        s["extract_text_before_ocr"] = bool(self._extract_text_var.get())
        s["extraction_source"] = self._extraction_source_var.get()
        s["enhance_images"] = bool(self._enhance_var.get())
        s["ocr_fallback_to_tesseract"] = bool(self._fallback_var.get())
        s["enable_multi_threading"] = bool(self._threads_var.get())
        s["max_threads"] = int(self._max_threads_var.get())
        s["fuzzy_match_threshold"] = int(self._threshold_var.get())
        s["image_scale_factor"] = int(self._scale_var.get())
        s["processing_mode"] = self._processing_mode_var.get()
        s["dry_run"] = bool(self._dry_run_var.get())
        s["desktop_notifications"] = bool(self._notify_var.get())
        s["grn_processing_engine"] = self._grn_engine_var.get()
        s["local_ocr_engine"] = self._engine_var.get()
        s["ocr_engine"] = self._engine_var.get()
        try:
            s["online_ocr_engine"] = int(self._online_engine_var.get())
        except (TypeError, ValueError):
            s["online_ocr_engine"] = 2
        s["auto_ingest_enabled"] = bool(self._auto_ingest_enabled_var.get())
        # Each mode has its own auto-ingest switch; a PDF is only auto-ingested
        # when its own mode is active, so enabling one never enables the other.
        s["auto_ingest_offline"] = bool(self._watcher_offline_var.get())
        s["auto_ingest_api"] = bool(self._watcher_api_var.get())
        s["auto_ingest_enabled"] = bool(s["auto_ingest_offline"] or s["auto_ingest_api"])
        s["auto_ingest_watcher"] = s["auto_ingest_enabled"]
        s["confidence_warn_threshold"] = int(self._conf_threshold_var.get())
        s["page_scan_region_enabled"] = bool(self._page_scan_enabled_var.get())
        s["page_scan_region_percent"] = int(self._page_scan_percent_var.get())
        s["ocr_mode"] = self._ocr_mode_var.get()
        s["supplier_extraction_method"] = self._supplier_method_var.get()
        s["ocr_word_correction_enabled"] = bool(self._ocr_word_correction_var.get())
        s["smart_cross_match_enabled"] = bool(self._smart_cross_match_var.get())

        if hasattr(self, "_supplier_strategy_var"):
            s["supplier_match_strategy"] = self._supplier_strategy_var.get()

        if hasattr(self, "_ocr_mode_info_var"):
            self._ocr_mode_info_var.set(self._ocr_mode_label(s["ocr_mode"]))

        self.cfg.setdefault("folders", {})["base"] = self._base_var.get()

        ai = self.cfg.setdefault("ai_settings", {})
        ai["enabled"] = bool(self._ai_enabled_var.get())
        ai["provider"] = self._ai_provider_var.get()
        ai["model"] = self._ai_model_var.get().strip()
        ai["api_key"] = self._ai_key_var.get().strip()
        ai["custom_base_url"] = self._ai_url_var.get().strip()
        ai["timeout_seconds"] = int(self._ai_timeout_var.get())

        ocr_space = self.cfg.setdefault("ocr_space", {})
        ocr_space["api_key"] = self._ocr_space_key_var.get().strip() or "K88109865088957"
        ocr_space["language"] = "eng"
        ocr_space["isOverlayRequired"] = False
        ocr_space["detectOrientation"] = True
        ocr_space["scale"] = True
        try:
            ocr_space["OCREngine"] = int(self._online_engine_var.get())
        except (TypeError, ValueError):
            ocr_space["OCREngine"] = int(self._ocr_space_engine_var.get())
        ocr_space["isTable"] = False
        ocr_space["filetype"] = "PDF"
        ocr_space["timeout_seconds"] = 30
        try:
            ocr_space["max_upload_mb"] = float(self._ocr_space_max_mb_var.get())
        except Exception:
            ocr_space["max_upload_mb"] = 1.0

        self._save_config()
        self.dirs = self._resolve_dirs()
        self._ensure_dirs()
        self._configure_tesseract()
        self._refresh_engine_badge()
        self._refresh_dashboard_stats()

        if AI_MATCHER_AVAILABLE:
            try:
                from ai_supplier_matcher import AISupplierMatcher
                self._ai_matcher = AISupplierMatcher(self.cfg, logger_func=self._append_ai_log)
                self._append_ai_log("AI matcher reloaded with latest settings.")
            except Exception as e:
                logging.warning(f"AI matcher reload failed: {e}")
                self._append_ai_log(f"AI matcher reload failed: {e}")

        messagebox.showinfo("Settings Saved", "All settings saved successfully.")

    def _reload_settings(self):
        self.cfg = self._load_config()
        self.dirs = self._resolve_dirs()
        self._ensure_dirs()
        self._configure_tesseract()
        self._refresh_engine_badge()
        self._threads_var.set(self.cfg.get("app_settings", {}).get("enable_multi_threading", True))
        self._set_status("Configuration reloaded.")
        messagebox.showinfo("Reloaded", "Configuration reloaded from disk.")

    def _on_processing_mode_changed(self):
        mode = self._processing_mode_var.get()
        self.cfg.setdefault("app_settings", {})["processing_mode"] = mode
        self._save_config()

    def _on_parallel_toggle(self):
        val = bool(self._threads_var.get())
        self.cfg.setdefault("app_settings", {})["enable_multi_threading"] = val
        self._save_config()

    def _on_dry_run_toggle(self):
        val = bool(self._dry_run_var.get())
        self.cfg.setdefault("app_settings", {})["dry_run"] = val
        self._save_config()
        self._set_status("Dry Run ON — preview before committing." if val else "Dry Run OFF — files will be moved.")

    def _on_supplier_method_changed(self):
        method = self._supplier_method_var.get()
        self.cfg.setdefault("app_settings", {})["supplier_extraction_method"] = method
        self._save_config()

    def _on_notify_toggle(self):
        val = bool(self._notify_var.get())
        self.cfg.setdefault("app_settings", {})["desktop_notifications"] = val
        self._save_config()
        self._set_status("Desktop notifications: ON" if val else "Desktop notifications: OFF")

    def _ocr_mode_label(self, mode: str) -> str:
        return {
            "full": "Active: Full OCR — entire page scanned",
            "zone": "Active: Zone OCR — header region only (fast mode)",
        }.get(mode, "Active: Full OCR")

    def _on_ocr_mode_changed(self):
        mode = self._ocr_mode_var.get()
        self.cfg.setdefault("app_settings", {})["ocr_mode"] = mode
        if hasattr(self, "_ocr_mode_info_var"):
            self._ocr_mode_info_var.set(self._ocr_mode_label(mode))
        self._save_config()
        self._set_status(
            "OCR Mode set to ZONE (fast header scan)." if mode == "zone"
            else "OCR Mode set to FULL PAGE."
        )

    def _on_supplier_strategy_changed(self):
        strategy = self._supplier_strategy_var.get()
        self.cfg.setdefault("app_settings", {})["supplier_match_strategy"] = strategy
        self._save_config()
        self._set_status(f"Supplier match strategy set to: {STRATEGY_LABELS.get(strategy, strategy)}")

    def _on_ai_enabled_toggle(self):
        val = bool(self._ai_enabled_var.get())
        self.cfg.setdefault("ai_settings", {})["enabled"] = val
        self._save_config()
        self._set_status("AI matching: ON" if val else "AI matching: OFF")

    def _on_ai_provider_changed(self):
        provider = self._ai_provider_var.get()
        self.cfg.setdefault("ai_settings", {})["provider"] = provider
        # Auto-fill default model for selected provider
        if PROVIDERS and provider in PROVIDERS:
            default_model = PROVIDERS[provider].get("default_model", "")
            self._ai_model_var.set(default_model)
            self.cfg["ai_settings"]["model"] = default_model
        self._save_config()

    def _test_ai_connection(self):
        self._ai_status_var.set("Testing connection...")
        self._append_ai_log("Starting AI/OCR connection test...")

        if not AI_MATCHER_AVAILABLE or self._ai_matcher is None:
            self._ai_status_var.set("ai_supplier_matcher.py not available")
            self._append_ai_log("ai_supplier_matcher.py not available.")
            return

        def _run():
            try:
                self.cfg.setdefault("ai_settings", {}).update({
                    "enabled": bool(self._ai_enabled_var.get()),
                    "provider": self._ai_provider_var.get(),
                    "model": self._ai_model_var.get(),
                    "api_key": self._ai_key_var.get().strip(),
                    "custom_base_url": self._ai_url_var.get().strip(),
                    "timeout_seconds": int(self._ai_timeout_var.get()),
                })

                self.cfg.setdefault("ocr_space", {}).update({
                    "api_key": self._ocr_space_key_var.get().strip() or "K88109865088957",
                    "OCREngine": int(self._ocr_space_engine_var.get()),
                    "language": "eng",
                    "isOverlayRequired": False,
                    "detectOrientation": True,
                    "scale": True,
                    "isTable": False,
                    "filetype": "PDF",
                    "timeout_seconds": 30,
                })

                try:
                    max_mb = float(self._ocr_space_max_mb_var.get() or 1.0)
                except Exception:
                    max_mb = 1.0

                self.cfg["ocr_space"]["max_upload_mb"] = max_mb

                from ai_supplier_matcher import AISupplierMatcher
                self._ai_matcher = AISupplierMatcher(self.cfg, logger_func=self._append_ai_log)
                status_code, message = self._ai_matcher.test_connection()

            except Exception as e:
                status_code = STATUS_DISCONNECTED
                message = str(e)

            color_map = {
                STATUS_CONNECTED: SUCCESS,
                STATUS_DISCONNECTED: ERROR,
                STATUS_LOW_CREDIT: WARNING,
                STATUS_OFFLINE: MUTED,
            }

            color = color_map.get(status_code, MUTED)

            dot_map = {
                STATUS_CONNECTED: "●",
                STATUS_DISCONNECTED: "✗",
                STATUS_LOW_CREDIT: "⚠",
                STATUS_OFFLINE: "○",
            }

            dot = dot_map.get(status_code, "○")

            self._append_ai_log(f"Connection test result: {status_code} | {message}")
            self.after(0, lambda: self._ai_status_var.set(f"{dot}  {message}"))
            self.after(0, lambda: self._ai_status_lbl.configure(fg=color))

        threading.Thread(target=_run, daemon=True).start()

    # ------------------------------------------------------------------
    # DESKTOP NOTIFICATION HELPER (INSTANCE)
    # ------------------------------------------------------------------
    def _notify(self, title: str, message: str):
        if self.cfg.get("app_settings", {}).get("desktop_notifications", True):
            threading.Thread(
                target=send_desktop_notification, args=(title, message), daemon=True
            ).start()

    # ------------------------------------------------------------------
    # CONFIDENCE DISPLAY HELPER
    # ------------------------------------------------------------------
    def _conf_display(self, confidence: float) -> str:
        if confidence <= 0:
            return "—"
        return f"{confidence:.0f}%"

    def _conf_tag(self, confidence: float, supplier: str) -> str:
        threshold = self.cfg.get("app_settings", {}).get("confidence_warn_threshold", 80)
        if supplier == "UNKNOWN SUPPLIER" or confidence <= 0:
            return "unknown"
        if confidence < threshold:
            return "low_conf"
        return "high_conf"

    # ------------------------------------------------------------------
    # RENAME WORKER
    # ------------------------------------------------------------------
    def _start_rename_worker(self):
        if self._worker_running:
            return

        _reset_last_good_supplier()
        self._worker_running = True
        self._cancel_requested = False

        if not getattr(self, "_rename_was_cancelled", False):
            self._rename_results = []
            self._rename_row_map.clear()
            self._rename_all_rows.clear()
            for i in self._rename_tree.get_children():
                self._rename_tree.delete(i)
        self._rename_was_cancelled = False

        self._set_buttons_state("disabled")
        self._set_status("OCR renaming started...", ACCENT)

        def worker():
            n_success = 0
            n_failed = 0
            failed_names = []

            try:
                scanned = self.dirs.get("scanned")
                processed = self.dirs.get("processed")
                failed = self.dirs.get("failed")

                os.makedirs(scanned, exist_ok=True)
                os.makedirs(processed, exist_ok=True)
                os.makedirs(failed, exist_ok=True)

                files = self._get_pdf_files_strict_order(scanned)
                logging.info("Strict processing order: %s", [os.path.basename(f) for f in files])

                total = len(files)
                if total == 0:
                    self._set_status("SCANNED folder is empty — no files to process.", WARNING)
                    self._rename_queue.put(None)
                    return

                self.after(0, lambda: self._progress.configure(maximum=max(total, 1), value=0))
                mode = self.cfg["app_settings"].get("processing_mode", "legacy")
                dry_run = self.cfg["app_settings"].get("dry_run", False)

                # Force sequential processing to preserve exact file order
                use_threads = False

                if dry_run:
                    previews = []
                    for pdf_path in files:
                        fname = os.path.basename(pdf_path)
                        self._set_status(f"[DRY RUN] Scanning: {fname}...", ACCENT)
                        try:
                            if mode == "legacy":
                                fields = self._process_file_legacy(pdf_path)
                            else:
                                text = self._extract_text(pdf_path)
                                if mode == "mixed":
                                    fields = self._process_file_mixed(pdf_path, text)
                                else:
                                    fields = self._process_file_custom(pdf_path, text)

                            sup = fields.get("supplier") or "UNKNOWN SUPPLIER"
                            grn = fields.get("grn") or "NO-GRN"
                            inv = fields.get("invoice") or "NO-INVOICE"
                            conf = fields.get("confidence", 0.0)

                            inv_part = f"IN {inv}" if inv and inv != "NO-INVOICE" else "NO-INVOICE"
                            proposed = self._safe_filename(f"{sup} GRN {grn} {inv_part}.pdf")
                            dupes = self._find_duplicate_invoice(inv, processed)

                            previews.append({
                                "file": fname,
                                "proposed_name": proposed,
                                "supplier": sup,
                                "grn": grn,
                                "confidence": conf,
                                "invoice": inv if inv != "NO-INVOICE" else "",
                                "duplicate_warning": f"Already exists: {dupes[0]}" if dupes else "",
                                "_path": pdf_path,
                            })
                        except Exception as e:
                            previews.append({
                                "file": fname,
                                "proposed_name": f"ERROR: {e}",
                                "supplier": "",
                                "grn": "",
                                "confidence": 0.0,
                                "invoice": "",
                                "duplicate_warning": "",
                                "_path": pdf_path,
                            })

                    proceed = [False]

                    def _show():
                        proceed[0] = self._show_dry_run_preview(previews)

                    self.after(0, _show)

                    import time
                    deadline = time.time() + 300
                    while time.time() < deadline:
                        time.sleep(0.1)
                        try:
                            if not any(
                                isinstance(w, tk.Toplevel) and "Preview" in (w.title() or "")
                                for w in self.winfo_children()
                            ):
                                break
                        except Exception:
                            break

                    if not proceed[0]:
                        self._set_status("Dry Run cancelled — no files moved.", WARNING)
                        self._rename_queue.put(None)
                        return

                    self.cfg["app_settings"]["dry_run"] = False

                for idx, pdf_path in enumerate(files, 1):
                    if self._cancel_requested:
                        self._set_status("Rename cancelled by user.", WARNING)
                        break

                    fname = os.path.basename(pdf_path)
                    self._set_status(f"[{mode.upper()}] Processing {fname}... ({idx}/{total})", ACCENT)

                    rd = self._rename_single_file(pdf_path, processed, failed, mode)
                    self._rename_results.append(rd)

                    if rd.get("status") == "success":
                        n_success += 1
                    else:
                        n_failed += 1
                        failed_names.append(fname)

                    self._rename_queue.put(("row", rd))
                    self._rename_queue.put(("progress", idx))

                if dry_run:
                    self.cfg["app_settings"]["dry_run"] = True

                if self._cancel_requested:
                    self._rename_was_cancelled = True

                if not self._cancel_requested:
                    summary = f"Rename complete — {n_success} succeeded, {n_failed} failed."
                    self._set_status(summary, SUCCESS if n_failed == 0 else WARNING)

                    notif_msg = summary
                    if n_failed > 0 and failed_names:
                        notif_msg += f"\nFailed: {', '.join(failed_names[:3])}"
                        if len(failed_names) > 3:
                            notif_msg += f" +{len(failed_names) - 3} more"

                    self._notify("Maafushivaru — Rename Complete", notif_msg)

            except Exception as e:
                logging.error(f"Rename worker error: {e}\n{traceback.format_exc()}")
                self.after(0, lambda m=str(e): messagebox.showerror("Rename Error", f"Unexpected error:\n\n{m}"))
                self._set_status(f"Rename failed: {e}", ERROR)

            finally:
                self._worker_running = False
                self._rename_queue.put(None)
                self.after(0, self._refresh_dashboard_stats)

        threading.Thread(target=worker, daemon=True).start()
        self.after(60, self._poll_rename_queue)

    def _cancel_worker(self):
        self._cancel_requested = True
        self._set_status("Cancel requested — stopping after current file...", WARNING)

    def _add_rename_tree_row(self, result):
        s    = result.get("status", "")
        sup  = result.get("supplier", "")
        conf = result.get("confidence", 0.0)
        dupe = result.get("duplicate_warning", "")
        inv_raw = result.get("invoice", "")
        inv_display = f"IN {inv_raw}" if inv_raw and inv_raw not in ("NO-INVOICE", "") else "NO-INVOICE"

        conf_thr = self.cfg.get("app_settings", {}).get("confidence_warn_threshold", 80)

        # Tag priority: error > unknown supplier > duplicate > low confidence > success
        if "error" in s:
            tag = "error"
        elif sup == "UNKNOWN SUPPLIER":
            tag = "unknown"
        elif dupe:
            tag = "dupe_warn"
        elif conf > 0 and conf < conf_thr:
            tag = "low_conf"
        elif s == "corrected":
            tag = "corrected"
        else:
            tag = "success"

        for t, fg in [
            ("success",  SUCCESS),
            ("unknown",  WARNING),
            ("error",    ERROR),
            ("dupe_warn",WARNING),
            ("low_conf", WARNING),
            ("corrected",ACCENT2),
        ]:
            self._rename_tree.tag_configure(t, foreground=fg)

        # Ensure each rename result has a stable doc_id so it can be linked
        # to a Dispatch row later.
        if not result.get("doc_id"):
            # Simple unique id based on filename + current time
            result["doc_id"] = f"{result.get('file', '')}|rename|{datetime.now().timestamp()}"

        row_id = self._rename_tree.insert(
            "", "end", tags=(tag,),
            values=(
                result.get("file", ""),
                result.get("new_name", ""),
                sup,
                self._conf_display(conf),
                result.get("grn", ""),
                inv_display,
                result.get("status", ""),
                dupe,
            ),
        )
        self._rename_row_map[row_id] = result
        self._rename_all_rows.append(row_id)

    def _clear_rename_results(self):
        self._rename_results = []
        self._rename_row_map.clear()
        self._rename_all_rows.clear()
        for i in self._rename_tree.get_children():
            self._rename_tree.delete(i)
        self._set_status("Rename results cleared.")

    # ------------------------------------------------------------------
    # SUPPLIER CORRECTION MEMORY (OFFER ALIAS)
    # ------------------------------------------------------------------
    def _offer_save_alias(self, old_supplier: str, new_supplier: str):
        if not old_supplier or not new_supplier or old_supplier == new_supplier:
            return
        if old_supplier == "UNKNOWN SUPPLIER":
            return
        suppliers = self.cfg.get("suppliers", [])
        if new_supplier.upper() not in [s.upper() for s in suppliers]:
            return
        if messagebox.askyesno(
            "Remember Correction?",
            f'Save "{old_supplier}" as an alias for "{new_supplier}"?\n\n'
            f'Future documents containing "{old_supplier}" will be matched automatically.',
        ):
            aliases = self.cfg.setdefault("aliases", {})
            existing = aliases.get(new_supplier, [])
            if old_supplier.upper() not in [a.upper() for a in existing]:
                existing.append(old_supplier.upper())
                aliases[new_supplier] = existing
                self._save_config()
                self._populate_supplier_tree()
                self._set_status(f"Alias saved: '{old_supplier}' → '{new_supplier}'", SUCCESS)

    def _on_rename_tree_edit(self, row_id, col_index, old_val, new_val):
        vals = list(self._rename_tree.item(row_id, "values"))

        if col_index == 4:   # GRN
            nv = new_val.strip().upper()
            if nv in ("NO-GRN", "", "NO GRN"):
                new_val_disp = "RC-MAM-0000"
            else:
                nv = "RC-MAM-" + nv if not nv.startswith("RC-MAM-") else nv
                new_val_disp = nv
            vals[4] = new_val_disp

        elif col_index == 5:  # Invoice #
            raw = new_val.strip()
            if raw.upper().startswith("IN "):
                raw = raw[3:].strip()
            new_val_disp = "NO-INVOICE" if not raw or raw.upper() == "NO-INVOICE" else f"IN {raw}"
            invoice_internal = "" if new_val_disp == "NO-INVOICE" else raw
            vals[5] = new_val_disp

        elif col_index == 2:  # Supplier
            new_val_disp = new_val.strip().upper()
            vals[2] = new_val_disp

        else:
            vals[col_index] = new_val
            self._rename_tree.item(row_id, values=vals)
            return

        result = self._rename_row_map.get(row_id)
        if not result:
            self._rename_tree.item(row_id, values=vals)
            return

        dp = result.get("dest_path", "")
        if not dp or not os.path.exists(dp):
            messagebox.showwarning("Cannot Rename", f"File not found:\n{dp or '(no path)'}")
            return

        sup = vals[2]
        grn = vals[4]

        if col_index == 5:
            result["invoice"] = invoice_internal
            result["invoice_dispatch"] = invoice_internal
        else:
            existing_disp = vals[5]
            if existing_disp.upper().startswith("IN "):
                result["invoice"] = existing_disp[3:].strip()
                result["invoice_dispatch"] = existing_disp[3:].strip()
            else:
                result["invoice"] = ""
                result["invoice_dispatch"] = ""

        old_supplier = result.get("supplier", "")
        result["supplier"] = sup
        result["grn"] = grn

        internal_invoice = result.get("invoice") or ""
        fn_inv = f"IN {internal_invoice}" if internal_invoice else "NO-INVOICE"

        nd = os.path.join(
            os.path.dirname(dp),
            self._safe_filename(f"{sup} GRN {grn} {fn_inv}.pdf")
        )

        if nd != dp:
            base, ext = os.path.splitext(nd)
            cnt = 1
            while os.path.exists(nd):
                nd = f"{base}_{cnt}{ext}"
                cnt += 1
            try:
                _safe_file_move(dp, nd)
                result["dest_path"] = nd
                vals[0] = os.path.basename(nd)
                vals[1] = os.path.basename(nd)
            except Exception as e:
                messagebox.showerror("Rename Failed", f"Could not rename:\n\n{e}")
                return
        else:
            vals[0] = os.path.basename(dp)
            vals[1] = os.path.basename(dp)

        # --- Rebuild proposed filename from all current cell values ---
        current_sup = vals[2] or "UNKNOWN SUPPLIER"
        current_grn = vals[4] or "NO-GRN"

        # Get invoice from column 5 display value
        inv_disp = vals[5] if len(vals) > 5 else "NO-INVOICE"
        if inv_disp.upper().startswith("IN "):
            current_inv_internal = inv_disp[3:].strip()
        else:
            current_inv_internal = ""

        fn_inv_part = f"IN {current_inv_internal}" if current_inv_internal else "NO-INVOICE"
        new_proposed_filename = self._safe_filename(
            f"{current_sup} GRN {current_grn} {fn_inv_part}.pdf"
        )

        # Update column 1 (New Name) live
        vals[1] = new_proposed_filename
        # --- End live filename rebuild ---

        result["file"] = vals[0]
        result["new_name"] = vals[1]
        result["status"] = "corrected"

        self._rename_tree.item(row_id, values=vals)
        self._rename_tree.item(row_id, tags=("corrected",))
        self._rename_tree.tag_configure("corrected", foreground=ACCENT2)

        self._set_status(f"Corrected: {vals[1]}", ACCENT2)
        self._refresh_dashboard_stats()
        self._sync_rename_to_dispatch(result)
        # Mirror the correction back to the AI Extract result tree too.
        self._sync_to_ai_extract(
            result.get("doc_id", ""),
            result.get("supplier", ""),
            result.get("grn", ""),
            result.get("invoice", ""),
            result.get("file", ""),
        )

        if col_index == 2 and old_supplier != sup:
            self.after(300, lambda: self._offer_save_alias(old_supplier, sup))

    # ------------------------------------------------------------------
    # DISPATCH WORKER
    # ------------------------------------------------------------------
    def _start_dispatch_worker(self):
        if self._dispatch_running:
            return

        self._dispatch_running = True
        self._cancel_requested = False
        self._dispatch_results = []
        self._dispatch_row_map.clear()
        self._dispatch_all_rows.clear()

        for i in self._dispatch_tree.get_children():
            self._dispatch_tree.delete(i)

        self._set_buttons_state("disabled")
        self._set_status("GRN extraction started...", ACCENT)

        def worker():
            n_success = 0
            n_failed = 0
            failed_names = []

            try:
                folder = self.dirs.get("processed") or self.dirs.get("scanned")
                if hasattr(self, "_dispatch_folder_var"):
                    ch = self._dispatch_folder_var.get().strip()
                    if ch:
                        folder = ch

                files = sorted(
                    [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(".pdf")],
                    key=lambda p: os.path.getmtime(p),
                    reverse=True,
                )

                total = len(files)
                self.after(0, lambda: self._dispatch_progress.configure(maximum=max(total, 1), value=0))

                ut = self.cfg["app_settings"].get("enable_multi_threading", True)
                mt = self.cfg["app_settings"].get("max_threads", 4)
                mode = self.cfg["app_settings"].get("processing_mode", "legacy")

                if ut and total > 1:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=mt) as ex:
                        fm = {
                            ex.submit(self._dispatch_single_file, p, idx, mode): p
                            for idx, p in enumerate(files)
                        }
                        dc = 0
                        for fut in concurrent.futures.as_completed(fm):
                            if self._cancel_requested:
                                break
                            r = fut.result()
                            self._dispatch_results.append(r)
                            dc += 1

                            fname = r.get("file", "")
                            sup = r.get("supplier", "")
                            conf = r.get("confidence", 0.0)

                            self._set_status(
                                f"[GRN] {fname}  →  {sup} ({conf:.0f}%)  [{dc}/{total}]",
                                SUCCESS if r.get("is_valid") else ERROR,
                            )

                            if r.get("is_valid"):
                                n_success += 1
                            else:
                                n_failed += 1
                                failed_names.append(fname)

                            self._dispatch_queue.put(("row", r))
                            self._dispatch_queue.put(("progress", dc))
                else:
                    for idx, p in enumerate(files):
                        if self._cancel_requested:
                            break

                        fname = os.path.basename(p)
                        self._set_status(f"[GRN] Extracting {fname}... ({idx + 1}/{total})", ACCENT)

                        r = self._dispatch_single_file(p, idx, mode)
                        self._dispatch_results.append(r)

                        if r.get("is_valid"):
                            n_success += 1
                        else:
                            n_failed += 1
                            failed_names.append(fname)

                        self._dispatch_queue.put(("row", r))
                        self._dispatch_queue.put(("progress", idx + 1))

                summary = f"GRN extraction complete — {n_success} ok, {n_failed} failed."
                self._set_status(summary, SUCCESS if n_failed == 0 else WARNING)

                notif = summary
                if n_failed and failed_names:
                    notif += f"\nFailed: {', '.join(failed_names[:3])}"

                self._notify("Maafushivaru — GRN Extraction Complete", notif)

            except Exception as e:
                logging.error("Dispatch worker failed", exc_info=True)
                self.after(0, lambda m=str(e): messagebox.showerror("Dispatch Error", m))
                self._set_status(f"GRN extraction failed: {e}", ERROR)

            finally:
                self._dispatch_running = False
                self._dispatch_queue.put(None)

        threading.Thread(target=worker, daemon=True).start()
        self.after(60, self._poll_dispatch_queue)

    def _add_dispatch_tree_row(self, result):
        iv   = result.get("is_valid", False)
        conf = result.get("confidence", 0.0)
        conf_thr = self.cfg.get("app_settings", {}).get("confidence_warn_threshold", 80)
        sup = result.get("supplier", "")

        if not iv:
            tag = "invalid"
        elif sup == "UNKNOWN SUPPLIER" or conf <= 0:
            tag = "unknown"
        elif conf < conf_thr:
            tag = "low_conf"
        else:
            tag = "valid"

        for t, fg in [
            ("valid",    SUCCESS),
            ("invalid",  ERROR),
            ("unknown",  WARNING),
            ("low_conf", WARNING),
        ]:
            self._dispatch_tree.tag_configure(t, foreground=fg)

        row_id = self._dispatch_tree.insert(
            "", "end", tags=(tag,),
            values=(
                result.get("file", ""),
                result.get("date", ""),
                sup,
                self._conf_display(conf),
                result.get("po", "") or "MAM-0000",
                result.get("invoice", ""),
                result.get("usd", ""),
                result.get("mvr", ""),
                result.get("eur", ""),
                result.get("gbp", ""),
                result.get("sgd", ""),
                result.get("grn", ""),
            ),
        )
        self._dispatch_row_map[row_id] = result
        self._dispatch_all_rows.append(row_id)

    def _clear_dispatch_results(self):
        self._dispatch_results = []
        self._dispatch_row_map.clear()
        self._dispatch_all_rows.clear()
        for i in self._dispatch_tree.get_children():
            self._dispatch_tree.delete(i)
        self._set_status("Dispatch results cleared.")

    def _on_dispatch_tree_edit(self, row_id, col_index, old_val, new_val):
        vals = list(self._dispatch_tree.item(row_id, "values"))

        if col_index == 11:  # GRN
            if new_val:
                nv = str(new_val).strip().upper()
                if not nv.startswith("RC-MAM-"):
                    nv = "RC-MAM-" + nv
                new_val = nv

        if col_index == 5:  # Invoice #
            raw = str(new_val).strip()
            if raw.upper().startswith("IN "):
                raw = raw[3:].strip()
            new_val = raw

        if col_index == 2:  # Supplier
            new_val = str(new_val).strip().upper()

        vals[col_index] = new_val
        self._dispatch_tree.item(row_id, values=vals)

        result = self._dispatch_row_map.get(row_id)
        if result:
            keys = ["file", "date", "supplier", "_conf", "po", "invoice",
                    "usd", "mvr", "eur", "gbp", "sgd", "grn"]
            if col_index < len(keys) and keys[col_index] != "_conf":
                result[keys[col_index]] = new_val

        if col_index in (2, 5, 11) and result:
            try:
                pdf_path = result.get("raw_path", "")
                if not pdf_path or not os.path.exists(pdf_path):
                    folder = self.dirs.get("processed") or self.dirs.get("scanned")
                    if hasattr(self, "_dispatch_folder_var"):
                        ch = self._dispatch_folder_var.get().strip()
                        if ch:
                            folder = ch
                    pdf_path = os.path.join(folder, result.get("file", ""))

                if not pdf_path or not os.path.exists(pdf_path):
                    messagebox.showwarning(
                        "Cannot Rename",
                        f"File not found:\n{pdf_path or '(no path)'}",
                    )
                else:
                    supplier = (vals[2] or "").strip().upper() or "UNKNOWN SUPPLIER"
                    grn_val = (vals[11] or "").strip().upper() or "NO-GRN"
                    inv_raw = (vals[5] or "").strip()

                    if inv_raw:
                        inv_part = f"IN {inv_raw}"
                    else:
                        inv_part = "NO-INVOICE"

                    new_dir = os.path.dirname(pdf_path)
                    new_name = self._safe_filename(f"{supplier} GRN {grn_val} {inv_part}.pdf")
                    new_path = os.path.join(new_dir, new_name)

                    base, ext = os.path.splitext(new_path)
                    cnt = 1
                    while os.path.exists(new_path) and new_path.lower() != pdf_path.lower():
                        new_path = f"{base}_{cnt}{ext}"
                        cnt += 1

                    if new_path != pdf_path:
                        _safe_file_move(pdf_path, new_path)

                    result["raw_path"] = new_path
                    result["file"] = new_name
                    result["supplier"] = supplier
                    result["invoice"] = inv_raw
                    result["grn"] = grn_val

                    vals[0] = new_name
                    vals[2] = supplier
                    vals[5] = inv_raw
                    vals[11] = grn_val
                    self._dispatch_tree.item(row_id, values=vals)

                    self._set_status(f"File renamed to {new_name}", ACCENT2)

            except Exception as e:
                logging.error(f"Dispatch rename failed: {e}", exc_info=True)
                messagebox.showerror("Rename Failed", f"Could not rename file:\n\n{e}")

        if result:
            self._sync_dispatch_to_rename(result)
            # Recolour the row (a corrected supplier is no longer "unknown")
            # and mirror the edit back to the AI Extract result tree.
            self._refresh_dispatch_row_tag(row_id)
            self._sync_to_ai_extract(
                result.get("doc_id", ""),
                result.get("supplier", ""),
                result.get("grn", ""),
                result.get("invoice", ""),
                result.get("file", ""),
            )

        self._set_status("Dispatch cell updated. Re-export to Excel to save changes.")

    # ------------------------------------------------------------------
    # EXPORT TO EXCEL
    # ------------------------------------------------------------------
    def _export_dispatch_to_excel(self):
        if not self._dispatch_results:
            messagebox.showwarning("No Data", "Run GRN Extraction first.")
            return

        wb = Workbook()
        ws = wb.active
        ws.title = "GRN Data"

        headers = [
            "INVOICE DATE", "SUPPLIER NAME", "PURCHASE ORDER #", "INVOICE #",
            "USD", "MVR", "EUR", "GBP", "SGD", "GRN NO."
        ]
        ws.append(headers)

        CURRENCY_COLS = {5: "E", 6: "F", 7: "G", 8: "H", 9: "I"}

        for row_id in self._dispatch_tree.get_children():
            vals = self._dispatch_tree.item(row_id, "values")
            if not vals:
                continue

            row_data = [
                vals[1],   # Date
                vals[2],   # Supplier
                vals[4],   # PO
                vals[5],   # Invoice
                vals[6],   # USD
                vals[7],   # MVR
                vals[8],   # EUR
                vals[9],   # GBP
                vals[10],  # SGD
                vals[11],  # GRN
            ]
            ws.append(row_data)
            rn = ws.max_row

            for ci, cl in CURRENCY_COLS.items():
                cell = ws[f"{cl}{rn}"]
                val = row_data[ci - 1]
                if val not in ("", None):
                    try:
                        cell.value = float(str(val).replace(",", ""))
                        cell.number_format = NUMBER_FMT
                    except (ValueError, TypeError):
                        cell.value = val
                else:
                    cell.value = ""

        font = Font(name="Arial Narrow", size=10)
        align = Alignment(horizontal="center", vertical="center")

        for col, w in zip("ABCDEFGHIJ", [14, 35, 18, 22, 12, 12, 12, 12, 12, 60]):
            ws.column_dimensions[col].width = w

        for row in ws.iter_rows():
            for cell in row:
                cell.font = font
                cell.alignment = align

        # Each manual export makes a BRAND-NEW file - never overwrite. The
        # timestamp normally guarantees uniqueness; the counter covers the
        # rare case of two exports within the same second.
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = os.path.join(self.dirs["base"], f"GRN_OUTPUT_{ts}.xlsx")
        _cnt = 1
        while os.path.exists(out):
            out = os.path.join(self.dirs["base"], f"GRN_OUTPUT_{ts}_{_cnt}.xlsx")
            _cnt += 1
        wb.save(out)

        self._set_status(f"Excel exported: {out}", SUCCESS)
        self._notify("Maafushivaru — Export Complete", f"{os.path.basename(out)} saved.")

        if messagebox.askyesno("Export Successful", f"Saved:\n{out}\n\nOpen now?"):
            os.startfile(out)

    def _write_grn_excel(self, rows: List[Dict]) -> str:
        """Write GRN rows (list of result dicts) to a BRAND-NEW timestamped
        Excel file and return its path. Shared by the AI Extract "Export Excel"
        button and the 30-result "generate sheet now?" prompt. Never overwrites
        an existing file.

        Reproduces the resort's official "GRN DISPATCH NOTE" workbook layout
        exactly: one sheet per month found in `rows` (e.g. 'APRIL'), 30
        line-items per printed dispatch page, a running DISPATCH number, the
        Outrigger logo top-right of every page, and the signature block --
        see grn_dispatch_excel.py. The starting DISPATCH number is read from
        (and, after a successful export, saved back to) app_settings so
        consecutive exports keep numbering forward."""
        from grn_dispatch_excel import write_grn_dispatch_workbook

        app_settings = self.cfg.setdefault("app_settings", {})
        start_no = int(app_settings.get("grn_next_dispatch_no", 1) or 1)

        logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "outrigger_logo.png")
        if not os.path.isfile(logo_path):
            # Fall back to whichever bundled logo asset exists so export
            # never hard-fails just because the ideal asset is missing.
            for cand in ("logo2.png", "logo.png"):
                alt = os.path.join(os.path.dirname(os.path.abspath(__file__)), cand)
                if os.path.isfile(alt):
                    logo_path = alt
                    break

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = os.path.join(self.dirs["base"], f"GRN_OUTPUT_{ts}.xlsx")

        final_path, next_no = write_grn_dispatch_workbook(rows, start_no, out, logo_path=logo_path)

        app_settings["grn_next_dispatch_no"] = next_no
        try:
            self._save_config()
        except Exception as e:
            logging.error(f"Could not persist next dispatch no: {e}", exc_info=True)

        return final_path

    def _validate_grn(self):
        rows = self._dispatch_tree.get_children()
        if not rows:
            messagebox.showinfo("Validate GRN", "No results to validate. Run GRN Extraction first.")
            return

        folder = self.dirs.get("processed") or self.dirs.get("scanned")
        if hasattr(self, "_dispatch_folder_var"):
            ch = self._dispatch_folder_var.get().strip()
            if ch:
                folder = ch

        if not os.path.isdir(folder):
            messagebox.showerror("Validate GRN", f"Folder not found:\n{folder}")
            return

        folder_files = {f.lower() for f in os.listdir(folder) if f.lower().endswith(".pdf")}
        self._dispatch_tree.tag_configure("missing", foreground=ERROR)
        self._dispatch_tree.tag_configure("present", foreground=SUCCESS)
        found = missing = 0

        for row_id in rows:
            vals = self._dispatch_tree.item(row_id, "values")
            filename = str(vals[0]).strip() if vals else ""
            if filename.lower() in folder_files:
                self._dispatch_tree.item(row_id, tags=("present",))
                found += 1
            else:
                self._dispatch_tree.item(row_id, tags=("missing",))
                missing += 1

        msg = (
            f"Validation complete.\n\n"
            f"Rows checked : {found + missing}\n"
            f"Files FOUND  : {found}  (green)\n"
            f"Files MISSING: {missing}  (red)\n\n"
            f"Folder: {folder}"
        )
        (messagebox.showinfo if missing == 0 else messagebox.showwarning)(
            "Validate GRN" + (" — All OK" if missing == 0 else " — Missing Files"), msg
        )
        self._set_status(f"GRN Validation: {found} found, {missing} missing.",
                         SUCCESS if missing == 0 else WARNING)

    # ------------------------------------------------------------------
    # UNIFIED EXTRACT & PROCESS
    # ------------------------------------------------------------------
    def _extract_core_fields_for_file(self, pdf_path: str, mode: str) -> Dict:
        file_lock = _get_file_lock(pdf_path)
        if not file_lock.acquire(timeout=30):
            logging.warning(f"File already being processed (lock timeout): {pdf_path}")
            return {
                "supplier": "UNKNOWN SUPPLIER", "grn": "RC-MAM-0000",
                "invoice": "", "po": "MAM-0000", "date": "",
                "confidence": 0.0, "error": "File is being processed by another thread",
            }
        try:
            return self._extract_core_fields_for_file_unlocked(pdf_path, mode)
        finally:
            file_lock.release()

    def _extract_core_fields_for_file_unlocked(self, pdf_path: str, mode: str) -> Dict:
        text = self._extract_text(pdf_path)
        if len((text or "").strip()) < 40:
            ai_ocr_text = self._extract_text_via_ai_ocrspace(pdf_path)
            if ai_ocr_text:
                text = ai_ocr_text

        rr = self._extract_receiving_report_fields(pdf_path)

        supplier = None
        invoice_for_filename = ""
        invoice_for_dispatch = ""
        confidence = 0.0

        app_s = self.cfg.get("app_settings", {})
        ocr_correction_on  = app_s.get("ocr_word_correction_enabled", False)
        cross_match_on     = app_s.get("smart_cross_match_enabled", False)
        suppliers_list     = self.cfg.get("suppliers", [])
        aliases_map        = self.cfg.get("aliases", {})

        # ── Step 0: OCR Word Correction (optional) ────────────────────────
        working_text = text
        if ocr_correction_on and OCR_CORRECTOR_AVAILABLE:
            corrected, ocr_detected_supplier = correct_supplier_in_text(
                text, suppliers_list, aliases_map
            )
            if ocr_detected_supplier:
                working_text = corrected
                supplier     = ocr_detected_supplier
                confidence   = 78.0
                logging.info(f"[OCR-CORR] OCR word correction matched supplier: {supplier}")

        # ── Step 1: Normal supplier extraction ───────────────────────────
        if not supplier:
            if mode == "legacy":
                cands, aliases = self._legacy_load_suppliers()
                legacy_text    = self._legacy_extract_text(pdf_path)
                supplier       = self._legacy_extract_supplier(legacy_text, cands, aliases) or "UNKNOWN SUPPLIER"
                invoice_for_filename = self._extract_invoice_old(legacy_text) or "NO-INVOICE"
                invoice_for_dispatch = self._extract_invoice_old(legacy_text) or ""
                confidence = 70.0 if supplier != "UNKNOWN SUPPLIER" else 0.0

            elif mode == "mixed":
                method = app_s.get("supplier_extraction_method", "both")
                if method in ("receiving_report_field", "both"):
                    supplier = self._extract_supplier_from_field(working_text)
                    if supplier: confidence = 85.0
                if not supplier and method in ("header_company_name", "both"):
                    supplier = self._extract_company_from_invoice_pages(pdf_path)
                    if supplier: confidence = 80.0
                if not supplier:
                    supplier, confidence = self._match_supplier_with_confidence(working_text, os.path.basename(pdf_path))
                if not supplier or supplier == "UNKNOWN SUPPLIER":
                    supplier   = _get_last_good_supplier() or "UNKNOWN SUPPLIER"
                    confidence = 0.0
                else:
                    _set_last_good_supplier(supplier)
                legacy_text = self._legacy_extract_text(pdf_path)
                invoice_for_filename = self._extract_invoice_old(legacy_text) or "NO-INVOICE"
                invoice_for_dispatch = self._extract_invoice_old(legacy_text) or ""

            else:  # custom
                method = app_s.get("supplier_extraction_method", "both")
                if method in ("receiving_report_field", "both"):
                    supplier = self._extract_supplier_from_field(working_text)
                    if supplier: confidence = 85.0
                if not supplier and method in ("header_company_name", "both"):
                    supplier = self._extract_company_from_invoice_pages(pdf_path)
                    if supplier: confidence = 80.0
                if not supplier:
                    supplier, confidence = self._match_supplier_with_confidence(working_text, os.path.basename(pdf_path))
                if not supplier or supplier == "UNKNOWN SUPPLIER":
                    supplier   = _get_last_good_supplier() or "UNKNOWN SUPPLIER"
                    confidence = 0.0
                else:
                    _set_last_good_supplier(supplier)
                invoice_for_filename = self._extract_invoice(working_text, supplier_hint=supplier) or "NO-INVOICE"
                invoice_for_dispatch = self._extract_invoice_old(working_text) or ""

        # Extract invoices for legacy mode if OCR correction found supplier early
        if mode == "legacy" and supplier != "UNKNOWN SUPPLIER" and not invoice_for_filename:
            legacy_text = self._legacy_extract_text(pdf_path)
            invoice_for_filename = self._extract_invoice_old(legacy_text) or "NO-INVOICE"
            invoice_for_dispatch = self._extract_invoice_old(legacy_text) or ""

        # ── Step 2: Smart Cross-Matching (optional) ───────────────────────
        if cross_match_on and CROSS_MATCHER_AVAILABLE:
            # Feature B: if supplier still unknown, try to ID from invoice pattern
            if (not supplier or supplier == "UNKNOWN SUPPLIER") and (invoice_for_dispatch or invoice_for_filename):
                invoice_probe = invoice_for_dispatch or invoice_for_filename
                inferred_sup, inferred_conf = infer_supplier_from_invoice(
                    working_text + " " + invoice_probe,
                    suppliers_list,
                    aliases_map,
                )
                if inferred_sup and inferred_conf > 0:
                    supplier   = inferred_sup
                    confidence = inferred_conf
                    logging.info(f"[CROSS-MATCH] Invoice pattern → supplier: {supplier} ({confidence:.0f}%)")
                    _set_last_good_supplier(supplier)

            # Feature A: if supplier is now known, clean the invoice number
            if supplier and supplier != "UNKNOWN SUPPLIER":
                raw_inv = invoice_for_filename if invoice_for_filename not in ("NO-INVOICE", "") else invoice_for_dispatch
                if raw_inv and raw_inv != "NO-INVOICE":
                    cleaned_inv = infer_invoice_from_supplier(raw_inv, supplier)
                    if cleaned_inv != raw_inv:
                        logging.info(f"[CROSS-MATCH] Invoice corrected: {raw_inv} → {cleaned_inv}")
                    invoice_for_filename = cleaned_inv or "NO-INVOICE"
                    invoice_for_dispatch = cleaned_inv or ""

        grn    = rr.get("grn", "") or self._extract_grn_full(pdf_path, text) or "RC-MAM-0000"
        po     = rr.get("po",  "") or "MAM-0000"
        date_v = rr.get("date","") or ""
        totals = rr.get("totals", {"USD": "", "MVR": "", "EUR": "", "GBP": "", "SGD": ""})

        return {
            "supplier": supplier or "UNKNOWN SUPPLIER",
            "grn": grn, "po": po, "date": date_v,
            "confidence": confidence,
            "invoice_filename": invoice_for_filename or "NO-INVOICE",
            "invoice_dispatch": invoice_for_dispatch or "",
            "usd": totals.get("USD", ""), "mvr": totals.get("MVR", ""),
            "eur": totals.get("EUR", ""), "gbp": totals.get("GBP", ""),
            "sgd": totals.get("SGD", ""), "text": text,
        }

    def _build_dispatch_from_rename_result(self, rename_result: Dict, scan_index: int = 0) -> Dict:
        return {
            "doc_id":     rename_result.get("doc_id", ""),
            "file":       rename_result.get("new_name", "") or rename_result.get("file", ""),
            "date":       rename_result.get("date", ""),
            "supplier":   rename_result.get("supplier", "UNKNOWN SUPPLIER"),
            "po":         rename_result.get("po", "") or "MAM-0000",
            "invoice":    rename_result.get("invoice_dispatch", rename_result.get("invoice", "")),
            "usd":        rename_result.get("usd", ""),
            "mvr":        rename_result.get("mvr", ""),
            "eur":        rename_result.get("eur", ""),
            "gbp":        rename_result.get("gbp", ""),
            "sgd":        rename_result.get("sgd", ""),
            "grn":        rename_result.get("grn", ""),
            "confidence": rename_result.get("confidence", 0.0),
            "is_valid":   rename_result.get("status") in ("success", "simulated"),
            "errors":     rename_result.get("errors", ""),
            "raw_path":   rename_result.get("dest_path", ""),
            "scan_index": scan_index,
        }

    def _rename_and_build_dispatch_single(self, pdf_path, processed, failed, mode, scan_index=0):
        src_name = os.path.basename(pdf_path)
        doc_id   = f"{src_name}|{scan_index}|{datetime.now().timestamp()}"

        rename_result = {
            "doc_id": doc_id, "file": src_name, "new_name": "",
            "supplier": "UNKNOWN SUPPLIER", "grn": "NO-GRN",
            "invoice": "", "invoice_dispatch": "", "date": "", "po": "MAM-0000",
            "usd": "", "mvr": "", "eur": "", "gbp": "", "sgd": "",
            "status": "error", "dest_path": "", "errors": "",
            "duplicate_warning": "", "confidence": 0.0,
        }

        try:
            core = self._extract_core_fields_for_file(pdf_path, mode)

            supplier         = core["supplier"] or "UNKNOWN SUPPLIER"
            grn              = core["grn"] or "RC-MAM-0000"
            invoice_filename = core["invoice_filename"] or "NO-INVOICE"
            invoice_dispatch = core["invoice_dispatch"] or ""
            date_val         = core["date"] or ""
            po               = core["po"] or "MAM-0000"
            confidence       = core.get("confidence", 0.0)

            rename_result.update({
                "supplier": supplier, "grn": grn, "confidence": confidence,
                "invoice": invoice_filename if invoice_filename != "NO-INVOICE" else "",
                "invoice_dispatch": invoice_dispatch, "date": date_val, "po": po,
                "usd": core["usd"], "mvr": core["mvr"], "eur": core["eur"],
                "gbp": core["gbp"], "sgd": core["sgd"],
            })

            dupes = self._find_duplicate_invoice(invoice_filename, processed)
            if dupes:
                rename_result["duplicate_warning"] = f"Invoice already exists: {dupes[0]}"

            inv_part = f"IN {invoice_filename}" if invoice_filename and invoice_filename != "NO-INVOICE" else "NO-INVOICE"
            dest = os.path.join(processed, self._safe_filename(f"{supplier} GRN {grn} {inv_part}.pdf"))

            cnt = 1
            base, ext = os.path.splitext(dest)
            while os.path.exists(dest):
                dest = f"{base}_{cnt}{ext}"
                cnt += 1

            dry_run = self.cfg.get("app_settings", {}).get("dry_run", False)
            if not dry_run:
                _safe_file_move(pdf_path, dest)
                rename_result["status"]    = "success"
                rename_result["dest_path"] = dest
            else:
                rename_result["status"]    = "simulated"
                rename_result["dest_path"] = pdf_path

            rename_result["new_name"] = os.path.basename(dest)
            dispatch_result = self._build_dispatch_from_rename_result(rename_result, scan_index)
            return rename_result, dispatch_result

        except Exception as e:
            rename_result["errors"] = str(e)
            rename_result["status"] = "error"
            logging.error(f"Unified process failed [{src_name}]: {e}", exc_info=True)
            try:
                _safe_file_move(pdf_path, os.path.join(failed, src_name))
            except Exception:
                pass
            dispatch_result = self._build_dispatch_from_rename_result(rename_result, scan_index)
            dispatch_result["is_valid"] = False
            dispatch_result["errors"]   = str(e)
            return rename_result, dispatch_result

    def _start_extract_and_process(self):
        if self._worker_running or self._dispatch_running:
            return

        _reset_last_good_supplier()
        self._worker_running = True
        self._dispatch_running = True
        self._cancel_requested = False

        if not getattr(self, "_ep_was_cancelled", False):
            self._rename_results = []
            self._dispatch_results = []
            self._rename_row_map.clear()
            self._dispatch_row_map.clear()
            self._rename_all_rows.clear()
            self._dispatch_all_rows.clear()

            for i in self._rename_tree.get_children():
                self._rename_tree.delete(i)
            for i in self._dispatch_tree.get_children():
                self._dispatch_tree.delete(i)

        self._ep_was_cancelled = False

        self._set_buttons_state("disabled")
        self._set_status("Extract & Process started...", ACCENT)
        mode = self.cfg.get("app_settings", {}).get("processing_mode", "legacy")

        def worker():
            n_success = 0
            n_failed = 0
            failed_names = []

            try:
                scanned = self.dirs.get("scanned")
                processed = self.dirs.get("processed")
                failed = self.dirs.get("failed")

                os.makedirs(scanned, exist_ok=True)
                os.makedirs(processed, exist_ok=True)
                os.makedirs(failed, exist_ok=True)

                files = self._get_pdf_files_strict_order(scanned)
                logging.info("Strict processing order: %s", [os.path.basename(f) for f in files])

                total = len(files)
                if total == 0:
                    self._set_status("SCANNED folder is empty — nothing to process.", WARNING)
                    self.after(0, lambda: self._progress.configure(maximum=1, value=0))
                    self.after(0, lambda: self._dispatch_progress.configure(maximum=1, value=0))
                    return

                self.after(0, lambda: self._progress.configure(maximum=max(total, 1), value=0))
                self.after(0, lambda: self._dispatch_progress.configure(maximum=max(total, 1), value=0))

                # Force sequential processing to preserve exact order
                for idx, pdf_path in enumerate(files, 1):
                    if self._cancel_requested:
                        self._set_status("Extract & Process cancelled.", WARNING)
                        break

                    fname = os.path.basename(pdf_path)
                    self._set_status(f"[{mode.upper()}] {fname} [{idx}/{total}]", ACCENT)

                    rr, dr = self._rename_and_build_dispatch_single(
                        pdf_path, processed, failed, mode, idx - 1
                    )

                    self._rename_results.append(rr)
                    self._dispatch_results.append(dr)

                    if rr.get("status") == "success":
                        n_success += 1
                    else:
                        n_failed += 1
                        failed_names.append(fname)

                    self.after(0, lambda r=rr: self._add_rename_tree_row(r))
                    self.after(0, lambda r=dr: self._add_dispatch_tree_row(r))
                    self.after(0, lambda v=idx: self._progress.configure(value=v))
                    self.after(0, lambda v=idx: self._dispatch_progress.configure(value=v))

                if self._cancel_requested:
                    self._ep_was_cancelled = True

                if not self._cancel_requested:
                    summary = f"Extract & Process complete — {n_success} succeeded, {n_failed} failed."
                    self._set_status(summary, SUCCESS if n_failed == 0 else WARNING)

                    notif = summary
                    if n_failed and failed_names:
                        notif += f"\nFailed: {', '.join(failed_names[:3])}"
                        if len(failed_names) > 3:
                            notif += f" +{len(failed_names) - 3} more"

                    self._notify("Maafushivaru — Extract & Process Complete", notif)

            except Exception as e:
                logging.error(f"Extract & Process error: {e}\n{traceback.format_exc()}")
                self.after(0, lambda m=str(e): messagebox.showerror("Extract & Process Error", f"Error:\n\n{m}"))
                self._set_status(f"Extract & Process failed: {e}", ERROR)

            finally:
                self._worker_running = False
                self._dispatch_running = False
                self.after(0, self._set_buttons_state, "normal")
                self.after(0, self._refresh_dashboard_stats)

        threading.Thread(target=worker, daemon=True).start()

    def _extract_text_via_ai_ocrspace(self, pdf_path: str) -> str:
        """
        Optional OCR.space extraction path.
        Uses OCR.space if available and configured.
        Returns upper-cased text or empty string on failure.
        """
        if not AI_MATCHER_AVAILABLE or self._ai_matcher is None:
            return ""

        try:
            self._append_ai_log(f"Trying OCR.space extraction for: {os.path.basename(pdf_path)}")
            text, err = self._ai_matcher.extract_text(file_path=pdf_path)
            if err:
                self._append_ai_log(f"OCR.space error: {err}")
                return ""
            self._append_ai_log(f"OCR.space extraction success: {len(text)} chars")
            return (text or "").upper()
        except Exception as e:
            self._append_ai_log(f"OCR.space extraction exception: {e}")
            return ""

    # ------------------------------------------------------------------
    # LINK HELPERS
    # ------------------------------------------------------------------
    def _find_dispatch_row_by_doc_id(self, doc_id: str):
        for row_id, data in self._dispatch_row_map.items():
            if data.get("doc_id") == doc_id:
                return row_id, data
        return None, None

    def _find_rename_row_by_doc_id(self, doc_id: str):
        for row_id, data in self._rename_row_map.items():
            if data.get("doc_id") == doc_id:
                return row_id, data
        return None, None

    def _apply_ai_extract_edit(self, ai_result: Dict) -> bool:
        """Propagate an edit made on the AI Extract tab to the OCR Renamer and
        GRN Dispatch tabs, and rename the file on disk to match.

        Only acts if this document has ALREADY been sent to the tabs (matched by
        doc_id). Returns True if it propagated, False if there was nothing to do.
        """
        doc_id = ai_result.get("doc_id", "")
        if not doc_id:
            return False
        r_row, rename_result = self._find_rename_row_by_doc_id(doc_id)
        if not r_row or not rename_result:
            return False  # not sent to tabs yet -> nothing to update

        supplier = (ai_result.get("supplier") or "UNKNOWN SUPPLIER").strip().upper()
        grn = (ai_result.get("grn") or "RC-MAM-0000").strip().upper()
        inv_raw = (ai_result.get("invoice") or "").strip()
        inv_internal = "" if (not inv_raw or inv_raw.upper() == "NO-INVOICE") else inv_raw

        # --- Rename the renamed file on disk to reflect the edit ---
        dp = rename_result.get("dest_path", "")
        if dp and os.path.exists(dp):
            fn_inv = f"IN {inv_internal}" if inv_internal else "NO-INVOICE"
            nd = os.path.join(
                os.path.dirname(dp),
                self._safe_filename(f"{supplier} GRN {grn} {fn_inv}.pdf"),
            )
            if os.path.abspath(nd) != os.path.abspath(dp):
                base, ext = os.path.splitext(nd)
                cnt = 1
                while os.path.exists(nd):
                    nd = f"{base}_{cnt}{ext}"
                    cnt += 1
                try:
                    shutil.move(dp, nd)
                    rename_result["dest_path"] = nd
                except Exception as e:
                    logging.error(f"[AI EDIT] rename failed: {e}", exc_info=True)
                    messagebox.showerror("Rename Failed", f"Could not rename file:\n\n{e}")
                    return False

        new_name = os.path.basename(rename_result.get("dest_path", "")) or rename_result.get("file", "")
        rename_result.update({
            "file": new_name,
            "new_name": new_name,
            "supplier": supplier,
            "grn": grn,
            "invoice": inv_internal,
            "invoice_dispatch": inv_internal,
            "date": ai_result.get("date", rename_result.get("date", "")),
            "po": ai_result.get("po", "") or "MAM-0000",
            "usd": ai_result.get("usd", ""),
            "mvr": ai_result.get("mvr", ""),
            "eur": ai_result.get("eur", ""),
            "gbp": ai_result.get("gbp", ""),
            "sgd": ai_result.get("sgd", ""),
            "status": "corrected",
        })

        # --- Update OCR Renamer tree row ---
        # rename columns: 0 file, 1 new_name, 2 supplier, 3 conf, 4 grn,
        #                 5 invoice, 6 status, 7 dupe
        vals = list(self._rename_tree.item(r_row, "values"))
        if vals:
            inv_disp = f"IN {inv_internal}" if inv_internal else "NO-INVOICE"
            vals[0] = new_name
            vals[1] = new_name
            vals[2] = supplier
            vals[4] = grn
            vals[5] = inv_disp
            if len(vals) > 6:
                vals[6] = "corrected"
            self._rename_tree.item(r_row, values=vals, tags=("corrected",))
            self._rename_tree.tag_configure("corrected", foreground=ACCENT2)

        # --- Propagate to GRN Dispatch (file/supplier/invoice/grn) ---
        self._sync_rename_to_dispatch(rename_result)

        # --- Also push date / PO / currency totals to the dispatch row ---
        # dispatch columns: 0 file,1 date,2 supplier,3 conf,4 po,5 invoice,
        #                   6 usd,7 mvr,8 eur,9 gbp,10 sgd,11 grn
        d_row, dispatch_result = self._find_dispatch_row_by_doc_id(doc_id)
        if d_row and dispatch_result:
            dvals = list(self._dispatch_tree.item(d_row, "values"))
            if dvals and len(dvals) >= 12:
                dvals[1] = rename_result["date"]
                dvals[4] = rename_result["po"]
                dvals[6] = rename_result["usd"]
                dvals[7] = rename_result["mvr"]
                dvals[8] = rename_result["eur"]
                dvals[9] = rename_result["gbp"]
                dvals[10] = rename_result["sgd"]
                self._dispatch_tree.item(d_row, values=dvals)
            dispatch_result.update({
                "date": rename_result["date"],
                "po": rename_result["po"],
                "usd": rename_result["usd"],
                "mvr": rename_result["mvr"],
                "eur": rename_result["eur"],
                "gbp": rename_result["gbp"],
                "sgd": rename_result["sgd"],
            })

        self._refresh_dashboard_stats()
        logging.info(f"[AI EDIT] Propagated edit + rename for doc {doc_id} -> {new_name}")
        return True

    def _sync_rename_to_dispatch(self, rename_result: Dict):
        doc_id = rename_result.get("doc_id", "")
        row_id, dispatch_result = self._find_dispatch_row_by_doc_id(doc_id)
        if not row_id or not dispatch_result:
            return

        vals = list(self._dispatch_tree.item(row_id, "values"))
        if not vals:
            return

        new_file = rename_result.get("new_name", "") or rename_result.get("file", "")
        new_supplier = rename_result.get("supplier", "")
        new_invoice = rename_result.get("invoice_dispatch", rename_result.get("invoice", ""))
        new_grn = rename_result.get("grn", "")

        vals[0] = new_file
        vals[2] = new_supplier
        vals[5] = new_invoice
        vals[11] = new_grn

        self._dispatch_tree.item(row_id, values=vals)

        dispatch_result.update({
            "file": new_file,
            "supplier": new_supplier,
            "invoice": new_invoice,
            "grn": new_grn,
            "raw_path": rename_result.get("dest_path", dispatch_result.get("raw_path", "")),
        })

    def _sync_to_ai_extract(self, doc_id: str, supplier: str, grn: str,
                            invoice_raw: str, file_name: str):
        """Propagate an edit made on the OCR Renamer / GRN Dispatch tab back to
        the matching row in the AI Extract result tree, so all three tabs stay
        consistent (e.g. an "UNKNOWN SUPPLIER" row corrected in Dispatch is no
        longer shown as unknown in AI Extract either)."""
        tree = getattr(self, "_aix_tree", None)
        row_map = getattr(self, "_aix_row_map", None)
        if tree is None or row_map is None or not doc_id:
            return
        for rid, res in list(row_map.items()):
            if res.get("doc_id") != doc_id:
                continue
            res["supplier"] = supplier
            res["grn"] = grn
            res["invoice"] = invoice_raw
            if file_name:
                res["file"] = file_name
            try:
                vals = list(tree.item(rid, "values"))
                if vals and len(vals) >= 12:
                    if file_name:
                        vals[0] = file_name
                    vals[2] = supplier            # Supplier
                    vals[5] = invoice_raw          # Invoice #
                    vals[11] = grn                 # GRN
                    tree.item(rid, values=vals)
            except Exception:
                pass
            break

    def _refresh_dispatch_row_tag(self, row_id: str):
        """Recompute the colour tag of a GRN Dispatch row after an inline edit
        so a corrected supplier no longer shows the orange 'unknown' colour."""
        result = self._dispatch_row_map.get(row_id)
        if not result:
            return
        conf = result.get("confidence", 0.0)
        conf_thr = self.cfg.get("app_settings", {}).get("confidence_warn_threshold", 80)
        sup = (result.get("supplier", "") or "").upper()
        if not result.get("is_valid", True):
            tag = "invalid"
        elif sup == "UNKNOWN SUPPLIER" or not sup:
            tag = "unknown"
        elif conf and conf < conf_thr:
            tag = "low_conf"
        else:
            tag = "valid"
        try:
            self._dispatch_tree.item(row_id, tags=(tag,))
        except Exception:
            pass

    def _sync_dispatch_to_rename(self, dispatch_result: Dict):
        doc_id = dispatch_result.get("doc_id", "")
        row_id, rename_result = self._find_rename_row_by_doc_id(doc_id)
        if not row_id or not rename_result:
            return

        vals = list(self._rename_tree.item(row_id, "values"))
        if not vals:
            return

        new_file = dispatch_result.get("file", "")
        new_supplier = dispatch_result.get("supplier", "")
        new_grn = dispatch_result.get("grn", "")
        new_invoice_raw = dispatch_result.get("invoice", "")

        vals[0] = new_file
        vals[1] = new_file
        vals[2] = new_supplier
        vals[4] = new_grn
        vals[5] = f"IN {new_invoice_raw}" if new_invoice_raw else "NO-INVOICE"

        self._rename_tree.item(row_id, values=vals)

        rename_result.update({
            "file": new_file,
            "new_name": new_file,
            "supplier": new_supplier,
            "grn": new_grn,
            "invoice": new_invoice_raw,
            "invoice_dispatch": new_invoice_raw,
            "dest_path": dispatch_result.get("raw_path", rename_result.get("dest_path", "")),
        })

    # ------------------------------------------------------------------
    # SUPPLIER CONFIG UI
    # ------------------------------------------------------------------
    def _populate_supplier_tree(self):
        for i in self._sup_tree.get_children():
            self._sup_tree.delete(i)
        self._sup_all_rows = []
        suppliers = self.cfg.get("suppliers", [])
        aliases   = self.cfg.get("aliases", {})
        for sup in suppliers:
            alias_str = ", ".join(aliases.get(sup, []))
            iid = self._sup_tree.insert("", "end", values=(sup, alias_str))
            self._sup_all_rows.append(iid)

    def _on_sup_tree_edit(self, row_id, col_index, old_val, new_val):
        vals = list(self._sup_tree.item(row_id, "values"))
        vals[col_index] = new_val
        self._sup_tree.item(row_id, values=vals)

    def _add_supplier_row(self):
        self._sup_tree.insert("", "end", values=("NEW SUPPLIER", ""))

    def _remove_supplier_row(self):
        selected = self._sup_tree.selection()
        if not selected:
            messagebox.showinfo("Remove Supplier", "Select a row first.")
            return
        for row_id in selected:
            vals = self._sup_tree.item(row_id, "values")
            name = vals[0] if vals else ""
            if messagebox.askyesno("Remove Supplier", f"Remove '{name}'?"):
                self._sup_tree.delete(row_id)

    def _save_suppliers(self):
        suppliers = []
        aliases   = {}
        for row_id in self._sup_tree.get_children():
            vals = self._sup_tree.item(row_id, "values")
            name = str(vals[0]).strip().upper() if vals else ""
            alias_raw = str(vals[1]).strip() if len(vals) > 1 else ""
            if not name or name == "NEW SUPPLIER":
                continue
            suppliers.append(name)
            if alias_raw:
                alias_list = [a.strip().upper() for a in alias_raw.split(",") if a.strip()]
                if alias_list:
                    aliases[name] = alias_list
        self.cfg["suppliers"] = suppliers
        self.cfg["aliases"]   = aliases
        self._save_config()
        messagebox.showinfo("Suppliers Saved", f"Saved {len(suppliers)} supplier(s).")

    # ------------------------------------------------------------------
    # INVOICE PATTERNS (Settings) — search / add / deactivate
    # ------------------------------------------------------------------
    def _populate_invoice_pattern_tree(self):
        tree = getattr(self, "_inv_tree", None)
        if tree is None:
            return
        for iid in tree.get_children():
            tree.delete(iid)
        self._inv_all_rows = []
        self._inv_row_data = {}  # iid -> {"supplier":..., "index":...}

        for row in sl.list_invoice_patterns(self.cfg):
            active = row["active"]
            iid = tree.insert(
                "", "end",
                tags=("active" if active else "inactive",),
                values=(
                    row["supplier"],
                    row["template"],
                    row["regex"],
                    "Active" if active else "Inactive",
                    row["count"],
                ),
            )
            self._inv_row_data[iid] = {"supplier": row["supplier"], "index": row["index"]}
            self._inv_all_rows.append(iid)

    def _add_invoice_pattern_dialog(self):
        suppliers = sorted(self.cfg.get("suppliers", []))
        if not suppliers:
            messagebox.showinfo("Add Invoice Pattern", "Add a supplier first (Suppliers & Aliases above).")
            return

        win = tk.Toplevel(self)
        win.title("Add Invoice Pattern")
        win.geometry("520x320")
        win.configure(bg=BG)
        win.transient(self)
        win.grab_set()

        pad = dict(padx=20, pady=(12, 0))

        tk.Label(win, text="Supplier", bg=BG, fg=MUTED, font=("Segoe UI", 9, "bold")).pack(anchor="w", **pad)
        sup_var = tk.StringVar(value=suppliers[0])
        sup_combo = ttk.Combobox(win, textvariable=sup_var, values=suppliers, state="readonly")
        sup_combo.pack(fill=tk.X, padx=20, pady=(4, 0))

        mode_var = tk.StringVar(value="sample")
        mode_row = tk.Frame(win, bg=BG)
        mode_row.pack(anchor="w", padx=20, pady=(14, 0))
        ttk.Radiobutton(mode_row, text="From a sample invoice number", variable=mode_var, value="sample").pack(anchor="w")
        ttk.Radiobutton(mode_row, text="From a raw regex (advanced)", variable=mode_var, value="regex").pack(anchor="w")

        tk.Label(win, text="Value", bg=BG, fg=MUTED, font=("Segoe UI", 9, "bold")).pack(anchor="w", **pad)
        value_var = tk.StringVar()
        value_ent = tk.Entry(
            win, textvariable=value_var, bg=PANEL2, fg=TEXT, insertbackground=TEXT,
            relief="flat", font=("Segoe UI", 10), bd=0,
            highlightthickness=1, highlightbackground=PANEL3, highlightcolor=ACCENT,
        )
        value_ent.pack(fill=tk.X, padx=20, pady=(4, 0), ipady=6)

        hint_var = tk.StringVar(value='e.g.  BB-2026-0451   (or a regex like  BB-(\\d{4,8})  in advanced mode)')
        tk.Label(win, textvariable=hint_var, bg=BG, fg=MUTED, font=("Segoe UI", 8), wraplength=470, justify="left").pack(
            anchor="w", padx=20, pady=(4, 0)
        )

        def _on_mode_change(*_):
            if mode_var.get() == "sample":
                hint_var.set('e.g.  BB-2026-0451   (a real invoice number — the pattern is derived from it)')
            else:
                hint_var.set('e.g.  BB-(\\d{4,8})   (a Python regex with the invoice number in a capture group)')
        mode_var.trace_add("write", _on_mode_change)

        btn_row = tk.Frame(win, bg=BG)
        btn_row.pack(fill=tk.X, padx=20, pady=20, side=tk.BOTTOM)

        def _save():
            supplier = sup_var.get().strip()
            value = value_var.get().strip()
            if not value:
                messagebox.showwarning("Add Invoice Pattern", "Enter a sample invoice number or a regex.", parent=win)
                return
            try:
                if mode_var.get() == "sample":
                    sl.add_invoice_pattern(supplier, sample_invoice_no=value, cfg=self.cfg)
                else:
                    sl.add_invoice_pattern(supplier, manual_regex=value, cfg=self.cfg)
            except ValueError as e:
                messagebox.showerror("Add Invoice Pattern", str(e), parent=win)
                return
            self._save_config()
            self._populate_invoice_pattern_tree()
            win.destroy()
            self._set_status(f"Invoice pattern added for {supplier}.", SUCCESS)

        ttk.Button(btn_row, text="Cancel", command=win.destroy).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(btn_row, text="💾  Save", style="Accent.TButton", command=_save).pack(side=tk.RIGHT)

    def _toggle_invoice_pattern_active(self):
        selected = self._inv_tree.selection()
        if not selected:
            messagebox.showinfo("Toggle Active", "Select one or more pattern rows first.")
            return
        changed = 0
        for row_id in selected:
            info = self._inv_row_data.get(row_id)
            if not info:
                continue
            current_active = "Active" in self._inv_tree.item(row_id, "values")
            try:
                sl.set_pattern_active(info["supplier"], info["index"], not current_active, cfg=self.cfg)
                changed += 1
            except ValueError as e:
                messagebox.showerror("Toggle Active", str(e))
        if changed:
            self._save_config()
            self._populate_invoice_pattern_tree()
            self._set_status(f"Toggled {changed} invoice pattern(s).", SUCCESS)

    # ------------------------------------------------------------------
    # TEST SINGLE PDF DEBUG
    # ------------------------------------------------------------------
    def _test_single_pdf_debug(self):
        path = filedialog.askopenfilename(
            title="Select PDF to test",
            filetypes=[("PDF files", "*.pdf")],
            initialdir=self.dirs.get("scanned", "."),
        )
        if not path:
            return

        win = tk.Toplevel(self)
        win.title(f"Debug — {os.path.basename(path)}")
        win.geometry("1020x740")
        win.configure(bg=BG)

        hdr = tk.Frame(win, bg=PANEL2, height=44)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        tk.Label(hdr, text=f"Debug: {os.path.basename(path)}",
                 bg=PANEL2, fg=TEXT, font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT, padx=16, pady=12)
        status_lbl = tk.Label(hdr, text="Extracting...", bg=PANEL2, fg=MUTED, font=("Segoe UI", 9))
        status_lbl.pack(side=tk.RIGHT, padx=16)

        nb = ttk.Notebook(win)
        nb.pack(fill=tk.BOTH, expand=True, padx=12, pady=(8, 12))

        def make_text_tab(title):
            f = ttk.Frame(nb)
            nb.add(f, text=f"  {title}  ")
            txt = tk.Text(f, bg=PANEL, fg=TEXT, font=("Consolas", 9),
                          wrap="word", relief="flat", insertbackground=TEXT)
            sb  = ttk.Scrollbar(f, orient="vertical", command=txt.yview)
            txt.configure(yscrollcommand=sb.set)
            txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            sb.pack(side=tk.RIGHT, fill=tk.Y)
            return txt

        raw_text   = make_text_tab("Raw Text")
        match_text = make_text_tab("Extraction Results")

        for tag, fg_color in [
            ("header",  ACCENT),  ("found",   SUCCESS),
            ("missing", ERROR),   ("label",   WARNING),
            ("value",   TEXT),
        ]:
            match_text.tag_configure(tag, foreground=fg_color)
        for tag, bg_color in [
            ("supplier_hl", "#1d4ed8"), ("grn_hl", "#065f46"),
            ("invoice_hl",  "#7c3aed"), ("po_hl",  "#92400e"),
        ]:
            raw_text.tag_configure(tag, background=bg_color, foreground="white")

        def run_debug():
            mode = self.cfg.get("app_settings", {}).get("processing_mode", "legacy")
            try:
                core = self._extract_core_fields_for_file(path, mode)
            except Exception as e:
                win.after(0, lambda: status_lbl.configure(text=f"Error: {e}"))
                win.after(0, lambda: raw_text.insert("end", traceback.format_exc()))
                return

            extracted_text = core.get("text", "")
            supplier       = core.get("supplier", "")
            grn            = core.get("grn", "")
            po             = core.get("po", "")
            inv_fn         = core.get("invoice_filename", "")
            inv_disp       = core.get("invoice_dispatch", "")
            date_val       = core.get("date", "")
            confidence     = core.get("confidence", 0.0)
            totals = {k: core.get(k.lower(), "") for k in ("USD", "MVR", "EUR", "GBP", "SGD")}

            def update_ui():
                status_lbl.configure(text="Done.")
                raw_text.delete("1.0", "end")
                raw_text.insert("end", extracted_text or "(no text extracted)")

                def highlight(term, tag):
                    if not term or len(term) < 3:
                        return
                    start = "1.0"
                    while True:
                        idx = raw_text.search(term, start, nocase=True, stopindex="end")
                        if not idx:
                            break
                        raw_text.tag_add(tag, idx, f"{idx}+{len(term)}c")
                        start = f"{idx}+{len(term)}c"

                highlight(supplier, "supplier_hl")
                highlight(grn.replace("RC-MAM-", "").split("-")[0] if grn else "", "grn_hl")
                highlight(inv_disp, "invoice_hl")
                highlight(po, "po_hl")

                match_text.delete("1.0", "end")

                def ins(text, tag=None):
                    match_text.insert("end", text, tag or ())

                ins(f"=== DEBUG: {os.path.basename(path)} ===\n\n", "header")
                ins(f"Mode: {mode.upper()}\n\n", "label")

                for label, val in [
                    ("Supplier",          supplier),
                    ("Confidence",        f"{confidence:.1f}%"),
                    ("GRN",               grn),
                    ("Invoice (filename)",inv_fn),
                    ("Invoice (dispatch)",inv_disp),
                    ("PO Number",         po),
                    ("Date",              date_val),
                ]:
                    ins(f"  {label:<28}", "label")
                    ok = val and val not in ("NO-GRN", "NO-INVOICE", "UNKNOWN SUPPLIER", "", "0.0%")
                    ins(f"{val or '(not found)'}\n", "found" if ok else "missing")

                ins("\nCurrency Totals:\n", "header")
                for cur, amt in totals.items():
                    ins(f"  {cur:<28}", "label")
                    ins(f"{amt if amt else '(none)'}\n", "found" if amt else "missing")

                ins("\nProposed filename:\n", "header")
                ins(f"  {supplier} GRN {grn} {inv_fn}.pdf\n", "found")

            win.after(0, update_ui)

        threading.Thread(target=run_debug, daemon=True).start()

    # ------------------------------------------------------------------
    # TEST CURRENT OCR ENGINE
    # ------------------------------------------------------------------
    def _test_current_engine(self):
        engine = self.cfg["app_settings"].get("ocr_engine", "tesseract")
        if not self._check_engine(engine) and engine != "tesseract":
            messagebox.showerror("Not Installed",
                                 f"{ENGINE_LABELS[engine]} not installed.\n{ENGINE_INSTALL[engine]}")
            return

        def run():
            try:
                canvas = np.full((100, 450), 255, dtype=np.uint8)
                cv2.putText(canvas, "INVOICE NO: INV-2024-001",   (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.75, 0, 2)
                cv2.putText(canvas, "RC-MAM-000000001",           (10, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.75, 0, 2)
                r  = self._run_ocr(canvas)
                ok = bool(r.strip())
                self.after(0, lambda: messagebox.showinfo(
                    "Engine Test",
                    f"{ENGINE_LABELS[engine]}\n\n{r.strip() or '(empty)'}\n\n{'✓ OK' if ok else '✗ Empty output'}",
                ))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Engine Test Failed", str(e)))

        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------------
    # CLEANUP ON EXIT
    # ------------------------------------------------------------------
    def _cleanup_on_exit(self):
        """Stop watcher, cancel pending timers, and release resources on app exit."""
        # Tk does not automatically suppress every scheduled callback when a
        # root window is destroyed. Cancel all registered callbacks first so a
        # watcher/log refresh cannot attempt to update a closed interface.
        try:
            for callback_id in self.tk.call("after", "info"):
                self.after_cancel(callback_id)
        except Exception as e:
            logging.debug(f"Cleanup: after-cancel skipped: {e}")
        try:
            self._stop_watcher()
        except Exception as e:
            logging.warning(f"Cleanup: watcher stop failed: {e}")
        # Signal any running workers to stop
        self._cancel_requested = True
        logging.info("Application shutdown complete.")


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------
def main():
    app = MaafushivaruHub()
    try:
        app.mainloop()
    finally:
        app._cleanup_on_exit()


if __name__ == "__main__":
    main()
