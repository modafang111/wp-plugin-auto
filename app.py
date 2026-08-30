"""CLI entry point: WordPress plugin JA localization + BASE listing automation."""

from __future__ import annotations

import argparse
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import Settings, load_settings
from src.base_client import BaseClient
from src.base_template import BaseTemplateService
from src.database import Database
from src.exceptions import NeedsHumanReview, PipelineError, SkipPlugin
from src.logger import log_exception, setup_logger
from src.legacy_catalog import (
    detail_for_item,
    latest_zip_for_slug,
    load_legacy_items,
    merge_scanned_items,
    scan_public_ja_items,
    write_delivery_map,
)
from src.mailer import Mailer
from src.order_delivery import OrderDeliveryService, ensure_test_zip
from src.package_builder import PackageBuilder
from src.plugin_analyzer import PluginAnalyzer
from src.plugin_downloader import PluginDownloader
from src.plugin_discovery import confirm_free_official, discover_plugins, import_discovered_txt
from src.translation_builder import TranslationBuilder, dump_strings
from src.translator import get_translator, load_extra_glossary
from src.utils import SafeHttp, extract_plugin_slug, official_plugin_url, read_json, write_json
from src.wordpress import PluginInfo, WordPressClient


STAGE_ORDER = [
    "wp_info",
    "downloaded",
    "extracted",
    "analyzed",
    "strings_extracted",
    "translated",
    "quality_checked",
    "packaged",
    "preview_ready",
    "base_registered",
    "completed",
]

PROCESS_OK = 0
PROCESS_ERROR = 1
PROCESS_NOT_FOUND = 2
PROCESS_SKIPPED = 10


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="WordPress公式プラグインの日本語化ファイル作成と BASE 商品登録を自動化します。",
    )
    parser.add_argument("url", nargs="?", help="https://wordpress.org/plugins/<slug>/")
    parser.add_argument("--input", dest="input_file", help="1行1URLのテキストファイル")
    parser.add_argument(
        "--discover",
        action="store_true",
        help="WordPress.org から公式JAパックの無いプラグインを自動取得する（URL省略時）",
    )
    parser.add_argument(
        "--discover-only",
        action="store_true",
        help="自動取得だけ行い、翻訳・BASE登録はしない",
    )
    parser.add_argument("--limit", type=int, default=None, help="自動取得する件数（初期値は DISCOVER_LIMIT=1）")
    parser.add_argument("--browse", default="", help="popular / new / updated。カンマ区切り可")
    parser.add_argument("--dry-run", action="store_true", help="BASEへ実登録しない")
    parser.add_argument("--resume", action="store_true", help="前回の途中から再開（翻訳キャッシュを利用）")
    parser.add_argument("--translate-only", action="store_true", help="翻訳と販売ZIPまで。BASE登録しない")
    parser.add_argument("--base-only", action="store_true", help="既存の翻訳成果から BASE 登録だけ行う")
    parser.add_argument("--force", action="store_true", help="同一versionの登録済み・十分日本語化済みでも続行")
    parser.add_argument("--base-auth", action="store_true", help="BASE OAuth 認可コードをトークンへ交換する")
    parser.add_argument("--fetch-template", action="store_true", help="テンプレート商品を取得してキャッシュする")
    parser.add_argument("--test-mail", action="store_true", help="NOTIFY_EMAIL へテストメールを送る")
    parser.add_argument(
        "--test-deliver",
        action="store_true",
        help="NOTIFY_EMAIL へ日本語化ZIP付きのお届けテストを送る（購入者には送らない）",
    )
    parser.add_argument(
        "--test-base",
        action="store_true",
        help="非公開のテスト商品を1件だけ実登録する（DRY_RUN を無視。テンプレートは変更しない）",
    )
    parser.add_argument(
        "--register-draft",
        action="store_true",
        help="DRY_RUN を無視し、非公開のまま BASE へ実登録する（テンプレートは変更しない）",
    )
    parser.add_argument(
        "--register",
        action="store_true",
        help="DRY_RUN を無視し、公開のまま BASE へ実登録する（テンプレートは変更しない）",
    )
    parser.add_argument(
        "--otp",
        default="",
        help="BASEのメール認証番号（6桁）。実登録時のみ。ログには残さない",
    )
    parser.add_argument("--update-image", metavar="ITEM_ID", help="指定した BASE 商品の画像だけ差し替える（テンプレートは不可）")
    parser.add_argument("--image", help="--update-image で使う PNG/JPG のパス")
    parser.add_argument(
        "--deliver-orders",
        action="store_true",
        help="売れた通常商品の日本語化ZIPを購入者へメールする（デジタルコンテンツApp未導入時）",
    )
    parser.add_argument("--watch", action="store_true", help="--deliver-orders を繰り返し実行する")
    parser.add_argument(
        "--sync-legacy",
        action="store_true",
        help="過去の日本語化商品をお届け対象に含め、説明文プレビューを揃える",
    )
    parser.add_argument(
        "--rewrite-pages",
        action="store_true",
        help="--sync-legacy と一緒に、テンプレート以外の過去商品ページを新書式へ更新する",
    )
    parser.add_argument(
        "--build-zips",
        action="store_true",
        help="--sync-legacy と一緒に、足りない過去商品の日本語化ZIPを作る",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    overrides = {}
    if args.dry_run:
        overrides["DRY_RUN"] = "true"
    if args.register_draft:
        overrides["DRY_RUN"] = "false"
        overrides["BASE_PUBLISH_MODE"] = "draft"
    if args.register:
        overrides["DRY_RUN"] = "false"
        overrides["BASE_PUBLISH_MODE"] = "public"
    if args.discover_only:
        args.discover = True
    if args.browse:
        overrides["DISCOVER_BROWSE"] = args.browse
    if args.limit is not None:
        overrides["DISCOVER_LIMIT"] = str(args.limit)
    settings = load_settings(overrides=overrides)
    settings.ensure_directories()
    load_extra_glossary(settings.data_dir / "templates" / "glossary.json")

    if args.base_auth:
        return run_base_auth(settings)
    if args.test_mail:
        return run_test_mail(settings)
    if args.test_base:
        return run_test_base(settings, otp=args.otp)
    if args.update_image:
        return run_update_image(settings, args.update_image, args.image, otp=args.otp)
    if args.fetch_template:
        logger, log_path = setup_logger(settings.logs_dir, slug="template", secrets=settings.secret_values())
        logger.info("処理開始: テンプレート取得")
        client = BaseClient(settings, logger)
        template = BaseTemplateService(settings, logger, client).load()
        write_json(settings.template_cache_path, template.to_dict())
        logger.info("テンプレート保存: %s source=%s", settings.template_cache_path, template.source)
        return 0
    if args.test_deliver:
        return run_test_deliver(settings)
    if args.deliver_orders:
        return run_deliver_orders(settings, dry_run=args.dry_run, watch=args.watch, otp=args.otp)
    if args.sync_legacy:
        return run_sync_legacy(
            settings,
            rewrite_pages=args.rewrite_pages,
            build_zips=args.build_zips,
            otp=args.otp,
        )

    urls = collect_urls(args, settings)
    if args.discover and not urls:
        if args.discover_only:
            discovered = run_discover(settings, limit=args.limit, for_register=False)
            return PROCESS_OK if discovered else PROCESS_ERROR
        return run_register_from_discover(args, settings)
    if not urls:
        return _no_plugin_found(settings)

    exit_code = PROCESS_OK
    for url in urls:
        code = process_one(url, args, settings)
        if code == PROCESS_SKIPPED:
            continue
        if code != PROCESS_OK:
            exit_code = code
    return exit_code


def collect_urls(args: argparse.Namespace, settings: Settings) -> list[str]:
    urls: list[str] = []
    if args.url:
        urls.append(args.url.strip())
        return urls
    if getattr(args, "discover", False) or getattr(args, "discover_only", False):
        return []
    input_file = Path(args.input_file) if args.input_file else None
    if not urls and not input_file:
        default_input = settings.input_dir / "plugins.txt"
        if default_input.exists() and default_input.read_text(encoding="utf-8").strip():
            input_file = default_input
    if input_file:
        text = Path(input_file).read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    return urls


def _no_plugin_found(settings: Settings) -> int:
    logger, log_path = setup_logger(settings.logs_dir, slug="register", secrets=settings.secret_values())
    message = "登録するプラグインが見つかりませんでした。URL指定か --discover が必要です。"
    logger.error(message)
    Mailer(settings, logger).error(
        {
            "plugin_name": "(未選択)",
            "stage": "対象選択",
            "error": message,
            "log_path": str(log_path),
            "retry": "python app.py --register --discover --limit 1",
        }
    )
    print(
        "プラグインURLを指定するか、--discover で WordPress.org から自動取得してください。",
        file=sys.stderr,
    )
    return PROCESS_NOT_FOUND


def run_register_from_discover(args: argparse.Namespace, settings: Settings) -> int:
    """Register DISCOVER_LIMIT plugins, skipping paid/ineligible ones in the same run."""
    want = args.limit if args.limit is not None else settings.discover_limit
    want = max(1, min(int(want), 20))
    processed = 0
    seen: set[str] = set()
    max_tries = max(want * 15, min(settings.discover_max_pages, 80))
    logger, _log_path = setup_logger(settings.logs_dir, slug="register", secrets=settings.secret_values())
    for attempt in range(1, max_tries + 1):
        remaining = want - processed
        batch = run_discover(settings, limit=remaining, for_register=True)
        fresh = [url for url in batch if url not in seen]
        if not fresh:
            break
        for url in fresh:
            seen.add(url)
            logger.info("自動取得 %s/%s 件目を処理します: %s", processed + 1, want, url)
            code = process_one(url, args, settings)
            if code == PROCESS_SKIPPED:
                logger.info("対象外のため次の無料プラグインを探します: %s", url)
                continue
            if code != PROCESS_OK:
                return code
            processed += 1
            if processed >= want:
                return PROCESS_OK
        if attempt == max_tries:
            break
    if processed == 0:
        return _no_plugin_found(settings)
    return PROCESS_OK


def run_discover(settings: Settings, limit: int | None = None, *, for_register: bool = False) -> list[str]:
    logger, log_path = setup_logger(settings.logs_dir, slug="discover", secrets=settings.secret_values())
    db = Database(settings.db_path)
    try:
        wp = WordPressClient(SafeHttp(timeout=settings.http_timeout_seconds))
        want = limit if limit is not None else settings.discover_limit
        imported = import_discovered_txt(settings.input_dir / "discovered.txt", db)
        if imported:
            logger.info("前回の discovered.txt から %s 件をキューへ入れました（同じ一覧は出しません）", imported)

        if for_register:
            pending = [
                row
                for row in db.queued_plugins()
                if row.get("url") and not db.slug_is_finished(str(row.get("slug") or ""))
            ]
            urls: list[str] = []
            for row in pending:
                slug = str(row.get("slug") or "")
                version = str(row.get("version") or "")
                pack = wp.japanese_language_pack(slug, version) if slug else None
                if pack:
                    logger.info("キューの %s は公式JAパックがあるためスキップします", slug)
                    db.upsert_job(
                        slug,
                        version or "unknown",
                        plugin_name=str(row.get("name") or slug),
                        wordpress_url=str(row.get("url") or ""),
                        status="skipped_already_translated",
                        error_message="公式日本語 language pack が公開されています。",
                    )
                    continue
                skip_reason = confirm_free_official(wp, slug)
                if skip_reason:
                    logger.info(
                        "キューの %s は対象外のため次の無料プラグインを探します: %s",
                        slug,
                        skip_reason,
                    )
                    db.upsert_job(
                        slug,
                        version or "unknown",
                        plugin_name=str(row.get("name") or slug),
                        wordpress_url=str(row.get("url") or ""),
                        status="skipped_not_eligible",
                        error_message=skip_reason,
                    )
                    continue
                urls.append(str(row["url"]))
                logger.info("キューから登録: %s %s", row.get("name") or slug, row.get("url"))
                if len(urls) >= want:
                    break
            if urls:
                return urls

        found = discover_plugins(wp, db, settings, logger, limit=want)
        for item in found:
            db.enqueue_discovered(
                item.slug,
                version=item.version,
                name=item.name,
                url=item.url,
                active_installs=item.active_installs,
            )
        lines = [
            f"{item.url}  # {item.name} {item.version} installs={item.active_installs if item.active_installs is not None else '?'}"
            for item in found
        ]
        out = settings.input_dir / "discovered.txt"
        header = [
            "# この実行で新しく見つけたプラグイン（前回出したものはキューに残し、繰り返しません）",
            f"# 未登録キュー: {len([r for r in db.queued_plugins() if not db.slug_is_finished(str(r.get('slug') or ''))])} 件",
            f"# 取得日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
        ]
        out.write_text("\n".join(header + lines) + ("\n" if lines else ""), encoding="utf-8")
        logger.info("新しい候補: %s件 保存=%s ログ=%s", len(found), out, log_path)
        if for_register:
            logger.info("register.bat は未登録キューの先頭から処理します。discover.bat を繰り返すと次の新規一覧になります。")
        for line in lines:
            print(line)
        return [item.url for item in found]
    finally:
        db.close()


def process_one(url: str, args: argparse.Namespace, settings: Settings) -> int:
    slug = "unknown"
    logger = None
    log_path = settings.logs_dir / "init.log"
    db = Database(settings.db_path)
    mailer = None
    screenshot_dir = settings.screenshots_dir
    try:
        slug = extract_plugin_slug(url)
        secrets = list(settings.secret_values())
        otp = getattr(args, "otp", "") or ""
        if otp:
            secrets.append(otp)
        logger, log_path = setup_logger(settings.logs_dir, slug=slug, secrets=secrets)
        mailer = Mailer(settings, logger)
        logger.info("処理開始")
        logger.info("URL解析: %s -> %s", url, slug)
        http = SafeHttp(timeout=settings.http_timeout_seconds)
        wp = WordPressClient(http)
        downloader = PluginDownloader(settings, http, db, logger)
        analyzer = PluginAnalyzer(wp, logger)
        builder = TranslationBuilder(logger)
        packager = PackageBuilder(settings, logger)
        base_client = BaseClient(settings, logger)
        templates = BaseTemplateService(settings, logger, base_client)

        info = wp.fetch_plugin(slug)
        logger.info("WordPress情報取得: %s %s", info.name, info.version)
        work = downloader.work_dir(info)
        work.mkdir(parents=True, exist_ok=True)
        write_json(work / "plugin_info.json", info.to_dict())
        screenshot_dir = settings.screenshots_dir / f"{info.slug}-{info.version}"

        existing_success = db.successful_job(info.slug, info.version)
        if existing_success and not args.force and not args.translate_only:
            logger.info("同一slug・同一バージョンは登録済みのためスキップします")
            db.upsert_job(info.slug, info.version, status="skipped_duplicate", plugin_name=info.name, wordpress_url=info.official_url)
            return PROCESS_SKIPPED
        previous = db.latest_job(info.slug)
        if previous and previous.get("plugin_version") != info.version:
            logger.info("更新版を検出: %s -> %s", previous.get("plugin_version"), info.version)

        job = db.get_job(info.slug, info.version)
        if job and (args.resume or args.base_only):
            db.upsert_job(
                info.slug,
                info.version,
                plugin_name=info.name,
                wordpress_url=info.official_url,
                download_url=info.download_url,
            )
            job = db.get_job(info.slug, info.version) or job
        else:
            job = db.upsert_job(
                info.slug,
                info.version,
                plugin_name=info.name,
                wordpress_url=info.official_url,
                download_url=info.download_url,
                status="wp_info",
                stage="wp_info",
            )
        if not should_skip(job, "downloaded", args):
            downloader.download(info)
            job = db.upsert_job(info.slug, info.version, status="downloaded", stage="downloaded")
        if not should_skip(job, "extracted", args):
            logger.info("ZIP展開")
            downloader.extract(info)
            job = db.upsert_job(info.slug, info.version, status="extracted", stage="extracted")
        job = db.get_job(info.slug, info.version) or job

        extract_dir = work / "original"
        analysis = analyzer.analyze(extract_dir, info, settings.skip_if_ja_percent)
        write_json(work / "analysis.json", analysis.to_dict())
        db.upsert_job(info.slug, info.version, status="analyzed", stage="analyzed")
        if analysis.already_translated and not settings.continue_if_already_translated and not args.force:
            msg = f"既に十分日本語化されている可能性があります。{analysis.reason}"
            logger.info(msg)
            db.upsert_job(
                info.slug,
                info.version,
                status="skipped_already_translated",
                error_message=msg,
            )
            mailer.needs_review(
                {
                    "plugin_name": info.name,
                    "plugin_version": info.version,
                    "reason": msg,
                    "log_path": str(log_path),
                    "retry": f'python app.py --force "{info.official_url}"',
                }
            )
            logger.info("処理終了")
            return PROCESS_SKIPPED

        translation_dir = work / "translation"
        strings_path = work / "strings.json"
        items = []
        if args.base_only:
            items = _load_saved_strings(strings_path)
        elif not should_skip(job, "strings_extracted", args) or not strings_path.exists():
            logger.info("翻訳対象抽出")
            items = builder.collect_strings(analysis.plugin_root, analysis.text_domain, analysis.pot_path)
            dump_strings(strings_path, items)
            db.upsert_job(info.slug, info.version, status="strings_extracted", stage="strings_extracted")
            if not items:
                raise PipelineError("翻訳対象抽出", "翻訳対象文字列が見つかりません。i18n未対応の可能性があります。")
        else:
            items = _load_saved_strings(strings_path)

        if not items:
            raise PipelineError("翻訳対象抽出", "翻訳対象文字列が見つかりません。i18n未対応の可能性があります。")

        translations_path = translation_dir / "translations.json"
        translations: list[str] = []
        if args.base_only and translations_path.exists():
            translations = [row.get("msgstr") or "" for row in (read_json(translations_path, []) or [])]
        elif not should_skip(job, "translated", args) or not translations_path.exists():
            translator = get_translator(settings, db, logger)
            translations = translator.translate_all(items)
            translation_dir.mkdir(parents=True, exist_ok=True)
            db.upsert_job(info.slug, info.version, status="translated", stage="translated", translation_date=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))
        else:
            translations = [row.get("msgstr") or "" for row in (read_json(translations_path, []) or [])]
            if len(translations) != len(items):
                translator = get_translator(settings, db, logger)
                translations = translator.translate_all(items)

        quality = builder.quality_check(items, translations)
        write_json(work / "quality.json", quality.to_dict())
        db.upsert_job(info.slug, info.version, status="quality_checked", stage="quality_checked")
        if not quality.ok:
            logger.warning("品質チェックで重大な指摘があります: %s", quality.errors[:8])
            if not settings.dry_run and not args.translate_only:
                raise PipelineError("品質チェック", "重大な翻訳エラーがあるため BASE へ自動登録しません。\n" + "\n".join(quality.errors[:12]))

        catalog = builder.write_catalog(info, analysis.text_domain, items, translations, translation_dir, analysis.plugin_root)
        package = packager.build(
            info,
            analysis.text_domain,
            translation_dir,
            work,
            analysis.plugin_root,
            analysis.license,
        )
        db.upsert_job(info.slug, info.version, status="packaged", stage="packaged", output_zip=package["output_zip"])

        if args.translate_only:
            logger.info("翻訳のみ指定のため BASE 登録は行いません")
            _notify_success(mailer, settings, info, quality, package, None, None, zip_only=True)
            logger.info("処理終了")
            return 0

        logger.info("商品情報生成")
        template = templates.load()
        listing = templates.build_listing(info, template, package, quality.to_dict())
        listing["dry_run"] = settings.dry_run
        preview_json = settings.output_dir / f"{info.slug}-{info.version}-preview.json"
        preview_txt = settings.output_dir / f"{info.slug}-{info.version}-preview.txt"
        write_json(preview_json, listing)
        preview_txt.write_text(_listing_text(listing), encoding="utf-8")
        write_json(work / "preview.json", listing)
        logger.info("プレビュー生成: %s", preview_json)
        db.upsert_job(info.slug, info.version, status="preview_ready", stage="preview_ready")

        if settings.dry_run:
            logger.info("DRY_RUN=true のため BASE へは登録しません")
            _notify_success(mailer, settings, info, quality, package, listing, None)
            db.upsert_job(info.slug, info.version, status="completed", stage="completed")
            logger.info("処理終了")
            return 0

        if not quality.ok:
            raise PipelineError("BASE商品登録", "品質エラーがあるため登録しません。")

        created = base_client.create_item(
            listing,
            zip_path=Path(package["output_zip"]),
            screenshot_dir=screenshot_dir,
            otp=getattr(args, "otp", "") or "",
        )
        listing["method"] = created.get("method")
        listing["file_uploaded"] = created.get("file_uploaded")
        db.upsert_job(
            info.slug,
            info.version,
            status="base_registered",
            stage="base_registered",
            base_product_id=created["item_id"],
            base_product_url=created.get("product_url") or "",
        )
        base_client.verify_item(created["item_id"], listing["title"])
        try:
            if settings.base_upload_digital_file and not created.get("file_uploaded"):
                base_client.upload_digital_file(listing, Path(package["output_zip"]), screenshot_dir)
        except NeedsHumanReview as review:
            db.upsert_job(info.slug, info.version, status="needs_review", error_message=review.message)
            mailer.needs_review(
                {
                    "plugin_name": info.name,
                    "plugin_version": info.version,
                    "reason": review.message,
                    "log_path": str(log_path),
                    "screenshot_path": str(screenshot_dir),
                    "output_zip": package["output_zip"],
                    "retry": f'python app.py --resume --base-only "{info.official_url}"',
                }
            )
            logger.info("処理終了")
            return 0

        listing["base_product_id"] = created["item_id"]
        listing["base_product_url"] = created.get("product_url")
        write_json(preview_json, listing)
        _notify_success(mailer, settings, info, quality, package, listing, created)
        db.upsert_job(info.slug, info.version, status="completed", stage="completed")
        logger.info("処理終了")
        return 0
    except SkipPlugin as exc:
        if logger:
            logger.info("対象外: %s", exc.message)
        db.upsert_job(slug, "unknown", plugin_name=slug, wordpress_url=official_plugin_url(slug), status="skipped_not_eligible", error_message=exc.message)
        if mailer and not getattr(args, "discover", False):
            mailer.needs_review({"plugin_name": slug, "reason": exc.message, "log_path": str(log_path)})
        return PROCESS_SKIPPED
    except NeedsHumanReview as exc:
        if logger:
            logger.error("要確認 (%s): %s", exc.stage, exc.message)
        db.upsert_job(slug, "unknown", status="needs_review", error_message=exc.message)
        if mailer:
            mailer.needs_review(
                {
                    "plugin_name": slug,
                    "reason": exc.message,
                    "log_path": str(log_path),
                    "screenshot_path": str(screenshot_dir),
                }
            )
        return 1
    except PipelineError as exc:
        if logger:
            logger.error("エラー (%s): %s", exc.stage, exc.message)
            log_exception(logger)
        else:
            print(f"エラー ({exc.stage}): {exc.message}", file=sys.stderr)
        try:
            db.upsert_job(slug, "unknown", status="error", error_message=f"{exc.stage}: {exc.message}")
        except Exception:
            pass
        if mailer:
            mailer.error(
                {
                    "plugin_name": slug,
                    "stage": exc.stage,
                    "error": exc.message,
                    "log_path": str(log_path),
                    "screenshot_path": str(screenshot_dir),
                    "retry": f'python app.py --resume "{url}"',
                }
            )
        return 1
    except Exception as exc:  # noqa: BLE001
        if logger:
            logger.error("予期しないエラー: %s", exc)
            log_exception(logger)
        else:
            traceback.print_exc()
        if mailer:
            mailer.error({"plugin_name": slug, "stage": "unknown", "error": str(exc), "log_path": str(log_path)})
        return 1
    finally:
        db.close()


def should_skip(job: dict, stage: str, args: argparse.Namespace) -> bool:
    if not args.resume and not args.base_only:
        return False
    current = job.get("stage") or job.get("status") or ""
    if current not in STAGE_ORDER or stage not in STAGE_ORDER:
        return False
    return STAGE_ORDER.index(current) >= STAGE_ORDER.index(stage)


def _load_saved_strings(path: Path):
    from src.plugin_analyzer import TranslatableString

    rows = read_json(path, []) or []
    return [
        TranslatableString(
            msgid=row.get("msgid") or "",
            msgid_plural=row.get("msgid_plural") or "",
            msgctxt=row.get("msgctxt") or "",
            references=list(row.get("references") or []),
            extracted_comment=row.get("extracted_comment") or "",
        )
        for row in rows
        if row.get("msgid")
    ]


def _listing_text(listing: dict) -> str:
    keys = [
        "title",
        "price",
        "visible",
        "publish_mode",
        "identifier",
        "category_ids",
        "image_url",
        "sales_file",
        "wordpress_url",
        "plugin_version",
        "dry_run",
    ]
    lines = ["BASE 登録予定内容", "=" * 40]
    for key in keys:
        lines.append(f"{key}: {listing.get(key)}")
    lines.append("")
    lines.append("detail:")
    lines.append(listing.get("detail") or "")
    return "\n".join(lines) + "\n"


def _notify_success(
    mailer: Mailer,
    settings: Settings,
    info: PluginInfo,
    quality,
    package,
    listing,
    created,
    *,
    zip_only: bool = False,
) -> None:
    mailer.success(
        {
            "plugin_name": info.name,
            "plugin_version": info.version,
            "wordpress_url": info.official_url,
            "translation_count": quality.translated_count,
            "untranslated_count": quality.untranslated_count,
            "base_title": (listing or {}).get("title") if listing else "",
            "price": (listing or {}).get("price") if listing else "",
            "base_product_url": (created or {}).get("product_url") if created else "",
            "admin_url": (created or {}).get("admin_url") if created else "",
            "method": (created or {}).get("method") if created else "",
            "output_zip": package.get("output_zip"),
            "publish_mode": settings.base_publish_mode,
            "processed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "dry_run": settings.dry_run,
            "zip_only": zip_only,
        }
    )


def run_test_base(settings: Settings, otp: str = "") -> int:
    """Register one unpublished digital item. Never edits/deletes the template product."""
    settings.dry_run = False
    settings.base_publish_mode = "draft"
    settings.base_upload_digital_file = True
    secrets = list(settings.secret_values())
    if otp:
        secrets.append(otp)
    logger, log_path = setup_logger(settings.logs_dir, slug="test-base", secrets=secrets)
    mailer = Mailer(settings, logger)
    screenshot_dir = settings.screenshots_dir / "test-base"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    logger.info("処理開始: BASE 非公開テスト商品を1件登録します")
    logger.info("テンプレート商品 %s は参照のみ。編集・削除はしません", settings.base_template_product_id or "(未設定)")
    zip_path = settings.output_dir / "hello-dolly-1.7.2-ja.zip"
    if not zip_path.exists():
        import zipfile

        zip_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("README.txt", ("自動登録テスト用の日本語化ファイルです。\n" * 40))
        logger.warning("既存の販売ZIPが無かったためテスト用ZIPを作りました: %s", zip_path)
    preview = read_json(settings.output_dir / "hello-dolly-1.7.2-preview.json", {}) or {}
    template_id = settings.base_template_product_id.strip() or "55749997"
    detail = (
        "これは自動登録テストの非公開商品です。ショップには表示しません。\n"
        "WordPress公式プラグイン本体は含まれていません。日本語化ファイルの登録確認用です。\n\n"
        + str(preview.get("detail") or "Hello Dolly の日本語化ファイルです。")
    )
    listing = {
        "title": "【テスト・非公開】Hello Dollyの日本語化ファイル",
        "detail": detail,
        "price": int(preview.get("price") or settings.product_price or 550),
        "stock": 99,
        "visible": 0,
        "item_tax_type": 1,
        "identifier": f"test-hd-{datetime.now().strftime('%Y%m%d%H%M')}",
        "category_ids": [],
        "image_url": "",
        "sales_file": str(zip_path),
        "publish_mode": "draft",
        "template_item_id": template_id,
        "plugin_name": "Hello Dolly",
        "plugin_version": "1.7.2",
        "wordpress_url": "https://wordpress.org/plugins/hello-dolly/",
    }
    preview_path = settings.output_dir / "test-base-preview.json"
    write_json(preview_path, listing)
    logger.info("登録予定: title=%s price=%s zip=%s", listing["title"], listing["price"], zip_path)
    client = BaseClient(settings, logger)
    try:
        created = client.create_item(listing, zip_path=zip_path, screenshot_dir=screenshot_dir, otp=otp)
        if created.get("item_id") == template_id:
            raise PipelineError("BASE商品登録", "テンプレート商品と同じIDです。登録結果を採用しません。")
        client.verify_item(created["item_id"], listing["title"])
        listing.update(
            {
                "base_product_id": created.get("item_id"),
                "base_product_url": created.get("product_url"),
                "admin_url": created.get("admin_url"),
                "method": created.get("method"),
                "dry_run": False,
            }
        )
        write_json(preview_path, listing)
        logger.info("非公開テスト商品を登録しました: item_id=%s", created.get("item_id"))
        mailer.send(
            "【BASE自動登録テスト完了・非公開】Hello Dolly",
            "\n".join(
                [
                    "非公開のテスト商品を1件登録しました。テンプレート商品は変更していません。",
                    f"商品名: {listing['title']}",
                    f"価格: {listing['price']}",
                    f"公開状態: 非公開（draft）",
                    f"BASE商品ID: {created.get('item_id')}",
                    f"公開URL（非公開のため出ない場合あり）: {created.get('product_url') or '(なし)'}",
                    f"管理画面: {created.get('admin_url') or '(なし)'}",
                    f"ファイル添付: {'あり' if created.get('file_uploaded') else 'なし（デジタルコンテンツメニュー無し）'}",
                    f"ログ: {log_path}",
                    "管理画面で内容を確認してください。自動削除はしません。",
                    "",
                ]
            ),
        )
        logger.info("処理終了")
        return 0
    except NeedsHumanReview as exc:
        logger.error("要確認 (%s): %s", exc.stage, exc.message)
        mailer.needs_review(
            {
                "plugin_name": listing["title"],
                "reason": exc.message,
                "log_path": str(log_path),
                "screenshot_path": str(screenshot_dir),
                "output_zip": str(zip_path),
                "retry": "認証番号が届いたら deliver-orders-dry-run.bat --otp で続きを実行",
            }
        )
        return 1
    except PipelineError as exc:
        logger.error("エラー (%s): %s", exc.stage, exc.message)
        mailer.error(
            {
                "plugin_name": listing["title"],
                "stage": exc.stage,
                "error": exc.message,
                "log_path": str(log_path),
                "screenshot_path": str(screenshot_dir),
                "retry": "python app.py --test-base",
            }
        )
        return 1


def run_update_image(settings: Settings, item_id: str, image: str | None, otp: str = "") -> int:
    from src.base_admin import BaseAdminClient

    secrets = list(settings.secret_values())
    if otp:
        secrets.append(otp)
    logger, log_path = setup_logger(settings.logs_dir, slug="update-image", secrets=secrets)
    path = Path(image) if image else None
    if path is None or not path.exists():
        logger.error("画像ファイルを --image で指定してください")
        return 2
    admin = BaseAdminClient(settings, logger, otp=otp)
    try:
        admin.replace_item_image(item_id.strip(), path, settings.screenshots_dir / f"update-image-{item_id}")
        logger.info("画像を更新しました: item_id=%s path=%s log=%s", item_id, path, log_path)
        return 0
    except NeedsHumanReview as exc:
        logger.error("要確認 (%s): %s", exc.stage, exc.message)
        return 1
    except PipelineError as exc:
        logger.error("エラー (%s): %s", exc.stage, exc.message)
        return 1


def run_test_deliver(settings: Settings) -> int:
    logger, _log_path = setup_logger(settings.logs_dir, slug="test-deliver", secrets=settings.secret_values())
    zip_path = ensure_test_zip(settings.output_dir)
    logger.info("テスト添付: %s", zip_path.name)
    db = Database(settings.db_path)
    try:
        mailer = Mailer(settings, logger)
        OrderDeliveryService(settings, db, mailer, logger).send_test(zip_path)
    finally:
        db.close()
    return 0


def run_sync_legacy(
    settings: Settings,
    *,
    rewrite_pages: bool = False,
    build_zips: bool = False,
    otp: str = "",
) -> int:
    """Map past JA listings for auto-delivery and optionally rewrite their copy."""
    from argparse import Namespace

    from src.base_admin import BaseAdminClient

    secrets = list(settings.secret_values())
    if otp:
        secrets.append(otp)
    logger, log_path = setup_logger(settings.logs_dir, slug="sync-legacy", secrets=secrets)
    template_id = str(settings.base_template_product_id or "").strip()
    logger.info("過去の日本語化商品をお届け対象に含め、説明文プレビューを新書式で揃えます")
    logger.info("テンプレート商品 %s はお届け対象です。商品ページは編集しません", template_id or "(未設定)")

    items = load_legacy_items(settings)
    scanned = scan_public_ja_items(settings, logger)
    if scanned:
        logger.info("公開カテゴリから日本語化商品を %s 件確認しました", len(scanned))
        items = merge_scanned_items(items, scanned)
    unmapped = [item for item in items if not item.slug]
    if unmapped:
        unknown_path = settings.output_dir / "legacy-preview" / "unmapped.json"
        unknown_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(
            unknown_path,
            [{"item_id": item.item_id, "title": item.title} for item in unmapped],
        )
        logger.warning(
            "slug 未設定の商品が %s 件あります。data/templates/legacy_items.json に追記してください: %s",
            len(unmapped),
            unknown_path,
        )

    if build_zips:
        zip_args = Namespace(
            force=True,
            translate_only=True,
            resume=False,
            base_only=False,
            otp=otp,
            dry_run=True,
            register=False,
            register_draft=False,
            discover=False,
        )
        for item in items:
            if not item.slug:
                continue
            existing = latest_zip_for_slug(settings.output_dir, item.slug)
            if existing is not None:
                logger.info("ZIPあり: %s %s", item.item_id, existing.name)
                continue
            url = item.wordpress_url or official_plugin_url(item.slug)
            logger.info("足りないZIPを作成します: %s %s", item.slug, url)
            code = process_one(url, zip_args, settings)
            if latest_zip_for_slug(settings.output_dir, item.slug) is None:
                logger.warning("ZIPを作れませんでした: item_id=%s slug=%s code=%s", item.item_id, item.slug, code)

    mapped = write_delivery_map(settings, items, settings.output_dir)
    missing = list(mapped.get("missing") or [])
    logger.info("お届け対応づけを保存しました: %s", settings.delivery_map_path)
    for item_id in missing:
        logger.warning("対応ZIPがまだありません: item_id=%s。--build-zips を実行してください", item_id)

    preview_dir = settings.output_dir / "legacy-preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    wp = WordPressClient(SafeHttp(timeout=settings.http_timeout_seconds))
    listings: list[tuple[object, dict]] = []
    for item in items:
        detail = detail_for_item(item, wp, settings)
        title = item.title or f"{item.plugin_name()}の日本語化ファイル"
        listing = {
            "title": title,
            "detail": detail,
            "protected": item.protected or item.item_id == template_id,
            "slug": item.slug,
            "item_id": item.item_id,
        }
        listings.append((item, listing))
        stem = f"{item.item_id}-{item.slug or 'unknown'}"
        write_json(preview_dir / f"{stem}.json", listing)
        (preview_dir / f"{stem}.txt").write_text(detail, encoding="utf-8")
        logger.info("説明プレビュー: %s", preview_dir / f"{stem}.txt")

    rewritten = 0
    skipped_protected = 0
    rewrite_failed = 0
    if rewrite_pages:
        logger.info("テンプレート以外の過去商品ページを新書式へ更新します（削除はしません）")
        admin = BaseAdminClient(settings, logger, otp=otp)
        screenshot_dir = settings.screenshots_dir / "sync-legacy"
        try:
            with admin.logged_in_page(screenshot_dir) as page:
                for item, listing in listings:
                    if listing.get("protected") or item.item_id == template_id:
                        skipped_protected += 1
                        logger.info("テンプレートのためページ更新をスキップ: %s", item.item_id)
                        continue
                    try:
                        admin.apply_item_copy(page, item.item_id, listing, screenshot_dir / item.item_id)
                        rewritten += 1
                    except (PipelineError, NeedsHumanReview) as exc:
                        rewrite_failed += 1
                        logger.error("ページ更新失敗 %s: %s", item.item_id, exc.message)
        except NeedsHumanReview as exc:
            logger.error("要確認 (%s): %s", exc.stage, exc.message)
            logger.info("ログ: %s", log_path)
            return 1

    logger.info(
        "完了: items=%s zip不足=%s ページ更新=%s テンプレートスキップ=%s 更新失敗=%s ログ=%s",
        len(items),
        len(missing),
        rewritten,
        skipped_protected,
        rewrite_failed,
        log_path,
    )
    if rewrite_pages and rewrite_failed:
        return 1
    if build_zips and missing:
        return 1
    return 0


def run_deliver_orders(settings: Settings, *, dry_run: bool, watch: bool, otp: str = "") -> int:
    secrets = list(settings.secret_values())
    if otp:
        secrets.append(otp)
    logger, _log_path = setup_logger(settings.logs_dir, slug="deliver-orders", secrets=secrets)
    if dry_run:
        logger.info("DRY RUN: 購入者へは送らず、対象注文だけ確認します")
    db = Database(settings.db_path)
    try:
        mailer = Mailer(settings, logger)
        service = OrderDeliveryService(settings, db, mailer, logger, otp=otp)
        if watch:
            try:
                service.watch(dry_run=dry_run)
            except KeyboardInterrupt:
                logger.info("注文監視を終了します")
            return 0
        counts = service.run_once(dry_run=dry_run)
        logger.info(
            "お届け結果: orders=%s sent=%s skipped=%s failed=%s",
            counts["orders"],
            counts["sent"],
            counts["skipped"],
            counts["failed"],
        )
        return 1 if counts["failed"] else 0
    except NeedsHumanReview as exc:
        logger.error("要確認 (%s): %s", exc.stage, exc.message)
        return 1
    except PipelineError as exc:
        logger.error("エラー (%s): %s", exc.stage, exc.message)
        return 1
    finally:
        db.close()


def run_test_mail(settings: Settings) -> int:
    logger, log_path = setup_logger(settings.logs_dir, slug="test-mail", secrets=settings.secret_values())
    settings.require_email = True
    mailer = Mailer(settings, logger)
    logger.info("テストメール送信先: %s", settings.notify_email)
    mailer.send(
        "【テスト】BASE商品登録のメール設定",
        "\n".join(
            [
                "base-wp-ja-auto のメール設定テストです。",
                "このメールが届けば SMTP 設定は有効です。",
                f"SMTP_HOST: {settings.smtp_host}",
                f"SMTP_PORT: {settings.smtp_port}",
                f"MAIL_FROM: {settings.mail_from or settings.smtp_user}",
                f"NOTIFY_EMAIL: {settings.notify_email}",
                f"ログ: {log_path}",
                "",
            ]
        ),
    )
    logger.info("テストメールを送信しました")
    return 0


def run_base_auth(settings: Settings) -> int:
    logger, _path = setup_logger(settings.logs_dir, slug="base-auth", secrets=settings.secret_values())
    client = BaseClient(settings, logger)
    print("次のURLをブラウザで開き、認可後の code を貼り付けてください。")
    print(client.authorization_url())
    print()
    code = input("code> ").strip()
    if not code:
        print("code が空です。", file=sys.stderr)
        return 2
    client.exchange_code(code)
    print(f"トークンを保存しました: {settings.token_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
