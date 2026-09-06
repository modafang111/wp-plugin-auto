import os
import tempfile
import unittest
from pathlib import Path

from src.notify_settings import apply_shared_notify_env, find_shared_notify_env


class NotifySettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {
            key: os.environ.get(key)
            for key in (
                "NOTIFY_MAIL_ENV",
                "SMTP_HOST",
                "SMTP_PORT",
                "SMTP_USER",
                "SMTP_PASSWORD",
                "SMTP_USE_TLS",
                "NOTIFY_EMAIL",
                "MAIL_FROM",
            )
        }

    def tearDown(self) -> None:
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_shared_smtp_overrides_existing_and_fills_empty_dest(self) -> None:
        os.environ["SMTP_HOST"] = "smtp.old.example"
        os.environ["SMTP_PASSWORD"] = "old-password"
        os.environ["NOTIFY_EMAIL"] = "project@example.com"
        os.environ.pop("MAIL_FROM", None)
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "notify-mail.env"
            path.write_text(
                "\n".join(
                    [
                        "SMTP_HOST=smtp.gmail.com",
                        "SMTP_PORT=587",
                        "SMTP_USER=shared@example.com",
                        "SMTP_PASSWORD=abcd efgh ijkl mnop",
                        "SMTP_USE_TLS=true",
                        "NOTIFY_EMAIL=shared@example.com",
                        "MAIL_FROM=shared@example.com",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            os.environ["NOTIFY_MAIL_ENV"] = str(path)
            found = apply_shared_notify_env()
            self.assertEqual(found, path.resolve())
            self.assertEqual(os.environ["SMTP_HOST"], "smtp.gmail.com")
            self.assertEqual(os.environ["SMTP_PASSWORD"], "abcd efgh ijkl mnop")
            self.assertEqual(os.environ["NOTIFY_EMAIL"], "project@example.com")
            self.assertEqual(os.environ["MAIL_FROM"], "shared@example.com")

    def test_missing_shared_file_returns_none(self) -> None:
        os.environ["NOTIFY_MAIL_ENV"] = str(Path(tempfile.gettempdir()) / "missing-notify-mail.env")
        self.assertIsNone(find_shared_notify_env())
        self.assertIsNone(apply_shared_notify_env())

    def test_shared_json_maps_smtp_keys(self) -> None:
        os.environ.pop("SMTP_HOST", None)
        os.environ.pop("NOTIFY_EMAIL", None)
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "notify.local.json"
            path.write_text(
                '{"smtp_host":"smtp.gmail.com","smtp_password":"abcd efgh","notify_email":"a@b.com"}',
                encoding="utf-8",
            )
            os.environ["NOTIFY_MAIL_ENV"] = str(path)
            self.assertEqual(apply_shared_notify_env(), path.resolve())
            self.assertEqual(os.environ["SMTP_HOST"], "smtp.gmail.com")
            self.assertEqual(os.environ["SMTP_PASSWORD"], "abcd efgh")
            self.assertEqual(os.environ["NOTIFY_EMAIL"], "a@b.com")
