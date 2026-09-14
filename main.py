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

    plan = sub.add_parser("plan", help="choose the next ambients to produce")
    plan.add_argument("--days", type=int, default=None)
    plan.set_defaults(func=not_yet(6))

    produce = sub.add_parser("produce", help="produce a video (resumes from last valid state)")
    target = produce.add_mutually_exclusive_group()
    target.add_argument("--recipe")
    target.add_argument("--video-id")
    produce.add_argument("--minutes", type=int, default=None, help="override duration")
    produce.set_defaults(func=not_yet(1))

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
