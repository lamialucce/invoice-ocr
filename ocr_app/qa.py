"""Lightweight QA metrics for document OCR results.

This module turns the report's partial QA plan into a concrete, reusable
comparison layer: per-field accuracy, completeness, silent-error detection,
and a simple pass/fail outcome for an individual document.
"""
from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import Any

HEADER_FIELDS = [
    "supplier_name",
    "supplier_tax_id",
    "supplier_number",
    "document_type",
    "document_number",
    "document_date",
    "currency",
    "subtotal",
    "vat",
    "total",
]
LINE_FIELDS = [
    "line_no",
    "code",
    "description",
    "quantity",
    "unit",
    "unit_price",
    "discount",
    "line_total",
]


def _normalize(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return " ".join(value.split()).strip()
    if isinstance(value, (int, float)):
        return value
    return str(value).strip()


def _coerce_numeric(value: Any):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().replace(",", "")
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _same_value(reference: Any, candidate: Any) -> bool:
    if reference is None and candidate is None:
        return True
    if reference is None or candidate is None:
        return False
    ref_num = _coerce_numeric(reference)
    cand_num = _coerce_numeric(candidate)
    if ref_num is not None and cand_num is not None:
        return abs(ref_num - cand_num) <= 0.01
    return _normalize(reference) == _normalize(candidate)


def _status(item: Any) -> str:
    if isinstance(item, dict):
        return (item.get("status") or "ok").lower()
    return "ok"


def _value(item: Any) -> Any:
    if isinstance(item, dict):
        if "value" in item:
            return item.get("value")
        return None
    return item


def _compare_item(label: str, reference: Any, candidate: Any, *, context: dict | None = None) -> dict:
    context = context or {}
    candidate_value = _value(candidate)
    reference_value = _value(reference)
    is_correct = _same_value(reference_value, candidate_value)
    is_present = candidate_value is not None
    is_silent = False
    if not is_correct and is_present and _status(candidate) == "ok":
        is_silent = True
    return {
        "label": label,
        "reference": reference_value,
        "candidate": candidate_value,
        "correct": is_correct,
        "present": is_present,
        "silent_error": is_silent,
        "status": _status(candidate),
    }


def summarize_document(reference: dict, candidate: dict) -> dict:
    """Return a compact QA summary for one document.

    The document is considered "ok" only when all compared fields are correct and
    there are no silent mismatches. This is the operational equivalent of the
    report's field-accuracy / completeness / silent-error checks.
    """
    rows: list[dict] = []
    total = 0
    correct = 0
    complete = 0
    silent = 0

    def add_pair(label: str, ref_field: Any, cand_field: Any):
        nonlocal total, correct, complete, silent
        total += 1
        result = _compare_item(label, ref_field, cand_field)
        rows.append(result)
        if result["correct"]:
            correct += 1
        if result["present"]:
            complete += 1
        if result["silent_error"]:
            silent += 1

    header_ref = (reference or {}).get("header", {})
    header_cand = (candidate or {}).get("header", {})
    for key in HEADER_FIELDS:
        if key in header_ref or key in header_cand:
            add_pair(f"header.{key}", header_ref.get(key), header_cand.get(key))

    max_lines = max(len((reference or {}).get("lines", [])), len((candidate or {}).get("lines", [])))
    for i in range(max_lines):
        ref_line = (reference or {}).get("lines", [])[i] if i < len((reference or {}).get("lines", [])) else {}
        cand_line = (candidate or {}).get("lines", [])[i] if i < len((candidate or {}).get("lines", [])) else {}
        ref_fields = (ref_line or {}).get("fields", {})
        cand_fields = (cand_line or {}).get("fields", {})
        for key in LINE_FIELDS:
            if key in ref_fields or key in cand_fields:
                add_pair(f"line.{i + 1}.{key}", ref_fields.get(key), cand_fields.get(key))

    field_accuracy = (correct / total) if total else 1.0
    field_completeness = (complete / total) if total else 1.0
    document_ok = total > 0 and field_accuracy >= 0.99 and silent == 0

    return {
        "field_accuracy": round(field_accuracy, 4),
        "field_completeness": round(field_completeness, 4),
        "silent_errors": silent,
        "fields_total": total,
        "fields_correct": correct,
        "fields_complete": complete,
        "document_ok": document_ok,
        "rows": rows,
    }


def summarize_directory(reference_dir: str | Path, candidate_dir: str | Path) -> dict:
    """Summarize a whole directory of reference and candidate documents."""
    ref = Path(reference_dir)
    cand = Path(candidate_dir)
    docs = []
    names = sorted({p.name for p in ref.glob("*.json")} | {p.name for p in cand.glob("*.json")})
    for name in names:
        ref_path = ref / name
        cand_path = cand / name
        if not ref_path.exists() or not cand_path.exists():
            continue
        try:
            reference = json.loads(ref_path.read_text(encoding="utf-8"))
            candidate = json.loads(cand_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        docs.append({"doc_id": name, "summary": summarize_document(reference, candidate)})

    if not docs:
        return {"documents": [], "overall_field_accuracy": None, "overall_field_completeness": None,
                "overall_silent_errors": 0, "document_pass_count": 0, "document_total": 0}

    overall_accuracy = mean(item["summary"]["field_accuracy"] for item in docs)
    overall_completeness = mean(item["summary"]["field_completeness"] for item in docs)
    overall_silent = sum(item["summary"]["silent_errors"] for item in docs)
    document_pass = sum(1 for item in docs if item["summary"]["document_ok"])
    summary = {
        "documents": docs,
        "overall_field_accuracy": round(overall_accuracy, 4),
        "overall_field_completeness": round(overall_completeness, 4),
        "overall_silent_errors": overall_silent,
        "document_pass_count": document_pass,
        "document_total": len(docs),
    }
    return summary


def render_markdown(summary: dict) -> str:
    rows = [
        "# OCR QA report",
        "",
        f"- Overall field accuracy: {summary.get('overall_field_accuracy')}",
        f"- Overall field completeness: {summary.get('overall_field_completeness')}",
        f"- Silent errors: {summary.get('overall_silent_errors')}",
        f"- Passing documents: {summary.get('document_pass_count')}/{summary.get('document_total')}",
        "",
        "| document | field_accuracy | field_completeness | silent_errors | ok |",
        "|---|---:|---:|---:|---:|",
    ]
    for doc in summary.get("documents", []):
        s = doc["summary"]
        rows.append(
            f"| {doc['doc_id']} | {s['field_accuracy']:.4f} | {s['field_completeness']:.4f} | {s['silent_errors']} | {'yes' if s['document_ok'] else 'no'} |"
        )
    return "\n".join(rows) + "\n"
