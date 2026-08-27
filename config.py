"""Load settings from .env. Secrets never belong in source files."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: str | None, default: int) -> int:
    if value is None or str(value).strip() == "":
        return default
    return int(value)


def _as_str(value: str | None, default: str = "") -> str:
    if value is None:
        return default
    return value.strip()


@dataclass
class Settings:
    root: Path
    dry_run: bool
    base_publish_mode: str
    continue_if_already_translated: bool
    skip_if_ja_percent: int

    base_login_email: str
    base_login_password: str
    base_client_id: str
    base_client_secret: str
    base_redirect_uri: str
    base_access_token: str
    base_refresh_token: str
    base_template_product_url: str
    base_template_product_id: str
    base_template_plugin_name: str
    base_template_plugin_version: str
    base_upload_digital_file: bool
    base_upload_generated_image: bool
    base_image_mode: str

    product_name_pattern: str
    product_price: str
    product_stock: int
    product_tax_type: int
    base_category_id: str
    shop_public_base_url: str
    sale_package_mode: str

    openai_api_key: str
    openai_model: str
    translation_provider: str
    translation_batch_size: int

    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_use_tls: bool
    notify_email: str
    mail_from: str
    require_email: bool

    max_zip_bytes: int
    max_uncompressed_bytes: int
    max_zip_files: int
    http_timeout_seconds: int

    @property
    def input_dir(self) -> Path:
        return self.root / "input"

    @property
    def work_dir(self) -> Path:
        return self.root / "work"

    @property
    def output_dir(self) -> Path:
        return self.root / "output"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def screenshots_dir(self) -> Path:
        return self.root / "screenshots"

    @property
    def backup_dir(self) -> Path:
        return self.root / "backup"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jobs.sqlite3"

    @property
    def token_path(self) -> Path:
        return self.data_dir / "base_tokens.json"

    @property
    def template_cache_path(self) -> Path:
        return self.data_dir / "base_template.json"

    @property
    def playwright_state_path(self) -> Path:
        return self.data_dir / "playwright" / "base_state.json"

    @property
    def visible_flag(self) -> int:
        return 0 if self.base_publish_mode != "public" else 1

    def ensure_directories(self) -> None:
        for path in (
            self.input_dir,
            self.work_dir,
            self.output_dir,
            self.logs_dir,
            self.data_dir,
            self.screenshots_dir,
            self.backup_dir,
            self.data_dir / "playwright",
            self.data_dir / "templates",
        ):
            path.mkdir(parents=True, exist_ok=True)

    def secret_values(self) -> list[str]:
        values = [
            self.base_login_password,
            self.base_client_secret,
            self.base_access_token,
            self.base_refresh_token,
            self.openai_api_key,
            self.smtp_password,
        ]
        return [v for v in values if v]


def load_settings(env_file: Path | None = None, overrides: dict | None = None) -> Settings:
    env_path = env_file or (ROOT / ".env")
    if env_path.exists():
        load_dotenv(env_path, override=False)
    else:
        example = ROOT / ".env.example"
        if example.exists():
            load_dotenv(example, override=False)

    overrides = overrides or {}

    def env(name: str, default: str = "") -> str:
        if name in overrides and overrides[name] is not None:
            return str(overrides[name])
        return _as_str(os.getenv(name), default)

    settings = Settings(
        root=ROOT,
        dry_run=_as_bool(env("DRY_RUN"), True),
        base_publish_mode=env("BASE_PUBLISH_MODE", "draft").lower() or "draft",
        continue_if_already_translated=_as_bool(env("CONTINUE_IF_ALREADY_TRANSLATED"), False),
        skip_if_ja_percent=_as_int(env("SKIP_IF_JA_PERCENT"), 95),
        base_login_email=env("BASE_LOGIN_EMAIL"),
        base_login_password=env("BASE_LOGIN_PASSWORD"),
        base_client_id=env("BASE_CLIENT_ID"),
        base_client_secret=env("BASE_CLIENT_SECRET"),
        base_redirect_uri=env("BASE_REDIRECT_URI", "https://localhost/callback"),
        base_access_token=env("BASE_ACCESS_TOKEN"),
        base_refresh_token=env("BASE_REFRESH_TOKEN"),
        base_template_product_url=env("BASE_TEMPLATE_PRODUCT_URL"),
        base_template_product_id=env("BASE_TEMPLATE_PRODUCT_ID"),
        base_template_plugin_name=env("BASE_TEMPLATE_PLUGIN_NAME"),
        base_template_plugin_version=env("BASE_TEMPLATE_PLUGIN_VERSION"),
        base_upload_digital_file=_as_bool(env("BASE_UPLOAD_DIGITAL_FILE"), False),
        base_upload_generated_image=_as_bool(env("BASE_UPLOAD_GENERATED_IMAGE"), False),
        base_image_mode=env("BASE_IMAGE_MODE", "wordpress_icon").lower() or "wordpress_icon",
        product_name_pattern=env(
            "PRODUCT_NAME_PATTERN",
            "{plugin_name} WordPressプラグイン 日本語化ファイル",
        ),
        product_price=env("PRODUCT_PRICE"),
        product_stock=_as_int(env("PRODUCT_STOCK"), 9999),
        product_tax_type=_as_int(env("PRODUCT_TAX_TYPE"), 1),
        base_category_id=env("BASE_CATEGORY_ID"),
        shop_public_base_url=env("SHOP_PUBLIC_BASE_URL"),
        sale_package_mode=env("SALE_PACKAGE_MODE", "translation_only").lower() or "translation_only",
        openai_api_key=env("OPENAI_API_KEY"),
        openai_model=env("OPENAI_MODEL", "gpt-4o-mini"),
        translation_provider=env("TRANSLATION_PROVIDER", "openai").lower() or "openai",
        translation_batch_size=_as_int(env("TRANSLATION_BATCH_SIZE"), 25),
        smtp_host=env("SMTP_HOST"),
        smtp_port=_as_int(env("SMTP_PORT"), 587),
        smtp_user=env("SMTP_USER"),
        smtp_password=env("SMTP_PASSWORD"),
        smtp_use_tls=_as_bool(env("SMTP_USE_TLS"), True),
        notify_email=env("NOTIFY_EMAIL"),
        mail_from=env("MAIL_FROM"),
        require_email=_as_bool(env("REQUIRE_EMAIL"), False),
        max_zip_bytes=_as_int(env("MAX_ZIP_BYTES"), 50 * 1024 * 1024),
        max_uncompressed_bytes=_as_int(env("MAX_UNCOMPRESSED_BYTES"), 200 * 1024 * 1024),
        max_zip_files=_as_int(env("MAX_ZIP_FILES"), 8000),
        http_timeout_seconds=_as_int(env("HTTP_TIMEOUT_SECONDS"), 60),
    )
    if settings.base_publish_mode not in {"draft", "public"}:
        settings.base_publish_mode = "draft"
    if settings.sale_package_mode not in {"translation_only", "plugin_and_translation"}:
        settings.sale_package_mode = "translation_only"
    return settings
