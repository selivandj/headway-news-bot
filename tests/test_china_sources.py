from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VPS_DIR = ROOT / "vps" if (ROOT / "vps").exists() else ROOT
sys.path.insert(0, str(VPS_DIR))

from parsers.china_sources import ChinaSourcesParser  # noqa: E402


def test_china_sources_are_configured():
    parser = ChinaSourcesParser()
    names = {source.name for source in parser.SOURCES}

    assert "cnevpost" in names
    assert "carnewschina" in names
    assert callable(parser.parse_all)
