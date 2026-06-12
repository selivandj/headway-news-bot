from __future__ import annotations

import gzip
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VPS_DIR = ROOT / "vps" if (ROOT / "vps").exists() else ROOT
sys.path.insert(0, str(VPS_DIR))

from backup import cleanup_backups, create_sqlite_backup  # noqa: E402


def test_backup_creates_gz_archive(tmp_path):
    db_path = tmp_path / "history.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY, title TEXT)")
        conn.execute("INSERT INTO sample(title) VALUES ('ok')")

    result = create_sqlite_backup(db_path, tmp_path / "backups", keep_days=14, keep_count=14)

    assert result.created is True
    archive = Path(result.path)
    assert archive.exists()
    assert archive.suffix == ".gz"
    with gzip.open(archive, "rb") as fh:
        assert fh.read(16).startswith(b"SQLite format 3")


def test_backup_cleanup_keeps_latest_files(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    for index in range(16):
        (backup_dir / f"history-{index:02d}.db.gz").write_bytes(b"x")

    removed = cleanup_backups(backup_dir, keep_days=14, keep_count=14)

    assert len(removed) == 2
    assert len(list(backup_dir.glob("*.db.gz"))) == 14
