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
    print("1. Abrí esta URL, iniciá sesión en Freesound y autorizá la app:\n")
    print("   " + provider.authorize_url() + "\n")
    code = input("2. Pegá aquí el código que muestra Freesound: ")
    provider.exchange_code(code)
    print(f"Token guardado en {settings.freesound_token_path}")
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


def not_yet(phase: int):
    def handler(args: argparse.Namespace) -> int:
        log.error("command '%s' is implemented in phase %d", args.command, phase)
        return 2

    return handler


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

    plan = sub.add_parser("plan", help="choose the next ambients to produce")
    plan.add_argument("--days", type=int, default=None)
    plan.set_defaults(func=not_yet(6))

    produce = sub.add_parser("produce", help="produce a video (resumes from last valid state)")
    target = produce.add_mutually_exclusive_group()
    target.add_argument("--recipe")
    target.add_argument("--video-id")
    produce.add_argument("--minutes", type=int, default=None, help="override duration")
    produce.set_defaults(func=not_yet(6))

    sub.add_parser("run-scheduler", help="run the daily scheduler worker").set_defaults(func=not_yet(6))

    dashboard = sub.add_parser("dashboard", help="start the control panel")
    dashboard.add_argument("--host", default="127.0.0.1")
    dashboard.add_argument("--port", type=int, default=8000)
    dashboard.set_defaults(func=not_yet(7))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    setup_logging(settings.log_dir, settings.log_level)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
