"""Which OCR model is active, and the history of models.

data/models/registry.json:
  {"active": "stock", "previous": null,
   "models": [{"id": "stock", "lang": "heb+eng", "tessdata": "", "status": "active", ...}]}

A model moves through: trained -> evaluated -> active (-> retired).
Training finishing does NOT activate a model. Activation is a separate,
deliberate step, and the previous model stays available for rollback.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from . import ocr

STOCK = {"id": "stock", "lang": "heb+eng", "tessdata": "", "status": "active",
         "description": "Tesseract stock models (heb + eng), no fine-tuning", "created_at": None,
         "evaluation": None}


def _path(data_dir) -> Path:
    return Path(data_dir) / "models" / "registry.json"


def load(data_dir) -> dict:
    p = _path(data_dir)
    if p.exists():
        return json.loads(p.read_text())
    return {"active": "stock", "previous": None, "models": [dict(STOCK)]}


def save(data_dir, reg):
    p = _path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(reg, ensure_ascii=False, indent=1))


def get(reg, model_id):
    return next((m for m in reg["models"] if m["id"] == model_id), None)


def apply_active(data_dir):
    """Point the OCR wrapper at the active model. Call at startup."""
    reg = load(data_dir)
    m = get(reg, reg["active"]) or STOCK
    ocr.set_model(m["id"], m["lang"], m.get("tessdata", ""))
    return m


def register(data_dir, model_id, lang, tessdata, description, training_info) -> dict:
    reg = load(data_dir)
    if get(reg, model_id):
        raise ValueError(f"model {model_id} already exists")
    m = {"id": model_id, "lang": lang, "tessdata": str(tessdata), "status": "trained",
         "description": description, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "training": training_info, "evaluation": None}
    reg["models"].append(m)
    save(data_dir, reg)
    return m


def record_evaluation(data_dir, model_id, evaluation: dict):
    reg = load(data_dir)
    m = get(reg, model_id)
    m["evaluation"] = evaluation
    if m["status"] == "trained":
        m["status"] = "evaluated"
    save(data_dir, reg)


def activate(data_dir, model_id, user: str, allow_unevaluated=False):
    reg = load(data_dir)
    m = get(reg, model_id)
    if not m:
        raise ValueError("unknown model")
    if m["id"] != "stock" and not m.get("evaluation") and not allow_unevaluated:
        raise ValueError("אי אפשר להפעיל מודל שלא נבדק על מסמכי הבדיקה")
    old = get(reg, reg["active"])
    if old and old["id"] != model_id:
        old["status"] = "evaluated" if old.get("evaluation") or old["id"] == "stock" else old["status"]
        reg["previous"] = old["id"]
    m["status"] = "active"
    m["activated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    m["activated_by"] = user
    reg["active"] = model_id
    save(data_dir, reg)
    apply_active(data_dir)


def rollback(data_dir, user: str):
    reg = load(data_dir)
    if not reg.get("previous"):
        raise ValueError("אין מודל קודם לחזור אליו")
    activate(data_dir, reg["previous"], user, allow_unevaluated=True)
