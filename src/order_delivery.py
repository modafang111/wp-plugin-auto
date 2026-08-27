"""Send sold Japanese localization ZIPs to buyers.

BASE official path: install Apps「デジタルコンテンツ販売」and register items as
digital content. Then BASE itself puts a download button on the purchase page
and in the buyer email.

This module covers the current shop, which sells 通常商品 (the Digital Content
App is not installed). It reads unpaid-complete orders from the shop admin
session, attaches the matching -ja.zip, and emails the buyer.
"""

from __future__ import annotations

import logging
import re
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import Settings
from src.base_admin import ORDERS_LIST_URL, SKIP_ORDER_STATUSES, BaseAdminClient
from src.database import Database
from src.exceptions import PipelineError
from src.mailer import Mailer
from src.utils import read_json, redact_email


IDENTIFIER_RE = re.compile(r"^([a-z0-9_-]+)-(\d+(?:\.\d+)*)")
JA_ZIP_RE = re.compile(r".+-ja\.zip$", re.I)


@dataclass
class DeliveryPlan:
    unique_key: str
    order_item_id: str
    item_id: str
    title: str
    buyer_email: str
    buyer_name: str
    zip_path: Path | None
    skip_reason: str = ""
    already_digital: bool = False


def is_safe_sales_zip(path: Path, root: Path, max_bytes: int) -> str:
    """Return empty string if the file may be emailed. Otherwise a reason."""
    try:
        resolved = path.resolve()
        root_resolved = root.resolve()
    except OSError:
        return "ZIPパスを解決できません。"
    if root_resolved not in resolved.parents and resolved != root_resolved:
        return "ZIPがプロジェクト外にあります。"
    if not resolved.exists() or not resolved.is_file():
        return "ZIPがありません。"
    if not JA_ZIP_RE.match(resolved.name):
        return "販売用ZIP（*-ja.zip）ではありません。"
    size = resolved.stat().st_size
    if size < 1024:
        return "ZIPが小さすぎます。"
    if size > max_bytes:
        return f"ZIPが大きすぎます（上限 {max_bytes} bytes）。"
    if not zipfile.is_zipfile(resolved):
        return "ZIPとして開けません。"
    return ""


def load_delivery_map(path: Path) -> dict[str, Path]:
    raw = read_json(path, {}) or {}
    if not isinstance(raw, dict):
        return {}
    mapping: dict[str, Path] = {}
    for key, value in raw.items():
        item_id = str(key).strip()
        if not item_id:
            continue
        if isinstance(value, dict):
            zip_value = value.get("zip") or value.get("path") or ""
        else:
            zip_value = value
        if zip_value:
            mapping[item_id] = Path(str(zip_value))
    return mapping


def resolve_sales_zip(
    *,
    item_id: str,
    title: str,
    identifier: str,
    jobs: list[dict[str, Any]],
    output_dir: Path,
    delivery_map: dict[str, Path],
    root: Path,
) -> Path | None:
    if item_id and item_id in delivery_map:
        mapped = delivery_map[item_id]
        return mapped if mapped.is_absolute() else (root / mapped)

    for job in jobs:
        if str(job.get("base_product_id") or "") != str(item_id):
            continue
        zip_value = job.get("output_zip") or ""
        if zip_value:
            return Path(str(zip_value))
        slug = str(job.get("plugin_slug") or "")
        version = str(job.get("plugin_version") or "")
        if slug and version:
            candidate = output_dir / f"{slug}-{version}-ja.zip"
            if candidate.exists():
                return candidate

    ident = (identifier or "").strip()
    match = IDENTIFIER_RE.match(ident)
    if match:
        candidate = output_dir / f"{match.group(1)}-{match.group(2)}-ja.zip"
        if candidate.exists():
            return candidate

    lowered = _norm(title)
    ident_norm = _norm(identifier)
    if output_dir.exists():
        for path in sorted(output_dir.glob("*-ja.zip"), reverse=True):
            stem = path.name[: -len("-ja.zip")] if path.name.lower().endswith("-ja.zip") else path.stem
            slug = re.sub(r"-\d+(?:\.\d+)*$", "", stem)
            if slug and (_norm(slug) in lowered or slug in ident or _norm(slug) in ident_norm):
                return path
    return None


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def buyer_greeting(first_name: str, last_name: str) -> str:
    last = (last_name or "").strip()
    first = (first_name or "").strip()
    if last or first:
        return f"{last}{first} 様"
    return "お客様"


def looks_like_digital_item(item: dict[str, Any]) -> bool:
    blob = " ".join(str(v) for v in item.values() if v is not None).lower()
    return any(key in item for key in ("download_limit", "download_expire", "remaining_download", "download_url")) or (
        "download" in blob and "digital" in blob
    )


def parse_order_plans(
    header: dict[str, Any],
    *,
    jobs: list[dict[str, Any]],
    output_dir: Path,
    delivery_map: dict[str, Path],
    root: Path,
    already_sent: set[tuple[str, str]],
) -> list[DeliveryPlan]:
    unique_key = str(header.get("unique_key") or "")
    buyer = header.get("buyer") if isinstance(header.get("buyer"), dict) else {}
    email = str((buyer or {}).get("mail_address") or "")
    name = buyer_greeting(str((buyer or {}).get("first_name") or ""), str((buyer or {}).get("last_name") or ""))
    items = header.get("orders") if isinstance(header.get("orders"), list) else []
    plans: list[DeliveryPlan] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        order_item_id = str(item.get("id") or item.get("order_item_id") or "")
        item_id = str(item.get("item_id") or "")
        title = str(item.get("name") or item.get("title") or "")
        status = str(item.get("status") or header.get("type") or "").lower()
        plan = DeliveryPlan(
            unique_key=unique_key,
            order_item_id=order_item_id,
            item_id=item_id,
            title=title,
            buyer_email=email,
            buyer_name=name,
            zip_path=None,
        )
        if not order_item_id:
            plan.skip_reason = "注文商品IDがありません。"
        elif (unique_key, order_item_id) in already_sent:
            plan.skip_reason = "この注文は既に送信済みです。"
        elif status in SKIP_ORDER_STATUSES:
            plan.skip_reason = f"注文ステータスが {status} のため送信しません。"
        elif looks_like_digital_item(item):
            plan.already_digital = True
            plan.skip_reason = "公式デジタルコンテンツのため BASE 側でダウンロード案内されます。"
        elif not email or "@" not in email:
            plan.skip_reason = "購入者メールアドレスがありません。"
        else:
            zip_path = resolve_sales_zip(
                item_id=item_id,
                title=title,
                identifier=str(item.get("item_identifier") or ""),
                jobs=jobs,
                output_dir=output_dir,
                delivery_map=delivery_map,
                root=root,
            )
            plan.zip_path = zip_path
            if zip_path is None:
                plan.skip_reason = "対応する日本語化ZIPが見つかりません。"
        plans.append(plan)
    return plans


def delivery_email_body(plan: DeliveryPlan) -> str:
    return "\n".join(
        [
            f"{plan.buyer_name}",
            "",
            "このたびはご購入ありがとうございます。",
            f"「{plan.title}」の日本語化ファイルを添付しました。",
            "",
            "【使い方】",
            "1. 添付ZIPを解凍する",
            "2. WordPress のサイト言語を日本語にする",
            "3. 次の場所へ .po と .mo を置く",
            "   wp-content/languages/plugins/",
            "4. 管理画面を再読み込みする",
            "",
            "プラグイン本体は WordPress.org 公式からインストールしてください。",
            "本ZIPは日本語化ファイルのみです。",
            "",
            "このメールに心当たりがない場合は破棄してください。",
            "",
        ]
    )


class OrderDeliveryService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        mailer: Mailer,
        logger: logging.Logger,
        *,
        otp: str = "",
    ) -> None:
        self.settings = settings
        self.db = db
        self.mailer = mailer
        self.logger = logger
        self.admin = BaseAdminClient(settings, logger, otp=otp)

    def run_once(self, *, dry_run: bool = False) -> dict[str, int]:
        counts = {"sent": 0, "skipped": 0, "failed": 0, "orders": 0}
        jobs = self.db.all_jobs_with_products()
        delivery_map = load_delivery_map(self.settings.delivery_map_path)
        screenshot_dir = self.settings.screenshots_dir / "deliver-orders"
        with self.admin.logged_in_page(screenshot_dir) as page:
            page.goto(ORDERS_LIST_URL, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                page.wait_for_timeout(1500)
            summaries = self.admin.list_order_summaries(page, statuses=["ordered"])
            counts["orders"] = len(summaries)
            self.logger.info("未対応の注文: %s 件", len(summaries))
            for summary in summaries:
                unique_key = str(summary.get("unique_key") or "")
                order_type = str(summary.get("type") or "order")
                if not unique_key:
                    continue
                header = self.admin.get_order_detail(page, unique_key, order_type)
                items = header.get("orders") if isinstance(header.get("orders"), list) else []
                sent_ids: set[tuple[str, str]] = set()
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    oid = str(item.get("id") or "")
                    row = self.db.get_delivery(unique_key, oid) if oid else None
                    if row and row.get("status") == "sent":
                        sent_ids.add((unique_key, oid))
                plans = parse_order_plans(
                    header,
                    jobs=jobs,
                    output_dir=self.settings.output_dir,
                    delivery_map=delivery_map,
                    root=self.settings.root,
                    already_sent=sent_ids,
                )
                dispatched_ids: list[str] = []
                for plan in plans:
                    result = self._handle_plan(plan, dry_run=dry_run)
                    counts[result] = counts.get(result, 0) + 1
                    if result == "sent" and not dry_run:
                        dispatched_ids.append(plan.order_item_id)
                if dispatched_ids and self.settings.delivery_mark_dispatched and not dry_run:
                    try:
                        self.admin.dispatch_order_items(
                            page,
                            unique_key,
                            dispatched_ids,
                            add_comment="日本語化ファイルをメール送信しました。",
                        )
                        self.logger.info("注文 %s を対応済にしました", unique_key)
                    except PipelineError as exc:
                        self.logger.warning("対応済への更新に失敗: %s", exc.message)
        return counts

    def _handle_plan(self, plan: DeliveryPlan, *, dry_run: bool) -> str:
        if plan.skip_reason:
            self.logger.info(
                "送信スキップ: order=%s item=%s reason=%s",
                plan.unique_key,
                plan.item_id,
                plan.skip_reason,
            )
            return "skipped"
        assert plan.zip_path is not None
        reason = is_safe_sales_zip(plan.zip_path, self.settings.root, self.settings.delivery_max_zip_bytes)
        if reason:
            self.logger.warning("ZIPを送れません: item=%s %s", plan.item_id, reason)
            if not dry_run:
                self.db.record_delivery(
                    plan.unique_key,
                    plan.order_item_id,
                    item_id=plan.item_id,
                    zip_path=str(plan.zip_path),
                    status="failed",
                    error_message=reason,
                )
            return "failed"
        zip_path = plan.zip_path.resolve()
        self.logger.info(
            "お届け準備: order=%s item=%s to=%s zip=%s dry_run=%s",
            plan.unique_key,
            plan.item_id,
            redact_email(plan.buyer_email),
            zip_path.name,
            dry_run,
        )
        if dry_run:
            return "skipped"
        try:
            bcc = self.settings.notify_email if self.settings.notify_email != plan.buyer_email else None
            self.mailer.send(
                f"【日本語化ファイル】{plan.title}",
                delivery_email_body(plan),
                to=plan.buyer_email,
                bcc=bcc,
                attachments=[zip_path],
                raise_on_error=True,
            )
            self.db.record_delivery(
                plan.unique_key,
                plan.order_item_id,
                item_id=plan.item_id,
                zip_path=str(zip_path),
                status="sent",
            )
            return "sent"
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("お届け失敗: order=%s %s", plan.unique_key, type(exc).__name__)
            self.db.record_delivery(
                plan.unique_key,
                plan.order_item_id,
                item_id=plan.item_id,
                zip_path=str(zip_path),
                status="failed",
                error_message=type(exc).__name__,
            )
            return "failed"

    def watch(self, *, dry_run: bool = False) -> int:
        interval = max(60, self.settings.delivery_poll_seconds)
        self.logger.info("注文監視を開始します。間隔=%s秒 終了は Ctrl+C", interval)
        while True:
            try:
                counts = self.run_once(dry_run=dry_run)
                self.logger.info(
                    "巡回結果: orders=%s sent=%s skipped=%s failed=%s",
                    counts["orders"],
                    counts["sent"],
                    counts["skipped"],
                    counts["failed"],
                )
            except PipelineError as exc:
                self.logger.error("巡回エラー (%s): %s", exc.stage, exc.message)
            time.sleep(interval)

    def send_test(self, zip_path: Path) -> None:
        reason = is_safe_sales_zip(zip_path, self.settings.root, self.settings.delivery_max_zip_bytes)
        if reason:
            raise PipelineError("お届けテスト", reason)
        dest = self.settings.notify_email
        if not dest:
            raise PipelineError("お届けテスト", "NOTIFY_EMAIL が未設定です。")
        plan = DeliveryPlan(
            unique_key="TEST",
            order_item_id="0",
            item_id="0",
            title=zip_path.stem,
            buyer_email=dest,
            buyer_name="テスト宛",
            zip_path=zip_path,
        )
        self.mailer.send(
            f"【テスト】日本語化ファイルのお届け {zip_path.name}",
            delivery_email_body(plan) + "\nこれはテスト送信です。購入者には送っていません。\n",
            to=dest,
            attachments=[zip_path],
            raise_on_error=True,
        )
        self.logger.info("テストお届けメールを送りました: %s", dest)


def pick_test_zip(output_dir: Path) -> Path | None:
    zips = sorted(output_dir.glob("*-ja.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    return zips[0] if zips else None


def ensure_test_zip(output_dir: Path) -> Path:
    """Use an existing sales ZIP, or write a tiny dummy *-ja.zip for SMTP tests."""
    existing = pick_test_zip(output_dir)
    if existing is not None:
        return existing
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / "delivery-test-ja.zip"
    readme = (
        "This is a mail-delivery test file for base-wp-ja-auto.\n"
        "It is not a WordPress translation package.\n"
    )
    padding = ("x" * 2048).encode("ascii")
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("README.txt", readme)
        zf.writestr("padding.txt", padding)
    return dest
