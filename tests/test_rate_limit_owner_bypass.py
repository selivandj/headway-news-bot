from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VPS_DIR = ROOT / "vps" if (ROOT / "vps").exists() else ROOT
sys.path.insert(0, str(VPS_DIR))

from security.rate_limit import RateLimiter, should_bypass_rate_limit  # noqa: E402


def test_owner_bypass_helper_allows_owner_when_enabled():
    assert should_bypass_rate_limit(is_owner=True, owner_bypass=True) is True
    assert should_bypass_rate_limit(is_owner=True, owner_bypass=False) is False
    assert should_bypass_rate_limit(is_owner=False, owner_bypass=True) is False


def test_rate_limiter_still_limits_non_owner():
    limiter = RateLimiter(requests=1, window_seconds=60, enabled=True)

    assert limiter.allow("user") is True
    assert limiter.allow("user") is False
