"""The employee's reviewed version of a document (review.json).

fields.json is what the system read; it is never edited.
review.json is what the employee confirmed. For every value we keep the
original reading next to the reviewed value, so we can always see what was
changed and by whom.

Values here are BUSINESS values (what should go into Bina). They are not
OCR training labels: an employee may write the catalog name of a product
even though the paper says something else. Exact-text corrections for
training live in training.py and are approved separately.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from .textutil import parse_number

SCHEMA_VERSION = "1.0"
HEADER_KEYS = ["supplier_name", "supplier_tax_id", "supplier_number", "document_type", "document_number",
               "document_date", "currency", "subtotal", "vat", "total"]
LINE_KEYS = ["line_no", "code", "description", "quantity", "unit", "unit_price", "discount", "line_total"]
NUMERIC = {"subtotal", "vat", "total", "line_no", "quantity", "unit_price", "discount", "line_total"}
STATUSES = ["not_reviewed", "draft", "confirmed"]


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _item(f):
    return {"value": f["value"], "original_value": f["value"], "original_raw": f["raw"],
            "read_status": f["status"], "reasons": f["reasons"], "source": f.get("source"),
            "changed": False, "written_on_document": None}


def init_review(doc_dir: Path) -> dict:
    fields = json.loads((doc_dir / "fields.json").read_text())
    return {
        "schema_version": SCHEMA_VERSION,
        "doc_id": fields["doc_id"],
        "status": "not_reviewed",
        "model": fields.get("model"),
        "warnings": fields.get("warnings", []),
        "checks": fields.get("checks", []),
        "header": {k: _item(fields["header"][k]) for k in HEADER_KEYS if k in fields["header"]},
        "lines": [{"line_id": l["line_id"], "page": l["page"], "row_bbox": l["row_bbox"],
                   "codes_in_description": l.get("codes_in_description", []),
                   "checks": l.get("checks", []),
                   "fields": {k: _item(l[k]) for k in LINE_KEYS if k in l},
                   "deleted": False, "added_by_reviewer": False} for l in fields["lines"]],
        "history": [],
        "created_at": now(),
    }


def load(doc_dir: Path) -> dict:
    p = doc_dir / "review.json"
    if p.exists():
        return json.loads(p.read_text())
    return init_review(doc_dir)


def save(doc_dir: Path, review: dict):
    review["updated_at"] = now()
    tmp = doc_dir / "review.json.tmp"
    tmp.write_text(json.dumps(review, ensure_ascii=False, indent=1))
    tmp.replace(doc_dir / "review.json")


def _coerce(key, value):
    if value in ("", None):
        return None
    if key in NUMERIC:
        v = parse_number(str(value))
        if v is None:
            raise ValueError(f"'{value}' אינו מספר")
        return v
    return str(value).strip()


def apply_changes(review: dict, changes: dict, user: str) -> list[str]:
    """changes = {"header": {key: value}, "lines": [{"line_id", "fields": {k: v},
    "deleted": bool}], "new_lines": [{k: v}], "written": {"<target>": bool}}.
    Returns a list of error messages (empty if all good)."""
    errors = []
    written = changes.get("written", {})

    def set_value(item, key, new, target):
        try:
            v = _coerce(key, new)
        except ValueError as e:
            errors.append(f"{target}: {e}")
            return
        if v != item["value"]:
            review["history"].append({"at": now(), "by": user, "target": target,
                                      "old": item["value"], "new": v})
            item["value"] = v
            item["changed"] = v != item["original_value"]
        if target in written:
            item["written_on_document"] = bool(written[target])

    for k, v in changes.get("header", {}).items():
        if k in review["header"]:
            set_value(review["header"][k], k, v, f"header.{k}")
    by_id = {l["line_id"]: l for l in review["lines"]}
    for lc in changes.get("lines", []):
        line = by_id.get(lc.get("line_id"))
        if not line:
            continue
        if lc.get("deleted") is not None and lc["deleted"] != line["deleted"]:
            line["deleted"] = bool(lc["deleted"])
            review["history"].append({"at": now(), "by": user, "target": f"{line['line_id']}.deleted",
                                      "old": not line["deleted"], "new": line["deleted"]})
        for k, v in lc.get("fields", {}).items():
            if k not in line["fields"]:
                line["fields"][k] = _item({"value": None, "raw": None, "status": "missing", "reasons": []})
            set_value(line["fields"][k], k, v, f"{line['line_id']}.{k}")
    for nl in changes.get("new_lines", []):
        n = sum(1 for l in review["lines"] if l["added_by_reviewer"]) + 1
        line = {"line_id": f"{review['doc_id']}-added-{n:02d}", "page": None, "row_bbox": None,
                "codes_in_description": [], "checks": [], "deleted": False, "added_by_reviewer": True,
                "fields": {k: _item({"value": None, "raw": None, "status": "missing", "reasons": []})
                           for k in LINE_KEYS}}
        for k, v in nl.items():
            if k in line["fields"]:
                set_value(line["fields"][k], k, v, f"{line['line_id']}.{k}")
        review["lines"].append(line)
        review["history"].append({"at": now(), "by": user, "target": line["line_id"], "old": None, "new": "added"})
    return errors


def check_reviewed(review: dict) -> list[str]:
    """Same math checks as extraction, on the reviewed values. Shown to the
    employee before confirming; they do not block saving."""
    msgs = []
    total = 0.0
    for l in review["lines"]:
        if l["deleted"]:
            continue
        f = {k: l["fields"].get(k, {}).get("value") for k in ["quantity", "unit_price", "discount", "line_total"]}
        if f["line_total"] is not None:
            total += f["line_total"]
        if None not in (f["quantity"], f["unit_price"], f["line_total"]):
            exp = f["quantity"] * f["unit_price"] * (1 - (f["discount"] or 0) / 100)
            if abs(exp - f["line_total"]) > 0.02 + 0.001 * abs(f["line_total"]):
                desc = l["fields"].get("description", {}).get("value") or l["line_id"]
                msgs.append(f"בשורה \"{desc}\": כמות × מחיר = {exp:,.2f}, אבל הסה\"כ הוא {f['line_total']:,.2f}")
    h = {k: review["header"].get(k, {}).get("value") for k in ["subtotal", "vat", "total"]}
    target = h["subtotal"] if h["subtotal"] is not None else h["total"]
    if target is not None and abs(total - target) > 0.05:
        msgs.append(f"סכום השורות {total:,.2f} שונה מהסכום במסמך {target:,.2f}")
    return msgs


def export_rows(review: dict) -> list[dict]:
    """Flat rows for Excel/CSV: one row per product line."""
    h = {k: v["value"] for k, v in review["header"].items()}
    rows = []
    for l in review["lines"]:
        if l["deleted"]:
            continue
        row = {"doc_id": review["doc_id"], "line_id": l["line_id"], "page": l["page"],
               "review_status": review["status"]}
        row.update({f"doc_{k}": h.get(k) for k in ["supplier_name", "document_type", "document_number",
                                                     "document_date", "currency", "total"]})
        for k in LINE_KEYS:
            f = l["fields"].get(k, {})
            row[k] = f.get("value")
            row[f"{k}_as_read"] = f.get("original_raw")
        row["model"] = (review.get("model") or {}).get("lang")
        rows.append(row)
    return rows
