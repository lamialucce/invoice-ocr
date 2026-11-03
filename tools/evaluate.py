"""Measure accuracy against an answer key.

Input: a folder with documents and, next to each, <name>.truth.json
(from tools/synth.py now; from reviewed real documents later).
For each single-page PDF it can also create a simulated scan and phone photo
(--variants), so PDF text, scans and photos are measured separately.

    python tools/evaluate.py truth_dir out_dir [--variants] [--data data_dir]

Writes out_dir/eval.json (all numbers) and out_dir/eval.md (summary tables).
Run it with OCR_TESSDATA=... to measure a fine-tuned model on the same files.
"""
import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_test_images import photo, scan  # noqa: E402
from ocr_app import ocr, pipeline  # noqa: E402
from ocr_app.textutil import script_of  # noqa: E402
from PIL import Image  # noqa: E402

HEAD = ["supplier_name", "supplier_tax_id", "document_type", "document_number", "document_date",
        "currency", "subtotal", "vat", "total"]
LINE = ["code", "description", "quantity", "unit", "unit_price", "line_total"]


def same(a, b):
    a = None if a == "" else a
    b = None if b == "" else b
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) < 0.005
    return " ".join(str(a).split()) == " ".join(str(b).split())


def edit_distance(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def score(truth, fields):
    """Per-field results for one document."""
    res = {"header": {}, "lines": [], "lines_truth": len(truth["lines"]), "lines_found": len(fields["lines"])}
    for k in HEAD:
        if k in truth["header"]:
            res["header"][k] = same(truth["header"][k], fields["header"][k]["value"])
    for i, tl in enumerate(truth["lines"]):
        fl = fields["lines"][i] if i < len(fields["lines"]) else None
        row = {"script": script_of(tl["description"])}
        for k in LINE:
            got = fl[k]["value"] if fl else None
            row[k] = same(tl[k], got)
            if k == "description":
                g = got or ""
                row["char_err"] = edit_distance(tl[k], g)
                row["chars"] = len(tl[k])
                tw, gw = tl[k].split(), g.split()
                row["word_err"] = edit_distance(tw, gw)
                row["words"] = len(tw)
        # "Linked": the quantity, price and total that belong to this product
        # all ended up on this product's line.
        row["linked"] = all(row[k] for k in ["quantity", "unit_price", "line_total"])
        # Was a wrong value marked for review (good) or shown as ok (bad)?
        row["wrong_but_ok"] = sum(1 for k in LINE if fl and not row[k] and fl[k]["status"] == "ok")
        res["lines"].append(row)
    return res


def make_variants(pdf, out_dir):
    """Simulated scan + photo of page 1 (single-page PDFs only)."""
    import pypdfium2 as pdfium
    import numpy as np
    doc = pdfium.PdfDocument(str(pdf))
    if len(doc) != 1:
        return []
    page = cv2.cvtColor(np.array(doc[0].render(scale=300 / 72).to_pil().convert("RGB")), cv2.COLOR_RGB2BGR)
    s = out_dir / f"{pdf.stem}__scan.pdf"
    Image.fromarray(scan(page)).save(s, resolution=200)
    p = out_dir / f"{pdf.stem}__photo.jpg"
    cv2.imwrite(str(p), photo(page), [cv2.IMWRITE_JPEG_QUALITY, 85])
    return [("scan", s), ("photo", p)]


def summarize(results):
    groups = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for r in results:
        for gname in ["all", f"input:{r['variant']}", f"layout:{r['layout']}"]:
            g = groups[gname]
            for k, ok in r["score"]["header"].items():
                g[k][0] += ok
                g[k][1] += 1
            for row in r["score"]["lines"]:
                for k in LINE + ["linked"]:
                    g[k][0] += row[k]
                    g[k][1] += 1
                g["desc_chars"][0] += row["char_err"]
                g["desc_chars"][1] += row["chars"]
                g["desc_words"][0] += row["word_err"]
                g["desc_words"][1] += row["words"]
                g["wrong_but_marked_ok"][0] += row["wrong_but_ok"]
                g["wrong_but_marked_ok"][1] += 1
                g[f"desc_{row['script']}"][0] += row["description"]
                g[f"desc_{row['script']}"][1] += 1
            g["lines_count_right"][0] += r["score"]["lines_found"] == r["score"]["lines_truth"]
            g["lines_count_right"][1] += 1
            g["seconds"][0] += r["seconds"]
            g["seconds"][1] += 1
    return {g: {k: {"right": v[0], "total": v[1], "rate": round(v[0] / v[1], 4) if v[1] else None}
                for k, v in d.items()} for g, d in groups.items()}


def to_markdown(summary, meta):
    keys = (["lines_count_right", "linked"] + LINE + HEAD +
            ["desc_he", "desc_en", "desc_mixed", "desc_chars", "desc_words", "wrong_but_marked_ok", "seconds"])
    names = {"desc_chars": "description character error rate", "desc_words": "description word error rate",
             "seconds": "seconds per document (avg)", "linked": "qty+price+total on the right product",
             "wrong_but_marked_ok": "wrong values shown as OK (count)",
             "desc_he": "description exact: Hebrew", "desc_en": "description exact: English",
             "desc_mixed": "description exact: mixed", "lines_count_right": "found the right number of lines"}
    groups = [g for g in ["all", "input:pdf", "input:scan", "input:photo"] if g in summary]
    groups += sorted(g for g in summary if g.startswith("layout:"))
    out = [f"# Evaluation\n\n{meta}\n", "| measure | " + " | ".join(groups) + " |",
           "|---|" + "---|" * len(groups)]
    for k in keys:
        cells = []
        for g in groups:
            v = summary[g].get(k)
            if not v or not v["total"]:
                cells.append("—")
            elif k in ("desc_chars", "desc_words"):
                cells.append(f"{v['right'] / v['total']:.1%}")
            elif k == "seconds":
                cells.append(f"{v['right'] / v['total']:.1f}")
            elif k == "wrong_but_marked_ok":
                cells.append(str(v["right"]))
            else:
                cells.append(f"{v['right']}/{v['total']}")
        out.append(f"| {names.get(k, k)} | " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("truth_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--data", default=None)
    ap.add_argument("--variants", action="store_true")
    a = ap.parse_args()
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    data = Path(a.data or out / "data")
    results = []
    for tf in sorted(Path(a.truth_dir).glob("*.truth.json")):
        truth = json.loads(tf.read_text())
        pdf = tf.with_name(tf.name.replace(".truth.json", ".pdf"))
        inputs = [("pdf", pdf)]
        if a.variants:
            inputs += make_variants(pdf, out)
        for variant, f in inputs:
            t0 = time.time()
            doc_dir = pipeline.process(f, data, reprocess=True)
            fields = json.loads((doc_dir / "fields.json").read_text())
            results.append({"file": f.name, "variant": variant, "layout": truth.get("layout", "real"),
                            "doc_dir": str(doc_dir), "seconds": round(time.time() - t0, 1),
                            "score": score(truth, fields)})
            print(f"{f.name}: lines {results[-1]['score']['lines_found']}/{len(truth['lines'])}", flush=True)
    summary = summarize(results)
    meta = f"Model: {json.dumps(ocr.model_version())}. Documents: {len(results)}."
    (out / "eval.json").write_text(json.dumps({"meta": meta, "summary": summary, "results": results},
                                              ensure_ascii=False, indent=1))
    (out / "eval.md").write_text(to_markdown(summary, meta))
    print(to_markdown(summary, meta))


if __name__ == "__main__":
    main()
