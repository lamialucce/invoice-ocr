"""Image clean-up for scans and phone photos, plus a simple quality check.

Nothing here edits the original. The caller saves the result in processed/
and records which steps were applied, so a reviewer can compare.
"""
import re

import cv2
import numpy as np
import pytesseract
from PIL import Image

MIN_TEXT_WIDTH = 1800  # below this, small invoice text is too few pixels to read


def to_cv(img: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def to_pil(arr: np.ndarray) -> Image.Image:
    if arr.ndim == 2:
        return Image.fromarray(arr)
    return Image.fromarray(cv2.cvtColor(arr, cv2.COLOR_BGR2RGB))


def quality(arr: np.ndarray) -> dict:
    """Numbers that tell us if a photo is usable. Sharpness is the variance of
    the Laplacian: low means few sharp edges, i.e. blur. Thresholds are first
    guesses and must be tuned on real phone photos."""
    gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY) if arr.ndim == 3 else arr
    scale = 1500 / max(gray.shape)
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    sharp = float(cv2.Laplacian(small, cv2.CV_64F).var())
    bright = float(small.mean())
    warnings = []
    if sharp < 60:
        warnings.append("blurry")
    if bright < 70:
        warnings.append("too_dark")
    if max(gray.shape) < 1000:
        warnings.append("low_resolution")
    return {"sharpness": round(sharp, 1), "brightness": round(bright, 1), "warnings": warnings}


def detect_scan_like(arr: np.ndarray) -> bool:
    """Heuristic for a scanned page: white paper, dark text, crisp lines, low background variation.

    This is a small extra stage before OCR, so a scan-like input can be recognized as a scan even if
    it is passed in as an image file and not a PDF with embedded text.
    """
    gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY) if arr.ndim == 3 else arr
    if gray.size == 0:
        return False
    dark = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    dark_ratio = float(np.mean(dark > 0))
    bright = float(gray.mean())
    sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    # A scanned page is usually mostly white paper with crisp black text on it.
    # A camera photo often has harsher background variation, less uniform white,
    # or darker shadows around the sheet.
    return 0.02 <= dark_ratio <= 0.28 and bright > 120 and sharp > 80


def enhance_for_ocr(gray: np.ndarray) -> np.ndarray:
    """Restore lower-quality scanned or photographed pages before OCR.

    On poor-quality inputs the OCR engine is much more forgiving when contrast is
    increased, details are sharpened, and the background is normalized.
    """
    img = gray.astype(np.float32)
    img = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX)
    img = cv2.medianBlur(img.astype(np.uint8), 3)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    img = clahe.apply(img.astype(np.uint8))
    kernel = np.array([[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]], dtype=np.float32)
    img = cv2.filter2D(img, -1, kernel)
    return img


def detect_rotation(arr: np.ndarray) -> int:
    """Tesseract's orientation detector: returns 0/90/180/270."""
    try:
        osd = pytesseract.image_to_osd(to_pil(arr), config="--psm 0 -c min_characters_to_try=30")
    except pytesseract.TesseractError:
        return 0  # not enough text to decide; leave as is
    m = re.search(r"Rotate: (\d+)", osd)
    conf = re.search(r"Orientation confidence: ([\d.]+)", osd)
    if m and conf and float(conf.group(1)) > 2.0:
        return int(m.group(1))
    return 0


_ROT = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}


def rotate(arr: np.ndarray, deg: int) -> np.ndarray:
    return cv2.rotate(arr, _ROT[deg]) if deg in _ROT else arr


def find_paper(arr: np.ndarray):
    """Find the sheet of paper in a photo (largest 4-corner shape)."""
    h, w = arr.shape[:2]
    scale = 1000 / max(h, w)
    small = cv2.resize(arr, None, fx=scale, fy=scale)
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    edges = cv2.dilate(cv2.Canny(gray, 50, 150), np.ones((3, 3), np.uint8))
    cnts, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:5]:
        approx = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
        area = cv2.contourArea(approx) / (small.shape[0] * small.shape[1])
        if len(approx) == 4 and 0.25 < area < 0.98:
            return approx.reshape(4, 2).astype(np.float32) / scale
    return None


def warp_paper(arr: np.ndarray, quad: np.ndarray) -> np.ndarray:
    s, d = quad.sum(1), np.diff(quad, axis=1).ravel()
    tl, br, tr, bl = quad[s.argmin()], quad[s.argmax()], quad[d.argmin()], quad[d.argmax()]
    width = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
    height = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
    m = cv2.getPerspectiveTransform(np.float32([tl, tr, br, bl]),
                                    np.float32([[0, 0], [width, 0], [width, height], [0, height]]))
    return cv2.warpPerspective(arr, m, (width, height), flags=cv2.INTER_CUBIC)


def skew_angle(gray: np.ndarray) -> float:
    """Small tilt of text lines, from the long horizontal lines/text rows."""
    bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    lines = cv2.HoughLinesP(bw, 1, np.pi / 1800, threshold=200,
                            minLineLength=gray.shape[1] // 4, maxLineGap=20)
    if lines is None:
        return 0.0
    angles = [np.degrees(np.arctan2(y2 - y1, x2 - x1)) for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4)]
    angles = [a for a in angles if abs(a) < 10]
    return float(np.median(angles)) if angles else 0.0


def deskew(arr: np.ndarray, angle: float) -> np.ndarray:
    h, w = arr.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(arr, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def preprocess(img: Image.Image, is_photo: bool) -> tuple[Image.Image, list[str], dict]:
    """Returns (processed image, list of steps applied, quality info)."""
    arr = to_cv(img)
    steps = []
    q = quality(arr)
    q["scan_like"] = detect_scan_like(arr)
    q["input_kind"] = "scan_like" if q["scan_like"] else "photo_or_unknown"
    if q["scan_like"]:
        steps.append("scan_like_detected")
    if is_photo:
        quad = find_paper(arr)
        if quad is not None:
            arr = warp_paper(arr, quad)
            steps.append("cropped_to_paper")
    rot = detect_rotation(arr)
    if rot:
        arr = rotate(arr, rot)
        steps.append(f"rotated_{rot}")
    gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
    ang = skew_angle(gray)
    if abs(ang) > 0.3:
        gray = deskew(gray, ang)
        steps.append(f"deskewed_{ang:.1f}deg")
    if gray.shape[1] < MIN_TEXT_WIDTH:
        f = MIN_TEXT_WIDTH / gray.shape[1]
        gray = cv2.resize(gray, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
        steps.append(f"upscaled_x{f:.2f}")
    if is_photo:
        # Even out shadows: divide by a blurred background estimate.
        bg = cv2.medianBlur(cv2.dilate(gray, np.ones((7, 7), np.uint8)), 31)
        gray = cv2.normalize(255 - cv2.absdiff(gray, bg), None, 0, 255, cv2.NORM_MINMAX)
        steps.append("shadow_removed")
    if q.get("scan_like") or any(w in q.get("warnings", []) for w in ("blurry", "too_dark", "low_resolution")):
        gray = enhance_for_ocr(gray)
        steps.append("ocr_enhanced")
        if gray.shape[1] < MIN_TEXT_WIDTH * 1.5:
            scale = (MIN_TEXT_WIDTH * 1.5) / gray.shape[1]
            gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            steps.append(f"upscaled_ocr_x{scale:.2f}")
    if steps:
        steps.insert(0, "grayscale")
    return to_pil(gray), steps, q
