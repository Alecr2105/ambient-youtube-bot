from __future__ import annotations

import platform
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from app.database.migrate import is_at_head
from app.database.session import make_engine
from app.utils import ffmpeg
from app.utils.config import PROJECT_ROOT, Settings

VISUAL_EXTENSIONS = {".mp4", ".mov", ".jpg", ".jpeg", ".png", ".webp"}


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    blocking: bool = True


def run_checks(settings: Settings) -> list[Check]:
    checks = [
        Check("python", sys.version_info >= (3, 11), f"{platform.python_version()}"),
        Check(".env", (PROJECT_ROOT / ".env").exists(), "found" if (PROJECT_ROOT / ".env").exists() else "missing: copy .env.example to .env", blocking=False),
        Check("mode", True, settings.mode.value),
    ]
    checks.extend(_ffmpeg_checks(settings))
    checks.append(_database_check(settings))

    secrets = settings.youtube_client_secrets_path
    token = settings.youtube_token_path
    checks.append(Check("youtube client secret", bool(secrets and secrets.exists()), str(secrets) if secrets and secrets.exists() else "missing", blocking=False))
    checks.append(Check("youtube token", bool(token and token.exists()), "present" if token and token.exists() else "missing: run `python main.py youtube-auth`", blocking=False))

    offenders = settings.secrets_inside_repo()
    checks.append(Check("secrets outside repo", not offenders, "ok" if not offenders else f"move outside repo: {offenders}"))

    for path in settings.runtime_dirs():
        path.mkdir(parents=True, exist_ok=True)
    visuals = [p for p in settings.visuals_dir.rglob("*") if p.suffix.lower() in VISUAL_EXTENSIONS]
    checks.append(Check("visuals", bool(visuals), f"{len(visuals)} files in {settings.visuals_dir}", blocking=False))

    free_gb = shutil.disk_usage(settings.output_dir).free / 1024**3
    checks.append(Check("disk", free_gb >= settings.min_free_disk_gb, f"{free_gb:.0f} GB free (min {settings.min_free_disk_gb:.0f})"))

    budget = "paid calls allowed" if settings.paid_operations_allowed else "zero budget: paid calls disabled"
    checks.append(Check("budget", True, budget))
    checks.append(Check("licenses", True, ",".join(settings.allowed_licenses)))
    return checks


def _ffmpeg_checks(settings: Settings) -> list[Check]:
    binary = ffmpeg.resolve_binary("ffmpeg", settings.ffmpeg_path)
    probe = ffmpeg.resolve_binary("ffprobe", settings.ffprobe_path)
    if binary is None:
        return [Check("ffmpeg", False, "not found")]
    checks = [
        Check("ffmpeg", True, f"{ffmpeg.version(binary)} ({binary})"),
        Check("ffprobe", probe is not None, str(probe) if probe else "not found"),
    ]
    try:
        available = ffmpeg.list_encoders(binary)
        choice = ffmpeg.select_video_encoder(
            settings.video_codec,
            settings.use_gpu,
            available,
            lambda name: ffmpeg.encoder_works(binary, name, settings.width, settings.height),
        )
        kind = "GPU" if choice.gpu else "CPU"
        checks.append(Check("video encoder", True, f"{choice.name} ({kind})"))
    except RuntimeError as exc:
        checks.append(Check("video encoder", False, str(exc)))
    return checks


def _database_check(settings: Settings) -> Check:
    try:
        engine = make_engine(settings.database_url)
        at_head = is_at_head(engine, settings.database_url)
        engine.dispose()
    except Exception as exc:  # doctor must report, not crash
        return Check("database", False, f"{type(exc).__name__}: {exc}")
    detail = "migrated" if at_head else "pending migrations: run `python main.py db-upgrade`"
    return Check("database", at_head, detail)


def render(checks: list[Check]) -> tuple[str, bool]:
    lines = []
    healthy = True
    for check in checks:
        mark = "OK  " if check.ok else ("FAIL" if check.blocking else "WARN")
        healthy &= check.ok or not check.blocking
        lines.append(f"[{mark}] {check.name:<20} {check.detail}")
    return "\n".join(lines), healthy
