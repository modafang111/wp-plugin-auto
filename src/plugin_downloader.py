"""Download official plugin ZIPs. PHP is never executed."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from config import Settings
from src.database import Database
from src.exceptions import PipelineError
from src.utils import SafeHttp, safe_extract_zip, write_json
from src.wordpress import PluginInfo


class PluginDownloader:
    def __init__(self, settings: Settings, http: SafeHttp, db: Database, logger: logging.Logger) -> None:
        self.settings = settings
        self.http = http
        self.db = db
        self.logger = logger

    def work_dir(self, info: PluginInfo) -> Path:
        return self.settings.work_dir / info.slug / info.version

    def download(self, info: PluginInfo) -> dict:
        work = self.work_dir(info)
        work.mkdir(parents=True, exist_ok=True)
        zip_path = work / "original.zip"
        self.logger.info("ZIPダウンロード: %s", info.download_url)
        size = self.http.download(info.download_url, zip_path, self.settings.max_zip_bytes)
        stamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        record = {
            "plugin_slug": info.slug,
            "plugin_version": info.version,
            "downloaded_at": stamp,
            "download_url": info.download_url,
            "zip_path": str(zip_path),
            "bytes": size,
        }
        write_json(work / "download.json", record)
        self.db.record_download(info.slug, info.version, info.download_url, str(zip_path))
        self.logger.info("ZIPダウンロード完了: %s bytes", size)
        return record

    def extract(self, info: PluginInfo) -> Path:
        work = self.work_dir(info)
        zip_path = work / "original.zip"
        if not zip_path.exists():
            raise PipelineError("ZIP展開", f"ZIPが見つかりません: {zip_path}")
        dest = work / "original"
        if dest.exists():
            # Resume: reuse existing extract if present.
            php_files = list(dest.rglob("*.php"))
            if php_files:
                self.logger.info("ZIP展開: 既存の展開フォルダを再利用します")
                return dest
        self.logger.info("ZIP展開: %s -> %s", zip_path, dest)
        safe_extract_zip(
            zip_path,
            dest,
            max_files=self.settings.max_zip_files,
            max_uncompressed=self.settings.max_uncompressed_bytes,
        )
        return dest
