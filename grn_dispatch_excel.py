"""GRN Dispatch Note Excel writer.

Reproduces the resort's official "GRN DISPATCH NOTE" workbook EXACTLY by
cloning a real page of that workbook cell-for-cell (fonts, fills, every
border, merges, row heights, column widths, number formats, the Outrigger
logo's exact anchor, page setup) from `assets/grn_dispatch_template.xlsx`,
then only overwriting the handful of cells that actually change per page:
the title's month/year, the DISPATCH #, and the 30 line-item rows.

This is a template-cloning implementation on purpose (rather than a
hand-built style reconstruction): copying the reference file's own cell
styles guarantees pixel-for-pixel fidelity instead of relying on a manual
re-creation that can drift from the original (borders/fills on blank
cells, exact fonts per column, etc. are all preserved automatically this
way). The template was produced by extracting rows 1-44 of the resort's
reference workbook "GRN DISPATCH NOTE APRIL - 2026.xlsx" verbatim.

Public entry point: write_grn_dispatch_workbook(...)
"""
from __future__ import annotations

import copy
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from openpyxl import Workbook, load_workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
from openpyxl.drawing.xdr import XDRPositiveSize2D
from openpyxl.utils import get_column_letter
from openpyxl.utils.units import pixels_to_EMU
from openpyxl.worksheet.pagebreak import Break

# ---------------------------------------------------------------------------
# Layout constants -- measured directly from the reference workbook.
# ---------------------------------------------------------------------------
ROWS_PER_PAGE = 30          # line items per dispatch page
BLOCK_HEIGHT = 44           # total rows (incl. blanks + signature) per page
TEMPLATE_COLS = 11          # A..K

# Row offsets (1-indexed, relative to a page's own block start = row 1)
TITLE_ROW = 4
DATE_ROW = 6
DISPATCH_NO_COL = 8         # H
HEADER_ROW1 = 8
DATA_START_ROW = 10
DATA_END_ROW = 39           # inclusive -- 30 rows (10..39)

CURRENCIES = ["usd", "mvr", "eur", "gbp", "sgd"]
CURRENCY_COLS = [6, 7, 8, 9, 10]   # F, G, H, I, J (1-indexed)

PLACEHOLDER_PO = "MAM-0000"
PLACEHOLDER_GRN = "RC-MAM-0000"

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_TEMPLATE_PATH = os.path.join(_MODULE_DIR, "assets", "grn_dispatch_template.xlsx")


def _load_template(template_path: str):
    """Loads the reference-page template workbook and returns its single
    worksheet plus the embedded logo image (bytes + native size + anchor
    offsets) so both can be cloned per page."""
    wb = load_workbook(template_path, data_only=False)
    ws = wb.worksheets[0]
    if not ws._images:
        raise RuntimeError(f"Template '{template_path}' has no embedded logo image.")
    img = ws._images[0]
    frm = img.anchor._from
    logo_info = {
        "col": frm.col, "row": frm.row, "colOff": frm.colOff, "rowOff": frm.rowOff,
        "width": img.width, "height": img.height,
        "path": img.ref if isinstance(img.ref, str) else None,
        "image_bytes": img._data() if hasattr(img, "_data") else None,
    }
    return wb, ws, logo_info


def _clone_block(template_ws, target_ws, row_offset: int):
    """Copies the full 44-row x 11-col template block (styles, values,
    merges, row heights) onto `target_ws`, shifted down by `row_offset`
    rows. Column widths are the caller's responsibility (set once)."""
    for r in range(1, BLOCK_HEIGHT + 1):
        for c in range(1, TEMPLATE_COLS + 1):
            src = template_ws.cell(row=r, column=c)
            dst = target_ws.cell(row=r + row_offset, column=c, value=src.value)
            dst.font = copy.copy(src.font)
            dst.fill = copy.copy(src.fill)
            dst.border = copy.copy(src.border)
            dst.alignment = copy.copy(src.alignment)
            dst.number_format = src.number_format
            dst.protection = copy.copy(src.protection)

        h = template_ws.row_dimensions[r].height
        if h is not None:
            target_ws.row_dimensions[r + row_offset].height = h

    for m in template_ws.merged_cells.ranges:
        target_ws.merge_cells(
            start_row=m.min_row + row_offset, start_column=m.min_col,
            end_row=m.max_row + row_offset, end_column=m.max_col,
        )


def _add_logo(target_ws, logo_info: dict, logo_path: str, row_offset: int):
    """Re-adds the Outrigger logo at the exact same relative anchor
    position/size the template uses, shifted to this page's block."""
    img = XLImage(logo_path)
    img.width, img.height = logo_info["width"], logo_info["height"]
    marker = AnchorMarker(
        col=logo_info["col"], colOff=logo_info["colOff"],
        row=logo_info["row"] + row_offset, rowOff=logo_info["rowOff"],
    )
    size = XDRPositiveSize2D(cx=pixels_to_EMU(img.width), cy=pixels_to_EMU(img.height))
    img.anchor = OneCellAnchor(_from=marker, ext=size)
    target_ws.add_image(img)


def _write_page(template_ws, target_ws, page_index: int, month_label: str, year: int,
                 dispatch_no: int, batch: List[Dict], logo_info: dict, logo_path: Optional[str]):
    row_offset = page_index * BLOCK_HEIGHT
    _clone_block(template_ws, target_ws, row_offset)

    # -- Dynamic overwrites (everything else is already correct from the clone) --
    title_cell = target_ws.cell(row=TITLE_ROW + row_offset, column=1)
    title_cell.value = f"GRN DISPATCH {month_label}- {year}"

    dispatch_cell = target_ws.cell(row=DATE_ROW + row_offset, column=DISPATCH_NO_COL)
    dispatch_cell.value = dispatch_no

    for i in range(ROWS_PER_PAGE):
        row = DATA_START_ROW + row_offset + i
        rec = batch[i] if i < len(batch) else None

        target_ws.cell(row=row, column=2).value = rec.get("date", "") if rec else ""

        supplier = (rec.get("supplier", "") or "UNKNOWN SUPPLIER") if rec else ""
        target_ws.cell(row=row, column=3).value = supplier

        po = (rec.get("po", "") or PLACEHOLDER_PO) if rec else PLACEHOLDER_PO
        target_ws.cell(row=row, column=4).value = po

        invoice = rec.get("invoice", "") if rec else ""
        if isinstance(invoice, str) and invoice.upper() == "NO-INVOICE":
            invoice = ""
        target_ws.cell(row=row, column=5).value = invoice

        for col_idx, cur in zip(CURRENCY_COLS, CURRENCIES):
            cell = target_ws.cell(row=row, column=col_idx)
            val = rec.get(cur, "") if rec else ""
            if val not in ("", None):
                try:
                    cell.value = float(str(val).replace(",", ""))
                except (ValueError, TypeError):
                    cell.value = val
            else:
                cell.value = ""

        grn = (rec.get("grn", "") or PLACEHOLDER_GRN) if rec else PLACEHOLDER_GRN
        target_ws.cell(row=row, column=11).value = grn

    if logo_path:
        _add_logo(target_ws, logo_info, logo_path, row_offset)


def _chunk(seq: List[Dict], size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _write_sheet(wb: Workbook, sheet_name: str, template_ws, logo_info: dict,
                  logo_path: Optional[str], month_label: str, year: int,
                  rows: List[Dict], start_dispatch_no: int) -> int:
    ws = wb.create_sheet(title=sheet_name[:31])

    for c in range(1, TEMPLATE_COLS + 1):
        letter = get_column_letter(c)
        src_dim = template_ws.column_dimensions.get(letter)
        if src_dim and src_dim.width:
            ws.column_dimensions[letter].width = src_dim.width

    ws.sheet_view.showGridLines = template_ws.sheet_view.showGridLines
    ws.page_setup.orientation = template_ws.page_setup.orientation
    ws.page_setup.fitToWidth = template_ws.page_setup.fitToWidth
    ws.page_setup.fitToHeight = template_ws.page_setup.fitToHeight
    ws.page_setup.scale = template_ws.page_setup.scale
    if template_ws.sheet_properties.pageSetUpPr is not None:
        ws.sheet_properties.pageSetUpPr.fitToPage = template_ws.sheet_properties.pageSetUpPr.fitToPage
    ws.page_margins = copy.copy(template_ws.page_margins)

    batches = list(_chunk(rows, ROWS_PER_PAGE)) or [[]]
    dispatch_no = start_dispatch_no
    for page_idx, batch in enumerate(batches):
        _write_page(template_ws, ws, page_idx, month_label, year, dispatch_no, batch, logo_info, logo_path)
        dispatch_no += 1
        if page_idx < len(batches) - 1:
            ws.row_breaks.append(Break(id=(page_idx + 1) * BLOCK_HEIGHT))

    last_row = len(batches) * BLOCK_HEIGHT
    ws.print_area = f"A1:K{last_row}"
    return dispatch_no


def write_grn_dispatch_workbook(rows: List[Dict], start_dispatch_no: int,
                                 out_path: str, logo_path: Optional[str] = None,
                                 month_label: Optional[str] = None,
                                 year: Optional[int] = None,
                                 template_path: Optional[str] = None) -> Tuple[str, int]:
    """Builds the GRN Dispatch Note workbook, cloned cell-for-cell from the
    reference "GRN DISPATCH NOTE" template: 30 line-items per printed page,
    a running DISPATCH number, the Outrigger logo top-right of every page,
    and the exact signature block -- ready to print in landscape.

    All `rows` go into ONE sheet (named after `month_label`/`year`, default
    today's month/year) chunked into 30-row pages, in the order given --
    matching the reference file, whose pages mix invoice dates from
    different months freely (the sheet/title reflects the DISPATCH month,
    not each line's own invoice date).

    rows: list of dicts with keys date, supplier, po, invoice, usd, mvr,
          eur, gbp, sgd, grn (same shape the AI Extract tab already uses).
    start_dispatch_no: first DISPATCH number to use; increments by 1 per
          page written.
    logo_path: path to the Outrigger logo PNG to embed (defaults to the
          bundled assets/outrigger_logo.png next to this module).
    template_path: override for the reference template workbook (defaults
          to assets/grn_dispatch_template.xlsx next to this module).
    Returns (saved_path, next_dispatch_no) so the caller can persist the
    next starting number for the following export.
    """
    now = datetime.now()
    year = year or now.year
    month_label = (month_label or now.strftime("%B")).upper()
    template_path = template_path or _DEFAULT_TEMPLATE_PATH
    logo_path = logo_path or os.path.join(_MODULE_DIR, "assets", "outrigger_logo.png")

    template_wb, template_ws, logo_info = _load_template(template_path)

    wb = Workbook()
    wb.remove(wb.active)  # drop the default blank sheet

    sheet_name = month_label
    next_dispatch_no = _write_sheet(wb, sheet_name, template_ws, logo_info, logo_path,
                                     month_label, year, rows, start_dispatch_no)

    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    base, ext = os.path.splitext(out_path)
    final_path = out_path
    cnt = 1
    while os.path.exists(final_path):
        final_path = f"{base}_{cnt}{ext}"
        cnt += 1
    wb.save(final_path)
    return final_path, next_dispatch_no
