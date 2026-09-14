from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.database.migrate import upgrade_to_head
from app.database.models import PublishHistory, Video, YoutubeResult
from app.database.session import make_engine, session_scope
from app.database.states import VideoState
from app.scheduler.selector import performance_factors
from app.youtube.quota import QuotaTracker
from app.youtube.sync import sync_status


@pytest.fixture
def db(make_settings):
    settings = make_settings()
    upgrade_to_head(settings.database_url)
    engine = make_engine(settings.database_url)
    yield settings, engine
    engine.dispose()


class FakeYouTube:
    def __init__(self, items):
        self.items, self.calls = items, []

    def videos(self):
        return self

    def list(self, **kwargs):
        self.calls.append(kwargs)
        ids = kwargs["id"].split(",")
        self._response = {"items": [i for i in self.items if i["id"] in ids]}
        return self

    def execute(self):
        return self._response


def add_video(session, video_id, yt_id, state, slug="gentle_rain", day=date(2026, 9, 1)):
    session.add(Video(id=video_id, mode="production", seed=1, duration_seconds=10800, state=state, target_publish_date=day))
    session.flush()
    session.add(YoutubeResult(video_id=video_id, youtube_video_id=yt_id))
    session.add(PublishHistory(video_id=video_id, ambient_slug=slug, subniche="sleep", published_on=day))


def test_sync_marks_published_and_updates_views(db):
    settings, engine = db
    with session_scope(engine) as session:
        add_video(session, "a", "YT_A", VideoState.SCHEDULED)
        add_video(session, "b", "YT_B", VideoState.SCHEDULED)
        add_video(session, "c", "YT_C", VideoState.PUBLISHED)
    youtube = FakeYouTube([
        {"id": "YT_A", "status": {"privacyStatus": "public"}, "statistics": {"viewCount": "120"}},
        {"id": "YT_B", "status": {"privacyStatus": "private"}, "statistics": {"viewCount": "0"}},
    ])
    counts = sync_status(youtube, engine, QuotaTracker(engine, settings))
    assert counts == {"published": 1, "stats_updated": 2, "missing": 1}
    assert len(youtube.calls) == 1  # batched
    with session_scope(engine) as session:
        assert session.get(Video, "a").state is VideoState.PUBLISHED
        assert session.get(Video, "b").state is VideoState.SCHEDULED
        assert session.scalar(select(PublishHistory.views).where(PublishHistory.video_id == "a")) == 120
    assert QuotaTracker(engine, settings).used("general") == 1


def test_performance_factor_needs_enough_videos_and_is_bounded(db):
    _settings, engine = db
    rows = [("thunderstorm", v) for v in (1000, 1200, 900)] + [("white_noise", v) for v in (100, 120, 80)] + [("fireplace", 100000)]
    with session_scope(engine) as session:
        for i, (slug, views) in enumerate(rows):
            video_id = f"{slug}_{i}"
            session.add(Video(id=video_id, mode="production", seed=1, duration_seconds=10800, state=VideoState.PUBLISHED))
            session.flush()
            session.add(PublishHistory(video_id=video_id, ambient_slug=slug, subniche="sleep", published_on=date(2026, 9, 1 + i % 3), views=views))
    with session_scope(engine) as session:
        factors = performance_factors(session, date(2026, 9, 14))
    assert factors["thunderstorm"] > 1.0 > factors["white_noise"]
    assert 0.8 <= factors["white_noise"] and factors["thunderstorm"] <= 1.3
    assert "fireplace" not in factors  # a single viral video is not a trend
