from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VPS_DIR = ROOT / "vps" if (ROOT / "vps").exists() else ROOT
sys.path.insert(0, str(VPS_DIR))

from env_loader import headway_env_paths, load_headway_env  # noqa: E402


def test_env_paths_support_root_and_vps_layout(tmp_path):
    vps_dir = tmp_path / "vps"
    vps_dir.mkdir()

    assert headway_env_paths(vps_dir) == [tmp_path / ".env", tmp_path / "vps" / ".env"]
    assert headway_env_paths(tmp_path) == [tmp_path / ".env", tmp_path / "vps" / ".env"]


def test_load_headway_env_loads_root_then_vps_without_requiring_files(tmp_path, monkeypatch):
    vps_dir = tmp_path / "vps"
    vps_dir.mkdir()
    (tmp_path / ".env").write_text("HEADWAY_ROOT_VALUE=root\nHEADWAY_SHARED=root\n", encoding="utf-8")
    (vps_dir / ".env").write_text("HEADWAY_VPS_VALUE=vps\nHEADWAY_SHARED=vps\n", encoding="utf-8")

    for key in ("HEADWAY_ROOT_VALUE", "HEADWAY_VPS_VALUE", "HEADWAY_SHARED"):
        monkeypatch.delenv(key, raising=False)

    loaded = load_headway_env(vps_dir)

    assert loaded == [tmp_path / ".env", vps_dir / ".env"]
    assert os.environ["HEADWAY_ROOT_VALUE"] == "root"
    assert os.environ["HEADWAY_VPS_VALUE"] == "vps"
    assert os.environ["HEADWAY_SHARED"] == "root"


def test_load_headway_env_ignores_missing_files(tmp_path):
    assert load_headway_env(tmp_path) == []
