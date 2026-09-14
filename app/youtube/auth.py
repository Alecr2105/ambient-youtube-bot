from __future__ import annotations

import logging
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from app.utils.config import PROJECT_ROOT, Settings

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]


class YouTubeAuthError(RuntimeError):
    pass


def _paths(settings: Settings) -> tuple[Path, Path]:
    secrets, token = settings.youtube_client_secrets_path, settings.youtube_token_path
    if secrets is None or token is None:
        raise YouTubeAuthError("set YOUTUBE_CLIENT_SECRETS_PATH and YOUTUBE_TOKEN_PATH in .env")
    for path in (secrets, token):
        if path.resolve().is_relative_to(PROJECT_ROOT):
            raise YouTubeAuthError(f"{path} is inside the repository; OAuth files must live outside it")
    return secrets, token


def _save(credentials: Credentials, token_path: Path) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = token_path.with_suffix(".tmp")
    tmp.write_text(credentials.to_json(), encoding="utf-8")
    tmp.replace(token_path)


def load_credentials(settings: Settings, interactive: bool = False) -> Credentials:
    """Stored token, refreshed if needed. With `interactive`, runs the browser consent flow
    when there is no usable token; the unattended bot never opens a browser."""
    secrets_path, token_path = _paths(settings)
    credentials = None
    if token_path.exists():
        credentials = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        if not credentials.valid and credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
                _save(credentials, token_path)
            except RefreshError as exc:
                log.warning("stored YouTube token could not be refreshed: %s", exc)
                credentials = None
    if credentials is not None and credentials.valid:
        return credentials
    if not interactive:
        raise YouTubeAuthError("no valid YouTube token; run `python main.py youtube-auth`")
    if not secrets_path.exists():
        raise YouTubeAuthError(f"client secret not found at {secrets_path}")
    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
    credentials = flow.run_local_server(port=0, open_browser=True, prompt="consent", access_type="offline")
    _save(credentials, token_path)
    return credentials


def build_service(credentials: Credentials):
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=credentials, cache_discovery=False)
