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

    def send(self, subject: str, body: str) -> None:
        self.logger.info("メール送信: %s", subject)
        if not self.settings.notify_email or not self.settings.smtp_host:
            message = "SMTP または NOTIFY_EMAIL が未設定のためメールは送信しません。"
            if self.settings.require_email:
                raise PipelineError("メール送信", message)
            self.logger.warning(message)
            return
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.settings.mail_from or self.settings.smtp_user or self.settings.notify_email
        msg["To"] = self.settings.notify_email
        msg.set_content(body)
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
            if self.settings.require_email:
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
                ("処理日時", info.get("processed_at")),
                ("DRY_RUN", info.get("dry_run")),
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
