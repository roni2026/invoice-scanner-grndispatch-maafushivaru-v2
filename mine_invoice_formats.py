"""
Mines per-supplier invoice-number FORMAT signatures from the historical
GRN DISPATCH NOTE workbooks, canonicalized against the existing
config.json suppliers/aliases so the output slots directly into the app's
existing supplier-matching pipeline.

Each supplier gets a ranked list of format entries. Each entry carries:
  - template        e.g. "INV-{0}"      (canonical literal parts; the
                     {N} slots are where the OCR'd digits get inserted)
  - digit_lengths    e.g. [7]            (expected length of each digit run,
                     used only to build a tolerant "shape" matcher)
  - regex            strict matcher: exact literal text + flexible digits
  - shape_regex      tolerant matcher: same digit flexibility, but the
                     literal letters are matched by length/class only, so
                     OCR letter-misreads ("INV"->"1NV") still match. When a
                     shape match fires, the app rebuilds the value from the
                     canonical `template` + the OCR'd digits, effectively
                     forcing the (never-changing) prefix.
  - count / share    frequency stats

Output: invoice_formats.json (merge into config.json under "invoice_formats").
"""
import json, re, glob, sys
from collections import Counter, defaultdict

sys.path.insert(0, "/home/claude/repo")
import openpyxl
from rapidfuzz import fuzz, process

CONFIG_PATH = "/home/claude/repo/config.json"
XLSX_GLOB = "/mnt/user-data/uploads/*.xlsx"

cfg = json.load(open(CONFIG_PATH))
CANDIDATES = list(cfg["suppliers"])
ALIASES = cfg["aliases"]


def norm(s):
    s = s.upper()
    s = re.sub(r"\bPRIVATE\s+LIMITADE\b", "PVT LTD", s)
    s = re.sub(r"\bPRIVATE\s+LIMITED\b", "PVT LTD", s)
    s = re.sub(r"\bPRIVATE\s+LTD\b", "PVT LTD", s)
    s = re.sub(r"\bPVT\.?\s*LTD\.?\b", "PVT LTD", s)
    s = re.sub(r"\bPTE\.?\s*LTD\.?\b", "PTE LTD", s)
    s = re.sub(r"\bLIMITED\b", "LTD", s)
    s = re.sub(r"[^A-Z0-9 &]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

exact_lookup, all_search_strings, search_to_canon = {}, [], {}
for c in CANDIDATES:
    n = norm(c)
    exact_lookup[n] = c
    all_search_strings.append(n)
    search_to_canon[n] = c
for canon, alist in ALIASES.items():
    for a in alist:
        n = norm(a)
        exact_lookup[n] = canon
        all_search_strings.append(n)
        search_to_canon[n] = canon

def canonicalize(raw_supplier):
    n = norm(raw_supplier)
    if not n:
        return None
    if n in exact_lookup:
        return exact_lookup[n]
    match = process.extractOne(n, all_search_strings, scorer=fuzz.token_sort_ratio)
    if match and match[1] >= 87:
        return search_to_canon[match[0]]
    return None

rows = []
for f in glob.glob(XLSX_GLOB):
    wb = openpyxl.load_workbook(f, data_only=True)
    ws = wb.active
    header_row = None
    for i, row in enumerate(ws.iter_rows(min_row=1, max_row=15, values_only=True), start=1):
        if row[0] == 'NO - #':
            header_row = i
            break
    for row in ws.iter_rows(min_row=header_row + 2, values_only=True):
        no, inv_date, supplier, po, invoice, usd, mvr, eur, gbp, sgd, grn = row
        if not supplier or not invoice:
            continue
        rows.append((str(supplier).strip(), str(invoice).strip()))

def clean_invoice(raw):
    t = raw.strip().upper()
    if re.search(r"\d{4}-\d{2}-\d{2} 00:00:00", t):
        return ""
    t = re.sub(r"\s+", " ", t).strip(" -:./\\#")
    return t

by_canon = defaultdict(list)
unmatched = Counter()
for raw_sup, raw_inv in rows:
    canon = canonicalize(raw_sup)
    inv = clean_invoice(raw_inv)
    if not inv:
        continue
    if canon:
        by_canon[canon].append(inv)
    else:
        unmatched[raw_sup] += 1

_SEP_CHARS = set("-/ .")

def tokenize(inv):
    """Return list of ('digits'|'sep'|'lit', text) tokens, in order.
    Separator punctuation is split out from letter runs so e.g. 'INV-' becomes
    two tokens ('lit','INV') + ('sep','-') instead of one opaque blob - that
    way the digit run after the dash still gets its own flexible matcher.
    """
    toks = []
    i, n = 0, len(inv)
    while i < n:
        c = inv[i]
        if c.isdigit():
            j = i
            while j < n and inv[j].isdigit():
                j += 1
            toks.append(("digits", inv[i:j]))
            i = j
        elif c in _SEP_CHARS:
            j = i
            while j < n and inv[j] in _SEP_CHARS:
                j += 1
            toks.append(("sep", inv[i:j]))
            i = j
        else:
            j = i
            while j < n and not inv[j].isdigit() and inv[j] not in _SEP_CHARS:
                j += 1
            toks.append(("lit", inv[i:j]))
            i = j
    return toks

def signature(toks):
    return "".join(
        f"D{{{len(v)}}}" if kind == "digits" else ("-" if kind == "sep" else v)
        for kind, v in toks
    )

def build_entry(example_toks, count, total):
    template_parts = []
    digit_lengths = []
    regex_parts = []
    shape_parts = []
    slot = 0
    for kind, v in example_toks:
        if kind == "digits":
            n = len(v)
            digit_lengths.append(n)
            template_parts.append("{" + str(slot) + "}")
            slot += 1
            lo, hi = max(1, n - 1), n + 1
            # Digit segments are their OWN capturing group so the app can
            # pull out exactly the right slot values later (a blind
            # re.findall(r"\d+", ...) over the whole match breaks as soon as
            # a stray digit shows up inside a garbled letter prefix, e.g.
            # OCR misreading 'INV' as '1NV').
            regex_parts.append(rf"(\d{{{lo},{hi}}})")
            shape_parts.append(rf"(\d{{{lo},{hi}}})")
        elif kind == "sep":
            template_parts.append(v)
            regex_parts.append(r"(?:[-/\s.]{0,2})")
            shape_parts.append(r"(?:[-/\s.]{0,2})")
        else:  # lit (letters/symbols, e.g. 'INV', 'S&O', 'LCM')
            template_parts.append(v)
            regex_parts.append(re.escape(v))
            lo, hi = max(1, len(v) - 1), len(v) + 1
            shape_parts.append(rf"(?:[A-Z&]{{{lo},{hi}}})")
    return {
        "template": "".join(template_parts),
        "digit_lengths": digit_lengths,
        "regex": "".join(regex_parts),
        "shape_regex": "".join(shape_parts),
        "count": count,
        "share": round(count / total, 3),
    }

formats = {}
for canon, invs in by_canon.items():
    total = len(invs)
    sig_counter = Counter()
    sig_example_toks = {}
    for v in invs:
        toks = tokenize(v)
        sig = signature(toks)
        sig_counter[sig] += 1
        if sig not in sig_example_toks:
            sig_example_toks[sig] = toks
    ranked = sig_counter.most_common()
    entries = []
    covered = 0
    for sig, cnt in ranked:
        if len(entries) >= 4:
            break
        if len(entries) >= 1 and covered / total >= 0.93:
            break
        entries.append(build_entry(sig_example_toks[sig], cnt, total))
        covered += cnt
    formats[canon] = {"sample_count": total, "formats": entries}

with open("/home/claude/work/invoice_formats.json", "w") as f:
    json.dump(formats, f, indent=2)

print(f"Rows: {len(rows)}  Canonical suppliers covered: {len(formats)}  Unmatched raw names: {len(unmatched)}")
for canon in ["LYCORN MALDIVES", "LILY INTERNATIONAL PVT LTD", "F&B EVENING STORE"]:
    if canon in formats:
        print(canon)
        for e in formats[canon]["formats"]:
            print("  ", e)
