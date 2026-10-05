"""Heuristics that lower the chance of using a mislabelled recording.

A CC0 label on an open upload site does not prove the uploader owns the audio, and
Content ID ignores licenses. These checks cannot guarantee safety; they reject the
obvious risks and record why each accepted sound was considered trustworthy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
from scipy import signal

from app.audio.providers.base import SoundCandidate

RISK_PATTERNS = [
    r"youtube", r"\bmovie\b", r"\bfilm\b", r"\bripped\b", r"\brip\b", r"\btv\b", r"netflix", r"spotify",
    r"soundcloud", r"copyright", r"\balbum\b", r"soundtrack", r"\bbbc\b", r"\bremix\b", r"\bbeat\b",
    r"\blyrics?\b", r"(?<!bird )\bsongs?\b", r"\bmusic(al)?\b", r"\bvocals?\b", r"\bvoices?\b", r"\bspeech\b",
    r"\btalking\b", r"\bpeople\b", r"\bcrowd\b", r"\bradio\b", r"\bgame\b", r"sample pack", r"\bsfx library\b",
    r"\bapp\b", r"\bsynth(esized)?\b", r"\bgenerated\b", r"\bai\b",
]
# Sounds that are legitimately CC0 but wrong for a nature ambience: a rain recording whose
# description mentions city traffic and a siren is a rain recording with traffic and a siren in it.
NOISE_PATTERNS = [
    r"\btraffic\b", r"\bsirens?\b", r"\bcars?\b", r"\btrucks?\b", r"\bbuses\b", r"\bbus\b", r"\bmotorcycles?\b",
    r"\bmopeds?\b", r"\bengines?\b", r"\bmotors?\b", r"\bhorns?\b", r"\bair ?planes?\b", r"\bplanes?\b",
    r"\bjets?\b", r"\bhelicopters?\b", r"\btrains?\b", r"\bsubway\b", r"\bmetro\b", r"\bcity\b", r"\burban\b",
    r"\bstreets?\b", r"\bhighways?\b", r"\broad ?noise\b", r"\bconstruction\b", r"\bdrill(ing)?\b",
    r"\bjackhammer\b", r"\bchainsaws?\b", r"\blawn ?mowers?\b", r"\bmachines?\b", r"\bgenerators?\b",
    r"\balarms?\b", r"\bbells?\b", r"\bfireworks?\b", r"\bgun ?shots?\b", r"\bdogs? bark\b", r"\bbarking\b",
]

LOSSLESS_TYPES = {"wav", "flac", "aiff", "aif"}
MIN_DESCRIPTION_CHARS = 20


@dataclass
class ProvenanceVerdict:
    accepted: bool
    score: float
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return f"score={self.score:.2f}; " + "; ".join(self.notes)


def unwanted_content(candidate: SoundCandidate) -> list[str]:
    """Man-made sounds named in the metadata. Nature ambience has to be free of them."""
    text = " ".join([candidate.name, candidate.description, " ".join(candidate.tags)]).lower()
    return [re.sub(r"\\b|[()?]", "", p) for p in NOISE_PATTERNS if re.search(p, text)]


def assess_metadata(candidate: SoundCandidate, min_sample_rate: int = 44100) -> ProvenanceVerdict:
    notes: list[str] = []
    text = " ".join([candidate.name, candidate.description, " ".join(candidate.tags)]).lower()
    hits = [p for p in RISK_PATTERNS if re.search(p, text)]
    if hits:
        return ProvenanceVerdict(False, 0.0, [f"risk terms: {hits}"])
    if candidate.file_type.lower() not in LOSSLESS_TYPES:
        return ProvenanceVerdict(False, 0.0, [f"lossy original ({candidate.file_type})"])
    if candidate.sample_rate < min_sample_rate:
        return ProvenanceVerdict(False, 0.0, [f"sample rate {candidate.sample_rate} < {min_sample_rate}"])
    if len(candidate.description.strip()) < MIN_DESCRIPTION_CHARS:
        return ProvenanceVerdict(False, 0.0, ["no recording description"])

    score = 0.4
    notes.append("lossless original with recording description")
    downloads = int(candidate.metadata.get("num_downloads") or 0)
    ratings = int(candidate.metadata.get("num_ratings") or 0)
    rating = float(candidate.metadata.get("avg_rating") or 0.0)
    if downloads >= 50:
        score += 0.2
        notes.append(f"{downloads} downloads without takedown")
    if ratings >= 2 and rating >= 3.5:
        score += 0.2
        notes.append(f"rated {rating:.1f} by {ratings} users")
    if re.search(r"\b(recorded|recording|field|zoom|tascam|sony|rode|mic|microphone)\b", text):
        score += 0.2
        notes.append("mentions recording gear/process")
    return ProvenanceVerdict(score >= 0.6, round(score, 2), notes)


def speech_likelihood(audio: np.ndarray, sample_rate: int) -> float:
    """Depth of voice-band envelope modulation at syllable rate (3-8 Hz).

    Returned as the standard deviation of the normalized envelope after a 3-8 Hz bandpass:
    steady textures (rain, river, wind) stay well under 0.1, speech is typically above 0.3.
    Used only on textures, because bird calls also modulate in this range.
    """
    mono = audio.mean(axis=1) if audio.ndim == 2 else audio
    band = signal.sosfilt(signal.butter(4, (300, 3400), "bandpass", fs=sample_rate, output="sos"), mono)
    hop = sample_rate // 100
    frames = len(band) // hop
    if frames < 400:
        return 0.0
    envelope = np.sqrt(np.mean(np.square(band[: frames * hop]).reshape(frames, hop), axis=1))
    mean = envelope.mean()
    if mean <= 0:
        return 0.0
    normalized = envelope / mean - 1
    syllabic = signal.sosfiltfilt(signal.butter(2, (3, 8), "bandpass", fs=100, output="sos"), normalized)
    return float(syllabic[50:-50].std())
