# Maafushivaru Document Processing Hub v5.3

## Unified GRN Dispatch workflow

The visible **AI Extract** workspace is now named **GRN Dispatch**. It is the single place to scan, process, review, finalise and export documents.

### Choose the OCR engine

Use the **Engine** selector in the GRN Dispatch toolbar:

| Selection | What happens |
|---|---|
| **Online — OCR.space API** | Keeps the existing OCR.space upload/compression workflow and its field-extraction logic. |
| **Offline — Tesseract (Local)** | Reads the original PDF locally at its native resolution with a forced Tesseract engine override. No 1 MB target, recompression, temporary API PDF or upload is used; the same shared field-extraction/result workflow is used. |

The current mode is always visible at the top-right of the application:

- `ONLINE — OCR.space`
- `OFFLINE — Tesseract`

## Processing flow

1. **Scan** a document or place PDFs in `SCANNED/`.
2. Select **Process New PDFs**. Documents are processed **one at a time in chronological scan serial order** (`SCAN_0001`, `SCAN_0002`, …).
3. Review or edit the shared result rows.
4. Select **Process All** to finalise: rename PDFs, move them to `PROCESSED/`, populate the internal compatibility records and make the Excel register ready for export.
5. Select **Export Excel** when required.

## Auto-ingest

The Settings tab now contains one **Enable Auto-Ingest** switch. It uses the same selected engine as GRN Dispatch:

- selecting **Offline** auto-processes original PDFs directly with local Tesseract, without API-size compression;
- selecting **Online** auto-processes with OCR.space;
- rapidly arriving scans remain queued and process serially, without sending the same PDF to both engines.

Original PDFs remain in `SCANNED/` until **Process All** is selected.

## Debugging

**Debug Engines** can test any available PDF with:

- Local Tesseract;
- OCR.space Engine 1 (Default);
- OCR.space Engine 2 (Enhanced);
- OCR.space Engine 3 (Extra Accurate).

It displays the raw OCR text and the shared parsed fields side by side.

## Compatibility

The old **OCR Renamer** and previous **GRN Dispatch** tabs are hidden from the UI but are still created internally. Their data structures and supporting logic remain available, so existing rename/dispatch/export integrations continue to work.

## Verification included

`test_unified_grn_dispatch.py` validates:

- only one visible GRN Dispatch workspace and both legacy tabs hidden;
- mode badge and selector updates;
- four image-only scanned PDFs processed locally through Tesseract in strict serial order, then finalised with **Process All**;
- the online path through a deterministic OCR.space-compatible test extractor, with the same shared result schema and serial order.

Run on a Linux development machine with:

```bash
xvfb-run -a python3 -m unittest -v test_unified_grn_dispatch.py
```

On Windows, run the app normally with `python maafushivaru_hub.py` after installing the dependencies listed in `README.md`.
