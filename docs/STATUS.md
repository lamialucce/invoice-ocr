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
| 6. Documented JSON for the future RAG component | partly done (review.json export with stable ids); schema doc still to write |

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

Re-run: `python tools/synth.py <catalog.xlsx> synth/ && python tools/evaluate.py synth/ eval/ --variants`
