
---

## 🆕 v2 changes (this repository)

This repo is a fork of [`invoice-scanner-grndispatch-maafushivaru`](https://github.com/roni2026/invoice-scanner-grndispatch-maafushivaru) with two targeted improvements:

### 1. Fixed: "Browse" for the Processed PDF Transfer path had no selectable option
`_browse_transfer_path()` (Dashboard tab) previously passed a possibly
relative / nonexistent folder straight into `filedialog.askdirectory()` as
`initialdir`. When that path didn't exist (e.g. a fresh install, or the app
launched from a different working directory), Windows' native folder picker
opened in a broken state with nothing selectable. The picker now resolves
every candidate path to an absolute path, verifies it actually exists, and
falls back through `Processed Pdf Transfer path → PROCESSED folder → base
folder → home folder → cwd` so the dialog always opens somewhere usable.

### 2. New: Excel export matches the official "GRN DISPATCH NOTE" workbook exactly
The AI Extract tab's **📊 Export Excel** button used to write a plain,
single-sheet list. It now reproduces the resort's official multi-page
dispatch-note layout cell-for-cell, built from a fresh `grn_dispatch_excel.py`
module:

- **One sheet per month** found in the extracted rows (e.g. `APRIL`, `MAY`) —
  the export groups rows by invoice month automatically, so a mixed batch of
  invoices spanning more than one month is split into the correct sheets
  intelligently, just like the reference workbook.
- **30 line-items per printed dispatch page**, with a running **DISPATCH #**
  that persists across exports (editable on the Dashboard tab under
  **GRN DISPATCH NEXT NO**, auto-advances after every export).
- Exact title block, DATE/DISPATCH box, two-row column headers
  (`GRN AMOUNT` spanning `USD/MVR/EUR/GBP/SGD`), dotted data-row borders,
  `MAM-0000` / `RC-MAM-0000` placeholder rows when a page has fewer than 30
  real invoices, and the `HANDED OVER BY` / `RECEIVED BY` signature block —
  all matched against the supplied reference file
  `GRN DISPATCH NOTE APRIL - 2026.xlsx`.
- The **Outrigger logo** is placed top-right of every printed page
  (`assets/outrigger_logo.png`), landscape print layout, and a manual page
  break after every 44-row page so it prints one dispatch note per page.

No other application behaviour was changed.
