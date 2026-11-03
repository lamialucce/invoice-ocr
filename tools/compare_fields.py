"""Compare the extracted fields of one document against a reference
(fields.json of a trusted reading, or later a reviewed result).

    python tools/compare_fields.py reference/fields.json candidate/fields.json
"""
import json
import sys

LINE_KEYS = ["code", "description", "quantity", "unit_price", "discount", "line_total"]
HEAD_KEYS = ["document_type", "document_number", "document_date", "currency", "subtotal", "vat", "total"]


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def compare(ref, cand):
    rows, right, total = [], 0, 0
    for k in HEAD_KEYS:
        a, b = ref["header"][k]["value"], cand["header"][k]["value"]
        total += 1
        right += a == b
        rows.append(("header", k, a, b, a == b))
    chars = errs = 0
    for i, rl in enumerate(ref["lines"]):
        cl = cand["lines"][i] if i < len(cand["lines"]) else None
        for k in LINE_KEYS:
            a = rl[k]["value"]
            b = cl[k]["value"] if cl else None
            total += 1
            right += a == b
            rows.append((f"line {i + 1}", k, a, b, a == b))
            if k == "description" and a:
                chars += len(a)
                errs += edit_distance(a, b or "")
    return {"fields_correct": right, "fields_total": total,
            "description_char_error_rate": round(errs / chars, 4) if chars else None,
            "lines_ref": len(ref["lines"]), "lines_found": len(cand["lines"]), "rows": rows}


if __name__ == "__main__":
    r = compare(json.load(open(sys.argv[1])), json.load(open(sys.argv[2])))
    for row in r.pop("rows"):
        if not row[4]:
            print("WRONG", row[:4])
    print(r)
