"""Analyze plugin headers, bundled translations, and extract i18n strings.

PHP/JS sources are read only. Nothing is rewritten or executed.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from src.exceptions import PipelineError
from src.wordpress import PluginInfo, WordPressClient


HEADER_KEYS = {
    "Plugin Name": "plugin_name",
    "Plugin URI": "plugin_uri",
    "Description": "description",
    "Author": "author",
    "Author URI": "author_uri",
    "Version": "version",
    "Text Domain": "text_domain",
    "Domain Path": "domain_path",
    "Requires at least": "requires",
    "Requires PHP": "requires_php",
    "License": "license",
    "License URI": "license_uri",
}

I18N_FUNCS = {
    "__": {"singular": 0, "domain": 1},
    "_e": {"singular": 0, "domain": 1},
    "esc_html__": {"singular": 0, "domain": 1},
    "esc_html_e": {"singular": 0, "domain": 1},
    "esc_attr__": {"singular": 0, "domain": 1},
    "esc_attr_e": {"singular": 0, "domain": 1},
    "_x": {"singular": 0, "context": 1, "domain": 2},
    "_ex": {"singular": 0, "context": 1, "domain": 2},
    "esc_html_x": {"singular": 0, "context": 1, "domain": 2},
    "esc_attr_x": {"singular": 0, "context": 1, "domain": 2},
    "_n": {"singular": 0, "plural": 1, "domain": 3},
    "_n_noop": {"singular": 0, "plural": 1, "domain": 2},
    "_nx": {"singular": 0, "plural": 1, "context": 3, "domain": 4},
    "_nx_noop": {"singular": 0, "plural": 1, "context": 2, "domain": 3},
    "translate": {"singular": 0, "domain": 1},
}

JS_FUNCS = {"__", "_x", "_n", "_nx"}


@dataclass
class TranslatableString:
    msgid: str
    msgid_plural: str = ""
    msgctxt: str = ""
    references: list[str] = field(default_factory=list)
    extracted_comment: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.msgctxt, self.msgid, self.msgid_plural)


@dataclass
class Analysis:
    plugin_root: Path
    headers: dict[str, str]
    text_domain: str
    domain_path: str
    languages_dir: Path | None
    bundled_files: dict[str, list[str]]
    has_pot: bool
    pot_path: str
    ja_files: list[str]
    official_ja_percent: int | None
    has_official_ja_pack: bool
    already_translated: bool
    reason: str
    license: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["plugin_root"] = str(self.plugin_root)
        data["languages_dir"] = str(self.languages_dir) if self.languages_dir else None
        return data


def decide_already_translated(
    *,
    official_ja_percent: int | None,
    has_official_ja_pack: bool,
    skip_if_ja_percent: int,
    bundled_ja_files: list[str] | None = None,
) -> tuple[bool, str]:
    """公式パックや完了率から「販売する意味が薄い」かを判定する。

    日本語サイトでは WordPress が translate.wordpress.org の language pack を
    自動ダウンロードする。パックがあるプラグインは、自作ファイルを入れる前から
    日本語UIになる。
    """
    if has_official_ja_pack:
        if official_ja_percent is not None:
            return True, (
                f"公式日本語 language pack が公開されています（約 {official_ja_percent}%）。"
                "日本語サイトでは WordPress が自動適用するため、プラグインを入れるだけで日本語になります。"
            )
        return True, (
            "公式日本語 language pack が公開されています。"
            "日本語サイトでは WordPress が自動適用するため、プラグインを入れるだけで日本語になります。"
        )
    if official_ja_percent is not None and official_ja_percent >= skip_if_ja_percent:
        return True, f"公式日本語翻訳が約 {official_ja_percent}% です。"
    if bundled_ja_files and official_ja_percent is None:
        return False, "プラグイン同梱の日本語ファイルがあります。公式完了率は未取得です。"
    return False, ""


def find_plugin_root(extract_dir: Path) -> Path:
    php_files = list(extract_dir.rglob("*.php"))
    if not php_files:
        raise PipelineError("ZIP展開", "PHPファイルが見つかりません。プラグインZIPではない可能性があります。")
    candidates: list[Path] = []
    for path in php_files:
        try:
            head = path.read_text(encoding="utf-8", errors="replace")[:8000]
        except OSError:
            continue
        if re.search(r"Plugin Name\s*:", head):
            candidates.append(path)
    if not candidates:
        children = [p for p in extract_dir.iterdir() if p.is_dir()]
        if len(children) == 1:
            return children[0]
        return extract_dir
    candidates.sort(key=lambda p: (len(p.parts), len(p.name)))
    return candidates[0].parent


def parse_plugin_headers(plugin_root: Path) -> dict[str, str]:
    headers: dict[str, str] = {}
    for path in sorted(plugin_root.glob("*.php")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:16000]
        except OSError:
            continue
        if "Plugin Name:" not in text and "Plugin Name :" not in text:
            continue
        for label, key in HEADER_KEYS.items():
            match = re.search(rf"^[ \t\*#]*{re.escape(label)}\s*:\s*(.+)$", text, re.M)
            if match:
                headers[key] = match.group(1).strip()
        if headers:
            headers["main_file"] = str(path)
            break
    readme = _find_readme(plugin_root)
    if readme:
        readme_text = readme.read_text(encoding="utf-8", errors="replace")[:20000]
        if "license" not in headers:
            match = re.search(r"^License:\s*(.+)$", readme_text, re.M | re.I)
            if match:
                headers["license"] = match.group(1).strip()
    return headers


def _find_readme(plugin_root: Path) -> Path | None:
    for name in ("readme.txt", "README.txt", "readme.md"):
        path = plugin_root / name
        if path.exists():
            return path
    return None


def _collect_lang_files(plugin_root: Path) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {ext: [] for ext in (".po", ".mo", ".pot", ".json")}
    for path in plugin_root.rglob("*"):
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        if ext in found:
            found[ext].append(str(path.relative_to(plugin_root)))
    return found


class PluginAnalyzer:
    def __init__(self, wp: WordPressClient, logger: logging.Logger) -> None:
        self.wp = wp
        self.logger = logger

    def analyze(self, extract_dir: Path, info: PluginInfo, skip_if_ja_percent: int) -> Analysis:
        plugin_root = find_plugin_root(extract_dir)
        headers = parse_plugin_headers(plugin_root)
        text_domain = headers.get("text_domain") or info.text_domain or info.slug
        domain_path = headers.get("domain_path") or "/languages"
        languages_dir = None
        for candidate in (
            plugin_root / domain_path.lstrip("/"),
            plugin_root / "languages",
            plugin_root / "lang",
        ):
            if candidate.is_dir():
                languages_dir = candidate
                break
        bundled = _collect_lang_files(plugin_root)
        pots = bundled.get(".pot") or []
        pot_path = ""
        if pots:
            preferred = [p for p in pots if Path(p).name.lower().startswith(text_domain.lower())]
            pot_path = str(plugin_root / (preferred[0] if preferred else pots[0]))
        ja_files = [
            rel
            for ext in bundled
            for rel in bundled[ext]
            if re.search(r"[-_]ja([-_.]|$)", Path(rel).name, re.I)
        ]
        license_name = headers.get("license") or info.license or ""
        ja_pack = self.wp.japanese_language_pack(info.slug, info.version, info.language_packs)
        percent = self.wp.glotpress_ja_percent(info.slug)
        already, reason = decide_already_translated(
            official_ja_percent=percent,
            has_official_ja_pack=bool(ja_pack),
            skip_if_ja_percent=skip_if_ja_percent,
            bundled_ja_files=ja_files,
        )
        self.logger.info(
            "日本語対応状況: percent=%s pack=%s bundled_ja=%s already=%s",
            percent,
            bool(ja_pack),
            ja_files,
            already,
        )
        return Analysis(
            plugin_root=plugin_root,
            headers=headers,
            text_domain=text_domain,
            domain_path=domain_path,
            languages_dir=languages_dir,
            bundled_files=bundled,
            has_pot=bool(pots),
            pot_path=pot_path,
            ja_files=ja_files,
            official_ja_percent=percent,
            has_official_ja_pack=bool(ja_pack),
            already_translated=already,
            reason=reason,
            license=license_name,
        )


def extract_strings(plugin_root: Path, text_domain: str) -> list[TranslatableString]:
    collected: dict[tuple[str, str, str], TranslatableString] = {}

    def add(item: TranslatableString) -> None:
        existing = collected.get(item.key)
        if existing:
            for ref in item.references:
                if ref not in existing.references:
                    existing.references.append(ref)
            return
        collected[item.key] = item

    for path in plugin_root.rglob("*"):
        if not path.is_file():
            continue
        rel = str(path.relative_to(plugin_root)).replace("\\", "/")
        suffix = path.suffix.lower()
        if suffix == ".php":
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for item in extract_php_strings(source, rel):
                add(item)
        elif suffix in {".js", ".jsx", ".ts", ".tsx"}:
            if "node_modules" in path.parts or "vendor" in path.parts:
                continue
            if path.name.endswith(".min.js") or path.name.endswith(".min.ts"):
                continue
            if any(part in {"wp-admin", "wp-includes"} for part in path.parts):
                continue
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for item in extract_js_strings(source, rel):
                add(item)
        elif path.name == "block.json":
            try:
                data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, json.JSONDecodeError):
                continue
            for field in ("title", "description"):
                value = data.get(field)
                if isinstance(value, str) and value.strip():
                    add(TranslatableString(msgid=value, references=[f"{rel}:{field}"]))

    headers = parse_plugin_headers(plugin_root)
    header_comments = {
        "plugin_name": "Plugin Name of the plugin",
        "plugin_uri": "Plugin URI of the plugin",
        "description": "Description of the plugin",
        "author": "Author of the plugin",
        "author_uri": "Author URI of the plugin",
    }
    main_ref = headers.get("main_file") or "plugin.php"
    try:
        main_rel = str(Path(main_ref).resolve().relative_to(plugin_root.resolve())).replace("\\", "/")
    except Exception:
        main_rel = Path(main_ref).name
    for key, comment in header_comments.items():
        value = (headers.get(key) or "").strip()
        if value:
            add(
                TranslatableString(
                    msgid=value,
                    references=[main_rel],
                    extracted_comment=comment,
                )
            )

    items = [item for item in collected.values() if item.msgid.strip()]
    items.sort(key=lambda i: (i.references[:1], i.msgid))
    return items


def extract_php_strings(source: str, relpath: str) -> list[TranslatableString]:
    """Read-only PHP call scanner. Does not rewrite source."""
    tokens = _php_tokenize(source)
    results: list[TranslatableString] = []
    i = 0
    translator_comment = ""
    while i < len(tokens):
        kind, value, line = tokens[i]
        if kind == "comment":
            if "translators:" in value.lower():
                translator_comment = re.sub(r"^/\*+|\*+/$", "", value).strip()
            i += 1
            continue
        if kind == "ident":
            name = value.lstrip("\\")
            if name in I18N_FUNCS and i + 1 < len(tokens) and tokens[i + 1][0] == "(":
                args, end = _parse_php_args(tokens, i + 2)
                spec = I18N_FUNCS[name]
                singular = _arg_string(args, spec["singular"])
                if singular:
                    results.append(
                        TranslatableString(
                            msgid=singular,
                            msgid_plural=_arg_string(args, spec.get("plural", -1)) or "",
                            msgctxt=_arg_string(args, spec.get("context", -1)) or "",
                            references=[f"{relpath}:{line}"],
                            extracted_comment=translator_comment,
                        )
                    )
                translator_comment = ""
                i = end
                continue
            translator_comment = ""
        i += 1
    return results


def extract_js_strings(source: str, relpath: str) -> list[TranslatableString]:
    results: list[TranslatableString] = []
    # Conservative: only extract when a known i18n function is followed by string literals.
    pattern = re.compile(
        r"""(?<![A-Za-z0-9_])(__|_x|_n|_nx)\s*\(\s*(['"`])""",
    )
    for match in pattern.finditer(source):
        func = match.group(1)
        start = match.end() - 1
        args, _ok = _parse_js_args(source, start)
        if not args:
            continue
        line = source.count("\n", 0, match.start()) + 1
        if func in {"__", "_e"}:
            if args[0]:
                results.append(TranslatableString(msgid=args[0], references=[f"{relpath}:{line}"]))
        elif func == "_x" and len(args) >= 2:
            results.append(TranslatableString(msgid=args[0], msgctxt=args[1], references=[f"{relpath}:{line}"]))
        elif func == "_n" and len(args) >= 2:
            results.append(
                TranslatableString(msgid=args[0], msgid_plural=args[1], references=[f"{relpath}:{line}"])
            )
        elif func == "_nx" and len(args) >= 3:
            results.append(
                TranslatableString(
                    msgid=args[0],
                    msgid_plural=args[1],
                    msgctxt=args[2],
                    references=[f"{relpath}:{line}"],
                )
            )
    return results


def _php_tokenize(source: str) -> list[tuple[str, str, int]]:
    tokens: list[tuple[str, str, int]] = []
    i = 0
    n = len(source)
    line = 1
    while i < n:
        ch = source[i]
        if ch == "\n":
            line += 1
            i += 1
            continue
        if ch in " \t\r":
            i += 1
            continue
        if ch == "#" or (ch == "/" and i + 1 < n and source[i + 1] == "/"):
            j = source.find("\n", i)
            if j < 0:
                j = n
            tokens.append(("comment", source[i:j], line))
            i = j
            continue
        if ch == "/" and i + 1 < n and source[i + 1] == "*":
            j = source.find("*/", i + 2)
            if j < 0:
                j = n
            else:
                j += 2
            comment = source[i:j]
            tokens.append(("comment", comment, line))
            line += comment.count("\n")
            i = j
            continue
        if ch in {"'", '"'}:
            string, i, extra_lines = _read_php_string(source, i)
            tokens.append(("string", string, line))
            line += extra_lines
            continue
        if ch.isalpha() or ch in "_\\":
            j = i + 1
            while j < n and (source[j].isalnum() or source[j] in "_\\"):
                j += 1
            tokens.append(("ident", source[i:j], line))
            i = j
            continue
        if ch.isdigit():
            j = i + 1
            while j < n and (source[j].isalnum() or source[j] == "."):
                j += 1
            tokens.append(("number", source[i:j], line))
            i = j
            continue
        tokens.append((ch, ch, line))
        i += 1
    return tokens


def _read_php_string(source: str, start: int) -> tuple[str, int, int]:
    quote = source[start]
    i = start + 1
    chars: list[str] = []
    extra_lines = 0
    n = len(source)
    while i < n:
        ch = source[i]
        if ch == "\\" and i + 1 < n:
            nxt = source[i + 1]
            escapes = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", quote: quote}
            chars.append(escapes.get(nxt, nxt))
            i += 2
            continue
        if ch == quote:
            return "".join(chars), i + 1, extra_lines
        if ch == "\n":
            extra_lines += 1
        chars.append(ch)
        i += 1
    return "".join(chars), i, extra_lines


def _parse_php_args(tokens: list[tuple[str, str, int]], start: int) -> tuple[list[list[tuple[str, str]]], int]:
    args: list[list[tuple[str, str]]] = [[]]
    depth = 1
    i = start
    while i < len(tokens):
        kind, value, _line = tokens[i]
        if kind == "(":
            depth += 1
            args[-1].append((kind, value))
        elif kind == ")":
            depth -= 1
            if depth == 0:
                return args, i + 1
            args[-1].append((kind, value))
        elif kind == "," and depth == 1:
            args.append([])
        else:
            args[-1].append((kind, value))
        i += 1
    return args, i


def _arg_string(args: list[list[tuple[str, str]]], index: int) -> str | None:
    if index < 0 or index >= len(args):
        return None
    parts = args[index]
    strings = [v for k, v in parts if k == "string"]
    others = [k for k, v in parts if k not in {"string", ".", "comment"} and v not in {".", " "}]
    # Concatenation of string literals only.
    if strings and not others:
        return "".join(strings)
    if len(parts) == 1 and parts[0][0] == "string":
        return parts[0][1]
    return None


def _parse_js_args(source: str, start: int) -> tuple[list[str], bool]:
    """Parse JS string-literal arguments starting at the first quote of arg0."""
    args: list[str] = []
    i = start
    n = len(source)
    depth = 1  # we enter after '(' already consumed by caller? start is first quote, '(' already passed
    # Caller start is first quote of first arg. Reconstruct from the '(' .
    # Walk backward is unnecessary: scan arguments from current string, then comma, etc.
    # Simpler: find matching call by scanning from the character before start.
    paren = source.rfind("(", 0, start)
    if paren < 0:
        return [], False
    i = paren + 1
    depth = 1
    current: list[str] = []
    in_str = ""
    escape = False
    while i < n and depth > 0:
        ch = source[i]
        if in_str:
            if escape:
                current.append(ch)
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == in_str:
                args.append(_js_unescape("".join(current), in_str))
                current = []
                in_str = ""
            else:
                current.append(ch)
            i += 1
            continue
        if ch in {"'", '"', "`"}:
            in_str = ch
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        i += 1
    return args, True


def _js_unescape(text: str, quote: str) -> str:
    return (
        text.replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace("\\r", "\r")
        .replace("\\'", "'")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )


def strings_to_jsonable(items: Iterable[TranslatableString]) -> list[dict[str, Any]]:
    return [
        {
            "msgid": i.msgid,
            "msgid_plural": i.msgid_plural,
            "msgctxt": i.msgctxt,
            "references": i.references,
            "extracted_comment": i.extracted_comment,
        }
        for i in items
    ]
