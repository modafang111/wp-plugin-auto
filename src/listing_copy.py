"""Build per-plugin listing copy from official data and translated UI strings.

No hand-written product pages. No AI sales rewrite. The skeleton stays the same;
the first lines change because each plugin has a different job and different UI text.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable

from src.utils import strip_html


# WordPress.org tags / slug tokens → what the plugin is for (Japanese).
JOB_BY_TOKEN = {
    "seo": "検索対策",
    "search-engine-optimization": "検索対策",
    "surerank": "検索対策",
    "google-analytics": "アクセス解析",
    "ga-google-analytics": "アクセス解析",
    "template-kit-import": "テンプレート取り込み",
    "so-widgets-bundle": "ウィジェット",
    "custom-fonts": "フォント",
    "sitemap": "サイトマップ",
    "schema": "構造化データ",
    "redirect": "リダイレクト",
    "redirection": "リダイレクト",
    "permalink": "パーマリンク",
    "analytics": "アクセス解析",
    "statistics": "アクセス解析",
    "security": "セキュリティ",
    "firewall": "セキュリティ",
    "malware": "マルウェア対策",
    "spam": "スパム対策",
    "antispam": "スパム対策",
    "captcha": "スパム防止",
    "akismet": "スパム対策",
    "login": "ログイン",
    "authentication": "認証",
    "two-factor": "二段階認証",
    "2fa": "二段階認証",
    "password": "パスワード管理",
    "membership": "会員サイト",
    "members": "会員サイト",
    "users": "ユーザー管理",
    "roles": "権限管理",
    "woocommerce": "ネットショップ",
    "ecommerce": "ネットショップ",
    "shop": "ネットショップ",
    "cart": "カート",
    "checkout": "決済",
    "payment": "決済",
    "stripe": "決済",
    "paypal": "決済",
    "forms": "フォーム",
    "form": "フォーム",
    "contact": "お問い合わせフォーム",
    "survey": "アンケート",
    "images": "画像",
    "image": "画像",
    "gallery": "ギャラリー",
    "slider": "スライダー",
    "lightbox": "画像表示",
    "lazy-load": "画像の遅延読み込み",
    "optimization": "表示速度の改善",
    "performance": "表示速度の改善",
    "cache": "キャッシュ",
    "caching": "キャッシュ",
    "minify": "ファイル圧縮",
    "cdn": "表示速度の改善",
    "compress": "圧縮",
    "compression": "圧縮",
    "imagify": "画像の圧縮",
    "smush": "画像の圧縮",
    "webp": "画像の圧縮",
    "backup": "バックアップ",
    "migrate": "サイト移行",
    "migration": "サイト移行",
    "clone": "複製",
    "duplicate": "投稿の複製",
    "import": "データの取り込み",
    "export": "データの書き出し",
    "csv": "CSV入出力",
    "smtp": "メール送信",
    "email": "メール",
    "mail": "メール",
    "newsletter": "メルマガ",
    "smtp-mailer": "メール送信",
    "multilingual": "多言語化",
    "translation": "翻訳",
    "language": "言語切替",
    "rtl": "多言語表示",
    "blocks": "ブロック編集",
    "gutenberg": "ブロック編集",
    "editor": "編集画面",
    "classic-editor": "クラシックエディタ",
    "widgets": "ウィジェット",
    "widget": "ウィジェット",
    "menu": "メニュー",
    "navigation": "ナビゲーション",
    "header": "ヘッダー",
    "footer": "フッター",
    "breadcrumb": "パンくず",
    "breadcrumbs": "パンくず",
    "custom-post-type": "カスタム投稿",
    "custom-fields": "カスタムフィールド",
    "acf": "カスタムフィールド",
    "shortcode": "ショートコード",
    "code": "コード挿入",
    "css": "CSSカスタム",
    "javascript": "スクリプト挿入",
    "snippet": "コード挿入",
    "snippets": "コード挿入",
    "font": "フォント",
    "fonts": "フォント",
    "typography": "フォント",
    "comments": "コメント",
    "reviews": "レビュー",
    "rating": "評価",
    "social": "SNS連携",
    "share": "シェアボタン",
    "facebook": "SNS連携",
    "twitter": "SNS連携",
    "cookie": "Cookie対応",
    "gdpr": "プライバシー対応",
    "privacy": "プライバシー対応",
    "consent": "同意バナー",
    "accessibility": "アクセシビリティ",
    "amp": "AMP対応",
    "pwa": "PWA",
    "cron": "定期処理",
    "database": "データベース",
    "file": "ファイル管理",
    "files": "ファイル管理",
    "media": "メディア",
    "video": "動画",
    "audio": "音声",
    "pdf": "PDF",
    "table": "表",
    "chart": "グラフ",
    "map": "地図",
    "calendar": "カレンダー",
    "booking": "予約",
    "event": "イベント",
    "faq": "FAQ",
    "related": "関連記事",
    "popular": "人気記事",
    "ads": "広告",
    "affiliate": "アフィリエイト",
    "admin": "管理画面の整理",
    "dashboard": "ダッシュボード",
    "notification": "通知",
    "notifications": "通知",
    "webhook": "外部連携",
    "api": "外部連携",
    "xml-rpc": "外部連携",
    "rest-api": "外部連携",
    "sso": "シングルサインオン",
    "ldap": "認証連携",
    "oauth": "認証連携",
    "honeypot": "スパム対策",
    "give": "寄付",
    "donation": "寄付",
    "donations": "寄付",
    "groups": "グループ管理",
    "adminimize": "管理画面の整理",
}

GENERIC_TOKENS = {
    "wp",
    "wordpress",
    "plugin",
    "plugins",
    "add",
    "addon",
    "add-on",
    "pro",
    "lite",
    "free",
    "plus",
    "premium",
    "for",
    "and",
    "the",
    "to",
    "with",
    "by",
    "from",
    "page",
    "pages",
    "post",
    "posts",
    "blog",
    "site",
    "sites",
    "easy",
    "simple",
    "best",
    "new",
    "advanced",
    "custom",
    "manager",
    "management",
    "tool",
    "tools",
    "kit",
    "builder",
    "made",
    "maker",
}

GENERIC_UI = {
    "保存",
    "設定",
    "削除",
    "キャンセル",
    "はい",
    "いいえ",
    "閉じる",
    "戻る",
    "次へ",
    "前へ",
    "検索",
    "更新",
    "編集",
    "追加",
    "有効化",
    "無効化",
    "一般",
    "オプション",
    "ヘルプ",
    "エラー",
    "成功",
    "名前",
    "タイトル",
    "説明",
    "適用",
    "リセット",
    "すべて",
    "なし",
    "選択",
    "選択済み",
    "必須",
    "任意",
    "公開",
    "下書き",
    "プレビュー",
    "インポート",
    "エクスポート",
    "アップロード",
    "ダウンロード",
}

HAS_JA = re.compile(r"[ぁ-んァ-ン一-龥]")


def looks_japanese(text: str) -> bool:
    return bool(HAS_JA.search(text or ""))


def plugin_job(slug: str = "", tags: Iterable[str] | None = None, name: str = "") -> str:
    found: list[str] = []
    seen: set[str] = set()

    def add(token: str) -> None:
        job = JOB_BY_TOKEN.get(token)
        if not job or job in seen:
            return
        if any(job in existing or existing in job for existing in seen):
            return
        seen.add(job)
        found.append(job)

    slug_key = (slug or "").strip().lower()
    if slug_key:
        add(slug_key)
    for token in _tokens(slug or ""):
        add(token)
        if len(found) >= 2:
            return "・".join(found)
    for token in _tokens(name or ""):
        add(token)
        if len(found) >= 2:
            return "・".join(found)
    for tag in tags or []:
        for token in _tokens(str(tag)):
            add(token)
            if len(found) >= 2:
                return "・".join(found)
    return "・".join(found)


def unique_lead(*, plugin_name: str, slug: str = "", job: str = "", short_description: str = "") -> str:
    name = (plugin_name or slug or "このプラグイン").strip()
    source = _one_line(short_description)
    short = _first_sentence(source, max_len=40)
    short_ja = short if looks_japanese(short) else ""
    if short_ja and (len(source) > 50 or short.endswith("…") or short_ja.startswith(name)):
        short_ja = ""
    job = (job or "").strip()
    if short_ja and job and job[:2] in short_ja:
        what = short_ja.rstrip("。")
        cores = (
            f"{name} は{what}。本商品はその管理画面を日本語で使うための .po / .mo です。",
            f"{name} の表示文言を日本語にします。{what}。",
            f"「{name}」向けの日本語化ファイルです。{what}。プラグイン本体は含みません。",
        )
    elif short_ja:
        what = short_ja.rstrip("。")
        cores = (
            f"{name} は{what}。本商品はその管理画面を日本語で使うための .po / .mo です。",
            f"{name} の設定画面・表示文言を日本語にします。{what}。",
            f"「{name}」向けの日本語化ファイルです。{what}。本体は含みません。",
        )
    elif job:
        cores = (
            f"{name} は{job}のためのプラグインです。本商品はその管理画面を日本語で使うための .po / .mo です。",
            f"{job}に使う「{name}」の表示文言を日本語にします。プラグイン本体は含みません。",
            f"{name}（{job}）の日本語化ファイルです。公式ディレクトリの本体と組み合わせて使います。",
        )
    else:
        cores = (
            f"{name} の管理画面・表示文言を日本語にするための .po / .mo です。プラグイン本体は含みません。",
            f"「{name}」向けの日本語化ファイルです。公式ディレクトリから本体を入れて使います。",
            f"{name} を日本語サイトで使うための翻訳ファイルです。本体は含まれません。",
        )
    return cores[_stable_index(slug or name, len(cores))]


def pick_sample_ui_lines(texts: Iterable[str] | None, *, limit: int = 5) -> list[str]:
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for raw in texts or []:
        text = _one_line(strip_html(str(raw or "")))
        if not looks_japanese(text):
            continue
        bare = text.rstrip("。．. ")
        if bare in GENERIC_UI or text in GENERIC_UI:
            continue
        if len(text) < 4 or len(text) > 42:
            continue
        if re.search(r"%[sd]|%\(\w+\)|%\d|\{[a-zA-Z]|https?://", text):
            continue
        key = bare.lower()
        if key in seen:
            continue
        seen.add(key)
        score = min(len(text), 28) + (3 if " " not in text else 0)
        scored.append((score, text))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [text for _, text in scored[:limit]]


def install_block(
    *,
    plugin_name: str,
    slug: str,
    version: str = "",
    po_name: str = "",
    mo_name: str = "",
) -> str:
    name = (plugin_name or slug or "このプラグイン").strip()
    slug = slug or "plugin-slug"
    po_name = po_name or f"{slug}-ja.po"
    mo_name = mo_name or f"{slug}-ja.mo"
    ver = f" バージョン {version}" if version and not str(version).startswith("(") else ""
    return (
        f"1. 公式ディレクトリから「{name}」{ver}をインストールします。\n"
        f"2. 「{name}」用の {po_name} と {mo_name} を、次のいずれかに置きます。\n"
        f"   wp-content/plugins/{slug}/languages/\n"
        f"   または\n"
        f"   wp-content/languages/plugins/\n"
        f"3. サイト言語を日本語にすると、「{name}」の管理画面が日本語になります。"
    )


def notes_block(*, plugin_name: str, version: str = "") -> str:
    name = (plugin_name or "このプラグイン").strip()
    target = f"「{name}」 {version}" if version and not str(version).startswith("(") else f"「{name}」"
    return (
        f"・本商品は{target}の日本語化ファイルです。プラグイン本体は含みません。\n"
        f"・「{name}」の著作権は原作者に帰属します。\n"
        f"・{target} 向けです。本体の更新で、一部の文字列が未翻訳になることがあります。\n"
        f"・WordPress.org および「{name}」の作者とは無関係の、第三者による翻訳ファイルです。"
    )


def official_description(text: str, *, translator: Any | None = None, plugin_name: str = "") -> str:
    cleaned = _clean_official_source(text)
    if not cleaned:
        return ""
    if looks_japanese(cleaned):
        return _trim_blurb(cleaned)
    if translator is None:
        return cleaned[:180]
    translated = translator.translate_official_blurb(cleaned, plugin_name=plugin_name)
    if looks_japanese(translated):
        return _trim_blurb(translated)
    return translated or cleaned[:180]


def listing_values(
    *,
    plugin_name: str,
    slug: str = "",
    version: str = "",
    official_url: str = "",
    short_description: str = "",
    description: str = "",
    tags: Iterable[str] | None = None,
    requires: str = "",
    requires_php: str = "",
    created: str = "",
    po_name: str = "",
    mo_name: str = "",
    sample_ui_lines: Iterable[str] | None = None,
    translated_count: Any = None,
    untranslated_count: Any = None,
) -> dict[str, str]:
    short = official_description(short_description or (description or "")[:400], plugin_name=plugin_name)
    job = plugin_job(slug, tags, plugin_name)
    lead = unique_lead(plugin_name=plugin_name, slug=slug, job=job, short_description=short)
    samples = list(sample_ui_lines or [])
    if not samples:
        samples = []
    return {
        "plugin_name": plugin_name,
        "version": version or "(対象バージョンは商品ページを確認)",
        "official_url": official_url,
        "short_description": short or f"「{plugin_name}」の管理画面・表示文字列を日本語化します。",
        "slug": slug or "plugin-slug",
        "created": created,
        "po_name": po_name or (f"{slug}-ja.po" if slug else "plugin-ja.po"),
        "mo_name": mo_name or (f"{slug}-ja.mo" if slug else "plugin-ja.mo"),
        "lead": lead,
        "job": job,
        "facts": _facts(
            job=job,
            tags=tags,
            requires=requires,
            requires_php=requires_php,
            translated_count=translated_count,
            untranslated_count=untranslated_count,
        ),
        "sample_ui": _sample_block(samples),
        "install": install_block(
            plugin_name=plugin_name,
            slug=slug or "plugin-slug",
            version=version,
            po_name=po_name or (f"{slug}-ja.po" if slug else "plugin-ja.po"),
            mo_name=mo_name or (f"{slug}-ja.mo" if slug else "plugin-ja.mo"),
        ),
        "notes": notes_block(plugin_name=plugin_name, version=version),
    }


def listing_values_from_info(info: Any, package: dict | None = None, quality: dict | None = None) -> dict[str, str]:
    package = package or {}
    quality = quality or {}
    short = (
        package.get("official_blurb_ja")
        or getattr(info, "short_description", "")
        or (getattr(info, "description", "") or "")[:400]
    )
    return listing_values(
        plugin_name=getattr(info, "name", "") or "",
        slug=getattr(info, "slug", "") or "",
        version=getattr(info, "version", "") or "",
        official_url=getattr(info, "official_url", "") or "",
        short_description=short,
        description=getattr(info, "description", "") or "",
        tags=getattr(info, "tags", None),
        requires=getattr(info, "requires", "") or "",
        requires_php=getattr(info, "requires_php", "") or "",
        created=str(package.get("created") or ""),
        po_name=str(package.get("po_name") or ""),
        mo_name=str(package.get("mo_name") or ""),
        sample_ui_lines=package.get("sample_ui_lines") or [],
        translated_count=quality.get("translated_count", package.get("translated_count")),
        untranslated_count=quality.get("untranslated_count", package.get("untranslated_count")),
    )


def _tokens(text: str) -> list[str]:
    lowered = (text or "").strip().lower().replace("_", "-")
    parts = re.split(r"[^a-z0-9]+", lowered)
    tokens = [part for part in parts if part and part not in GENERIC_TOKENS]
    if lowered and re.fullmatch(r"[a-z0-9\-]+", lowered):
        tokens.insert(0, lowered)
    return tokens


def _one_line(text: str) -> str:
    cleaned = strip_html(text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _clean_official_source(text: str) -> str:
    cleaned = _one_line(text)
    cleaned = re.sub(r"[★☆⭐✨🚀♥❤]+", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _trim_blurb(text: str, *, max_len: int = 180) -> str:
    cleaned = _one_line(text)
    if not cleaned:
        return ""
    parts = re.split(r"(?<=[。．.!?！？])\s*", cleaned)
    kept: list[str] = []
    for part in parts:
        if not part:
            continue
        kept.append(part)
        if len("".join(kept)) >= 80 or len(kept) >= 3:
            break
    out = " ".join(kept).strip()
    if len(out) > max_len:
        out = out[: max_len - 1].rstrip(" 、,") + "…"
    return out


def _first_sentence(text: str, *, max_len: int = 70) -> str:
    cleaned = _one_line(text)
    if not cleaned:
        return ""
    match = re.split(r"(?<=[。．.!?！？])\s+", cleaned, maxsplit=1)
    sentence = match[0].strip() if match else cleaned
    if len(sentence) > max_len:
        sentence = sentence[: max_len - 1].rstrip(" 、,") + "…"
    return sentence


def _stable_index(key: str, size: int) -> int:
    if size <= 1:
        return 0
    digest = hashlib.sha256((key or "").encode("utf-8")).hexdigest()
    return int(digest, 16) % size


def _facts(
    *,
    job: str,
    tags: Iterable[str] | None,
    requires: str,
    requires_php: str,
    translated_count: Any,
    untranslated_count: Any,
) -> str:
    lines: list[str] = []
    if job:
        lines.append(f"用途：{job}")
    tag_text = _tag_text(tags)
    if tag_text:
        lines.append(f"タグ：{tag_text}")
    if requires:
        lines.append(f"対応WordPress：{requires} 以降")
    if requires_php:
        lines.append(f"対応PHP：{requires_php} 以降")
    count_line = _count_line(translated_count, untranslated_count)
    if count_line:
        lines.append(count_line)
    return "\n".join(lines)


def _tag_text(tags: Iterable[str] | None) -> str:
    mapped: list[str] = []
    seen: set[str] = set()
    for tag in list(tags or [])[:6]:
        tokens = _tokens(str(tag))
        label = ""
        for token in tokens:
            if token in JOB_BY_TOKEN:
                label = JOB_BY_TOKEN[token]
                break
        if not label:
            continue
        if label in seen:
            continue
        seen.add(label)
        mapped.append(label)
    return " / ".join(mapped)


def _count_line(translated_count: Any, untranslated_count: Any) -> str:
    try:
        done = int(translated_count)
    except (TypeError, ValueError):
        return ""
    if done <= 0:
        return ""
    try:
        left = int(untranslated_count)
    except (TypeError, ValueError):
        left = 0
    if left > 0:
        return f"翻訳した文字列：{done} 件（未訳 {left}）"
    return f"翻訳した文字列：{done} 件"


def _sample_block(lines: list[str]) -> str:
    cleaned = [f"・{_one_line(line)}" for line in lines if _one_line(line)]
    if not cleaned:
        return ""
    return "■日本語になる文言の例\n" + "\n".join(cleaned)
