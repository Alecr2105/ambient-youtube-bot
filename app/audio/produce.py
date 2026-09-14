from __future__ import annotations

import json
import logging
import secrets
import shutil
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import numpy as np

from app.audio.mixer.render import render_program
from app.audio.providers.freesound import FreesoundProvider
from app.audio.providers.own_recordings import OwnRecordingsProvider
from app.audio.recipe import LibrarySpec, instantiate, load_recipe
from app.audio.sources import SourceSelector
from app.database.migrate import is_at_head
from app.database.session import make_engine, session_scope
from app.licensing.report import build_license_report
from app.licensing.validator import LicenseValidator
from app.quality.audio import AudioThresholds, check_audio
from app.quality.report import QualityReport
from app.utils.config import Settings
from app.utils.costs import UsageLedger

log = logging.getLogger(__name__)

MAX_JUNCTIONS_CHECKED = 400


def resolve_duration_minutes(settings: Settings, override: float | None, seed: int) -> float:
    if override is not None:
        return override
    if settings.video_duration is not None:
        return settings.video_duration
    rng = np.random.default_rng(seed)
    return float(rng.integers(settings.video_duration_min, settings.video_duration_max + 1))


def provider_factory(settings: Settings, ledger: UsageLedger):
    def build(name: str, library: LibrarySpec):
        if name == "own":
            return OwnRecordingsProvider(settings.audio_own_dir, library.category)
        if name == "freesound" and "freesound" in settings.audio_providers and settings.freesound_api_key:
            return FreesoundProvider(settings.freesound_api_key, settings.freesound_client_id, settings.freesound_token_path, ledger)
        return None

    return build


def produce_audio(settings: Settings, recipe_slug: str, minutes: float | None = None, seed: int | None = None) -> tuple[Path, QualityReport]:
    recipe = load_recipe(recipe_slug)
    seed = seed if seed is not None else secrets.randbits(32)
    duration_min = resolve_duration_minutes(settings, minutes, seed)
    run_id = f"{recipe.slug}_{datetime.now():%Y%m%d_%H%M%S}_{seed}"
    out_dir = settings.output_dir / "audio_runs" / run_id
    work_dir = settings.work_dir / run_id

    settings.work_dir.mkdir(parents=True, exist_ok=True)
    free_gb = shutil.disk_usage(settings.work_dir).free / 1024**3
    if free_gb < settings.min_free_disk_gb:
        raise RuntimeError(f"only {free_gb:.0f} GB free; MIN_FREE_DISK_GB={settings.min_free_disk_gb}")

    engine = make_engine(settings.database_url)
    if not is_at_head(engine, settings.database_url):
        raise RuntimeError("database has pending migrations; run `python main.py db-upgrade`")
    validator = LicenseValidator(settings.allowed_licenses)
    ledger = UsageLedger(engine, settings.daily_budget, settings.monthly_budget)

    instance = instantiate(recipe, seed)
    started = time.perf_counter()
    with session_scope(engine) as session:
        resolution = SourceSelector(session, provider_factory(settings, ledger), validator, settings.cache_dir, seed).resolve(recipe, instance)
    for note in resolution.notes:
        log.warning(note)
    source_s = time.perf_counter() - started

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "recipe_instance.json").write_text(json.dumps(instance, indent=2), encoding="utf-8")
    license_report = build_license_report(run_id, resolution.resources, validator)
    license_report.write(out_dir / "licenses.json")
    if not license_report.valid:
        raise RuntimeError(f"license gate failed before rendering; see {out_dir / 'licenses.json'}")

    log.info("rendering %s: %.0f min, seed %d -> %s", recipe.slug, duration_min, seed, out_dir)
    try:
        result = render_program(
            recipe, instance, duration_min * 60, settings.audio_sample_rate,
            settings.target_lufs, settings.target_true_peak, work_dir, out_dir / "audio.flac",
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    result.timings["source_selection_s"] = source_s

    junctions = result.junctions_s
    if len(junctions) > MAX_JUNCTIONS_CHECKED:
        junctions = [junctions[i] for i in np.linspace(0, len(junctions) - 1, MAX_JUNCTIONS_CHECKED).astype(int)]
    t_qc = time.perf_counter()
    report = check_audio(
        result.path,
        AudioThresholds(expected_duration_s=duration_min * 60, target_lufs=settings.target_lufs, max_true_peak_dbtp=settings.target_true_peak),
        junctions_s=junctions,
    )
    report.add("licenses", license_report.valid, len(resolution.resources), "all in ALLOWED_LICENSES", "licenses.json")
    result.timings["quality_s"] = time.perf_counter() - t_qc
    result.timings["total_s"] = time.perf_counter() - started
    report.metrics["render"] = {k: v for k, v in asdict(result).items() if k not in ("path", "events", "junctions_s")}
    report.metrics["event_count"] = len(result.events)
    report.metrics["junction_count"] = len(result.junctions_s)
    report.metrics["source_notes"] = resolution.notes
    report.write(out_dir / "quality_report.json")
    (out_dir / "events.json").write_text(json.dumps(result.events, indent=2), encoding="utf-8")
    engine.dispose()

    level = logging.INFO if report.passed else logging.ERROR
    log.log(level, "quality %s in %.0f s: %s", "PASSED" if report.passed else "FAILED", result.timings["total_s"],
            ", ".join(f"{c.name}={c.value}" for c in report.checks))
    return out_dir, report
