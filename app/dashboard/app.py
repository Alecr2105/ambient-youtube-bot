"""Local control panel. Binds to 127.0.0.1 by default; it has no login, so never expose it."""

from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from app.database.models import ApiCost, Command, ErrorLog, License, Sound, StateLog, Video, Visual, YoutubeResult
from app.database.session import make_engine, session_scope
from app.database.states import VideoState
from app.scheduler.service import is_paused
from app.utils.config import PROJECT_ROOT, get_settings
from app.utils.costs import UsageLedger
from app.youtube.quota import QuotaTracker

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
EDITABLE_ENV = {
    "VIDEO_DURATION_MIN": r"\d{1,3}",
    "VIDEO_DURATION_MAX": r"\d{1,3}",
    "VIDEO_DURATION": r"(\d{1,3})?",
    "PUBLISH_HOUR": r"([01]?\d|2[0-3])",
    "PUBLISH_WINDOW_MINUTES": r"\d{1,3}",
    "CREATE_HOUR": r"([01]?\d|2[0-3])",
    "ACTIVE_WEEKDAYS": r"(mon|tue|wed|thu|fri|sat|sun)(,(mon|tue|wed|thu|fri|sat|sun))*",
    "BUFFER_DAYS": r"\d{1,2}",
}


def create_app(settings=None, env_path: Path | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = make_engine(settings.database_url)
    env_file = env_path or PROJECT_ROOT / ".env"
    app = FastAPI(title="Ambient Bot", docs_url=None, redoc_url=None)

    def render(request: Request, name: str, **context):
        return TEMPLATES.TemplateResponse(request, name, {"mode": settings.mode.value, **context})

    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request):
        quota = QuotaTracker(engine, settings)
        ledger = UsageLedger(engine, settings.daily_budget, settings.monthly_budget)
        with session_scope(engine) as session:
            counts = dict(session.execute(select(Video.state, func.count()).group_by(Video.state)).all())
            upcoming = session.scalars(select(Video).where(Video.state.not_in([VideoState.PUBLISHED])).order_by(Video.target_publish_date).limit(10)).all()
            errors = session.scalars(select(ErrorLog).order_by(ErrorLog.id.desc()).limit(8)).all()
            commands = session.scalars(select(Command).order_by(Command.id.desc()).limit(6)).all()
            paused = is_paused(session)
            visuals = session.scalar(select(func.count()).select_from(Visual).where(Visual.missing.is_(False)))
        disk = shutil.disk_usage(settings.output_dir if settings.output_dir.exists() else PROJECT_ROOT).free / 1024**3
        return render(
            request, "overview.html",
            counts={s.value: counts.get(s, 0) for s in VideoState}, upcoming=upcoming, errors=errors, commands=commands, paused=paused,
            quota={b: (quota.used(b), quota.limits[b]) for b in ("uploads", "general", "search")},
            spend=(ledger.spent_today(), settings.daily_budget, ledger.spent_this_month(), settings.monthly_budget),
            disk_gb=disk, visuals=visuals,
        )

    @app.get("/videos/{video_id}", response_class=HTMLResponse)
    def video_detail(request: Request, video_id: str):
        with session_scope(engine) as session:
            video = session.get(Video, video_id)
            if video is None:
                raise HTTPException(404)
            log_rows = session.scalars(select(StateLog).where(StateLog.video_id == video_id).order_by(StateLog.id)).all()
            errors = session.scalars(select(ErrorLog).where(ErrorLog.video_id == video_id).order_by(ErrorLog.id.desc())).all()
            youtube = session.scalar(select(YoutubeResult).where(YoutubeResult.video_id == video_id).order_by(YoutubeResult.id.desc()))
        out = settings.output_dir / video_id

        def load(name):
            path = out / name
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

        return render(request, "video.html", video=video, states=log_rows, errors=errors, youtube=youtube,
                      quality=load("quality_report.json"), licenses=load("licenses.json"), metadata=load("metadata.json"),
                      has_thumb=(out / "thumb_selected.jpg").exists())

    @app.get("/videos/{video_id}/thumbnail")
    def thumbnail(video_id: str):
        if not re.fullmatch(r"[\w-]+", video_id):
            raise HTTPException(404)
        path = settings.output_dir / video_id / "thumb_selected.jpg"
        if not path.exists():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/jpeg")

    @app.get("/visuals", response_class=HTMLResponse)
    def visuals_page(request: Request):
        with session_scope(engine) as session:
            rows = session.scalars(select(Visual).order_by(Visual.missing, Visual.path)).all()
        return render(request, "visuals.html", visuals=rows, root=settings.visuals_dir)

    @app.get("/sounds", response_class=HTMLResponse)
    def sounds_page(request: Request):
        with session_scope(engine) as session:
            rows = session.execute(select(Sound, License).join(License, License.sound_id == Sound.id).order_by(Sound.category, Sound.id)).all()
            costs = session.execute(select(ApiCost.provider, func.count(), func.sum(ApiCost.cost_usd)).group_by(ApiCost.provider)).all()
        return render(request, "sounds.html", sounds=rows, costs=costs)

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, saved: int = 0):
        env = _read_env(env_file)
        return render(request, "settings.html", values={k: env.get(k, "") for k in EDITABLE_ENV}, saved=saved)

    @app.post("/settings")
    async def save_settings(request: Request):
        form = await request.form()
        updates = {}
        for key, pattern in EDITABLE_ENV.items():
            value = str(form.get(key, "")).strip().lower() if key == "ACTIVE_WEEKDAYS" else str(form.get(key, "")).strip()
            if not re.fullmatch(pattern, value):
                raise HTTPException(400, f"invalid value for {key}")
            updates[key] = value
        if updates["VIDEO_DURATION_MIN"] and updates["VIDEO_DURATION_MAX"] and int(updates["VIDEO_DURATION_MIN"]) > int(updates["VIDEO_DURATION_MAX"]):
            raise HTTPException(400, "minimum duration exceeds maximum")
        _write_env(env_file, updates)
        return RedirectResponse("/settings?saved=1", status_code=303)

    @app.post("/commands/{name}")
    def command(name: str, video_id: str = Form(""), recipe: str = Form(""), date: str = Form("")):
        allowed = {"pause", "resume", "produce_now", "resume_video", "upload_video", "reindex_visuals"}
        if name not in allowed:
            raise HTTPException(404)
        payload = {k: v for k, v in {"video_id": video_id, "recipe": recipe, "date": date}.items() if v}
        status = "done" if name in ("pause", "resume") else "pending"
        with session_scope(engine) as session:
            session.add(Command(name=name, payload=payload, status=status, processed_at=datetime.now(UTC) if status == "done" else None))
        return RedirectResponse("/", status_code=303)

    return app


def _read_env(path: Path) -> dict[str, str]:
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    return values


def _write_env(path: Path, updates: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen = set()
    for i, line in enumerate(lines):
        key = line.partition("=")[0].strip()
        if key in updates and not line.lstrip().startswith("#"):
            lines[i] = f"{key}={updates[key]}"
            seen.add(key)
    lines += [f"{k}={v}" for k, v in updates.items() if k not in seen]
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)
