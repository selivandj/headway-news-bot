from __future__ import annotations

import gzip
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VPS_DIR = ROOT / "vps" if (ROOT / "vps").exists() else ROOT
sys.path.insert(0, str(VPS_DIR))

from backup import backup_all_sqlite_databases  # noqa: E402


def create_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO sample(value) VALUES ('ok')")


def test_backup_all_sqlite_databases_creates_archive_for_each_db(tmp_path):
    database_dir = tmp_path / "database"
    backup_dir = tmp_path / "backups"
    database_dir.mkdir()
    create_db(database_dir / "history.db")
    create_db(database_dir / "image_hashes.db")
    (database_dir / "notes.txt").write_text("skip", encoding="utf-8")

    result = backup_all_sqlite_databases(database_dir, backup_dir, keep_days=14)

    assert result.found == 2
    assert result.created == 2
    assert result.skipped == []
    names = {Path(path).name.split("-")[0] for path in result.paths}
    assert names == {"history", "image_hashes"}
    for path in result.paths:
        with gzip.open(path, "rb") as fh:
            assert fh.read(16).startswith(b"SQLite format 3")


def test_backup_all_sqlite_databases_reports_empty_directory(tmp_path):
    database_dir = tmp_path / "database"
    database_dir.mkdir()

    result = backup_all_sqlite_databases(database_dir, tmp_path / "backups", keep_days=14)

    assert result.found == 0
    assert result.created == 0
    assert result.paths == []
    assert result.skipped
