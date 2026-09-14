from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.dashboard.app import create_app
from app.database.migrate import upgrade_to_head
from app.database.models import Command, ErrorLog, Video
from app.database.session import make_engine, session_scope
from app.database.states import VideoState


@pytest.fixture
def client(make_settings, tmp_path):
    settings = make_settings()
    upgrade_to_head(settings.database_url)
    env = tmp_path / ".env"
    env.write_text("MODE=test\nVIDEO_DURATION_MIN=180\nVIDEO_DURATION_MAX=240\nYOUTUBE_TOKEN_PATH=C:/secret/token.json\n", encoding="utf-8")
    engine = make_engine(settings.database_url)
    with session_scope(engine) as session:
        session.add(Video(id="20260920_gentle_rain_ab12", mode="test", seed=1, duration_seconds=10800, target_publish_date=date(2026, 9, 20),
                          state=VideoState.FAILED, failed_from_state=VideoState.RENDERING))
        session.flush()
        session.add(ErrorLog(video_id="20260920_gentle_rain_ab12", stage="RENDERING", error_type="RenderError", message="ffmpeg failed"))
    yield TestClient(create_app(settings, env_path=env)), engine, env
    engine.dispose()


def test_pages_render(client):
    http, _engine, _env = client
    for path in ("/", "/visuals", "/sounds", "/settings", "/videos/20260920_gentle_rain_ab12"):
        response = http.get(path)
        assert response.status_code == 200, path
    home = http.get("/").text
    assert "20260920_gentle_rain_ab12" in home and "ffmpeg failed" in home and "Reintentar" in home


def test_commands_are_queued_for_the_worker(client):
    http, engine, _env = client
    assert http.post("/commands/pause", follow_redirects=False).status_code == 303
    http.post("/commands/resume_video", data={"video_id": "20260920_gentle_rain_ab12"})
    http.post("/commands/produce_now", data={"recipe": "gentle_rain"})
    assert http.post("/commands/delete_everything").status_code == 404
    with session_scope(engine) as session:
        rows = [(c.name, c.status, c.payload) for c in session.scalars(select(Command).order_by(Command.id))]
    assert rows == [("pause", "done", {}), ("resume_video", "pending", {"video_id": "20260920_gentle_rain_ab12"}), ("produce_now", "pending", {"recipe": "gentle_rain"})]
    assert "Automatización en pausa" in http.get("/").text


def test_settings_only_writes_whitelisted_valid_values(client):
    http, _engine, env = client
    form = {"VIDEO_DURATION_MIN": "150", "VIDEO_DURATION_MAX": "210", "VIDEO_DURATION": "", "PUBLISH_HOUR": "21",
            "PUBLISH_WINDOW_MINUTES": "60", "CREATE_HOUR": "2", "ACTIVE_WEEKDAYS": "mon,wed,fri", "BUFFER_DAYS": "3",
            "MODE": "production"}
    assert http.post("/settings", data=form, follow_redirects=False).status_code == 303
    text = env.read_text(encoding="utf-8")
    assert "VIDEO_DURATION_MIN=150" in text and "ACTIVE_WEEKDAYS=mon,wed,fri" in text
    assert "MODE=test" in text and "YOUTUBE_TOKEN_PATH=C:/secret/token.json" in text

    bad = dict(form, PUBLISH_HOUR="25")
    assert http.post("/settings", data=bad).status_code == 400
    swapped = dict(form, VIDEO_DURATION_MIN="300")
    assert http.post("/settings", data=swapped).status_code == 400


def test_thumbnail_route_rejects_path_tricks(client):
    http, _engine, _env = client
    assert http.get("/videos/..%2F..%2Fsecret/thumbnail").status_code == 404
