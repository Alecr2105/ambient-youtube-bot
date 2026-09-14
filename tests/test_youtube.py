from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httplib2
import pytest
from googleapiclient.errors import HttpError

from app.database.migrate import upgrade_to_head
from app.database.session import make_engine
from app.utils.config import PROJECT_ROOT
from app.youtube.auth import YouTubeAuthError, load_credentials
from app.youtube.quota import QuotaExceededError, QuotaTracker, load_costs, quota_day
from app.youtube.uploader import MetadataError, VideoMetadata, build_video_body, upload_video


def meta(**kw) -> VideoMetadata:
    base = dict(title="Heavy Rain on Window for Deep Sleep - 4 Hours", description="Rain sounds.", tags=["rain sounds"], category_id="10")
    base.update(kw)
    return VideoMetadata(**base)


def test_body_is_private_not_for_kids_and_english():
    body = build_video_body(meta(contains_synthetic_media=True))
    assert body["status"] == {"privacyStatus": "private", "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True}
    assert body["snippet"]["defaultLanguage"] == "en"
    assert body["snippet"]["defaultAudioLanguage"] == "en"
    assert body["snippet"]["categoryId"] == "10"
    assert "localizations" not in body


def test_publish_at_is_rfc3339_utc():
    now = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    ny = datetime(2026, 9, 15, 21, 0, tzinfo=__import__("zoneinfo").ZoneInfo("America/New_York"))
    body = build_video_body(meta(publish_at=ny), now=now)
    assert body["status"]["publishAt"] == "2026-09-16T01:00:00Z"


@pytest.mark.parametrize(
    "override",
    [
        {"title": "x" * 101},
        {"title": "   "},
        {"title": "Rain <4 hours>"},
        {"tags": ["rain sounds for sleeping"] * 25},
        {"publish_at": datetime(2020, 1, 1, tzinfo=UTC)},
        {"publish_at": datetime(2030, 1, 1)},
        {"publish_at": datetime(2030, 1, 1, tzinfo=UTC), "privacy_status": "public"},
    ],
)
def test_invalid_metadata_is_rejected(override):
    with pytest.raises(MetadataError):
        build_video_body(meta(**override))


@pytest.fixture
def quota(make_settings):
    settings = make_settings(youtube_quota_uploads_daily=2, youtube_quota_general_daily=60)
    upgrade_to_head(settings.database_url)
    engine = make_engine(settings.database_url)
    yield QuotaTracker(engine, settings)
    engine.dispose()


def test_costs_file_has_verified_values():
    costs = load_costs()
    assert (costs["videos.insert"].bucket, costs["videos.insert"].units) == ("uploads", 1)
    assert (costs["thumbnails.set"].bucket, costs["thumbnails.set"].units) == ("general", 50)


def test_quota_is_tracked_per_bucket_and_enforced(quota):
    quota.record("videos.insert")
    quota.record("thumbnails.set")
    assert quota.used("uploads") == 1 and quota.used("general") == 50
    quota.ensure("videos.insert")
    quota.record("videos.insert")
    with pytest.raises(QuotaExceededError, match="uploads"):
        quota.ensure("videos.insert")
    with pytest.raises(QuotaExceededError, match="general"):
        quota.ensure("thumbnails.set")
    quota.ensure("videos.list")


def test_quota_day_follows_pacific_midnight():
    assert str(quota_day(datetime(2026, 9, 14, 6, 30, tzinfo=UTC))) == "2026-09-13"
    assert str(quota_day(datetime(2026, 9, 14, 7, 30, tzinfo=UTC))) == "2026-09-14"


class FakeStatus:
    def __init__(self, progress):
        self._progress = progress

    def progress(self):
        return self._progress


class FakeRequest:
    def __init__(self, script):
        self.script = list(script)

    def next_chunk(self):
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


class FakeService:
    def __init__(self, script):
        self.request = FakeRequest(script)
        self.calls = []

    def videos(self):
        return self

    def insert(self, **kwargs):
        self.calls.append(kwargs)
        return self.request


def http_error(status):
    return HttpError(httplib2.Response({"status": status}), b"{}")


def test_upload_retries_transient_errors_and_charges_quota_once(quota, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\0" * 1024)
    service = FakeService([(FakeStatus(0.5), None), http_error(503), (None, {"id": "abc123", "status": {"privacyStatus": "private"}})])
    sleeps, progress = [], []
    response = upload_video(service, video, meta(), quota, sleep=sleeps.append, progress=progress.append)
    assert response["id"] == "abc123"
    assert len(sleeps) == 1 and progress == [0.5]
    assert quota.used("uploads") == 1
    call = service.calls[0]
    assert call["part"] == "snippet,status"
    assert call["body"]["status"]["selfDeclaredMadeForKids"] is False


def test_upload_does_not_retry_client_errors(quota, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\0")
    with pytest.raises(HttpError):
        upload_video(FakeService([http_error(403)]), video, meta(), quota, sleep=lambda s: None)


def test_upload_refuses_when_bucket_is_empty(quota, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\0")
    quota.record("videos.insert")
    quota.record("videos.insert")
    service = FakeService([])
    with pytest.raises(QuotaExceededError):
        upload_video(service, video, meta(), quota)
    assert service.calls == []


def test_oauth_files_inside_repo_are_refused(make_settings, tmp_path):
    settings = make_settings(youtube_client_secrets_path=tmp_path / "cs.json", youtube_token_path=PROJECT_ROOT / "token.json")
    with pytest.raises(YouTubeAuthError, match="inside the repository"):
        load_credentials(settings)


def test_unattended_run_never_opens_browser(make_settings, tmp_path):
    settings = make_settings(youtube_client_secrets_path=tmp_path / "cs.json", youtube_token_path=tmp_path / "missing.json")
    with pytest.raises(YouTubeAuthError, match="youtube-auth"):
        load_credentials(settings, interactive=False)
