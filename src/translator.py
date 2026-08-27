"""Pluggable translators. OpenAI is the default; glossary is for dry-run/offline."""

from __future__ import annotations

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from pathlib import Path

from config import Settings
from src.database import Database
from src.exceptions import PipelineError
from src.plugin_analyzer import TranslatableString
from src.utils import looks_like_code, looks_like_url, placeholder_tokens, sha256_text


GLOSSARY = {
    "Save": "保存",
    "Saved": "保存しました",
    "Settings": "設定",
    "Setting": "設定",
    "Delete": "削除",
    "Deleted": "削除しました",
    "Enable": "有効化",
    "Enabled": "有効",
    "Disable": "無効化",
    "Disabled": "無効",
    "Update": "更新",
    "Updated": "更新しました",
    "Edit": "編集",
    "Add": "追加",
    "Add New": "新規追加",
    "Cancel": "キャンセル",
    "Submit": "送信",
    "Search": "検索",
    "Upload": "アップロード",
    "Download": "ダウンロード",
    "Install": "インストール",
    "Installed": "インストール済み",
    "Activate": "有効化",
    "Activated": "有効化しました",
    "Deactivate": "無効化",
    "Plugin": "プラグイン",
    "Plugins": "プラグイン",
    "Widget": "ウィジェット",
    "Widgets": "ウィジェット",
    "Dashboard": "ダッシュボード",
    "Options": "オプション",
    "General": "一般",
    "Advanced": "高度な設定",
    "Yes": "はい",
    "No": "いいえ",
    "OK": "OK",
    "Close": "閉じる",
    "Back": "戻る",
    "Next": "次へ",
    "Previous": "前へ",
    "Required": "必須",
    "Optional": "任意",
    "Name": "名前",
    "Title": "タイトル",
    "Description": "説明",
    "Email": "メール",
    "Password": "パスワード",
    "Username": "ユーザー名",
    "User": "ユーザー",
    "Users": "ユーザー",
    "Role": "権限グループ",
    "Status": "ステータス",
    "Published": "公開",
    "Draft": "下書き",
    "Preview": "プレビュー",
    "Publish": "公開",
    "Apply": "適用",
    "Reset": "リセット",
    "Import": "インポート",
    "Export": "エクスポート",
    "Help": "ヘルプ",
    "Documentation": "ドキュメント",
    "Support": "サポート",
    "Error": "エラー",
    "Warning": "警告",
    "Success": "成功",
    "Loading...": "読み込み中...",
    "Please wait...": "お待ちください...",
    "Are you sure?": "よろしいですか?",
    "None": "なし",
    "All": "すべて",
    "Select": "選択",
    "Selected": "選択済み",
    "Remove": "削除",
    "Refresh": "再読み込み",
    "Copy": "コピー",
    "Paste": "貼り付け",
    "Filter": "絞り込み",
    "Sort": "並び替え",
    "Date": "日付",
    "Time": "時刻",
    "Language": "言語",
    "Translation": "翻訳",
    "WordPress": "WordPress",
}

SYSTEM_PROMPT = """あなたは WordPress 管理画面の日本語翻訳者です。
英語の原文を、日本の WordPress 利用者が違和感なく読める管理画面向け日本語に翻訳してください。

必須ルール:
- WordPress で一般的な用語を使う（Save=保存, Settings=設定, Delete=削除, Enable=有効化, Disable=無効化 など）。
- プレースホルダーを絶対に壊さない。%s %d %1$s %2$s {0} {name} %(name)s はそのまま残す。
- HTMLタグを壊さない。追加・削除しない。
- URL は翻訳しない。
- プラグイン名、商品名、会社名、作者名などの固有名詞は無理に日本語化しない。
- コード断片や意味が変わる識別子は変更しない。
- 直訳調を避け、短いラベルは管理画面らしい簡潔な表現にする。
- 出力は JSON オブジェクトのみ。キー translations の配列。各要素は {"id": <int>, "text": "<日本語>"}。
"""


def keep_original(item: TranslatableString) -> bool:
    text = item.msgid.strip()
    if not text:
        return True
    if looks_like_url(text) or looks_like_code(text):
        return True
    if re.fullmatch(r"[\d\s\W_]+", text):
        return True
    if len(text) <= 2 and not re.search(r"[A-Za-z]", text):
        return True
    return False


class Translator(ABC):
    name = "base"

    def __init__(self, settings: Settings, db: Database, logger: logging.Logger) -> None:
        self.settings = settings
        self.db = db
        self.logger = logger

    @abstractmethod
    def translate_batch(self, items: list[tuple[int, TranslatableString]]) -> dict[int, str]:
        raise NotImplementedError

    def translate_all(self, items: list[TranslatableString]) -> list[str]:
        results = [""] * len(items)
        pending: list[tuple[int, TranslatableString]] = []
        for index, item in enumerate(items):
            if keep_original(item):
                results[index] = item.msgid
                continue
            cached = self.db.cache_get(sha256_text(item.msgid), item.msgctxt, self.name)
            if cached is not None:
                results[index] = cached
                continue
            glossary = GLOSSARY.get(item.msgid) or GLOSSARY.get(item.msgid.strip())
            if glossary:
                results[index] = glossary
                self.db.cache_put(sha256_text(item.msgid), item.msgid, item.msgctxt, glossary, self.name)
                continue
            pending.append((index, item))

        batch_size = max(1, self.settings.translation_batch_size)
        self.logger.info("AI翻訳開始: 未キャッシュ %s / 全 %s", len(pending), len(items))
        for offset in range(0, len(pending), batch_size):
            chunk = pending[offset : offset + batch_size]
            translated = self.translate_batch(chunk)
            for index, item in chunk:
                text = translated.get(index, "")
                results[index] = text
                if text:
                    self.db.cache_put(sha256_text(item.msgid), item.msgid, item.msgctxt, text, self.name)
        self.logger.info("AI翻訳終了")
        return results


class OpenAITranslator(Translator):
    name = "openai"

    def translate_batch(self, items: list[tuple[int, TranslatableString]]) -> dict[int, str]:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise PipelineError("AI翻訳開始", "openai パッケージがインストールされていません。") from exc
        if not self.settings.openai_api_key:
            raise PipelineError("AI翻訳開始", "OPENAI_API_KEY が未設定です。")

        payload = []
        for index, item in items:
            payload.append(
                {
                    "id": index,
                    "text": item.msgid,
                    "plural": item.msgid_plural,
                    "context": item.msgctxt,
                    "comment": item.extracted_comment,
                }
            )
        user = (
            "次の WordPress プラグイン文字列を日本語へ翻訳してください。\n"
            + json.dumps(payload, ensure_ascii=False)
        )
        client = OpenAI(api_key=self.settings.openai_api_key)
        delay = 1.0
        last_error = None
        for attempt in range(5):
            try:
                response = client.chat.completions.create(
                    model=self.settings.openai_model,
                    temperature=0.2,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user},
                    ],
                )
                content = (response.choices[0].message.content or "").strip()
                data = _parse_json_object(content)
                rows = data.get("translations") if isinstance(data, dict) else None
                out: dict[int, str] = {}
                if isinstance(rows, list):
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        try:
                            out[int(row["id"])] = str(row.get("text") or "")
                        except (KeyError, TypeError, ValueError):
                            continue
                if len(out) != len(items):
                    # Map by order if ids missing.
                    if isinstance(rows, list) and len(rows) == len(items):
                        for (index, _item), row in zip(items, rows):
                            if isinstance(row, dict):
                                out[index] = str(row.get("text") or "")
                return out
            except Exception as exc:  # noqa: BLE001 - retry then fail
                last_error = exc
                self.logger.warning("OpenAI API 失敗 (%s回目): %s", attempt + 1, type(exc).__name__)
                time.sleep(delay)
                delay *= 2
        raise PipelineError("AI翻訳開始", f"OpenAI API が繰り返し失敗しました: {last_error}")


class OfflineGlossaryTranslator(Translator):
    """Deterministic fallback for DRY_RUN without API keys. Not for production quality."""

    name = "offline_glossary"

    def translate_batch(self, items: list[tuple[int, TranslatableString]]) -> dict[int, str]:
        out: dict[int, str] = {}
        for index, item in items:
            text = item.msgid
            translated = GLOSSARY.get(text)
            if translated:
                out[index] = translated
                continue
            # Keep placeholders and URLs; translate common UI words inside longer labels.
            out[index] = _soft_glossary_translate(text)
        return out


def _soft_glossary_translate(text: str) -> str:
    result = text
    for en, ja in sorted(GLOSSARY.items(), key=lambda kv: len(kv[0]), reverse=True):
        result = re.sub(rf"\b{re.escape(en)}\b", ja, result)
    return result


def _parse_json_object(content: str) -> dict:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I | re.S)
    try:
        data = json.loads(content)
        return data if isinstance(data, dict) else {"translations": data}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.S)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}


def get_translator(settings: Settings, db: Database, logger: logging.Logger) -> Translator:
    provider = (settings.translation_provider or "openai").lower()
    if provider == "offline_glossary":
        logger.info("翻訳プロバイダ: offline_glossary")
        return OfflineGlossaryTranslator(settings, db, logger)
    if provider == "openai":
        if settings.openai_api_key:
            logger.info("翻訳プロバイダ: openai model=%s", settings.openai_model)
            return OpenAITranslator(settings, db, logger)
        if settings.dry_run:
            logger.warning("OPENAI_API_KEY 未設定のため DRY_RUN では offline_glossary を使います。")
            return OfflineGlossaryTranslator(settings, db, logger)
        raise PipelineError("AI翻訳開始", "OPENAI_API_KEY が未設定です。")
    raise PipelineError("AI翻訳開始", f"未対応の TRANSLATION_PROVIDER です: {provider}")


def load_extra_glossary(path: Path) -> None:
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(key, str) and isinstance(value, str) and key and value:
                GLOSSARY[key] = value
