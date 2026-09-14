from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.database.states import VideoState
from app.utils.config import LicenseType


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


state_enum = Enum(VideoState, name="video_state", native_enum=False, length=32)
license_enum = Enum(LicenseType, name="license_type", native_enum=False, length=32)


class Ambient(TimestampMixin, Base):
    __tablename__ = "ambients"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    subniche: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Recipe(TimestampMixin, Base):
    __tablename__ = "recipes"
    __table_args__ = (UniqueConstraint("slug", "checksum"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ambient_id: Mapped[int] = mapped_column(ForeignKey("ambients.id"))
    slug: Mapped[str] = mapped_column(String(64), index=True)
    path: Mapped[str] = mapped_column(String(512))
    checksum: Mapped[str] = mapped_column(String(64))
    content: Mapped[dict[str, Any]] = mapped_column(JSON)


class Video(TimestampMixin, Base):
    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    recipe_id: Mapped[int | None] = mapped_column(ForeignKey("recipes.id"))
    state: Mapped[VideoState] = mapped_column(state_enum, default=VideoState.PLANNED, index=True)
    failed_from_state: Mapped[VideoState | None] = mapped_column(state_enum)
    mode: Mapped[str] = mapped_column(String(16))
    seed: Mapped[int] = mapped_column(Integer)
    duration_seconds: Mapped[int] = mapped_column(Integer)
    target_publish_date: Mapped[date | None] = mapped_column(Date, index=True)
    publish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    combination_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    concept_fingerprint: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    output_path: Mapped[str | None] = mapped_column(String(512))
    stage_timings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    states: Mapped[list[StateLog]] = relationship(back_populates="video", order_by="StateLog.id")


class StateLog(Base):
    __tablename__ = "states_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"), index=True)
    from_state: Mapped[VideoState | None] = mapped_column(state_enum)
    to_state: Mapped[VideoState] = mapped_column(state_enum)
    detail: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    video: Mapped[Video] = relationship(back_populates="states")


class Sound(TimestampMixin, Base):
    __tablename__ = "sounds"
    __table_args__ = (UniqueConstraint("provider", "provider_asset_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    category: Mapped[str] = mapped_column(String(64), index=True)
    type: Mapped[str] = mapped_column(String(16))  # layer | event | bed
    source: Mapped[str] = mapped_column(String(512))
    provider: Mapped[str] = mapped_column(String(32))
    provider_asset_id: Mapped[str] = mapped_column(String(128))
    duration: Mapped[float] = mapped_column(Float)
    sample_rate: Mapped[int] = mapped_column(Integer)
    channels: Mapped[int] = mapped_column(Integer)
    bpm: Mapped[float | None] = mapped_column(Float)
    features: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    restrictions: Mapped[str | None] = mapped_column(Text)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    local_path: Mapped[str] = mapped_column(String(512))
    checksum: Mapped[str] = mapped_column(String(64))

    license: Mapped[License | None] = relationship(back_populates="sound", uselist=False)


class License(TimestampMixin, Base):
    __tablename__ = "licenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    sound_id: Mapped[int] = mapped_column(ForeignKey("sounds.id"), unique=True)
    license_type: Mapped[LicenseType] = mapped_column(license_enum)
    license_url: Mapped[str | None] = mapped_column(String(512))
    source_url: Mapped[str | None] = mapped_column(String(512))
    author: Mapped[str | None] = mapped_column(String(256))
    attribution_required: Mapped[bool] = mapped_column(Boolean, default=False)
    attribution_text: Mapped[str | None] = mapped_column(Text)
    restrictions: Mapped[str | None] = mapped_column(Text)
    terms_snapshot_path: Mapped[str | None] = mapped_column(String(512))
    provenance_notes: Mapped[str | None] = mapped_column(Text)
    verified_by: Mapped[str] = mapped_column(String(16), default="auto")
    blacklisted: Mapped[bool] = mapped_column(Boolean, default=False)
    blacklist_reason: Mapped[str | None] = mapped_column(Text)

    sound: Mapped[Sound] = relationship(back_populates="license")


class VideoSound(Base):
    __tablename__ = "video_sounds"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"), index=True)
    sound_id: Mapped[int] = mapped_column(ForeignKey("sounds.id"), index=True)
    layer: Mapped[str] = mapped_column(String(64))
    seconds_used: Mapped[float] = mapped_column(Float)


class Visual(TimestampMixin, Base):
    __tablename__ = "visuals"

    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(String(512), unique=True)
    type: Mapped[str] = mapped_column(String(16))  # video | image
    duration: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Float)
    tags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    allow_mirror: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    missing: Mapped[bool] = mapped_column(Boolean, default=False)


class VideoVisual(Base):
    __tablename__ = "video_visuals"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"), index=True)
    visual_id: Mapped[int] = mapped_column(ForeignKey("visuals.id"), index=True)
    pattern: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Title(TimestampMixin, Base):
    __tablename__ = "titles"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"), index=True)
    text: Mapped[str] = mapped_column(String(128))
    score: Mapped[float] = mapped_column(Float)
    score_breakdown: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)


class Description(TimestampMixin, Base):
    __tablename__ = "descriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    localizations: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Thumbnail(TimestampMixin, Base):
    __tablename__ = "thumbnails"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"), index=True)
    path: Mapped[str] = mapped_column(String(512))
    source_visual_id: Mapped[int | None] = mapped_column(ForeignKey("visuals.id"))
    text: Mapped[str] = mapped_column(String(64))
    score: Mapped[float] = mapped_column(Float)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)


class ResearchResult(TimestampMixin, Base):
    __tablename__ = "research_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[str | None] = mapped_column(ForeignKey("videos.id"), index=True)
    ambient_slug: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(32))
    term: Mapped[str] = mapped_column(String(256))
    score: Mapped[float] = mapped_column(Float)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class YoutubeResult(TimestampMixin, Base):
    __tablename__ = "youtube_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"), index=True)
    youtube_video_id: Mapped[str | None] = mapped_column(String(32))
    url: Mapped[str | None] = mapped_column(String(256))
    privacy_status: Mapped[str | None] = mapped_column(String(16))
    publish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_body: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    response: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class PublishHistory(TimestampMixin, Base):
    __tablename__ = "publish_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"), index=True)
    ambient_slug: Mapped[str] = mapped_column(String(64), index=True)
    subniche: Mapped[str] = mapped_column(String(32))
    published_on: Mapped[date] = mapped_column(Date, index=True)
    views: Mapped[int | None] = mapped_column(Integer)
    stats_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ErrorLog(TimestampMixin, Base):
    __tablename__ = "errors"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[str | None] = mapped_column(ForeignKey("videos.id"), index=True)
    stage: Mapped[str] = mapped_column(String(64))
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    error_type: Mapped[str] = mapped_column(String(128))
    message: Mapped[str] = mapped_column(Text)
    traceback: Mapped[str | None] = mapped_column(Text)


class ApiCost(TimestampMixin, Base):
    __tablename__ = "api_costs"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[str | None] = mapped_column(ForeignKey("videos.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    operation: Mapped[str] = mapped_column(String(64))
    units: Mapped[float] = mapped_column(Float, default=0.0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class QuotaUsage(TimestampMixin, Base):
    __tablename__ = "quota_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[str | None] = mapped_column(ForeignKey("videos.id"), index=True)
    bucket: Mapped[str] = mapped_column(String(16), index=True)  # general | uploads | search
    method: Mapped[str] = mapped_column(String(64))
    units: Mapped[int] = mapped_column(Integer)
    # YouTube quota resets at midnight Pacific time; store that day for easy sums.
    quota_day: Mapped[date] = mapped_column(Date, index=True)


class Command(TimestampMixin, Base):
    __tablename__ = "commands"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(32))  # pause | resume | produce_now | reindex_visuals
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[str | None] = mapped_column(Text)
