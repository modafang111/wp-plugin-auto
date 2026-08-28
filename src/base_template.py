"""Treat an existing BASE product as a read-only template. Never edit or delete it."""

from __future__ import annotations

import html as htmlmod
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse

from config import Settings
from src.utils import SafeHttp, read_json, write_json
from src.wordpress import PluginInfo


DEFAULT_DESCRIPTION = """■商品について
本商品は「{plugin_name}」の日本語化ファイルです。
WordPress公式プラグイン本体は含まれていません。
公式ディレクトリからプラグイン本体をインストールしたうえで、本日本語化ファイルをご利用ください。

■対象プラグイン
プラグイン名：{plugin_name}
対象バージョン：{version}
公式URL：{official_url}

■概要
{short_description}

■日本語化対象
管理画面およびプラグインが表示する文字列

■導入方法
1. 公式ページから「{plugin_name}」をインストールしてください。
2. 本商品のZIPを展開し、{po_name} と {mo_name} を次のいずれかに配置します。
   wp-content/plugins/{slug}/languages/
   または
   wp-content/languages/plugins/
3. サイト言語を日本語に設定してください。

■注意事項
・本商品は日本語化ファイルです。プラグイン本体ではありません。
・オリジナルプラグインの著作権は原作者に帰属します。
・プラグインのアップデートにより、一部の文字列が未翻訳になる場合があります。
・WordPressおよび原作者とは関係のない第三者による翻訳ファイルです。

■更新日
{created}
"""


@dataclass
class ProductTemplate:
    item_id: str = ""
    title: str = ""
    detail: str = ""
    price: int = 0
    stock: int = 9999
    visible: int = 0
    item_tax_type: int = 1
    identifier: str = ""
    category_ids: list[int] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    name_pattern: str = "{plugin_name} WordPressプラグイン 日本語化ファイル"
    sale_package_mode: str = "translation_only"
    source: str = "defaults"
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_shop_fields(settings: Settings) -> dict[str, str]:
    """Recover item URL / ID / shop URL even if the three .env values were swapped."""
    blobs = [
        settings.base_template_product_url,
        settings.base_template_product_id,
        settings.shop_public_base_url,
    ]
    product_url = ""
    item_id = ""
    shop_url = ""
    for blob in blobs:
        text = (blob or "").strip()
        if not text:
            continue
        match = re.search(r"(https?://[^/\s]+)/items/(\d+)/?", text)
        if match:
            shop_url = shop_url or match.group(1)
            item_id = item_id or match.group(2)
            product_url = product_url or f"{match.group(1)}/items/{match.group(2)}"
            continue
        parsed = urlparse(text)
        if parsed.scheme in {"http", "https"} and parsed.netloc and not parsed.path.strip("/"):
            shop_url = shop_url or f"{parsed.scheme}://{parsed.netloc}"
            continue
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            shop_url = shop_url or f"{parsed.scheme}://{parsed.netloc}"
            continue
        if text.isdigit():
            item_id = item_id or text
    if not product_url and shop_url and item_id:
        product_url = f"{shop_url}/items/{item_id}"
    if product_url:
        settings.base_template_product_url = product_url
    if item_id:
        settings.base_template_product_id = item_id
    if shop_url:
        settings.shop_public_base_url = shop_url
    return {"product_url": product_url, "item_id": item_id, "shop_url": shop_url}


class BaseTemplateService:
    def __init__(self, settings: Settings, logger: logging.Logger, client: Any | None = None) -> None:
        self.settings = settings
        self.logger = logger
        self.client = client

    def load(self) -> ProductTemplate:
        fixed = normalize_shop_fields(self.settings)
        if fixed.get("item_id") or fixed.get("product_url"):
            self.logger.info(
                "テンプレート商品を正規化: id=%s url=%s shop=%s",
                fixed.get("item_id"),
                fixed.get("product_url"),
                fixed.get("shop_url"),
            )
        cached = read_json(self.settings.template_cache_path, None)
        template = ProductTemplate()
        template.name_pattern = self.settings.product_name_pattern
        template.sale_package_mode = self.settings.sale_package_mode
        template.stock = self.settings.product_stock
        template.item_tax_type = self.settings.product_tax_type
        template.visible = self.settings.visible_flag
        if self.settings.product_price:
            try:
                template.price = int(self.settings.product_price)
            except ValueError:
                template.price = 0
        if self.settings.base_category_id:
            try:
                template.category_ids = [int(self.settings.base_category_id)]
            except ValueError:
                pass

        filled = False
        item_id = self._template_item_id()
        if item_id and self.client and self.client.can_read:
            self.logger.info("BASEテンプレート商品の情報を取得: item_id=%s", item_id)
            remote = self.client.get_item(item_id)
            if remote:
                template.source = "base_api"
                template.raw = remote
                item = remote.get("item") if isinstance(remote, dict) else None
                item = item if isinstance(item, dict) else remote
                template.item_id = str(item.get("item_id") or item_id)
                template.title = str(item.get("title") or "")
                template.detail = str(item.get("detail") or "")
                template.price = int(item.get("price") or template.price or 0)
                template.stock = int(item.get("stock") or template.stock)
                template.visible = int(item.get("visible") if item.get("visible") is not None else template.visible)
                template.item_tax_type = int(item.get("item_tax_type") or template.item_tax_type)
                template.identifier = str(item.get("identifier") or "")
                template.image_urls = [
                    str(item[key])
                    for key in item
                    if str(key).startswith("img") and str(key).endswith("_origin") and item.get(key)
                ]
                cats = self.client.get_item_categories(item_id)
                template.category_ids = [
                    int(row["category_id"])
                    for row in (cats or [])
                    if str(row.get("category_id") or "").isdigit()
                ]
                write_json(self.settings.template_cache_path, template.to_dict())
                filled = True

        if not filled:
            public = self._fetch_public_product(self.settings.base_template_product_url)
            if public:
                self.logger.info("BASEテンプレート: 公開商品ページから取得しました")
                template.source = "public_page"
                template.item_id = str(public.get("item_id") or item_id or "")
                template.title = public.get("title") or ""
                template.detail = public.get("detail") or ""
                if public.get("price"):
                    template.price = int(public["price"])
                if public.get("image_url"):
                    template.image_urls = [str(public["image_url"])]
                write_json(self.settings.template_cache_path, template.to_dict())
                filled = True

        if not filled and isinstance(cached, dict) and cached.get("title"):
            self.logger.info("BASEテンプレート: キャッシュ data/base_template.json を使用します")
            template.source = "cache"
            template.item_id = str(cached.get("item_id") or "")
            template.title = str(cached.get("title") or "")
            template.detail = str(cached.get("detail") or "")
            template.price = int(cached.get("price") or template.price or 0)
            template.stock = int(cached.get("stock") or template.stock)
            template.visible = int(cached.get("visible") if cached.get("visible") is not None else template.visible)
            template.item_tax_type = int(cached.get("item_tax_type") or template.item_tax_type)
            template.identifier = str(cached.get("identifier") or "")
            template.category_ids = list(cached.get("category_ids") or template.category_ids)
            template.image_urls = list(cached.get("image_urls") or [])
            if cached.get("name_pattern"):
                template.name_pattern = str(cached["name_pattern"])
            filled = True

        if not filled:
            self.logger.warning(
                "BASEテンプレート商品をAPI/公開ページから取得できません。"
                " PRODUCT_NAME_PATTERN 等のデフォルトを使います。"
            )
            template.source = "defaults"

        if template.title and not self.settings.base_template_plugin_name:
            for suffix in ("の日本語化ファイル", " WordPressプラグイン 日本語化ファイル"):
                if template.title.endswith(suffix):
                    self.settings.base_template_plugin_name = template.title[: -len(suffix)].strip()
                    break

        template.name_pattern = self._infer_name_pattern(template) or template.name_pattern
        if not template.price:
            template.price = int(self.settings.product_price or 0)
        return template

    def build_listing(
        self,
        info: PluginInfo,
        template: ProductTemplate,
        package: dict,
        quality: dict,
    ) -> dict:
        title = self.render_name(info.name, template)
        detail = self.render_description(info, template, package)
        identifier = re.sub(r"\s+", "", f"{info.slug}-{info.version}")[:50]
        listing = {
            "title": title,
            "detail": detail,
            "price": template.price,
            "stock": template.stock,
            "visible": self.settings.visible_flag,
            "item_tax_type": template.item_tax_type,
            "identifier": identifier,
            "category_ids": template.category_ids,
            "image_url": self._choose_image(info, template, package),
            "generated_image": package.get("image_path") or "",
            "sales_file": package.get("output_zip") or "",
            "publish_mode": self.settings.base_publish_mode,
            "template_item_id": template.item_id,
            "template_source": template.source,
            "sale_package_mode": self.settings.sale_package_mode,
            "plugin_name": info.name,
            "plugin_version": info.version,
            "wordpress_url": info.official_url,
            "translation_count": quality.get("translated_count"),
            "untranslated_count": quality.get("untranslated_count"),
        }
        return listing

    def render_name(self, plugin_name: str, template: ProductTemplate) -> str:
        pattern = template.name_pattern or self.settings.product_name_pattern
        return pattern.replace("{plugin_name}", plugin_name).replace("{version}", "")

    def render_description(self, info: PluginInfo, template: ProductTemplate, package: dict) -> str:
        values = {
            "plugin_name": info.name,
            "version": info.version,
            "official_url": info.official_url,
            "short_description": info.short_description or info.description[:180],
            "slug": info.slug,
            "created": package.get("created") or "",
            "po_name": package.get("po_name") or f"{info.slug}-ja.po",
            "mo_name": package.get("mo_name") or f"{info.slug}-ja.mo",
        }
        custom = self.settings.data_dir / "templates" / "product_description.txt"
        body = DEFAULT_DESCRIPTION
        if custom.exists():
            body = custom.read_text(encoding="utf-8")
        return body.format(**values)

    def _infer_name_pattern(self, template: ProductTemplate) -> str:
        title = template.title.strip()
        old_name = self.settings.base_template_plugin_name.strip()
        if title and old_name and old_name in title:
            return title.replace(old_name, "{plugin_name}")
        if title:
            for suffix in ("の日本語化ファイル", " WordPressプラグイン 日本語化ファイル"):
                if title.endswith(suffix):
                    return "{plugin_name}" + suffix
            match = re.match(r"^(.+?)(\s*WordPressプラグイン.*)$", title)
            if match:
                return "{plugin_name}" + match.group(2)
        return self.settings.product_name_pattern

    def _fetch_public_product(self, url: str) -> dict[str, Any] | None:
        url = (url or "").strip()
        if not url.startswith("http"):
            return None
        host = urlparse(url).hostname or ""
        try:
            http = SafeHttp(timeout=self.settings.http_timeout_seconds, extra_hosts={host} if host else None)
            response = http.request("GET", url, allow_hosts={host} if host else None, allow_redirects=True)
            response.raise_for_status()
            body = response.text
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("公開商品ページの取得に失敗しました: %s", type(exc).__name__)
            return None

        def meta(prop: str) -> str:
            pattern = (
                rf'<meta[^>]+(?:property|name)=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']*)["\']'
                rf'|<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']{re.escape(prop)}["\']'
            )
            match = re.search(pattern, body, re.I)
            if not match:
                return ""
            return htmlmod.unescape((match.group(1) or match.group(2) or "")).strip()

        title = meta("og:title").split("|")[0].strip()
        detail = meta("og:description") or meta("description")
        price_raw = meta("product:price:amount")
        image_url = meta("og:image")
        canonical = meta("og:url") or url
        item_id = ""
        found = re.search(r"/items/(\d+)", canonical)
        if found:
            item_id = found.group(1)
        if not title:
            return None
        price = 0
        if price_raw:
            try:
                price = int(float(price_raw))
            except ValueError:
                price = 0
        return {
            "title": title,
            "detail": detail,
            "price": price,
            "image_url": image_url.split("?")[0] if image_url else "",
            "item_id": item_id,
            "canonical": canonical,
        }

    def _template_item_id(self) -> str:
        if self.settings.base_template_product_id:
            return self.settings.base_template_product_id.strip()
        url = self.settings.base_template_product_url.strip()
        if not url:
            return ""
        match = re.search(r"/items/(\d+)", url)
        if match:
            return match.group(1)
        parsed = urlparse(url)
        if parsed.path.strip("/").isdigit():
            return parsed.path.strip("/")
        return ""

    def _choose_image(self, info: PluginInfo, template: ProductTemplate, package: dict) -> str:
        mode = self.settings.base_image_mode
        if mode == "skip":
            return ""
        if mode == "template" and template.image_urls:
            return template.image_urls[0]
        if mode == "generated":
            return ""
        return info.icon_url or (template.image_urls[0] if template.image_urls else "")
