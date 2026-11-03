"""Stage 6: the reviewed-document hand-off format (synthetic data only)."""
import json

import openpyxl
import pytest

from ocr_app import handoff, review
from ocr_app.extract import field


def _make_doc(data_dir, doc_id, uploaded="2026-11-01T08:00:00+00:00"):
    """A minimal document folder, as the pipeline would leave it."""
    d = data_dir / "documents" / doc_id
    (d / "raw").mkdir(parents=True)
    header = {k: field() for k in review.HEADER_KEYS}
    header["supplier_name"] = field('ספק לדוגמה בע"מ', 'ספק לדוגמה בע"מ', page=1, bbox=[10, 10, 300, 40])
    header["document_type"] = field("חשבונית מס", "invoice", page=1, bbox=[100, 60, 300, 90])
    header["document_number"] = field("12345", "12345", page=1, bbox=[310, 60, 400, 90])
    header["subtotal"] = field("100.00", 100.0, page=1, bbox=[10, 900, 120, 930])

    def line(r, desc, qty, price):
        l = {k: field() for k in review.LINE_KEYS}
        l.update(description=field(desc, desc, page=1, bbox=[200, 100 + r * 30, 500, 125 + r * 30]),
                 quantity=field(f"{qty:g}", float(qty)), unit_price=field(f"{price:.2f}", price),
                 line_total=field(f"{qty * price:.2f}", qty * price))
        l.update(line_id=f"{doc_id}-p1-r{r:02d}", page=1, row_bbox=[10, 100 + r * 30, 900, 125 + r * 30],
                 codes_in_description=[], checks=[])
        return l

    fields = {"doc_id": doc_id, "model": {"engine": "tesseract", "model_id": "stock", "lang": "heb+eng"},
              "warnings": [], "checks": [], "header": header,
              "lines": [line(1, "כבל HDMI 2m", 2, 25.0), line(2, "Tripod stand", 1, 50.0)]}
    (d / "fields.json").write_text(json.dumps(fields, ensure_ascii=False))
    (d / "meta.json").write_text(json.dumps({
        "doc_id": doc_id, "uploaded_at": uploaded,
        "originals": [{"file": "original/a.pdf", "sha256": "0" * 64, "original_filename": "a.pdf"}]}))
    (d / "raw" / "page-001.json").write_text(json.dumps({
        "page": 1, "source_file": "original/a.pdf", "source_page": 1, "method": "pdf_text",
        "coordinates_refer_to": "pages/page-001.png", "preprocessing": []}))
    return d


def _confirm(d, user="דנה", changes=None):
    r = review.load(d)
    assert review.apply_changes(r, changes or {}, user) == []
    r["status"] = "confirmed"
    r["confirmed_by"] = user
    r["confirmed_at"] = review.now()
    review.save(d, r)


def test_build_matches_schema(tmp_path):
    pytest.importorskip("jsonschema")
    d = _make_doc(tmp_path, "doc1")
    doc = handoff.build(d)
    assert handoff.validate(doc) == []
    assert doc["schema"] == handoff.SCHEMA_NAME and doc["schema_version"] == "1.0"
    assert doc["goods_receipt_confirmed"] is False
    assert [l["line_id"] for l in doc["lines"]] == ["doc1-p1-r01", "doc1-p1-r02"]
    assert doc["header"]["supplier_name"]["location"] == {"page": 1, "bbox": [10, 10, 300, 40]}
    for l in doc["lines"]:
        assert l["product_match"] == {"status": "not_matched", "bina_item_code": None, "candidates": []}


def test_review_values_kept_next_to_reading(tmp_path):
    d = _make_doc(tmp_path, "doc1")
    _confirm(d, changes={"lines": [{"line_id": "doc1-p1-r01", "fields": {"quantity": "3"}},
                                   {"line_id": "doc1-p1-r02", "deleted": True}]})
    doc = handoff.build(d)
    assert handoff.validate(doc) == [] or "not installed" in handoff.validate(doc)[0]
    q = doc["lines"][0]["fields"]["quantity"]
    assert q["value"] == 3.0 and q["read_value"] == 2.0 and q["as_read"] == "2" and q["changed_by_reviewer"]
    assert [l["line_id"] for l in doc["lines"]] == ["doc1-p1-r01"]
    assert doc["deleted_line_ids"] == ["doc1-p1-r02"]
    assert doc["review"]["status"] == "confirmed" and doc["review"]["changes_count"] == 2
    assert doc["checks"]["after_review"]  # 3 x 25 != 50 is reported, not hidden


def test_reading_id_changes_only_with_new_reading(tmp_path):
    d = _make_doc(tmp_path, "doc1")
    first = handoff.build(d)["reading_id"]
    _confirm(d)
    assert handoff.build(d)["reading_id"] == first  # review does not change it
    f = json.loads((d / "fields.json").read_text())
    f["warnings"] = ["page 2 added"]
    (d / "fields.json").write_text(json.dumps(f))
    assert handoff.build(d)["reading_id"] != first


def test_status_and_since_filters(tmp_path):
    _make_doc(tmp_path, "a")
    _confirm(_make_doc(tmp_path, "b"))
    assert [d["doc_id"] for d in handoff.iter_documents(tmp_path)] == ["b"]
    assert [d["doc_id"] for d in handoff.iter_documents(tmp_path, status=None)] == ["a", "b"]
    assert [d["doc_id"] for d in handoff.iter_documents(tmp_path, status="not_reviewed")] == ["a"]
    assert list(handoff.iter_documents(tmp_path, since="2999-01-01")) == []
    assert list(handoff.iter_documents(tmp_path / "missing")) == []


def test_flat_exports(tmp_path):
    _confirm(_make_doc(tmp_path, "b"))
    docs = list(handoff.iter_documents(tmp_path))
    rows = handoff.line_rows(docs[0])
    assert rows[0]["quantity"] == 2.0 and rows[0]["quantity_as_read"] == "2" and rows[0]["bina_item_code"] is None
    text = handoff.to_csv(rows)
    assert text.startswith("﻿") and "כבל HDMI 2m" in text
    out = tmp_path / "x.xlsx"
    out.write_bytes(handoff.excel_for(docs))
    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames == ["מסמכים", "שורות"]
    assert wb["מסמכים"].max_row == 2 and wb["שורות"].max_row == 3


def test_cli_jsonl(tmp_path, capsys):
    _confirm(_make_doc(tmp_path, "b"))
    out = tmp_path / "out.jsonl"
    handoff.main(["--data", str(tmp_path), "jsonl", str(out)])
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["doc_id"] == "b"


def test_matcher_interface_does_not_change_document(tmp_path):
    doc = handoff.build(_make_doc(tmp_path, "doc1"))
    before = json.dumps(doc, sort_keys=True)

    class Fake:
        def match(self, line, document):
            return {"status": "suggested", "candidates": [{"bina_item_code": "X1", "score": 0.5, "reason": "test"}]}

    res = handoff.suggest_matches(doc, Fake())
    assert set(res) == {"doc1-p1-r01", "doc1-p1-r02"}
    assert json.dumps(doc, sort_keys=True) == before
    assert handoff.suggest_matches(doc, handoff.NoMatcher())["doc1-p1-r01"]["status"] == "no_candidates"
