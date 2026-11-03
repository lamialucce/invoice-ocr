"""A simple results page per document (Stage 1). Three separate panels:
    1. the document image (original, and processed version if any)
    2. the raw reading result, exactly as read
    3. the structured fields, coloured by status
Click a field to see where on the page it was read. This is a checking tool
for us, not the employee interface (that is Stage 3).
"""
import html
import json
from pathlib import Path

STATUS_HE = {"ok": "תקין", "missing": "חסר", "unclear": "לבדיקה", "check_failed": "חישוב לא מתאים"}
METHOD_HE = {"pdf_text": "טקסט מתוך ה-PDF", "ocr": "OCR (זיהוי מתמונה)"}
WORD_SOURCE_HE = {"pdf_text": "טקסט PDF", "pdf_text+repaired": "טקסט PDF + תיקון אות",
                  "ocr_fallback": "OCR למילה", "ocr": "OCR"}
HEAD_HE = {"supplier_name": "ספק", "supplier_tax_id": "ע.מ / ח.פ", "supplier_number": "מספר ספק",
           "document_type": "סוג מסמך", "document_number": "מספר מסמך", "document_date": "תאריך",
           "currency": "מטבע", "subtotal": "סה\"כ לפני מע\"מ", "vat": "מע\"מ", "total": "סה\"כ לתשלום"}
LINE_HE = [("line_no", "#"), ("code", "קוד ספק"), ("description", "תיאור"), ("quantity", "כמות"),
           ("unit", "יחידה"), ("unit_price", "מחיר"), ("discount", "הנחה %"), ("line_total", "סה\"כ")]

e = html.escape

CSS = """
:root{--ok:#1f7a3a;--miss:#8a8a8a;--unc:#b26a00;--bad:#b3261e;--bg:#fff;--fg:#1d1d1f;--panel:#f5f5f7;--line:#ddd}
@media (prefers-color-scheme: dark){:root{--bg:#161618;--fg:#eee;--panel:#222226;--line:#3a3a3e}}
body{margin:0;font-family:Arial,"Noto Sans Hebrew",sans-serif;background:var(--bg);color:var(--fg);font-size:15px}
header{padding:12px 16px;border-bottom:1px solid var(--line)}
h1{font-size:20px;margin:0 0 4px} h2{font-size:17px;margin:0 0 8px}
.grid{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(0,.8fr) minmax(0,1.2fr);gap:12px;padding:12px}
@media (max-width:1100px){.grid{grid-template-columns:1fr}}
section{background:var(--panel);border-radius:8px;padding:12px;overflow:auto;max-height:92vh}
.pagebox{position:relative;display:inline-block;width:100%}
.pagebox img{width:100%;display:block;border:1px solid var(--line)}
.hl{position:absolute;border:3px solid #e8a400;background:rgba(232,164,0,.18);pointer-events:none;display:none}
pre{white-space:pre-wrap;direction:rtl;unicode-bidi:plaintext;font-family:inherit;margin:0;line-height:1.6}
.raw-line{unicode-bidi:plaintext;display:block}
table{border-collapse:collapse;width:100%;margin-bottom:12px}
td,th{border:1px solid var(--line);padding:4px 6px;vertical-align:top;text-align:start}
td.f{cursor:pointer} td.f:hover{outline:2px solid #e8a400}
.ok{color:var(--ok)} .missing{color:var(--miss)} .unclear{color:var(--unc);font-weight:bold}
.check_failed{color:var(--bad);font-weight:bold}
.ltr{direction:ltr;unicode-bidi:isolate;display:inline-block}
.small{font-size:12px;opacity:.8} .warn{background:#fff3cd;color:#5c4400;padding:6px 10px;border-radius:6px;margin:4px 0}
.tag{font-size:12px;padding:1px 6px;border-radius:10px;border:1px solid currentColor}
"""

JS = """
function show(page,b){
  document.querySelectorAll('.hl').forEach(x=>x.style.display='none');
  if(!b)return;
  const box=document.getElementById('pg'+page); const img=box.querySelector('img'); const h=box.querySelector('.hl');
  const sx=img.clientWidth/img.naturalWidth, sy=img.clientHeight/img.naturalHeight;
  h.style.left=(b[0]*sx-4)+'px'; h.style.top=(b[1]*sy-4)+'px';
  h.style.width=((b[2]-b[0])*sx+8)+'px'; h.style.height=((b[3]-b[1])*sy+8)+'px'; h.style.display='block';
  box.scrollIntoView({behavior:'smooth',block:'nearest'});
  img.parentElement.parentElement.scrollTop = Math.max(0, b[1]*sy - 120);
}
"""


def _val(f):
    v = f["value"]
    if v is None:
        return "—"
    if isinstance(v, float):
        s = f"{v:,.2f}" if "." in (f["raw"] or "") or not v.is_integer() else f"{v:,.0f}"
    else:
        s = str(v)
    return f'<bdi>{e(s)}</bdi>'


def _cell(f, extra=""):
    src = f.get("source") or {}
    attrs = f'onclick=\'show({src.get("page")},{json.dumps(src.get("bbox"))})\'' if src.get("bbox") else ""
    raw = f["raw"]
    tip = "קריאה מקורית: " + raw if raw is not None else "לא נמצא במסמך"
    if f["reasons"]:
        tip += " | " + " ; ".join(f["reasons"])
    return (f'<td class="f {f["status"]}" title="{e(tip)}" {attrs}>{_val(f)}'
            f'{extra}<div class="small">{e(STATUS_HE[f["status"]])}</div></td>')


def write(doc_dir: Path) -> Path:
    doc_dir = Path(doc_dir)
    fields = json.loads((doc_dir / "fields.json").read_text())
    meta = json.loads((doc_dir / "meta.json").read_text())
    raws = [json.loads(p.read_text()) for p in sorted((doc_dir / "raw").glob("page-*.json"))]
    orig = next((doc_dir / "original").iterdir())

    pages_html = []
    raw_html = []
    for r in raws:
        img = r.get("processed_image") or r["page_image"]
        label = "תמונה אחרי עיבוד (" + ", ".join(r["preprocessing"]) + ")" if r.get("processed_image") else "העמוד כפי שהוא"
        pages_html.append(
            f'<h2>עמוד {r["page"]}</h2><div class="small">{e(label)} · '
            f'<a href="{e(r["page_image"])}" target="_blank">העמוד המקורי</a></div>'
            f'<div class="pagebox" id="pg{r["page"]}"><img src="{e(img)}" alt="עמוד {r["page"]}"><div class="hl"></div></div>')
        sources = {}
        for w in r["words"]:
            sources[w["source"]] = sources.get(w["source"], 0) + 1
        src_txt = ", ".join(f"{WORD_SOURCE_HE.get(k, k)}: {v}" for k, v in sources.items())
        q = r.get("quality")
        qtxt = f' · חדות {q["sharpness"]}, בהירות {q["brightness"]}' if q else ""
        repaired = r.get("repaired_letters")
        rep = f'<div class="small">אותיות שתוקנו: {e(json.dumps(repaired, ensure_ascii=False))}</div>' if repaired else ""
        lines = "".join(
            f'<span class="raw-line" onclick=\'show({r["page"]},{json.dumps(l["bbox"])})\'>{e(l["text"])}</span>'
            for l in r["lines"])
        raw_html.append(
            f'<h2>עמוד {r["page"]} — {e(METHOD_HE.get(r["method"], r["method"]))}</h2>'
            f'<div class="small">{e(r["method_reason"])}{e(qtxt)}<br>מקור המילים: {e(src_txt)}</div>{rep}'
            f'<pre>{lines}</pre>')

    head_rows = "".join(f"<tr><th>{e(HEAD_HE[k])}</th>{_cell(f)}</tr>" for k, f in fields["header"].items())
    line_head = "".join(f"<th>{e(n)}</th>" for _, n in LINE_HE) + "<th>בדיקת חישוב</th>"
    line_rows = []
    for l in fields["lines"]:
        tds = "".join(_cell(l[k]) for k, _ in LINE_HE)
        chk = l["checks"][0] if l["checks"] else {}
        res = chk.get("result")
        cls = {"ok": "ok", "failed": "check_failed"}.get(res, "missing")
        txt = {"ok": "תקין", "failed": f'צפוי {chk.get("expected")}', "not_possible": "אי אפשר לבדוק"}.get(res, "")
        note = f'<div class="small">{e(chk["note"])}</div>' if chk.get("note") else ""
        line_rows.append(f'<tr onclick=\'show({l["page"]},{json.dumps(l["row_bbox"])})\'>{tds}'
                         f'<td class="{cls}">{e(txt)}{note}</td></tr>')
    doc_checks = "".join(
        f'<li class="{"ok" if c["result"] == "ok" else "check_failed"}">{e(c["check"])}: '
        f'צפוי {c["expected"]:,.2f}, במסמך {c["found"]:,.2f}</li>' for c in fields["checks"])
    warns = "".join(f'<div class="warn">{e(w)}</div>' for w in fields["warnings"])
    model = fields.get("model") or {}

    page = f"""<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>תוצאת קריאה</title>
<style>{CSS}</style></head><body>
<header><h1>{e(meta["original_filename"])}</h1>
<div class="small">מזהה מסמך <bdi>{e(fields["doc_id"])}</bdi> · מנוע <bdi>{e(model.get("engine", ""))} {e(model.get("engine_version", ""))} ({e(model.get("lang", ""))})</bdi>
 · זמן עיבוד {fields.get("processing_seconds")} שניות · <a href="original/{e(orig.name)}" target="_blank">הקובץ המקורי</a></div>{warns}</header>
<div class="grid">
<section><h2>1. המסמך</h2>{''.join(pages_html)}</section>
<section><h2>2. מה נקרא (גולמי)</h2>{''.join(raw_html)}</section>
<section><h2>3. שדות שחולצו</h2>
<div class="small">לחצו על שדה כדי לראות מאיפה הוא נקרא. מעבר עכבר מציג את הטקסט המקורי. אחוז ביטחון של המנוע אינו הבטחה לנכונות.</div>
<table>{head_rows}</table>
<h2>שורות</h2><table><tr>{line_head}</tr>{''.join(line_rows)}</table>
<h2>בדיקות מסמך</h2><ul>{doc_checks or '<li>אין</li>'}</ul></section>
</div><script>{JS}</script></body></html>"""
    out = doc_dir / "report.html"
    out.write_text(page)
    return out


def write_index(data_dir: Path) -> Path:
    """List of all processed documents, linking to each report."""
    rows = []
    for d in sorted((Path(data_dir) / "documents").iterdir()):
        if not (d / "fields.json").exists():
            continue
        f = json.loads((d / "fields.json").read_text())
        m = json.loads((d / "meta.json").read_text())
        statuses = [l[k]["status"] for l in f["lines"] for k, _ in LINE_HE]
        flagged = sum(s in ("unclear", "check_failed") for s in statuses)
        methods = ", ".join(METHOD_HE.get(p["method"], p["method"]) for p in f["pages"])
        rows.append(f'<tr><td><a href="documents/{e(d.name)}/report.html">{e(m["original_filename"])}</a></td>'
                    f'<td>{e(methods)}</td><td>{len(f["lines"])}</td><td>{flagged}</td>'
                    f'<td>{e(" ; ".join(f["warnings"]) or "—")}</td></tr>')
    page = (f'<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>מסמכים שעובדו</title>'
            f'<style>{CSS}</style></head><body><header><h1>מסמכים שעובדו</h1></header><section>'
            f'<table><tr><th>קובץ</th><th>שיטת קריאה</th><th>שורות</th><th>שדות לבדיקה</th><th>אזהרות</th></tr>'
            f'{"".join(rows)}</table></section></body></html>')
    out = Path(data_dir) / "index.html"
    out.write_text(page)
    return out
