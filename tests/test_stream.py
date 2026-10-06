from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

import pytest

from app.database.migrate import upgrade_to_head
from app.database.models import Video
from app.database.session import make_engine, session_scope
from app.database.states import VideoState
from app.stream.sync import SENT_FILE, Server, prepare_program, sync_stream
from app.utils import ffmpeg

FFMPEG = ffmpeg.resolve_binary("ffmpeg")
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="FFmpeg not installed")


def produced(out: Path, seconds: int = 6) -> Path:
    """A finished video folder: video.mp4 and audio.flac, as the pipeline leaves them."""
    out.mkdir(parents=True)
    subprocess.run([str(FFMPEG), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=640x360:r=30:d={seconds}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(out / "video.mp4")], check=True, timeout=120)
    subprocess.run([str(FFMPEG), "-v", "error", "-y", "-f", "lavfi", "-i", f"anoisesrc=d={seconds}:a=0.1:r=48000",
                    "-ac", "2", str(out / "audio.flac")], check=True, timeout=120)
    return out


def probe(path: Path, entries: str, extra: tuple[str, ...] = ()) -> str:
    ffprobe = ffmpeg.resolve_binary("ffprobe")
    return subprocess.run([str(ffprobe), "-v", "error", *extra, "-show_entries", entries, "-of", "csv=p=0", str(path)],
                          check=True, capture_output=True, text=True).stdout


def test_program_is_a_live_ready_loop_and_aac_audio(make_settings, tmp_path):
    settings = make_settings()
    program = prepare_program(settings, produced(tmp_path / "20261005_rain_ab12"), tmp_path / "work")
    width, height, rate = probe(program.scene, "stream=width,height,r_frame_rate").strip().split(",")
    assert (width, height, rate) == ("1920", "1080", "30/1")
    keyframes = [float(t.strip(",")) for t in probe(program.scene, "frame=pts_time", ("-skip_frame", "nokey", "-select_streams", "v")).split()]
    assert keyframes == [0.0, 2.0, 4.0, 6.0, 8.0]  # YouTube Live: a keyframe every 2 s
    bitrate = int(probe(program.scene, "format=bit_rate"))
    assert 3_500_000 < bitrate < 5_500_000  # filler keeps a still image at the configured 4.5 Mbps
    assert probe(program.audio, "stream=codec_name").strip() == "aac"
    assert abs(float(probe(program.audio, "format=duration")) - 6) < 0.2


def test_sync_sends_uploaded_videos_once_and_prunes_old_programmes(make_settings, tmp_path, monkeypatch):
    settings = make_settings(oracle_host="203.0.113.7", oracle_key_path=tmp_path / "key", stream_keep_programs=2)
    upgrade_to_head(settings.database_url)
    engine = make_engine(settings.database_url)
    folders = {}
    with session_scope(engine) as session:
        for day, state in ((3, VideoState.PUBLISHED), (4, VideoState.SCHEDULED), (5, VideoState.READY)):
            video_id = f"2026100{day}_rain_000{day}"
            folders[day] = produced(settings.output_dir / video_id, seconds=2)
            session.add(Video(id=video_id, state=state, mode="production", seed=day, duration_seconds=2,
                              target_publish_date=date(2026, 10, day), output_path=str(folders[day])))
    commands = []
    server = Server(settings, run=lambda cmd, timeout: commands.append(cmd))

    assert sync_stream(settings, engine, server) == ["20261003_rain_0003", "20261004_rain_0004"]
    assert sync_stream(settings, engine, server) == []  # already on the server
    engine.dispose()

    assert json.loads((folders[4] / SENT_FILE).read_text())["host"] == "203.0.113.7"
    assert not (folders[5] / SENT_FILE).exists()  # READY: its upload may still fail
    copies = [c for c in commands if c[0] == "scp"]
    assert len(copies) == 2 and copies[1][-1] == "ubuntu@203.0.113.7:/home/ubuntu/stream/incoming/20261004_rain_0004/"
    assert all(c[1:3] == ["-i", str(tmp_path / "key")] or c[2:4] == ["-i", str(tmp_path / "key")] for c in commands)
    remote = [c[-1] for c in commands if c[0] == "ssh"]
    assert "mv '/home/ubuntu/stream/incoming/20261004_rain_0004' '/home/ubuntu/stream/programs/20261004_rain_0004'" in remote[-2]
    assert remote[-1].endswith("head -n -2 | xargs -r rm -rf")


def test_sync_is_off_without_a_host(make_settings):
    assert sync_stream(make_settings(), engine=None) == []


def test_a_running_sync_blocks_a_second_one_until_its_lock_goes_stale(make_settings):
    import os
    import time

    from app.stream.sync import LOCK_FILE, LOCK_STALE_S

    settings = make_settings(oracle_host="203.0.113.7")
    lock = settings.work_dir / LOCK_FILE
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("busy")
    # Skipped before touching the database or the server.
    assert sync_stream(settings, engine=None, server=object()) == []
    assert lock.exists()
    old = time.time() - LOCK_STALE_S - 60
    os.utime(lock, (old, old))  # left behind by a crash
    upgrade_to_head(settings.database_url)
    engine = make_engine(settings.database_url)
    assert sync_stream(settings, engine, Server(settings, run=lambda cmd, timeout: None)) == []
    engine.dispose()
    assert not lock.exists()
