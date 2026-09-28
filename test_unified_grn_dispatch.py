"""Regression tests for the unified GRN Dispatch screen.

Run under xvfb on Linux because the desktop application uses Tkinter:
    xvfb-run -a python3 -m unittest -v test_unified_grn_dispatch.py

The online path is tested with a deterministic OCR.space stand-in; no network
request or API credits are used.  The offline path uses the installed local
Tesseract binary against generated scanned-PDF images.
"""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
from PIL import Image, ImageDraw, ImageFont

import aiextracttab as aix
from maafushivaru_hub import MaafushivaruHub


SAMPLE_TEXT = """RECEIVING REPORT
RECEIVING RECORD #: RC-MAM-000000123
PURCHASE ORDER NO: MAM-000000456
DATE: 01/09/2026
SUPPLIER: HAPPY MARKET
INVOICE NO: INV-2026-001
INVOICE TOTAL: USD 123.45
"""


class _FakeOCRSpaceExtractor:
    """Network-free stand-in that preserves the extractor's public contract."""

    def __init__(self, config):
        self.config = config

    def extract_from_file(self, _path):
        return SAMPLE_TEXT, ""


def _make_scanned_pdf(path: Path, text: str = SAMPLE_TEXT) -> None:
    """Create an image-only, portrait PDF that requires the local OCR route."""
    image = Image.new("RGB", (1650, 2330), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 40)
    except OSError:
        font = ImageFont.load_default()
    y = 150
    for line in text.splitlines():
        draw.text((110, y), line, fill="black", font=font)
        y += 150
    png_path = path.with_suffix(".png")
    image.save(png_path)
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, filename=str(png_path))
    doc.save(path)
    doc.close()
    png_path.unlink()


class UnifiedGRNDispatchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="grn_dispatch_test_")
        base = Path(self._tmp.name)
        self.scanned = base / "SCANNED"
        self.processed = base / "PROCESSED"
        self.failed = base / "FAILED"
        self.logs = base / "LOGS"
        self.temp_api = base / "TEMP API PDFS"
        for folder in (self.scanned, self.processed, self.failed, self.logs, self.temp_api):
            folder.mkdir(parents=True, exist_ok=True)

        self.app = MaafushivaruHub()
        self.app._auto_ingest_enabled_var.set(False)
        self.app._watcher_api_var.set(False)
        self.app._watcher_offline_var.set(False)
        self.app._stop_watcher()
        self.app.dirs = {
            "base": str(base),
            "scanned": str(self.scanned),
            "processed": str(self.processed),
            "archive": str(base / "ARCHIVE"),
            "failed": str(self.failed),
            "logs": str(self.logs),
        }
        self.app._aix_temp_folder = str(self.temp_api)
        self.app.cfg["app_settings"].update({
            "ocr_mode": "full",
            "extraction_source": "image_only",
            "image_scale_factor": 2,
            "page_scan_region_enabled": False,
            "dry_run": False,
        })

    def tearDown(self):
        try:
            # Flush any zero-delay callbacks queued by the OCR worker before
            # the test destroys the Tk interpreter.
            for _ in range(3):
                self.app.update()
            self.app._cleanup_on_exit()
            self.app.destroy()
        finally:
            self._tmp.cleanup()

    def _wait_for_results(self, expected_count: int, timeout: float = 45.0):
        deadline = time.time() + timeout
        completed = {"value": False}
        settling = {"value": False}

        def _check():
            ready = (
                not self.app._aix_running
                and self.app._aix_queue.empty()
                and len(self.app._aix_results) >= expected_count
            )
            if ready and not settling["value"]:
                # Let all zero-delay visual updates complete before a test
                # destroys the root window, mirroring normal user operation.
                settling["value"] = True

                def _finish():
                    completed["value"] = True
                    self.app.quit()

                self.app.after(100, _finish)
                return
            if time.time() >= deadline:
                self.app.quit()
                return
            self.app.after(30, _check)

        self.app.after(30, _check)
        self.app.mainloop()
        if not completed["value"]:
            self.fail(
                f"Timed out waiting for {expected_count} result(s); "
                f"got {len(self.app._aix_results)}. Status: {self.app._status_var.get()}"
            )

    def _clear_result_store(self):
        self.app._aix_results = []
        self.app._aix_row_map.clear()
        self.app._aix_all_rows.clear()
        for item in self.app._aix_tree.get_children():
            self.app._aix_tree.delete(item)

    def test_visible_workspace_hides_legacy_tabs_and_shows_mode(self):
        self.app.update_idletasks()
        visible = [
            self.app.notebook.tab(tab, "text").strip()
            for tab in self.app.notebook.tabs()
            if self.app.notebook.tab(tab, "state") != "hidden"
        ]
        self.assertEqual(visible.count("GRN Dispatch"), 1)
        self.assertNotIn("OCR Renamer", visible)
        self.assertEqual(self.app.notebook.tab(self.app._legacy_renamer_tab, "state"), "hidden")
        self.assertEqual(self.app.notebook.tab(self.app._legacy_dispatch_tab, "state"), "hidden")
        self.assertIn("ONLINE", self.app._processing_mode_badge_var.get())
        self.assertEqual(self.app._aix_btn_send.cget("text"), "✅  Process All")
        self.assertNotIn("Extract & Process", str(self.app._status_var.get()))

    def test_offline_tesseract_pipeline_is_serial_and_finalises_all_documents(self):
        for number in range(1, 5):
            _make_scanned_pdf(self.scanned / f"SCAN_{number:04d}.pdf")

        self.app._grn_engine_var.set("offline")
        aix._aix_refresh_mode_ui(self.app)
        self.assertIn("OFFLINE", self.app._processing_mode_badge_var.get())
        self.assertIn("Offline", self.app._aix_btn_process.cget("text"))

        aix._aix_start_process(self.app)
        self._wait_for_results(4)

        expected_order = [f"SCAN_{number:04d}.pdf" for number in range(1, 5)]
        self.assertEqual([row["file"] for row in self.app._aix_results], expected_order)
        self.assertTrue(all(row["processing_engine"] == "offline" for row in self.app._aix_results))
        self.assertTrue(all(row["raw_ocr_text"].strip() for row in self.app._aix_results))
        self.assertTrue(all(row["is_valid"] for row in self.app._aix_results))
        self.assertTrue(all(row["temp_path"] == "" for row in self.app._aix_results))
        self.assertEqual(len(list(self.temp_api.glob("*.pdf"))), 0)

        with patch.object(aix.messagebox, "askyesno", return_value=True), patch.object(aix.messagebox, "showinfo"):
            aix._aix_send_to_tabs(self.app)

        self.assertTrue(all(row.get("sent") for row in self.app._aix_results))
        self.assertEqual(len(list(self.processed.glob("*.pdf"))), 4)
        self.assertEqual(len(list(self.scanned.glob("*.pdf"))), 0)
        self.assertEqual(len(self.app._dispatch_results), 4)

    def test_online_pipeline_uses_same_result_schema_and_serial_order(self):
        for number in range(1, 3):
            _make_scanned_pdf(self.scanned / f"SCAN_{number:04d}.pdf")

        self.app._grn_engine_var.set("online")
        aix._aix_refresh_mode_ui(self.app)
        self.assertIn("ONLINE", self.app._processing_mode_badge_var.get())
        self.assertIn("Online", self.app._aix_btn_process.cget("text"))

        with patch.object(aix, "OCRSpaceExtractor", _FakeOCRSpaceExtractor):
            aix._aix_start_process(self.app)
            self._wait_for_results(2)

        expected_order = ["SCAN_0001.pdf", "SCAN_0002.pdf"]
        self.assertEqual([row["file"] for row in self.app._aix_results], expected_order)
        self.assertTrue(all(row["processing_engine"] == "online" for row in self.app._aix_results))
        expected_keys = {
            "file", "date", "supplier", "po", "invoice", "usd", "mvr", "eur",
            "gbp", "sgd", "grn", "confidence", "is_valid", "errors", "raw_path",
            "raw_ocr_text", "parsed_fields", "processing_engine",
        }
        self.assertTrue(expected_keys.issubset(self.app._aix_results[0]))
        self.assertTrue(all(row["is_valid"] for row in self.app._aix_results))


if __name__ == "__main__":
    unittest.main(verbosity=2)
