"""
supplier_learning.py
---------------------
Drop this file next to maafushivaru_hub.py / config.json.

Gives the app four abilities, all operating on the SAME config.json schema
already in the repo (suppliers[], aliases{}, invoice_formats{}):

1. learn_from_correction(...)
   Call this whenever the user manually fixes a wrong supplier name on a
   row (Dispatch tab / Renamer tab). It:
     - adds the OCR text fragment that was mis-read as a new alias of the
       CORRECT supplier, so the same misread is auto-fixed next time.
     - looks at the invoice number that was extracted for that row and
       either bumps the matching invoice_formats entry's count, or adds a
       brand new regex format for that supplier if the shape is new.

2. add_supplier_interactive(...) / add_supplier(...)
   Backing logic for a right-click "Add Supplier" menu item. Adds the
   supplier to suppliers[], seeds aliases[] and invoice_formats[] from a
   sample invoice number (or a manually typed regex), and saves.

3. cross_match_and_autocorrect(...)  [+ build_ownership_index,
   identify_supplier_by_invoice_number, reconstruct_invoice_number]
   Backs the new Settings toggle "auto_fix_supplier_by_invoice_pattern".
   If the supplier name is misread but the invoice number's format is
   owned by exactly one supplier, fixes the supplier from the invoice
   number. If the supplier is right but the invoice number looks
   OCR-broken, repairs it using that supplier's own known formats.
   Formats with no distinguishing literal (pure digits) or shared by more
   than one different supplier are never used for identification.

4. count_pending_pdfs(folder)
   One-liner used by the Dashboard tab to show how many PDFs are sitting
   in the watched/transfer folder right now.

All writes go through save_config(), which does an atomic write + timestamped
backup, so a crash mid-write can never corrupt config.json.
"""

from __future__ import annotations
import json
import os
import re
import shutil
import difflib
from datetime import datetime
from pathlib import Path
from typing import Optional

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
BACKUP_DIR = Path(__file__).resolve().parent / "LOGS" / "config_backups"

_SEP = re.compile(r"[^A-Z0-9&]+")


def _norm(s: str) -> str:
    s = s.upper().strip()
    s = _SEP.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


# --------------------------------------------------------------------------- #
# config load/save
# --------------------------------------------------------------------------- #

def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg: dict, path: Path = CONFIG_PATH) -> None:
    """Atomic write with a timestamped backup, so a bad write never eats
    the user's 511-supplier config."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(path, BACKUP_DIR / f"config_{stamp}.json")
        # keep only the last 30 backups
        backups = sorted(BACKUP_DIR.glob("config_*.json"))
        for old in backups[:-30]:
            old.unlink(missing_ok=True)

    tmp_path = path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)  # atomic on both Windows and POSIX


# --------------------------------------------------------------------------- #
# invoice-number -> regex-format helpers (mirrors the shapes already used in
# config.json's "invoice_formats" block, e.g. "INV-{0}/SG/{1}")
# --------------------------------------------------------------------------- #

_TOKEN_RE = re.compile(r"[A-Za-z&]+|\d+")


def _build_format_from_sample(invoice_no: str) -> Optional[dict]:
    """Turn a real invoice-number sample (e.g. 'BB-1029384') into a
    template/regex pair in the same shape the rest of config.json uses."""
    invoice_no = invoice_no.strip()
    if not invoice_no:
        return None
    tokens = _TOKEN_RE.findall(invoice_no)
    if not tokens:
        return None

    template_parts = []
    regex_parts = []
    shape_parts = []
    digit_lengths = []
    slot = 0
    for tok in tokens:
        if tok.isdigit():
            template_parts.append("{%d}" % slot)
            regex_parts.append(r"(\d{%d,%d})" % (max(2, len(tok) - 1), len(tok) + 2))
            shape_parts.append(r"(\d{%d,%d})" % (max(2, len(tok) - 1), len(tok) + 2))
            digit_lengths.append(len(tok))
            slot += 1
        else:
            template_parts.append(tok)
            regex_parts.append(re.escape(tok))
            shape_parts.append(r"(?:[A-Z&]{1,%d})" % max(2, len(tok)))

    template = "".join(template_parts)
    # join literal/number pieces with a flexible separator, same convention
    # used elsewhere in config.json
    joiner = r"(?:[-/\s.]{0,2})"
    regex = joiner.join(regex_parts)
    shape_regex = joiner.join(shape_parts)

    return {
        "template": template,
        "digit_lengths": digit_lengths or [len(invoice_no)],
        "regex": regex,
        "shape_regex": shape_regex,
        "count": 1,
        "share": 1.0,
        "active": True,
    }


def _format_signature(fmt: dict) -> str:
    """Two formats are 'the same shape' if their regex is identical."""
    return fmt.get("regex", "")


# --------------------------------------------------------------------------- #
# 1. Learn from a manual correction
# --------------------------------------------------------------------------- #

def learn_from_correction(
    extracted_text: str,
    wrong_supplier_guess: Optional[str],
    correct_supplier: str,
    extracted_invoice_no: Optional[str] = None,
    cfg: Optional[dict] = None,
) -> dict:
    """
    Call this the moment the user fixes a wrong supplier name on a row.

    extracted_text        - the raw OCR text block for that document (or at
                             least the line where the supplier name was read)
    wrong_supplier_guess   - whatever the app had guessed (may be None/blank)
    correct_supplier       - the CANONICAL supplier name the user selected
                             (must exist in cfg["suppliers"])
    extracted_invoice_no   - the invoice number the app pulled from the page,
                             if any, so its shape can be learned too.

    Returns a small report dict describing what was changed, so the caller
    can show a toast/notification ("Learned: added 'EURO MARK' as an alias
    of EURO MARKETING PVT LTD").
    """
    owns_cfg = cfg is None
    cfg = cfg or load_config()
    report = {"alias_added": None, "format_added": None, "format_bumped": None}

    if correct_supplier not in cfg.get("suppliers", []):
        raise ValueError(
            f"'{correct_supplier}' is not a known supplier. "
            f"Use add_supplier() first to onboard it."
        )

    # ---- figure out the mis-read fragment worth remembering as an alias ----
    candidate_fragment = None
    if wrong_supplier_guess and wrong_supplier_guess.strip():
        candidate_fragment = wrong_supplier_guess.strip()
    else:
        # fall back to scanning extracted_text for the line that looks most
        # like a supplier/company line (all-caps line with >=2 words,
        # nearest match to the correct supplier by fuzzy ratio)
        best_line, best_score = None, 0.0
        for line in (extracted_text or "").splitlines():
            line = line.strip()
            if len(line) < 3 or not re.search(r"[A-Za-z]{3,}", line):
                continue
            score = difflib.SequenceMatcher(None, _norm(line), _norm(correct_supplier)).ratio()
            if score > best_score:
                best_score, best_line = score, line
        if best_score >= 0.55:
            candidate_fragment = best_line

    if candidate_fragment:
        aliases = cfg.setdefault("aliases", {}).setdefault(correct_supplier, [])
        existing_norm = {_norm(correct_supplier)} | {_norm(a) for a in aliases}
        if _norm(candidate_fragment) not in existing_norm:
            aliases.append(candidate_fragment)
            report["alias_added"] = candidate_fragment

    # ---- learn the invoice-number shape for this supplier ----
    if extracted_invoice_no:
        inv_formats = cfg.setdefault("invoice_formats", {}).setdefault(
            correct_supplier, {"sample_count": 0, "formats": []}
        )
        new_fmt = _build_format_from_sample(extracted_invoice_no)
        if new_fmt:
            matched = None
            for existing in inv_formats["formats"]:
                if not existing.get("active", True):
                    continue  # a deactivated pattern must stay off, even if OCR keeps producing it
                # same shape if the literal (non-digit) tokens line up
                if existing.get("template", "").replace("{0}", "").replace("{1}", "") == \
                   new_fmt["template"].replace("{0}", "").replace("{1}", ""):
                    matched = existing
                    break
            if matched:
                matched["count"] = matched.get("count", 0) + 1
                inv_formats["sample_count"] += 1
                _rebalance_shares(inv_formats)
                report["format_bumped"] = matched["template"]
            else:
                inv_formats["formats"].append(new_fmt)
                inv_formats["sample_count"] += 1
                _rebalance_shares(inv_formats)
                report["format_added"] = new_fmt["template"]

    if owns_cfg:
        save_config(cfg)
    return report


def _rebalance_shares(inv_formats: dict) -> None:
    total = sum(f.get("count", 1) for f in inv_formats["formats"]) or 1
    for f in inv_formats["formats"]:
        f["share"] = round(f.get("count", 1) / total, 3)
    inv_formats["formats"].sort(key=lambda f: -f.get("count", 0))


# --------------------------------------------------------------------------- #
# 2. Add a brand-new supplier (right-click "Add Supplier")
# --------------------------------------------------------------------------- #

def add_supplier(
    canonical_name: str,
    aliases: Optional[list[str]] = None,
    sample_invoice_no: Optional[str] = None,
    manual_regex: Optional[str] = None,
    cfg: Optional[dict] = None,
) -> dict:
    """
    canonical_name     - the clean supplier name to store, e.g. "TOYA PUMPS SINGAPORE"
    aliases            - any known misspellings/short forms to seed
    sample_invoice_no  - one real invoice number, e.g. "TPSPL-2026I-00457",
                          used to auto-derive the regex format
    manual_regex       - if you'd rather hand-write the regex yourself,
                          pass it here instead of sample_invoice_no
    """
    owns_cfg = cfg is None
    cfg = cfg or load_config()
    canonical_name = canonical_name.strip().upper()

    suppliers = cfg.setdefault("suppliers", [])
    if canonical_name not in suppliers:
        suppliers.append(canonical_name)
        suppliers.sort()

    if aliases:
        alist = cfg.setdefault("aliases", {}).setdefault(canonical_name, [])
        existing_norm = {_norm(a) for a in alist} | {_norm(canonical_name)}
        for a in aliases:
            if a and _norm(a) not in existing_norm:
                alist.append(a.strip())
                existing_norm.add(_norm(a))

    fmt = None
    if manual_regex:
        fmt = {
            "template": manual_regex,
            "digit_lengths": [],
            "regex": manual_regex,
            "shape_regex": manual_regex,
            "count": 1,
            "share": 1.0,
            "active": True,
        }
    elif sample_invoice_no:
        fmt = _build_format_from_sample(sample_invoice_no)

    if fmt:
        cfg.setdefault("invoice_formats", {})[canonical_name] = {
            "sample_count": 1,
            "formats": [fmt],
        }

    if owns_cfg:
        save_config(cfg)
    return {"supplier": canonical_name, "format": fmt}


# --------------------------------------------------------------------------- #
# 3. Cross-match: invoice-number PATTERN <-> supplier identity
#    (backs the new "auto-fix supplier via invoice pattern" setting)
# --------------------------------------------------------------------------- #
#
# Idea, per your instructions:
#   - If the supplier name OCR is wrong but the invoice number was read
#     cleanly AND its literal format (e.g. "BB-", "EMH-", "MSI-") belongs to
#     exactly ONE supplier -> trust the invoice number, fix the supplier.
#   - If the supplier name is right but the invoice number looks OCR-broken
#     -> use that supplier's own known format(s) to repair/reconstruct it.
#   - NEVER use a format for identification if:
#       a) it has no literal prefix/suffix at all (pure digits like "54621"
#          -> template "{0}") -- too common, could be anyone, OR
#       b) the same literal format is already used by more than one
#          DIFFERENT supplier (e.g. plain "INV-" is used by MIAAF, LILY
#          INTERNATIONAL, GRAPE EXPECTATIONS, etc. -- ambiguous, skip).
#
# Toggle in config.json -> app_settings:
#   "auto_fix_supplier_by_invoice_pattern": false   (default off)
#
# Add that key to config.json once (Settings tab checkbox writes it), then
# call cross_match_and_autocorrect() per document when it's True.

import string
from collections import defaultdict

_PLACEHOLDER_RE = re.compile(r"\{\d+\}")

# common OCR digit/letter confusions, used only to REPAIR an invoice number
# once the supplier (and therefore expected format) is already known/trusted
_OCR_CONFUSIONS = {
    "O": "0", "o": "0", "D": "0",
    "I": "1", "l": "1", "|": "1", "i": "1",
    "Z": "2",
    "E": "3",
    "A": "4",
    "S": "5", "s": "5",
    "G": "6", "b": "6",
    "T": "7",
    "B": "8",
    "g": "9", "q": "9",
}


def _literal_key(fmt: dict) -> str:
    """The part of a format that actually identifies a supplier: every
    letter OUTSIDE any regex/template group, stripped of separators and
    placeholders. Empty string means 'no distinguishing literal' -> always
    treated as generic. Works for both auto-derived templates (e.g.
    'BB-{0}') and hand-typed raw regexes (e.g. 'BB(?:[-/\\s.]{0,2})(\\d{6,8})')
    -- {0}/{1} placeholders and (...) regex groups are both stripped the
    same way before the literal letters are pulled out, so a manually
    typed regex is never mistaken for its own literal prefix+digits."""
    src = fmt.get("template") or fmt.get("regex") or ""
    src = _PLACEHOLDER_RE.sub("", src)
    # iteratively strip parenthesized groups (handles multiple/sequential
    # groups; regex groups here are never meaningfully nested in our formats)
    while True:
        stripped = re.sub(r"\([^()]*\)", "", src)
        if stripped == src:
            break
        src = stripped
    return re.sub(r"[^A-Za-z]", "", src).upper()


def build_ownership_index(cfg: dict) -> dict:
    """Scan every supplier's invoice_formats and figure out which literal
    formats are safe to use for supplier identification (exactly one owner,
    non-generic) vs which must never be used that way."""
    owners = defaultdict(set)          # literal_key -> {supplier, ...}
    formats_by_key = defaultdict(list)  # literal_key -> [(supplier, fmt), ...]

    for supplier, data in cfg.get("invoice_formats", {}).items():
        for fmt in data.get("formats", []):
            if not fmt.get("active", True):
                continue  # deactivated -> never used for identification
            key = _literal_key(fmt)
            if not key:
                continue  # pure-numeric format, never diagnostic
            owners[key].add(supplier)
            formats_by_key[key].append((supplier, fmt))

    unique = {}   # literal_key -> (supplier, fmt)   safe to use
    ambiguous = set()  # literal_key -> shared by 2+ different suppliers, never use
    for key, owner_set in owners.items():
        if len(owner_set) == 1:
            supplier, fmt = formats_by_key[key][0]
            unique[key] = (supplier, fmt)
        else:
            ambiguous.add(key)

    return {"unique": unique, "ambiguous": ambiguous}


def _extract_literal_and_digits(invoice_no: str):
    """Split a raw invoice string into its uppercased literal skeleton
    (letters/symbols only, placeholders removed) and its digit run(s), so it
    can be looked up in the ownership index the same way formats are keyed."""
    literal = re.sub(r"[\d\s\-/.]+", "", invoice_no).upper()
    return literal


def identify_supplier_by_invoice_number(invoice_no: str, ownership_index: dict):
    """Returns (supplier, matched_format) if the invoice number's literal
    format uniquely belongs to one supplier, else (None, None)."""
    if not invoice_no or not invoice_no.strip():
        return None, None
    key = _extract_literal_and_digits(invoice_no)
    if not key:
        return None, None  # pure digits -> never diagnostic, per your rule
    if key in ownership_index["ambiguous"]:
        return None, None  # shared by multiple different suppliers -> skip
    match = ownership_index["unique"].get(key)
    if not match:
        return None, None
    supplier, fmt = match
    if not re.search(fmt.get("regex", ""), invoice_no, re.IGNORECASE):
        # literal skeleton matched but digit count/shape didn't -> too risky
        return None, None
    return supplier, fmt


# characters that can be OCR-confused with a digit -- used ONLY inside the
# digit slots of a format's own regex, never against the literal prefix, so
# "EMH-" can never be corrupted while repairing the digits after it.
_FUZZY_DIGIT_CLASS = "[0-9" + "".join(sorted(set(_OCR_CONFUSIONS.keys()))) + "]"
_DIGIT_GROUP_RE = re.compile(r"\\d")


def _fuzzy_pattern(pattern: str) -> str:
    """Widen only the \\d digit-group parts of a known-good format regex to
    also accept common OCR digit/letter confusions, leaving every literal
    character (the supplier's actual prefix/suffix) untouched."""
    return _DIGIT_GROUP_RE.sub(_FUZZY_DIGIT_CLASS, pattern)


def _clean_digits(s: str) -> str:
    return "".join(_OCR_CONFUSIONS.get(ch, ch) for ch in s)


def reconstruct_invoice_number(raw_invoice_text: str, supplier: str, cfg: dict):
    """Supplier is trusted/known; the invoice number OCR looks broken. Try
    the supplier's own known formats, first exactly, then with the DIGIT
    slots (only) widened to tolerate OCR confusions like O/0, I/1, S/5 --
    the literal prefix/suffix must still match exactly. Returns the
    cleanest match, or None."""
    formats = cfg.get("invoice_formats", {}).get(supplier, {}).get("formats", [])
    if not formats or not raw_invoice_text:
        return None

    for fmt in sorted(formats, key=lambda f: -f.get("share", 0)):
        if not fmt.get("active", True):
            continue  # deactivated -> skip when repairing too
        pattern = fmt.get("regex", "")
        if not pattern:
            continue
        # 1) exact match first
        m = re.search(pattern, raw_invoice_text, re.IGNORECASE)
        if not m:
            # 2) retry allowing OCR-confusable chars only inside digit slots
            m = re.search(_fuzzy_pattern(pattern), raw_invoice_text, re.IGNORECASE)
        if m:
            cleaned = fmt.get("template", "{0}")
            for idx, group in enumerate(m.groups()):
                cleaned = cleaned.replace("{%d}" % idx, _clean_digits(group))
            return {"invoice_no": cleaned, "matched_format": fmt, "supplier": supplier}
    return None


def cross_match_and_autocorrect(
    supplier_guess: Optional[str],
    invoice_no_raw: Optional[str],
    cfg: dict,
    ownership_index: Optional[dict] = None,
) -> dict:
    """
    The function to call per-document when
    app_settings["auto_fix_supplier_by_invoice_pattern"] is True.

    Returns:
        {
          "supplier": <final supplier to use>,
          "invoice_no": <final, possibly repaired invoice number>,
          "action": "none" | "supplier_fixed_from_pattern" | "invoice_repaired",
          "reason": <human-readable audit note for the log>,
        }
    Never overrides anything unless it found an UNAMBIGUOUS, non-generic
    match -- silence (action="none") is the safe default.
    """
    ownership_index = ownership_index or build_ownership_index(cfg)
    result = {
        "supplier": supplier_guess,
        "invoice_no": invoice_no_raw,
        "action": "none",
        "reason": "",
    }
    if not invoice_no_raw:
        return result

    # Case A: invoice number reads cleanly -> does its format uniquely
    # belong to a DIFFERENT supplier than what OCR guessed for the name?
    id_supplier, id_fmt = identify_supplier_by_invoice_number(invoice_no_raw, ownership_index)
    if id_supplier and id_supplier != supplier_guess:
        result["supplier"] = id_supplier
        result["action"] = "supplier_fixed_from_pattern"
        result["reason"] = (
            f"Invoice # '{invoice_no_raw}' matches the format "
            f"'{id_fmt.get('template')}', used exclusively by {id_supplier} "
            f"-> corrected supplier from '{supplier_guess or '(blank)'}'."
        )
        return result

    # Case B: supplier name looks trustworthy (already known), but the
    # invoice number didn't cleanly match ITS OWN known formats -> repair it.
    if supplier_guess and supplier_guess in cfg.get("invoice_formats", {}):
        already_clean = any(
            re.search(f.get("regex", ""), invoice_no_raw, re.IGNORECASE)
            for f in cfg["invoice_formats"][supplier_guess].get("formats", [])
        )
        if not already_clean:
            repaired = reconstruct_invoice_number(invoice_no_raw, supplier_guess, cfg)
            if repaired:
                result["invoice_no"] = repaired["invoice_no"]
                result["action"] = "invoice_repaired"
                result["reason"] = (
                    f"'{invoice_no_raw}' didn't match any known {supplier_guess} "
                    f"format; repaired to '{repaired['invoice_no']}' using "
                    f"format '{repaired['matched_format'].get('template')}'."
                )
    return result


# --------------------------------------------------------------------------- #
# 3c. Settings panel backend: search / add / deactivate / reactivate patterns
# --------------------------------------------------------------------------- #

def list_invoice_patterns(cfg: dict, search: str = "") -> list[dict]:
    """Flat, UI-ready list of every invoice pattern across every supplier,
    for the Settings search box. Each item:
        {supplier, template, regex, active, count, share, index}
    `index` is the position within that supplier's formats[] list, needed to
    address a specific pattern for deactivate/reactivate/edit calls.
    Matches search against supplier name AND template/regex text.
    """
    q = (search or "").strip().upper()
    out = []
    for supplier, data in cfg.get("invoice_formats", {}).items():
        for i, fmt in enumerate(data.get("formats", [])):
            if q and q not in supplier.upper() and q not in fmt.get("template", "").upper() \
                    and q not in fmt.get("regex", "").upper():
                continue
            out.append({
                "supplier": supplier,
                "index": i,
                "template": fmt.get("template", ""),
                "regex": fmt.get("regex", ""),
                "active": fmt.get("active", True),
                "count": fmt.get("count", 0),
                "share": fmt.get("share", 0.0),
            })
    out.sort(key=lambda r: (r["supplier"], -r["count"]))
    return out


def add_invoice_pattern(
    supplier: str,
    sample_invoice_no: Optional[str] = None,
    manual_regex: Optional[str] = None,
    cfg: Optional[dict] = None,
) -> dict:
    """Add a pattern to an EXISTING supplier from the Settings panel. Exactly
    one of sample_invoice_no / manual_regex should be given -- manual_regex
    wins if both are somehow passed."""
    owns_cfg = cfg is None
    cfg = cfg or load_config()

    if supplier not in cfg.get("suppliers", []):
        raise ValueError(f"'{supplier}' is not a known supplier.")

    if manual_regex:
        try:
            re.compile(manual_regex)
        except re.error as e:
            raise ValueError(f"That regex doesn't compile: {e}")
        fmt = {
            "template": manual_regex,
            "digit_lengths": [],
            "regex": manual_regex,
            "shape_regex": manual_regex,
            "count": 1,
            "share": 1.0,
            "active": True,
        }
    elif sample_invoice_no:
        fmt = _build_format_from_sample(sample_invoice_no)
        if not fmt:
            raise ValueError("Couldn't derive a pattern from that sample.")
    else:
        raise ValueError("Provide either a sample invoice number or a regex.")

    inv_formats = cfg.setdefault("invoice_formats", {}).setdefault(
        supplier, {"sample_count": 0, "formats": []}
    )
    inv_formats["formats"].append(fmt)
    inv_formats["sample_count"] = inv_formats.get("sample_count", 0) + 1
    _rebalance_shares(inv_formats)

    if owns_cfg:
        save_config(cfg)
    return {"supplier": supplier, "format": fmt}


def set_pattern_active(
    supplier: str,
    index: int,
    active: bool,
    cfg: Optional[dict] = None,
) -> dict:
    """Deactivate (active=False) or reactivate (active=True) one pattern by
    its position in that supplier's formats[] list -- get `index` from
    list_invoice_patterns(). This NEVER deletes the entry: count/share/history
    stay intact, the pattern just stops being used for matching/repair while
    inactive, per your 'deactivate not delete' preference."""
    owns_cfg = cfg is None
    cfg = cfg or load_config()

    formats = cfg.get("invoice_formats", {}).get(supplier, {}).get("formats", [])
    if index < 0 or index >= len(formats):
        raise ValueError(f"No pattern at index {index} for '{supplier}'.")

    formats[index]["active"] = bool(active)

    if owns_cfg:
        save_config(cfg)
    return {"supplier": supplier, "index": index, "active": active, "pattern": formats[index]}


# --------------------------------------------------------------------------- #
# 3d. Dashboard: how many PDFs are sitting in the watched folder right now
# --------------------------------------------------------------------------- #

def count_pending_pdfs(folder: str) -> int:
    """Used by the Dashboard tab. Cheap enough to call on a timer (e.g. every
    3-5s via `after()`) without touching disk more than a directory listing."""
    try:
        p = Path(folder)
        if not p.is_dir():
            return 0
        return sum(1 for f in p.iterdir() if f.is_file() and f.suffix.lower() == ".pdf")
    except OSError:
        return 0
