from __future__ import annotations

import pytest
from sqlalchemy import inspect, select

from app.database.migrate import is_at_head, upgrade_to_head
from app.database.models import Base, StateLog, Video
from app.database.repository import transition
from app.database.session import make_engine, session_scope
from app.database.states import PIPELINE_ORDER, InvalidTransitionError, VideoState


@pytest.fixture
def engine(make_settings):
    settings = make_settings()
    upgrade_to_head(settings.database_url)
    engine = make_engine(settings.database_url)
    yield engine
    engine.dispose()


def new_video(session) -> Video:
    video = Video(id="v1", mode="test", seed=42, duration_seconds=300)
    session.add(video)
    session.flush()
    return video


def test_migrations_match_models(engine, make_settings):
    assert is_at_head(engine, make_settings().database_url)
    assert set(inspect(engine).get_table_names()) - {"alembic_version"} == set(Base.metadata.tables)


def test_full_happy_path_is_logged(engine):
    with session_scope(engine) as session:
        video = new_video(session)
        for target in PIPELINE_ORDER[1:]:
            transition(session, video, target)
    with session_scope(engine) as session:
        video = session.get(Video, "v1")
        assert video.state is VideoState.PUBLISHED
        assert len(session.scalars(select(StateLog)).all()) == len(PIPELINE_ORDER) - 1


def test_skipping_a_stage_is_rejected(engine):
    with session_scope(engine) as session:
        video = new_video(session)
        with pytest.raises(InvalidTransitionError):
            transition(session, video, VideoState.RENDERING)


def test_failure_records_origin_and_resume_only_to_it(engine):
    with session_scope(engine) as session:
        video = new_video(session)
        transition(session, video, VideoState.RESEARCHING)
        transition(session, video, VideoState.FAILED, detail="network down")
        assert video.failed_from_state is VideoState.RESEARCHING
        with pytest.raises(InvalidTransitionError):
            transition(session, video, VideoState.AUDIO_GENERATION)
        transition(session, video, VideoState.RESEARCHING)
        assert video.failed_from_state is None


def test_published_cannot_fail(engine):
    with session_scope(engine) as session:
        video = new_video(session)
        video.state = VideoState.PUBLISHED
        with pytest.raises(InvalidTransitionError):
            transition(session, video, VideoState.FAILED)
