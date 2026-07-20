from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import BACKUP_DIR, DB_PATH, ensure_data_dirs


SCHEMA_VERSION = 2


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    ensure_data_dirs()
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db() -> None:
    ensure_data_dirs()
    _backup_before_migration()
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                progress REAL NOT NULL DEFAULT 0,
                current_step TEXT NOT NULL DEFAULT '',
                error TEXT,
                total_catalogs INTEGER NOT NULL DEFAULT 0,
                total_pages INTEGER NOT NULL DEFAULT 0,
                processed_pages INTEGER NOT NULL DEFAULT 0,
                products_total INTEGER NOT NULL DEFAULT 0,
                duplicates_removed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS catalogs (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                filename TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                brand TEXT NOT NULL DEFAULT '',
                collection TEXT NOT NULL DEFAULT '',
                page_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'queued',
                error TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS products (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                dedupe_key TEXT NOT NULL,
                product_code TEXT NOT NULL,
                normalized_code TEXT NOT NULL,
                brand TEXT NOT NULL DEFAULT '',
                catalog TEXT NOT NULL DEFAULT '',
                collection TEXT NOT NULL DEFAULT '',
                page_number INTEGER NOT NULL,
                source_pdf TEXT NOT NULL,
                catalog_id TEXT NOT NULL,
                texture_relpath TEXT NOT NULL,
                page_image_relpath TEXT NOT NULL,
                bbox_json TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'auto',
                method TEXT NOT NULL DEFAULT '',
                dominant_color TEXT NOT NULL DEFAULT '',
                width INTEGER NOT NULL DEFAULT 0,
                height INTEGER NOT NULL DEFAULT 0,
                image_hash TEXT NOT NULL DEFAULT '',
                sources_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_products_job ON products(job_id);
            CREATE INDEX IF NOT EXISTS idx_products_code ON products(job_id, normalized_code);
            CREATE INDEX IF NOT EXISTS idx_products_dedupe ON products(job_id, dedupe_key);
            CREATE INDEX IF NOT EXISTS idx_catalogs_job ON catalogs(job_id);

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS catalog_versions (
                version INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
                activated_at TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT ''
            );
            """
        )
        db.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, utc_now()),
        )
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _backup_before_migration() -> None:
    if not DB_PATH.is_file() or DB_PATH.stat().st_size == 0:
        return
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = BACKUP_DIR / f"pre-migration-{timestamp}.sqlite3"
    source = sqlite3.connect(DB_PATH, timeout=30)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    snapshots = sorted(BACKUP_DIR.glob("pre-migration-*.sqlite3"), reverse=True)
    for expired in snapshots[7:]:
        expired.unlink(missing_ok=True)


def create_job(job_id: str, catalogs: list[dict[str, Any]]) -> None:
    now = utc_now()
    with connect() as db:
        db.execute(
            """INSERT INTO jobs
            (id, status, progress, current_step, total_catalogs, created_at, updated_at)
            VALUES (?, 'queued', 0, '업로드 완료', ?, ?, ?)""",
            (job_id, len(catalogs), now, now),
        )
        db.executemany(
            """INSERT INTO catalogs
            (id, job_id, filename, stored_path, brand, collection, created_at)
            VALUES (:id, :job_id, :filename, :stored_path, :brand, :collection, :created_at)""",
            [{**catalog, "job_id": job_id, "created_at": now} for catalog in catalogs],
        )


def update_job(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    allowed = {
        "status", "progress", "current_step", "error", "total_pages",
        "processed_pages", "products_total", "duplicates_removed",
    }
    payload = {key: value for key, value in fields.items() if key in allowed}
    payload["updated_at"] = utc_now()
    assignments = ", ".join(f"{key} = ?" for key in payload)
    with connect() as db:
        db.execute(
            f"UPDATE jobs SET {assignments} WHERE id = ?",
            [*payload.values(), job_id],
        )


def update_catalog(catalog_id: str, **fields: Any) -> None:
    allowed = {"brand", "collection", "page_count", "status", "error"}
    payload = {key: value for key, value in fields.items() if key in allowed}
    if not payload:
        return
    assignments = ", ".join(f"{key} = ?" for key in payload)
    with connect() as db:
        db.execute(
            f"UPDATE catalogs SET {assignments} WHERE id = ?",
            [*payload.values(), catalog_id],
        )


def get_job(job_id: str) -> dict[str, Any] | None:
    with connect() as db:
        row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["catalogs"] = [
            dict(item)
            for item in db.execute(
                "SELECT id, filename, brand, collection, page_count, status, error "
                "FROM catalogs WHERE job_id = ? ORDER BY created_at",
                (job_id,),
            ).fetchall()
        ]
        return result


def get_catalogs(job_id: str) -> list[dict[str, Any]]:
    with connect() as db:
        return [
            dict(row)
            for row in db.execute(
                "SELECT * FROM catalogs WHERE job_id = ? ORDER BY created_at", (job_id,)
            ).fetchall()
        ]


def _source_identity(source: dict[str, Any]) -> tuple[Any, ...]:
    return (source.get("source_pdf"), source.get("page_number"), source.get("product_code"))


def insert_or_merge_product(product: dict[str, Any]) -> bool:
    """Insert a product. Return True when an existing product was merged."""
    now = utc_now()
    with connect() as db:
        existing = db.execute(
            "SELECT * FROM products WHERE job_id = ? AND dedupe_key = ? ORDER BY confidence DESC LIMIT 1",
            (product["job_id"], product["dedupe_key"]),
        ).fetchone()
        if not existing:
            db.execute(
                """INSERT INTO products (
                    id, job_id, dedupe_key, product_code, normalized_code, brand,
                    catalog, collection, page_number, source_pdf, catalog_id,
                    texture_relpath, page_image_relpath, bbox_json, confidence,
                    status, method, dominant_color, width, height, image_hash,
                    sources_json, created_at, updated_at
                ) VALUES (
                    :id, :job_id, :dedupe_key, :product_code, :normalized_code, :brand,
                    :catalog, :collection, :page_number, :source_pdf, :catalog_id,
                    :texture_relpath, :page_image_relpath, :bbox_json, :confidence,
                    :status, :method, :dominant_color, :width, :height, :image_hash,
                    :sources_json, :created_at, :updated_at
                )""",
                {**product, "created_at": now, "updated_at": now},
            )
            return False

        old = dict(existing)
        sources = json.loads(old.get("sources_json") or "[]")
        incoming_sources = json.loads(product.get("sources_json") or "[]")
        identities = {_source_identity(source) for source in sources}
        for source in incoming_sources:
            if _source_identity(source) not in identities:
                sources.append(source)
                identities.add(_source_identity(source))

        if float(product["confidence"]) > float(old["confidence"]):
            replacement_fields = [
                "product_code", "normalized_code", "brand", "catalog", "collection",
                "page_number", "source_pdf", "catalog_id", "texture_relpath",
                "page_image_relpath", "bbox_json", "confidence", "method",
                "dominant_color", "width", "height", "image_hash",
            ]
            assignments = ", ".join(f"{key} = ?" for key in replacement_fields)
            db.execute(
                f"UPDATE products SET {assignments}, sources_json = ?, updated_at = ? WHERE id = ?",
                [*(product[key] for key in replacement_fields), json.dumps(sources, ensure_ascii=False), now, old["id"]],
            )
        else:
            db.execute(
                "UPDATE products SET sources_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(sources, ensure_ascii=False), now, old["id"]),
            )
        return True


def list_products(
    job_id: str,
    search: str = "",
    brand: str = "",
    catalog: str = "",
    status: str = "",
    limit: int = 1000,
    offset: int = 0,
) -> list[dict[str, Any]]:
    clauses = ["job_id = ?"]
    params: list[Any] = [job_id]
    if search:
        clauses.append("(product_code LIKE ? OR brand LIKE ? OR collection LIKE ?)")
        token = f"%{search}%"
        params.extend([token, token, token])
    if brand:
        clauses.append("brand = ?")
        params.append(brand)
    if catalog:
        clauses.append("catalog = ?")
        params.append(catalog)
    if status:
        clauses.append("status = ?")
        params.append(status)
    params.extend([limit, offset])
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM products WHERE " + " AND ".join(clauses)
            + " ORDER BY brand, normalized_code LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [_deserialize_product(dict(row)) for row in rows]


def get_all_products(job_id: str, include_excluded: bool = False) -> list[dict[str, Any]]:
    clause = "" if include_excluded else " AND status != 'excluded'"
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM products WHERE job_id = ?" + clause + " ORDER BY brand, normalized_code",
            (job_id,),
        ).fetchall()
        return [_deserialize_product(dict(row)) for row in rows]


def get_product(job_id: str, product_id: str) -> dict[str, Any] | None:
    with connect() as db:
        row = db.execute(
            "SELECT * FROM products WHERE job_id = ? AND id = ?", (job_id, product_id)
        ).fetchone()
        return _deserialize_product(dict(row)) if row else None


def update_product(job_id: str, product_id: str, fields: dict[str, Any]) -> None:
    allowed = {
        "product_code", "normalized_code", "brand", "catalog", "collection",
        "status", "dedupe_key", "bbox_json", "texture_relpath", "dominant_color",
        "width", "height", "image_hash", "confidence", "method",
    }
    payload = {key: value for key, value in fields.items() if key in allowed}
    if not payload:
        return
    payload["updated_at"] = utc_now()
    assignments = ", ".join(f"{key} = ?" for key in payload)
    with connect() as db:
        db.execute(
            f"UPDATE products SET {assignments} WHERE job_id = ? AND id = ?",
            [*payload.values(), job_id, product_id],
        )


def _deserialize_product(product: dict[str, Any]) -> dict[str, Any]:
    product["bbox"] = json.loads(product.pop("bbox_json") or "[0, 0, 1, 1]")
    product["sources"] = json.loads(product.pop("sources_json") or "[]")
    return product


def summary_values(job_id: str) -> dict[str, Any]:
    with connect() as db:
        row = db.execute(
            """SELECT COUNT(*) AS total,
            SUM(CASE WHEN status = 'excluded' THEN 1 ELSE 0 END) AS excluded,
            SUM(CASE WHEN confidence < 0.7 AND status != 'excluded' THEN 1 ELSE 0 END) AS needs_review,
            COUNT(DISTINCT brand) AS brands,
            COUNT(DISTINCT catalog) AS catalogs
            FROM products WHERE job_id = ?""",
            (job_id,),
        ).fetchone()
        return {key: (row[key] or 0) for key in row.keys()}


def set_active_job(job_id: str) -> None:
    with connect() as db:
        job = db.execute(
            "SELECT id FROM jobs WHERE id = ? AND status = 'complete'", (job_id,)
        ).fetchone()
        product_count = db.execute(
            "SELECT COUNT(*) FROM products WHERE job_id = ? AND status != 'excluded'", (job_id,)
        ).fetchone()[0]
        if not job or product_count < 1:
            raise ValueError("완료된 제품이 있는 작업만 앱에 적용할 수 있습니다.")
        db.execute(
            """INSERT INTO settings (key, value, updated_at) VALUES ('active_job_id', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            (job_id, utc_now()),
        )
        db.execute(
            "INSERT OR IGNORE INTO catalog_versions (job_id, activated_at) VALUES (?, ?)",
            (job_id, utc_now()),
        )


def get_catalog_versions() -> list[dict[str, Any]]:
    with connect() as db:
        return [
            dict(row)
            for row in db.execute(
                """SELECT v.version, v.job_id, v.activated_at, v.note,
                j.products_total, j.total_catalogs
                FROM catalog_versions v
                JOIN jobs j ON j.id = v.job_id
                ORDER BY v.version DESC"""
            ).fetchall()
        ]


def get_schema_version() -> int:
    with connect() as db:
        row = db.execute("PRAGMA user_version").fetchone()
        return int(row[0]) if row else 0


def get_active_job_id() -> str | None:
    with connect() as db:
        setting = db.execute(
            "SELECT value FROM settings WHERE key = 'active_job_id'"
        ).fetchone()
        if setting:
            valid = db.execute(
                "SELECT id FROM jobs WHERE id = ? AND status = 'complete'", (setting["value"],)
            ).fetchone()
            if valid:
                return str(setting["value"])
        latest = db.execute(
            """SELECT j.id FROM jobs j
            WHERE j.status = 'complete'
              AND EXISTS (SELECT 1 FROM products p WHERE p.job_id = j.id AND p.status != 'excluded')
            ORDER BY j.created_at DESC LIMIT 1"""
        ).fetchone()
        return str(latest["id"]) if latest else None
