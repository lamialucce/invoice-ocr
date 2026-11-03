"""Turn an uploaded file into page images, and keep the original untouched.

Every document gets a folder:
    data/documents/<doc_id>/original/<file>   exact copy of the upload (read-only)
    data/documents/<doc_id>/pages/page-001.png  page as rendered / as photographed
    data/documents/<doc_id>/processed/page-001.png  only if we changed the image
"""
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageOps

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp", ".heic", ".heif"}
RENDER_DPI = 300


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def store_original(src: Path, data_dir: Path) -> tuple[str, Path, bool]:
    """Copy the upload into the document store. The doc id comes from the file
    content, so uploading the same file twice is detected as a duplicate."""
    digest = sha256(src)
    doc_id = digest[:16]
    doc_dir = data_dir / "documents" / doc_id
    orig_dir = doc_dir / "original"
    duplicate = orig_dir.exists() and any(orig_dir.iterdir())
    if not duplicate:
        orig_dir.mkdir(parents=True, exist_ok=True)
        dst = orig_dir / src.name
        shutil.copy2(src, dst)
        os.chmod(dst, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        meta = {
            "doc_id": doc_id,
            "original_filename": src.name,
            "sha256": digest,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "originals": [{"file": src.name, "sha256": digest, "original_filename": src.name}],
        }
        (doc_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    return doc_id, doc_dir, duplicate


def original_file(doc_dir: Path) -> Path:
    return original_files(doc_dir)[0]


def original_files(doc_dir: Path) -> list[Path]:
    """All uploaded files of a document, in page order (a phone user may
    photograph a 3-page invoice as 3 pictures)."""
    return sorted((doc_dir / "original").iterdir())


def new_document(files: list[tuple[str, bytes]], data_dir: Path) -> tuple[str, Path, list[str]]:
    """Store uploaded files as a new document. Returns (doc_id, folder,
    ids of earlier documents that contain an identical file)."""
    doc_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
    doc_dir = Path(data_dir) / "documents" / doc_id
    (doc_dir / "original").mkdir(parents=True)
    meta = {"doc_id": doc_id, "uploaded_at": datetime.now(timezone.utc).isoformat(), "originals": []}
    (doc_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    dups = []
    for name, data in files:
        dups += add_original(doc_dir, name, data)
    return doc_id, doc_dir, sorted(set(dups))


def add_original(doc_dir: Path, name: str, data: bytes) -> list[str]:
    """Add one more file (e.g. a missing page) to a document."""
    safe = re.sub(r"[^\w.\-א-ת]", "_", Path(name).name)[-80:] or "file"
    kind_of(Path(safe))  # raises for unsupported types
    meta = json.loads((doc_dir / "meta.json").read_text())
    n = len(meta.get("originals", [])) + 1
    dst = doc_dir / "original" / f"{n:02d}_{safe}"
    dst.write_bytes(data)
    os.chmod(dst, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    digest = hashlib.sha256(data).hexdigest()
    meta.setdefault("originals", []).append({"file": dst.name, "sha256": digest, "original_filename": name})
    meta.setdefault("original_filename", name)
    (doc_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    dups = []
    for other in doc_dir.parent.iterdir():
        if other == doc_dir or not (other / "meta.json").exists():
            continue
        om = json.loads((other / "meta.json").read_text())
        shas = {o["sha256"] for o in om.get("originals", [])} | {om.get("sha256")}
        if digest in shas:
            dups.append(other.name)
    return dups


def kind_of(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return "pdf"
    if ext in IMAGE_EXT:
        return "image"
    raise ValueError(f"סוג קובץ לא נתמך: {ext}")


def load_image(path: Path) -> Image.Image:
    if path.suffix.lower() in {".heic", ".heif"}:
        try:
            import pillow_heif  # optional: iPhone photos
            pillow_heif.register_heif_opener()
        except ImportError as e:
            raise ValueError("קובצי HEIC דורשים התקנת pillow-heif") from e
    img = Image.open(path)
    # Phones store rotation as a flag instead of rotating pixels. Applying it is
    # not an edit of content, but we still save the result as a separate file.
    return ImageOps.exif_transpose(img).convert("RGB")


def render_pages(doc_dir: Path) -> list[dict]:
    """Write pages/page-NNN.png for every page of every uploaded file."""
    pages_dir = doc_dir / "pages"
    pages_dir.mkdir(exist_ok=True)
    out = []
    for src in original_files(doc_dir):
        if kind_of(src) == "pdf":
            pdf = pdfium.PdfDocument(str(src))
            for i in range(len(pdf)):
                page = pdf[i]
                img = page.render(scale=RENDER_DPI / 72).to_pil().convert("RGB")
                p = pages_dir / f"page-{len(out) + 1:03d}.png"
                img.save(p)
                out.append({"page": len(out) + 1, "image": str(p.relative_to(doc_dir)),
                            "width": img.width, "height": img.height, "dpi": RENDER_DPI,
                            "source_file": src.name, "source_page": i + 1, "kind": "pdf"})
        else:
            img = load_image(src)
            p = pages_dir / f"page-{len(out) + 1:03d}.png"
            img.save(p)
            out.append({"page": len(out) + 1, "image": str(p.relative_to(doc_dir)),
                        "width": img.width, "height": img.height, "dpi": None,
                        "source_file": src.name, "source_page": 1, "kind": "photo"})
    return out
