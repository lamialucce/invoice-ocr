import json

import pytest

from ocr_app import models, review, training
from ocr_app.extract import field


def _review():
    h = {k: field() for k in review.HEADER_KEYS}
    h["subtotal"] = field("100.00", 100.0)
    line = {k: field() for k in review.LINE_KEYS}
    line.update(quantity=field("2", 2.0), unit_price=field("50.00", 50.0), line_total=field("100.00", 100.0),
                description=field("כבל HDMI", "כבל HDMI"))
    return {"schema_version": "1.0", "doc_id": "d1", "status": "not_reviewed", "history": [],
            "header": {k: review._item(v) for k, v in h.items()},
            "lines": [{"line_id": "d1-p1-r01", "page": 1, "row_bbox": None, "deleted": False,
                       "added_by_reviewer": False, "checks": [], "codes_in_description": [],
                       "fields": {k: review._item(v) for k, v in line.items()}}]}


def test_change_keeps_original_and_history():
    r = _review()
    errs = review.apply_changes(r, {"lines": [{"line_id": "d1-p1-r01", "fields": {"quantity": "3"}}]}, "דנה")
    assert errs == []
    q = r["lines"][0]["fields"]["quantity"]
    assert q["value"] == 3.0 and q["original_value"] == 2.0 and q["changed"]
    assert r["history"][-1]["by"] == "דנה"
    assert review.check_reviewed(r)  # 3 x 50 != 100 is reported


def test_bad_number_is_rejected():
    r = _review()
    errs = review.apply_changes(r, {"header": {"total": "abc"}}, "x")
    assert errs and r["header"]["total"]["value"] is None


def test_split_is_by_document():
    assert training.split_of("doc-a") == training.split_of("doc-a")
    splits = {training.split_of(f"doc-{i}") for i in range(200)}
    assert splits == {"train", "val", "test"}


def test_model_needs_evaluation_and_rollback(tmp_path):
    models.register(tmp_path, "ft1", "heb_ft1+eng", tmp_path / "m", "test", {"examples": 1})
    with pytest.raises(ValueError):
        models.activate(tmp_path, "ft1", "x")  # trained but not evaluated
    models.record_evaluation(tmp_path, "ft1", {"summary_text": "ok"})
    models.activate(tmp_path, "ft1", "x")
    assert models.load(tmp_path)["active"] == "ft1"
    models.rollback(tmp_path, "x")
    reg = models.load(tmp_path)
    assert reg["active"] == "stock"
    models.apply_active(tmp_path)
