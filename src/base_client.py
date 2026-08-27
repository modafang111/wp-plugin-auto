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

    def create_item(self, listing: dict[str, Any]) -> dict[str, Any]:
        self._validate_listing(listing)
        payload = {
            "title": listing["title"],
            "detail": listing.get("detail") or "",
            "price": str(listing["price"]),
            "stock": str(listing.get("stock") or self.settings.product_stock),
            "visible": str(listing.get("visible", self.settings.visible_flag)),
            "item_tax_type": str(listing.get("item_tax_type") or 1),
            "identifier": listing.get("identifier") or "",
        }
        self.logger.info("BASE商品登録: title=%s visible=%s", payload["title"], payload["visible"])
        result = self._api("POST", "/items/add", data=payload)
        item = result.get("item") if isinstance(result, dict) else None
        if not isinstance(item, dict) or not item.get("item_id"):
            raise PipelineError("BASE商品登録", f"商品登録レスポンスが不正です: {result}")
        item_id = str(item["item_id"])
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
        }

    def verify_item(self, item_id: str, expected_title: str) -> dict[str, Any]:
        data = self.get_item(item_id)
        if not data:
            raise PipelineError("登録確認", f"登録した商品を再取得できませんでした: {item_id}")
        item = data.get("item") if isinstance(data, dict) else data
        title = str((item or {}).get("title") or "")
        if title != expected_title:
            raise PipelineError("登録確認", f"登録確認で商品名が一致しません: {title}")
        self.logger.info("登録確認: item_id=%s title=%s", item_id, title)
        return item if isinstance(item, dict) else {}

    def upload_digital_file(self, listing: dict[str, Any], zip_path: Path, screenshot_dir: Path) -> str:
        """Best-effort Playwright upload. Stops on CAPTCHA/2FA. Never bypasses auth."""
        if not self.settings.base_upload_digital_file:
            return "skipped"
        if not zip_path.exists():
            raise PipelineError("BASE商品登録", f"販売ZIPがありません: {zip_path}")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise NeedsHumanReview("BASE商品登録", "Playwright が未インストールです。デジタルファイルは手動アップロードが必要です。") from exc

        screenshot_dir.mkdir(parents=True, exist_ok=True)
        state_path = self.settings.playwright_state_path
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context_kwargs: dict[str, Any] = {}
            if state_path.exists():
                context_kwargs["storage_state"] = str(state_path)
            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            try:
                page.goto(self.settings.base_admin_url, wait_until="domcontentloaded", timeout=45000)
                html = page.content()
                if self._needs_manual_auth(html, page.url):
                    shot = screenshot_dir / "base-auth-required.png"
                    page.screenshot(path=str(shot), full_page=True)
                    raise NeedsHumanReview(
                        "BASEログイン",
                        "BASEで手動認証が必要です（CAPTCHA / 二段階認証 / 本人確認）。自動回避は行いません。",
                    )
                if "login" in page.url.lower() or page.get_by_label(re.compile("メール|email", re.I)).count():
                    if not self.settings.base_login_email or not self.settings.base_login_password:
                        raise NeedsHumanReview("BASEログイン", "BASE_LOGIN_EMAIL / BASE_LOGIN_PASSWORD が未設定です。")
                    self._login(page)
                    html = page.content()
                    if self._needs_manual_auth(html, page.url):
                        shot = screenshot_dir / "base-auth-required.png"
                        page.screenshot(path=str(shot), full_page=True)
                        raise NeedsHumanReview("BASEログイン", "BASEで手動認証が必要です。")
                context.storage_state(path=str(state_path))
                shot = screenshot_dir / "base-digital-upload-needed.png"
                page.screenshot(path=str(shot), full_page=True)
                raise NeedsHumanReview(
                    "BASE商品登録",
                    "公式APIにデジタルコンテンツアップロードが無いため、管理画面へのファイル添付は人手確認とします。"
                    f" 販売ZIP: {zip_path} スクリーンショット: {shot}",
                )
            finally:
                context.close()
                browser.close()

    def _login(self, page: Any) -> None:
        email = page.get_by_label(re.compile("メール|email", re.I)).first
        password = page.get_by_label(re.compile("パスワード|password", re.I)).first
        email.fill(self.settings.base_login_email)
        password.fill(self.settings.base_login_password)
        page.get_by_role("button", name=re.compile("ログイン|Login")).first.click()
        page.wait_for_load_state("domcontentloaded")

    @staticmethod
    def _needs_manual_auth(html: str, url: str) -> bool:
        blob = f"{html} {url}".lower()
        needles = ("captcha", "recaptcha", "二段階", "2段階", "本人確認", "認証コード", "認証番号", "sms", "one-time", "otp")
        return any(n in blob for n in needles)

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
