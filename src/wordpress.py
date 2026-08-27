"""WordPress.org Plugin Directory client. Official Plugin API only."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlencode

from src.exceptions import PipelineError, SkipPlugin
from src.utils import SafeHttp, official_plugin_url, strip_html


PLUGIN_API = "https://api.wordpress.org/plugins/info/1.2/"
TRANSLATIONS_API = "https://api.wordpress.org/translations/plugins/1.0/"
GLOTPRESS_API = "https://translate.wordpress.org/api/projects/wp-plugins/{slug}/stable"


@dataclass
class PluginInfo:
    name: str
    slug: str
    version: str
    author: str
    official_url: str
    download_url: str
    description: str
    short_description: str
    requires: str
    tested: str
    requires_php: str
    last_updated: str
    active_installs: int | None
    rating: int | None
    icon_url: str
    banner_url: str
    screenshots: list[dict[str, str]]
    tags: list[str]
    license: str
    text_domain: str
    homepage: str
    business_model: str
    language_packs: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data


def _first_icon(icons: Any) -> str:
    if not isinstance(icons, dict):
        return ""
    for key in ("2x", "1x", "svg", "default"):
        value = icons.get(key)
        if value:
            return str(value)
    return next((str(v) for v in icons.values() if v), "")


def _first_banner(banners: Any) -> str:
    if not isinstance(banners, dict):
        return ""
    for key in ("high", "low"):
        value = banners.get(key)
        if value:
            return str(value)
    return next((str(v) for v in banners.values() if v), "")


def _screenshots(value: Any) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    if isinstance(value, dict):
        items = value.values()
    elif isinstance(value, list):
        items = value
    else:
        return results
    for item in items:
        if isinstance(item, dict):
            results.append(
                {
                    "src": str(item.get("src") or ""),
                    "caption": strip_html(str(item.get("caption") or "")),
                }
            )
        elif item:
            results.append({"src": str(item), "caption": ""})
    return results


def _tags(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(v) for v in value.values() if v]
    if isinstance(value, list):
        return [str(v) for v in value if v]
    return []


class WordPressClient:
    def __init__(self, http: SafeHttp) -> None:
        self.http = http

    def fetch_plugin(self, slug: str) -> PluginInfo:
        params = {
            "action": "plugin_information",
            "request[slug]": slug,
            "request[fields][icons]": "1",
            "request[fields][banners]": "1",
            "request[fields][sections]": "1",
            "request[fields][screenshots]": "1",
            "request[fields][language_packs]": "1",
            "request[fields][downloaded]": "1",
            "request[locale]": "ja",
        }
        url = PLUGIN_API + "?" + urlencode(params)
        data = self.http.get_json(url)
        if not isinstance(data, dict) or data.get("error") or not data.get("slug"):
            raise SkipPlugin("WordPress情報取得", f"Plugin APIにプラグインが見つかりません: {slug}")
        if str(data.get("slug")) != slug:
            raise SkipPlugin("WordPress情報取得", f"slugが一致しません: 要求={slug} 応答={data.get('slug')}")

        download_url = str(data.get("download_link") or "")
        business_model = str(data.get("business_model") or "")
        self._assert_free_official(slug, download_url, business_model, data)

        sections = data.get("sections") if isinstance(data.get("sections"), dict) else {}
        description = strip_html(str((sections or {}).get("description") or data.get("description") or ""))
        short_description = strip_html(
            str(data.get("short_description") or data.get("short-description") or "")
        )
        if not short_description:
            short_description = description[:180]

        text_domain = str(data.get("textdomain") or data.get("text_domain") or slug)
        info = PluginInfo(
            name=strip_html(str(data.get("name") or slug)),
            slug=slug,
            version=str(data.get("version") or ""),
            author=strip_html(str(data.get("author") or "")),
            official_url=official_plugin_url(slug),
            download_url=download_url,
            description=description,
            short_description=short_description,
            requires=str(data.get("requires") or ""),
            tested=str(data.get("tested") or ""),
            requires_php="" if data.get("requires_php") in {False, None} else str(data.get("requires_php") or ""),
            last_updated=str(data.get("last_updated") or ""),
            active_installs=data.get("active_installs") if isinstance(data.get("active_installs"), int) else None,
            rating=data.get("rating") if isinstance(data.get("rating"), int) else None,
            icon_url=_first_icon(data.get("icons")),
            banner_url=_first_banner(data.get("banners")),
            screenshots=_screenshots(data.get("screenshots")),
            tags=_tags(data.get("tags")),
            license=str(data.get("license") or ""),
            text_domain=text_domain,
            homepage=str(data.get("homepage") or official_plugin_url(slug)),
            business_model=business_model,
            language_packs=list(data.get("language_packs") or []) if isinstance(data.get("language_packs"), list) else [],
            raw={k: v for k, v in data.items() if k != "sections"},
        )
        if not info.version:
            raise PipelineError("WordPress情報取得", "バージョン情報を取得できませんでした。")
        return info

    def query_plugins(
        self,
        *,
        browse: str = "popular",
        page: int = 1,
        per_page: int = 30,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if browse not in {"popular", "new", "updated", "featured", "beta"}:
            raise PipelineError("プラグイン自動取得", f"未対応のbrowseです: {browse}")
        if page < 1 or per_page < 1 or per_page > 100:
            raise PipelineError("プラグイン自動取得", "page / per_page が不正です。")
        params = {
            "action": "query_plugins",
            "request[browse]": browse,
            "request[page]": str(page),
            "request[per_page]": str(per_page),
            "request[fields][active_installs]": "1",
            "request[fields][language_packs]": "1",
            "request[fields][short_description]": "1",
            "request[locale]": "ja",
        }
        url = PLUGIN_API + "?" + urlencode(params)
        data = self.http.get_json(url)
        if not isinstance(data, dict):
            raise PipelineError("プラグイン自動取得", "Plugin APIの応答が不正です。")
        plugins = data.get("plugins") if isinstance(data.get("plugins"), list) else []
        info = data.get("info") if isinstance(data.get("info"), dict) else {}
        return [row for row in plugins if isinstance(row, dict)], info

    def fetch_translations(self, slug: str, version: str) -> list[dict[str, Any]]:
        url = f"{TRANSLATIONS_API}?slug={slug}&version={version}"
        try:
            data = self.http.get_json(url)
        except Exception:
            return []
        translations = data.get("translations") if isinstance(data, dict) else None
        return list(translations or []) if isinstance(translations, list) else []

    def japanese_language_pack(self, slug: str, version: str, packs: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
        items = packs if packs is not None else self.fetch_translations(slug, version)
        for item in items:
            lang = str(item.get("language") or item.get("locale") or "").lower()
            if lang in {"ja", "ja_jp"} or lang.startswith("ja"):
                return item
        return None

    def glotpress_ja_percent(self, slug: str) -> int | None:
        url = GLOTPRESS_API.format(slug=slug)
        try:
            data = self.http.get_json(url)
        except Exception:
            return None
        sets = data.get("translation_sets") if isinstance(data, dict) else None
        if not isinstance(sets, list):
            return None
        for item in sets:
            locale = str(item.get("locale") or item.get("wp_locale") or "").lower()
            if locale in {"ja", "ja_jp"}:
                percent = item.get("percent_translated")
                return int(percent) if percent is not None else None
        return None

    @staticmethod
    def _assert_free_official(slug: str, download_url: str, business_model: str, data: dict[str, Any]) -> None:
        model = (business_model or "").lower()
        if model in {"commercial", "paid", "premium"}:
            raise SkipPlugin(
                "WordPress情報取得",
                f"有料/商用プラグインのため自動処理しません (business_model={business_model})。",
            )
        if not download_url:
            raise SkipPlugin("WordPress情報取得", "公式ダウンロードURLがありません。外部購入が必要なプラグインの可能性があります。")
        host = download_url.split("/")[2].lower() if "://" in download_url else ""
        if host != "downloads.wordpress.org":
            raise SkipPlugin(
                "WordPress情報取得",
                f"ダウンロード元が WordPress 公式ではありません: {host or download_url}",
            )
        expected_prefix = f"https://downloads.wordpress.org/plugin/{slug}"
        if not download_url.lower().startswith(expected_prefix):
            raise SkipPlugin(
                "WordPress情報取得",
                f"ダウンロードURLが対象slugと一致しません: {download_url}",
            )
        # Heuristic: Pro-only directory listings sometimes keep a download of a stub.
        name = str(data.get("name") or "")
        if re.search(r"\b(pro|premium)\b", name, re.I) and "add" not in name.lower():
            homepage = str(data.get("homepage") or "")
            if homepage and "wordpress.org/plugins/" not in homepage:
                raise SkipPlugin("WordPress情報取得", "名称とホームページから有料版と判断したため自動処理しません。")
