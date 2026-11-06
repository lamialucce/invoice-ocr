"""Tesseract wrapper. Every OCR call in the project goes through here, so the
model version is recorded in one place.

Words come back in normal reading order (logical order), with a box in page
pixels and Tesseract's own confidence (0-100). The confidence is only a hint:
in our first test Tesseract read the Hebrew word "אביב" as "DAN" with 90.
"""
import os
import subprocess
from functools import lru_cache

import pytesseract
from PIL import Image

from .textutil import clean, script_of

# Tesseract's own multi-threading makes it much slower when several reads run
# at once or the machine has few cores (measured: 147 s vs 1.5 s for one page
# on a 4-core server). One thread per read is the setting Tesseract's
# documentation suggests for this case. Set OMP_THREAD_LIMIT to override.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

LANG = os.environ.get("OCR_LANG", "heb+eng")
# Folder with a fine-tuned .traineddata. Empty = the stock models.
TESSDATA = os.environ.get("OCR_TESSDATA", "")
MODEL_ID = os.environ.get("OCR_MODEL_ID", "stock")


def set_model(model_id: str, lang: str, tessdata: str = ""):
    """Switch the model used for all following OCR calls (see models.py)."""
    global LANG, TESSDATA, MODEL_ID
    MODEL_ID, LANG, TESSDATA = model_id, lang, tessdata


@lru_cache
def _engine_version() -> str:
    out = subprocess.run(["tesseract", "--version"], capture_output=True, text=True).stdout
    return out.split("\n")[0].split()[-1]


def model_version() -> dict:
    return {"engine": "tesseract", "engine_version": _engine_version(), "model_id": MODEL_ID,
            "lang": LANG, "tessdata": TESSDATA or "stock"}


def _config(psm: int, extra: str = "") -> str:
    cfg = f"--oem 1 --psm {psm} -c preserve_interword_spaces=1 {extra}"
    if TESSDATA:
        cfg += f' --tessdata-dir "{TESSDATA}"'
    return cfg


def ocr_words(img: Image.Image, psm: int = 3, lang: str | None = None,
              offset=(0, 0), extra: str = "") -> list[dict]:
    d = pytesseract.image_to_data(img, lang=lang or LANG, config=_config(psm, extra),
                                  output_type=pytesseract.Output.DICT)
    words = []
    for i, t in enumerate(d["text"]):
        t = clean(t)
        if not t:
            continue
        x, y = d["left"][i] + offset[0], d["top"][i] + offset[1]
        words.append({
            "text": t,
            "bbox": [x, y, x + d["width"][i], y + d["height"][i]],
            "conf": round(float(d["conf"][i]), 1),
            "line_key": (d["block_num"][i], d["par_num"][i], d["line_num"][i]),
            "source": "ocr",
            "script": script_of(t),
        })
    return words


def ocr_text(img: Image.Image, psm: int = 6, lang: str | None = None, extra: str = "") -> tuple[str, float | None]:
    """OCR a small crop (one cell or one word). Returns text and mean confidence."""
    words = ocr_words(img, psm=psm, lang=lang, extra=extra)
    if not words:
        return "", None
    lines: dict = {}
    for w in words:
        lines.setdefault(w["line_key"], []).append(w["text"])
    text = "\n".join(" ".join(v) for v in lines.values())
    return text, round(sum(w["conf"] for w in words) / len(words), 1)


def ocr_text_best(img: Image.Image, psm: int = 6) -> tuple[str, float | None, str]:
    """Tesseract treats the first language as the main one. "heb+eng" read
    an English product name ("abcbot") as digits ("008004"); "eng+heb" gets it right but is worse on Hebrew. So for
    mixed cells we run both orders and keep the more confident reading."""
    best = ("", None, LANG)
    for lang in _lang_orders():
        text, conf = ocr_text(img, psm=psm, lang=lang)
        if conf is not None and (best[1] is None or conf > best[1] + 0.5):
            best = (text, conf, lang)
    return best


def _lang_orders() -> list[str]:
    parts = LANG.split("+")
    return [LANG] if len(parts) < 2 else [LANG, "+".join(parts[1:] + parts[:1])]


def group_lines(words: list[dict]) -> list[dict]:
    """Group OCR words into lines using Tesseract's own line numbers."""
    lines: dict = {}
    for w in words:
        lines.setdefault(w["line_key"], []).append(w)
    out = []
    for ws in lines.values():
        out.append({
            "text": " ".join(w["text"] for w in ws),
            "bbox": [min(w["bbox"][0] for w in ws), min(w["bbox"][1] for w in ws),
                     max(w["bbox"][2] for w in ws), max(w["bbox"][3] for w in ws)],
            "word_ids": [w["id"] for w in ws],
        })
    out.sort(key=lambda l: (l["bbox"][1], -l["bbox"][2]))
    return out
