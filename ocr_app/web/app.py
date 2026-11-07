"""Employee web interface (Stage 3) and reviewer screens (Stage 4).

Run:  OCR_DATA=data REVIEWER_PIN=1234 uvicorn ocr_app.web.app:app --host 0.0.0.0 --port 8000
Then open http://<server>:8000 on a computer or phone in the same network.

Everything runs on this server. Nothing is sent to outside services.
"""
import json
import os
import queue
import threading
import traceback
from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import handoff, models, pages, pipeline, review, training

DATA = Path(os.environ.get("OCR_DATA", "data")).resolve()
PIN = os.environ.get("REVIEWER_PIN", "1234")
MAX_UPLOAD_MB = 40
HERE = Path(__file__).parent

app = FastAPI(title="קליטת חשבוניות")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
(DATA / "documents").mkdir(parents=True, exist_ok=True)
models.apply_active(DATA)

# ---------------------------------------------------------------- processing
jobs: "queue.Queue[str]" = queue.Queue()


def _status_path(doc_id):
    return DATA / "documents" / doc_id / "status.json"


def set_status(doc_id, state, message, done=0, total=0):
    _status_path(doc_id).write_text(json.dumps(
        {"state": state, "message": message, "done": done, "total": total}, ensure_ascii=False))


def get_status(doc_id):
    p = _status_path(doc_id)
    return json.loads(p.read_text()) if p.exists() else {"state": "unknown", "message": ""}


def worker():
    while True:
        doc_id = jobs.get()
        doc_dir = DATA / "documents" / doc_id
        try:
            set_status(doc_id, "processing", "מתחיל עיבוד")
            pipeline.process_dir(doc_dir, progress=lambda m, d, t: set_status(doc_id, "processing", m, d, t))
            (doc_dir / "review.json").unlink(missing_ok=True)  # a re-read starts a fresh review
            set_status(doc_id, "ready", "העיבוד הסתיים")
        except Exception as e:  # show a plain message, keep details in the log
            traceback.print_exc()
            set_status(doc_id, "error", "לא הצלחנו לקרוא את הקובץ. נסו להעלות אותו שוב או לצלם שוב.",)
            (doc_dir / "error.log").write_text(traceback.format_exc() + f"\n{e}")
        finally:
            jobs.task_done()


threading.Thread(target=worker, daemon=True).start()
for d in (DATA / "documents").iterdir():  # resume work interrupted by a restart
    st = get_status(d.name)
    if st["state"] in ("queued", "processing"):
        set_status(d.name, "queued", "ממתין לעיבוד")
        jobs.put(d.name)


# ---------------------------------------------------------------- helpers
def doc_dir_of(doc_id: str) -> Path:
    d = (DATA / "documents" / doc_id).resolve()
    if d.parent != (DATA / "documents") or not d.exists():
        raise HTTPException(404, "המסמך לא נמצא")
    return d


def user_of(request: Request) -> str:
    return unquote(request.cookies.get("user_name") or "") or "לא ידוע"


def is_reviewer(request: Request) -> bool:
    return request.cookies.get("reviewer") == PIN


def doc_summary(d: Path) -> dict:
    meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").exists() else {}
    st = get_status(d.name)
    s = {"id": d.name, "name": meta.get("original_filename", d.name), "uploaded": meta.get("uploaded_at", ""),
         "state": st["state"], "message": st.get("message", ""), "review": None, "supplier": None, "number": None}
    if (d / "review.json").exists():
        r = json.loads((d / "review.json").read_text())
        s["review"] = r["status"]
        s["supplier"] = r["header"].get("supplier_name", {}).get("value")
        s["number"] = r["header"].get("document_number", {}).get("value")
    elif (d / "fields.json").exists():
        f = json.loads((d / "fields.json").read_text())
        s["supplier"] = f["header"]["supplier_name"]["value"]
        s["number"] = f["header"]["document_number"]["value"]
        s["review"] = "not_reviewed"
    return s


async def read_uploads(files: list[UploadFile]) -> list[tuple[str, bytes]]:
    out = []
    for f in files:
        if not f.filename:
            continue
        data = await f.read()
        if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(413, f"הקובץ {f.filename} גדול מדי (מעל {MAX_UPLOAD_MB}MB)")
        try:
            pages.kind_of(Path(f.filename))
        except ValueError:
            raise HTTPException(400, f"אפשר להעלות רק PDF או תמונה (JPG, PNG, HEIC). הקובץ {f.filename} לא נתמך.")
        out.append((f.filename, data))
    if not out:
        raise HTTPException(400, "לא נבחר קובץ")
    return out


# ---------------------------------------------------------------- employee pages
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    docs = sorted((doc_summary(d) for d in (DATA / "documents").iterdir() if (d / "meta.json").exists()),
                  key=lambda s: s["uploaded"], reverse=True)[:50]
    return templates.TemplateResponse(request, "home.html", {"docs": docs, "user": user_of(request),
                                                             "reviewer": is_reviewer(request)})


@app.post("/upload")
async def upload(files: list[UploadFile] = File(...)):
    data = await read_uploads(files)
    doc_id, doc_dir, dups = pages.new_document(data, DATA)
    if dups:
        (doc_dir / "duplicates.json").write_text(json.dumps(dups))
    set_status(doc_id, "queued", "ממתין לעיבוד")
    jobs.put(doc_id)
    return RedirectResponse(f"/doc/{doc_id}", status_code=303)


@app.post("/doc/{doc_id}/add_page")
async def add_page(doc_id: str, files: list[UploadFile] = File(...)):
    d = doc_dir_of(doc_id)
    for name, data in await read_uploads(files):
        pages.add_original(d, name, data)
    set_status(doc_id, "queued", "ממתין לעיבוד (נוסף עמוד)")
    jobs.put(doc_id)
    return RedirectResponse(f"/doc/{doc_id}", status_code=303)


@app.get("/doc/{doc_id}", response_class=HTMLResponse)
def doc_page(request: Request, doc_id: str):
    d = doc_dir_of(doc_id)
    st = get_status(doc_id)
    if st["state"] != "ready" or not (d / "fields.json").exists():
        return templates.TemplateResponse(request, "status.html", {"doc_id": doc_id, "status": st})
    return templates.TemplateResponse(request, "review.html", {"doc_id": doc_id, "user": user_of(request),
                                                               "reviewer": is_reviewer(request)})


@app.get("/api/doc/{doc_id}/status")
def api_status(doc_id: str):
    doc_dir_of(doc_id)
    return get_status(doc_id)


@app.get("/api/doc/{doc_id}")
def api_doc(doc_id: str):
    d = doc_dir_of(doc_id)
    r = review.load(d)
    page_infos = []
    for pf in sorted((d / "raw").glob("page-*.json")):
        p = json.loads(pf.read_text())
        q = p.get("quality") or {}
        page_infos.append({"page": p["page"], "image": f"/files/{doc_id}/{p['coordinates_refer_to']}",
                           "original_image": f"/files/{doc_id}/{p['page_image']}", "method": p["method"],
                           "preprocessing": p["preprocessing"], "quality_warnings": q.get("warnings", [])})
    meta = json.loads((d / "meta.json").read_text())
    dups = json.loads((d / "duplicates.json").read_text()) if (d / "duplicates.json").exists() else []
    return {"review": r, "pages": page_infos, "meta": meta, "duplicates": dups,
            "math_warnings": review.check_reviewed(r)}


@app.post("/api/doc/{doc_id}/review")
async def api_review(request: Request, doc_id: str):
    d = doc_dir_of(doc_id)
    body = await request.json()
    r = review.load(d)
    if r["status"] == "confirmed" and body.get("action") != "reopen":
        return JSONResponse({"ok": False, "errors": ["המסמך כבר אושר. כדי לשנות, לחצו 'פתיחה מחדש'."]}, 409)
    if body.get("action") == "reopen":
        r["status"] = "draft"
        r["history"].append({"at": review.now(), "by": user_of(request), "target": "status", "old": "confirmed",
                             "new": "draft"})
        review.save(d, r)
        return {"ok": True}
    errors = review.apply_changes(r, body.get("changes", {}), user_of(request))
    if errors:
        return JSONResponse({"ok": False, "errors": errors}, 400)
    if body.get("action") == "confirm":
        r["status"] = "confirmed"
        r["confirmed_by"], r["confirmed_at"] = user_of(request), review.now()
    elif r["status"] == "not_reviewed":
        r["status"] = "draft"
    review.save(d, r)
    if body.get("action") == "confirm":
        # Prepare exact-text examples for the reviewer (Stage 4). Done here so it
        # does not slow down the first reading of the document.
        threading.Thread(target=training.generate_candidates, args=(d, DATA, r), daemon=True).start()
    return {"ok": True, "status": r["status"], "math_warnings": review.check_reviewed(r)}


def _download(content, media, filename):
    return Response(content, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@app.get("/doc/{doc_id}/export.{fmt}")
def export(doc_id: str, fmt: str):
    """One document. JSON is the documented hand-off format (docs/DATA_FORMAT.md)."""
    doc = handoff.build(doc_dir_of(doc_id))
    name = f"document_{doc_id}"
    if fmt == "json":
        return _download(json.dumps(doc, ensure_ascii=False, indent=1), "application/json", f"{name}.json")
    if fmt == "csv":
        return _download(handoff.to_csv(handoff.line_rows(doc)), "text/csv; charset=utf-8", f"{name}.csv")
    if fmt == "xlsx":
        return _download(handoff.excel_for([doc]), XLSX, f"{name}.xlsx")
    raise HTTPException(404)


@app.get("/export/confirmed.{fmt}")
def export_confirmed(fmt: str, since: str | None = None):
    """All confirmed documents, for employees (Excel/CSV) or other systems (JSONL)."""
    docs = list(handoff.iter_documents(DATA, "confirmed", since))
    if fmt == "xlsx":
        return _download(handoff.excel_for(docs), XLSX, "confirmed_documents.xlsx")
    if fmt == "csv":
        rows = [r for d in docs for r in handoff.line_rows(d)]
        return _download(handoff.to_csv(rows), "text/csv; charset=utf-8", "confirmed_lines.csv")
    if fmt == "jsonl":
        body = "".join(json.dumps(d, ensure_ascii=False) + "\n" for d in docs)
        return _download(body, "application/x-ndjson", "confirmed_documents.jsonl")
    raise HTTPException(404)


@app.get("/api/handoff")
def api_handoff_list(status: str = "confirmed", since: str | None = None):
    """Hand-off interface for later components (product matching, RAG).
    status: confirmed | draft | not_reviewed | any. since: ISO date/time."""
    docs = list(handoff.iter_documents(DATA, None if status == "any" else status, since))
    return {"schema": handoff.SCHEMA_NAME, "schema_version": handoff.SCHEMA_VERSION,
            "count": len(docs), "documents": docs}


@app.get("/api/handoff/{doc_id}")
def api_handoff_one(doc_id: str):
    return handoff.build(doc_dir_of(doc_id))


@app.get("/files/{doc_id}/{sub}/{name}")
def files(doc_id: str, sub: str, name: str):
    d = doc_dir_of(doc_id)
    if sub not in ("pages", "processed", "original"):
        raise HTTPException(404)
    p = (d / sub / name).resolve()
    if p.parent != (d / sub).resolve() or not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


@app.post("/set_user")
def set_user(name: str = Form(...)):
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("user_name", quote(name.strip()[:40]), max_age=3600 * 24 * 365)
    return resp


# ---------------------------------------------------------------- reviewer (Stage 4)
@app.post("/reviewer/login")
def reviewer_login(pin: str = Form(...)):
    resp = RedirectResponse("/training", status_code=303)
    if pin == PIN:
        resp.set_cookie("reviewer", PIN, max_age=3600 * 12)
    return resp


def require_reviewer(request: Request):
    if not is_reviewer(request):
        raise HTTPException(403, "המסך הזה מיועד לבודק מורשה")


@app.get("/training", response_class=HTMLResponse)
def training_page(request: Request, status: str = "suggested", doc: str | None = None):
    if not is_reviewer(request):
        return templates.TemplateResponse(request, "login.html", {})
    exs = training.list_examples(DATA, status=status or None, doc_id=doc)[:60]
    reg = models.load(DATA)
    training_status = getattr(training, "get_training_status", lambda *_: {"state": "idle", "message": "אין עבודה פעילה כרגע"})(DATA)
    return templates.TemplateResponse(request, "training.html", {
        "examples": exs, "status": status, "counters": training.counters(DATA), "registry": reg,
        "user": user_of(request), "doc": doc, "training_status": training_status})


@app.get("/api/training/status")
def api_training_status():
    reg = models.load(DATA)
    st = training.get_training_status(DATA)
    st["active_model"] = reg["active"]
    st["model_count"] = len(reg["models"])
    return st


@app.get("/training/crop/{eid}")
def crop(request: Request, eid: str):
    require_reviewer(request)
    p = (DATA / "training" / "crops" / f"{eid}.png").resolve()
    if p.parent != (DATA / "training" / "crops").resolve() or not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


@app.post("/api/examples/{eid}")
async def api_example(request: Request, eid: str):
    require_reviewer(request)
    body = await request.json()
    try:
        ex = training.update_example(DATA, eid, body.get("text"), body.get("action", "save"), user_of(request))
    except (ValueError, FileNotFoundError) as e:
        return JSONResponse({"ok": False, "error": str(e)}, 400)
    return {"ok": True, "status": ex["status"]}


@app.post("/training/generate/{doc_id}")
def gen(request: Request, doc_id: str):
    require_reviewer(request)
    d = doc_dir_of(doc_id)
    n = training.generate_candidates(d, DATA, review.load(d))
    return RedirectResponse(f"/training?doc={doc_id}&status=suggested&made={n}", status_code=303)


@app.post("/training/export")
def export_training(request: Request):
    require_reviewer(request)
    m = training.export_tesstrain(DATA, DATA / "training" / "export")
    return JSONResponse({"ok": True, "counts": m["counts"], "folder": str(DATA / "training" / "export")})


@app.post("/models/{model_id}/activate")
def activate(request: Request, model_id: str):
    require_reviewer(request)
    try:
        models.activate(DATA, model_id, user_of(request))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return RedirectResponse("/training", status_code=303)


@app.post("/models/rollback")
def rollback(request: Request):
    require_reviewer(request)
    try:
        models.rollback(DATA, user_of(request))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return RedirectResponse("/training", status_code=303)
