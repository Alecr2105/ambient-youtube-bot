from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse

import numpy as np
import pytest
import soundfile as sf

from app.audio.providers.base import ProviderUnavailableError, SoundCandidate, SoundQuery
from app.audio.providers.freesound import FreesoundProvider
from app.audio.providers.provenance import assess_metadata, speech_likelihood, unwanted_content
from app.utils.config import LicenseType

SR = 48000


class FakeLedger:
    def __init__(self, today=0, minute=0):
        self.today, self.minute, self.calls = today, minute, []

    def requests_today(self, provider):
        return self.today

    def requests_last_minute(self, provider):
        return self.minute

    def record(self, provider, operation, **kw):
        self.calls.append(operation)


def good_candidate(**kw) -> SoundCandidate:
    base = dict(
        provider="freesound", asset_id="1", name="Rain on leaves", duration=120, sample_rate=48000, channels=2,
        file_type="wav", tags=["rain", "field-recording"], description="Recorded with a Zoom H5 in the garden during a storm.",
        author="rec", source_url="https://freesound.org/s/1/", raw_license="Creative Commons 0",
        metadata={"num_downloads": 300, "avg_rating": 4.2, "num_ratings": 5},
    )
    base.update(kw)
    return SoundCandidate(**base)


def test_trustworthy_metadata_is_accepted():
    verdict = assess_metadata(good_candidate())
    assert verdict.accepted and verdict.score >= 0.8


@pytest.mark.parametrize(
    "override",
    [
        {"description": "Rain from a YouTube video"},
        {"name": "movie rain ambience"},
        {"tags": ["rain", "music"]},
        {"description": "people talking in a cafe while it rains outside"},
        {"file_type": "mp3"},
        {"sample_rate": 22050},
        {"description": "rain"},
    ],
)
def test_risky_metadata_is_rejected(override):
    assert not assess_metadata(good_candidate(**override)).accepted


def test_bird_song_is_not_mistaken_for_music():
    candidate = good_candidate(name="Bird song at dawn", tags=["bird", "birdsong"])
    assert assess_metadata(candidate).accepted


def test_man_made_sounds_named_in_the_metadata_are_caught():
    """A real case: a CC0 rain recording whose own title says it has city traffic and a siren."""
    noisy = good_candidate(name="Rain under a roof with City Traffic And A Siren")
    assert assess_metadata(noisy).accepted  # nothing wrong with its provenance...
    assert unwanted_content(noisy) == ["traffic", "sirens", "city"]  # ...but it is not nature ambience


@pytest.mark.parametrize(
    "override",
    [
        {"name": "Bellbird in the cloud forest"},
        {"name": "Thunder rolling over the valley"},
        {"description": "Campfire crackling, recorded with a Zoom H5 at night in the forest."},
        {"tags": ["rain", "business", "metronome"]},  # no false positives from bus/metro
    ],
)
def test_nature_recordings_pass_the_man_made_filter(override):
    assert unwanted_content(good_candidate(**override)) == []


def test_unknown_uploader_without_signals_is_rejected():
    candidate = good_candidate(description="A long rainy afternoon near the lake, calm.", metadata={}, tags=["rain"])
    assert not assess_metadata(candidate).accepted


def test_speech_like_modulation_scores_higher_than_steady_texture():
    rng = np.random.default_rng(0)
    seconds = 20
    steady = rng.standard_normal((SR * seconds, 2)) * 0.1
    t = np.arange(SR * seconds) / SR
    syllables = (0.5 + 0.5 * np.sin(2 * np.pi * 5 * t)) ** 3
    speechy = steady * syllables[:, None]
    assert speech_likelihood(steady, SR) < 0.1
    assert speech_likelihood(speechy, SR) > 0.25


def search_response(*items):
    return json.dumps({"results": list(items)}).encode()


def fs_item(sound_id=7, license_value="http://creativecommons.org/publicdomain/zero/1.0/"):
    return {
        "id": sound_id, "name": "Stream", "tags": ["water"], "description": "Recorded at a creek with a Zoom recorder.",
        "username": "rec", "license": license_value, "url": f"https://freesound.org/people/rec/sounds/{sound_id}/",
        "type": "wav", "samplerate": 48000, "channels": 2, "duration": 95.5, "num_downloads": 80, "avg_rating": 4, "num_ratings": 3,
    }


def test_freesound_search_builds_cc0_query_and_parses_results():
    seen = {}

    def transport(request):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        return search_response(fs_item(7), fs_item(8))

    provider = FreesoundProvider("KEY", "CID", None, FakeLedger(), transport)
    results = provider.search(SoundQuery("creek", "texture", 60, 900, frozenset({"8"})))
    params = urllib.parse.parse_qs(urllib.parse.urlparse(seen["url"]).query)
    assert seen["url"].startswith("https://freesound.org/apiv2/search/text/?")
    # CC0 only, the requested length, and no multi-hundred-MB originals above 48 kHz.
    assert params["filter"] == ['license:"Creative Commons 0" duration:[60 TO 900] '
                                'filesize:[0 TO 60000000] samplerate:[44100 TO 48000]']
    assert seen["auth"] == "Token KEY"
    assert [c.asset_id for c in results] == ["7"]
    record = provider.license_info(results[0])
    assert record.license_type is LicenseType.CC0 and record.author == "rec"


def test_freesound_broadens_a_phrase_that_matches_nothing():
    """Freesound ANDs the words, so a long phrase can return nothing while two words return plenty."""
    asked = []

    def transport(request):
        text = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)["query"][0]
        asked.append(text)
        return search_response(fs_item(7)) if text == "rain window" else search_response()

    provider = FreesoundProvider("KEY", "CID", None, FakeLedger(), transport)
    results = provider.search(SoundQuery("rain window glass drops", "texture", 60, 900))
    assert asked == ["rain window glass drops", "rain window"]
    assert [c.asset_id for c in results] == ["7"]


def test_freesound_returns_nothing_when_even_one_word_fails():
    provider = FreesoundProvider("KEY", "CID", None, FakeLedger(), lambda r: search_response())
    assert provider.search(SoundQuery("zzz unlikely words", "texture", 60, 900)) == []


def test_freesound_rejects_non_commercial_license():
    provider = FreesoundProvider("KEY", "CID", None, FakeLedger(), lambda r: b"")
    candidate = FreesoundProvider._candidate(fs_item(license_value="https://creativecommons.org/licenses/by-nc/4.0/"))
    assert provider.license_info(candidate) is None


def test_freesound_unavailable_without_key_or_quota():
    with pytest.raises(ProviderUnavailableError):
        FreesoundProvider(None, None, None, FakeLedger(), lambda r: b"").search(SoundQuery("x", "texture", 1, 2))
    with pytest.raises(ProviderUnavailableError, match="daily"):
        FreesoundProvider("KEY", None, None, FakeLedger(today=2000), lambda r: b"").search(SoundQuery("x", "texture", 1, 2))


def test_freesound_auth_errors_become_unavailable():
    def transport(request):
        raise urllib.error.HTTPError(request.full_url, 401, "unauthorized", {}, None)

    with pytest.raises(ProviderUnavailableError, match="401"):
        FreesoundProvider("KEY", None, None, FakeLedger(), transport).search(SoundQuery("x", "texture", 1, 2))


def test_freesound_download_refreshes_token_and_normalizes(tmp_path):
    token_path = tmp_path / "token.json"
    token_path.write_text(json.dumps({"access_token": "old", "refresh_token": "R", "expires_at": time.time() - 10}))
    wav = tmp_path / "src.wav"
    sf.write(wav, np.random.default_rng(0).standard_normal((44100 * 2, 1)) * 0.1, 44100)
    calls = []

    def transport(request):
        calls.append(request.full_url)
        if request.full_url.endswith("/access_token/"):
            body = urllib.parse.parse_qs(request.data.decode())
            assert body["grant_type"] == ["refresh_token"] and body["refresh_token"] == ["R"]
            return json.dumps({"access_token": "new", "refresh_token": "R2", "expires_in": 86400}).encode()
        assert request.get_header("Authorization") == "Bearer new"
        return wav.read_bytes()

    provider = FreesoundProvider("KEY", "CID", token_path, FakeLedger(), transport)
    fetched = provider.fetch(FreesoundProvider._candidate(fs_item(7)), tmp_path / "cache")
    info = sf.info(str(fetched.local_path))
    assert (info.samplerate, info.channels) == (48000, 2)
    assert json.loads(token_path.read_text())["access_token"] == "new"
    assert calls[-1].endswith("/sounds/7/download/")
