"""Treat an existing BASE product as a read-only template. Never edit or delete it."""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse

from config import Settings
from src.utils import read_json, write_json
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


class BaseTemplateService:
    def __init__(self, settings: Settings, logger: logging.Logger, client: Any | None = None) -> None:
        self.settings = settings
        self.logger = logger
        self.client = client

    def load(self) -> ProductTemplate:
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

        remote = None
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
        elif isinstance(cached, dict) and cached.get("title"):
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
        else:
            self.logger.warning(
                "BASEテンプレート商品をAPI取得できません。PRODUCT_NAME_PATTERN 等のデフォルトを使います。"
                " 取得後は data/base_template.json にキャッシュされます。"
            )
            template.source = "defaults"

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
        source = (template.detail or "").strip()
        if source:
            text = source
            old_name = self.settings.base_template_plugin_name
            old_version = self.settings.base_template_plugin_version
            if old_name:
                text = text.replace(old_name, info.name)
            if old_version:
                text = text.replace(old_version, info.version)
            text = re.sub(
                r"https?://(?:www\.)?wordpress\.org/plugins/[a-z0-9_-]+/?",
                info.official_url,
                text,
                flags=re.I,
            )
            # Keep original line breaks / structure. Only fill leftover placeholders.
            for key, value in values.items():
                text = text.replace("{" + key + "}", str(value))
            return text
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
            # If the template title already looks like a generic pattern, keep suffix.
            match = re.match(r"^(.+?)(\s*WordPressプラグイン.*)$", title)
            if match:
                return "{plugin_name}" + match.group(2)
        return self.settings.product_name_pattern

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
