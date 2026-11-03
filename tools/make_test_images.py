"""Make SIMULATED test inputs from a clean PDF page, to exercise the OCR path
before we have real scans and phone photos. Results on these are not a
substitute for real photos.

    python tools/make_test_images.py page.png out_dir
"""
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def scan(page: np.ndarray) -> np.ndarray:
    """Grey, 200 dpi, slightly tilted, a bit of noise: like an office scanner."""
    g = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, None, fx=2 / 3, fy=2 / 3, interpolation=cv2.INTER_AREA)
    h, w = g.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), 1.2, 1.0)
    g = cv2.warpAffine(g, m, (w, h), borderValue=255)
    noise = np.random.default_rng(0).normal(0, 8, g.shape)
    return np.clip(g + noise, 0, 255).astype(np.uint8)


def photo(page: np.ndarray) -> np.ndarray:
    """Paper on a desk, taken at an angle, with a shadow and mild blur."""
    h, w = page.shape[:2]
    canvas = np.full((int(h * 1.25), int(w * 1.25), 3), (60, 85, 110), np.uint8)
    H, W = canvas.shape[:2]
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[W * 0.12, H * 0.08], [W * 0.90, H * 0.11], [W * 0.94, H * 0.93], [W * 0.07, H * 0.90]])
    m = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(page, m, (W, H))
    mask = cv2.warpPerspective(np.full((h, w), 255, np.uint8), m, (W, H))
    canvas[mask > 0] = warped[mask > 0]
    shade = np.tile(np.linspace(0.65, 1.0, W), (H, 1))[..., None]
    canvas = (canvas * shade).astype(np.uint8)
    canvas = cv2.GaussianBlur(canvas, (3, 3), 0)
    return cv2.resize(canvas, (W * 3 // 4, H * 3 // 4), interpolation=cv2.INTER_AREA)


def main(src, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    page = cv2.imread(src)
    s = scan(page)
    Image.fromarray(s).save(out / "sim_scan.pdf", resolution=200)
    cv2.imwrite(str(out / "sim_photo.jpg"), photo(page), [cv2.IMWRITE_JPEG_QUALITY, 85])
    blurry = cv2.GaussianBlur(photo(page), (15, 15), 0)
    cv2.imwrite(str(out / "sim_photo_blurry.jpg"), blurry, [cv2.IMWRITE_JPEG_QUALITY, 85])
    rot = cv2.rotate(photo(page), cv2.ROTATE_90_CLOCKWISE)
    cv2.imwrite(str(out / "sim_photo_rotated.jpg"), rot, [cv2.IMWRITE_JPEG_QUALITY, 85])


if __name__ == "__main__":
    main(*sys.argv[1:])
