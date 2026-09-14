from __future__ import annotations

from sqlalchemy.orm import Session

from app.database.models import StateLog, Video
from app.database.states import VideoState, check_transition


def transition(session: Session, video: Video, target: VideoState, detail: str | None = None) -> None:
    check_transition(video.state, target, video.failed_from_state)
    previous = video.state
    if target is VideoState.FAILED:
        video.failed_from_state = previous
    elif previous is VideoState.FAILED:
        video.failed_from_state = None
    video.state = target
    session.add(StateLog(video_id=video.id, from_state=previous, to_state=target, detail=detail))
