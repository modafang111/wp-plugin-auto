"""BASE shop admin (Playwright). Creates unpublished digital items. Never deletes.

Confirmed live on 2026-08-27:
- Login: https://admin.thebase.com/users/login
- New environments hit email OTP at
  https://admin.thebase.com/users/verify_two_factor_auth_via_mail
- This shop's installed Apps do not include「デジタルコンテンツ販売」.
  New items are created as 通常商品 at /shop_admin/items/add (非公開).
- Existing JA listings are regular products (price 550, category 日本語翻訳ファイル).

Template product is read-only. Clicks named 削除 / この商品を削除 / 更新する
on the template item are refused.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from config import Settings
from src.exceptions import NeedsHumanReview, PipelineError
from src.utils import write_json


ADMIN_ORIGIN = "https://admin.thebase.com"
ITEMS_LIST_URL = f"{ADMIN_ORIGIN}/shop_admin/items"
ITEMS_ADD_URL = f"{ADMIN_ORIGIN}/shop_admin/items/add"
FORBIDDEN_CLICK = (
    "削除",
    "削除する",
    "この商品を削除",
    "商品を削除",
    "退会",
    "アカウント削除",
)
TWO_FACTOR_URL_HINTS = (
    "verify_two_factor",
    "two_factor_auth",
    "two-factor",
    "2fa",
)


def is_two_factor_page(url: str, title: str = "", visible_text: str = "") -> bool:
    blob = f"{url} {title}".lower()
    if any(hint in blob for hint in TWO_FACTOR_URL_HINTS):
        return True
    if "認証番号入力" in title or "認証番号の入力" in (title + visible_text):
        return True
    if "/users/login" in url.lower() and "verify" not in url.lower():
        return False
    return False


def is_login_page(url: str) -> bool:
    path = (urlparse(url).path or "").lower()
    return path.rstrip("/") == "/users/login"


def is_protected_item_url(url: str, item_id: str) -> bool:
    if not item_id or not item_id.isdigit():
        return False
    return bool(re.search(rf"/items/(?:edit/)?{re.escape(item_id)}(?:/|$|\?)", url or ""))


def forbidden_control_name(name: str) -> bool:
    text = (name or "").strip()
    if not text:
        return False
    if text in FORBIDDEN_CLICK:
        return True
    return "削除" in text


def pending_path(settings: Settings) -> Path:
    return settings.data_dir / "playwright" / "pending.json"


class BaseAdminClient:
    def __init__(self, settings: Settings, logger: logging.Logger, *, otp: str = "") -> None:
        self.settings = settings
        self.logger = logger
        self.otp = (otp or "").strip()

    def create_digital_item(
        self,
        listing: dict[str, Any],
        zip_path: Path,
        screenshot_dir: Path,
    ) -> dict[str, Any]:
        if not zip_path.exists():
            raise PipelineError("BASE商品登録", f"販売ZIPがありません: {zip_path}")
        if zip_path.stat().st_size < 1024:
            raise PipelineError("BASE商品登録", "デジタルコンテンツは 1KB 未満のファイルをアップロードできません。")
        template_id = str(listing.get("template_item_id") or self.settings.base_template_product_id or "")
        if template_id and listing.get("title") and template_id in str(listing.get("title")):
            raise PipelineError("BASE商品登録", "テンプレート商品IDが商品名に含まれています。登録を中止します。")
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeout
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise NeedsHumanReview("BASE商品登録", "Playwright が未インストールです。pip install playwright && python -m playwright install chromium") from exc

        state_path = self.settings.playwright_state_path
        state_path.parent.mkdir(parents=True, exist_ok=True)
        pending = _read_pending(self.settings)

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=self.settings.playwright_headless,
                args=["--disable-dev-shm-usage"],
            )
            context_kwargs: dict[str, Any] = {
                "locale": "ja-JP",
                "timezone_id": "Asia/Tokyo",
                "viewport": {"width": 1600, "height": 1100},
            }
            if state_path.exists():
                context_kwargs["storage_state"] = str(state_path)
            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            try:
                self._login_or_resume(page, context, screenshot_dir, pending)
                self._guard_template(page.url, template_id)
                self._open_new_digital_form(page, screenshot_dir, template_id)
                self._fill_form(page, listing, zip_path, screenshot_dir)
                created = self._submit_new_item(page, listing, screenshot_dir, template_id)
                context.storage_state(path=str(state_path))
                _clear_pending(self.settings)
                self.logger.info(
                    "BASE管理画面で非公開商品を登録: item_id=%s url=%s",
                    created.get("item_id"),
                    created.get("product_url") or created.get("admin_url"),
                )
                return created
            except NeedsHumanReview:
                self._snapshot(page, screenshot_dir / "base-needs-review.png")
                try:
                    context.storage_state(path=str(state_path))
                except Exception:
                    pass
                raise
            except PlaywrightTimeout as exc:
                self._snapshot(page, screenshot_dir / "base-timeout.png")
                raise NeedsHumanReview("BASE商品登録", f"管理画面の操作が時間切れです。画面構成が変わった可能性があります。 {exc}") from exc
            finally:
                context.close()
                browser.close()

    def replace_item_image(self, item_id: str, image_path: Path, screenshot_dir: Path) -> None:
        template_id = str(self.settings.base_template_product_id or "")
        if not item_id.isdigit():
            raise PipelineError("BASE商品登録", "商品IDが不正です。")
        if item_id == template_id:
            raise PipelineError("BASE商品登録", "テンプレート商品の画像は変更しません。")
        if not image_path.exists():
            raise PipelineError("BASE商品登録", f"画像がありません: {image_path}")
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=self.settings.playwright_headless,
                args=["--disable-dev-shm-usage"],
            )
            context = browser.new_context(
                locale="ja-JP",
                timezone_id="Asia/Tokyo",
                viewport={"width": 1600, "height": 1100},
                **({"storage_state": str(self.settings.playwright_state_path)} if self.settings.playwright_state_path.exists() else {}),
            )
            page = context.new_page()
            try:
                self._login_or_resume(page, context, screenshot_dir, _read_pending(self.settings))
                edit_url = f"{ADMIN_ORIGIN}/shop_admin/items/edit/{item_id}"
                page.goto(edit_url, wait_until="domcontentloaded", timeout=45000)
                try:
                    page.wait_for_load_state("networkidle", timeout=20000)
                except Exception:
                    page.wait_for_timeout(1500)
                self._guard_template(page.url, template_id)
                if str(item_id) not in page.url:
                    raise PipelineError("BASE商品登録", f"指定した商品の編集画面ではありません: {page.url}")
                page.locator("input[type=file]").first.wait_for(state="attached", timeout=20000)
                listing = {"generated_image": str(image_path)}
                if not self._upload_product_image(page, listing):
                    raise NeedsHumanReview("BASE商品登録", "画像のファイル欄が見つかりません。")
                save = _first_existing(
                    page,
                    [
                        lambda: page.get_by_role("button", name="変更を保存", exact=True),
                        lambda: page.get_by_role("button", name=re.compile(r"^変更を保存$")),
                    ],
                )
                if save is None:
                    raise NeedsHumanReview("BASE商品登録", "「変更を保存」が見つかりません。削除は押しません。")
                self._safe_click(page, save, "変更を保存")
                page.wait_for_timeout(2500)
                self._guard_template(page.url, template_id)
                self._snapshot(page, screenshot_dir / "base-image-updated.png")
                self.logger.info("商品画像を更新しました: item_id=%s", item_id)
                context.storage_state(path=str(self.settings.playwright_state_path))
            except PlaywrightTimeout as exc:
                self._snapshot(page, screenshot_dir / "base-image-timeout.png")
                raise NeedsHumanReview("BASE商品登録", f"画像更新が時間切れです。 {exc}") from exc
            finally:
                context.close()
                browser.close()

    def _login_or_resume(self, page: Any, context: Any, screenshot_dir: Path, pending: dict[str, Any]) -> None:
        state_path = self.settings.playwright_state_path
        if state_path.exists():
            page.goto(ITEMS_LIST_URL, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                page.wait_for_timeout(2000)
            if self._already_logged_in(page):
                self.logger.info("BASE管理画面: 保存済みセッションでログイン済み")
                return

        if self.otp:
            resume_url = pending.get("url") if pending.get("purpose") == "two_factor" else ""
            page.goto(
                resume_url or f"{ADMIN_ORIGIN}/users/verify_two_factor_auth_via_mail",
                wait_until="domcontentloaded",
                timeout=45000,
            )
            if is_two_factor_page(page.url, page.title()):
                self.logger.info("認証番号入力画面を開きました（番号はログに出しません）")
                self._complete_two_factor(page, screenshot_dir)
                self._wait_logged_in(page, screenshot_dir)
                context.storage_state(path=str(self.settings.playwright_state_path))
                _clear_pending(self.settings)
                return

        page.goto(self.settings.base_admin_url, wait_until="domcontentloaded", timeout=45000)
        if self._already_logged_in(page):
            self.logger.info("BASE管理画面: 保存済みセッションでログイン済み")
            return

        wall = self._auth_wall(page)
        if wall == "two_factor":
            self._handle_two_factor(page, context, screenshot_dir)
            return
        if wall:
            self._fail_auth(page, screenshot_dir, wall)

        if is_login_page(page.url) or page.get_by_label(re.compile("メールアドレス")).count():
            if not self.settings.base_login_email or not self.settings.base_login_password:
                raise NeedsHumanReview("BASEログイン", "BASE_LOGIN_EMAIL / BASE_LOGIN_PASSWORD が未設定です。")
            self.logger.info("BASE管理画面へパスワードログインします")
            email = page.get_by_label(re.compile("メールアドレス")).first
            password = page.get_by_label(re.compile("^パスワード$")).first
            email.fill(self.settings.base_login_email)
            password.fill(self.settings.base_login_password)
            self._safe_click(page, page.get_by_role("button", name="ログイン", exact=True).first, "ログイン")
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(1500)

        wall = self._auth_wall(page)
        if wall == "two_factor":
            self._handle_two_factor(page, context, screenshot_dir)
            return
        if wall:
            self._fail_auth(page, screenshot_dir, wall)
        if is_login_page(page.url):
            self._snapshot(page, screenshot_dir / "base-login-failed.png")
            raise NeedsHumanReview("BASEログイン", "パスワードログインに失敗しました。メールアドレスまたはパスワードを確認してください。")
        self._wait_logged_in(page, screenshot_dir)
        context.storage_state(path=str(self.settings.playwright_state_path))

    def _handle_two_factor(self, page: Any, context: Any, screenshot_dir: Path) -> None:
        context.storage_state(path=str(self.settings.playwright_state_path))
        write_json(
            pending_path(self.settings),
            {"purpose": "two_factor", "url": page.url, "saved_at": int(time.time())},
        )
        self._snapshot(page, screenshot_dir / "base-two-factor.png")
        dump_page(page, screenshot_dir / "base-two-factor.txt")
        if self.otp:
            self._complete_two_factor(page, screenshot_dir)
            self._wait_logged_in(page, screenshot_dir)
            context.storage_state(path=str(self.settings.playwright_state_path))
            _clear_pending(self.settings)
            return
        raise NeedsHumanReview(
            "BASEログイン",
            "BASEがメール認証番号の入力を求めています（新しい環境からのログイン）。"
            " 認証の回避はしません。"
            " 届いた6桁を次で渡してください: python app.py --test-base --otp 123456"
            " （番号はログに書きません）",
        )

    def _complete_two_factor(self, page: Any, screenshot_dir: Path) -> None:
        if not self.otp:
            raise NeedsHumanReview("BASEログイン", "認証番号が空です。")
        if not re.fullmatch(r"\d{4,8}", self.otp):
            raise PipelineError("BASEログイン", "認証番号の形式が不正です。")
        field = page.get_by_label(re.compile("認証番号")).first
        field.fill(self.otp)
        self._safe_click(page, page.get_by_role("button", name="ログイン", exact=True).first, "ログイン")
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(2000)
        if is_two_factor_page(page.url, page.title()):
            self._snapshot(page, screenshot_dir / "base-two-factor-invalid.png")
            raise NeedsHumanReview(
                "BASEログイン",
                "認証番号が一致しませんでした。番号は一度限りです。新しい番号で python app.py --test-base --otp を再実行してください。",
            )
        wall = self._auth_wall(page)
        if wall and wall != "two_factor":
            self._fail_auth(page, screenshot_dir, wall)

    def _already_logged_in(self, page: Any) -> bool:
        if is_login_page(page.url) or is_two_factor_page(page.url, page.title()):
            return False
        if "admin.thebase.com" not in (urlparse(page.url).netloc or ""):
            return False
        if page.get_by_role("link", name=re.compile("商品管理")).count():
            return True
        if page.get_by_text("商品管理", exact=True).count():
            return True
        return "/shop_admin" in page.url

    def _wait_logged_in(self, page: Any, screenshot_dir: Path) -> None:
        self._dismiss_noise(page)
        if self._already_logged_in(page):
            return
        if is_two_factor_page(page.url, page.title()):
            self._snapshot(page, screenshot_dir / "base-two-factor.png")
            raise NeedsHumanReview("BASEログイン", "認証番号の入力がまだ完了していません。")
        page.goto(ITEMS_LIST_URL, wait_until="domcontentloaded", timeout=45000)
        self._dismiss_noise(page)
        if is_login_page(page.url) or is_two_factor_page(page.url, page.title()):
            self._snapshot(page, screenshot_dir / "base-login-required.png")
            raise NeedsHumanReview("BASEログイン", "管理画面に入れませんでした。")
        if not self._already_logged_in(page) and "shop_admin" not in page.url:
            self._snapshot(page, screenshot_dir / "base-unknown-after-login.png")
            dump_page(page, screenshot_dir / "base-unknown-after-login.txt")
            raise NeedsHumanReview("BASEログイン", f"ログイン後の画面を認識できません: {page.url}")

    def _open_new_digital_form(self, page: Any, screenshot_dir: Path, template_id: str) -> None:
        page.goto(ITEMS_LIST_URL, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            page.wait_for_timeout(2500)
        self._guard_template(page.url, template_id)
        self._dismiss_noise(page)
        self._snapshot(page, screenshot_dir / "base-items.png")
        dump_page(page, screenshot_dir / "base-items.txt")

        add = _first_existing(
            page,
            [
                lambda: page.get_by_role("button", name=re.compile(r"商品を登録")),
                lambda: page.get_by_role("link", name=re.compile(r"商品を登録")),
            ],
        )
        digital_opened = False
        if add is not None:
            self._safe_click(page, add, "商品を登録")
            page.wait_for_timeout(800)
            digital = _first_existing(
                page,
                [
                    lambda: page.get_by_role("menuitem", name=re.compile(r"デジタルコンテンツ")),
                    lambda: page.get_by_role("link", name=re.compile(r"デジタルコンテンツ")),
                    lambda: page.get_by_text("デジタルコンテンツ", exact=True),
                ],
            )
            if digital is not None:
                self._safe_click(page, digital, "デジタルコンテンツ")
                digital_opened = True
                page.wait_for_load_state("domcontentloaded")
                page.wait_for_timeout(1000)
            else:
                self.logger.warning(
                    "「商品を登録」メニューにデジタルコンテンツがありません。"
                    " 通常商品の新規登録画面へ進みます（非公開）。"
                )
                page.goto(ITEMS_ADD_URL, wait_until="domcontentloaded", timeout=45000)
        else:
            page.goto(ITEMS_ADD_URL, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            page.wait_for_timeout(1500)
        page.get_by_label(re.compile("商品名")).first.wait_for(timeout=20000)
        self._guard_template(page.url, template_id)
        if "/items/add" not in page.url and not digital_opened:
            raise NeedsHumanReview("BASE商品登録", f"新規登録画面ではありません: {page.url}")
        self._snapshot(page, screenshot_dir / "base-new-item.png")
        dump_page(page, screenshot_dir / "base-new-item.txt")

    def _fill_form(self, page: Any, listing: dict[str, Any], zip_path: Path, screenshot_dir: Path) -> None:
        name = page.locator("#itemDetail_name")
        if name.count():
            name.first.fill(listing["title"])
        elif not _fill_by_labels(page, ["商品名"], listing["title"]):
            raise NeedsHumanReview("BASE商品登録", "商品名の入力欄が見つかりません。")
        detail = listing.get("detail") or ""
        if page.locator("#itemDetail_detail").count():
            page.locator("#itemDetail_detail").first.fill(detail)
        elif not _fill_by_labels(page, ["商品説明"], detail):
            if page.locator("textarea").count():
                page.locator("textarea").first.fill(detail)
            else:
                self.logger.warning("商品説明の入力欄が見つからないため本文は未入力です")
        price = page.locator("#itemDetail_price")
        if price.count():
            price.first.fill(str(listing["price"]))
        elif not _fill_by_labels(page, [r"価格"], str(listing["price"])):
            raise NeedsHumanReview("BASE商品登録", "価格の入力欄が見つかりません。")
        stock = str(min(int(listing.get("stock") or 99), 10000))
        stock_box = page.locator("#itemDetail_stock")
        if stock_box.count():
            stock_box.first.fill(stock)
        elif not _fill_by_labels(page, ["在庫数"], stock):
            self.logger.warning("在庫の入力欄が見つかりません。画面上の初期値を使います")
        self._set_unpublished(page)
        pin = page.locator("#orderfirst")
        if pin.count():
            if pin.first.is_checked():
                page.locator("label[for='orderfirst']").click()
        else:
            pin_l = page.get_by_label(re.compile("商品一覧の先頭に追加する"))
            if pin_l.count():
                pin_l.first.set_checked(False, force=True)
        cat = page.locator("label").filter(has_text=re.compile(r"^日本語翻訳ファイル$"))
        if cat.count():
            cat.first.click()
            self.logger.info("カテゴリ「日本語翻訳ファイル」を選択しました")
        listing["image_uploaded"] = self._upload_product_image(page, listing)
        zip_input = _choose_zip_input(page)
        listing["file_uploaded"] = False
        if zip_input is None:
            self.logger.warning(
                "デジタルコンテンツ販売 App が未導入のため ZIP は未添付です。"
                " 既存ショップと同じ通常商品（非公開）として登録します。"
            )
        else:
            zip_input.set_input_files(str(zip_path))
            listing["file_uploaded"] = True
            page.wait_for_timeout(500)
        self._snapshot(page, screenshot_dir / "base-form-filled.png")

    def _upload_product_image(self, page: Any, listing: dict[str, Any]) -> bool:
        path = Path(listing.get("generated_image") or listing.get("image_path") or "")
        if not path.exists() or path.stat().st_size < 32:
            return False
        inputs = page.locator("input[type=file]")
        try:
            inputs.first.wait_for(state="attached", timeout=8000)
        except Exception:
            return False
        chosen = None
        for i in range(inputs.count()):
            el = inputs.nth(i)
            accept = (el.get_attribute("accept") or "").lower()
            if "zip" in accept:
                continue
            if "image" in accept or accept == "":
                chosen = el
                if "image" in accept:
                    break
        if chosen is None:
            return False
        chosen.set_input_files(str(path))
        page.wait_for_timeout(800)
        self.logger.info("商品画像を添付しました: %s", path.name)
        return True

    def _set_unpublished(self, page: Any) -> None:
        label = page.locator("label.c-radio__label").filter(has_text=re.compile(r"^(非公開|未公開)$"))
        if label.count():
            label.first.click()
            self.logger.info("公開状態を非公開にしました")
            return
        labeled = page.get_by_text(re.compile(r"^(非公開|未公開)$"), exact=True)
        if labeled.count():
            labeled.first.click()
            self.logger.info("公開状態を非公開にしました")
            return
        self.logger.warning("公開/非公開スイッチが見つかりません。BASE_PUBLISH_MODE=draft のまま登録します")

    def _submit_new_item(self, page: Any, listing: dict[str, Any], screenshot_dir: Path, template_id: str) -> dict[str, Any]:
        submit = _first_existing(
            page,
            [
                lambda: page.get_by_role("button", name="商品を登録", exact=True),
                lambda: page.get_by_role("button", name=re.compile(r"^商品を登録$")),
                lambda: page.get_by_role("button", name="登録する", exact=True),
            ],
        )
        if submit is None:
            raise NeedsHumanReview("BASE商品登録", "「商品を登録」ボタンが見つかりません。更新/削除は押しません。")
        self._safe_click(page, submit, "商品を登録")
        try:
            page.wait_for_url(re.compile(r"/shop_admin/items/(?:edit/\d+)?(?:\?.*)?$"), timeout=45000)
        except Exception:
            page.wait_for_timeout(3000)
        self._guard_template(page.url, template_id)
        if "/items/add" in page.url:
            self._snapshot(page, screenshot_dir / "base-after-submit.png")
            dump_page(page, screenshot_dir / "base-after-submit.txt")
            raise NeedsHumanReview(
                "BASE商品登録",
                "登録ボタンを押したあと新規登録画面のままです。必須項目不足の可能性があります。削除はしていません。",
            )
        self._snapshot(page, screenshot_dir / "base-after-submit.png")
        dump_page(page, screenshot_dir / "base-after-submit.txt")
        item_id = _extract_item_id(page.url, template_id)
        if not item_id:
            page.goto(ITEMS_LIST_URL, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                page.wait_for_timeout(2000)
            search = page.get_by_placeholder(re.compile("検索"))
            if search.count():
                search.first.fill(listing["title"])
                search.first.press("Enter")
                page.wait_for_timeout(2000)
            item_id = _find_item_id_from_list(page, listing["title"], template_id)
        if not item_id:
            raise NeedsHumanReview(
                "BASE商品登録",
                "登録ボタンは押しましたが、新しい商品IDを画面から取得できませんでした。管理画面で未公開商品を確認してください。",
            )
        shop = (self.settings.shop_public_base_url or "").rstrip("/")
        product_url = f"{shop}/items/{item_id}" if shop else ""
        admin_url = f"{ADMIN_ORIGIN}/shop_admin/items/edit/{item_id}"
        return {
            "item_id": item_id,
            "product_url": product_url,
            "admin_url": admin_url,
            "visible": 0,
            "method": "playwright_admin",
            "file_uploaded": bool(listing.get("file_uploaded")),
            "image_uploaded": bool(listing.get("image_uploaded")),
        }

    def _auth_wall(self, page: Any) -> str:
        url = page.url
        title = ""
        try:
            title = page.title()
        except Exception:
            title = ""
        if is_two_factor_page(url, title):
            return "two_factor"
        visible = ""
        try:
            visible = page.inner_text("body", timeout=2000)[:4000]
        except Exception:
            visible = ""
        blob = f"{title}\n{visible}"
        if re.search(r"(認証コードを入力|二段階認証|2段階認証|ワンタイムパスワード)", blob):
            return "two_factor"
        if re.search(r"(本人確認|本人認証)", blob) and "認証番号" in blob:
            return "two_factor"
        if "recaptcha" in visible.lower() and "私はロボットではありません" in visible:
            return "captcha"
        if re.search(r"パスキーで認証", blob) and not page.get_by_label(re.compile("パスワード")).count():
            return "passkey"
        return ""

    def _fail_auth(self, page: Any, screenshot_dir: Path, wall: str) -> None:
        self._snapshot(page, screenshot_dir / f"base-auth-{wall}.png")
        raise NeedsHumanReview(
            "BASEログイン",
            f"BASEで手動認証が必要です（{wall}）。CAPTCHA / 二段階認証 / パスキー / 本人確認の回避は行いません。",
        )

    def _guard_template(self, url: str, template_id: str) -> None:
        if is_protected_item_url(url, template_id):
            raise PipelineError(
                "BASE商品登録",
                f"テンプレート商品 {template_id} の編集画面へ進もうとしたため停止しました。テンプレートは参照専用です。",
            )

    def _safe_click(self, page: Any, locator: Any, expected_name: str) -> None:
        if forbidden_control_name(expected_name):
            raise PipelineError("BASE商品登録", f"禁止操作です: {expected_name}")
        try:
            name = (locator.inner_text(timeout=1000) or "").strip().splitlines()[0]
        except Exception:
            name = expected_name
        if forbidden_control_name(name):
            raise PipelineError("BASE商品登録", f"禁止操作です: {name}")
        locator.click()

    def _dismiss_noise(self, page: Any) -> None:
        for name in ("閉じる", "スキップ", "後で", "今はしない", "OK"):
            loc = page.get_by_role("button", name=name, exact=True)
            if loc.count():
                try:
                    loc.first.click(timeout=1000)
                except Exception:
                    pass

    @staticmethod
    def _snapshot(page: Any, path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(path), full_page=True)
        except Exception:
            pass


def dump_page(page: Any, out: Path) -> None:
    lines = [f"URL: {page.url}", f"TITLE: {page.title()}", "", "== buttons =="]
    try:
        for loc in page.get_by_role("button").all()[:80]:
            try:
                text = loc.inner_text(timeout=400).strip().replace("\n", " ")
            except Exception:
                text = ""
            if text:
                lines.append(text)
        lines.append("")
        lines.append("== links ==")
        for loc in page.get_by_role("link").all()[:80]:
            try:
                text = loc.inner_text(timeout=400).strip().replace("\n", " ")
            except Exception:
                continue
            if text:
                lines.append(text)
    except Exception:
        pass
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fill_by_labels(page: Any, labels: list[str], value: str) -> bool:
    for label in labels:
        loc = page.get_by_label(re.compile(label))
        if loc.count():
            loc.first.fill(str(value))
            return True
    return False


def _first_existing(page: Any, factories: list) -> Any | None:
    for factory in factories:
        try:
            loc = factory()
            if loc.count():
                return loc.first
        except Exception:
            continue
    return None


def _choose_zip_input(page: Any) -> Any | None:
    labeled = page.get_by_label(re.compile("ファイルを選択|デジタルコンテンツ"))
    if labeled.count():
        handle = labeled.first
        try:
            if (handle.get_attribute("type") or "").lower() == "file":
                return handle
        except Exception:
            pass
    inputs = page.locator("input[type=file]")
    count = inputs.count()
    if count == 0:
        return None
    for i in range(count):
        el = inputs.nth(i)
        accept = (el.get_attribute("accept") or "").lower()
        if "image" in accept:
            continue
        if "zip" in accept or "octet" in accept or "application" in accept:
            return el
    return None


def _extract_item_id(url: str, template_id: str) -> str:
    match = re.search(r"/items/(?:edit/)?(\d+)", url or "")
    if not match:
        return ""
    item_id = match.group(1)
    if item_id == template_id:
        return ""
    return item_id


def _find_item_id_from_list(page: Any, title: str, template_id: str) -> str:
    try:
        link = page.get_by_role("link", name=title).first
        if link.count():
            href = link.get_attribute("href") or ""
            found = _extract_item_id(href, template_id)
            if found:
                return found
    except Exception:
        pass
    return ""


def _read_pending(settings: Settings) -> dict[str, Any]:
    path = pending_path(settings)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _clear_pending(settings: Settings) -> None:
    path = pending_path(settings)
    if path.exists():
        path.unlink()
