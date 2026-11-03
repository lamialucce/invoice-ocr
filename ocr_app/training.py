"""Training examples for the OCR model (Stage 4).

What Tesseract can be fine-tuned on is its TEXT RECOGNITION: an image of one
line of text -> the exact characters in it. So an example is:

    crop of one text line from a page  +  the exact text visible in it

Rules:
- The label is what is PRINTED, letter for letter. Not the catalog name, not
  a "cleaned" value. That is why these are separate from review.json.
- Each example has a status:
      suggested  - created automatically, nobody checked it yet
      corrected  - a reviewer typed/confirmed the text but did not approve it
      approved   - an authorized reviewer approved it for training
      rejected   - not usable (cut-off crop, unreadable, two lines, ...)
- Approved is still not "trained". Training is a separate step (models.py,
  tools/train_tesseract.sh) and is recorded in the model registry.

Files: data/training/examples/<id>.json and data/training/crops/<id>.png
"""
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from . import ocr
from .pipeline import _clean_cell as clean_cell
from .textutil import clean, script_of

STATUSES = ["suggested", "corrected", "approved", "rejected"]


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dirs(data_dir):
    root = Path(data_dir) / "training"
    (root / "examples").mkdir(parents=True, exist_ok=True)
    (root / "crops").mkdir(parents=True, exist_ok=True)
    return root


def _cell_lines(page, cell):
    """Split a table cell into its text lines using the word boxes."""
    words = {w["id"]: w for w in page["words"]}
    ws = [words[i] for i in cell.get("word_ids", []) if i in words]
    rows: list[list[dict]] = []
    for w in sorted(ws, key=lambda w: (w["bbox"][1] + w["bbox"][3]) / 2):
        cy = (w["bbox"][1] + w["bbox"][3]) / 2
        if rows and abs(cy - sum((x["bbox"][1] + x["bbox"][3]) / 2 for x in rows[-1]) / len(rows[-1])) < \
                0.5 * (w["bbox"][3] - w["bbox"][1]):
            rows[-1].append(w)
        else:
            rows.append([w])
    texts = [t for t in clean(cell.get("text", "")).split("\n") if t.strip()]
    out = []
    for i, r in enumerate(rows):
        box = [min(w["bbox"][0] for w in r), min(w["bbox"][1] for w in r),
               max(w["bbox"][2] for w in r), max(w["bbox"][3] for w in r)]
        out.append((box, texts[i] if i < len(texts) and len(texts) == len(rows) else None, r))
    return out


def generate_candidates(doc_dir: Path, data_dir: Path, review: dict | None = None) -> int:
    """Create 'suggested' examples for every text line in the product table
    of a document. Returns how many were created."""
    root = _dirs(data_dir)
    doc_dir = Path(doc_dir)
    made = 0
    written = {}  # employee said "this is exactly what is written" for these values
    if review:
        for l in review["lines"]:
            for k, f in l["fields"].items():
                if f.get("written_on_document") and f.get("changed"):
                    written[(l["line_id"], k)] = f["value"]
    line_by_row = {}
    fields = json.loads((doc_dir / "fields.json").read_text())
    for l in fields["lines"]:
        line_by_row[(l["page"], l["line_id"].rsplit("-r", 1)[-1])] = l["line_id"]
    for pf in sorted((doc_dir / "raw").glob("page-*.json")):
        page = json.loads(pf.read_text())
        img = Image.open(doc_dir / page["coordinates_refer_to"]).convert("L")
        for t in page["tables"]:
            kinds = t.get("column_kinds", {})
            for cell in t["cells"]:
                if cell["row"] == 0 or not clean(cell.get("text", "")):
                    continue
                kind = kinds.get(str(cell["col"]))
                line_id = line_by_row.get((page["page"], f"{cell['row']:02d}"))
                for li, (box, line_text, ws) in enumerate(_cell_lines(page, cell)):
                    key = f"{doc_dir.name}|{page['page']}|{cell['row']}|{cell['col']}|{li}"
                    eid = hashlib.sha1(key.encode()).hexdigest()[:16]
                    if (root / "examples" / f"{eid}.json").exists():
                        continue
                    x0, y0, x1, y1 = box
                    pad = max(4, int((y1 - y0) * 0.25))
                    # Same clean-up as for reading a cell: table lines erased,
                    # so the model does not learn borders as letters.
                    crop = clean_cell(img, [max(0, x0 - pad), max(0, y0 - pad),
                                            min(img.width, x1 + pad), min(img.height, y1 + pad)])
                    if crop is None:
                        continue
                    crop.save(root / "crops" / f"{eid}.png")
                    predicted, conf, lang = ocr.ocr_text_best(crop, psm=7)
                    from_pdf = page["method"] == "pdf_text" and all(w["source"] == "pdf_text" for w in ws)
                    suggested = line_text if from_pdf and line_text else clean(predicted)
                    origin = "pdf_text" if from_pdf and line_text else "ocr_prediction"
                    single_line_cell = li == 0 and len(clean(cell["text"]).split("\n")) == 1
                    if line_id and kind and (line_id, kind) in written and single_line_cell:
                        suggested, origin = written[(line_id, kind)], "employee_said_written"
                    ex = {"example_id": eid, "doc_id": doc_dir.name, "doc_group": doc_dir.name,
                          "page": page["page"], "bbox": [int(v) for v in box],
                          "page_image": page["coordinates_refer_to"], "column": kind,
                          "line_id": line_id, "crop": f"crops/{eid}.png",
                          "predicted_text": clean(predicted), "predicted_conf": conf,
                          "model": ocr.model_version(), "suggested_text": suggested,
                          "suggestion_origin": origin, "verified_text": None, "status": "suggested",
                          "script": script_of(suggested), "input_kind": "pdf" if page["method"] == "pdf_text"
                          else "photo" if page.get("preprocessing") and "cropped_to_paper" in page["preprocessing"]
                          else "scan_or_photo", "created_at": _now(), "history": []}
                    (root / "examples" / f"{eid}.json").write_text(json.dumps(ex, ensure_ascii=False, indent=1))
                    made += 1
    return made


def list_examples(data_dir, status=None, doc_id=None) -> list[dict]:
    root = _dirs(data_dir)
    out = []
    for p in sorted(root.glob("examples/*.json")):
        ex = json.loads(p.read_text())
        if (status is None or ex["status"] == status) and (doc_id is None or ex["doc_id"] == doc_id):
            out.append(ex)
    return out


def update_example(data_dir, eid: str, verified_text: str | None, action: str, user: str) -> dict:
    """action: 'save' (corrected), 'approve', 'reject', 'reset'."""
    root = _dirs(data_dir)
    p = root / "examples" / f"{eid}.json"
    ex = json.loads(p.read_text())
    old = (ex["status"], ex["verified_text"])
    if verified_text is not None:
        ex["verified_text"] = verified_text.strip()
    if action == "approve":
        if not ex["verified_text"]:
            ex["verified_text"] = ex["suggested_text"]
        if not ex["verified_text"]:
            raise ValueError("אי אפשר לאשר דוגמה בלי טקסט")
        ex["status"] = "approved"
    elif action == "reject":
        ex["status"] = "rejected"
    elif action == "save":
        ex["status"] = "corrected"
    elif action == "reset":
        ex["status"], ex["verified_text"] = "suggested", None
    ex["script"] = script_of(ex["verified_text"] or ex["suggested_text"])
    ex["history"].append({"at": _now(), "by": user, "action": action, "old": old,
                          "new": (ex["status"], ex["verified_text"])})
    ex["reviewed_by"], ex["reviewed_at"] = user, _now()
    p.write_text(json.dumps(ex, ensure_ascii=False, indent=1))
    return ex


def split_of(group: str) -> str:
    """Train / validation / test by document, never by line, so lines of the
    same document (or a re-upload of it) never end up on both sides."""
    h = int(hashlib.sha1(group.encode()).hexdigest(), 16) % 10
    return "test" if h == 0 else "val" if h == 1 else "train"


def counters(data_dir) -> dict:
    exs = list_examples(data_dir)
    c = {s: 0 for s in STATUSES}
    for ex in exs:
        c[ex["status"]] += 1
    c["corrections_saved"] = sum(1 for ex in exs if ex["status"] in ("corrected", "approved")
                                 and ex["verified_text"] and ex["verified_text"] != ex["predicted_text"])
    c["approved_by_split"] = {s: sum(1 for ex in exs if ex["status"] == "approved" and split_of(ex["doc_group"]) == s)
                              for s in ("train", "val", "test")}
    c["documents"] = len({ex["doc_id"] for ex in exs})
    return c


def export_tesstrain(data_dir, out_dir) -> dict:
    """Write approved examples in the layout tesstrain expects:
        out_dir/<split>/<id>.png + <id>.gt.txt   and out_dir/manifest.json"""
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    root = _dirs(data_dir)
    manifest = {"created_at": _now(), "format": "tesstrain line images (.png + .gt.txt)", "examples": []}
    for ex in list_examples(data_dir, status="approved"):
        split = split_of(ex["doc_group"])
        d = out / split
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(root / ex["crop"], d / f"{ex['example_id']}.png")
        (d / f"{ex['example_id']}.gt.txt").write_text(ex["verified_text"] + "\n")
        manifest["examples"].append({"id": ex["example_id"], "split": split, "doc_group": ex["doc_group"],
                                     "script": ex["script"], "input_kind": ex["input_kind"]})
    manifest["counts"] = {s: sum(1 for e in manifest["examples"] if e["split"] == s) for s in ("train", "val", "test")}
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    return manifest
