"""Read the text that is already stored inside a PDF ("embedded text").

This is faster and usually more exact than OCR, but not always correct: in
our first sample PDF the font has no mapping for the letter נ, so the PDF says
"הזמ?ת". Such letters appear as (cid:NNN). We detect them and repair them
with OCR of just that word, and we record that we did.
"""
import re

import pdfplumber
from PIL import Image

from . import ocr
from .textutil import CID, UNKNOWN, clean, order_line, page_direction, script_of, visual_to_logical

MIN_WORDS = 10           # fewer words than this: treat the page as a scan
MAX_UNKNOWN_RATIO = 0.2  # more broken letters than this: OCR the whole page


def extract(pdf_path, page_no: int, scale: float) -> list[dict]:
    """Words of one page, in page-image pixels, text in visual order."""
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_no - 1]
        raw = page.extract_words(keep_blank_chars=False, use_text_flow=False,
                                 extra_attrs=["fontname", "upright"])
    words = []
    for w in raw:
        if not w.get("upright", True):
            continue  # sideways text in the margin (e.g. "printed by ..."); not needed
        cids = [int(m) for m in re.findall(r"\(cid:(\d+)\)", w["text"])]
        visual = CID.sub(UNKNOWN, w["text"])
        words.append({
            "visual": visual,
            "bbox": [w["x0"] * scale, w["top"] * scale, w["x1"] * scale, w["bottom"] * scale],
            "font": w.get("fontname", ""),
            "cids": cids,
        })
    return words


def page_is_usable(words: list[dict]) -> tuple[bool, str]:
    if len(words) < MIN_WORDS:
        return False, f"only {len(words)} words of embedded text"
    chars = sum(len(w["visual"]) for w in words)
    unknown = sum(w["visual"].count(UNKNOWN) for w in words)
    if chars and unknown / chars > MAX_UNKNOWN_RATIO:
        return False, f"{unknown}/{chars} letters have no text mapping"
    return True, f"{len(words)} words, {unknown} unmapped letters"


def _crop(img: Image.Image, bbox, pad=8):
    x0, y0, x1, y1 = bbox
    return img.crop((max(0, x0 - pad), max(0, y0 - pad), min(img.width, x1 + pad), min(img.height, y1 + pad)))


def repair_unknown(words: list[dict], page_img: Image.Image) -> dict:
    """Learn which letter each broken glyph stands for, by OCR of the words
    that contain it. A glyph mapping is accepted only if the OCR text matches
    the rest of the word exactly. Returns {(font, cid): letter}."""
    learned: dict = {}
    for w in words:
        if not w["cids"]:
            continue
        logical = visual_to_logical(w["visual"])
        if len(set(w["cids"])) != 1:
            continue  # keep it simple: learn only from words with one unknown glyph kind
        key = (w["font"], w["cids"][0])
        if key in learned:
            continue
        text, _ = ocr.ocr_text(_crop(page_img, w["bbox"]), psm=7)
        pattern = re.escape(logical).replace(re.escape(UNKNOWN), "(.)")
        m = re.fullmatch(pattern, clean(text).replace(" ", ""))
        if m and len(set(m.groups())) == 1:
            learned[key] = m.group(1)
    return learned


def group_lines(words: list[dict]) -> list[dict]:
    """Words whose vertical centres are close form one line."""
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["bbox"][1] + w["bbox"][3]) / 2):
        cy = (w["bbox"][1] + w["bbox"][3]) / 2
        h = w["bbox"][3] - w["bbox"][1]
        if rows:
            last = rows[-1]
            lcy = sum((x["bbox"][1] + x["bbox"][3]) / 2 for x in last) / len(last)
            if abs(cy - lcy) < 0.5 * h:
                last.append(w)
                continue
        rows.append([w])
    out = []
    for ws in rows:
        ws.sort(key=lambda w: w["bbox"][0])
        out.append({"text": order_line(ws, ws[0].get("_base") or "R"),
                    "bbox": [min(w["bbox"][0] for w in ws), min(w["bbox"][1] for w in ws),
                             max(w["bbox"][2] for w in ws), max(w["bbox"][3] for w in ws)],
                    "word_ids": [w["id"] for w in ws]})
    return out


def finalize(words: list[dict], page_img: Image.Image) -> tuple[list[dict], dict]:
    learned = repair_unknown(words, page_img)
    base = page_direction(w["visual"] for w in words)
    out = []
    for w in words:
        visual = w["visual"]
        source = "pdf_text"
        conf = None
        note = None
        if w["cids"]:
            fills = iter(w["cids"])
            visual = "".join(learned.get((w["font"], next(fills)), UNKNOWN) if ch == UNKNOWN else ch
                             for ch in visual)
            if UNKNOWN in visual:
                # Could not learn the letter: fall back to OCR for this word.
                text, conf = ocr.ocr_text(_crop(page_img, w["bbox"]), psm=7)
                source, note = "ocr_fallback", "embedded text had unknown letters"
                out.append(_word(clean(text) or visual_to_logical(visual), w, source, conf, note, visual, base))
                continue
            source, note = "pdf_text+repaired", "unknown letters filled from OCR of other words"
        out.append(_word(visual_to_logical(visual), w, source, conf, note, visual, base))
    return out, {f"{k[0]}:cid{k[1]}": v for k, v in learned.items()}


def _word(text, w, source, conf, note, visual, base):
    # "_visual"/"_base" are only used to build lines in the right order; not saved.
    d = {"text": text, "bbox": [round(v) for v in w["bbox"]], "conf": conf,
         "source": source, "script": script_of(text), "_visual": visual, "_base": base}
    if note:
        d["note"] = note
    return d
