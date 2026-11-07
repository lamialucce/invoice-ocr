"""Structured fields from the raw reading result.

Rules:
- "raw" is exactly the text that was read. "value" is the cleaned version
  (number, ISO date, ...). They are stored separately.
- Nothing is invented. If a value is not found, status is "missing".
- status values:
    ok           found and passed the checks we have
    missing      not found on the document
    unclear      found, but the reading or our guess is uncertain
    check_failed found, but the math does not add up (e.g. qty x price != total)
- No product matching to the catalog happens here (that is a later stage).
"""
import csv
import io
import json
import re
from datetime import date

from .textutil import clean, parse_number

DOC_TYPES = [
    ("invoice_receipt", ["חשבונית מס קבלה", "חשבונית מס/קבלה", "חשבונית מס-קבלה", "tax invoice receipt", "invoice receipt"]),
    ("credit_note", ["חשבונית זיכוי", "credit note", "credit invoice"]),
    ("invoice", ["חשבונית מס", "חשבונית", "tax invoice", "invoice"]),
    ("delivery_note", ["תעודת משלוח", "תעודת משלוח מספר", "delivery note", "packing slip", "delivery"]),
    ("purchase_order", ["הזמנת טובין", "הזמנת רכש", "הזמנה", "purchase order"]),
]
EXPECTED_TYPES = {"invoice", "invoice_receipt", "delivery_note", "credit_note"}
DATE_RE = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\b|\b(\d{4})[/.\-](\d{1,2})[/.\-](\d{1,2})\b")
DOCNUM_RE = re.compile(r"(?<![\d/])(\d{2,4}/\d{2,8}|\d{4,12}(?:-\d+)?)(?![\d/])")
CURRENCY = [("ILS", ['ש"ח', "ש''ח", "₪", "nis", "ils", "שקל"]), ("USD", ["usd", "$", "דולר"]),
            ("EUR", ["eur", "€", "יורו"])]
TAX_ID_RE = re.compile(r"(?:עוסק מורשה|ע\.מ|ח\.פ|ח\"פ|ע\"מ|vat|tax id|company no)[^\d]{0,20}(\d{8,9})", re.I)
MODEL_CODE_RE = re.compile(r"\b[A-Za-z]{2,4}-\d{3,5}-[A-Za-z]{1,3}\b")


def norm(s: str) -> str:
    return clean(s).replace("״", '"').replace("''", '"').replace("׳", "'").lower()


def join_wrapped(raw: str) -> str:
    """Cell text that wrapped onto several lines -> one line. A line ending
    in "-" continues without a space ("LU-" + "69170" -> "LU-69170")."""
    parts = [" ".join(p.split()) for p in raw.split("\n") if p.strip()]
    out = ""
    for p in parts:
        # "a7-" + "iv" -> "a7-iv" (word split), but "B510 -" + "x" keeps the space.
        glue = out.endswith("-") and not out.endswith(" -")
        out += p if not out or glue else " " + p
    return out


def field(raw=None, value=None, status=None, page=None, bbox=None, reasons=None):
    if status is None:
        status = "ok" if raw not in (None, "") else "missing"
    return {"raw": raw, "value": value, "status": status, "reasons": reasons or [],
            "source": {"page": page, "bbox": bbox} if page else None}


def _json_field_value(field_entry):
    if not isinstance(field_entry, dict):
        return None
    if "value" in field_entry:
        return field_entry.get("value")
    return field_entry.get("raw")


def extract_product_rows(document):
    """Return only the invoice-table rows needed for downstream work.

    This intentionally ignores all document metadata: header fields, supplier
    details, totals, warnings and any other top-level keys. Only actual product
    rows from `lines[*].fields` are exported.
    """
    if isinstance(document, (str, bytes)):
        document = json.loads(document)
    if not isinstance(document, dict):
        return []

    lines = document.get("lines", [])
    if not isinstance(lines, list):
        return []

    field_keys = ["line_no", "code", "description", "quantity", "unit_price", "discount", "line_total"]
    rows = []
    for line in lines:
        if not isinstance(line, dict):
            continue
        fields = line.get("fields") or {}
        if not isinstance(fields, dict):
            continue

        description = _json_field_value(fields.get("description")) or ""
        description = str(description).strip() if description is not None else ""
        if not description:
            continue

        entry = {}
        for key in field_keys:
            value = _json_field_value(fields.get(key))
            if key == "discount" and value is None:
                value = 0.0
            entry[key] = value
        rows.append(entry)
    return rows


def product_rows_to_csv(document, include_header=True):
    """Convert a reviewed-document JSON to CSV containing only the product rows.

    Output columns are always ordered as:
    line_no, code, description, quantity, unit_price, discount, line_total
    """
    rows = extract_product_rows(document)
    fieldnames = ["line_no", "code", "description", "quantity", "unit_price", "discount", "line_total"]
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    if include_header:
        writer.writeheader()
    for row in rows:
        cleaned = {}
        for key in fieldnames:
            value = row.get(key)
            if value is None:
                cleaned[key] = ""
            elif isinstance(value, float):
                cleaned[key] = str(value)
            elif isinstance(value, (int, str)):
                cleaned[key] = value
            else:
                cleaned[key] = str(value)
        writer.writerow(cleaned)
    return buf.getvalue()


def _all_lines(pages):
    for p in pages:
        for l in p["lines"]:
            yield p["page"], l


def _doc_type(pages):
    for page, l in _all_lines(pages):
        t = norm(l["text"])
        for key, kws in DOC_TYPES:
            for kw in kws:
                if kw in t:
                    reasons = [] if key in EXPECTED_TYPES else [
                        "סוג מסמך שאינו חשבונית או תעודת משלוח"]
                    return field(kw, key, "ok" if not reasons else "unclear", page, l["bbox"], reasons), l, page
    return field(), None, None


def _doc_number(type_line, type_page):
    if not type_line:
        return field(reasons=["לא נמצא כותרת מסמך"])
    m = DOCNUM_RE.search(clean(type_line["text"]))
    if m:
        return field(m.group(1), m.group(1), "ok", type_page, type_line["bbox"])
    return field(reasons=["לא נמצא מספר בשורת הכותרת"])


def _parse_date(m):
    if m.group(1):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        y, mo, d = int(m.group(4)), int(m.group(5)), int(m.group(6))
    if y < 100:
        y += 2000
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def _doc_date(pages):
    candidates = []
    for page, l in _all_lines(pages):
        for m in DATE_RE.finditer(clean(l["text"])):
            near = any(k in norm(l["text"]) for k in ["תאריך", "date"])
            candidates.append((not near, page, l, m))
    if not candidates:
        return field()
    candidates.sort(key=lambda c: (c[0], c[1]))
    _, page, l, m = candidates[0]
    iso = _parse_date(m)
    reasons = [] if iso else ["תאריך לא תקין"]
    if iso and m.group(1) and int(m.group(1)) <= 12 and int(m.group(2)) <= 12:
        reasons.append("הנחה: יום/חודש (סדר ישראלי)")
    status = "ok" if iso and not candidates[0][0] else "unclear"
    if not reasons and status == "unclear":
        reasons.append("התאריך לא נמצא ליד המילה 'תאריך'")
    return field(m.group(0), iso, status, page, l["bbox"], reasons)


def _strip_date(text: str) -> str:
    """Header lines often share a row with the date: remove it."""
    text = re.sub(r"(תאריך|date)\s*:?\s*", " ", text, flags=re.I)
    text = DATE_RE.sub(" ", text)
    return " ".join(text.split()).strip(" :-")


def _parties(pages, doc_type):
    """Who issued the document and who it is addressed to. Which of them is
    the supplier depends on the document type, so it is always marked for
    the employee to confirm."""
    p1 = pages[0]
    lines = p1["lines"]
    issuer = lines[0] if lines else None
    addressee = None
    for i, l in enumerate(lines):
        t = norm(l["text"])
        if re.search(r"(^|\s)(לכבוד|to:|bill to)", t):
            # Remove the keyword and anything date-like that shares the line.
            rest = re.sub(r"לכבוד|bill to:?|to:|תאריך\W*|date\W*", " ", clean(l["text"]), flags=re.I)
            rest = " ".join(DATE_RE.sub(" ", rest).split()).strip(" :")
            if rest:
                addressee = (rest, l)
            elif i + 1 < len(lines):
                addressee = (clean(lines[i + 1]["text"]), lines[i + 1])
            break
    supplier_is_addressee = doc_type == "purchase_order"
    if supplier_is_addressee and addressee:
        name, l = addressee
        why = "בהזמנה הספק הוא הנמען (לכבוד)"
    elif issuer:
        name, l = _strip_date(clean(issuer["text"])), issuer
        why = "נלקח מהשורה העליונה של המסמך"
    else:
        return field(reasons=["לא נמצא שם ספק"]), field()
    supplier = field(name, name, "unclear", p1["page"], l["bbox"], [why + " — יש לאשר"])
    tax = field()
    for _, l in _all_lines(pages):
        m = TAX_ID_RE.search(clean(l["text"]))
        if m:
            reasons = ["זה מספר העוסק של מנפיק המסמך"] if supplier_is_addressee else []
            tax = field(m.group(1), m.group(1), "unclear" if reasons else "ok", _, l["bbox"], reasons)
            break
    return supplier, tax


def _supplier_number(pages):
    for page, l in _all_lines(pages):
        if "מספר ספק" in norm(l["text"]) or "supplier no" in norm(l["text"]):
            m = re.search(r"\d+", clean(l["text"]))
            if m:
                return field(m.group(0), m.group(0), "ok", page, l["bbox"])
    return field()


def _in_table(page, bbox):
    for t in page["tables"]:
        tb = t["bbox"]
        cy = (bbox[1] + bbox[3]) / 2
        if tb[1] < cy < tb[3]:
            return True
    return False


def _currency(pages):
    """Currency word outside the product table (product names can contain
    letters like "usd")."""
    for p in pages:
        for l in p["lines"]:
            if _in_table(p, l["bbox"]):
                continue
            t = norm(l["text"])
            for code, kws in CURRENCY:
                for kw in kws:
                    # Hebrew may glue a one-letter prefix to the word: בש"ח
                    if re.search(r"(?<![a-zא-ת])[בלמוה]?" + re.escape(kw) + r"(?![a-zא-ת])", t):
                        return field(kw, code, "ok", p["page"], l["bbox"])
    return field()


TOTAL_RE = re.compile(r'סה\W{0,3}י?\W{0,3}[כב]\b|\btotal\b|לתשלום')
VAT_RE = re.compile(r'מע\W{0,3}י?\W{0,3}מ|\bvat\b|\b\d{1,2}(\.\d+)?\s*%')
STRAY = "()[]|=:;'\"`~"


def _amount_in_line(text):
    toks = [t for t in clean(text).split() if "%" not in t]
    nums = [(t, parse_number(t.strip(STRAY))) for t in toks]
    nums = [(t, v) for t, v in nums if v is not None]
    with_dec = [n for n in nums if "." in n[0]]
    return (with_dec or [None])[-1]


def _totals(pages):
    """Totals block. OCR often misreads סה"כ as סה''ב and מע"מ as noise,
    so the keywords are matched loosely."""
    out = {"subtotal": field(), "vat": field(), "total": field()}
    for page, l in _all_lines(pages):
        t = norm(l["text"])
        is_total, is_vat = bool(TOTAL_RE.search(t)), bool(VAT_RE.search(t))
        if "לפני" in t or "subtotal" in t or "before" in t:
            key = "subtotal"
        elif "לתשלום" in t or "כולל" in t or "grand" in t or "total due" in t:
            key = "total"
        elif is_vat:
            key = "vat"
        elif is_total and out["total"]["status"] == "missing":
            key = "total"
        else:
            continue
        if out[key]["status"] != "missing":
            continue
        pick = _amount_in_line(l["text"])
        if pick:
            out[key] = field(pick[0], pick[1], "ok", page, l["bbox"])
    return out


def _cell(t, row, col):
    for c in t["cells"]:
        if c["row"] == row and c["col"] == col:
            return c
    return None


def _line_items(doc_id, pages):
    items = []
    for p in pages:
        for t in p["tables"]:
            kinds = {}
            for c, k in sorted(t["column_kinds"].items(), key=lambda x: int(x[0])):
                if k and k not in kinds.values():  # first column wins if two look alike
                    kinds[int(c)] = k
            nrows = t["n_rows"]
            _infer_line_no_column(t, kinds, nrows)
            for r in range(1, nrows):
                cells = {k: _cell(t, r, c) for c, k in kinds.items()}
                if not any(c and clean(c["text"]) for c in cells.values()):
                    continue
                row_bbox = t["row_bboxes"][r]
                item = {"line_id": f"{doc_id}-p{p['page']}-r{r:02d}", "page": p["page"],
                        "row_bbox": row_bbox}
                for key in ["line_no", "code", "description", "quantity", "unit", "unit_price",
                            "discount", "line_total"]:
                    c = cells.get(key)
                    raw = clean(c["text"]) if c else ""
                    if not raw:
                        item[key] = field(reasons=[] if c else ["אין עמודה כזו במסמך"])
                        continue
                    reasons, status = [], "ok"
                    if key in {"quantity", "unit_price", "discount", "line_total", "line_no"}:
                        value = parse_number(raw)
                        if value is None:
                            status, reasons = "unclear", ["לא מספר תקין"]
                    else:
                        value = join_wrapped(raw)
                    if c.get("conf") is not None and c["conf"] < 70:
                        status = "unclear"
                        reasons.append(f"קריאה לא בטוחה (ביטחון מנוע {c['conf']:.0f})")
                    item[key] = field(raw, value, status, p["page"], c["bbox"], reasons)
                    item[key]["source"]["method"] = c.get("source")
                desc = item["description"]["raw"] or ""
                item["codes_in_description"] = MODEL_CODE_RE.findall(desc)
                _check_line(item)
                items.append(item)
    return items


def _infer_line_no_column(t, kinds, nrows):
    """If the header of a column could not be read but its cells are 1, 2, 3...
    it is the line-number column."""
    if "line_no" in kinds.values():
        return
    for c in range(t["n_cols"]):
        if c in kinds:
            continue
        vals = [parse_number(clean((_cell(t, r, c) or {}).get("text", ""))) for r in range(1, nrows)]
        vals = [v for v in vals if v is not None]
        if len(vals) >= 2 and vals == list(range(1, len(vals) + 1)):
            kinds[c] = "line_no"
            t["column_kinds"][str(c)] = "line_no (inferred)"
            return


def _check_line(item):
    q, pr, tot = (item[k]["value"] for k in ["quantity", "unit_price", "line_total"])
    d = item["discount"]["value"] or 0
    item["checks"] = []
    if None in (q, pr, tot):
        missing = [k for k in ["quantity", "unit_price", "line_total"] if item[k]["value"] is None]
        item["checks"].append({"check": "qty_x_price", "result": "not_possible", "missing": missing})
        return
    expected = q * pr * (1 - d / 100)
    ok = abs(expected - tot) <= 0.02 + 0.001 * abs(tot)
    chk = {"check": "qty_x_price", "result": "ok" if ok else "failed",
           "expected": round(expected, 2), "found": tot}
    if d < 0 and ok:
        chk["note"] = f"הנחה שלילית: המחיר הועלה ב-{-d:g}%"
    item["checks"].append(chk)
    if not ok:
        for k in ["quantity", "unit_price", "line_total"]:
            if item[k]["status"] == "ok":
                item[k]["status"] = "check_failed"
                item[k]["reasons"].append(f"כמות × מחיר = {expected:,.2f} אבל בשורה כתוב {tot:,.2f}")


def _doc_checks(lines, totals):
    checks = []
    s = sum(l["line_total"]["value"] for l in lines if l["line_total"]["value"] is not None)
    target_key = "subtotal" if totals["subtotal"]["value"] is not None else "total"
    target = totals[target_key]["value"]
    if target is not None and lines:
        ok = abs(s - target) <= 0.05
        checks.append({"check": f"sum_lines_vs_{target_key}", "result": "ok" if ok else "failed",
                       "expected": round(s, 2), "found": target})
        if not ok:
            totals[target_key]["status"] = "check_failed"
            totals[target_key]["reasons"].append(f"סכום השורות {s:,.2f} שונה מהסכום במסמך")
    sub, vat, tot = (totals[k]["value"] for k in ["subtotal", "vat", "total"])
    if None not in (sub, vat, tot):
        ok = abs(sub + vat - tot) <= 0.05
        checks.append({"check": "subtotal_plus_vat", "result": "ok" if ok else "failed",
                       "expected": round(sub + vat, 2), "found": tot})
    return checks


def extract_document(doc_id: str, pages: list[dict]) -> dict:
    dtype, type_line, type_page = _doc_type(pages)
    supplier, tax_id = _parties(pages, dtype["value"])
    totals = _totals(pages)
    lines = _line_items(doc_id, pages)
    checks = _doc_checks(lines, totals)
    warnings = []
    if not any(p["tables"] for p in pages):
        warnings.append("לא נמצאה טבלת מוצרים")
    for p in pages:
        q = p.get("quality") or {}
        if "blurry" in q.get("warnings", []):
            warnings.append(f"עמוד {p['page']}: התמונה מטושטשת — מומלץ לצלם שוב")
        if "too_dark" in q.get("warnings", []):
            warnings.append(f"עמוד {p['page']}: התמונה חשוכה מדי — מומלץ לצלם שוב באור טוב")
    page_marks = []
    for p in pages:
        for l in p["lines"]:
            m = re.search(r"עמוד\s*(\d+)\s*מתוך\s*(\d+)|page\s*(\d+)\s*of\s*(\d+)", norm(l["text"]))
            if m:
                g = [x for x in m.groups() if x]
                page_marks.append((int(g[0]), int(g[1])))
    if page_marks:
        total_pages = max(t for _, t in page_marks)
        seen = {n for n, _ in page_marks}
        missing = sorted(set(range(1, total_pages + 1)) - seen)
        if missing:
            warnings.append(f"חסרים עמודים: {missing}. נא להעלות את העמודים החסרים")
    return {
        "schema_version": "0.1",
        "doc_id": doc_id,
        "model": pages[0].get("model") if pages else None,
        "pages": [{"page": p["page"], "method": p["method"], "method_reason": p["method_reason"],
                   "preprocessing": p["preprocessing"], "quality": p["quality"]} for p in pages],
        "header": {
            "supplier_name": supplier,
            "supplier_tax_id": tax_id,
            "supplier_number": _supplier_number(pages),
            "document_type": dtype,
            "document_number": _doc_number(type_line, type_page),
            "document_date": _doc_date(pages),
            "currency": _currency(pages),
            **totals,
        },
        "lines": lines,
        "checks": checks,
        "warnings": warnings,
        "review_status": "not_reviewed",
    }
