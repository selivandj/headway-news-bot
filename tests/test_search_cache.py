from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VPS_DIR = ROOT / "vps" if (ROOT / "vps").exists() else ROOT
sys.path.insert(0, str(VPS_DIR))

from search.cache import SearchCache  # noqa: E402


def test_search_cache_set_get(tmp_path):
    cache = SearchCache(tmp_path, ttl_hours=24)
    payload = {"title": "EV charging", "count": 2}

    cache.set("query", payload)

    assert cache.get("query") == payload
    assert cache.get("missing") is None
