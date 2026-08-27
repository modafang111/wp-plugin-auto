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
from src.mailer import Mailer
from src.package_builder import PackageBuilder
from src.plugin_analyzer import PluginAnalyzer
from src.plugin_downloader import PluginDownloader
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="WordPress公式プラグインの日本語化ファイル作成と BASE 商品登録を自動化します。",
    )
    parser.add_argument("url", nargs="?", help="https://wordpress.org/plugins/<slug>/")
    parser.add_argument("--input", dest="input_file", help="1行1URLのテキストファイル")
    parser.add_argument("--dry-run", action="store_true", help="BASEへ実登録しない")
    parser.add_argument("--resume", action="store_true", help="前回の途中から再開（翻訳キャッシュを利用）")
    parser.add_argument("--translate-only", action="store_true", help="翻訳と販売ZIPまで。BASE登録しない")
    parser.add_argument("--base-only", action="store_true", help="既存の翻訳成果から BASE 登録だけ行う")
    parser.add_argument("--force", action="store_true", help="同一versionの登録済み・十分日本語化済みでも続行")
    parser.add_argument("--base-auth", action="store_true", help="BASE OAuth 認可コードをトークンへ交換する")
    parser.add_argument("--fetch-template", action="store_true", help="テンプレート商品を取得してキャッシュする")
    parser.add_argument("--test-mail", action="store_true", help="NOTIFY_EMAIL へテストメールを送る")
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
        "--otp",
        default="",
        help="BASEのメール認証番号（6桁）。実登録時のみ。ログには残さない",
    )
    parser.add_argument("--update-image", metavar="ITEM_ID", help="指定した BASE 商品の画像だけ差し替える（テンプレートは不可）")
    parser.add_argument("--image", help="--update-image で使う PNG/JPG のパス")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    overrides = {}
    if args.dry_run:
        overrides["DRY_RUN"] = "true"
    if args.register_draft:
        overrides["DRY_RUN"] = "false"
        overrides["BASE_PUBLISH_MODE"] = "draft"
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

    urls = collect_urls(args, settings)
    if not urls:
        print("プラグインURLを指定するか、--input で一覧ファイルを指定してください。", file=sys.stderr)
        return 2

    exit_code = 0
    for url in urls:
        code = process_one(url, args, settings)
        if code != 0:
            exit_code = code
    return exit_code


def collect_urls(args: argparse.Namespace, settings: Settings) -> list[str]:
    urls: list[str] = []
    if args.url:
        urls.append(args.url.strip())
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
            return 0
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
            return 0

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
            _notify_success(mailer, settings, info, quality, package, None, None)
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
        if mailer:
            mailer.needs_review({"plugin_name": slug, "reason": exc.message, "log_path": str(log_path)})
        return 0
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


def _notify_success(mailer: Mailer, settings: Settings, info: PluginInfo, quality, package, listing, created) -> None:
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
            "processed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "dry_run": settings.dry_run,
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
                "retry": "認証番号が届いたら python app.py --test-base --otp で続きを実行",
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
