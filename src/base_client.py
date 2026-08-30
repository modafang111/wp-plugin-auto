"""BASE integration.

Confirmed against official docs at implementation time:

Shop owner entry (current):
- https://thebase.com/
- Login: https://admin.thebase.com/users/login
- Developers portal: https://developers.thebase.com/
- Domain moved from thebase.in to thebase.com in March 2023.

API (still .in per official docs https://docs.thebase.in/api/):
- OAuth and item APIs on https://api.thebase.in/

API can:
- OAuth2 access/refresh tokens
- GET /1/items/detail/:item_id  (template, read-only)
- POST /1/items/add             (title, detail, price, stock, visible, identifier, tax)
- POST /1/items/add_image       (image_url only, public URL)
- GET /1/item_categories/detail/:item_id
- POST /1/item_categories/add

API cannot:
- Digital content file upload (Apps「デジタルコンテンツ販売」の管理画面操作)
- Local file image upload (add_image needs a public URL)
- Editing digital-content items (API returns デジタルコンテンツの商品は編集できません)

This module never calls POST /1/items/delete or /1/items/edit on the template item.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from config import Settings
from src.base_admin import BaseAdminClient, is_protected_item_url
from src.exceptions import NeedsHumanReview, PipelineError
from src.utils import read_json, write_json


API_ROOT = "https://api.thebase.in/1"
AUTHORIZE_URL = "https://api.thebase.in/1/oauth/authorize"
TOKEN_URL = "https://api.thebase.in/1/oauth/token"
SCOPES = "read_users read_items write_items"


class BaseClient:
    def __init__(self, settings: Settings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.session = requests.Session()
        stored = read_json(settings.token_path, {}) or {}
        self.access_token = settings.base_access_token or stored.get("access_token") or ""
        self.refresh_token = settings.base_refresh_token or stored.get("refresh_token") or ""

    @property
    def can_read(self) -> bool:
        return bool(self.access_token or (self.refresh_token and self.settings.base_client_id))

    @property
    def can_write(self) -> bool:
        return self.can_read

    def authorization_url(self) -> str:
        if not self.settings.base_client_id:
            raise PipelineError("BASEログイン", "BASE_CLIENT_ID が未設定です。ショップログイン（thebase.com）だけでは発行されません。使う場合は https://developers.thebase.com/ で申請してください。")
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.settings.base_client_id,
                "redirect_uri": self.settings.base_redirect_uri,
                "scope": SCOPES,
            }
        )
        return f"{AUTHORIZE_URL}?{query}"

    def exchange_code(self, code: str) -> dict[str, Any]:
        data = self._token_request(
            {
                "grant_type": "authorization_code",
                "client_id": self.settings.base_client_id,
                "client_secret": self.settings.base_client_secret,
                "code": code,
                "redirect_uri": self.settings.base_redirect_uri,
            }
        )
        self._store_tokens(data)
        return data

    def ensure_token(self) -> str:
        if self.access_token:
            return self.access_token
        if self.refresh_token:
            self.refresh()
            return self.access_token
        raise PipelineError(
            "BASEログイン",
            "BASE のアクセストークンがありません。python app.py --base-auth で認可してください。",
        )

    def refresh(self) -> None:
        if not self.refresh_token:
            raise PipelineError("BASEログイン", "BASE_REFRESH_TOKEN がありません。")
        data = self._token_request(
            {
                "grant_type": "refresh_token",
                "client_id": self.settings.base_client_id,
                "client_secret": self.settings.base_client_secret,
                "refresh_token": self.refresh_token,
                "redirect_uri": self.settings.base_redirect_uri,
            }
        )
        self._store_tokens(data)

    def get_item(self, item_id: str) -> dict[str, Any] | None:
        try:
            return self._api("GET", f"/items/detail/{item_id}")
        except PipelineError as exc:
            self.logger.warning("テンプレート商品の取得に失敗: %s", exc.message)
            return None

    def get_item_categories(self, item_id: str) -> list[dict[str, Any]]:
        try:
            data = self._api("GET", f"/item_categories/detail/{item_id}")
        except PipelineError:
            return []
        rows = data.get("item_categories") if isinstance(data, dict) else None
        return list(rows or []) if isinstance(rows, list) else []

    def create_item(
        self,
        listing: dict[str, Any],
        zip_path: Path | None = None,
        screenshot_dir: Path | None = None,
        otp: str = "",
    ) -> dict[str, Any]:
        self._validate_listing(listing)
        template_id = str(listing.get("template_item_id") or self.settings.base_template_product_id or "")
        zip_file = Path(zip_path or listing.get("sales_file") or "")
        shots = screenshot_dir or (self.settings.screenshots_dir / "base-register")
        if self._should_use_admin(zip_file):
            admin = BaseAdminClient(self.settings, self.logger, otp=otp)
            created = admin.create_digital_item(listing, zip_file, shots)
            if template_id and created.get("item_id") == template_id:
                raise PipelineError("BASE商品登録", "テンプレート商品と同じIDが返りました。登録を中止します。")
            return created
        if not self.can_write:
            raise NeedsHumanReview(
                "BASE商品登録",
                "ショップログイン情報も BASE API トークンも無いため自動登録できません。",
            )
        return self._create_item_api(listing, template_id)

    def _should_use_admin(self, zip_file: Path) -> bool:
        if not (self.settings.base_login_email and self.settings.base_login_password):
            return False
        if zip_file.exists() and self.settings.base_upload_digital_file:
            return True
        return not self.can_write

    def _create_item_api(self, listing: dict[str, Any], template_id: str) -> dict[str, Any]:
        payload = {
            "title": listing["title"],
            "detail": listing.get("detail") or "",
            "price": str(listing["price"]),
            "stock": str(listing.get("stock") or self.settings.product_stock),
            "visible": str(listing.get("visible", self.settings.visible_flag)),
            "item_tax_type": str(listing.get("item_tax_type") or 1),
            "identifier": listing.get("identifier") or "",
        }
        self.logger.info("BASE API 商品登録: title=%s visible=%s", payload["title"], payload["visible"])
        result = self._api("POST", "/items/add", data=payload)
        item = result.get("item") if isinstance(result, dict) else None
        if not isinstance(item, dict) or not item.get("item_id"):
            raise PipelineError("BASE商品登録", f"商品登録レスポンスが不正です: {result}")
        item_id = str(item["item_id"])
        if template_id and item_id == template_id:
            raise PipelineError("BASE商品登録", "テンプレート商品と同じIDが返りました。登録を中止します。")
        for category_id in listing.get("category_ids") or []:
            self._api("POST", "/item_categories/add", data={"item_id": item_id, "category_id": str(category_id)})
        image_url = listing.get("image_url") or ""
        if image_url:
            try:
                self._api(
                    "POST",
                    "/items/add_image",
                    data={"item_id": item_id, "image_no": "1", "image_url": image_url},
                )
            except PipelineError as exc:
                self.logger.warning("商品画像API登録をスキップ: %s", exc.message)
        shop = self.settings.shop_public_base_url.rstrip("/")
        product_url = f"{shop}/items/{item_id}" if shop else ""
        return {
            "item_id": item_id,
            "item": item,
            "product_url": product_url,
            "raw": result,
            "method": "api",
            "file_uploaded": False,
        }

    def verify_item(self, item_id: str, expected_title: str) -> dict[str, Any]:
        template_id = str(self.settings.base_template_product_id or "")
        if template_id and item_id == template_id:
            raise PipelineError("登録確認", "テンプレート商品を確認対象にしていません。")
        if is_protected_item_url(f"/items/{item_id}", template_id):
            raise PipelineError("登録確認", "テンプレート商品の編集はしません。")
        if not self.can_read:
            self.logger.info("登録確認: APIトークンが無いため item_id=%s title=%s をログに残します", item_id, expected_title)
            return {"item_id": item_id, "title": expected_title}
        data = self.get_item(item_id)
        if not data:
            self.logger.warning("APIでは再取得できませんでした（デジタルコンテンツの可能性）。item_id=%s", item_id)
            return {"item_id": item_id, "title": expected_title}
        item = data.get("item") if isinstance(data, dict) else data
        title = str((item or {}).get("title") or "")
        if title and title != expected_title:
            raise PipelineError("登録確認", f"登録確認で商品名が一致しません: {title}")
        self.logger.info("登録確認: item_id=%s title=%s", item_id, title or expected_title)
        return item if isinstance(item, dict) else {}

    def upload_digital_file(self, listing: dict[str, Any], zip_path: Path, screenshot_dir: Path, otp: str = "") -> str:
        """Digital files are attached during Playwright create_item(). API cannot upload them."""
        if listing.get("file_uploaded") or listing.get("method") == "playwright_admin":
            return "already_uploaded"
        if not self.settings.base_upload_digital_file:
            return "skipped"
        raise NeedsHumanReview(
            "BASE商品登録",
            "公式APIにはデジタルコンテンツのファイルアップロードがありません。"
            " BASE_LOGIN_EMAIL を設定し、管理画面からの新規登録を使ってください。"
            f" 販売ZIP: {zip_path}",
        )

    def _validate_listing(self, listing: dict[str, Any]) -> None:
        missing = [key for key in ("title", "price") if not listing.get(key) and listing.get(key) != 0]
        if missing:
            raise PipelineError("BASE商品登録", f"必須項目が不足しています: {', '.join(missing)}")
        if int(listing.get("price") or 0) <= 0:
            raise PipelineError("BASE商品登録", "価格が未設定です。テンプレート商品を取得するか PRODUCT_PRICE を設定してください。")
        detail = listing.get("detail") or ""
        if "日本語化" not in detail and "翻訳" not in detail:
            raise PipelineError("BASE商品登録", "商品説明に日本語化ファイルである旨がありません。登録を中止します。")
        if listing.get("title") and "削除" in str(listing.get("title")):
            raise PipelineError("BASE商品登録", "予期しない商品名です。登録を中止します。")

    def _token_request(self, data: dict[str, str]) -> dict[str, Any]:
        response = self.session.post(
            TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=self.settings.http_timeout_seconds,
        )
        payload = _json(response)
        if response.status_code != 200 or payload.get("error"):
            raise PipelineError("BASEログイン", payload.get("error_description") or f"OAuth token error HTTP {response.status_code}")
        return payload

    def _store_tokens(self, data: dict[str, Any]) -> None:
        self.access_token = str(data.get("access_token") or "")
        if data.get("refresh_token"):
            self.refresh_token = str(data["refresh_token"])
        write_json(
            self.settings.token_path,
            {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "token_type": data.get("token_type"),
                "expires_in": data.get("expires_in"),
                "saved_at": int(time.time()),
            },
        )

    def _api(self, method: str, path: str, data: dict[str, str] | None = None) -> dict[str, Any]:
        token = self.ensure_token()
        url = API_ROOT + path
        headers = {
            "Authorization": f"Bearer {token}",
        }
        if method == "POST":
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        response = self.session.request(
            method,
            url,
            data=data,
            headers=headers,
            timeout=self.settings.http_timeout_seconds,
        )
        payload = _json(response)
        if response.status_code == 400 and payload.get("error") == "invalid_request" and "アクセストークンが無効" in str(payload.get("error_description") or ""):
            self.access_token = ""
            self.refresh()
            return self._api(method, path, data=data)
        if response.status_code != 200 or payload.get("error"):
            raise PipelineError("BASE商品登録", payload.get("error_description") or f"BASE API error HTTP {response.status_code}")
        return payload


def _json(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
        return data if isinstance(data, dict) else {"raw": data}
    except json.JSONDecodeError:
        return {"error": "invalid_json", "error_description": response.text[:500]}
