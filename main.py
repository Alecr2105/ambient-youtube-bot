from __future__ import annotations

import argparse
import logging
import sys

from app.utils.config import get_settings
from app.utils.log import setup_logging

log = logging.getLogger("main")


def cmd_doctor(_args: argparse.Namespace) -> int:
    from app.utils.doctor import render, run_checks

    report, healthy = render(run_checks(get_settings()))
    print(report)
    return 0 if healthy else 1


def cmd_db_upgrade(_args: argparse.Namespace) -> int:
    from app.database.migrate import upgrade_to_head

    upgrade_to_head(get_settings().database_url)
    log.info("database migrated to head")
    return 0


def cmd_produce_audio(args: argparse.Namespace) -> int:
    from app.audio.produce import produce_audio
    from app.audio.sources import SourceUnavailableError

    try:
        _out_dir, report = produce_audio(get_settings(), args.recipe, args.minutes, args.seed)
    except SourceUnavailableError as exc:
        log.error("recipe '%s' cannot be produced right now: %s", args.recipe, exc)
        return 1
    return 0 if report.passed else 1


def cmd_check_audio(args: argparse.Namespace) -> int:
    from pathlib import Path

    from app.quality.audio import AudioThresholds, check_audio

    settings = get_settings()
    thresholds = AudioThresholds(
        expected_duration_s=args.minutes * 60 if args.minutes else None,
        target_lufs=settings.target_lufs,
        max_true_peak_dbtp=settings.target_true_peak,
    )
    report = check_audio(Path(args.path), thresholds)
    for check in report.checks:
        print(f"[{'OK  ' if check.passed else 'FAIL'}] {check.name:<20} {check.value} (limit {check.threshold}) {check.detail}")
    return 0 if report.passed else 1


def cmd_list_recipes(_args: argparse.Namespace) -> int:
    from app.audio.recipe import all_recipes

    for recipe in all_recipes():
        state = "" if recipe.enabled else " (disabled)"
        print(f"{recipe.slug:<20} {recipe.name}{state} [{', '.join(recipe.subniches)}]")
    return 0


def _engine():
    from app.database.session import make_engine

    return make_engine(get_settings().database_url)


def cmd_freesound_auth(_args: argparse.Namespace) -> int:
    from app.audio.providers.freesound import FreesoundProvider
    from app.utils.costs import UsageLedger

    settings = get_settings()
    provider = FreesoundProvider(
        settings.freesound_api_key, settings.freesound_client_id, settings.freesound_token_path,
        UsageLedger(_engine(), settings.daily_budget, settings.monthly_budget),
    )
    print("1. Open this URL, sign in to Freesound and authorize the app:\n")
    print("   " + provider.authorize_url() + "\n")
    code = input("2. Paste the code shown by Freesound: ")
    provider.exchange_code(code)
    print(f"Token stored at {settings.freesound_token_path}")
    return 0


def cmd_import_sound(args: argparse.Namespace) -> int:
    from pathlib import Path

    from app.audio import catalog
    from app.audio.providers.base import FetchedSound
    from app.audio.providers.normalize import normalize_to_flac, sha256_file
    from app.audio.providers.own_recordings import manual_candidate
    from app.database.session import session_scope
    from app.licensing.licenses import CANONICAL_URLS, LicenseRecord
    from app.licensing.validator import LicenseValidator
    from app.utils.config import LicenseType

    settings = get_settings()
    source = Path(args.path)
    license_type = LicenseType(args.license)
    candidate = manual_candidate(source, license_type, args.source_url, args.author, args.title)
    target = settings.cache_dir / "sounds" / "manual" / f"{candidate.asset_id}.flac"
    normalize_to_flac(source, target)
    record = LicenseRecord(
        license_type, args.source_url, args.author, license_url=args.license_url or CANONICAL_URLS.get(license_type),
        title=candidate.name, verified_by="manual", provenance_notes=args.notes,
    )
    validator = LicenseValidator(settings.allowed_licenses)
    with session_scope(_engine()) as session:
        sound = catalog.register(session, FetchedSound(candidate, target, sha256_file(source), record), args.category, args.kind, validator)
        print(f"sound {sound.id} registered in category '{args.category}' ({args.kind})")
    return 0


def cmd_list_sounds(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from app.database.models import Sound
    from app.database.session import session_scope

    with session_scope(_engine()) as session:
        query = select(Sound).order_by(Sound.category, Sound.id)
        if args.category:
            query = query.where(Sound.category == args.category)
        for sound in session.scalars(query):
            lic = sound.license
            flag = " BLACKLISTED" if lic and lic.blacklisted else ""
            print(f"{sound.id:>5} {sound.category:<24} {sound.type:<8} {sound.provider:<10} {lic.license_type if lic else '-':<10} {sound.duration:7.1f}s {sound.name[:40]}{flag}")
    return 0


def cmd_blacklist_sound(args: argparse.Namespace) -> int:
    from app.audio import catalog
    from app.database.session import session_scope

    with session_scope(_engine()) as session:
        catalog.blacklist(session, args.sound_id, args.reason)
    print(f"sound {args.sound_id} blacklisted")
    return 0


def cmd_youtube_auth(_args: argparse.Namespace) -> int:
    from app.youtube.auth import build_service, load_credentials
    from app.youtube.quota import QuotaTracker
    from app.youtube.uploader import my_channel

    settings = get_settings()
    print("Opening the browser: choose the YouTube channel this tool publishes to and authorize 'Ambient Bot'.")
    print("If Google shows \"Google hasn't verified this app\", open 'Advanced' > 'Go to Ambient Bot'.")
    credentials = load_credentials(settings, interactive=True)
    channel = my_channel(build_service(credentials), QuotaTracker(_engine(), settings))
    if channel is None:
        log.error("authorized account has no YouTube channel")
        return 1
    print(f"Connected to channel: {channel['snippet']['title']} (https://www.youtube.com/channel/{channel['id']})")
    print(f"Refresh token stored at {settings.youtube_token_path} (outside the repository)")
    return 0


def cmd_youtube_upload_file(args: argparse.Namespace) -> int:
    from pathlib import Path

    from app.youtube.upload_file import upload_single_file

    if not args.confirm:
        log.error("this uploads '%s' to your channel as %s; re-run with --confirm", args.file, args.privacy)
        return 2
    result = upload_single_file(get_settings(), Path(args.file), args.title, args.description, args.privacy, args.tags)
    for key, value in result.items():
        print(f"{key}: {value}")
    return 0 if result["privacy_status"] == args.privacy else 1


def cmd_youtube_test_upload(args: argparse.Namespace) -> int:
    from app.youtube.smoke_upload import run_test_upload

    if not args.confirm:
        log.error("this uploads a PRIVATE 30-second test video to your channel; re-run with --confirm")
        return 2
    result = run_test_upload(get_settings())
    for key, value in result.items():
        print(f"{key}: {value}")
    return 0 if result["privacy_status"] == "private" else 1


def cmd_index_visuals(_args: argparse.Namespace) -> int:
    from app.database.session import session_scope
    from app.utils import ffmpeg
    from app.visuals.indexer import index_visuals

    settings = get_settings()
    ffprobe = ffmpeg.require_binary("ffprobe", settings.ffprobe_path)
    with session_scope(_engine()) as session:
        report = index_visuals(session, settings.visuals_dir, ffprobe)
    print(f"added: {len(report.added)}  updated: {len(report.updated)}  missing: {len(report.missing)}  skipped: {len(report.skipped)}")
    for name, reason in report.skipped.items():
        print(f"  skipped {name}: {reason}")
    return 0


def cmd_visuals(_args: argparse.Namespace) -> int:
    from app.database.session import session_scope
    from app.visuals.coverage import recipe_coverage, render_report

    settings = get_settings()
    with session_scope(_engine()) as session:
        report = recipe_coverage(session)
    print(render_report(report, str(settings.visuals_dir)))
    return 0 if any(coverage.ready for coverage in report) else 1


def cmd_produce_video(args: argparse.Namespace) -> int:
    from pathlib import Path

    from app.video.produce import produce_video
    from app.visuals.matcher import NoMatchingVisualsError

    try:
        _out, report = produce_video(get_settings(), args.recipe, Path(args.audio), args.seed, args.segment_minutes * 60, args.variants)
    except NoMatchingVisualsError as exc:
        log.error("%s; add footage to assets/visuals and run `python main.py index-visuals`", exc)
        return 1
    return 0 if report.passed else 1


def cmd_metadata_preview(args: argparse.Namespace) -> int:
    import secrets

    from app.audio.recipe import load_recipe
    from app.metadata.preview import preview_metadata

    package = preview_metadata(load_recipe(args.recipe), args.minutes, get_settings(), args.seed or secrets.randbits(32), not args.no_research)
    print(f"TITLE: {package.title}\n")
    print("TOP CANDIDATES:")
    for c in package.title_candidates[:5]:
        print(f"  {c['score']:6.2f}  {c['text']}")
    print(f"\nDESCRIPTION:\n{package.description}\n")
    print(f"TAGS ({len(package.tags)}): {', '.join(package.tags)}\n")
    print("RESEARCH:", ", ".join(f"{t['term']} ({t['score']})" for t in package.research_terms[:12]))
    return 0


def _today():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(get_settings().timezone)).date()


def _no_footage(exc: Exception) -> int:
    log.error("%s", exc)
    log.error("run `python main.py visuals` to see what footage each recipe needs, "
              "put your clips in assets/visuals and run `python main.py index-visuals`")
    return 1


def cmd_plan(_args: argparse.Namespace) -> int:
    from app.database.session import session_scope
    from app.scheduler.selector import choose_plan
    from app.scheduler.service import dates_needing_videos
    from app.visuals.matcher import NoMatchingVisualsError

    settings, engine = get_settings(), _engine()
    days = dates_needing_videos(settings, engine, _today())
    if not days:
        print("buffer is full: every day in the next BUFFER_DAYS already has a video")
    for day in days:
        try:
            with session_scope(engine) as session:
                plan = choose_plan(session, settings, day)
        except NoMatchingVisualsError as exc:
            return _no_footage(exc)
        print(f"{day}  {plan.recipe.slug:<20} {plan.duration_seconds // 60} min  publish {plan.publish_at:%Y-%m-%d %H:%M %Z}  score {plan.score}")
    return 0


def cmd_produce(args: argparse.Namespace) -> int:
    from datetime import date

    from app.database.states import VideoState
    from app.scheduler.pipeline import run_production
    from app.scheduler.service import dates_needing_videos, produce_for_day
    from app.visuals.matcher import NoMatchingVisualsError

    settings, engine = get_settings(), _engine()
    if args.video_id:
        return 0 if run_production(settings, engine, args.video_id) is VideoState.READY else 1
    if args.date:
        day = date.fromisoformat(args.date)
    else:
        pending = dates_needing_videos(settings, engine, _today())
        day = pending[0] if pending else _today()
    try:
        video_id = produce_for_day(settings, engine, day, args.recipe)
    except NoMatchingVisualsError as exc:
        return _no_footage(exc)
    if video_id:
        print(f"READY: {video_id} -> {settings.output_dir / video_id}")
    return 0 if video_id else 1


def cmd_upload(args: argparse.Namespace) -> int:
    from app.scheduler.service import upload_ready

    state = upload_ready(get_settings(), _engine(), args.video_id)
    print(f"{args.video_id}: {state.value}")
    return 0


def cmd_run_scheduler(_args: argparse.Namespace) -> int:
    from app.scheduler.service import run_scheduler

    run_scheduler(get_settings(), _engine())
    return 0


def cmd_command(name: str) -> int:
    from app.database.models import Command
    from app.database.session import session_scope

    with session_scope(_engine()) as session:
        session.add(Command(name=name, status="done"))
    print(f"automation {'paused' if name == 'pause' else 'resumed'}")
    return 0


def cmd_videos(_args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from app.database.models import Video
    from app.database.session import session_scope

    with session_scope(_engine()) as session:
        for video in session.scalars(select(Video).order_by(Video.created_at.desc()).limit(30)):
            failed = f" (from {video.failed_from_state.value})" if video.failed_from_state else ""
            print(f"{video.id:<40} {video.state.value:<22}{failed} publish {video.target_publish_date}")
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    import uvicorn

    from app.dashboard.app import create_app

    if args.host not in ("127.0.0.1", "localhost"):
        log.warning("the dashboard has no login; binding to %s exposes it to your network", args.host)
    print(f"Panel en http://{args.host}:{args.port}")
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ambient_youtube_bot")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check environment, FFmpeg/GPU, database and config").set_defaults(func=cmd_doctor)
    sub.add_parser("db-upgrade", help="apply database migrations").set_defaults(func=cmd_db_upgrade)

    sub.add_parser("recipes", help="list ambient recipes").set_defaults(func=cmd_list_recipes)

    produce_audio = sub.add_parser("produce-audio", help="render a mastered audio track from a recipe and run the audio quality gate")
    produce_audio.add_argument("--recipe", required=True)
    produce_audio.add_argument("--minutes", type=float, default=None, help="default: VIDEO_DURATION or random in MIN..MAX")
    produce_audio.add_argument("--seed", type=int, default=None)
    produce_audio.set_defaults(func=cmd_produce_audio)

    check = sub.add_parser("check-audio", help="run the audio quality gate on a file")
    check.add_argument("path")
    check.add_argument("--minutes", type=float, default=None, help="expected duration")
    check.set_defaults(func=cmd_check_audio)

    sub.add_parser("freesound-auth", help="authorize Freesound OAuth2 (needed to download original files)").set_defaults(func=cmd_freesound_auth)

    importer = sub.add_parser("import-sound", help="register a sound you downloaded by hand and vouch for")
    importer.add_argument("path")
    importer.add_argument("--category", required=True)
    importer.add_argument("--kind", choices=["texture", "event"], required=True)
    importer.add_argument("--license", required=True, help="CC0, CC-BY-4.0, OWN, ...")
    importer.add_argument("--source-url", required=True)
    importer.add_argument("--author", required=True)
    importer.add_argument("--license-url", default=None)
    importer.add_argument("--title", default=None)
    importer.add_argument("--notes", default=None, help="why this source is trustworthy")
    importer.set_defaults(func=cmd_import_sound)

    sounds = sub.add_parser("sounds", help="list the sound catalog")
    sounds.add_argument("--category", default=None)
    sounds.set_defaults(func=cmd_list_sounds)

    black = sub.add_parser("blacklist-sound", help="never use a catalog sound again (e.g. after a claim)")
    black.add_argument("sound_id", type=int)
    black.add_argument("--reason", required=True)
    black.set_defaults(func=cmd_blacklist_sound)

    sub.add_parser("index-visuals", help="index your footage in assets/visuals").set_defaults(func=cmd_index_visuals)
    sub.add_parser("visuals", help="which recipes your footage already covers and what to film next").set_defaults(func=cmd_visuals)
    video = sub.add_parser("produce-video", help="build the long video for a recipe from your footage and a rendered audio track")
    video.add_argument("--recipe", required=True)
    video.add_argument("--audio", required=True, help="audio.flac from produce-audio; sets the video duration")
    video.add_argument("--seed", type=int, default=None)
    video.add_argument("--segment-minutes", type=float, default=10.0)
    video.add_argument("--variants", type=int, default=6)
    video.set_defaults(func=cmd_produce_video)

    meta = sub.add_parser("metadata-preview", help="generate English title, description and tags for a recipe")
    meta.add_argument("--recipe", required=True)
    meta.add_argument("--minutes", type=float, default=240)
    meta.add_argument("--seed", type=int, default=None)
    meta.add_argument("--no-research", action="store_true", help="skip YouTube search suggestions")
    meta.set_defaults(func=cmd_metadata_preview)

    sub.add_parser("youtube-auth", help="authorize the bot on your YouTube channel (opens the browser once)").set_defaults(func=cmd_youtube_auth)
    upload_file = sub.add_parser("youtube-upload-file", help="upload one existing video file (e.g. an audit screencast)")
    upload_file.add_argument("--file", required=True)
    upload_file.add_argument("--title", required=True)
    upload_file.add_argument("--description", default="")
    upload_file.add_argument("--privacy", choices=["private", "unlisted", "public"], default="private")
    upload_file.add_argument("--tags", nargs="*", default=[])
    upload_file.add_argument("--confirm", action="store_true")
    upload_file.set_defaults(func=cmd_youtube_upload_file)

    upload_test = sub.add_parser("youtube-test-upload", help="upload a private 30-second test video")
    upload_test.add_argument("--confirm", action="store_true")
    upload_test.set_defaults(func=cmd_youtube_test_upload)

    sub.add_parser("plan", help="show which days need a video and what would be produced (no changes)").set_defaults(func=cmd_plan)

    produce = sub.add_parser("produce", help="plan and produce one video up to READY, or resume one")
    target = produce.add_mutually_exclusive_group()
    target.add_argument("--recipe", help="force a recipe instead of the daily selector")
    target.add_argument("--video-id", help="resume an existing video from its last valid stage")
    produce.add_argument("--date", default=None, help="target publish date YYYY-MM-DD (default: next day that needs a video)")
    produce.set_defaults(func=cmd_produce)

    upload = sub.add_parser("upload", help="upload and schedule a READY video (MODE=production)")
    upload.add_argument("--video-id", required=True)
    upload.set_defaults(func=cmd_upload)

    sub.add_parser("run-scheduler", help="run the daily scheduler worker").set_defaults(func=cmd_run_scheduler)
    sub.add_parser("pause", help="pause automation").set_defaults(func=lambda a: cmd_command("pause"))
    sub.add_parser("resume", help="resume automation").set_defaults(func=lambda a: cmd_command("resume"))
    sub.add_parser("videos", help="list videos and their states").set_defaults(func=cmd_videos)

    dashboard = sub.add_parser("dashboard", help="start the control panel")
    dashboard.add_argument("--host", default="127.0.0.1")
    dashboard.add_argument("--port", type=int, default=8000)
    dashboard.set_defaults(func=cmd_dashboard)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    setup_logging(settings.log_dir, settings.log_level)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
