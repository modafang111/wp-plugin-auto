"""SQLite job history, translation cache, and stage tracking."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


STATUSES = (
    "pending",
    "wp_info",
    "downloaded",
    "extracted",
    "analyzed",
    "strings_extracted",
    "translated",
    "quality_checked",
    "packaged",
    "preview_ready",
    "base_registered",
    "completed",
    "skipped_already_translated",
    "skipped_not_eligible",
    "skipped_duplicate",
    "needs_review",
    "error",
)

DONE_STATUSES = {
    "completed",
    "base_registered",
    "skipped_already_translated",
    "skipped_not_eligible",
    "skipped_duplicate",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plugin_slug TEXT NOT NULL,
    plugin_name TEXT,
    plugin_version TEXT NOT NULL,
    wordpress_url TEXT,
    download_url TEXT,
    translation_date TEXT,
    output_zip TEXT,
    base_product_id TEXT,
    base_product_url TEXT,
    status TEXT NOT NULL,
    stage TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    error_message TEXT,
    extra_json TEXT,
    UNIQUE(plugin_slug, plugin_version)
);

CREATE TABLE IF NOT EXISTS downloads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plugin_slug TEXT NOT NULL,
    plugin_version TEXT NOT NULL,
    downloaded_at TEXT NOT NULL,
    download_url TEXT,
    zip_path TEXT
);

CREATE TABLE IF NOT EXISTS translation_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_hash TEXT NOT NULL,
    source_text TEXT NOT NULL,
    context TEXT NOT NULL DEFAULT '',
    translated_text TEXT NOT NULL,
    provider TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(source_hash, context, provider)
);

CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    unique_key TEXT NOT NULL,
    order_item_id TEXT NOT NULL,
    item_id TEXT,
    zip_path TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    error_message TEXT,
    UNIQUE(unique_key, order_item_id)
);
"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def get_job(self, slug: str, version: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE plugin_slug = ? AND plugin_version = ?",
            (slug, version),
        ).fetchone()
        return dict(row) if row else None

    def latest_job(self, slug: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE plugin_slug = ? ORDER BY updated_at DESC LIMIT 1",
            (slug,),
        ).fetchone()
        return dict(row) if row else None

    def is_finished(self, slug: str, version: str) -> bool:
        job = self.get_job(slug, version)
        return bool(job and job.get("status") in DONE_STATUSES)

    def successful_job(self, slug: str, version: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT * FROM jobs
            WHERE plugin_slug = ? AND plugin_version = ?
              AND status IN ('completed', 'base_registered')
            """,
            (slug, version),
        ).fetchone()
        return dict(row) if row else None

    def upsert_job(self, slug: str, version: str, **fields: Any) -> dict[str, Any]:
        existing = self.get_job(slug, version)
        now = _now()
        if existing:
            assignments = ["updated_at = ?"]
            values: list[Any] = [now]
            for key, value in fields.items():
                assignments.append(f"{key} = ?")
                values.append(value)
            values.extend([slug, version])
            self.conn.execute(
                f"UPDATE jobs SET {', '.join(assignments)} WHERE plugin_slug = ? AND plugin_version = ?",
                values,
            )
        else:
            payload = {
                "plugin_slug": slug,
                "plugin_name": fields.get("plugin_name"),
                "plugin_version": version,
                "wordpress_url": fields.get("wordpress_url"),
                "download_url": fields.get("download_url"),
                "translation_date": fields.get("translation_date"),
                "output_zip": fields.get("output_zip"),
                "base_product_id": fields.get("base_product_id"),
                "base_product_url": fields.get("base_product_url"),
                "status": fields.get("status", "pending"),
                "stage": fields.get("stage", fields.get("status", "pending")),
                "created_at": now,
                "updated_at": now,
                "error_message": fields.get("error_message"),
                "extra_json": fields.get("extra_json"),
            }
            keys = ", ".join(payload.keys())
            marks = ", ".join("?" for _ in payload)
            self.conn.execute(f"INSERT INTO jobs ({keys}) VALUES ({marks})", list(payload.values()))
        self.conn.commit()
        job = self.get_job(slug, version)
        assert job is not None
        return job

    def record_download(self, slug: str, version: str, download_url: str, zip_path: str) -> None:
        self.conn.execute(
            """
            INSERT INTO downloads (plugin_slug, plugin_version, downloaded_at, download_url, zip_path)
            VALUES (?, ?, ?, ?, ?)
            """,
            (slug, version, _now(), download_url, zip_path),
        )
        self.conn.commit()

    def cache_get(self, source_hash: str, context: str, provider: str) -> str | None:
        row = self.conn.execute(
            """
            SELECT translated_text FROM translation_cache
            WHERE source_hash = ? AND context = ? AND provider = ?
            """,
            (source_hash, context or "", provider),
        ).fetchone()
        return row["translated_text"] if row else None

    def cache_put(self, source_hash: str, source_text: str, context: str, translated: str, provider: str) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO translation_cache
            (source_hash, source_text, context, translated_text, provider, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (source_hash, source_text, context or "", translated, provider, _now()),
        )
        self.conn.commit()

    def job_by_item_id(self, item_id: str) -> dict[str, Any] | None:
        if not item_id:
            return None
        row = self.conn.execute(
            """
            SELECT * FROM jobs
            WHERE base_product_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (str(item_id),),
        ).fetchone()
        return dict(row) if row else None

    def all_jobs_with_products(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT * FROM jobs
            WHERE base_product_id IS NOT NULL AND base_product_id != ''
            ORDER BY updated_at DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def get_delivery(self, unique_key: str, order_item_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT * FROM deliveries
            WHERE unique_key = ? AND order_item_id = ?
            """,
            (unique_key, order_item_id),
        ).fetchone()
        return dict(row) if row else None

    def record_delivery(
        self,
        unique_key: str,
        order_item_id: str,
        *,
        item_id: str = "",
        zip_path: str = "",
        status: str,
        error_message: str = "",
    ) -> None:
        now = _now()
        existing = self.get_delivery(unique_key, order_item_id)
        if existing:
            self.conn.execute(
                """
                UPDATE deliveries
                SET item_id = ?, zip_path = ?, status = ?, error_message = ?, created_at = ?
                WHERE unique_key = ? AND order_item_id = ?
                """,
                (item_id, zip_path, status, error_message, now, unique_key, order_item_id),
            )
        else:
            self.conn.execute(
                """
                INSERT INTO deliveries
                (unique_key, order_item_id, item_id, zip_path, status, created_at, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (unique_key, order_item_id, item_id, zip_path, status, now, error_message),
            )
        self.conn.commit()
