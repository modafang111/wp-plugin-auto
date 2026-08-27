"""Build WordPress .po/.mo (and optional JS JSON) from extracted strings."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import polib

from src.exceptions import PipelineError
from src.plugin_analyzer import TranslatableString, extract_strings, strings_to_jsonable
from src.utils import html_tag_names, placeholder_tokens, write_json
from src.wordpress import PluginInfo


class QualityReport:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.source_count = 0
        self.translated_count = 0
        self.untranslated_count = 0
        self.empty_count = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "source_count": self.source_count,
            "translated_count": self.translated_count,
            "untranslated_count": self.untranslated_count,
            "empty_count": self.empty_count,
            "errors": self.errors,
            "warnings": self.warnings,
        }


class TranslationBuilder:
    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger

    def collect_strings(self, plugin_root: Path, text_domain: str, pot_path: str) -> list[TranslatableString]:
        if pot_path and Path(pot_path).exists():
            self.logger.info("翻訳対象抽出: 同梱 .pot を優先します (%s)", pot_path)
            return self._from_pot(Path(pot_path))
        wp_pot = self._try_wp_cli(plugin_root, text_domain)
        if wp_pot:
            self.logger.info("翻訳対象抽出: WP-CLI i18n make-pot を使用しました")
            return wp_pot
        self.logger.info("翻訳対象抽出: PHP/JS を読み取り専用スキャンします")
        return extract_strings(plugin_root, text_domain)

    def _from_pot(self, path: Path) -> list[TranslatableString]:
        po = polib.pofile(str(path))
        items: list[TranslatableString] = []
        for entry in po:
            if entry.obsolete or not entry.msgid:
                continue
            items.append(
                TranslatableString(
                    msgid=entry.msgid,
                    msgid_plural=entry.msgid_plural or "",
                    msgctxt=entry.msgctxt or "",
                    references=list(entry.occurrences) if False else [f"{a}:{b}" for a, b in entry.occurrences],
                    extracted_comment=entry.comment or "",
                )
            )
        return items

    def _try_wp_cli(self, plugin_root: Path, text_domain: str) -> list[TranslatableString] | None:
        pot = plugin_root.parent.parent / "pot" / f"{text_domain}.pot"
        pot.parent.mkdir(parents=True, exist_ok=True)
        try:
            result = subprocess.run(
                ["wp", "i18n", "make-pot", str(plugin_root), str(pot), f"--domain={text_domain}", "--skip-audit"],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0 or not pot.exists():
            return None
        return self._from_pot(pot)

    def write_catalog(
        self,
        info: PluginInfo,
        text_domain: str,
        items: list[TranslatableString],
        translations: list[str],
        dest_dir: Path,
        plugin_root: Path,
    ) -> dict:
        if len(items) != len(translations):
            raise PipelineError("品質チェック", "原文数と翻訳数が一致しません。")
        dest_dir.mkdir(parents=True, exist_ok=True)
        po = polib.POFile()
        po.metadata = {
            "Project-Id-Version": f"{info.name} {info.version}",
            "Report-Msgid-Bugs-To": info.official_url,
            "POT-Creation-Date": datetime.now().strftime("%Y-%m-%d %H:%M%z"),
            "PO-Revision-Date": datetime.now().strftime("%Y-%m-%d %H:%M%z"),
            "Last-Translator": "base-wp-ja-auto",
            "Language-Team": "Japanese",
            "Language": "ja",
            "MIME-Version": "1.0",
            "Content-Type": "text/plain; charset=UTF-8",
            "Content-Transfer-Encoding": "8bit",
            "Plural-Forms": "nplurals=1; plural=0;",
            "X-Generator": "base-wp-ja-auto 1.0",
            "X-Domain": text_domain,
        }
        js_entries: list[tuple[TranslatableString, str]] = []
        for item, translated in zip(items, translations):
            entry = polib.POEntry(
                msgid=item.msgid,
                msgstr=translated or "",
                msgctxt=item.msgctxt or None,
                msgid_plural=item.msgid_plural or None,
                msgstr_plural=[translated or ""] if item.msgid_plural else None,
                occurrences=[tuple((ref.split(":") + [""])[:2]) for ref in item.references],
                comment=item.extracted_comment or None,
            )
            po.append(entry)
            if any(ref.lower().endswith(ext) for ref in item.references for ext in (".js", ".jsx", ".ts", ".tsx", ".json")):
                js_entries.append((item, translated))

        po_path = dest_dir / f"{text_domain}-ja.po"
        mo_path = dest_dir / f"{text_domain}-ja.mo"
        po.save(str(po_path))
        po.save_as_mofile(str(mo_path))
        self._verify_mo(po_path, mo_path)
        json_paths = self._write_js_json(text_domain, js_entries, dest_dir) if js_entries else []
        mapping_path = dest_dir / "translations.json"
        write_json(
            mapping_path,
            [
                {
                    "msgid": item.msgid,
                    "msgctxt": item.msgctxt,
                    "msgstr": translated,
                    "references": item.references,
                }
                for item, translated in zip(items, translations)
            ],
        )
        self.logger.info(".po生成: %s", po_path)
        self.logger.info(".mo生成: %s", mo_path)
        return {
            "po_path": str(po_path),
            "mo_path": str(mo_path),
            "json_paths": json_paths,
            "mapping_path": str(mapping_path),
            "count": len(items),
        }

    def _verify_mo(self, po_path: Path, mo_path: Path) -> None:
        if not mo_path.exists() or mo_path.stat().st_size == 0:
            raise PipelineError(".mo生成", "mo ファイルが空、または作成されていません。")
        po = polib.pofile(str(po_path))
        mo = polib.mofile(str(mo_path))
        po_ids = {e.msgid for e in po if e.msgid}
        mo_ids = {e.msgid for e in mo if e.msgid}
        if po_ids - mo_ids:
            raise PipelineError(".mo生成", "po から mo への変換で一部エントリが欠けています。")

    def _write_js_json(
        self,
        text_domain: str,
        entries: list[tuple[TranslatableString, str]],
        dest_dir: Path,
    ) -> list[str]:
        by_file: dict[str, list[tuple[TranslatableString, str]]] = defaultdict(list)
        for item, translated in entries:
            js_ref = next((r.split(":")[0] for r in item.references if r.lower().endswith((".js", ".jsx", ".ts", ".tsx", ".json"))), "script.js")
            by_file[js_ref].append((item, translated))
        paths: list[str] = []
        for source, rows in by_file.items():
            messages = {
                "": {
                    "domain": "messages",
                    "lang": "ja",
                    "plural-forms": "nplurals=1; plural=0;",
                }
            }
            for item, translated in rows:
                key = item.msgid if not item.msgctxt else f"{item.msgctxt}\u0004{item.msgid}"
                messages[key] = [translated]
            md5 = hashlib.md5(source.encode("utf-8")).hexdigest()
            name = f"{text_domain}-ja-{md5}.json"
            path = dest_dir / name
            payload = {
                "translation-revision-date": datetime.now().isoformat(),
                "generator": "base-wp-ja-auto",
                "source": source,
                "domain": "messages",
                "locale_data": {"messages": messages},
            }
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            paths.append(str(path))
        return paths

    def quality_check(self, items: list[TranslatableString], translations: list[str]) -> QualityReport:
        self.logger.info("品質チェック")
        report = QualityReport()
        report.source_count = len(items)
        if len(items) != len(translations):
            report.errors.append(f"原文数 {len(items)} と翻訳数 {len(translations)} が一致しません。")
            return report
        by_source: dict[str, set[str]] = defaultdict(set)
        for item, translated in zip(items, translations):
            text = translated if translated is not None else ""
            if not str(text).strip():
                report.empty_count += 1
                report.untranslated_count += 1
                report.errors.append(f"空翻訳: {item.msgid[:80]}")
                continue
            report.translated_count += 1
            if "\ufffd" in text or re_mojibake(text):
                report.errors.append(f"文字化けの可能性: {item.msgid[:60]}")
            src_ph = placeholder_tokens(item.msgid)
            dst_ph = placeholder_tokens(text)
            if sorted(src_ph) != sorted(dst_ph):
                report.errors.append(f"プレースホルダー不一致: {item.msgid[:60]}")
            src_tags = html_tag_names(item.msgid)
            dst_tags = html_tag_names(text)
            if sorted(src_tags) != sorted(dst_tags):
                report.errors.append(f"HTMLタグ不一致: {item.msgid[:60]}")
            if len(text) > max(len(item.msgid) * 4, len(item.msgid) + 120):
                report.warnings.append(f"異常に長い翻訳: {item.msgid[:60]}")
            by_source[item.msgid].add(text)
        for msgid, variants in by_source.items():
            if len(variants) > 1:
                report.warnings.append(f"同一原文で訳が異なる: {msgid[:60]}")
        if report.empty_count and report.empty_count == report.source_count:
            report.errors.append("すべての文字列が未翻訳です。")
        return report


def re_mojibake(text: str) -> bool:
    return bool(
        any(token in text for token in ("Ã", "Â", "ã€", "äººäºº", "ðŸ"))
        and not any(ord(ch) > 0x3000 for ch in text)
    )


def dump_strings(path: Path, items: list[TranslatableString]) -> None:
    write_json(path, strings_to_jsonable(items))
