"""Resumable production pipeline for one video.

Every stage writes its artifacts under output/<video_id>/ and a marker in .stages/. A stage
whose marker exists is skipped, so re-running a video (after a crash, a retry or a FAILED
state) continues from the last completed stage. Content problems (no footage, no licensed
source, quality gate) fail immediately; infrastructure errors are retried with backoff.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
import traceback
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audio.mixer.render import render_program
from app.audio.recipe import Recipe, instantiate, load_recipe
from app.audio.sources import SourceSelector, SourceUnavailableError, UsedResource
from app.database.models import Description, ErrorLog, Title, Thumbnail as ThumbnailRow, Video, VideoSound, VideoVisual
from app.database.repository import transition
from app.database.session import session_scope
from app.database.states import PIPELINE_ORDER, VideoState
from app.licensing.licenses import LicenseRecord
from app.licensing.report import build_license_report
from app.licensing.validator import LicenseValidator
from app.metadata.builder import MetadataPackage, build_metadata, channel_focus
from app.metadata.preview import research_recipe
from app.quality.audio import AudioThresholds, check_audio
from app.quality.duplicates import Combination, check_duplicates, recent_concept_repeats
from app.quality.gate import license_section, run_gate
from app.quality.metadata import check_metadata
from app.quality.report import QualityReport
from app.quality.video import VideoThresholds, check_video
from app.research.suggest import RankedTerm
from app.thumbnails.generator import generate_thumbnails
from app.utils import ffmpeg
from app.utils.config import LicenseType, Mode, Settings, VisualMotion
from app.utils.costs import UsageLedger
from app.video.plan import is_animated, is_still, plan_timeline, plan_variants
from app.audio.produce import study_jump_limit
from app.video.produce import EDGE_S, bitrate_for, variant_plan_for
from app.video.render import VideoFormat, render_video
from app.visuals.loops import with_prepared_loops
from app.visuals.matcher import NoMatchingVisualsError, VisualChoice, choose_visuals, mark_used

log = logging.getLogger(__name__)

SEGMENT_SECONDS = 600.0
MAX_VARIANTS = 6
MAX_PHOTO_VARIANTS = 8  # a still video changes picture instead of moving, so it can use more segments


class ContentError(RuntimeError):
    """Retrying will not help (no footage, no licensed source, failed quality gate)."""


CONTENT_ERRORS = (ContentError, SourceUnavailableError, NoMatchingVisualsError)


@dataclass
class Context:
    settings: Settings
    engine: Any
    video_id: str
    recipe: Recipe
    seed: int
    duration: float
    publish_date: date

    @property
    def out(self) -> Path:
        return self.settings.output_dir / self.video_id

    @property
    def work(self) -> Path:
        return self.settings.work_dir / self.video_id

    def marker(self, state: VideoState) -> Path:
        return self.out / ".stages" / f"{state.value}.json"

    def read_json(self, name: str) -> Any:
        return json.loads((self.out / name).read_text(encoding="utf-8"))

    def write_json(self, name: str, data: Any) -> None:
        path = self.out / name
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(path)


# --- stages ----------------------------------------------------------------

def _ensure_fresh_concept(ctx: Context) -> list[VisualChoice]:
    with session_scope(ctx.engine) as session:
        visuals = choose_visuals(session, ctx.recipe.visual_tags)
        # New music in every video: the same scene and ambience are a channel look, not a repeat.
        repeats = [] if ctx.recipe.music else recent_concept_repeats(session, ctx.recipe.slug, [v.id for v in visuals], ctx.publish_date, exclude_video_id=ctx.video_id)
    if repeats:
        raise ContentError(f"same recipe and footage used recently by {repeats}; needs other footage or another recipe")
    return visuals


def stage_research(ctx: Context) -> dict:
    _ensure_fresh_concept(ctx)  # fail before any rendering, not at the final gate
    terms = research_recipe(ctx.recipe, ctx.settings)
    ctx.write_json("research.json", [asdict(t) for t in terms])
    return {"terms": len(terms)}


def _resources_to_json(resources: list[UsedResource]) -> list[dict]:
    return [{**{k: v for k, v in asdict(r).items() if k != "license"}, "license": {**asdict(r.license), "license_type": r.license.license_type.value}} for r in resources]


def _resources_from_json(items: list[dict]) -> list[UsedResource]:
    out = []
    for item in items:
        lic = dict(item["license"])
        lic["license_type"] = LicenseType(lic["license_type"])
        out.append(UsedResource(**{**{k: v for k, v in item.items() if k != "license"}, "license": LicenseRecord(**lic)}))
    return out


def stage_audio_sources(ctx: Context) -> dict:
    settings = ctx.settings
    validator = LicenseValidator(settings.allowed_licenses)
    from app.audio.produce import provider_factory

    instance = instantiate(ctx.recipe, ctx.seed)
    with session_scope(ctx.engine) as session:
        ledger = UsageLedger(ctx.engine, settings.daily_budget, settings.monthly_budget, session)
        resolution = SourceSelector(session, provider_factory(settings, ledger), validator, settings.cache_dir, ctx.seed).resolve(ctx.recipe, instance)
    report = build_license_report(ctx.video_id, resolution.resources, validator)
    if not report.valid:
        raise ContentError("license validation failed for selected sources")
    ctx.write_json("recipe_instance.json", instance)
    ctx.write_json("sources.json", {"resources": _resources_to_json(resolution.resources), "notes": resolution.notes})
    report.write(ctx.out / "licenses.json")
    return {"resources": len(resolution.resources), "notes": resolution.notes}


def stage_audio_render(ctx: Context) -> dict:
    s = ctx.settings
    # With music the ambience is a bed: rendered and mastered exactly as on its own, then mixed below the music.
    target = ctx.work / "bed.flac" if ctx.recipe.music else ctx.out / "audio.flac"
    result = render_program(ctx.recipe, ctx.read_json("recipe_instance.json"), ctx.duration, s.audio_sample_rate,
                            s.target_lufs, s.target_true_peak, ctx.work / "audio", target)
    render = {"events": result.events, "junctions_s": result.junctions_s, "timings": result.timings,
              "integrated_lufs": result.integrated_lufs, "true_peak_dbtp": result.true_peak_dbtp}
    if ctx.recipe.music:
        render |= _add_music(ctx, target)
    ctx.write_json("audio_render.json", render)
    return result.timings


def _add_music(ctx: Context, bed: Path) -> dict:
    from app.music import lofi

    s, music = ctx.settings, ctx.recipe.music
    plans = lofi.plan_tracks(ctx.duration, s.music_track_seconds, s.music_crossfade_seconds, music.styles, ctx.seed)
    started = time.perf_counter()
    try:
        tracks, music_junctions = lofi.produce_music(
            ffmpeg.require_binary("ffmpeg", s.ffmpeg_path), s.comfyui_dir, s.comfyui_url, plans, bed, music.bed_below_lu,
            ctx.duration, s.music_track_seconds, s.music_crossfade_seconds, s.target_lufs, s.audio_sample_rate,
            ctx.work / "music", ctx.out / "audio.flac",
        )
    except lofi.MusicError as exc:
        raise ContentError(str(exc)) from exc
    ctx.write_json("music.json", {"model": lofi.MODEL_NAME, "license": lofi.MODEL_LICENSE, "model_url": lofi.MODEL_URL,
                                  "bed_below_lu": music.bed_below_lu, "tracks": lofi.describe(tracks)})
    # The tracks are the channel's own work, made with an Apache-2.0 model: recorded next to the ambience sources.
    sources = ctx.read_json("sources.json")
    music_resources = [
        UsedResource(item=f"music_{t.plan.index:03d}", kind="music", provider="ace-step", asset_id=f"{ctx.video_id}:{t.plan.index}",
                     license=LicenseRecord(LicenseType.OWN, source_url=None, author="Costa Rica Ambience", title=t.plan.style,
                                           provenance_notes=f"generated locally by the channel with {lofi.MODEL_NAME} "
                                                            f"({lofi.MODEL_LICENSE}, {lofi.MODEL_URL}); seed {t.plan.seed}; prompt: {t.plan.tags}"))
        for t in tracks
    ]
    resources = _resources_from_json(sources["resources"]) + music_resources
    sources["resources"] = _resources_to_json(resources)
    ctx.write_json("sources.json", sources)
    validator = LicenseValidator(s.allowed_licenses)
    report = build_license_report(ctx.video_id, resources, validator)
    if not report.valid:
        raise ContentError("license validation failed for the music tracks")
    report.write(ctx.out / "licenses.json")
    return {"music_tracks": len(tracks), "music_junctions_s": music_junctions, "music_s": round(time.perf_counter() - started, 1)}


def stage_audio_check(ctx: Context) -> dict:
    s = ctx.settings
    render = ctx.read_json("audio_render.json")
    junctions = render["junctions_s"]
    if len(junctions) > 400:
        junctions = [junctions[i] for i in np.linspace(0, len(junctions) - 1, 400).astype(int)]
    thresholds = AudioThresholds(expected_duration_s=ctx.duration, target_lufs=s.target_lufs,
                                 max_true_peak_dbtp=s.target_true_peak,
                                 max_short_term_jump_lu=study_jump_limit(ctx.recipe, s))
    if not ctx.recipe.music:
        report = check_audio(ctx.out / "audio.flac", thresholds, junctions)
    else:
        # The ambience bed passes every check it would on its own (loops, clicks, calm)...
        bed = check_audio(ctx.work / "bed.flac", thresholds, junctions)
        # ...and the mix is checked as music: a lofi track repeats its bars by design, so no loop
        # detection; beats rise a little above the usual level, so a wider calm limit.
        music = AudioThresholds(expected_duration_s=ctx.duration, target_lufs=s.target_lufs, max_true_peak_dbtp=s.target_true_peak,
                                max_short_term_jump_lu=s.music_max_short_term_jump_lu, loop_max_correlation=1.01)
        report = check_audio(ctx.out / "audio.flac", music, render.get("music_junctions_s") or None)
        for check in bed.checks:
            report.add(f"bed_{check.name}", check.passed, check.value, check.threshold, check.detail)
    report.write(ctx.out / "audio_report.json")
    if not report.passed:
        raise ContentError(f"audio quality failed: {[c.name for c in report.failures()]}")
    return {"passed": True}


def stage_visual_selection(ctx: Context) -> dict:
    s = ctx.settings
    visuals = _ensure_fresh_concept(ctx)
    visuals = with_prepared_loops(visuals, ffmpeg.require_binary("ffmpeg", s.ffmpeg_path), s.width, s.height, s.fps, s.cache_dir)
    sleep = ctx.recipe.subniches[0] == "sleep"
    moving = s.visual_motion is not VisualMotion.OFF
    one_scene_per_segment = all(v.type in ("image", "loop") for v in visuals)
    body, count = variant_plan_for(ctx.duration, SEGMENT_SECONDS, MAX_PHOTO_VARIANTS if one_scene_per_segment else MAX_VARIANTS)
    variants = plan_variants(visuals, count, body, EDGE_S, ctx.seed, sleep, moving=moving)
    timeline = plan_timeline(ctx.duration, body, EDGE_S, count, ctx.seed, sleep)
    ctx.write_json("visual_plan.json", {"visuals": [asdict(v) for v in visuals], "body_s": body, "edge_s": EDGE_S,
                                        "timeline": timeline.order, "variants": [asdict(v) for v in variants]})
    return {"visuals": [v.path for v in visuals], "variants": count}


def _recent_titles(session: Session, exclude: str) -> list[str]:
    return list(session.scalars(select(Title.text).where(Title.selected.is_(True), Title.video_id != exclude).order_by(Title.created_at.desc()).limit(200)))


def stage_metadata(ctx: Context) -> dict:
    research = [RankedTerm(t["term"], t["score"], tuple(t["sources"])) for t in ctx.read_json("research.json")]
    licenses = json.loads((ctx.out / "licenses.json").read_text(encoding="utf-8"))
    with session_scope(ctx.engine) as session:
        recent = _recent_titles(session, ctx.video_id)
    package = build_metadata(ctx.recipe, ctx.duration / 60, research, recent, ctx.seed, ctx.settings.youtube_category_id,
                             filmed_in_costa_rica=True, attribution=licenses["attribution_block"],
                             enable_es_localization=ctx.settings.enable_es_localization,
                             visual_style=ctx.settings.visual_style.value,
                             focus=channel_focus(ctx.recipe.subniches, ctx.settings.subniche_weights))
    package.write(ctx.out / "metadata.json")
    return {"title": package.title}


def _load_plan(ctx: Context):
    from app.video.plan import Grade, Motion, Piece, SegmentVariant, Timeline

    data = ctx.read_json("visual_plan.json")
    variants = [
        SegmentVariant(v["index"], v["length"], tuple(Piece(**p) for p in v["pieces"]), tuple(v["inner_crossfades"]), Motion(**v["motion"]), Grade(**v["grade"]))
        for v in data["variants"]
    ]
    return data, variants, Timeline(edge=data["edge_s"], body=data["body_s"], order=data["timeline"])


def stage_render_video(ctx: Context) -> dict:
    s = ctx.settings
    binary = ffmpeg.require_binary("ffmpeg", s.ffmpeg_path)
    probe_bin = ffmpeg.require_binary("ffprobe", s.ffprobe_path)
    _data, variants, timeline = _load_plan(ctx)
    codec = s.animated_video_codec if is_animated(variants) else s.video_codec
    encoder = ffmpeg.select_video_encoder(codec, s.use_gpu, ffmpeg.list_encoders(binary), lambda n: ffmpeg.encoder_works(binary, n, s.width, s.height))
    still = is_still(variants)
    fmt = VideoFormat(s.width, s.height, s.fps, bitrate_for(variants, s), s.audio_codec, s.audio_bitrate, still=still)
    result = render_video(binary, probe_bin, variants, timeline, ctx.out / "audio.flac", ctx.duration, fmt, encoder, ctx.work / "video", ctx.out / "video.mp4", s.cache_dir)
    thumb, thumbs = generate_thumbnails(binary, result.path, result.duration, result.junctions_s, ctx.recipe, ctx.duration / 60, ctx.out / "thumbs")
    ctx.write_json("video_render.json", {"junctions_s": result.junctions_s, "timings": result.timings, "encoder": encoder.name,
                                         "thumbnail": {"selected": asdict(thumb) | {"path": str(thumb.path)},
                                                       "variants": [asdict(t) | {"path": str(t.path)} for t in thumbs]}})
    return result.timings


def stage_quality(ctx: Context) -> dict:
    s = ctx.settings
    binary = ffmpeg.require_binary("ffmpeg", s.ffmpeg_path)
    probe_bin = ffmpeg.require_binary("ffprobe", s.ffprobe_path)
    render = ctx.read_json("video_render.json")
    video_report = check_video(binary, probe_bin, ctx.out / "video.mp4", VideoThresholds(ctx.duration, s.width, s.height, s.fps), render["junctions_s"])
    audio_data = ctx.read_json("audio_report.json")
    audio_report = QualityReport("audio")
    for check in audio_data["checks"]:
        audio_report.add(check["name"], check["passed"], check["value"], check["threshold"], check["detail"])

    package = MetadataPackage(**ctx.read_json("metadata.json"))
    metadata_report = check_metadata(package, s.declare_synthetic_media)

    from app.licensing.report import LicenseReport

    licenses = license_section(LicenseReport(json.loads((ctx.out / "licenses.json").read_text(encoding="utf-8"))))
    sources = _resources_from_json(ctx.read_json("sources.json")["resources"])
    plan = ctx.read_json("visual_plan.json")
    combination = Combination(
        ctx.recipe.slug,
        tuple(str(r.sound_id) if r.sound_id is not None else f"procedural:{r.asset_id}" for r in sources),
        tuple(v["id"] for v in plan["visuals"]),
        package.title,
    )
    with session_scope(ctx.engine) as session:
        duplicates = check_duplicates(session, combination, ctx.publish_date, exclude_video_id=ctx.video_id)

    gate = run_gate([audio_report, video_report, metadata_report, licenses, duplicates])
    gate.write(ctx.out / "quality_report.json")
    if not gate.passed:
        raise ContentError(f"quality gate failed: {gate.summary()}")

    with session_scope(ctx.engine) as session:
        video = session.get(Video, ctx.video_id)
        video.combination_hash = combination.hash()
        video.concept_fingerprint = combination.fingerprint()
        video.output_path = str(ctx.out)
        session.add(Title(video_id=ctx.video_id, text=package.title, score=package.title_candidates[0]["score"] if package.title_candidates else 0, selected=True))
        session.add(Description(video_id=ctx.video_id, text=package.description, tags=package.tags))
        selected = render["thumbnail"]["selected"]
        session.add(ThumbnailRow(video_id=ctx.video_id, path=selected["path"], text=" / ".join(selected["lines"]), score=selected["score"], selected=True))
        for resource in sources:
            if resource.sound_id is not None:
                session.add(VideoSound(video_id=ctx.video_id, sound_id=resource.sound_id, layer=resource.item, seconds_used=ctx.duration))
        for visual in plan["visuals"]:
            session.add(VideoVisual(video_id=ctx.video_id, visual_id=visual["id"], pattern={"timeline": plan["timeline"], "seed": ctx.seed}))
        mark_used(session, [v["id"] for v in plan["visuals"]])
    shutil.rmtree(ctx.work, ignore_errors=True)
    return {"passed": True}


STAGES: dict[VideoState, Callable[[Context], dict]] = {
    VideoState.RESEARCHING: stage_research,
    VideoState.AUDIO_SOURCE_SELECTION: stage_audio_sources,
    VideoState.AUDIO_GENERATION: stage_audio_render,
    VideoState.AUDIO_MIXING: stage_audio_check,
    VideoState.VISUAL_SELECTION: stage_visual_selection,
    VideoState.VIDEO_PROCESSING: stage_metadata,
    VideoState.RENDERING: stage_render_video,
    VideoState.QUALITY_CHECK: stage_quality,
}


# --- driver ----------------------------------------------------------------

def _record_error(engine, video_id: str, stage: VideoState, attempt: int, exc: BaseException) -> None:
    with session_scope(engine) as session:
        session.add(ErrorLog(video_id=video_id, stage=stage.value, attempt=attempt, error_type=type(exc).__name__,
                             message=str(exc)[:4000], traceback=traceback.format_exc()[-8000:]))


def _set_state(engine, video_id: str, target: VideoState, detail: str | None = None) -> None:
    with session_scope(engine) as session:
        video = session.get(Video, video_id)
        if video.state is not target:
            transition(session, video, target, detail)


def run_production(settings: Settings, engine, video_id: str, sleep: Callable[[float], None] = time.sleep) -> VideoState:
    """Advance a video through production up to READY. Returns the final state reached."""
    with session_scope(engine) as session:
        video = session.get(Video, video_id)
        if video is None:
            raise KeyError(video_id)
        if video.state is VideoState.FAILED:
            transition(session, video, video.failed_from_state, "resume after failure")
        state = video.state
        recipe = load_recipe(_recipe_slug(session, video))
        ctx = Context(settings, engine, video_id, recipe, video.seed, float(video.duration_seconds), video.target_publish_date or date.today())
    ctx.out.mkdir(parents=True, exist_ok=True)
    (ctx.out / ".stages").mkdir(exist_ok=True)

    stages = [s for s in PIPELINE_ORDER if s in STAGES]
    for stage in stages:
        if PIPELINE_ORDER.index(stage) < PIPELINE_ORDER.index(state) and ctx.marker(stage).exists():
            continue
        if state is not stage:
            _set_state(engine, video_id, stage)
            state = stage
        if ctx.marker(stage).exists():
            continue
        attempt = 0
        while True:
            started = time.perf_counter()
            try:
                result = STAGES[stage](ctx)
                ctx.marker(stage).write_text(json.dumps({"result": result, "seconds": round(time.perf_counter() - started, 1)}, default=str), encoding="utf-8")
                _store_timing(engine, video_id, stage, time.perf_counter() - started)
                break
            except CONTENT_ERRORS as exc:
                _record_error(engine, video_id, stage, attempt, exc)
                _set_state(engine, video_id, VideoState.FAILED, f"{type(exc).__name__}: {exc}"[:2000])
                log.error("video %s failed at %s (not retryable): %s", video_id, stage.value, exc)
                return VideoState.FAILED
            except Exception as exc:
                _record_error(engine, video_id, stage, attempt, exc)
                if attempt >= settings.max_retries:
                    _set_state(engine, video_id, VideoState.FAILED, f"{type(exc).__name__}: {exc}"[:2000])
                    log.error("video %s failed at %s after %d retries: %s", video_id, stage.value, attempt, exc)
                    return VideoState.FAILED
                delay = settings.backoff_base * 2**attempt
                attempt += 1
                log.warning("stage %s of %s failed (%s); retry %d/%d in %.0f s", stage.value, video_id, exc, attempt, settings.max_retries, delay)
                sleep(delay)
    _set_state(engine, video_id, VideoState.READY, "quality gate passed")
    if settings.mode is Mode.TEST:
        log.info("video %s READY (test mode: nothing is uploaded) -> %s", video_id, ctx.out)
    return VideoState.READY


def _recipe_slug(session: Session, video: Video) -> str:
    from app.database.models import Recipe as RecipeRow

    return session.get(RecipeRow, video.recipe_id).slug


def _store_timing(engine, video_id: str, stage: VideoState, seconds: float) -> None:
    with session_scope(engine) as session:
        video = session.get(Video, video_id)
        video.stage_timings = {**(video.stage_timings or {}), stage.value: round(seconds, 1)}
