"""Pick WordPress.org plugins that do not already have an official JA pack."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from config import Settings
from src.database import Database
from src.plugin_analyzer import decide_already_translated
from src.utils import official_plugin_url
from src.wordpress import WordPressClient


DEFAULT_BLOCKED_SLUGS = frozenset({"hello-dolly", "akismet"})
ALLOWED_BROWSE = ("popular", "new", "updated", "featured")


@dataclass
class DiscoveredPlugin:
    slug: str
    name: str
    version: str
    url: str
    active_installs: int | None
    reason: str = ""


def blocked_slugs(settings: Settings) -> set[str]:
    extra = {part.strip().lower() for part in (settings.discover_skip_slugs or "").split(",") if part.strip()}
    return set(DEFAULT_BLOCKED_SLUGS) | extra


def browse_names(settings: Settings) -> list[str]:
    names = [part.strip().lower() for part in (settings.discover_browse or "popular").split(",") if part.strip()]
    return [name for name in names if name in ALLOWED_BROWSE] or ["popular"]


def plugin_has_ja_pack(plugin: dict[str, Any]) -> bool:
    packs = plugin.get("language_packs")
    if not isinstance(packs, list):
        return False
    for item in packs:
        if not isinstance(item, dict):
            continue
        lang = str(item.get("language") or item.get("locale") or "").lower()
        if lang in {"ja", "ja_jp"} or lang.startswith("ja"):
            return True
    return False


def eligibility_reason(plugin: dict[str, Any], *, min_installs: int, skip_slugs: set[str]) -> str:
    slug = str(plugin.get("slug") or "").strip().lower()
    if not slug or not re.fullmatch(r"[a-z0-9_-]+", slug):
        return "slugが不正です。"
    if slug in skip_slugs:
        return "対象外スラッグです。"
    model = str(plugin.get("business_model") or "").lower()
    if model in {"commercial", "paid", "premium"}:
        return f"有料/商用プラグインです ({model})。"
    download_url = str(plugin.get("download_link") or "")
    if not download_url:
        return "公式ダウンロードURLがありません。"
    host = (urlparse(download_url).hostname or "").lower()
    if host != "downloads.wordpress.org":
        return "ダウンロード元が WordPress 公式ではありません。"
    if not download_url.lower().startswith(f"https://downloads.wordpress.org/plugin/{slug}"):
        return "ダウンロードURLがslugと一致しません。"
    name = str(plugin.get("name") or "")
    if re.search(r"\b(pro|premium)\b", name, re.I) and "add" not in name.lower():
        homepage = str(plugin.get("homepage") or "")
        if homepage and "wordpress.org/plugins/" not in homepage:
            return "名称から有料版と判断しました。"
    installs = plugin.get("active_installs")
    if isinstance(installs, int) and installs < min_installs:
        return f"有効インストール数が {min_installs} 未満です。"
    if plugin_has_ja_pack(plugin):
        return "公式日本語 language pack があります。"
    if not str(plugin.get("version") or "").strip():
        return "バージョンがありません。"
    return ""


def discover_plugins(
    wp: WordPressClient,
    db: Database,
    settings: Settings,
    logger: logging.Logger,
    *,
    limit: int,
    check_glotpress: bool = True,
) -> list[DiscoveredPlugin]:
    want = max(1, min(int(limit), 20))
    skip = blocked_slugs(settings)
    found: list[DiscoveredPlugin] = []
    seen: set[str] = set()
    logger.info(
        "プラグイン自動取得を開始: browse=%s limit=%s min_installs=%s max_pages=%s",
        settings.discover_browse,
        want,
        settings.discover_min_installs,
        settings.discover_max_pages,
    )
    for browse in browse_names(settings):
        if len(found) >= want:
            break
        for page in range(1, settings.discover_max_pages + 1):
            if len(found) >= want:
                break
            plugins, info = wp.query_plugins(browse=browse, page=page, per_page=settings.discover_per_page)
            pages = int(info.get("pages") or 0) if isinstance(info.get("pages"), int) else 0
            skipped_pack = 0
            skipped_other = 0
            for plugin in plugins:
                slug = str(plugin.get("slug") or "").strip().lower()
                version = str(plugin.get("version") or "").strip()
                if not slug or slug in seen:
                    continue
                seen.add(slug)
                reason = eligibility_reason(
                    plugin,
                    min_installs=settings.discover_min_installs,
                    skip_slugs=skip,
                )
                if reason:
                    if "language pack" in reason:
                        skipped_pack += 1
                    else:
                        skipped_other += 1
                    continue
                if version and db.is_finished(slug, version):
                    skipped_other += 1
                    logger.info("スキップ %s %s: このバージョンは処理済み", slug, version)
                    continue
                if check_glotpress:
                    percent = wp.glotpress_ja_percent(slug)
                    already, already_reason = decide_already_translated(
                        official_ja_percent=percent,
                        has_official_ja_pack=False,
                        skip_if_ja_percent=settings.skip_if_ja_percent,
                    )
                    if already:
                        skipped_other += 1
                        logger.info("スキップ %s: %s", slug, already_reason)
                        continue
                installs = plugin.get("active_installs") if isinstance(plugin.get("active_installs"), int) else None
                candidate = DiscoveredPlugin(
                    slug=slug,
                    name=str(plugin.get("name") or slug),
                    version=version,
                    url=official_plugin_url(slug),
                    active_installs=installs,
                )
                found.append(candidate)
                logger.info(
                    "候補 %s/%s: %s %s installs=%s %s",
                    len(found),
                    want,
                    candidate.name,
                    candidate.version,
                    installs if installs is not None else "?",
                    candidate.url,
                )
                if len(found) >= want:
                    break
            logger.info(
                "一覧 %s page %s: 取得=%s JAパック=%s その他スキップ=%s 候補累計=%s",
                browse,
                page,
                len(plugins),
                skipped_pack,
                skipped_other,
                len(found),
            )
            if pages and page >= pages:
                break
            if not plugins:
                break
            time.sleep(0.15)
    if not found:
        logger.warning("条件に合うプラグインが見つかりませんでした。DISCOVER_MAX_PAGES を増やすか、min_installs を下げてください。")
    return found
