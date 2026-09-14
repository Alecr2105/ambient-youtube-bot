from __future__ import annotations

import pytest

from app.utils.config import GpuPolicy
from app.utils.ffmpeg import EncoderChoice, parse_encoders, select_video_encoder

ENCODERS_OUTPUT = """Encoders:
 V..... = Video
 ------
 V....D libx264              libx264 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10 (codec h264)
 V....D h264_nvenc           NVIDIA NVENC H.264 encoder (codec h264)
 V....D libx265              libx265 H.265 / HEVC (codec hevc)
 A....D aac                  AAC (Advanced Audio Coding)
"""


def test_parse_encoders_keeps_video_only():
    assert parse_encoders(ENCODERS_OUTPUT) == {"libx264", "h264_nvenc", "libx265"}


def test_gpu_chosen_when_it_works():
    choice = select_video_encoder("h264", GpuPolicy.AUTO, {"libx264", "h264_nvenc"}, lambda _: True)
    assert choice == EncoderChoice("h264_nvenc", gpu=True)


def test_falls_back_to_cpu_when_gpu_encode_fails():
    choice = select_video_encoder("h264", GpuPolicy.AUTO, {"libx264", "h264_nvenc"}, lambda _: False)
    assert choice == EncoderChoice("libx264", gpu=False)


def test_gpu_disabled_never_probes():
    def fail(_name):
        raise AssertionError("should not probe GPU")

    assert select_video_encoder("h265", GpuPolicy.FALSE, {"libx265", "hevc_nvenc"}, fail).name == "libx265"


def test_gpu_required_but_missing_raises():
    with pytest.raises(RuntimeError):
        select_video_encoder("h264", GpuPolicy.TRUE, {"libx264"}, lambda _: True)
