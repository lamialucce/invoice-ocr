"""Generate SYNTHETIC supplier documents with a known answer key.

Why: we have only one real document. To build and measure the system we make
fake invoices / delivery notes in several layouts, using product names from
the catalog. Supplier names are invented. Every PDF gets a matching
<name>.truth.json with the values that are printed on it.

Results on these documents show the system works on these layouts. They do
NOT prove accuracy on real supplier documents.

    python tools/synth.py catalog.xlsx out_dir --count 12 --seed 1
"""
import argparse
import html
import json
import random
import re
import subprocess
from datetime import date, timedelta
from pathlib import Path

import openpyxl

SUPPLIERS = [  # invented
    {"name": 'אור-טק ציוד צילום בע"מ', "tax_id": "514203987", "lang": "he"},
    {"name": 'פוטו פלוס יבוא והפצה בע"מ', "tax_id": "515678321", "lang": "he"},
    {"name": "Nordlight Imaging Ltd", "tax_id": "516004512", "lang": "en"},
    {"name": "Lumen Pro Supply Co.", "tax_id": "513377820", "lang": "en"},
]
UNITS_HE = ["יח'", "יח'", "יח'", "קרטון", "זוג", "מטר"]
UNITS_EN = ["pcs", "pcs", "pcs", "box", "pair", "m"]
HE_PREFIX = ["מצלמה", "כבל", "מעמד", "תאורה", "תיק", "סוללה", "פילטר", "מיקרופון"]
VAT = 18.0


def load_catalog(path):
    ws = openpyxl.load_workbook(path, read_only=True).active
    out = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        vals = [re.sub(r'^="|"$', "", str(v or "")) for v in r[:3]]
        code, desc, group = vals
        if desc and "לא לשימוש" not in desc and not desc.startswith("**") and len(desc) > 6:
            out.append({"code": code, "desc": " ".join(desc.replace("זמני", "").split()).strip(" -")})
    return out


def make_doc(rng, catalog, i):
    layout = ["he_grid_invoice", "en_plain_invoice", "he_delivery_note", "mixed_long_invoice"][i % 4]
    sup = rng.choice([s for s in SUPPLIERS if (s["lang"] == "en") == (layout == "en_plain_invoice")])
    n_lines = rng.randint(18, 26) if layout == "mixed_long_invoice" else rng.randint(3, 9)
    heb_items = [c for c in catalog if re.search("[א-ת]", c["desc"])]
    lines = []
    for k in range(n_lines):
        item = rng.choice(heb_items if layout != "en_plain_invoice" and rng.random() < 0.3 else catalog)
        desc = item["desc"][:55]
        if layout != "en_plain_invoice" and rng.random() < 0.3 and not re.search("[א-ת]", desc):
            desc = f"{rng.choice(HE_PREFIX)} {desc}"  # supplier's own wording, not the catalog's
        code_kind = rng.random()
        code = (item["code"] if code_kind < 0.4 else
                f"{sup['name'][:2].upper() if sup['lang'] == 'en' else 'OT'}-{rng.randint(1000, 99999)}" if code_kind < 0.85 else "")
        qty = rng.choice([1, 2, 3, 4, 5, 6, 10, 12, 20, 24, 50])
        price = round(rng.choice([rng.uniform(5, 120), rng.uniform(120, 2500)]), 2)
        unit = rng.choice(UNITS_EN if layout == "en_plain_invoice" else UNITS_HE)
        lines.append({"code": code, "description": desc, "quantity": float(qty), "unit": unit,
                      "unit_price": price, "line_total": round(qty * price, 2)})
    sub = round(sum(l["line_total"] for l in lines), 2)
    vat = round(sub * VAT / 100, 2)
    d = date(2026, 1, 1) + timedelta(days=rng.randint(0, 290))
    truth = {
        "layout": layout,
        "synthetic": True,
        "header": {
            "supplier_name": sup["name"], "supplier_tax_id": sup["tax_id"],
            "document_type": "delivery_note" if layout == "he_delivery_note" else "invoice",
            "document_number": str(rng.randint(10000, 99999)),
            "document_date": d.isoformat(),
            # A delivery note prints no prices, so no currency either.
            "currency": None if layout == "he_delivery_note" else
            "USD" if layout == "en_plain_invoice" and rng.random() < 0.5 else "ILS",
            "subtotal": None if layout == "he_delivery_note" else sub,
            "vat": None if layout == "he_delivery_note" else vat,
            "total": None if layout == "he_delivery_note" else round(sub + vat, 2),
        },
        "lines": lines,
    }
    if layout == "he_delivery_note":
        for l in lines:
            l["unit_price"] = l["line_total"] = None
    return truth


e = html.escape
CSS = """
@page{size:A4;margin:14mm}
body{font-family:'DejaVu Sans','Liberation Sans',sans-serif;font-size:10.5pt;color:#111}
.top{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10mm}
.name{font-size:16pt;font-weight:bold} h1{font-size:15pt;text-align:center;margin:6mm 0}
table{width:100%;border-collapse:collapse;margin-top:4mm}
.grid td,.grid th{border:1px solid #333;padding:2mm 2mm}
.plain th{border-bottom:2px solid #333;padding:2mm;text-align:start}.plain td{padding:1.6mm 2mm}
.num{text-align:end;white-space:nowrap} .tot{margin-top:5mm;width:45%} .tot td{padding:1mm 2mm}
.code{direction:ltr;unicode-bidi:isolate}
tr{page-break-inside:avoid}
"""


def fmt(v):
    return f"{v:,.2f}"


def render_html(t):
    h, L = t["header"], t["lines"]
    lay = t["layout"]
    he = lay != "en_plain_invoice"
    dd = date.fromisoformat(h["document_date"])
    date_txt = dd.strftime("%d/%m/%Y")
    cur_sym = {"ILS": 'ש"ח' if he else "NIS", "USD": "USD", None: ""}[h["currency"]]
    if he:
        title = {"he_delivery_note": "תעודת משלוח", "he_grid_invoice": "חשבונית מס",
                 "mixed_long_invoice": "חשבונית מס"}[lay] + f" מספר {h['document_number']}"
        cols = [("מס'", None), ('מק"ט', "code"), ("תיאור פריט", "description"), ("כמות", "quantity"), ("יח' מידה", "unit")]
        if lay != "he_delivery_note":
            cols += [("מחיר ליח'", "unit_price"), ('סה"כ', "line_total")]
        party = f"<div>לכבוד<br><b>חברה לדוגמה בע\"מ</b></div>"
        hdr = (f"<div><div class='name'>{e(h['supplier_name'])}</div><div>רח' התעשייה 12, חולון</div>"
               f"<div>ע.מ. {h['supplier_tax_id']}</div></div><div>תאריך: {date_txt}</div>")
        tot_labels = ('סה"כ לפני מע"מ', f'מע"מ {VAT:g}%', f'סה"כ לתשלום ב{cur_sym}')
    else:
        title = f"TAX INVOICE No. {h['document_number']}"
        cols = [("#", None), ("Item code", "code"), ("Description", "description"), ("Qty", "quantity"),
                ("Unit", "unit"), ("Unit price", "unit_price"), ("Amount", "line_total")]
        party = "<div>Bill to:<br><b>Example Company Ltd</b></div>"
        hdr = (f"<div><div class='name'>{e(h['supplier_name'])}</div><div>14 Harbour Rd, Haifa</div>"
               f"<div>VAT No. {h['supplier_tax_id']}</div></div><div>Date: {date_txt}</div>")
        tot_labels = ("Subtotal", f"VAT {VAT:g}%", f"Total {cur_sym}")
    cls = "plain" if lay == "en_plain_invoice" else "grid"
    th = "".join(f"<th>{e(c)}</th>" for c, _ in cols)
    rows = []
    for i, l in enumerate(L, 1):
        tds = []
        for _, k in cols:
            if k is None:
                tds.append(f"<td class='num'>{i}</td>")
            elif k in ("quantity",):
                tds.append(f"<td class='num'>{l[k]:g}</td>")
            elif k in ("unit_price", "line_total"):
                tds.append(f"<td class='num'>{fmt(l[k])}</td>")
            elif k == "code":
                tds.append(f"<td><span class='code'>{e(l[k])}</span></td>")
            else:
                tds.append(f"<td>{e(l[k])}</td>")
        rows.append("<tr>" + "".join(tds) + "</tr>")
    totals = ""
    if h["subtotal"] is not None:
        totals = (f"<table class='tot'><tr><td>{tot_labels[0]}</td><td class='num'>{fmt(h['subtotal'])}</td></tr>"
                  f"<tr><td>{tot_labels[1]}</td><td class='num'>{fmt(h['vat'])}</td></tr>"
                  f"<tr><td><b>{tot_labels[2]}</b></td><td class='num'><b>{fmt(h['total'])}</b></td></tr></table>")
    sign = "חתימת מקבל הסחורה: ____________" if lay == "he_delivery_note" else ""
    return f"""<!doctype html><html dir="{'rtl' if he else 'ltr'}" lang="{'he' if he else 'en'}"><head><meta charset="utf-8">
<style>{CSS}</style></head><body><div class="top">{hdr}</div>{party}<h1>{e(title)}</h1>
<table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{''.join(rows)}</tbody></table>{totals}<p>{sign}</p></body></html>"""


def to_pdf(jobs):
    """HTML -> PDF with Chromium (Node Playwright, see html2pdf.js)."""
    args = []
    for html_text, out in jobs:
        h = out.with_suffix(".html")
        h.write_text(html_text)
        args += [str(h), str(out)]
    subprocess.run(["node", str(Path(__file__).with_name("html2pdf.js")), *args], check=True)
    for html_text, out in jobs:
        out.with_suffix(".html").unlink()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("catalog")
    ap.add_argument("out")
    ap.add_argument("--count", type=int, default=12)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    catalog = load_catalog(a.catalog)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    jobs = []
    for i in range(a.count):
        t = make_doc(rng, catalog, i)
        name = f"synth_{i:03d}_{t['layout']}"
        (out / f"{name}.truth.json").write_text(json.dumps(t, ensure_ascii=False, indent=1))
        jobs.append((render_html(t), out / f"{name}.pdf"))
    to_pdf(jobs)
    print(f"wrote {len(jobs)} documents to {out}")


if __name__ == "__main__":
    main()
