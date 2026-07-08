
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

### 2. New: Excel export is a byte-for-byte clone of the official "GRN DISPATCH NOTE" workbook
The AI Extract tab's **📊 Export Excel** button used to write a plain,
single-sheet list. It's been completely rebuilt as `grn_dispatch_excel.py`,
which works by **cloning** a real page of the reference workbook
(`assets/grn_dispatch_template.xlsx`, extracted verbatim from
`GRN DISPATCH NOTE APRIL - 2026.xlsx`) rather than hand-reconstructing the
formatting — every font, fill, border (including the dotted data-row
borders), merge, row height, column width, number format, the exact 70%
print scale, landscape margins, and the Outrigger logo's precise anchor
position/size are copied cell-for-cell from the real file, then only the
handful of cells that actually change per page are overwritten:

- **30 line-items per page**, exactly matching the reference's fixed
  44-row block (title + DATE/DISPATCH box + 2-row header + 30 data rows +
  3 blank rows + signature block).
- A running **DISPATCH #** that persists across exports (editable on the
  Dashboard tab as **GRN DISPATCH NEXT NO**, auto-advances after every
  export).
- `MAM-0000` / `RC-MAM-0000` placeholder rows when a page has fewer than
  30 real invoices, matching the reference exactly.
- All rows for one export go into a **single sheet** named after the
  current month (e.g. `APRIL`) — this matches the reference file, whose
  pages freely mix invoice dates from different months (the sheet/title
  reflects the *dispatch* month, not each line's own invoice date), so the
  export does **not** try to split rows into separate sheets by invoice
  date.
- The **Outrigger logo** is placed at the exact same top-right anchor on
  every page, using the exact PNG bytes extracted from the reference file.

Verified with an automated cell-by-cell diff (font, fill, every border
side, alignment, number format, merges, row heights, column widths, image
anchor/size, and print setup) against the reference workbook — **zero
differences** on values or styles.

No other application behaviour was changed.
