from ocr_app.extract import _check_line, field
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
