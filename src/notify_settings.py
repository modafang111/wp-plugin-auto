"""Shared notification SMTP settings for local software.

SMTP credentials live in a user-level dotenv file, not in each project's .env.
This module is duplicated at D:\\dev\\notify_mail_env.py so other apps can:

    import sys
    sys.path.insert(0, r"D:\\dev")
    from notify_mail_env import apply_shared_notify_env
    apply_shared_notify_env()

Search order:
1) NOTIFY_MAIL_ENV (explicit path; if set, other files are ignored)
2) %USERPROFILE%\\.config\\notify-mail.env
3) D:\\dev\\.env.notify
4) D:\\dev\\cloud-agent-sync\\notify.local.json
"""

from __future__ import annotations

import json
import os
from pathlib import Path

NOTIFY_SMTP_KEYS = (
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "SMTP_USE_TLS",
)
NOTIFY_FILL_KEYS = (
    "NOTIFY_EMAIL",
    "MAIL_FROM",
)


def shared_notify_env_candidates() -> list[Path]:
    custom = os.environ.get("NOTIFY_MAIL_ENV", "").strip()
    if custom:
        path = Path(custom).expanduser()
        return [path if path.is_absolute() else path.resolve()]
    paths = [
        Path.home() / ".config" / "notify-mail.env",
        Path("D:/dev/.env.notify"),
        Path("D:/dev/cloud-agent-sync/notify.local.json"),
    ]
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        resolved = path if path.is_absolute() else path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


def find_shared_notify_env() -> Path | None:
    for path in shared_notify_env_candidates():
        if path.is_file():
            return path
    return None


_JSON_KEY_MAP = {
    "smtp_host": "SMTP_HOST",
    "smtp_port": "SMTP_PORT",
    "smtp_user": "SMTP_USER",
    "smtp_password": "SMTP_PASSWORD",
    "smtp_use_tls": "SMTP_USE_TLS",
    "notify_email": "NOTIFY_EMAIL",
    "mail_from": "MAIL_FROM",
}


def _read_settings_file(path: Path) -> dict[str, str]:
    if path.suffix.lower() == ".json":
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        values: dict[str, str] = {}
        for src, dest in _JSON_KEY_MAP.items():
            item = raw.get(src)
            if item is None or not str(item).strip():
                continue
            values[dest] = str(item).strip()
        return values
    return _read_dotenv(path)


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    text = path.read_text(encoding="utf-8")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.lower().startswith("export "):
            key = key[7:].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def apply_shared_notify_env() -> Path | None:
    """Apply shared SMTP settings. SMTP_* always win; dest keys fill if empty."""
    path = find_shared_notify_env()
    if path is None:
        return None
    values = _read_settings_file(path)
    for key in NOTIFY_SMTP_KEYS:
        raw = values.get(key)
        if raw is None or not str(raw).strip():
            continue
        os.environ[key] = str(raw).strip()
    for key in NOTIFY_FILL_KEYS:
        if os.environ.get(key, "").strip():
            continue
        raw = values.get(key)
        if raw is None or not str(raw).strip():
            continue
        os.environ[key] = str(raw).strip()
    return path
