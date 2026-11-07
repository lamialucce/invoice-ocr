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


def test_approved_examples_can_trigger_auto_training_plan(tmp_path):
    ex_dir = tmp_path / "training" / "examples"
    ex_dir.mkdir(parents=True)
    ex = {
        "example_id": "ex1",
        "doc_id": "doc-1",
        "doc_group": "doc-1",
        "page": 1,
        "bbox": [0, 0, 10, 10],
        "page_image": "page-001.png",
        "column": "description",
        "line_id": "doc-1-r01",
        "crop": "crops/ex1.png",
        "predicted_text": "HDMI",
        "predicted_conf": 80.0,
        "model": "stock",
        "suggested_text": "כבל HDMI",
        "suggestion_origin": "ocr_prediction",
        "verified_text": "כבל HDMI",
        "status": "approved",
        "script": "Hebrew",
        "input_kind": "scan_or_photo",
        "created_at": "2026-01-01T00:00:00+00:00",
        "history": [],
    }
    (ex_dir / "ex1.json").write_text(json.dumps(ex, ensure_ascii=False))
    plan = training.maybe_train_approved_model(tmp_path, model_id="ft-dry", dry_run=True, minimum_examples=1)
    assert plan["ok"] is True
    assert plan["approved_examples"] == 1
    assert plan["dry_run"] is True


def test_auto_training_requires_100_approvals_and_waits_for_full_windows(tmp_path):
    ex_dir = tmp_path / "training" / "examples"
    ex_dir.mkdir(parents=True)
    for i in range(99):
        ex = {
            "example_id": f"ex{i:02d}",
            "doc_id": f"doc-{i}",
            "doc_group": f"doc-{i}",
            "page": 1,
            "bbox": [0, 0, 10, 10],
            "page_image": "page-001.png",
            "column": "description",
            "line_id": f"doc-{i}-r01",
            "crop": f"crops/ex{i:02d}.png",
            "predicted_text": "HDMI",
            "predicted_conf": 80.0,
            "model": "stock",
            "suggested_text": "כבל HDMI",
            "suggestion_origin": "ocr_prediction",
            "verified_text": "כבל HDMI",
            "status": "approved",
            "script": "Hebrew",
            "input_kind": "scan_or_photo",
            "created_at": "2026-01-01T00:00:00+00:00",
            "history": [],
        }
        (ex_dir / f"ex{i:02d}.json").write_text(json.dumps(ex, ensure_ascii=False))

    plan = training.maybe_train_approved_model(tmp_path, model_id="ft-dry-99", dry_run=True)
    assert plan["ok"] is False
    assert plan["approved_examples"] == 99

    ex = {
        "example_id": "ex100",
        "doc_id": "doc-100",
        "doc_group": "doc-100",
        "page": 1,
        "bbox": [0, 0, 10, 10],
        "page_image": "page-001.png",
        "column": "description",
        "line_id": "doc-100-r01",
        "crop": "crops/ex100.png",
        "predicted_text": "HDMI",
        "predicted_conf": 80.0,
        "model": "stock",
        "suggested_text": "כבל HDMI",
        "suggestion_origin": "ocr_prediction",
        "verified_text": "כבל HDMI",
        "status": "approved",
        "script": "Hebrew",
        "input_kind": "scan_or_photo",
        "created_at": "2026-01-01T00:00:00+00:00",
        "history": [],
    }
    (ex_dir / "ex100.json").write_text(json.dumps(ex, ensure_ascii=False))
    plan = training.maybe_train_approved_model(tmp_path, model_id="ft-dry-100", dry_run=True)
    assert plan["ok"] is True
    assert plan["approved_examples"] == 100

    for i in range(101, 106):
        ex = {
            "example_id": f"ex{i:02d}",
            "doc_id": f"doc-{i}",
            "doc_group": f"doc-{i}",
            "page": 1,
            "bbox": [0, 0, 10, 10],
            "page_image": "page-001.png",
            "column": "description",
            "line_id": f"doc-{i}-r01",
            "crop": f"crops/ex{i:02d}.png",
            "predicted_text": "HDMI",
            "predicted_conf": 80.0,
            "model": "stock",
            "suggested_text": "כבל HDMI",
            "suggestion_origin": "ocr_prediction",
            "verified_text": "כבל HDMI",
            "status": "approved",
            "script": "Hebrew",
            "input_kind": "scan_or_photo",
            "created_at": "2026-01-01T00:00:00+00:00",
            "history": [],
        }
        (ex_dir / f"ex{i:02d}.json").write_text(json.dumps(ex, ensure_ascii=False))

    plan = training.maybe_train_approved_model(tmp_path, model_id="ft-dry-105", dry_run=True)
    assert plan["ok"] is False
    assert plan["approved_examples"] == 105


def test_auto_training_registers_and_activates_on_success(monkeypatch, tmp_path):
    ex_dir = tmp_path / "training" / "examples"
    ex_dir.mkdir(parents=True)
    crop_dir = tmp_path / "training" / "crops"
    crop_dir.mkdir(parents=True)
    (crop_dir / "ex1.png").write_bytes(b"fake-image")
    ex = {
        "example_id": "ex1",
        "doc_id": "doc-1",
        "doc_group": "doc-1",
        "page": 1,
        "bbox": [0, 0, 10, 10],
        "page_image": "page-001.png",
        "column": "description",
        "line_id": "doc-1-r01",
        "crop": "crops/ex1.png",
        "predicted_text": "HDMI",
        "predicted_conf": 80.0,
        "model": "stock",
        "suggested_text": "כבל HDMI",
        "suggestion_origin": "ocr_prediction",
        "verified_text": "כבל HDMI",
        "status": "approved",
        "script": "Hebrew",
        "input_kind": "scan_or_photo",
        "created_at": "2026-01-01T00:00:00+00:00",
        "history": [],
    }
    (ex_dir / "ex1.json").write_text(json.dumps(ex, ensure_ascii=False))

    monkeypatch.setattr(training.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(training, "export_tesstrain", lambda *a, **k: {"counts": {"train": 1, "val": 0, "test": 0}})

    plan = training.maybe_train_approved_model(tmp_path, model_id="ft-live", minimum_examples=1)
    reg = models.load(tmp_path)
    assert plan["ok"] is True
    assert reg["active"] == "ft-live"
    assert reg["models"][-1]["status"] == "active"


def test_training_status_is_persisted_and_dynamic(tmp_path):
    training.set_training_status(tmp_path, "training", "מייצר אימון למודל חדש", model_id="ft-demo")
    status = training.get_training_status(tmp_path)
    assert status["state"] == "training"
    assert status["message"] == "מייצר אימון למודל חדש"
    assert status["model_id"] == "ft-demo"
