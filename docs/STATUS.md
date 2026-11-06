# Project status

Updated 2026-11-03. Business documents and the product catalog are **not** in this
repository. All numbers below come from 12 synthetic documents generated with
`tools/synth.py`, plus a simulated scan and photo of each single-page document.
They show that the system works on these layouts. They do not measure accuracy
on real supplier documents.

| Stage | State |
|---|---|
| 1. Baseline reading (PDF text or OCR, raw output kept separate from fields) | done |
| 2. Field extraction (header, product lines, totals, math checks) | done, tested on synthetic documents only |
| 3. Employee web interface (Hebrew, RTL, phone + computer) | done, not yet tested with real employees or real phone cameras |
| 4. Training examples: reviewer screen, approval, export, model registry with rollback | done, no real approved examples yet |
| 5. Fine-tuning + before/after evaluation | not started (Tesseract's `lstmtraining` runs on CPU; needs `tessdata_best` Hebrew and enough approved examples) |
| 6. Documented hand-off format for the future RAG / matching component | done: [DATA_FORMAT.md](DATA_FORMAT.md), JSON Schema, `/api/handoff`, JSONL/Excel/CSV export, `ProductMatcher` interface. RAG itself is not built (by design). |

## Synthetic evaluation (stock Tesseract 5.3.4, heb+eng)

| Measure | PDF text | simulated scan | simulated photo |
|---|---|---|---|
| quantity, price and total on the right product | 124/124 | 48/58 | 51/58 |
| supplier product code exact | 124/124 | 27/58 | 40/58 |
| description exact | 115/124 | 32/58 | 28/58 |
| average seconds per document (CPU) | <1 | ~20 | ~14 |

Main weakness: OCR of codes and mixed Hebrew/English product descriptions on
scans and photos. Engine confidence is not a reliable warning: many wrong values
were not flagged. This is the motivation for fine-tuning (stage 5).

## Image clean-up: before and after (2026-11-03)

`tools/compare_preprocessing.py` reads the same simulated phone photos with
no clean-up (`OCR_PREPROCESS=off`), the current clean-up (`standard`, the
default) and an extra contrast stretch (`contrast`). Full table:
[eval-preprocessing.md](eval-preprocessing.md). 9 synthetic single-page
documents, 58 product lines; simulated damage, not real photos.

| Photo problem | no clean-up | current clean-up |
|---|---|---|
| normal photo | 3/58 | 51/58 |
| dark | 0/58 | 47/58 |
| grey / low contrast | 0/58 | 0/58 before the fix below, 47/58 after |
| faded ink | 0/58 | 45/58 |
| sideways (90°) | 0/58 | 47/58 |
| upside down | 0/58 | 37/58 |

(Lines where quantity, price and total all landed on the right product.)

- The clean-up is necessary: without it almost nothing is read.
- Fixed: on grey, washed-out photos the paper edge was not found, so the page
  was not cropped. Paper detection now stretches contrast first (detection
  only; the crop comes from the unchanged photo).
- The extra contrast stretch did **not** help overall (better on grey photos,
  worse on faded ink, more wrong values shown as OK). It stays off by default.
- New `low_contrast` quality warning, shown to the employee.
- Speed: Tesseract now runs single-threaded per call (`OMP_THREAD_LIMIT=1`).
  On the 4-core test server one page took 147 s with the default threading
  and 1.5 s without it.

Re-run: `python tools/synth.py <catalog.xlsx> synth/ && python tools/evaluate.py synth/ eval/ --variants`
