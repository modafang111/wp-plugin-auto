"""SMTP notifications. Secrets are never included in the body."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from pathlib import Path

from config import Settings
from src.exceptions import PipelineError


class Mailer:
    def __init__(self, settings: Settings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger

    def send(
        self,
        subject: str,
        body: str,
        *,
        to: str | None = None,
        bcc: str | None = None,
        attachments: list[Path] | None = None,
        raise_on_error: bool | None = None,
    ) -> None:
        self.logger.info("メール送信: %s", subject)
        dest = (to or self.settings.notify_email or "").strip()
        if not dest or not self.settings.smtp_host:
            message = "SMTP または宛先が未設定のためメールは送信しません。"
            if self.settings.require_email:
                raise PipelineError("メール送信", message)
            self.logger.warning(message)
            return
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.settings.mail_from or self.settings.smtp_user or self.settings.notify_email
        msg["To"] = dest
        if bcc:
            msg["Bcc"] = bcc
        msg.set_content(body)
        for path in attachments or []:
            data = path.read_bytes()
            msg.add_attachment(
                data,
                maintype="application",
                subtype="zip",
                filename=path.name,
            )
        try:
            if self.settings.smtp_port == 465:
                server: smtplib.SMTP = smtplib.SMTP_SSL(self.settings.smtp_host, self.settings.smtp_port, timeout=30)
            else:
                server = smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=30)
                if self.settings.smtp_use_tls:
                    server.starttls()
            with server:
                if self.settings.smtp_user:
                    server.login(self.settings.smtp_user, self.settings.smtp_password)
                server.send_message(msg)
        except Exception as exc:  # noqa: BLE001
            fail_hard = self.settings.require_email if raise_on_error is None else raise_on_error
            if fail_hard:
                raise PipelineError("メール送信", f"メール送信に失敗しました: {exc}") from exc
            self.logger.warning("メール送信に失敗しました: %s", type(exc).__name__)

    def success(self, info: dict) -> None:
        subject = f"【BASE商品登録完了】{info.get('plugin_name')} {info.get('plugin_version')}"
        body = self._lines(
            [
                ("プラグイン名", info.get("plugin_name")),
                ("バージョン", info.get("plugin_version")),
                ("WordPress公式URL", info.get("wordpress_url")),
                ("翻訳文字列数", info.get("translation_count")),
                ("未翻訳数", info.get("untranslated_count")),
                ("BASE商品名", info.get("base_title")),
                ("販売価格", info.get("price")),
                ("BASE商品URL", info.get("base_product_url") or "(DRY RUN のため未登録)"),
                ("管理画面", info.get("admin_url") or ""),
                ("登録方法", info.get("method") or ""),
                ("販売用ZIP保存場所", info.get("output_zip")),
                ("公開状態", info.get("publish_mode")),
                ("処理日時", info.get("processed_at")),
                ("DRY_RUN", info.get("dry_run")),
            ]
        )
        self.send(subject, body)

    def sale(self, info: dict) -> None:
        subject = f"【BASE売上・自動お届け】{info.get('title') or ''}"
        body = self._lines(
            [
                ("商品名", info.get("title")),
                ("注文キー", info.get("unique_key")),
                ("BASE商品ID", info.get("item_id")),
                ("送付ZIP", info.get("zip_name")),
                ("購入者", info.get("buyer")),
                ("対応", "日本語化ZIPを購入者へメールし、注文を対応済にします"),
            ]
        )
        self.send(subject, body)

    def sale_failed(self, info: dict) -> None:
        subject = f"【BASE売上・お届け失敗】{info.get('title') or ''}"
        body = self._lines(
            [
                ("商品名", info.get("title")),
                ("注文キー", info.get("unique_key")),
                ("BASE商品ID", info.get("item_id")),
                ("理由", info.get("reason")),
            ]
        )
        self.send(subject, body)

    def error(self, info: dict) -> None:
        subject = f"【BASE商品登録エラー】{info.get('plugin_name') or info.get('slug') or ''}"
        body = self._lines(
            [
                ("エラーが発生した工程", info.get("stage")),
                ("エラー内容", info.get("error")),
                ("ログファイルの場所", info.get("log_path")),
                ("スクリーンショットの場所", info.get("screenshot_path") or "(なし)"),
                ("再実行方法", info.get("retry") or "python app.py --resume \"<URL>\""),
            ]
        )
        self.send(subject, body)

    def needs_review(self, info: dict) -> None:
        subject = "【要確認】BASE商品登録処理"
        body = self._lines(
            [
                ("プラグイン名", info.get("plugin_name")),
                ("バージョン", info.get("plugin_version")),
                ("理由", info.get("reason")),
                ("ログファイルの場所", info.get("log_path")),
                ("スクリーンショットの場所", info.get("screenshot_path") or "(なし)"),
                ("販売用ZIP", info.get("output_zip")),
                ("再実行方法", info.get("retry") or "python app.py --resume \"<URL>\""),
            ]
        )
        self.send(subject, body)

    @staticmethod
    def _lines(rows: list[tuple[str, object]]) -> str:
        return "\n".join(f"{label}: {value if value is not None else ''}" for label, value in rows) + "\n"
