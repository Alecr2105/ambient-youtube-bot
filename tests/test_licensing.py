from __future__ import annotations

import pytest

from app.licensing.attribution import attribution_block
from app.licensing.licenses import CANONICAL_URLS, LicenseRecord, normalize_license
from app.licensing.validator import LicenseError, LicenseValidator
from app.utils.config import LicenseType


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Creative Commons 0", LicenseType.CC0),
        ("http://creativecommons.org/publicdomain/zero/1.0/", LicenseType.CC0),
        ("https://creativecommons.org/licenses/by/4.0/", LicenseType.CC_BY_4),
        ("http://creativecommons.org/licenses/by/3.0/", LicenseType.CC_BY_3),
        ("Attribution NonCommercial", None),
        ("https://creativecommons.org/licenses/by-nc/4.0/", None),
        ("http://creativecommons.org/licenses/sampling+/1.0/", None),
        ("Attribution", None),  # version unknown: not verifiable
        ("", None),
        (None, None),
    ],
)
def test_normalize_license(raw, expected):
    assert normalize_license(raw) is expected


def cc0(**kw):
    base = {"license_type": LicenseType.CC0, "source_url": "https://freesound.org/s/1/", "author": "rec", "license_url": CANONICAL_URLS[LicenseType.CC0]}
    base.update(kw)
    return LicenseRecord(**base)


def test_default_whitelist_blocks_cc_by():
    validator = LicenseValidator([LicenseType.CC0, LicenseType.PROCEDURAL, LicenseType.OWN])
    validator.assert_usable(cc0())
    with pytest.raises(LicenseError, match="not in ALLOWED_LICENSES"):
        validator.assert_usable(cc0(license_type=LicenseType.CC_BY_4, license_url=CANONICAL_URLS[LicenseType.CC_BY_4]))


@pytest.mark.parametrize(
    ("record", "blacklisted", "message"),
    [
        (None, False, "no license record"),
        (cc0(source_url=None), False, "source_url"),
        (cc0(license_url=None), False, "license_url"),
        (cc0(), True, "blacklisted"),
    ],
)
def test_incomplete_or_blacklisted_records_are_blocked(record, blacklisted, message):
    with pytest.raises(LicenseError, match=message):
        LicenseValidator([LicenseType.CC0]).assert_usable(record, blacklisted=blacklisted)


def test_procedural_and_own_need_no_urls():
    validator = LicenseValidator([LicenseType.PROCEDURAL, LicenseType.OWN])
    validator.assert_usable(LicenseRecord(LicenseType.PROCEDURAL, None, None))
    validator.assert_usable(LicenseRecord(LicenseType.OWN, None, "channel owner"))


def test_attribution_only_for_cc_by_and_deduplicated():
    by = cc0(license_type=LicenseType.CC_BY_4, title="Rain", author="alice", source_url="https://freesound.org/s/9/")
    block = attribution_block([cc0(), by, by])
    assert block.count("alice") == 1
    assert '"Rain" by alice (https://freesound.org/s/9/) licensed under CC BY 4.0' in block
    assert attribution_block([cc0()]) == ""
