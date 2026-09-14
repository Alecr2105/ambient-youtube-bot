from __future__ import annotations

from enum import StrEnum


class VideoState(StrEnum):
    PLANNED = "PLANNED"
    RESEARCHING = "RESEARCHING"
    AUDIO_SOURCE_SELECTION = "AUDIO_SOURCE_SELECTION"
    AUDIO_GENERATION = "AUDIO_GENERATION"
    AUDIO_MIXING = "AUDIO_MIXING"
    VISUAL_SELECTION = "VISUAL_SELECTION"
    VIDEO_PROCESSING = "VIDEO_PROCESSING"
    RENDERING = "RENDERING"
    QUALITY_CHECK = "QUALITY_CHECK"
    READY = "READY"
    UPLOADING = "UPLOADING"
    SCHEDULED = "SCHEDULED"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


PIPELINE_ORDER: tuple[VideoState, ...] = tuple(state for state in VideoState if state is not VideoState.FAILED)


class InvalidTransitionError(ValueError):
    pass


def next_state(state: VideoState) -> VideoState | None:
    if state is VideoState.FAILED:
        return None
    index = PIPELINE_ORDER.index(state)
    return PIPELINE_ORDER[index + 1] if index + 1 < len(PIPELINE_ORDER) else None


def check_transition(current: VideoState, target: VideoState, failed_from: VideoState | None = None) -> None:
    if target is VideoState.FAILED:
        if current in (VideoState.FAILED, VideoState.PUBLISHED):
            raise InvalidTransitionError(f"cannot fail from {current}")
        return
    if current is VideoState.FAILED:
        # Resuming re-enters the stage that failed.
        if failed_from is None or target is not failed_from:
            raise InvalidTransitionError(f"FAILED can only resume to {failed_from}, not {target}")
        return
    if next_state(current) is not target:
        raise InvalidTransitionError(f"{current} -> {target} is not allowed")
