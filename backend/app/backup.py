from __future__ import annotations

import json
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .config import BACKUP_DIR, DB_PATH, job_dir
from .db import get_active_job_id, get_schema_version


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sqlite_snapshot(target: Path) -> None:
    source = sqlite3.connect(DB_PATH, timeout=30)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()


def create_catalog_backup() -> Path:
    """Create a portable backup of the database and active catalog files."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _timestamp()
    database_snapshot = BACKUP_DIR / f"database-{stamp}.sqlite3"
    archive_path = BACKUP_DIR / f"hanall-backup-{stamp}.zip"
    _sqlite_snapshot(database_snapshot)
    active_job_id = get_active_job_id()
    manifest = {
        "name": "HANALL AI Catalog Backup",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "schema_version": get_schema_version(),
        "active_job_id": active_job_id,
        "database": "database/hanall_catalogs.sqlite3",
        "active_catalog_directory": f"jobs/{active_job_id}/" if active_job_id else None,
    }
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.write(database_snapshot, "database/hanall_catalogs.sqlite3")
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        if active_job_id:
            root = job_dir(active_job_id)
            if root.is_dir():
                for path in root.rglob("*"):
                    if path.is_file() and "exports" not in path.relative_to(root).parts:
                        archive.write(path, Path("jobs") / active_job_id / path.relative_to(root))
    database_snapshot.unlink(missing_ok=True)
    backups = sorted(BACKUP_DIR.glob("hanall-backup-*.zip"), reverse=True)
    for expired in backups[5:]:
        expired.unlink(missing_ok=True)
    return archive_path


def list_catalog_backups() -> list[dict[str, object]]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return [
        {
            "filename": path.name,
            "size": path.stat().st_size,
            "created_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
        }
        for path in sorted(BACKUP_DIR.glob("hanall-backup-*.zip"), reverse=True)
    ]
