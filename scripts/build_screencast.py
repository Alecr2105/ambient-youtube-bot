"""Builds the audit screencast from the raw scene recordings and the browser stills.

Every caption is burned in in English, the cards are rendered with the repository font, and
the audio bed is one of the tool's own procedural previews at a low volume.

    python scripts/build_screencast.py --out docs/screencast/ambient_bot_api_walkthrough.mp4
"""

from __future__ import annotations

import argparse
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "docs" / "screencast" / "raw"
STILLS = ROOT / "docs" / "screencast" / "stills"
WORK = ROOT / "docs" / "screencast" / "work"
FONT = ROOT / "assets" / "fonts" / "Montserrat-Variable.ttf"
BACKGROUND = (16, 21, 26)
WIDTH, HEIGHT, FPS = 1920, 1080, 30


@dataclass
class Scene:
    """One segment of the final video."""

    name: str
    caption: str = ""
    source: str | None = None  # file in raw/ or stills/
    start: float = 0.0
    seconds: float = 6.0
    speed: float = 1.0
    card: list[str] = field(default_factory=list)  # title card lines instead of a source


SCENES = [
    Scene(
        "00_title",
        card=[
            "Ambient Bot",
            "How the YouTube Data API is used to upload videos",
            "API client: Ambient Bot  ·  Google Cloud project <número-de-proyecto>",
            "Channel: Costa Rica Ambience",
        ],
        seconds=7,
    ),
    Scene(
        "01_doctor",
        source="01_doctor.mp4",
        start=1.0,
        seconds=13.0,
        caption="A private command-line tool on the channel owner's own computer.\n"
        "MODE=test means nothing is ever published without the owner running the command.",
    ),
    Scene(
        "02_site",
        source="site_home.jpg",
        seconds=6.0,
        caption="Public page for the tool, linked from the OAuth consent screen.",
    ),
    Scene(
        "03_privacy",
        source="site_privacy.jpg",
        seconds=7.0,
        caption="Privacy policy: which YouTube data the tool uses, how tokens are stored,\n"
        "and how the owner can revoke access at any time.",
    ),
    Scene(
        "04_oauth",
        source="02_oauth.mp4",
        start=21.0,
        seconds=13.0,
        caption="OAuth 2.0 installed-app flow (app/youtube/auth.py). The owner signs in, picks the\n"
        "Costa Rica Ambience channel and grants youtube.upload and youtube.",
    ),
    Scene(
        "04b_brand_account",
        source="oauth_brand_account.jpg",
        seconds=7.0,
        caption="The consent flow runs in the owner's browser: the owner picks the brand account\n"
        "of their own channel, Costa Rica Ambience.",
    ),
    Scene(
        "04c_scopes",
        source="oauth_scopes.jpg",
        seconds=7.0,
        caption="The two scopes granted to Ambient Bot: manage the owner's YouTube videos and account.\n"
        "Nothing else is requested.",
    ),
    Scene(
        "05_channel",
        source="02_oauth.mp4",
        start=34.0,
        seconds=9.0,
        caption="channels.list (mine=true) confirms the tool is attached to the owner's own channel.\n"
        "The refresh token is stored outside the code, on the owner's machine only.",
    ),
    Scene(
        "06_upload",
        source="03_upload.mp4",
        start=2.0,
        seconds=16.0,
        caption="One command uploads a video: the tool renders it, then calls videos.insert as a\n"
        "resumable upload (app/youtube/uploader.py). --confirm is required every time.",
    ),
    Scene(
        "07_result",
        source="03_upload.mp4",
        start=20.0,
        seconds=12.0,
        caption="The API returns the video ID. privacyStatus=private, selfDeclaredMadeForKids=false,\n"
        "and the quota used today is counted per bucket (app/youtube/quota.py).",
    ),
    Scene(
        "08_studio",
        source="studio_private.jpg",
        seconds=8.0,
        caption="The same upload in YouTube Studio: visibility Private, on the owner's own channel.",
    ),
    Scene(
        "09_compliance",
        source="05_compliance.mp4",
        start=1.0,
        seconds=15.0,
        caption="Every video keeps a license log for all audio and a quality report before upload.\n"
        "All footage is filmed by the channel owner; no third-party media is ever used.",
    ),
    Scene(
        "10_revoke",
        source="google_connections.jpg",
        seconds=7.0,
        caption="Ambient Bot in the owner's Google Account: access can be removed at any time,\n"
        "exactly as stated in the privacy policy.",
    ),
    Scene(
        "11_end",
        card=[
            "One upload per day",
            "The owner's own videos, on the owner's own channel.",
            "No other users, no other channels, no viewer data.",
            "Default quota is sufficient - no increase is requested.",
        ],
        seconds=8,
    ),
]


def ffmpeg_binary() -> Path:
    return next(Path(r"C:\Users").glob("*/AppData/Local/Microsoft/WinGet/Packages/*FFmpeg*/*/bin/ffmpeg.exe"))


def ffprobe_duration(binary: Path, path: Path) -> float:
    probe = binary.with_name("ffprobe.exe")
    out = subprocess.run(
        [str(probe), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def card_font(size: int, weight: str) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(FONT), size)
    font.set_variation_by_name(weight)
    return font


def render_card(lines: list[str], out: Path) -> Path:
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    styles = [(84, "SemiBold", (240, 246, 242)), (48, "Medium", (176, 214, 192)),
              (36, "Regular", (196, 208, 216)), (36, "Regular", (196, 208, 216))]
    rows = [(line, *styles[min(index, len(styles) - 1)]) for index, line in enumerate(lines)]
    gap = 40
    heights = [size + gap for _, size, _, _ in rows]
    y = (HEIGHT - (sum(heights) - gap)) / 2
    for line, size, weight, color in rows:
        font = card_font(size, weight)
        draw.text((WIDTH / 2, y + size / 2), line, font=font, fill=color, anchor="mm")
        y += size + gap
    image.save(out)
    return out


def caption_filter(text: str, caption_file: Path) -> str:
    """Bottom caption: one drawtext per line at a fixed height, so the band always fits."""
    font = str(FONT).replace("\\", "/").replace(":", "\\:")
    lines = text.split("\n")
    size, step = 38, 54
    band = 44 + step * len(lines)
    filters = [f"drawbox=x=0:y=ih-{band}:w=iw:h={band}:color=0x080b0e@0.88:t=fill"]
    for index, line in enumerate(lines):
        path = caption_file.with_name(f"{caption_file.stem}_{index}.txt")
        path.write_text(line, encoding="utf-8")
        escaped = str(path).replace("\\", "/").replace(":", "\\:")
        offset = band - 26 - step * index
        filters.append(
            f"drawtext=fontfile='{font}':textfile='{escaped}':fontsize={size}:fontcolor=0xF2F6F4:"
            f"borderw=1:bordercolor=0xF2F6F4:x=(w-text_w)/2:y=h-{offset}"
        )
    return ",".join(filters)


def build_scene(binary: Path, scene: Scene, index: int) -> Path:
    out = WORK / f"{index:02d}_{scene.name}.mp4"
    filters = []
    if scene.card:
        card = render_card(scene.card, WORK / f"{index:02d}_{scene.name}.png")
        inputs = ["-loop", "1", "-t", f"{scene.seconds}", "-i", str(card)]
    else:
        source = RAW / scene.source if (RAW / scene.source).exists() else STILLS / scene.source
        if not source.exists():
            raise SystemExit(f"missing source for scene {scene.name}: {scene.source}")
        if source.suffix.lower() in {".jpg", ".jpeg", ".png"}:
            inputs = ["-loop", "1", "-t", f"{scene.seconds}", "-i", str(source)]
        else:
            start = scene.start if scene.start >= 0 else max(ffprobe_duration(binary, source) + scene.start, 0)
            inputs = ["-ss", f"{start}", "-t", f"{scene.seconds}", "-i", str(source)]
            if scene.speed != 1.0:
                filters.append(f"setpts=PTS/{scene.speed}")
    filters.append(
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=0x{BACKGROUND[0]:02x}{BACKGROUND[1]:02x}{BACKGROUND[2]:02x}"
    )
    if scene.caption:
        filters.append(caption_filter(scene.caption, WORK / f"{index:02d}_{scene.name}.txt"))
    filters.append(f"fps={FPS}")
    command = [
        str(binary), "-hide_banner", "-loglevel", "error", "-y", *inputs,
        "-vf", ",".join(filters), "-an",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-video_track_timescale", "30000", str(out),
    ]
    subprocess.run(command, check=True)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "docs" / "screencast" / "ambient_bot_api_walkthrough.mp4"))
    parser.add_argument("--audio", default=str(ROOT / "output" / "previews" / "gentle_rain.flac"))
    parser.add_argument("--audio-db", default="-30")
    parser.add_argument("--only", nargs="*", help="build just these scene names (debugging)")
    args = parser.parse_args()

    binary = ffmpeg_binary()
    WORK.mkdir(parents=True, exist_ok=True)
    scenes = [s for s in SCENES if not args.only or s.name in args.only]
    parts = [build_scene(binary, scene, index) for index, scene in enumerate(SCENES) if scene in scenes]

    listing = WORK / "concat.txt"
    listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    silent = WORK / "silent.mp4"
    subprocess.run(
        [str(binary), "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
         "-i", str(listing), "-c", "copy", str(silent)], check=True,
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    duration = ffprobe_duration(binary, silent)
    subprocess.run(
        [str(binary), "-hide_banner", "-loglevel", "error", "-y", "-i", str(silent),
         "-stream_loop", "-1", "-i", args.audio, "-map", "0:v", "-map", "1:a",
         "-af", f"volume={args.audio_db}dB,afade=t=in:d=2,afade=t=out:st={max(duration - 3, 0)}:d=3",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-t", f"{duration}",
         "-movflags", "+faststart", str(out)], check=True,
    )
    print(f"{out}  {ffprobe_duration(binary, out):.1f}s")
    print("scenes: " + ", ".join(p.stem for p in parts))


if __name__ == "__main__":
    main()
