"""Feeds the 24/7 live stream on the Oracle Cloud VM (see deploy/stream/).

The VM has no GPU and a single small CPU, so it never encodes: for every produced video the
laptop prepares a "programme" -- a 10 s scene loop at a constant bitrate with a keyframe every
2 s (what YouTube Live asks for) and the audio in AAC -- and copies it over SSH. The VM only
repeats the loop under the audio and forwards the packets to YouTube.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.database.models import Video
from app.database.session import session_scope
from app.database.states import VideoState
from app.utils import ffmpeg
from app.utils.config import Settings

log = logging.getLogger(__name__)

SENT_FILE = "stream_sent.json"
SCENE_SECONDS = 10
SCENE_FRAME_AT_S = 600.0  # well past the fade-in
AUDIO_BITRATE = "128k"
#: Only videos that went to YouTube; a READY video may still fail its upload.
STREAMABLE_STATES = (VideoState.SCHEDULED, VideoState.PUBLISHED)


@dataclass(frozen=True)
class Program:
    name: str
    scene: Path
    audio: Path


def stream_enabled(settings: Settings) -> bool:
    return bool(settings.oracle_host)


def _run(cmd: list[str], timeout: float) -> None:
    subprocess.run(cmd, check=True, timeout=timeout, capture_output=True)


def _duration(settings: Settings, path: Path) -> float:
    ffprobe = ffmpeg.require_binary("ffprobe", settings.ffprobe_path)
    out = subprocess.run(
        [str(ffprobe), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True, timeout=60,
    ).stdout
    return float(out.strip())


def animated_scene(out_dir: Path) -> Path | None:
    """The seamless loop the video was made from, if it used one (see app/visuals/loops.py)."""
    try:
        plan = json.loads((out_dir / "visual_plan.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for variant in plan.get("variants", []):
        for piece in variant.get("pieces", []):
            if piece.get("loop") and Path(piece["path"]).exists():
                return Path(piece["path"])
    return None


def prepare_program(settings: Settings, out_dir: Path, work: Path) -> Program:
    """Scene loop and AAC audio for one produced video (video.mp4 + audio.flac in `out_dir`)."""
    binary = str(ffmpeg.require_binary("ffmpeg", settings.ffmpeg_path))
    work.mkdir(parents=True, exist_ok=True)
    frame, scene, audio = work / "scene.png", work / "scene.mp4", work / "audio.m4a"
    loop = animated_scene(out_dir)
    if loop is not None:
        source = ["-i", str(loop)]  # already seamless, 1080p30: the stream repeats it as it is
        tune = []
    else:
        video = out_dir / "video.mp4"
        at = min(SCENE_FRAME_AT_S, _duration(settings, video) / 2)
        _run([binary, "-v", "error", "-y", "-ss", f"{at:.1f}", "-i", str(video), "-frames:v", "1", str(frame)], 120)
        source = ["-loop", "1", "-framerate", "30", "-i", str(frame), "-t", str(SCENE_SECONDS)]
        tune = ["-tune", "stillimage"]
    rate = settings.stream_video_bitrate
    _run([
        binary, "-v", "error", "-y", *source,
        "-vf", "fps=30,scale=1920:1080:flags=lanczos,format=yuv420p",
        "-c:v", "libx264", "-preset", "medium", *tune, "-profile:v", "high",
        # A still image compresses to almost nothing; filler keeps the bitrate YouTube expects.
        "-b:v", rate, "-minrate", rate, "-maxrate", rate, "-bufsize", rate,
        "-x264-params", "nal-hrd=cbr:filler=1:keyint=60:min-keyint=60:scenecut=0",
        "-an", "-movflags", "+faststart", str(scene),
    ], 600)
    _run([binary, "-v", "error", "-y", "-i", str(out_dir / "audio.flac"), "-vn", "-c:a", "aac", "-b:a", AUDIO_BITRATE,
          "-ar", "48000", "-movflags", "+faststart", str(audio)], 3600)
    frame.unlink(missing_ok=True)
    return Program(out_dir.name, scene, audio)


class Server:
    """The VM, reached with the system OpenSSH client (ships with Windows 10+)."""

    def __init__(self, settings: Settings, run=_run):
        self.target = f"{settings.oracle_user}@{settings.oracle_host}"
        self.root = settings.stream_root.rstrip("/")
        self.options = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=30", "-o", "StrictHostKeyChecking=accept-new"]
        if settings.oracle_key_path:
            self.options = ["-i", str(settings.oracle_key_path), *self.options]
        self.run = run

    def ssh(self, command: str, timeout: float = 120) -> None:
        self.run(["ssh", *self.options, self.target, command], timeout)

    def send(self, program: Program, keep: int) -> None:
        # Upload into incoming/ and move into programs/ in one step: the player never sees half a file.
        incoming, final = f"{self.root}/incoming/{program.name}", f"{self.root}/programs/{program.name}"
        self.ssh(f"rm -rf '{incoming}' && mkdir -p '{incoming}' '{self.root}/programs'")
        self.run(["scp", "-q", *self.options, str(program.scene), str(program.audio), f"{self.target}:{incoming}/"], 7200)
        self.ssh(f"rm -rf '{final}' && mv '{incoming}' '{final}'")
        # Names start with the publish date, so the oldest programmes sort first.
        self.ssh(f"cd '{self.root}/programs' && ls -1d */ | head -n -{keep} | xargs -r rm -rf")


def was_sent(out_dir: Path) -> bool:
    return (out_dir / SENT_FILE).exists()


LOCK_FILE = "stream_sync.lock"
#: A sync of a week of programmes takes well under this; an older lock was left by a crash.
LOCK_STALE_S = 3 * 3600


def _acquire(lock: Path) -> bool:
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        if time.time() - lock.stat().st_mtime > LOCK_STALE_S:
            lock.unlink(missing_ok=True)
    except FileNotFoundError:
        pass
    try:
        with lock.open("x", encoding="utf-8") as f:
            f.write(datetime.now(UTC).isoformat())
        return True
    except FileExistsError:
        return False


def sync_stream(settings: Settings, engine, server: Server | None = None) -> list[str]:
    """Sends every uploaded video that the live stream does not have yet; returns their ids."""
    if not stream_enabled(settings):
        return []
    # The worker and `main.py stream-sync` must not send the same programme at the same time.
    lock = settings.work_dir / LOCK_FILE
    if not _acquire(lock):
        log.info("live stream: another sync is running; skipping")
        return []
    try:
        return _sync(settings, engine, server or Server(settings))
    finally:
        lock.unlink(missing_ok=True)


def _sync(settings: Settings, engine, server: Server) -> list[str]:
    with session_scope(engine) as session:
        rows = session.scalars(
            select(Video).where(Video.state.in_(STREAMABLE_STATES)).order_by(Video.target_publish_date)
        ).all()
        candidates = [Path(v.output_path) for v in rows if v.output_path]
    # Only the newest ones matter: older programmes would be pruned right away.
    pending = [d for d in candidates if (d / "video.mp4").exists() and (d / "audio.flac").exists() and not was_sent(d)]
    pending = pending[-settings.stream_keep_programs:]
    sent = []
    for out_dir in pending:
        work = settings.work_dir / f"stream_{out_dir.name}"
        try:
            program = prepare_program(settings, out_dir, work)
            server.send(program, settings.stream_keep_programs)
        except (subprocess.SubprocessError, OSError) as exc:
            detail = getattr(exc, "stderr", b"") or b""
            log.error("live stream: could not send %s: %s %s", out_dir.name, exc, detail.decode(errors="replace")[-500:])
            break
        finally:
            shutil.rmtree(work, ignore_errors=True)
        (out_dir / SENT_FILE).write_text(json.dumps({"sent_at": datetime.now(UTC).isoformat(), "host": settings.oracle_host}), encoding="utf-8")
        log.info("live stream: %s added to the rotation", out_dir.name)
        sent.append(out_dir.name)
    return sent
