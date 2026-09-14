from __future__ import annotations

import re
import unicodedata

from app.metadata.builder import MetadataPackage
from app.metadata.language import spanish_findings
from app.quality.report import QualityReport
from app.youtube.uploader import MAX_TAGS_CHARS, MAX_TITLE_CHARS, MetadataError, _tags_length, build_video_body

EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")


def _odd_characters(text: str) -> list[str]:
    allowed_symbols = set(" .,:;!?'\"&()-—–|•#/%+")
    odd = []
    for ch in text:
        if ch.isalnum() or ch in allowed_symbols or ch == "\n" or EMOJI.match(ch):
            continue
        if unicodedata.category(ch).startswith("C") or ch in "<>{}[]\\^~`*_=@$":
            odd.append(ch)
    return sorted(set(odd))


def check_metadata(package: MetadataPackage, contains_synthetic_media: bool) -> QualityReport:
    report = QualityReport("metadata")
    title = package.title
    report.add("title_length", 0 < len(title) <= MAX_TITLE_CHARS, len(title), MAX_TITLE_CHARS)
    report.add("title_characters", not _odd_characters(title), _odd_characters(title), [])
    report.add("title_emoji", len(EMOJI.findall(title)) <= 1, len(EMOJI.findall(title)), 1)
    letters = [c for c in title if c.isalpha()]
    caps_ratio = sum(c.isupper() for c in letters) / max(len(letters), 1)
    report.add("title_not_shouting", caps_ratio < 0.6, round(caps_ratio, 2), 0.6)
    report.add("description_present", len(package.description.strip()) >= 80, len(package.description), 80)
    report.add("description_no_external_links", "http" not in package.description.lower(), None, None)
    report.add("tags_present", bool(package.tags), len(package.tags), ">0")
    report.add("tags_length", _tags_length(package.tags) <= MAX_TAGS_CHARS, _tags_length(package.tags), MAX_TAGS_CHARS)
    spanish = package.spanish_problems()
    report.add("english_only", not spanish, spanish, {})
    report.add("languages", (package.default_language, package.default_audio_language) == ("en", "en"),
               [package.default_language, package.default_audio_language], ["en", "en"])
    try:
        body = build_video_body(package.to_video_metadata(contains_synthetic_media))
        report.add("made_for_kids_false", body["status"]["selfDeclaredMadeForKids"] is False, body["status"]["selfDeclaredMadeForKids"], False)
        report.add("upload_body_valid", True)
    except MetadataError as exc:
        report.add("upload_body_valid", False, str(exc))
    return report
