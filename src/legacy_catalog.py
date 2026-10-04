"""Past shop JA listings: delivery mapping and unified product copy.

Template item 55749997 is included in auto-delivery but is never edited.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from config import Settings
from src.base_template import DEFAULT_DESCRIPTION, short_plugin_name, strip_listing_text
from src.listing_copy import listing_values, official_description
from src.utils import SafeHttp, official_plugin_url, read_json, write_json
from src.wordpress import WordPressClient


JA_TITLE_SUFFIX = "の日本語化ファイル"


@dataclass
class LegacyItem:
    item_id: str
    title: str
    slug: str
    protected: bool = False
    wordpress_url: str = ""

    def plugin_name(self) -> str:
        name = (self.title or "").strip()
        if name.endswith(JA_TITLE_SUFFIX):
            name = name[: -len(JA_TITLE_SUFFIX)].strip()
        return html.unescape(name)


def catalog_path(settings: Settings) -> Path:
    return settings.data_dir / "templates" / "legacy_items.json"


def load_legacy_items(settings: Settings) -> list[LegacyItem]:
    raw = read_json(catalog_path(settings), {}) or {}
    rows = raw.get("items") if isinstance(raw, dict) else None
    items: list[LegacyItem] = []
    seen: set[str] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        item_id = str(row.get("item_id") or "").strip()
        slug = str(row.get("slug") or "").strip().lower()
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        items.append(
            LegacyItem(
                item_id=item_id,
                title=html.unescape(str(row.get("title") or "")),
                slug=slug,
                protected=bool(row.get("protected")),
                wordpress_url=str(row.get("wordpress_url") or (official_plugin_url(slug) if slug else "")),
            )
        )
    template_id = str(settings.base_template_product_id or "").strip()
    if template_id:
        for item in items:
            if item.item_id == template_id:
                item.protected = True
    return items


def category_url(settings: Settings) -> str:
    raw = read_json(catalog_path(settings), {}) or {}
    url = str((raw or {}).get("shop_category_url") or "").strip()
    if url:
        return url
    shop = (settings.shop_public_base_url or "").rstrip("/")
    return f"{shop}/categories/5655306" if shop else ""


def plugin_name_from_title(title: str) -> str:
    name = html.unescape(title or "").strip()
    if name.endswith(JA_TITLE_SUFFIX):
        return name[: -len(JA_TITLE_SUFFIX)].strip()
    return name


def latest_zip_for_slug(output_dir: Path, slug: str) -> Path | None:
    if not slug or not output_dir.exists():
        return None
    matches = sorted(output_dir.glob(f"{slug}-*-ja.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def catalog_index(items: list[LegacyItem]) -> dict[str, LegacyItem]:
    return {item.item_id: item for item in items}


def slug_for_order(
    *,
    item_id: str,
    title: str,
    items: list[LegacyItem],
) -> str:
    by_id = catalog_index(items)
    if item_id in by_id and by_id[item_id].slug:
        return by_id[item_id].slug
    name = plugin_name_from_title(title)
    lowered = re.sub(r"[^a-z0-9]+", "", name.lower())
    for item in items:
        if not item.slug:
            continue
        if plugin_name_from_title(item.title).lower() == name.lower():
            return item.slug
        if re.sub(r"[^a-z0-9]+", "", item.slug) in lowered:
            return item.slug
        if re.sub(r"[^a-z0-9]+", "", plugin_name_from_title(item.title).lower()) == lowered:
            return item.slug
    return ""


def structured_product_detail(
    *,
    plugin_name: str,
    slug: str,
    version: str,
    official_url: str,
    short_description: str,
    created: str = "",
    po_name: str = "",
    mo_name: str = "",
    tags: list[str] | None = None,
    requires: str = "",
    requires_php: str = "",
    sample_ui_lines: list[str] | None = None,
    translated_count: int | None = None,
    untranslated_count: int | None = None,
    translator: Any | None = None,
    settings: Settings | None = None,
) -> str:
    short_description = official_description(
        short_description,
        translator=translator,
        plugin_name=plugin_name,
    )
    values = listing_values(
        plugin_name=plugin_name,
        slug=slug,
        version=version,
        official_url=official_url or (official_plugin_url(slug) if slug else ""),
        short_description=short_description,
        tags=tags,
        requires=requires,
        requires_php=requires_php,
        created=created,
        po_name=po_name,
        mo_name=mo_name,
        sample_ui_lines=sample_ui_lines,
        translated_count=translated_count,
        untranslated_count=untranslated_count,
    )
    body = DEFAULT_DESCRIPTION
    if settings is not None:
        custom = settings.data_dir / "templates" / "product_description.txt"
        if custom.exists():
            body = custom.read_text(encoding="utf-8")
    return strip_listing_text(body.format(**values))


def scan_public_ja_items(settings: Settings, logger: logging.Logger) -> list[tuple[str, str]]:
    """Return (item_id, title) from the public 日本語翻訳ファイル category page."""
    url = category_url(settings)
    if not url:
        return []
    host = (urlparse(url).hostname or "").lower()
    try:
        http = SafeHttp(timeout=settings.http_timeout_seconds, extra_hosts={host} if host else None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("公開カテゴリの取得に失敗しました: %s", type(exc).__name__)
        return []
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for page_no in range(1, 8):
        page_url = url if page_no == 1 else f"{url.rstrip('/')}?page={page_no}"
        try:
            response = http.request("GET", page_url, allow_hosts={host} if host else None, allow_redirects=True)
            response.raise_for_status()
            body = response.text
        except Exception as exc:  # noqa: BLE001
            if page_no == 1:
                logger.warning("公開カテゴリの取得に失敗しました: %s", type(exc).__name__)
            break
        added = 0
        for match in re.finditer(r"https?://[^/\"']+/items/(\d+)", body):
            item_id = match.group(1)
            if item_id in seen:
                continue
            seen.add(item_id)
            found.append((item_id, ""))
            added += 1
        if added == 0:
            break
    titles: dict[str, str] = {}
    for item_id, _blank in found:
        item_url = f"{(settings.shop_public_base_url or '').rstrip('/')}/items/{item_id}"
        title = _og_title(http, item_url, host)
        if JA_TITLE_SUFFIX in title:
            titles[item_id] = title
    return [(item_id, titles[item_id]) for item_id, _ in found if item_id in titles]


def _og_title(http: SafeHttp, url: str, host: str) -> str:
    try:
        response = http.request("GET", url, allow_hosts={host} if host else None, allow_redirects=True)
        response.raise_for_status()
        body = response.text
    except Exception:
        return ""
    match = re.search(
        r'<meta[^>]+(?:property|name)=["\']og:title["\'][^>]+content=["\']([^"\']*)["\']',
        body,
        re.I,
    )
    if not match:
        match = re.search(
            r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']og:title["\']',
            body,
            re.I,
        )
    if not match:
        return ""
    return html.unescape(match.group(1)).split("|")[0].strip()


def merge_scanned_items(known: list[LegacyItem], scanned: list[tuple[str, str]]) -> list[LegacyItem]:
    by_id = catalog_index(known)
    extra: list[LegacyItem] = []
    for item_id, title in scanned:
        if item_id in by_id:
            if title and not by_id[item_id].title:
                by_id[item_id].title = title
            continue
        extra.append(LegacyItem(item_id=item_id, title=title, slug="", protected=False))
    return known + extra


def merge_registered_jobs(items: list[LegacyItem], jobs: list[dict[str, Any]]) -> list[LegacyItem]:
    """Add BASE-registered JA listings from jobs.sqlite3 so recent items are not missed."""
    by_id = catalog_index(items)
    extra: list[LegacyItem] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        item_id = str(job.get("base_product_id") or "").strip()
        slug = str(job.get("plugin_slug") or "").strip().lower()
        if not item_id or not slug or not item_id.isdigit():
            continue
        name = short_plugin_name(str(job.get("plugin_name") or slug), slug)
        title = f"{name}{JA_TITLE_SUFFIX}"
        official = str(job.get("wordpress_url") or official_plugin_url(slug))
        if item_id in by_id:
            if not by_id[item_id].slug:
                by_id[item_id].slug = slug
            if not by_id[item_id].title:
                by_id[item_id].title = title
            if not by_id[item_id].wordpress_url:
                by_id[item_id].wordpress_url = official
            continue
        extra.append(
            LegacyItem(
                item_id=item_id,
                title=title,
                slug=slug,
                protected=False,
                wordpress_url=official,
            )
        )
    return items + extra


def write_delivery_map(settings: Settings, items: list[LegacyItem], output_dir: Path) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    missing: list[str] = []
    for item in items:
        zip_path = latest_zip_for_slug(output_dir, item.slug) if item.slug else None
        entry: dict[str, Any] = {
            "slug": item.slug,
            "title": item.title,
            "protected": item.protected,
        }
        if zip_path is not None:
            try:
                entry["zip"] = str(zip_path.relative_to(settings.root))
            except ValueError:
                entry["zip"] = str(zip_path)
        else:
            missing.append(item.item_id)
        mapping[item.item_id] = entry
    write_json(settings.delivery_map_path, mapping)
    return {"mapping": mapping, "missing": missing}


def detail_for_item(
    item: LegacyItem,
    wp: WordPressClient | None,
    settings: Settings,
    translator: Any | None = None,
) -> str:
    version = ""
    short = ""
    official = item.wordpress_url or (official_plugin_url(item.slug) if item.slug else "")
    name = item.plugin_name()
    tags: list[str] = []
    requires = ""
    requires_php = ""
    if wp and item.slug:
        try:
            info = wp.fetch_plugin(item.slug)
            version = info.version
            short = info.short_description or info.description[:180]
            official = info.official_url
            name = short_plugin_name(info.name, info.slug)
            tags = list(info.tags or [])
            requires = info.requires or ""
            requires_php = info.requires_php or ""
        except Exception:
            pass
    zip_path = latest_zip_for_slug(settings.output_dir, item.slug) if item.slug else None
    created = ""
    po_name = f"{item.slug}-ja.po" if item.slug else ""
    mo_name = f"{item.slug}-ja.mo" if item.slug else ""
    if zip_path is not None:
        created = datetime.fromtimestamp(zip_path.stat().st_mtime).strftime("%Y-%m-%d")
    return strip_listing_text(
        structured_product_detail(
            plugin_name=name,
            slug=item.slug,
            version=version,
            official_url=official,
            short_description=short,
            created=created,
            po_name=po_name,
            mo_name=mo_name,
            tags=tags,
            requires=requires,
            requires_php=requires_php,
            translator=translator,
            settings=settings,
        )
    )
