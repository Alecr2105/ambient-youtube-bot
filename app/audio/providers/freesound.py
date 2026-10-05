"""Freesound APIv2 provider (https://freesound.org/docs/api/).

Search uses the API key; downloading the original file requires an OAuth2 access token
(24 h lifetime, refreshed automatically). Limits: 60 requests/min, 2000/day.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.audio.providers.base import FetchedSound, ProviderUnavailableError, SoundCandidate, SoundQuery, SoundUnusableError
from app.audio.providers.normalize import normalize_to_flac, sha256_file
from app.licensing.licenses import CANONICAL_URLS, LicenseRecord, normalize_license

log = logging.getLogger(__name__)

API = "https://freesound.org/apiv2"
AUTHORIZE_URL = f"{API}/oauth2/authorize/"
TOKEN_URL = f"{API}/oauth2/access_token/"
FIELDS = "id,name,tags,description,username,license,url,type,samplerate,channels,duration,filesize,num_downloads,avg_rating,num_ratings"
PER_MINUTE = 60
PER_DAY = 2000
PAGE_SIZE = 50
# A 5-minute original at 96 kHz can be 500 MB, and downloading one pool took 23 minutes.
# The program is rendered at 48 kHz, so nothing above that is worth the wait.
MAX_FILESIZE_BYTES = 60_000_000
MAX_SAMPLE_RATE = 48000

Transport = Callable[[urllib.request.Request], bytes]


def broaden(text: str) -> list[str]:
    """Freesound's text search requires every word to match, so a long phrase often returns
    nothing at all ("rain on window glass" has no results; "rain window" has hundreds).
    Try the phrase as written, then its two strongest words, then just the first one."""
    words = text.split()
    tries = [text, " ".join(words[:2]), words[0]] if words else []
    return list(dict.fromkeys(t for t in tries if t))


def default_transport(request: urllib.request.Request) -> bytes:
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


@dataclass
class OAuthToken:
    access_token: str
    refresh_token: str
    expires_at: float

    @classmethod
    def from_response(cls, data: dict[str, Any]) -> OAuthToken:
        return cls(data["access_token"], data["refresh_token"], time.time() + float(data.get("expires_in", 86400)) - 300)


class Usage:
    """Counter backed by the api_costs ledger (or any object with the same two methods)."""

    def __init__(self, ledger):
        self.ledger = ledger

    def check(self) -> None:
        if self.ledger.requests_today("freesound") >= PER_DAY:
            raise ProviderUnavailableError("freesound daily request limit reached")
        while self.ledger.requests_last_minute("freesound") >= PER_MINUTE:
            time.sleep(2)

    def record(self, operation: str) -> None:
        self.ledger.record("freesound", operation)


class FreesoundProvider:
    name = "freesound"

    def __init__(self, api_key: str | None, client_id: str | None, token_path: Path | None, ledger, transport: Transport = default_transport):
        self.api_key = api_key
        self.client_id = client_id
        self.token_path = token_path
        self.usage = Usage(ledger)
        self.transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _call(self, operation: str, request: urllib.request.Request) -> bytes:
        self.usage.check()
        try:
            body = self.transport(request)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 429):
                raise ProviderUnavailableError(f"freesound {operation}: HTTP {exc.code}") from exc
            raise
        except urllib.error.URLError as exc:
            raise ProviderUnavailableError(f"freesound unreachable: {exc.reason}") from exc
        finally:
            self.usage.record(operation)
        return body

    def search(self, query: SoundQuery) -> list[SoundCandidate]:
        if not self.configured:
            raise ProviderUnavailableError("FREESOUND_API_KEY not set")
        for text in broaden(query.text):
            found = self._search_once(text, query)
            if found:
                if text != query.text:
                    log.info("freesound: %r found nothing, used %r instead", query.text, text)
                return found
        return []

    def _search_once(self, text: str, query: SoundQuery) -> list[SoundCandidate]:
        params = {
            "query": text,
            "filter": (f'license:"Creative Commons 0" duration:[{query.min_duration_s:g} TO {query.max_duration_s:g}] '
                       f"filesize:[0 TO {MAX_FILESIZE_BYTES}] samplerate:[44100 TO {MAX_SAMPLE_RATE}]"),
            "sort": "rating_desc",
            "fields": FIELDS,
            "page_size": str(PAGE_SIZE),
        }
        request = urllib.request.Request(f"{API}/search/text/?{urllib.parse.urlencode(params)}", headers={"Authorization": f"Token {self.api_key}"})
        data = json.loads(self._call("search", request))
        return [self._candidate(item) for item in data.get("results", []) if str(item["id"]) not in query.exclude_ids]

    @staticmethod
    def _candidate(item: dict[str, Any]) -> SoundCandidate:
        return SoundCandidate(
            provider="freesound",
            asset_id=str(item["id"]),
            name=item.get("name", ""),
            duration=float(item.get("duration") or 0),
            sample_rate=int(float(item.get("samplerate") or 0)),
            channels=int(item.get("channels") or 0),
            file_type=str(item.get("type") or ""),
            tags=list(item.get("tags") or []),
            description=item.get("description") or "",
            author=item.get("username"),
            source_url=item.get("url") or f"https://freesound.org/s/{item['id']}/",
            raw_license=item.get("license") or "",
            metadata={k: item.get(k) for k in ("num_downloads", "avg_rating", "num_ratings", "filesize")},
        )

    def license_info(self, candidate: SoundCandidate) -> LicenseRecord | None:
        license_type = normalize_license(candidate.raw_license)
        if license_type is None:
            return None
        return LicenseRecord(
            license_type=license_type,
            source_url=candidate.source_url,
            author=candidate.author,
            license_url=CANONICAL_URLS.get(license_type),
            title=candidate.name,
            extra={"raw_license": candidate.raw_license},
        )

    # --- OAuth2 -----------------------------------------------------------

    def authorize_url(self, state: str = "ambient_bot") -> str:
        if not self.client_id:
            raise ProviderUnavailableError("FREESOUND_CLIENT_ID not set")
        return f"{AUTHORIZE_URL}?{urllib.parse.urlencode({'client_id': self.client_id, 'response_type': 'code', 'state': state})}"

    def exchange_code(self, code: str) -> OAuthToken:
        token = self._token_request({"grant_type": "authorization_code", "code": code.strip()})
        self._save_token(token)
        return token

    def _token_request(self, fields: dict[str, str]) -> OAuthToken:
        if not (self.client_id and self.api_key):
            raise ProviderUnavailableError("FREESOUND_CLIENT_ID and FREESOUND_API_KEY are required for OAuth2")
        payload = urllib.parse.urlencode({"client_id": self.client_id, "client_secret": self.api_key, **fields}).encode()
        request = urllib.request.Request(TOKEN_URL, data=payload, method="POST")
        return OAuthToken.from_response(json.loads(self._call("oauth_token", request)))

    def _save_token(self, token: OAuthToken) -> None:
        if self.token_path is None:
            raise ProviderUnavailableError("FREESOUND_TOKEN_PATH not set")
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(json.dumps(token.__dict__), encoding="utf-8")

    def _access_token(self) -> str:
        if self.token_path is None or not self.token_path.exists():
            raise ProviderUnavailableError("no Freesound OAuth2 token; run `python main.py freesound-auth`")
        token = OAuthToken(**json.loads(self.token_path.read_text(encoding="utf-8")))
        if time.time() >= token.expires_at:
            token = self._token_request({"grant_type": "refresh_token", "refresh_token": token.refresh_token})
            self._save_token(token)
        return token.access_token

    # --- Download ---------------------------------------------------------

    def fetch(self, candidate: SoundCandidate, cache_dir: Path) -> FetchedSound:
        record = self.license_info(candidate)
        if record is None:
            raise ValueError(f"freesound {candidate.asset_id}: license '{candidate.raw_license}' is not usable")
        target = cache_dir / "sounds" / "freesound" / f"{candidate.asset_id}.flac"
        original = cache_dir / "downloads" / "freesound" / f"{candidate.asset_id}.{candidate.file_type or 'bin'}"
        expected = int(candidate.metadata.get("filesize") or 0)
        if not original.exists():
            request = urllib.request.Request(
                f"{API}/sounds/{candidate.asset_id}/download/",
                headers={"Authorization": f"Bearer {self._access_token()}"},
            )
            original.parent.mkdir(parents=True, exist_ok=True)
            body = self._call("download", request)
            # A dropped connection can end the read early and still look like a complete file,
            # and a truncated WAV fails much later, deep inside the mixer.
            if expected and len(body) != expected:
                raise SoundUnusableError(f"freesound {candidate.asset_id}: got {len(body)} of {expected} bytes")
            tmp = original.with_suffix(original.suffix + ".part")
            tmp.write_bytes(body)
            tmp.replace(original)
        if not target.exists():
            try:
                normalize_to_flac(original, target)
            except Exception as exc:  # undecodable download: drop it so a retry fetches it again
                original.unlink(missing_ok=True)
                target.unlink(missing_ok=True)
                raise SoundUnusableError(f"freesound {candidate.asset_id}: cannot decode ({exc})") from exc
        return FetchedSound(candidate, target, sha256_file(original), record)

