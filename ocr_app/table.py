"""Find the product table on a page.

Reading the whole page at once mixed up table rows in our first test (codes
and quantities were lost). So we first find the table grid, then read each
cell on its own and keep track of which row and column it came from. That is
what keeps a quantity attached to the right product.

Two methods:
  grid       - the table has printed lines (like our first sample PDF)
  columns    - no lines: rows from text lines, columns from the header words
"""
import re

import cv2
import numpy as np
from PIL import Image

from .textutil import clean

# Header words -> column meaning. Hebrew and English, several spellings.
HEADER_KEYWORDS = {
    "line_no": ["מס'", "מס", "#", "no", "no.", "שורה"],
    "code": ["קוד", "מק\"ט", "מקט", "ברקוד", "code", "sku", "item", "part", "catalog", "קטלוגי"],
    "description": ["תאור", "תיאור", "פירוט", "שם", "description", "product", "פריט"],
    "quantity": ["כמות", "qty", "quantity", "כמ'"],
    "unit": ["יחידה", "יח'", "יח", "unit", "uom", "מידה"],
    "unit_price": ["מחיר", "price", "unit price", "מחיר ליח'"],
    "discount": ["הנחה", "%", "discount", "disc"],
    "line_total": ["סה\"כ", "סהכ", "total", "amount", "סכום"],
}


def classify_header(text: str) -> str | None:
    """Column meaning from header text. Also tries the text reversed, because
    OCR sometimes returns a Hebrew word in reversed letter order ("ריחמ")."""
    return _classify(text) or _classify(clean(text)[::-1])


def _classify(text: str) -> str | None:
    t = clean(text).lower().replace("״", '"').replace("׳", "'")
    if not t:
        return None
    if re.search(r"סה\W{0,3}י?\W{0,3}כ", t):  # סה"כ, also when OCR reads ״ as ''י
        return "line_total"
    # Order matters: "קוד פריט" is a code column even though it has "פריט".
    for key in ["code", "quantity", "unit_price", "discount", "line_total", "unit", "line_no", "description"]:
        for kw in HEADER_KEYWORDS[key]:
            if kw in t.split() or (len(kw) > 2 and kw in t):
                return key
    return None


def _positions(mask: np.ndarray, axis: int, min_len: int) -> list[int]:
    """Centre positions of long lines in a binary line mask."""
    proj = (mask > 0).sum(axis=axis)
    idx = np.where(proj >= min_len)[0]
    if len(idx) == 0:
        return []
    groups, start = [], idx[0]
    for a, b in zip(idx, idx[1:]):
        if b - a > 3:
            groups.append((start + a) // 2)
            start = b
    groups.append((start + idx[-1]) // 2)
    return [int(g) for g in groups]


def find_grid_tables(img: Image.Image) -> list[dict]:
    gray = np.array(img.convert("L"))
    bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 25, 15)
    h, w = bw.shape
    horiz = cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (w // 25, 1)))
    vert = cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, h // 60)))
    grid = cv2.dilate(cv2.bitwise_or(horiz, vert), np.ones((5, 5), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(grid)
    tables = []
    for i in range(1, n):
        x, y, bw_, bh, _ = stats[i]
        if bw_ < w * 0.3 or bh < h * 0.04:
            continue
        hm, vm = horiz[y:y + bh, x:x + bw_], vert[y:y + bh, x:x + bw_]
        rows = _positions(hm, axis=1, min_len=int(bw_ * 0.5))
        cols = _positions(vm, axis=0, min_len=int(bh * 0.5))
        if len(rows) < 3 or len(cols) < 3:
            continue
        rows = [int(r + y) for r in rows]
        cols = [int(c + x) for c in cols]
        cells = []
        for ri in range(len(rows) - 1):
            for ci in range(len(cols) - 1):
                cells.append({"row": ri, "col": ci,
                              "bbox": [cols[ci], rows[ri], cols[ci + 1], rows[ri + 1]]})
        tables.append({"method": "grid", "bbox": [int(x), int(y), int(x + bw_), int(y + bh)],
                       "row_lines": rows, "col_lines": cols, "cells": cells,
                       "n_rows": len(rows) - 1, "n_cols": len(cols) - 1,
                       "row_bboxes": [[cols[0], rows[r], cols[-1], rows[r + 1]] for r in range(len(rows) - 1)]})
    return sorted(tables, key=lambda t: (t["bbox"][2] - t["bbox"][0]) * (t["bbox"][3] - t["bbox"][1]), reverse=True)


def _is_number(t: str) -> bool:
    return bool(re.fullmatch(r"[-+]?[\d,]+(\.\d+)?-?%?", clean(t)))


def _span_dist(a, b):
    """Horizontal distance between two [x0, x1] spans (0 if they overlap)."""
    return max(0, max(a[0], b[0]) - min(a[1], b[1]))


def find_column_table(lines: list[dict], words: list[dict], join) -> dict | None:
    """Table WITHOUT printed lines.

    1. Header: a text line with 3+ column words (plus a header word that
       wraps onto the line just above or below, like "Unit / price").
    2. Columns: header words that sit close together form one label; each
       body word goes to the label it is horizontally closest to.
    3. Rows: a line with a number in a quantity/price/total column starts a
       product row ("anchor"). Lines without numbers are wrapped description
       text; they join the nearest anchor above or below.
    4. The table ends at the first totals line.
    Checked only on synthetic layouts so far."""
    by_id = {w["id"]: w for w in words}
    for li, line in enumerate(lines):
        ws = [by_id[i] for i in line["word_ids"]]
        kinds = {classify_header(w["text"]) for w in ws} - {None}
        if len(kinds) < 3 or any(_is_number(w["text"]) for w in ws if w["text"] not in "#"):
            continue
        h = sum(w["bbox"][3] - w["bbox"][1] for w in ws) / len(ws)
        head = list(ws)
        for nb in (li - 1, li + 1):  # wrapped header words
            if 0 <= nb < len(lines):
                other = lines[nb]
                gap = abs((other["bbox"][1] + other["bbox"][3]) - (line["bbox"][1] + line["bbox"][3])) / 2
                ows = [by_id[i] for i in other["word_ids"]]
                if gap < 1.8 * h and len(ows) <= 3 and not any(_is_number(w["text"]) for w in ows):
                    head += ows
        # Merge header words into labels.
        head.sort(key=lambda w: w["bbox"][0])
        labels: list[list[dict]] = []
        for w in head:
            if labels:
                span = [min(x["bbox"][0] for x in labels[-1]), max(x["bbox"][2] for x in labels[-1])]
                if _span_dist(span, [w["bbox"][0], w["bbox"][2]]) < 0.6 * h:
                    labels[-1].append(w)
                    continue
            labels.append([w])
        cols = []
        for lab in labels:
            text = join(lab)
            cols.append({"span": [min(x["bbox"][0] for x in lab), max(x["bbox"][2] for x in lab)],
                         "text": text, "kind": classify_header(text)})
        header_bottom = max(w["bbox"][3] for w in head)
        # Body lines until the totals block.
        body = []
        for nxt in lines[li + 1:]:
            if nxt["bbox"][1] < header_bottom - 2:
                continue
            t = clean(nxt["text"]).lower()
            if re.search(r"סה\W{0,3}י?\W{0,3}[כב]|subtotal|total|לתשלום|מע\W{0,2}מ", t) and len(nxt["word_ids"]) <= 6:
                break
            body.append(nxt)
        if not body:
            return None
        numeric_cols = {i for i, c in enumerate(cols) if c["kind"] in ("quantity", "unit_price", "line_total")}

        # Column regions: x-ranges separated by a vertical strip of white
        # space that no body word crosses. Each region belongs to the header
        # label it overlaps most. Body text that is wider than its header
        # label (long descriptions) stays in its own column this way.
        body_words = [by_id[i] for l in body for i in l["word_ids"]]
        spans = sorted([[w["bbox"][0], w["bbox"][2]] for w in body_words] + [c["span"] for c in cols])
        regions = [list(spans[0])]
        for a0, a1 in spans[1:]:
            if a0 - regions[-1][1] < 0.5 * h:
                regions[-1][1] = max(regions[-1][1], a1)
            else:
                regions.append([a0, a1])

        def overlap(a, b):
            return max(0, min(a[1], b[1]) - max(a[0], b[0]))

        region_col = {}
        for ri, reg in enumerate(regions):
            inside = [i for i, c in enumerate(cols) if overlap(reg, c["span"]) > 0]
            region_col[ri] = inside if inside else [min(range(len(cols)), key=lambda i: _span_dist(reg, cols[i]["span"]))]

        def col_of(w):
            span = [w["bbox"][0], w["bbox"][2]]
            ri = max(range(len(regions)), key=lambda r: overlap(regions[r], span))
            cands = region_col[ri]
            if len(cands) == 1:
                return cands[0]
            # Two header labels share one region: nearest label wins.
            return min(cands, key=lambda i: (_span_dist(cols[i]["span"], span),
                                             abs(sum(cols[i]["span"]) - sum(span))))

        placed = []  # (line, {col: [words]}, is_anchor)
        for l in body:
            per_col: dict = {}
            for wid in l["word_ids"]:
                w = by_id[wid]
                per_col.setdefault(col_of(w), []).append(w)
            anchor = any(c in numeric_cols and any(_is_number(w["text"]) for w in per_col[c]) for c in per_col)
            placed.append((l, per_col, anchor or not numeric_cols))
        anchors = [i for i, p in enumerate(placed) if p[2]]
        if not anchors:
            return None
        rows: dict = {a: [] for a in anchors}
        for i, p in enumerate(placed):
            if p[2]:
                rows[i].append(p)
                continue
            cy = (p[0]["bbox"][1] + p[0]["bbox"][3]) / 2
            near = min(anchors, key=lambda a: (abs(cy - (placed[a][0]["bbox"][1] + placed[a][0]["bbox"][3]) / 2), -a))
            rows[near].append(p)
        cells, row_bboxes = [], []
        for ri, a in enumerate(anchors, start=1):
            parts = sorted(rows[a], key=lambda p: p[0]["bbox"][1])
            y0 = min(p[0]["bbox"][1] for p in parts) - 4
            y1 = max(p[0]["bbox"][3] for p in parts) + 4
            row_bboxes.append([0, int(y0), 0, int(y1)])
            for ci in range(len(cols)):
                cw = [w for p in parts for w in p[1].get(ci, [])]
                bbox = ([min(w["bbox"][0] for w in cw), min(w["bbox"][1] for w in cw),
                         max(w["bbox"][2] for w in cw), max(w["bbox"][3] for w in cw)] if cw
                        else [int(cols[ci]["span"][0]), int(y0), int(cols[ci]["span"][1]), int(y1)])
                cells.append({"row": ri, "col": ci, "bbox": [int(v) for v in bbox],
                              "word_ids": [w["id"] for w in cw], "text": join(cw) if cw else "",
                              "conf": (round(sum(w["conf"] for w in cw) / len(cw), 1)
                                       if cw and all(w.get("conf") is not None for w in cw) else None),
                              "source": sorted({w["source"] for w in cw}) or ["empty"]})
        for ci, c in enumerate(cols):
            cells.append({"row": 0, "col": ci, "bbox": [int(c["span"][0]), int(line["bbox"][1]),
                                                       int(c["span"][1]), int(header_bottom)],
                          "text": c["text"], "conf": None, "source": ["header"], "word_ids": []})
        x0 = min(min(c["bbox"][0] for c in cells), line["bbox"][0])
        x1 = max(max(c["bbox"][2] for c in cells), line["bbox"][2])
        for rb in row_bboxes:
            rb[0], rb[2] = int(x0), int(x1)
        return {"method": "columns", "filled": True,
                "bbox": [int(x0), int(line["bbox"][1]), int(x1), row_bboxes[-1][3]],
                "row_bboxes": [[int(x0), int(line["bbox"][1]), int(x1), int(header_bottom)]] + row_bboxes,
                "cells": cells, "n_rows": len(anchors) + 1, "n_cols": len(cols),
                "column_kinds": {str(i): c["kind"] for i, c in enumerate(cols)}}
    return None


def words_in(bbox, words: list[dict]) -> list[dict]:
    x0, y0, x1, y1 = bbox
    out = []
    for w in words:
        cx, cy = (w["bbox"][0] + w["bbox"][2]) / 2, (w["bbox"][1] + w["bbox"][3]) / 2
        if x0 <= cx < x1 and y0 <= cy < y1:
            out.append(w)
    return out
