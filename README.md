# Invoice OCR (final-year project)

Reads supplier invoices and delivery notes (Hebrew, English, mixed), from PDFs,
scans and phone photos, on your own machine. No document leaves the machine.

## Install (Ubuntu / Debian)

```bash
sudo apt-get install tesseract-ocr tesseract-ocr-heb tesseract-ocr-eng
pip install -r requirements.txt
```

## Run

```bash
python -m ocr_app path/to/invoice.pdf path/to/photo.jpg --data data
```

For each file this creates `data/documents/<doc_id>/`:

| Path | What it is |
|---|---|
| `original/` | Exact copy of the upload, read-only. Never changed. |
| `pages/page-001.png` | The page as an image (PDF rendered at 300 dpi, or the photo). |
| `processed/page-001.png` | Only if we cleaned the image (cropped, rotated, straightened, shadows removed). |
| `raw/page-001.json` / `.txt` | **Raw reading result**: every word with its position, which method read it, and the table cells. |
| `fields.json` | **Structured fields** taken from the raw result. Each field keeps the exact `raw` text and a separate cleaned `value`, plus `status` (`ok`, `missing`, `unclear`, `check_failed`) and the reasons. |
| `report.html` | A page that shows the three side by side. Click a field to see where it was read. |

`doc_id` comes from the file content, so re-uploading the same file is detected.

## How a page is read

1. **PDF with usable built-in text** → method `pdf_text`. Letters the PDF font
   cannot map (shown as `(cid:NNN)`) are repaired with OCR of that word only,
   and recorded under `repaired_letters`.
2. **Scan, photo, or PDF without usable text** → method `ocr` with Tesseract
   (`heb+eng`). Photos are first cropped to the paper, rotated, straightened
   and evened out. A blurry or dark image produces a warning asking for a new photo.
3. **Table**: we find the table grid and read each cell separately, so each
   quantity and price stays on its own product line. Number columns are read
   with a digits-only setting. Text cells are read twice (Hebrew-first and
   English-first) and the more confident reading is kept.
4. **Checks**: quantity × price × (1 − discount%) = line total, sum of lines =
   subtotal, subtotal + VAT = total. A failed check marks the fields for review.

Product matching to the catalog is **not** done here; it is a later, separate stage.

## Employee interface (web, Hebrew, computer and phone)

```bash
pip install fastapi uvicorn python-multipart jinja2 xlsxwriter
OCR_DATA=data REVIEWER_PIN=<choose a code> uvicorn ocr_app.web.app:app --host 0.0.0.0 --port 8000
```

Open `http://<server>:8000` from a computer or a phone on the same network.

1. Upload a PDF, pick a photo, or take a photo (phone camera opens directly).
2. A status screen shows progress.
3. The review screen shows the page next to the fields. Click a field to see it on the page.
   Orange = worth checking, red = the math does not add up, dashed = not found.
4. Fix values, add or delete lines, then "אישור המסמך" (confirm).
5. After confirming: export to Excel, CSV or JSON.

A blurry/dark photo gives a "please retake" message; a missing page ("עמוד 2 מתוך 3")
gives an "add the missing page" message. Reading a document never updates Bina.

Saved data per document: `review.json` (the employee's values next to the original reading,
with a change history). `fields.json` (the machine reading) is never edited.

## Training data (Stage 4)

`/training` (reviewer code required). When a document is confirmed, every text line of
its product table becomes a *suggested* example: a cropped line image plus a suggested text.
The reviewer types exactly what is printed and approves it. Statuses are kept apart:

| Counter | Meaning |
|---|---|
| corrections saved | reviewer typed text that differs from what the model read |
| approved for training | examples allowed into training, split train/val/test **by document** |
| trainings completed | entries in `data/models/registry.json` |
| active model | changed only by an explicit "activate" after evaluation; "rollback" returns to the previous model |

Export: button on `/training`, or `training.export_tesstrain()`. It writes `<split>/<id>.png` + `<id>.gt.txt`
(the tesstrain line format) and `manifest.json`.

## Test

```bash
python -m pytest -q tests
python tools/make_test_images.py data/documents/<id>/pages/page-001.png sim/   # simulated scan/photos
python tools/compare_fields.py reference/fields.json candidate/fields.json
python tools/synth.py catalog.xlsx synth/ --count 12          # synthetic documents with answer keys
python tools/evaluate.py synth/ eval/ --variants              # accuracy: PDF, simulated scan, simulated photo
```

## Settings

- `OCR_LANG` (default `heb+eng`)
- `OCR_TESSDATA` folder with a fine-tuned model (empty = stock model). The model
  name and version are written into every result.
