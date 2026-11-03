# Reviewed-document format (v1.0)

This is the format that later components read: product matching, a future RAG
component, or an import into Bina. It is produced by `ocr_app/handoff.py`, and
the JSON Schema is in [`schema/reviewed-document.v1.schema.json`](schema/reviewed-document.v1.schema.json).

Later components must read **only** this format. The internal files
`fields.json` and `review.json` can change between versions; this format
changes only with a new `schema_version`.

This stage does **not** build embeddings, a vector database, RAG or product
matching. It only fixes the data those parts will receive.

## How to get it

| Way | Use |
|---|---|
| `GET /api/handoff?status=confirmed&since=2026-11-01` | All documents with that review status (`confirmed`, `draft`, `not_reviewed`, `any`). `since` is an ISO date or time; it compares to `confirmed_at`, else `updated_at`, else `uploaded_at`. Returns `{schema, schema_version, count, documents: [...]}`. |
| `GET /api/handoff/<doc_id>` | One document. |
| `GET /doc/<doc_id>/export.json` | The same, as a download. |
| `python -m ocr_app.handoff --data data jsonl out.jsonl [--status confirmed] [--since ...]` | One document per line (JSON Lines), for batch jobs. |
| `ocr_app.handoff.iter_documents(data_dir, status, since)` | From Python. |
| `GET /export/confirmed.xlsx` / `.csv` / `.jsonl` | Flat files for employees (see "Flat exports" below). |

The web app has no login apart from the reviewer code. Run it only on the internal
network.

Default: only `confirmed` documents. A document an employee has not confirmed is
a machine reading and should not be treated as fact.

## Top level

| Key | Meaning |
|---|---|
| `schema`, `schema_version` | Always `"invoice-ocr/reviewed-document"`, `"1.0"`. Check them before reading. |
| `doc_id` | Stable id of the document (folder name). Never reused. |
| `reading_id` | Id of the machine reading the review is based on. It changes only if the document is read again (for example, a missing page was added). It does not change when an employee edits values. |
| `review` | `status` (`not_reviewed`, `draft`, `confirmed`), `confirmed_by`, `confirmed_at`, `updated_at`, `changes_count` (number of edits in the history). |
| `goods_receipt_confirmed` | Always `false`. A confirmed document means "the paper says this", not "the goods arrived". Received quantities are confirmed in Bina by a person. |
| `model` | Which OCR engine and model read the document: `engine`, `engine_version`, `model_id` (`stock` or a fine-tuned id), `lang`, `tessdata`. |
| `source_files` | The original uploads: `file` (path inside the document folder), `sha256`, `original_filename`. |
| `uploaded_at` | ISO time (UTC). |
| `coordinate_space` | What `bbox` numbers refer to (see "Positions"). |
| `pages` | One entry per page: `page` (1, 2, ...), `source_file`, `source_page`, `read_method` (`pdf_text` or `ocr`), `image` (the page image that positions refer to), `preprocessing` (what was done to a photo: cropped, rotated, ...). |
| `header` | The document fields, each in the field format below. |
| `lines` | Product lines that were not deleted, in order. |
| `deleted_line_ids` | Lines the reader found but an employee deleted (for example, a row that was not a product). |
| `warnings` | Reading warnings (blurry photo, missing page, ...). |
| `checks` | `at_reading`: math checks on the machine reading. `after_review`: the same checks on the reviewed values. An employee can confirm even if a check fails; the failure stays visible here. |

## One field

Every header field and every line field has the same shape:

```json
{
  "value": 3.0,
  "as_read": "2",
  "read_value": 2.0,
  "read_status": "ok",
  "changed_by_reviewer": true,
  "location": {"page": 1, "bbox": [812, 1040, 870, 1078]}
}
```

| Key | Meaning |
|---|---|
| `value` | The value to use: what the employee confirmed. `null` = not on the document. Numbers are numbers; dates are `YYYY-MM-DD`. |
| `as_read` | The exact text the machine read, before any cleaning. Use it to see what was printed or to measure OCR. |
| `read_value` | The cleaned value from the machine reading, before review. |
| `read_status` | Machine status: `ok`, `missing`, `unclear`, `check_failed`. |
| `changed_by_reviewer` | `true` if `value` differs from `read_value`. |
| `location` | Where on the page it was read, or `null` (added by hand, or not found). |

Nothing fills a value from the catalog. If the supplier wrote a product name the
catalog does not know, `value` is still the supplier's text.

Header fields: `supplier_name`, `supplier_tax_id`, `supplier_number`,
`document_type` (`invoice`, `invoice_receipt`, `delivery_note`, `credit_note`, `purchase_order`),
`document_number`, `document_date`, `currency` (`ILS`, `USD`, `EUR`, or `null`),
`subtotal`, `vat`, `total`.

## One line

| Key | Meaning |
|---|---|
| `line_id` | Stable id: `<doc_id>-p<page>-r<row>` for lines read from the page, `<doc_id>-added-<n>` for lines an employee added. |
| `position` | Order on the document after deletions (1, 2, ...). Use `line_id`, not `position`, to refer to a line. |
| `page`, `row_location` | Page number and the whole row's box, or `null` for added lines. |
| `added_by_reviewer` | `true` if an employee typed the whole line. |
| `fields` | `line_no`, `code`, `description`, `quantity`, `unit`, `unit_price`, `discount` (percent), `line_total`, each in the field format above. `code` is the supplier's code as printed, not a Bina code. |
| `model_codes_in_description` | Model or part numbers found inside the description text (for example `HDMI-2.1`). A hint for matching, not a confirmed code. |
| `product_match` | **Reserved for the matching component.** Today it is always `{"status": "not_matched", "bina_item_code": null, "candidates": []}`. |

### Ids are stable

`doc_id` never changes. `line_id` is stable for one `reading_id`. If the document
is read again, store your results against the new `reading_id` and match lines
again.

## Positions

`bbox` is `[x0, y0, x1, y1]` in pixels of the page image named in
`pages[].image` (origin top-left). For a PDF it is the page rendered at 300 dpi.
For a photo it is the cleaned image (after cropping and straightening), so the
box always fits the image the employee saw.

## How a later component should connect

1. Read confirmed documents with `/api/handoff?since=<last run>` or the JSONL.
2. Check `schema` and `schema_version`. Refuse a major version you do not know.
3. Do the work (for example, suggest Bina items for each line) and **store the results
   in your own store**, keyed by `doc_id` + `reading_id` + `line_id`. Do not write into
   the document folder.
4. Treat every result as a suggestion until an employee confirms it. Never update Bina
   automatically.

The interface for product matching is fixed in `handoff.py`:

```python
class ProductMatcher(Protocol):
    def match(self, line: dict, document: dict) -> dict:
        # -> {"status": "suggested" | "no_candidates",
        #     "candidates": [{"bina_item_code": str, "score": float, "reason": str}]}
        ...

results = handoff.suggest_matches(doc, my_matcher)   # {line_id: result}; doc is not changed
```

`NoMatcher` is the placeholder used until a real one exists. A RAG-based matcher
would implement `match()`. It would build its index from the catalog and from past
confirmed lines (`description` + `code` + the Bina code an employee chose).

The parts stay separate:

```
OCR (ocr.py, textlayer.py)     words and lines with positions      raw/page-*.json
field extraction (extract.py)  header, lines, checks               fields.json
employee review (review.py)    confirmed values + history          review.json
hand-off (handoff.py)          this format                         /api/handoff, JSONL
product matching / RAG         NOT BUILT: reads this format, keeps its own store
Bina                           NOT CONNECTED: updated only by a person
```

## Flat exports (for employees)

`/export/confirmed.xlsx` (sheets "מסמכים" and "שורות"), `/export/confirmed.csv` (lines),
and per document `/doc/<id>/export.xlsx` and `.csv`. One row per line with the
document fields repeated, each line field as `value` plus `<field>_as_read`, then
`changed_by_reviewer` and `bina_item_code` (empty for now). The CSV has a BOM so
Excel shows Hebrew correctly. These files are for people. Programs should use the
JSON.

## Changing the format

- Adding a key: `1.0` → `1.1`. Readers must ignore keys they do not know.
- Renaming or removing a key, or changing its meaning: `2.0`, with a new schema file.
- Run `python -m pytest tests/test_handoff.py` after any change; it checks the
  output against the schema.
