"""Run one document through the whole baseline:

    original file -> page images -> (clean-up) -> text (PDF text or OCR)
                  -> raw result per page (raw/page-NNN.json + .txt)
                  -> structured fields (fields.json)

The raw result and the structured fields are separate files on purpose, so a
mistake can be traced to the reading step or to the field extraction step.
"""
import json
import shutil
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import extract, ocr, pages, preprocess, table, textlayer

NUMERIC = {"quantity", "unit_price", "discount", "line_total", "line_no"}


def _clean_cell(img: Image.Image, bbox) -> Image.Image | None:
    """Crop a cell, erase the printed table lines inside it, and return None if
    nothing is left (an empty cell). Without this, Tesseract "reads" the
    border lines of an empty cell as random letters."""
    x0, y0, x1, y1 = bbox
    pad = 2
    crop = np.array(img.convert("L").crop((x0 + pad, y0 + pad, x1 - pad, y1 - pad)))
    if crop.shape[0] < 8 or crop.shape[1] < 8:
        return None
    bw = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    h, w = bw.shape
    # Vertical: only lines through almost the whole height (a border), so tall
    # letters like "ל" or "1" are never taken for a line.
    lines = cv2.bitwise_or(
        cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(10, int(w * 0.6)), 1))),
        cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(10, int(h * 0.85))))))
    lines = cv2.dilate(lines, np.ones((3, 3), np.uint8))
    crop[lines > 0] = 255
    # Ink = clearly darker than the paper. A fixed contrast margin instead of an
    # automatic threshold, so faint shading in an empty cell is not "text".
    paper = float(np.median(crop))
    ink = ((crop < paper - 60) * 255).astype(np.uint8)
    crop[crop > paper - 35] = 255  # flatten the background
    # Remove specks (scanner noise) and slivers of the border at the cell edge.
    n, lab, st, _ = cv2.connectedComponentsWithStats(ink)
    for i in range(1, n):
        x, y, cw, ch, area = st[i]
        speck = area < 10
        edge_sliver = (x <= 1 or x + cw >= w - 1) and cw <= 4 or (y <= 1 or y + ch >= h - 1) and ch <= 4
        if speck or edge_sliver:
            crop[lab == i] = 255
            ink[lab == i] = 0
    if (ink > 0).mean() < 0.004:
        return None
    return Image.fromarray(cv2.copyMakeBorder(crop, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255))


def _ocr_cell(img: Image.Image, bbox, kind: str | None) -> tuple[str, float | None, str | None]:
    crop = _clean_cell(img, bbox)
    if crop is None:
        return "", None, None
    if kind in NUMERIC:
        text, conf = ocr.ocr_text(crop, psm=7, lang="eng",
                                  extra="-c tessedit_char_whitelist=0123456789.,-%")
        return text, conf, "eng(digits)"
    return ocr.ocr_text_best(crop, psm=6)


def _cell_text_from_words(cell_words: list[dict], layer_lines) -> str:
    if not cell_words:
        return ""
    return "\n".join(l["text"] for l in layer_lines(cell_words))


def _line_grouper(method):
    return textlayer.group_lines if method != "ocr" else ocr.group_lines


def _join_words(method):
    group = _line_grouper(method)

    def join(ws):
        for w in ws:
            w.setdefault("_visual", w["text"])
        return "\n".join(l["text"] for l in group(ws))
    return join


def read_table(t: dict, img: Image.Image, words: list[dict], method: str) -> dict:
    """Fill each cell with text. Header row decides what each column means."""
    if t.get("filled"):
        return t
    for w in words:
        w.setdefault("_visual", w["text"])
    layer_lines = _line_grouper(method)
    ncols = t["n_cols"]
    col_kind = dict(t.get("header_columns", {}))
    for cell in t["cells"]:
        cw = table.words_in(cell["bbox"], words)
        cell["word_ids"] = [w["id"] for w in cw]
        if method == "ocr":
            kind = col_kind.get(cell["col"]) if cell["row"] > 0 else None
            text, conf, lang = _ocr_cell(img, cell["bbox"], kind)
            cell.update(text=text, conf=conf, source="ocr_cell", lang=lang)
        else:
            cell.update(text=_cell_text_from_words(cw, layer_lines), conf=None,
                        source=sorted({w["source"] for w in cw}) or ["empty"])
        if cell["row"] == 0 and cell["col"] not in col_kind:
            k = table.classify_header(cell["text"])
            if k:
                col_kind[cell["col"]] = k
    t["column_kinds"] = {str(c): col_kind.get(c) for c in range(ncols)}
    return t


def process_page(doc_dir: Path, info: dict) -> dict:
    is_pdf = info.get("kind", "pdf") == "pdf"
    is_photo = not is_pdf
    page_no = info["page"]
    img = Image.open(doc_dir / info["image"]).convert("RGB")
    result = {"page": page_no, "page_image": info["image"], "preprocessing": [], "quality": None}
    method, reason = "ocr", "image file"
    words: list[dict] = []
    if is_pdf:
        layer = textlayer.extract(doc_dir / "original" / info["source_file"], info["source_page"], info["dpi"] / 72)
        ok, reason = textlayer.page_is_usable(layer)
        if ok:
            words, learned = textlayer.finalize(layer, img)
            method = "pdf_text"
            result["repaired_letters"] = learned
        else:
            reason = "PDF has no usable text: " + reason
    work_img = img
    if method == "ocr":
        work_img, steps, q = preprocess.preprocess(img, is_photo=is_photo)
        result["quality"] = q
        if steps:
            p = doc_dir / "processed" / f"page-{page_no:03d}.png"
            p.parent.mkdir(exist_ok=True)
            work_img.save(p)
            result["processed_image"] = str(p.relative_to(doc_dir))
            result["preprocessing"] = steps
        words = ocr.ocr_words(work_img, psm=3)
        latin = sum(w["script"] == "en" for w in words)
        hebrew = sum(w["script"] == "he" for w in words)
        if latin > hebrew and len(ocr._lang_orders()) > 1:
            # Mostly English page: read again with English as the main language.
            words = ocr.ocr_words(work_img, psm=3, lang=ocr._lang_orders()[1])
            result["page_lang"] = ocr._lang_orders()[1]
    for i, w in enumerate(words):
        w["id"] = f"p{page_no}w{i:04d}"
    lines = ocr.group_lines(words) if method == "ocr" else textlayer.group_lines(words)
    tables = table.find_grid_tables(work_img)
    if not tables:
        ct = table.find_column_table(lines, words, _join_words(method))
        tables = [ct] if ct else []
    tables = [read_table(t, work_img, words, method) for t in tables[:1]]
    for w in words:
        w.pop("_visual", None)
        w.pop("_base", None)
        w.pop("line_key", None)
    result.update(method=method, method_reason=reason,
                  coordinates_refer_to=result.get("processed_image", info["image"]),
                  words=words, lines=lines, tables=tables)
    return result


def process_dir(doc_dir: Path, progress=None) -> dict:
    """Read every page of a stored document and write raw/ and fields.json.
    `progress(message, done, total)` is called for the status screen."""
    t0 = time.time()
    doc_dir = Path(doc_dir)
    for old in ("raw", "processed"):
        if (doc_dir / old).exists():
            shutil.rmtree(doc_dir / old)
    infos = pages.render_pages(doc_dir)
    raw_dir = doc_dir / "raw"
    raw_dir.mkdir(exist_ok=True)
    results = []
    for info in infos:
        if progress:
            progress(f"קורא עמוד {info['page']} מתוך {len(infos)}", info["page"] - 1, len(infos))
        r = process_page(doc_dir, info)
        r["model"] = ocr.model_version()
        r["source_file"], r["source_page"] = info["source_file"], info["source_page"]
        (raw_dir / f"page-{info['page']:03d}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1))
        (raw_dir / f"page-{info['page']:03d}.txt").write_text("\n".join(l["text"] for l in r["lines"]))
        results.append(r)
    fields = extract.extract_document(doc_dir.name, results)
    product_rows = extract.extract_product_rows(fields)
    fields["processing_seconds"] = round(time.time() - t0, 1)
    (doc_dir / "fields.json").write_text(json.dumps(fields, ensure_ascii=False, indent=1))
    (doc_dir / "products.json").write_text(json.dumps(product_rows, ensure_ascii=False, indent=1))
    return fields


def process(src: Path, data_dir: Path, reprocess: bool = False) -> Path:
    """Command-line entry: one file -> one document (id = file content hash)."""
    doc_id, doc_dir, duplicate = pages.store_original(Path(src), Path(data_dir))
    if duplicate and not reprocess and (doc_dir / "fields.json").exists():
        return doc_dir
    fields = process_dir(doc_dir)
    fields["duplicate_upload"] = duplicate
    (doc_dir / "fields.json").write_text(json.dumps(fields, ensure_ascii=False, indent=1))
    return doc_dir
