from __future__ import annotations

import gzip
import logging
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class BackupResult:
    created: bool
    path: str
    message: str


@dataclass(frozen=True)
class BackupAllResult:
    found: int
    created: int
    paths: list[str]
    skipped: list[str]


def create_sqlite_backup(
    db_path: str | Path = "vps/database/history.db",
    backup_dir: str | Path = "vps/backups",
    keep_days: int = 14,
    keep_count: int = 14,
    cleanup: bool = True,
) -> BackupResult:
    db_path = Path(db_path)
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)

    if not db_path.exists():
        message = f"SQLite database not found: {db_path}"
        logging.info(message)
        cleanup_backups(backup_dir, keep_days=keep_days, keep_count=keep_count)
        return BackupResult(False, "", message)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    raw_backup = backup_dir / f"{db_path.stem}-{stamp}.db"
    gz_backup = raw_backup.with_suffix(raw_backup.suffix + ".gz")

    try:
        source = sqlite3.connect(db_path)
        try:
            target = sqlite3.connect(raw_backup)
            try:
                source.backup(target)
            finally:
                target.close()
        finally:
            source.close()

        with raw_backup.open("rb") as src, gzip.open(gz_backup, "wb") as dst:
            shutil.copyfileobj(src, dst)
        raw_backup.unlink(missing_ok=True)
        if cleanup:
            cleanup_backups(backup_dir, keep_days=keep_days, keep_count=keep_count)
        logging.info("SQLite backup created: %s", gz_backup)
        return BackupResult(True, str(gz_backup), "Backup created")
    except Exception as exc:
        raw_backup.unlink(missing_ok=True)
        logging.exception("SQLite backup failed")
        return BackupResult(False, "", f"Backup failed: {exc}")


def backup_all_sqlite_databases(
    database_dir: str | Path = "vps/database",
    backup_dir: str | Path = "vps/backups",
    keep_days: int = 14,
) -> BackupAllResult:
    database_dir = Path(database_dir)
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)

    db_paths = sorted(path for path in database_dir.glob("*.db") if path.is_file())
    if not db_paths:
        cleanup_backups(backup_dir, keep_days=keep_days, keep_count=keep_days)
        return BackupAllResult(
            found=0,
            created=0,
            paths=[],
            skipped=[f"No SQLite databases found in {database_dir}"],
        )

    created_paths: list[str] = []
    skipped: list[str] = []
    keep_count = max(1, int(keep_days)) * max(1, len(db_paths))
    for db_path in db_paths:
        result = create_sqlite_backup(
            db_path=db_path,
            backup_dir=backup_dir,
            keep_days=keep_days,
            keep_count=keep_count,
            cleanup=False,
        )
        if result.created:
            created_paths.append(result.path)
        else:
            skipped.append(f"{db_path.name}: {result.message}")

    cleanup_backups(backup_dir, keep_days=keep_days, keep_count=keep_count)
    return BackupAllResult(
        found=len(db_paths),
        created=len(created_paths),
        paths=created_paths,
        skipped=skipped,
    )


def cleanup_backups(backup_dir: str | Path, keep_days: int = 14, keep_count: int = 14) -> list[Path]:
    backup_dir = Path(backup_dir)
    if not backup_dir.exists():
        return []

    now = datetime.now(timezone.utc).timestamp()
    max_age = max(1, int(keep_days)) * 24 * 60 * 60
    files = sorted(backup_dir.glob("*.db.gz"), key=lambda path: path.stat().st_mtime, reverse=True)
    removed: list[Path] = []

    for index, path in enumerate(files):
        too_old = now - path.stat().st_mtime > max_age
        over_count = index >= max(1, int(keep_count))
        if too_old or over_count:
            try:
                path.unlink()
                removed.append(path)
            except FileNotFoundError:
                pass
    return removed
