# Maafushivaru Hub

A desktop app that takes a stack of scanned supplier invoices and Goods Received Notes and turns them into a clean, ready-to-file Excel register — built for the receiving/stores team at Outrigger Maafushivaru Resort.

## The problem this solves

Every day the receiving team deals with a pile of paper invoices and GRNs. Each one has to be read, matched to a supplier, checked against a PO, and typed into a master spreadsheet. Done by hand, that's slow and easy to get wrong — a misread number or a supplier name typo throws off the whole register.

This app automates that chain: **scan → read → identify supplier → pull out the numbers → rename the file → write it to Excel.**

## How it works

1. **Get the document in** — pull straight from a scanner (Windows WIA) or watch a `SCANNED/` folder, with PDFs auto-renamed to a clean sequence.
2. **Read it** — OCR runs via Tesseract locally by default, or through the OCR.space API for the AI Extract workflow. Only the pages that are actually receiving reports get kept, filtered by pattern (GRN/PO number formats), so noise pages get dropped automatically.
3. **Work out the supplier** — OCR text from a scanned invoice is never perfectly clean, so this goes through several layers: a maintained list of known suppliers plus aliases, fuzzy matching (RapidFuzz) to survive typos, and an optional AI matcher for the genuinely hard cases.
4. **Pull the fields** — GRN number, PO number, invoice number, currency, and amount, using configurable patterns per document format.
5. **Rename and file** — source PDFs get renamed consistently, and everything gets written into a formatted `GRN_OUTPUT_<timestamp>.xlsx`, split correctly across currency columns.

Anything the app isn't confident about gets flagged with a confidence score so a human can double check it, and every run writes a log to `LOGS/`.

## The app itself

Built as a single Tkinter application (`maafushivaru_hub.py`) with tabs for Dashboard, Scan, Renamer, AI Extract, Dispatch, Settings, and About. It also **learns as you use it** — when you manually correct a supplier name, it registers the OCR text it originally read as an alias, so it gets that supplier right automatically next time.

## Getting started

Requires Python 3 with `pytesseract`, `pymupdf`, `rapidfuzz`, and the packages listed for the OCR/Excel pipeline (see the imports at the top of `maafushivaru_hub.py`). Tesseract OCR itself needs to be installed separately and pointed to in `config.json`.

```bash
python maafushivaru_hub.py
```

On macOS, use `setup_and_run.py` instead — it checks dependencies, patches the Tesseract path in `config.json` for your system, and launches the app.

## Config

Everything supplier/pattern related lives in `config.json`: `suppliers`, `aliases`, `patterns`, and `invoice_formats`. This is what the self-learning feature updates as you correct entries in the app.

## v2 vs the original

This is the actively developed version of [`invoice-scanner-grndispatch-maafushivaru`](https://github.com/roni2026/invoice-scanner-grndispatch-maafushivaru) — same core idea, with a more mature supplier-matching pipeline, self-learning aliases, and a cleaner GUI.
