"""Shared helpers: safe HTTP, ZIP extraction, URL parsing, redaction."""

from __future__ import annotations

import hashlib
import html
import json
import re
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from src.exceptions import PipelineError


USER_AGENT = "base-wp-ja-auto/1.0 (WordPress.org Plugin API client)"

ALLOWED_HOSTS = {
    "api.wordpress.org",
    "downloads.wordpress.org",
    "ps.w.org",
    "s.w.org",
    "translate.wordpress.org",
    "api.thebase.in",
    "api.openai.com",
    "thebase.com",
    "admin.thebase.com",
    "developers.thebase.com",
    "thebase.in",
    "admin.thebase.in",
    "developers.thebase.in",
}

ALLOWED_HOST_SUFFIXES = (
    ".wordpress.org",
    ".w.org",
    ".thebase.com",
    ".thebase.in",
    ".base.shop",
    ".theshop.jp",
    ".base.ec",
    ".shopselect.net",
    ".buyshop.jp",
    ".akamaized.net",
)

PLUGIN_URL_RE = re.compile(
    r"^https?://(?:www\.)?wordpress\.org/plugins/([a-z0-9_-]+)/?",
    re.IGNORECASE,
)
SLUG_RE = re.compile(r"^[a-z0-9_-]+$")
PLACEHOLDER_RE = re.compile(
    r"%([0-9]+\$)?[-+#0 ]*(?:\d+|\*)?(?:\.(?:\d+|\*))?[sdifFouxXeEgGc%]"
    r"|\{[0-9]+\}"
    r"|\{[a-zA-Z_][a-zA-Z0-9_]*\}"
    r"|%\([a-zA-Z_][a-zA-Z0-9_]*\)s"
)
# msgid の「100%s」「100%1$s」など。WordPress は 100% を gettext に書けないため
# sprintf でパーセント記号を埋め込む。
_NUM_PERCENT_PLACEHOLDER_RE = re.compile(
    r"(?P<num>[0-9０-９]+)(?P<ph>%(?:[0-9]+\$)?s)"
)
# 訳文側で同じ数字の直後に残った「ただの %」。sprintf トークンではないもの。
_BROKEN_PERCENT_AFTER_NUM_RE = r"(?:％|%%|パーセント|%(?![sdifFouxXeEgGc%]|[0-9]+\$))"
HTML_TAG_RE = re.compile(r"</?([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>")
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def today_compact() -> str:
    return time.strftime("%Y%m%d")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def redact_email(value: str) -> str:
    text = (value or "").strip()
    if not text or "@" not in text:
        return ""
    local, _, domain = text.partition("@")
    if not local or not domain:
        return "[redacted]"
    return f"{local[0]}***@{domain}"


def strip_html(value: str) -> str:
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def host_allowed(url: str, extra_hosts: set[str] | None = None) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    allowed = set(ALLOWED_HOSTS)
    if extra_hosts:
        allowed.update(extra_hosts)
    if host in allowed:
        return True
    return any(host.endswith(suffix) for suffix in ALLOWED_HOST_SUFFIXES)


def extract_plugin_slug(url_or_slug: str) -> str:
    raw = (url_or_slug or "").strip()
    if not raw:
        raise PipelineError("URL解析", "プラグインURLまたはslugが空です。")
    match = PLUGIN_URL_RE.match(raw)
    if match:
        slug = match.group(1).lower()
    else:
        parsed = urlparse(raw)
        if parsed.scheme and parsed.netloc:
            raise PipelineError(
                "URL解析",
                "対象は wordpress.org/plugins/ で公開されている無料プラグインのみです。"
                f" 入力={raw}",
            )
        slug = raw.strip("/").lower()
    if slug in {"plugins", "browse", "search", "tags"}:
        raise PipelineError("URL解析", f"プラグインスラッグとして無効です: {slug}")
    if not SLUG_RE.match(slug):
        raise PipelineError("URL解析", f"プラグインスラッグとして無効です: {slug}")
    return slug


def official_plugin_url(slug: str) -> str:
    return f"https://wordpress.org/plugins/{slug}/"


def safe_filename(name: str, fallback: str = "file") -> str:
    cleaned = re.sub(r'[<>:"/\\\\|?*]', "_", name).strip(" .")
    return cleaned or fallback


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


class SafeHttp:
    def __init__(self, timeout: int = 60, extra_hosts: set[str] | None = None) -> None:
        self.timeout = timeout
        self.extra_hosts = extra_hosts or set()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def request(
        self,
        method: str,
        url: str,
        *,
        allow_hosts: set[str] | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        extra = set(self.extra_hosts)
        if allow_hosts:
            extra.update(allow_hosts)
        if not host_allowed(url, extra):
            raise PipelineError("HTTP", f"許可されていないホストへのアクセスを拒否しました: {urlparse(url).hostname}")
        timeout = kwargs.pop("timeout", self.timeout)
        allow_redirects = kwargs.pop("allow_redirects", False)
        kwargs.setdefault("headers", {})
        response = self.session.request(
            method,
            url,
            timeout=timeout,
            allow_redirects=False,
            **kwargs,
        )
        hops = 0
        while response.is_redirect and hops < 5:
            location = response.headers.get("Location")
            if not location:
                break
            next_url = urljoin(url, location)
            if not host_allowed(next_url, extra):
                raise PipelineError("HTTP", f"リダイレクト先が許可ホストではありません: {urlparse(next_url).hostname}")
            url = next_url
            hops += 1
            if not allow_redirects and hops > 0:
                response = self.session.request(method if method == "GET" else "GET", url, timeout=timeout, allow_redirects=False)
                continue
            response = self.session.request("GET", url, timeout=timeout, allow_redirects=False)
        return response

    def get_json(self, url: str, **kwargs: Any) -> Any:
        response = self.request("GET", url, allow_redirects=True, **kwargs)
        response.raise_for_status()
        return response.json()

    def download(self, url: str, dest: Path, max_bytes: int) -> int:
        dest.parent.mkdir(parents=True, exist_ok=True)
        response = self.request("GET", url, allow_redirects=True, stream=True)
        response.raise_for_status()
        total = 0
        with dest.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    handle.close()
                    dest.unlink(missing_ok=True)
                    raise PipelineError("ZIPダウンロード", f"ファイルサイズが上限 {max_bytes} bytes を超えています。")
                handle.write(chunk)
        return total


def safe_extract_zip(
    zip_path: Path,
    dest_dir: Path,
    *,
    max_files: int,
    max_uncompressed: int,
) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest_dir.resolve()
    total_bytes = 0
    file_count = 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        infos = zf.infolist()
        if len(infos) > max_files:
            raise PipelineError("ZIP展開", f"ZIP内のファイル数が上限 {max_files} を超えています。")
        for info in infos:
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or name.startswith("\\") or ":" in name.split("/")[0]:
                raise PipelineError("ZIP展開", f"危険なZIPエントリを検出しました: {info.filename}")
            parts = Path(name).parts
            if ".." in parts:
                raise PipelineError("ZIP展開", f"ZIP Slipの可能性があるエントリです: {info.filename}")
            target = (dest_dir / name).resolve()
            if not str(target).startswith(str(dest_resolved)):
                raise PipelineError("ZIP展開", f"展開先が作業ディレクトリ外です: {info.filename}")
            if info.is_dir() or name.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            total_bytes += info.file_size
            if total_bytes > max_uncompressed:
                raise PipelineError("ZIP展開", f"展開後サイズが上限 {max_uncompressed} bytes を超えています。")
            file_count += 1
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, target.open("wb") as out:
                remaining = info.file_size
                while remaining > 0:
                    chunk = src.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    out.write(chunk)
                    remaining -= len(chunk)
    return dest_dir


def placeholder_tokens(text: str) -> list[str]:
    return [m.group(0) for m in PLACEHOLDER_RE.finditer(text or "")]


def repair_placeholders(source: str, dest: str) -> str:
    """Restore sprintf placeholders the translator dropped or 'corrected'.

    WordPress strings often use ``100%s`` (runtime-injected ``%``) instead of
    a literal ``100%``. Models rewrite that to ``100%`` / ``100％`` / ``100%%``
    / ``100パーセント``. This restores the original token with regex only when
    the msgid actually contains ``<digits>%s`` or ``<digits>%1$s``.
    """
    if not dest:
        return dest
    if sorted(placeholder_tokens(source)) == sorted(placeholder_tokens(dest)):
        return dest
    repaired = dest
    for match in _NUM_PERCENT_PLACEHOLDER_RE.finditer(source or ""):
        num = match.group("num")
        ph = match.group("ph")
        if re.search(rf"{re.escape(num)}{re.escape(ph)}", repaired):
            continue
        repaired, _n = re.subn(
            rf"{re.escape(num)}\s*{_BROKEN_PERCENT_AFTER_NUM_RE}",
            f"{num}{ph}",
            repaired,
            count=1,
        )
    return repaired


def html_tag_names(text: str) -> list[str]:
    return [m.group(0).lower() for m in HTML_TAG_RE.finditer(text or "")]


def looks_like_url(text: str) -> bool:
    value = (text or "").strip()
    return bool(URL_RE.fullmatch(value) or re.fullmatch(r"https?://\S+", value, re.I))


def looks_like_code(text: str) -> bool:
    value = text or ""
    if re.search(r"\$[a-zA-Z_]|->|::|function\s*\(|\{\{.*\}\}", value):
        return True
    if value.count("{") >= 2 and value.count("%") == 0 and len(value) < 80:
        return True
    return False
