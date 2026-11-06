"""Does image clean-up help OCR? Compare the same simulated photos read with
different clean-up settings, against the answer key.

    python tools/compare_preprocessing.py truth_dir out_dir [--docs N] [--jobs J]

For every single-page document in truth_dir it makes a simulated phone photo
plus darker, greyer, faded, sideways and upside-down versions of it
(tools/make_test_images.py). Each image is read three times:
  off       - no clean-up (the "before")
  standard  - the current clean-up (crop, rotate, straighten, shadows)
  contrast  - standard + contrast stretch for dark, grey or faded images

The input images are never changed. Each run gets its own data folder, so the
original, the cleaned image, the raw OCR and the fields are kept per setting
and can be opened side by side.

These are SIMULATED problems on synthetic documents. They show whether a
step helps on that kind of damage. They do not measure real phone photos.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from evaluate import LINE, score  # noqa: E402
from make_test_images import PROBLEMS, photo  # noqa: E402

MODES = ["off", "standard", "contrast"]
FIELDS = ["linked", "code", "description", "quantity", "unit_price", "line_total"]


def make_images(pdf: Path, out: Path) -> list[tuple[str, Path]]:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf))
    if len(doc) != 1:
        return []
    page = cv2.cvtColor(np.array(doc[0].render(scale=300 / 72).to_pil().convert("RGB")), cv2.COLOR_RGB2BGR)
    base = photo(page)
    made = []
    for name, fn in PROBLEMS.items():
        p = out / "inputs" / f"{pdf.stem}__{name}.jpg"
        p.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(p), fn(base), [cv2.IMWRITE_JPEG_QUALITY, 85])
        made.append((name, p))
    return made


def run_one(job):
    """Run in a separate process so the clean-up setting is read fresh."""
    img, mode, truth_file, data = job
    env = dict(os.environ, OCR_PREPROCESS=mode)
    code = ("import json,sys; from pathlib import Path; from ocr_app import pipeline;"
            "d=pipeline.process(Path(sys.argv[1]), Path(sys.argv[2]), reprocess=True); print(d)")
    t0 = time.time()
    r = subprocess.run([sys.executable, "-c", code, str(img), str(data)], env=env,
                       capture_output=True, text=True, cwd=HERE.parent)
    secs = round(time.time() - t0, 1)
    if r.returncode != 0:
        return {"error": r.stderr[-2000:]}
    doc_dir = Path(r.stdout.strip().splitlines()[-1])
    fields = json.loads((doc_dir / "fields.json").read_text())
    truth = json.loads(Path(truth_file).read_text())
    pages = fields.get("pages", [])
    return {"doc_dir": str(doc_dir), "seconds": secs, "score": score(truth, fields),
            "steps": [s for p in pages for s in (p.get("preprocessing") or [])],
            "quality": [p.get("quality") for p in pages],
            "warnings": fields.get("warnings", [])}


def tally(rows):
    t = {k: [0, 0] for k in FIELDS}
    cerr = chars = wrong_ok = 0
    for r in rows:
        for line in r["score"]["lines"]:
            for k in FIELDS:
                t[k][0] += line[k]
                t[k][1] += 1
            cerr += line["char_err"]
            chars += line["chars"]
            wrong_ok += line["wrong_but_ok"]
    return t, (cerr / chars if chars else None), wrong_ok


def to_markdown(results, n_docs):
    modes = [m for m in MODES if any(r["mode"] == m for r in results)]
    out = ["# Image clean-up: OCR before and after", "",
           f"Simulated phone photos of {n_docs} synthetic documents. Each cell: right / total "
           "product lines. **off** = no clean-up, **standard** = current clean-up, "
           "**contrast** = standard + contrast stretch.", ""]
    names = {"linked": "qty+price+total on the right product", "code": "supplier code",
             "description": "description exact", "quantity": "quantity", "unit_price": "unit price",
             "line_total": "line total"}
    for prob in PROBLEMS:
        rows = {m: [r for r in results if r["problem"] == prob and r["mode"] == m and "score" in r]
                for m in modes}
        if not any(rows.values()):
            continue
        out += [f"## {prob}", "", "| measure | " + " | ".join(modes) + " |", "|---|" + "---|" * len(modes)]
        tallies = {m: tally(rows[m]) for m in modes}
        for k in FIELDS:
            out.append(f"| {names[k]} | " + " | ".join(
                f"{tallies[m][0][k][0]}/{tallies[m][0][k][1]}" for m in modes) + " |")
        out.append("| description character error rate | " + " | ".join(
            f"{tallies[m][1]:.1%}" if tallies[m][1] is not None else "—" for m in modes) + " |")
        out.append("| wrong values shown as OK | " + " | ".join(str(tallies[m][2]) for m in modes) + " |")
        out.append("| lines found (right count) | " + " | ".join(
            f"{sum(r['score']['lines_found'] == r['score']['lines_truth'] for r in rows[m])}/{len(rows[m])}"
            for m in modes) + " |")
        out.append("| seconds per document | " + " | ".join(
            f"{sum(r['seconds'] for r in rows[m]) / len(rows[m]):.1f}" if rows[m] else "—" for m in modes) + " |")
        out.append("")
    errors = [r for r in results if "error" in r]
    if errors:
        out += [f"**{len(errors)} runs failed** (see compare.json)."]
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("truth_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--docs", type=int, default=0, help="use only the first N single-page documents")
    ap.add_argument("--problems", default=",".join(PROBLEMS), help="comma-separated subset")
    ap.add_argument("--modes", default=",".join(MODES), help="comma-separated subset")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    a = ap.parse_args()
    out = Path(a.out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    jobs, meta = [], []
    used = 0
    for tf in sorted(Path(a.truth_dir).glob("*.truth.json")):
        pdf = tf.with_name(tf.name.replace(".truth.json", ".pdf"))
        imgs = make_images(pdf, out)
        if not imgs:
            continue
        used += 1
        for prob, img in imgs:
            if prob not in a.problems.split(","):
                continue
            for m in a.modes.split(","):
                jobs.append((img, m, tf, out / f"data_{m}"))
                meta.append({"file": img.name, "problem": prob, "mode": m})
        if a.docs and used >= a.docs:
            break
    results = []
    with ProcessPoolExecutor(a.jobs) as ex:
        for m, r in zip(meta, ex.map(run_one, jobs)):
            results.append({**m, **r})
            print(m["file"], m["mode"], "error" if "error" in r else
                  f"{sum(l['linked'] for l in r['score']['lines'])}/{len(r['score']['lines'])} linked",
                  flush=True)
    (out / "compare.json").write_text(json.dumps(results, ensure_ascii=False, indent=1))
    md = to_markdown(results, used)
    (out / "compare.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
