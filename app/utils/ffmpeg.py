from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.utils.config import GpuPolicy

GPU_ENCODERS = {
    "h264": ("h264_nvenc", "h264_qsv", "h264_amf", "h264_vaapi", "h264_videotoolbox"),
    "h265": ("hevc_nvenc", "hevc_qsv", "hevc_amf", "hevc_vaapi", "hevc_videotoolbox"),
}
CPU_ENCODERS = {"h264": "libx264", "h265": "libx265"}

_ENCODER_LINE = re.compile(r"^\s*V[\w.]{5}\s+([\w-]+)\s")


class FFmpegNotFoundError(RuntimeError):
    pass


@dataclass(frozen=True)
class EncoderChoice:
    name: str
    gpu: bool


def resolve_binary(name: str, configured: Path | None = None) -> Path | None:
    if configured is not None:
        return configured if configured.exists() else None
    found = shutil.which(name)
    if found:
        return Path(found)
    # winget installs update PATH only for new shells, so look in its package store too.
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        packages = Path(local_appdata) / "Microsoft" / "WinGet" / "Packages"
        matches = sorted(packages.glob(f"*FFmpeg*/*/bin/{name}.exe"), reverse=True)
        if matches:
            return matches[0]
    return None


def require_binary(name: str, configured: Path | None = None) -> Path:
    path = resolve_binary(name, configured)
    if path is None:
        raise FFmpegNotFoundError(f"{name} not found; install FFmpeg or set {name.upper()}_PATH")
    return path


def version(binary: Path) -> str:
    result = subprocess.run([str(binary), "-hide_banner", "-version"], capture_output=True, text=True, timeout=30, check=True)
    return result.stdout.splitlines()[0]


def parse_encoders(output: str) -> set[str]:
    return {match.group(1) for line in output.splitlines() if (match := _ENCODER_LINE.match(line))}


def list_encoders(ffmpeg: Path) -> set[str]:
    result = subprocess.run([str(ffmpeg), "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=30, check=True)
    return parse_encoders(result.stdout)


def encoder_works(ffmpeg: Path, encoder: str, width: int = 1920, height: int = 1080) -> bool:
    """A listed encoder can still fail (no driver / no device), so do a real 1-second encode."""
    command = [
        str(ffmpeg), "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate=30",
        "-t", "1", "-c:v", encoder, "-f", "null", "-",
    ]
    try:
        return subprocess.run(command, capture_output=True, timeout=60).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def select_video_encoder(
    codec: str,
    policy: GpuPolicy,
    available: set[str],
    works: Callable[[str], bool],
) -> EncoderChoice:
    if policy is not GpuPolicy.FALSE:
        for encoder in GPU_ENCODERS[codec]:
            if encoder in available and works(encoder):
                return EncoderChoice(encoder, gpu=True)
        if policy is GpuPolicy.TRUE:
            raise RuntimeError(f"USE_GPU=true but no working GPU encoder for {codec}")
    cpu = CPU_ENCODERS[codec]
    if cpu not in available:
        raise RuntimeError(f"FFmpeg build lacks {cpu}")
    return EncoderChoice(cpu, gpu=False)
