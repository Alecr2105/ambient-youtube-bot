"""Indexes the owner's footage in assets/visuals/. Files are only read, never modified."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Visual
from app.video.probe import probe

log = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mov"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
TAGS_FILE = "visuals.yaml"
SAMPLE_BYTES = 8 * 1024 * 1024
MIN_VIDEO_SECONDS = 30.0
#: Shorter clips are animated scenes meant to repeat (e.g. made with Kling from one picture), not
#: footage to cut from. Anything under a couple of seconds cannot hold a seamless loop.
MIN_LOOP_SECONDS = 2.0


@dataclass(frozen=True)
class VisualOptions:
    tags: list[str]
    allow_mirror: bool = False
    exclude: bool = False
    loop: bool = False


@dataclass
class IndexReport:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)


def fingerprint(path: Path) -> str:
    """Size + first and last 8 MB: fast on multi-GB clips, still changes when the file does."""
    size = path.stat().st_size
    digest = hashlib.sha256(str(size).encode())
    with path.open("rb") as f:
        digest.update(f.read(SAMPLE_BYTES))
        if size > 2 * SAMPLE_BYTES:
            f.seek(-SAMPLE_BYTES, 2)
            digest.update(f.read(SAMPLE_BYTES))
    return digest.hexdigest()


def tags_from_name(path: Path) -> list[str]:
    words = re.split(r"[^a-z]+", path.stem.lower())
    return sorted({w for w in words if len(w) > 2 and w not in {"img", "dsc", "mov", "clip", "video", "final", "edit"}})


def load_options(root: Path) -> dict[str, VisualOptions]:
    tags_path = root / TAGS_FILE
    if not tags_path.exists():
        return {}
    data = yaml.safe_load(tags_path.read_text(encoding="utf-8-sig")) or {}
    defaults = data.get("defaults", {})
    options = {}
    for name, entry in (data.get("files") or {}).items():
        entry = {**defaults, **(entry or {})}
        options[name.replace("\\", "/")] = VisualOptions(
            tags=sorted({str(t).lower() for t in entry.get("tags", [])}),
            allow_mirror=bool(entry.get("allow_mirror", False)),
            exclude=bool(entry.get("exclude", False)),
            loop=bool(entry.get("loop", False)),
        )
    return options


def index_visuals(session: Session, root: Path, ffprobe: Path) -> IndexReport:
    report = IndexReport()
    options = load_options(root)
    seen: set[str] = set()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        suffix = path.suffix.lower()
        if suffix not in VIDEO_EXTENSIONS | IMAGE_EXTENSIONS:
            continue
        relative = path.relative_to(root).as_posix()
        option = options.get(relative) or VisualOptions(tags=tags_from_name(path))
        if option.exclude:
            report.skipped[relative] = "excluded in visuals.yaml"
            continue
        try:
            info = probe(ffprobe, path)
        except Exception as exc:  # unreadable files are reported, not fatal
            report.skipped[relative] = f"probe failed: {exc}"
            continue
        kind = "image" if suffix in IMAGE_EXTENSIONS else "video"
        if kind == "video" and (option.loop or (info.duration or 0) < MIN_VIDEO_SECONDS):
            kind = "loop"
            if (info.duration or 0) < MIN_LOOP_SECONDS:
                report.skipped[relative] = f"shorter than {MIN_LOOP_SECONDS:.0f} s, too short to loop"
                continue
        if not option.tags:
            report.skipped[relative] = "no tags (add it to visuals.yaml or use descriptive file names)"
            continue
        seen.add(str(path))
        checksum = fingerprint(path)
        row = session.scalar(select(Visual).where(Visual.path == str(path)))
        values = dict(
            type=kind, duration=info.duration, width=info.width, height=info.height, fps=info.fps,
            tags=option.tags, allow_mirror=option.allow_mirror, checksum=checksum, missing=False,
        )
        if row is None:
            session.add(Visual(path=str(path), **values))
            report.added.append(relative)
        else:
            changed = any(getattr(row, key) != value for key, value in values.items())
            for key, value in values.items():
                setattr(row, key, value)
            if changed:
                report.updated.append(relative)
    session.flush()
    for row in session.scalars(select(Visual).where(Visual.missing.is_(False))):
        if row.path not in seen and Path(row.path).is_relative_to(root):
            row.missing = True
            report.missing.append(row.path)
    return report
