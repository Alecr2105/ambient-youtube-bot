from __future__ import annotations

import re
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
SUBNICHES = ("sleep", "study", "relaxation")


class Mode(StrEnum):
    TEST = "test"
    PRODUCTION = "production"


class LicenseType(StrEnum):
    CC0 = "CC0"
    CC_BY_3 = "CC-BY-3.0"
    CC_BY_4 = "CC-BY-4.0"
    OWN = "OWN"
    PROCEDURAL = "PROCEDURAL"
    AI_COMMERCIAL = "AI_COMMERCIAL"


class GpuPolicy(StrEnum):
    AUTO = "auto"
    TRUE = "true"
    FALSE = "false"


class VisualStyle(StrEnum):
    """What the pictures are, so the description never claims more than is true."""

    FOOTAGE = "footage"  # the owner's own footage, filmed in Costa Rica
    ILLUSTRATED = "illustrated"  # illustrated scenes inspired by Costa Rica


class VisualMotion(StrEnum):
    """How much the picture moves. `off` leaves a photo perfectly still, which also means
    it is never enlarged: a 1920x1080 photo reaches the encoder untouched."""

    OFF = "off"
    FULL = "full"


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


def _empty_to_none(value: object) -> object:
    if isinstance(value, str) and not value.strip():
        return None
    return value


CsvList = Annotated[list[str], NoDecode]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    mode: Mode = Mode.TEST
    log_level: str = "INFO"

    videos_per_day: int = Field(1, ge=1, le=10)
    video_duration_min: int = Field(180, ge=1)
    video_duration_max: int = Field(240, ge=1)
    video_duration: int | None = Field(None, ge=1)
    buffer_days: int = Field(2, ge=0, le=14)

    timezone: str = "America/Costa_Rica"
    publish_timezone: str = "America/New_York"
    publish_hour: int = Field(20, ge=0, le=23)
    publish_window_minutes: int = Field(120, ge=0, le=720)
    create_hour: int = Field(1, ge=0, le=23)
    active_weekdays: CsvList = list(WEEKDAYS)

    ffmpeg_path: Path | None = None
    ffprobe_path: Path | None = None

    resolution: str = "1920x1080"
    fps: int = 30
    video_codec: str = "h264"
    video_bitrate: str = "5M"
    # A still picture needs a fraction of the bits of moving footage; used when nothing moves.
    static_video_bitrate: str = "800k"
    #: Animated loops (rain, steam, a lamp): far less change than real footage, more than a photo.
    animated_video_bitrate: str = "2500k"
    visual_motion: VisualMotion = VisualMotion.OFF
    visual_style: VisualStyle = VisualStyle.FOOTAGE
    use_gpu: GpuPolicy = GpuPolicy.AUTO

    audio_sample_rate: int = 48000
    audio_codec: str = "aac"
    audio_bitrate: str = "256k"
    target_lufs: float = Field(-18.0, le=-5, ge=-40)
    # An ambient tagged for studying must not startle: cap on how far the loudest 3 s
    # may sit above the usual level (BS.1770-4 short-term loudness).
    study_max_short_term_jump_lu: float = Field(5.0, gt=0, le=20)
    target_true_peak: float = Field(-1.0, le=0, ge=-10)

    daily_budget: float = Field(0.0, ge=0)
    monthly_budget: float = Field(0.0, ge=0)

    allowed_licenses: Annotated[list[LicenseType], NoDecode] = [
        LicenseType.CC0,
        LicenseType.PROCEDURAL,
        LicenseType.OWN,
    ]

    subniche_weights: Annotated[dict[str, float], NoDecode] = {
        "relaxation": 0.4,
        "sleep": 0.3,
        "study": 0.3,
    }
    enable_es_localization: bool = False
    declare_synthetic_media: bool = False

    youtube_category_id: str = "10"
    youtube_client_secrets_path: Path | None = None
    youtube_token_path: Path | None = None
    youtube_playlist_id: str | None = None
    youtube_quota_general_daily: int = 10000
    youtube_quota_uploads_daily: int = 100
    youtube_quota_search_daily: int = 100

    audio_providers: CsvList = ["own", "freesound"]
    freesound_api_key: str | None = None
    freesound_client_id: str | None = None
    freesound_token_path: Path | None = None

    #: Lofi format: only recipes with a `music` block are produced, with music generated per video
    #: by ACE-Step in a local ComfyUI. Off = the ambience-only recipes, as before.
    music_mode: bool = False
    comfyui_dir: Path = Path(r"C:\ai_tools\ComfyUI_windows_portable")
    comfyui_url: str = "http://127.0.0.1:8188"
    music_track_seconds: float = Field(180.0, ge=60, le=240)
    music_crossfade_seconds: float = Field(4.0, ge=0.5, le=15)
    #: For study: how far the loudest 3 s of the mix may rise above its usual level (music has beats).
    music_max_short_term_jump_lu: float = Field(8.0, gt=0, le=20)
    #: Animated scenes in H.265: about half the size of H.264 at the same quality (and upload time).
    animated_video_codec: str = "h264"

    #: 24/7 live stream server (Oracle Cloud VM). Empty host = no live stream.
    oracle_host: str | None = None
    oracle_user: str = "ubuntu"
    oracle_key_path: Path | None = None
    stream_root: str = "/home/ubuntu/stream"
    #: Programmes (one per produced video) kept on the server and played in rotation.
    stream_keep_programs: int = Field(7, ge=1, le=60)
    #: Constant bitrate of the scene loop; YouTube flags 1080p streams under ~4.5 Mbps.
    stream_video_bitrate: str = "4500k"

    max_retries: int = Field(3, ge=0, le=20)
    backoff_base: float = Field(60.0, gt=0)

    database_url: str = "sqlite:///data/bot.db"
    visuals_dir: Path = Path("assets/visuals")
    audio_own_dir: Path = Path("assets/audio_own")
    output_dir: Path = Path("output")
    cache_dir: Path = Path("cache")
    work_dir: Path = Path("work")
    log_dir: Path = Path("logs")
    retention_days: int = Field(7, ge=0)
    min_free_disk_gb: float = Field(40.0, ge=0)

    @field_validator(
        "video_duration",
        "ffmpeg_path",
        "ffprobe_path",
        "youtube_client_secrets_path",
        "youtube_token_path",
        "youtube_playlist_id",
        "freesound_api_key",
        "freesound_client_id",
        "freesound_token_path",
        "oracle_host",
        "oracle_key_path",
        mode="before",
    )
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        return _empty_to_none(value)

    @field_validator("active_weekdays", "audio_providers", "allowed_licenses", mode="before")
    @classmethod
    def _csv(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("active_weekdays")
    @classmethod
    def _weekdays(cls, value: list[str]) -> list[str]:
        days = [day.lower() for day in value]
        invalid = [day for day in days if day not in WEEKDAYS]
        if invalid:
            raise ValueError(f"invalid weekdays: {invalid}; use {WEEKDAYS}")
        if not days:
            raise ValueError("ACTIVE_WEEKDAYS cannot be empty")
        return days

    @field_validator("subniche_weights", mode="before")
    @classmethod
    def _weights(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        weights: dict[str, float] = {}
        for pair in _split_csv(value):
            name, _, weight = pair.partition(":")
            weights[name.strip().lower()] = float(weight)
        return weights

    @field_validator("subniche_weights")
    @classmethod
    def _normalize_weights(cls, value: dict[str, float]) -> dict[str, float]:
        unknown = set(value) - set(SUBNICHES)
        if unknown:
            raise ValueError(f"unknown subniches: {sorted(unknown)}; use {SUBNICHES}")
        if any(weight < 0 for weight in value.values()) or sum(value.values()) <= 0:
            raise ValueError("SUBNICHE_WEIGHTS must be non-negative with a positive sum")
        total = sum(value.values())
        return {name: weight / total for name, weight in value.items()}

    @field_validator("timezone", "publish_timezone")
    @classmethod
    def _tz(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone: {value}") from exc
        return value

    @field_validator("resolution")
    @classmethod
    def _resolution(cls, value: str) -> str:
        if not re.fullmatch(r"\d{3,4}x\d{3,4}", value):
            raise ValueError("RESOLUTION must look like 1920x1080")
        return value

    @field_validator("fps")
    @classmethod
    def _fps(cls, value: int) -> int:
        if value not in (24, 25, 30, 60):
            raise ValueError("FPS must be 24, 25, 30 or 60")
        return value

    @field_validator("video_codec", "animated_video_codec")
    @classmethod
    def _codec(cls, value: str) -> str:
        value = value.lower()
        if value not in ("h264", "h265"):
            raise ValueError("VIDEO_CODEC must be h264 or h265")
        return value

    @field_validator("audio_codec")
    @classmethod
    def _audio_codec(cls, value: str) -> str:
        value = value.lower()
        if value not in ("aac", "opus"):
            raise ValueError("AUDIO_CODEC must be aac or opus")
        return value

    @model_validator(mode="after")
    def _resolve(self) -> Settings:
        if self.video_duration_min > self.video_duration_max:
            raise ValueError("VIDEO_DURATION_MIN cannot exceed VIDEO_DURATION_MAX")
        for name in ("visuals_dir", "audio_own_dir", "output_dir", "cache_dir", "work_dir", "log_dir"):
            path: Path = getattr(self, name)
            if not path.is_absolute():
                setattr(self, name, PROJECT_ROOT / path)
        if self.database_url.startswith("sqlite:///") and not self.database_url.startswith("sqlite:////"):
            relative = self.database_url.removeprefix("sqlite:///")
            if relative != ":memory:" and not Path(relative).is_absolute():
                self.database_url = f"sqlite:///{(PROJECT_ROOT / relative).as_posix()}"
        return self

    @property
    def width(self) -> int:
        return int(self.resolution.split("x")[0])

    @property
    def height(self) -> int:
        return int(self.resolution.split("x")[1])

    @property
    def paid_operations_allowed(self) -> bool:
        return self.daily_budget > 0 and self.monthly_budget > 0

    def secrets_inside_repo(self) -> list[str]:
        """Names of secret paths that point inside the repository."""
        offenders = []
        for name in ("youtube_client_secrets_path", "youtube_token_path", "freesound_token_path", "oracle_key_path"):
            path: Path | None = getattr(self, name)
            if path is not None and path.resolve().is_relative_to(PROJECT_ROOT):
                offenders.append(name)
        return offenders

    def runtime_dirs(self) -> list[Path]:
        return [self.output_dir, self.cache_dir, self.work_dir, self.log_dir, self.visuals_dir, self.audio_own_dir]


@lru_cache
def get_settings() -> Settings:
    return Settings()
