import csv
import io

import cv2
import numpy as np
import pytest

from ocr_app.extract import _check_line, extract_product_rows, field, product_rows_to_csv
from ocr_app.preprocess import detect_scan_like
from ocr_app.table import classify_header
from ocr_app.textutil import parse_number, visual_to_logical


def test_parse_number():
    assert parse_number("12,345.00") == 12345.0
    assert parse_number("10-") == -10.0  # minus after the number (RTL)
    assert parse_number("-696.10") == -696.1
    assert parse_number("12a") is None
    assert parse_number("") is None


def test_visual_to_logical_hebrew_and_brackets():
    assert visual_to_logical('מ"עב (2000) המגוד תרבח') == 'חברת דוגמה (2000) בע"מ'
    assert visual_to_logical("2026/1234 רפסמ ןיבוט תנמזה") == "הזמנת טובין מספר 2026/1234"
    assert visual_to_logical("email: info@example.com") == "email: info@example.com"


def test_classify_header():
    assert classify_header("קוד פריט") == "code"
    assert classify_header("תאור פריט") == "description"
    assert classify_header("סה''יכ ש''ח") == "line_total"  # OCR variant of סה"כ
    assert classify_header("ריחמ") == "unit_price"  # reversed by OCR
    assert classify_header("Qty") == "quantity"


def _line(q, p, d, t):
    item = {k: field(str(v), v) for k, v in
            {"quantity": q, "unit_price": p, "discount": d, "line_total": t}.items()}
    _check_line(item)
    return item


def test_line_check_negative_discount_is_a_surcharge():
    item = _line(20, 500.0, -10.0, 11000.0)  # a "-10" discount that adds 10%, as seen on a real order
    assert item["checks"][0]["result"] == "ok"
    assert "הועלה" in item["checks"][0]["note"]


def test_line_check_failure_marks_fields():
    item = _line(20, 500.0, 0, 11000.0)
    assert item["checks"][0]["result"] == "failed"
    assert item["quantity"]["status"] == "check_failed"


def test_qa_summary_counts_accuracy_and_silent_errors():
    from ocr_app.qa import summarize_document

    reference = {
        "header": {
            "document_number": {"value": "INV-102"},
            "total": {"value": 200.0},
        },
        "lines": [{
            "fields": {
                "description": {"value": "כבל HDMI", "status": "ok"},
                "quantity": {"value": 2.0, "status": "ok"},
                "unit_price": {"value": 100.0, "status": "ok"},
                "line_total": {"value": 200.0, "status": "ok"},
            }
        }],
    }
    candidate = {
        "header": {
            "document_number": {"value": "INV-102", "status": "ok"},
            "total": {"value": 195.0, "status": "ok"},
        },
        "lines": [{
            "fields": {
                "description": {"value": "כבל HDMI", "status": "ok"},
                "quantity": {"value": 2.0, "status": "ok"},
                "unit_price": {"value": 100.0, "status": "ok"},
                "line_total": {"value": 195.0, "status": "ok"},
            }
        }],
    }

    result = summarize_document(reference, candidate)
    assert result["field_accuracy"] == pytest.approx(0.6667, abs=1e-4)
    assert result["field_completeness"] == 1.0
    assert result["silent_errors"] == 2
    assert result["document_ok"] is False


def test_extract_product_rows_from_json_document():
    doc = {
        "lines": [
            {
                "line_id": "L1",
                "fields": {
                    "line_no": {"value": 1},
                    "code": {"value": "HDMI-001"},
                    "description": {"value": "כבל HDMI"},
                    "quantity": {"value": 2},
                    "unit_price": {"value": 100.0},
                    "discount": {"value": 0},
                    "line_total": {"value": 200.0},
                },
            },
            {
                "line_id": "L2",
                "fields": {
                    "line_no": {"value": 2},
                    "code": {"value": "USB-010"},
                    "description": {"value": "מארז USB"},
                    "quantity": {"value": 1},
                    "unit_price": {"value": 30.0},
                    "discount": {"value": 10.0},
                    "line_total": {"value": 27.0},
                },
            },
            {"line_id": "L3", "fields": {"description": {"value": ""}, "quantity": {"value": 4}}},
        ]
    }
    rows = extract_product_rows(doc)
    assert rows == [
        {
            "line_no": 1,
            "code": "HDMI-001",
            "description": "כבל HDMI",
            "quantity": 2,
            "unit_price": 100.0,
            "discount": 0,
            "line_total": 200.0,
        },
        {
            "line_no": 2,
            "code": "USB-010",
            "description": "מארז USB",
            "quantity": 1,
            "unit_price": 30.0,
            "discount": 10.0,
            "line_total": 27.0,
        },
    ]


def test_product_rows_to_csv_exports_only_product_table_columns():
    doc = {
        "header": {"document_number": {"value": "INV-1"}},
        "lines": [
            {
                "line_id": "L1",
                "fields": {
                    "line_no": {"value": 1},
                    "code": {"value": "HDMI-001"},
                    "description": {"value": "כבל HDMI"},
                    "quantity": {"value": 2},
                    "unit_price": {"value": 100.0},
                    "discount": {"value": 0},
                    "line_total": {"value": 200.0},
                },
            },
            {
                "line_id": "L2",
                "fields": {
                    "line_no": {"value": 2},
                    "code": {"value": "USB-010"},
                    "description": {"value": "מארז USB"},
                    "quantity": {"value": 1},
                    "unit_price": {"value": 30.0},
                    "discount": {"value": 10.0},
                    "line_total": {"value": 27.0},
                },
            },
        ],
    }
    csv_text = product_rows_to_csv(doc)
    reader = csv.DictReader(io.StringIO(csv_text))
    rows = list(reader)
    assert reader.fieldnames == ["line_no", "code", "description", "quantity", "unit_price", "discount", "line_total"]
    assert rows[0]["description"] == "כבל HDMI"
    assert rows[1]["line_total"] == "27.0"
    assert all("document_number" not in row for row in rows)


def test_detect_scan_like_works_for_scanned_document():
    arr = np.full((1200, 1600, 3), 255, dtype=np.uint8)
    cv2.rectangle(arr, (80, 80), (1520, 1120), (30, 30, 30), thickness=20)
    for y in range(180, 1000, 90):
        cv2.line(arr, (180, y), (1420, y), (0, 0, 0), thickness=4)
    assert detect_scan_like(arr) is True
