"""Small text helpers: Hebrew direction, invisible marks, number parsing."""
import re

from bidi import get_display

HEB = re.compile(r"[֐-׿]")
LAT = re.compile(r"[A-Za-z]")
# Direction marks that Tesseract and some PDFs insert. They are invisible and
# break string comparison, so we strip them from every text we store.
MARKS = re.compile(r"[‎‏‪-‮⁦-⁩]")
CID = re.compile(r"\(cid:\d+\)")
UNKNOWN = "�"  # placeholder for a PDF glyph with no known letter


def clean(s: str) -> str:
    return MARKS.sub("", s or "").strip()


def has_hebrew(s: str) -> bool:
    return bool(HEB.search(s))


def script_of(s: str) -> str:
    """'he', 'en', 'mixed' or 'other' - used later for per-language metrics."""
    h, l = bool(HEB.search(s)), bool(LAT.search(s))
    return "mixed" if h and l else "he" if h else "en" if l else "other"


def visual_to_logical(visual: str, base: str | None = None) -> str:
    """PDF text and our left-to-right word sorting give *visual* order
    (Hebrew letters appear reversed). Convert to the normal reading order.
    `base` is the paragraph direction: "R" for a Hebrew document, "L" for an
    English one. It matters for mixed text such as "כבל nikon hood": without
    it, a cell with more English than Hebrew letters is put in the wrong order."""
    if base is None:
        strong_he = len(HEB.findall(visual))
        strong_en = len(LAT.findall(visual))
        base = "R" if strong_he >= strong_en and strong_he else "L"
    return _fix_brackets(get_display(visual, base_dir=base))


def page_direction(texts) -> str:
    """'R' if the page is a Hebrew document (even with English product names)."""
    texts = list(texts)
    he = sum(len(HEB.findall(t)) for t in texts)
    en = sum(len(LAT.findall(t)) for t in texts)
    return "R" if he > 0.15 * (he + en) else "L"


_BRACKETS = [(re.compile(r"\)([^()]*)\("), r"(\1)"), (re.compile(r"\]([^\[\]]*)\["), r"[\1]")]


def _fix_brackets(s: str) -> str:
    """The reordering library flips brackets that sit in Hebrew text, giving
    ")1999(". Turn an inside-out pair back into "(1999)"."""
    for rx, rep in _BRACKETS:
        s = rx.sub(rep, s)
    return s


_NUM = re.compile(r"^[-+]?\d{1,3}(?:,\d{3})*(?:\.\d+)?[-]?$|^[-+]?\d+(?:\.\d+)?[-]?$")


def parse_number(raw: str):
    """'12,345.00' -> 12345.0, '10-' -> -10.0 (RTL documents often show the
    minus sign after the number). Returns None if it is not a clean number."""
    s = clean(raw).replace(" ", "").replace("₪", "").replace("$", "").replace("€", "")
    if not s or not _NUM.match(s):
        return None
    neg = s.startswith("-") or s.endswith("-")
    s = s.strip("-+").replace(",", "")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def _word_dir(text: str) -> str:
    return "R" if HEB.search(text) else "L" if LAT.search(text) else "N"


def order_line(words: list[dict], base: str) -> str:
    """Reading-order text of one line from words with boxes.

    Each word's own text is already in reading order. Only the word ORDER
    must be decided: Hebrew words run right-to-left, English words and codes
    left-to-right, and numbers/punctuation follow their neighbours. Working
    on whole words keeps a hyphen or bracket attached to its word ("a7-",
    "(ilc-e9m3b"), which character-level reordering got wrong."""
    ws = sorted(words, key=lambda w: w["bbox"][0])
    dirs = [_word_dir(w["text"]) for w in ws]
    strong = [(i, d) for i, d in enumerate(dirs) if d != "N"]
    other = "L" if base == "R" else "R"
    resolved = list(dirs)
    for i, d in enumerate(dirs):
        if d != "N":
            continue
        left = next((d2 for j, d2 in reversed(strong) if j < i), None)
        right = next((d2 for j, d2 in strong if j > i), None)
        if left == other and right == other:
            resolved[i] = other
        elif re.search(r"\d", ws[i]["text"]) and base == "R" and "L" in (left, right):
            # A number next to English words belongs to the product name
            # ("tiffen uv 58", "nikon d5300 #2178738").
            resolved[i] = "L"
        else:
            resolved[i] = base
    dirs = resolved
    runs: list[tuple[str, list]] = []
    for w, d in zip(ws, dirs):
        if runs and runs[-1][0] == d:
            runs[-1][1].append(w)
        else:
            runs.append((d, [w]))
    if base == "R":
        runs.reverse()
    out = []
    for d, rw in runs:
        for w in (reversed(rw) if d == "R" else rw):
            text = w["text"]
            if "_visual" in w and _word_dir(text) == "N":
                # Digits/punctuation only (",92"): its letter order depends on
                # the words around it, which we now know.
                text = visual_to_logical(w["_visual"], d)
            out.append(text)
    return " ".join(out)
