from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import soundfile as sf

from app.audio.recipe import load_recipe
from app.database.session import make_engine, session_scope
from app.quality.video import VideoThresholds, check_video
from app.quality.report import QualityReport
from app.utils import ffmpeg
from app.utils.config import Settings
from app.video.plan import plan_timeline, plan_variants
from app.video.render import VideoFormat, render_video
from app.visuals.matcher import choose_visuals, mark_used

log = logging.getLogger(__name__)

EDGE_S = 4.0


def variant_plan_for(duration: float, body_s: float, variants: int) -> tuple[float, int]:
    """Shorter programs need fewer, shorter segments."""
    body = min(body_s, max(duration / 3, 20.0))
    return body, max(1, min(variants, int(duration // (body + EDGE_S)) or 1))


def produce_video(
    settings: Settings,
    recipe_slug: str,
    audio: Path,
    seed: int | None = None,
    body_s: float = 600.0,
    variants: int = 6,
    out_dir: Path | None = None,
) -> tuple[Path, QualityReport]:
    recipe = load_recipe(recipe_slug)
    seed = seed if seed is not None else secrets.randbits(32)
    duration = sf.info(str(audio)).duration
    run_id = f"{recipe.slug}_{datetime.now():%Y%m%d_%H%M%S}_{seed}"
    out_dir = out_dir or settings.output_dir / "video_runs" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    binary = ffmpeg.require_binary("ffmpeg", settings.ffmpeg_path)
    probe_bin = ffmpeg.require_binary("ffprobe", settings.ffprobe_path)
    encoder = ffmpeg.select_video_encoder(
        settings.video_codec, settings.use_gpu, ffmpeg.list_encoders(binary),
        lambda name: ffmpeg.encoder_works(binary, name, settings.width, settings.height),
    )
    engine = make_engine(settings.database_url)
    with session_scope(engine) as session:
        visuals = choose_visuals(session, recipe.visual_tags)
    sleep = recipe.subniches[0] == "sleep"
    body, variant_count = variant_plan_for(duration, body_s, variants)
    plan = plan_variants(visuals, variant_count, body, EDGE_S, seed, sleep)
    timeline = plan_timeline(duration, body, EDGE_S, variant_count, seed, sleep)
    (out_dir / "visual_plan.json").write_text(
        json.dumps({"visuals": [asdict(v) for v in visuals], "timeline": timeline.order, "body_s": body, "edge_s": EDGE_S,
                    "variants": [asdict(v) for v in plan], "encoder": encoder.name}, indent=2),
        encoding="utf-8",
    )
    fmt = VideoFormat(settings.width, settings.height, settings.fps, settings.video_bitrate, settings.audio_codec, settings.audio_bitrate)
    log.info("rendering %.0f s video: %d variants x %.0f s, %d timeline slots, encoder %s", duration, variant_count, body, len(timeline.order), encoder.name)

    started = time.perf_counter()
    result = render_video(binary, probe_bin, plan, timeline, audio, duration, fmt, encoder, settings.work_dir / f"video_{run_id}", out_dir / "video.mp4", settings.cache_dir)
    result.timings["render_total_s"] = time.perf_counter() - started

    started = time.perf_counter()
    report = check_video(binary, probe_bin, result.path, VideoThresholds(duration, settings.width, settings.height, settings.fps), result.junctions_s)
    result.timings["quality_s"] = time.perf_counter() - started
    report.metrics["timings"] = result.timings
    report.write(out_dir / "video_quality_report.json")
    if report.passed:
        with session_scope(engine) as session:
            mark_used(session, [v.id for v in visuals])
    engine.dispose()
    level = logging.INFO if report.passed else logging.ERROR
    log.log(level, "video quality %s: %s", "PASSED" if report.passed else "FAILED", ", ".join(f"{c.name}={c.value}" for c in report.checks))
    return out_dir, report
