import io
import uuid
from contextlib import asynccontextmanager
from PIL import Image, UnidentifiedImageError
from fastapi import FastAPI, BackgroundTasks, UploadFile, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from . import store
from .domain import ROOT, INGREDIENTS, Preferences, Item, Strict, validate_items
from .agent import execute

@asynccontextmanager
async def lifespan(app):
    store.recover()
    yield

app = FastAPI(title="Cook Helper", lifespan=lifespan)
class NewRun(Strict):
    image_id: str | None = None
class Confirmation(Strict):
    preferences_version: int
    items: list[Item] = Field(max_length=100)

@app.get("/api/health")
def health():
    from .llm_client import LLMClient
    try:
        client = LLMClient()
        vision = LLMClient(purpose="vision")
        return {"ok": True, "model": client.model, "reasoning_provider": client.mode, "vision_provider": vision.mode, "vision_model": vision.model}
    except Exception:
        return {"ok": False, "error": "模型配置无效", "vision_provider": "unavailable"}

@app.get("/api/ingredients")
def ingredients():
    return INGREDIENTS

@app.get("/api/preferences")
def preferences():
    return store.preferences()

@app.put("/api/preferences")
def update_preferences(prefs: Preferences):
    if any(i not in INGREDIENTS for i in prefs.excluded_ingredient_ids):
        raise HTTPException(422, "未知忌口食材")
    result = {**prefs.model_dump(), "version": store.preferences()["version"] + 1}
    store.put("preferences", result)
    return result

@app.post("/api/images")
async def upload(file: UploadFile):
    content = await file.read(8 * 1024 * 1024 + 1)
    if len(content) > 8 * 1024 * 1024:
        raise HTTPException(413, "图片不得超过 8MB")
    try:
        with Image.open(io.BytesIO(content)) as img:
            if img.format not in ("JPEG", "PNG", "WEBP") or img.width * img.height > 20_000_000:
                raise ValueError()
            img.load()
            clean = img.convert("RGB")
            clean.thumbnail((1600, 1600))
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(422, "请上传有效的 JPG、PNG 或 WebP 图片（最多 2000 万像素）")
    image_id = str(uuid.uuid4())
    clean.save(store.RUNTIME / (image_id + ".jpg"), "JPEG")
    store.put("image:" + image_id, True)
    return {"image_id": image_id}

@app.get("/api/images/{image_id}")
def image(image_id: str):
    if not store.get("image:" + image_id):
        raise HTTPException(404, "图片不存在")
    return FileResponse(store.RUNTIME / (image_id + ".jpg"))

@app.post("/api/runs")
def new_run(body: NewRun, tasks: BackgroundTasks):
    if body.image_id and not store.get("image:" + body.image_id):
        raise HTTPException(404, "图片不存在")
    prefs = store.preferences()
    run = {"id": str(uuid.uuid4()), "image_id": body.image_id, "preferences_version": prefs["version"],
           "preferences": {k:v for k,v in prefs.items() if k != "version"}, "items": [], "events": [],
           "status": "queued" if body.image_id else "awaiting_confirmation", "result": None}
    store.put("run:" + run["id"], run)
    if body.image_id:
        tasks.add_task(execute, run["id"], "recognize")
    return run

def read_run(run_id):
    run = store.get("run:" + run_id)
    if not run:
        raise HTTPException(404, "任务不存在")
    return run

@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    run = read_run(run_id)
    return {**run, "stale": run["preferences_version"] != store.preferences()["version"]}

@app.post("/api/runs/{run_id}/confirm-inventory")
def confirm(run_id: str, body: Confirmation, tasks: BackgroundTasks):
    run = read_run(run_id)
    if run["status"] != "awaiting_confirmation":
        raise HTTPException(409, "任务当前不能确认")
    if body.preferences_version != run["preferences_version"] or body.preferences_version != store.preferences()["version"]:
        raise HTTPException(409, "偏好已更新，请重新开始")
    try:
        validate_items(body.items)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    run.update(items=[i.model_dump() for i in body.items], status="queued")
    store.put("run:" + run_id, run)
    tasks.add_task(execute, run_id, "match")
    return run

@app.post("/api/runs/{run_id}/cancel")
def cancel(run_id: str):
    run = read_run(run_id)
    if run["status"] not in ("completed", "failed", "cancelled"):
        run["status"] = "cancelled"
        store.put("run:" + run_id, run)
    return run

if (ROOT / "frontend/dist").exists():
    app.mount("/", StaticFiles(directory=ROOT / "frontend/dist", html=True), name="web")
