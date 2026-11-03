"""Hand-off of reviewed documents to later components (Stage 6).

This module is the ONE place where other components (product matching, a
future RAG component, an import into Bina) get document data from. They
should not read fields.json or review.json directly: those are internal and
may change. The format produced here is versioned and documented in
docs/DATA_FORMAT.md, with a JSON Schema in docs/schema/.

The components stay separate:

    OCR (ocr.py, textlayer.py)        -> words and lines with positions
    field extraction (extract.py)     -> fields.json
    employee review (review.py)       -> review.json
    hand-off (this file)              -> "reviewed document" JSON
    product matching  (NOT BUILT)     -> reads the hand-off, writes its own results
    RAG               (NOT BUILT)     -> reads the hand-off, writes its own results

Nothing here builds embeddings, a vector database or RAG, and nothing here
writes to Bina. A reviewed document says what the paper says; it does not
prove that the goods arrived.

Command line:
    python -m ocr_app.handoff --data data jsonl out.jsonl   [--status confirmed] [--since 2026-11-01]
    python -m ocr_app.handoff --data data excel out.xlsx
    python -m ocr_app.handoff --data data show <doc_id>
"""
import argparse
import csv
import hashlib
import io
import json
import sys
from pathlib import Path
from typing import Iterator, Protocol

from . import review

SCHEMA_NAME = "invoice-ocr/reviewed-document"
SCHEMA_VERSION = "1.0"
SCHEMA_FILE = Path(__file__).resolve().parent.parent / "docs" / "schema" / "reviewed-document.v1.schema.json"
COORDINATES = "pixels of the page image named in pages[].image (origin top-left)"


def _location(src):
    if not src or not src.get("bbox") or not src.get("page"):
        return None
    return {"page": src["page"], "bbox": [int(v) for v in src["bbox"]]}


def _value(item: dict) -> dict:
    """One field: the reviewed value next to exactly what was read."""
    return {
        "value": item.get("value"),
        "as_read": item.get("original_raw"),
        "read_value": item.get("original_value"),
        "read_status": item.get("read_status", "missing"),
        "changed_by_reviewer": bool(item.get("changed")),
        "location": _location(item.get("source")),
    }


def _empty_value() -> dict:
    return {"value": None, "as_read": None, "read_value": None, "read_status": "missing",
            "changed_by_reviewer": False, "location": None}


def _pages(doc_dir: Path) -> list[dict]:
    out = []
    for pf in sorted((doc_dir / "raw").glob("page-*.json")):
        p = json.loads(pf.read_text())
        out.append({"page": p["page"], "source_file": p.get("source_file"), "source_page": p.get("source_page"),
                    "read_method": p["method"], "image": p["coordinates_refer_to"],
                    "preprocessing": p.get("preprocessing", [])})
    return out


def build(doc_dir: Path) -> dict:
    """The reviewed-document JSON for one document folder."""
    doc_dir = Path(doc_dir)
    r = review.load(doc_dir)
    meta = json.loads((doc_dir / "meta.json").read_text())
    reading_id = hashlib.sha256((doc_dir / "fields.json").read_bytes()).hexdigest()[:16]
    lines, deleted = [], []
    for l in r["lines"]:
        if l["deleted"]:
            deleted.append(l["line_id"])
            continue
        fields = {k: _value(l["fields"][k]) if k in l["fields"] else _empty_value() for k in review.LINE_KEYS}
        lines.append({
            "line_id": l["line_id"],
            "position": len(lines) + 1,
            "page": l["page"],
            "row_location": {"page": l["page"], "bbox": [int(v) for v in l["row_bbox"]]}
            if l.get("row_bbox") and l.get("page") else None,
            "added_by_reviewer": bool(l.get("added_by_reviewer")),
            "fields": fields,
            "model_codes_in_description": l.get("codes_in_description", []),
            # Filled by a LATER component. OCR alone does not identify the Bina item.
            "product_match": {"status": "not_matched", "bina_item_code": None, "candidates": []},
        })
    return {
        "schema": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "doc_id": r["doc_id"],
        "reading_id": reading_id,
        "review": {
            "status": r["status"],
            "confirmed_by": r.get("confirmed_by"),
            "confirmed_at": r.get("confirmed_at"),
            "updated_at": r.get("updated_at"),
            "changes_count": len(r.get("history", [])),
        },
        "goods_receipt_confirmed": False,
        "model": r.get("model"),
        "source_files": [{"file": o["file"], "sha256": o["sha256"], "original_filename": o.get("original_filename")}
                         for o in meta.get("originals", [])],
        "uploaded_at": meta.get("uploaded_at"),
        "coordinate_space": COORDINATES,
        "pages": _pages(doc_dir),
        "header": {k: _value(r["header"][k]) if k in r["header"] else _empty_value() for k in review.HEADER_KEYS},
        "lines": lines,
        "deleted_line_ids": deleted,
        "warnings": r.get("warnings", []),
        "checks": {"at_reading": r.get("checks", []), "after_review": review.check_reviewed(r)},
    }


def iter_documents(data_dir, status: str | None = "confirmed", since: str | None = None) -> Iterator[dict]:
    """All documents with the given review status (None = any), optionally
    only those reviewed/updated at or after `since` (ISO date or time)."""
    docs = Path(data_dir) / "documents"
    if not docs.exists():
        return
    for d in sorted(docs.iterdir()):
        if not (d / "fields.json").exists() or not (d / "meta.json").exists():
            continue
        doc = build(d)
        if status and doc["review"]["status"] != status:
            continue
        stamp = doc["review"]["confirmed_at"] or doc["review"]["updated_at"] or doc["uploaded_at"] or ""
        if since and stamp < since:
            continue
        yield doc


def validate(doc: dict) -> list[str]:
    """Check a document against the JSON Schema. Returns error messages."""
    try:
        import jsonschema
    except ImportError:
        return ["jsonschema is not installed: pip install jsonschema"]
    schema = json.loads(SCHEMA_FILE.read_text())
    v = jsonschema.Draft202012Validator(schema)
    return [f"{'/'.join(map(str, e.path))}: {e.message}" for e in v.iter_errors(doc)]


# ---------------------------------------------------------------- flat exports for employees
DOC_COLUMNS = ["doc_id", "review_status", "confirmed_by", "confirmed_at", "supplier_name", "supplier_tax_id",
               "supplier_number", "document_type", "document_number", "document_date", "currency",
               "subtotal", "vat", "total", "lines", "model"]


def document_row(doc: dict) -> dict:
    h = doc["header"]
    row = {"doc_id": doc["doc_id"], "review_status": doc["review"]["status"],
           "confirmed_by": doc["review"]["confirmed_by"], "confirmed_at": doc["review"]["confirmed_at"]}
    row.update({k: h[k]["value"] for k in review.HEADER_KEYS})
    row["lines"] = len(doc["lines"])
    row["model"] = (doc.get("model") or {}).get("model_id") or (doc.get("model") or {}).get("lang")
    return row


def line_rows(doc: dict) -> list[dict]:
    h = doc["header"]
    rows = []
    for l in doc["lines"]:
        row = {"doc_id": doc["doc_id"], "line_id": l["line_id"], "position": l["position"], "page": l["page"],
               "review_status": doc["review"]["status"], "supplier_name": h["supplier_name"]["value"],
               "document_type": h["document_type"]["value"], "document_number": h["document_number"]["value"],
               "document_date": h["document_date"]["value"]}
        for k in review.LINE_KEYS:
            row[k] = l["fields"][k]["value"]
            row[f"{k}_as_read"] = l["fields"][k]["as_read"]
        row["changed_by_reviewer"] = any(f["changed_by_reviewer"] for f in l["fields"].values())
        row["bina_item_code"] = l["product_match"]["bina_item_code"]
        rows.append(row)
    return rows


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return "﻿" + buf.getvalue()  # BOM so Excel opens Hebrew correctly


def to_xlsx(sheets: dict[str, list[dict]]) -> bytes:
    import xlsxwriter
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    bold = wb.add_format({"bold": True})
    for name, rows in sheets.items():
        ws = wb.add_worksheet(name)
        ws.right_to_left()
        if not rows:
            continue
        for c, k in enumerate(rows[0].keys()):
            ws.write(0, c, k, bold)
        for i, row in enumerate(rows, 1):
            for c, v in enumerate(row.values()):
                ws.write(i, c, v)
        ws.freeze_panes(1, 0)
    wb.close()
    return buf.getvalue()


def excel_for(docs: list[dict]) -> bytes:
    return to_xlsx({"מסמכים": [document_row(d) for d in docs],
                    "שורות": [r for d in docs for r in line_rows(d)]})


# ---------------------------------------------------------------- interface for later components
class ProductMatcher(Protocol):
    """What a future product-matching component (e.g. one using RAG) must
    provide. It receives one reviewed line and its document and returns
    suggestions. It must NOT change the reviewed values, and its result is
    only a suggestion until an employee confirms it."""

    def match(self, line: dict, document: dict) -> dict:
        """Return {"status": "suggested" | "no_candidates",
                   "candidates": [{"bina_item_code": str, "score": float, "reason": str}]}"""
        ...


class NoMatcher:
    """Placeholder used until product matching exists."""

    def match(self, line: dict, document: dict) -> dict:
        return {"status": "no_candidates", "candidates": []}


def suggest_matches(doc: dict, matcher: ProductMatcher) -> dict:
    """Run a matcher over all lines. Returns {line_id: result}. The result is
    stored by the matching component itself, keyed by doc_id + reading_id +
    line_id; the reviewed document is not modified."""
    return {l["line_id"]: matcher.match(l, doc) for l in doc["lines"]}


# ---------------------------------------------------------------- command line
def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m ocr_app.handoff")
    ap.add_argument("--data", default="data")
    sub = ap.add_subparsers(dest="cmd", required=True)
    j = sub.add_parser("jsonl", help="one reviewed document per line")
    j.add_argument("out")
    x = sub.add_parser("excel", help="documents + lines sheets for employees")
    x.add_argument("out")
    for p in (j, x):
        p.add_argument("--status", default="confirmed", help="confirmed | draft | not_reviewed | any")
        p.add_argument("--since", default=None)
    s = sub.add_parser("show", help="print one document")
    s.add_argument("doc_id")
    a = ap.parse_args(argv)
    if a.cmd == "show":
        doc = build(Path(a.data) / "documents" / a.doc_id)
        print(json.dumps(doc, ensure_ascii=False, indent=1))
        return
    docs = list(iter_documents(a.data, None if a.status == "any" else a.status, a.since))
    if a.cmd == "jsonl":
        with open(a.out, "w", encoding="utf-8") as f:
            for d in docs:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
    else:
        Path(a.out).write_bytes(excel_for(docs))
    print(f"{len(docs)} documents -> {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
