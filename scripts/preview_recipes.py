"""Renders short previews of several recipes and converts them to MP3 for listening.

    python scripts/preview_recipes.py --minutes 3 heavy_rain_window thunderstorm

Each preview goes through the real pipeline (source selection, mixing, mastering, audio
quality gate), so what you hear is what a full-length video would sound like.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run as `python scripts/preview_recipes.py`

from app.audio.produce import produce_audio  # noqa: E402
from app.audio.sources import SourceUnavailableError  # noqa: E402
from app.utils import ffmpeg  # noqa: E402
from app.utils.config import get_settings  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("recipes", nargs="+")
    parser.add_argument("--minutes", type=float, default=3.0)
    parser.add_argument("--out", default=str(ROOT / "output" / "previews_v2"))
    args = parser.parse_args()

    settings = get_settings()
    binary = ffmpeg.require_binary("ffmpeg", settings.ffmpeg_path)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    for slug in args.recipes:
        started = time.perf_counter()
        try:
            run_dir, report = produce_audio(settings, slug, args.minutes)
        except (SourceUnavailableError, RuntimeError) as exc:
            print(f"{slug}: FAILED {exc}", flush=True)
            continue
        flac = run_dir / "audio.flac"
        mp3 = out_dir / f"{slug}.mp3"
        subprocess.run(
            [str(binary), "-hide_banner", "-loglevel", "error", "-y", "-i", str(flac),
             "-c:a", "libmp3lame", "-b:a", "192k", str(mp3)], check=True, timeout=600,
        )
        shutil.copy2(run_dir / "licenses.json", out_dir / f"{slug}.licenses.json")
        print(f"{slug}: {'PASSED' if report.passed else 'FAILED GATE'} in {time.perf_counter() - started:.0f} s "
              f"-> {mp3.name} ({mp3.stat().st_size / 1e6:.1f} MB)", flush=True)


if __name__ == "__main__":
    main()
