import logging
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

from app import parse_args
from config import Settings, load_settings
from src.base_admin import BaseAdminClient, forbidden_control_name, is_login_page, is_protected_item_url, is_two_factor_page
from src.base_template import BaseTemplateService, normalize_shop_fields
from src.exceptions import PipelineError
from src.database import Database
from src.legacy_catalog import (
    LegacyItem,
    latest_zip_for_slug,
    load_legacy_items,
    slug_for_order,
    structured_product_detail,
)
from src.mailer import Mailer
from src.package_builder import IMAGE_KICKER, IMAGE_SUBLINE, PackageBuilder, ascii_overlay
from src.plugin_analyzer import TranslatableString, decide_already_translated, extract_php_strings
from src.translation_builder import TranslationBuilder
from src.plugin_discovery import discover_plugins, eligibility_reason, plugin_has_ja_pack
from src.order_delivery import (
    ensure_test_zip,
    is_actionable_delivery_failure,
    is_safe_sales_zip,
    parse_order_plans,
    resolve_sales_zip,
)
from src.utils import extract_plugin_slug, placeholder_tokens, redact_email, repair_placeholders, safe_extract_zip


class CoreTests(unittest.TestCase):
    def test_extract_plugin_slug_from_url(self) -> None:
        self.assertEqual(extract_plugin_slug("https://wordpress.org/plugins/contact-form-7/"), "contact-form-7")
        self.assertEqual(extract_plugin_slug("hello-dolly"), "hello-dolly")

    def test_reject_non_official_url(self) -> None:
        with self.assertRaises(PipelineError):
            extract_plugin_slug("https://example.com/plugins/foo/")

    def test_php_i18n_extraction_does_not_need_regex_rewrite(self) -> None:
        source = r'''<?php
        echo __("Save", "demo");
        esc_html_e('Delete %s', 'demo');
        _x("Settings", "menu", "demo");
        _n("Item", "Items", $n, "demo");
        $ignored = $not_a_function;
        '''
        items = extract_php_strings(source, "demo.php")
        msgids = {i.msgid for i in items}
        self.assertIn("Save", msgids)
        self.assertIn("Delete %s", msgids)
        self.assertIn("Settings", msgids)
        self.assertIn("Item", msgids)
        contexts = {i.msgid: i.msgctxt for i in items}
        self.assertEqual(contexts["Settings"], "menu")

    def test_placeholders(self) -> None:
        tokens = placeholder_tokens("Hello %1$s and {name} and %s")
        self.assertIn("%1$s", tokens)
        self.assertIn("{name}", tokens)
        self.assertIn("%s", tokens)

    def test_repair_percent_injection_placeholder(self) -> None:
        src = (
            "With Zip AI by your side, you can create beautiful, 100%s custom web pages "
            "without the need for any design or coding skills."
        )
        dst = "Zip AI を使えば、デザインやコーディングのスキルがなくても、100%カスタムの美しいウェブページを作成できます。"
        repaired = repair_placeholders(src, dst)
        self.assertIn("100%s", repaired)
        self.assertEqual(sorted(placeholder_tokens(src)), sorted(placeholder_tokens(repaired)))
        self.assertEqual(
            repaired,
            "Zip AI を使えば、デザインやコーディングのスキルがなくても、100%sカスタムの美しいウェブページを作成できます。",
        )

    def test_repair_fullwidth_percent_injection(self) -> None:
        src = "Save 100%s off"
        dst = "100％オフ"
        self.assertEqual(repair_placeholders(src, dst), "100%sオフ")

    def test_repair_percent_word_and_escaped(self) -> None:
        self.assertEqual(repair_placeholders("Save 50%s", "50パーセント"), "50%s")
        self.assertEqual(repair_placeholders("Save 50%s", "50%%"), "50%s")
        self.assertEqual(repair_placeholders("Save 50%s", "50 %"), "50%s")

    def test_repair_numbered_percent_placeholder(self) -> None:
        src = "Discount 100%1$s today"
        dst = "本日100％引き"
        self.assertEqual(repair_placeholders(src, dst), "本日100%1$s引き")

    def test_repair_does_not_touch_unrelated_percent(self) -> None:
        src = "Hello %s"
        dst = "こんにちは %s（50%オフ）"
        self.assertEqual(repair_placeholders(src, dst), dst)

    def test_repair_leaves_matching_placeholders(self) -> None:
        src = "Delete %s"
        dst = "%sを削除"
        self.assertEqual(repair_placeholders(src, dst), dst)

    def test_quality_check_repairs_percent_injection(self) -> None:
        src = (
            "With Zip AI by your side, you can create beautiful, 100%s custom web pages "
            "without the need for any design or coding skills."
        )
        dst = "Zip AI を使えば、デザインやコーディングのスキルがなくても、100%カスタムの美しいウェブページを作成できます。"
        items = [TranslatableString(msgid=src)]
        translations = [dst]
        report = TranslationBuilder(logging.getLogger("test")).quality_check(items, translations)
        self.assertTrue(report.ok, report.errors)
        self.assertIn("100%s", translations[0])
        self.assertTrue(any(w.startswith("プレースホルダーを自動修復") for w in report.warnings))

    def test_write_catalog_saves_plural_entries(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            dest = Path(raw) / "translation"
            plugin_root = Path(raw) / "plugin"
            plugin_root.mkdir()
            info = SimpleNamespace(
                name="Demo",
                version="1.0",
                official_url="https://wordpress.org/plugins/demo/",
            )
            items = [
                TranslatableString(msgid="Save"),
                TranslatableString(msgid="%s year", msgid_plural="%s years"),
            ]
            translations = ["保存", "%s年"]
            catalog = TranslationBuilder(logging.getLogger("test")).write_catalog(
                info, "demo", items, translations, dest, plugin_root
            )
            self.assertTrue(Path(catalog["po_path"]).exists())
            self.assertTrue(Path(catalog["mo_path"]).exists())
            po_text = Path(catalog["po_path"]).read_text(encoding="utf-8")
            self.assertIn('msgid_plural "%s years"', po_text)
            self.assertIn('msgstr[0] "%s年"', po_text)

    def test_zip_slip_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp_path = Path(raw)
            zip_path = tmp_path / "bad.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("../evil.php", "<?php")
            dest = tmp_path / "out"
            dest.mkdir()
            with self.assertRaises(PipelineError):
                safe_extract_zip(zip_path, dest, max_files=10, max_uncompressed=10000)

    def test_swapped_shop_fields_are_normalized(self) -> None:
        settings = load_settings(overrides={
            "BASE_TEMPLATE_PRODUCT_URL": "https://123789.theshop.jp/",
            "BASE_TEMPLATE_PRODUCT_ID": "https://123789.theshop.jp/items/55749997",
            "SHOP_PUBLIC_BASE_URL": "55749997",
        })
        fixed = normalize_shop_fields(settings)
        self.assertEqual(fixed["item_id"], "55749997")
        self.assertEqual(fixed["shop_url"], "https://123789.theshop.jp")
        self.assertEqual(fixed["product_url"], "https://123789.theshop.jp/items/55749997")
        self.assertEqual(settings.base_template_product_id, "55749997")

    def test_two_factor_page_detection(self) -> None:
        self.assertTrue(
            is_two_factor_page(
                "https://admin.thebase.com/users/verify_two_factor_auth_via_mail?url=",
                "ログイン 認証番号入力 | BASE",
            )
        )
        self.assertFalse(is_two_factor_page("https://admin.thebase.com/users/login", "ログイン | BASE"))
        self.assertTrue(is_login_page("https://admin.thebase.com/users/login"))
        self.assertFalse(is_login_page("https://admin.thebase.com/users/verify_two_factor_auth_via_mail"))

    def test_template_item_is_protected(self) -> None:
        self.assertTrue(is_protected_item_url("https://admin.thebase.com/shop_admin/items/55749997", "55749997"))
        self.assertTrue(is_protected_item_url("https://admin.thebase.com/shop_admin/items/edit/55749997", "55749997"))
        self.assertFalse(is_protected_item_url("https://admin.thebase.com/shop_admin/items/edit/123", "55749997"))
        self.assertFalse(is_protected_item_url("https://admin.thebase.com/shop_admin/items/add", "55749997"))
        self.assertTrue(forbidden_control_name("削除する"))
        self.assertFalse(forbidden_control_name("登録する"))

    def test_official_language_pack_counts_as_already_translated(self) -> None:
        already, reason = decide_already_translated(
            official_ja_percent=83,
            has_official_ja_pack=True,
            skip_if_ja_percent=95,
        )
        self.assertTrue(already)
        self.assertIn("language pack", reason)
        self.assertIn("83%", reason)

        already, reason = decide_already_translated(
            official_ja_percent=0,
            has_official_ja_pack=False,
            skip_if_ja_percent=95,
        )
        self.assertFalse(already)
        self.assertEqual(reason, "")

        already, reason = decide_already_translated(
            official_ja_percent=96,
            has_official_ja_pack=False,
            skip_if_ja_percent=95,
        )
        self.assertTrue(already)
        self.assertIn("96%", reason)

    def test_product_image_overlay_is_english_ascii(self) -> None:
        self.assertTrue(IMAGE_KICKER.isascii())
        self.assertTrue(IMAGE_SUBLINE.isascii())
        self.assertEqual(ascii_overlay("Classic Editor"), "Classic Editor")
        self.assertEqual(ascii_overlay("日本語化"), "")
        settings = load_settings()
        builder = PackageBuilder(settings, logging.getLogger("test"))
        with tempfile.TemporaryDirectory() as raw:
            dest = Path(raw) / "product_image.png"
            info = SimpleNamespace(name="Classic Editor", version="1.7.0", slug="classic-editor")
            path = builder.generate_image(info, dest)
            self.assertIsNotNone(path)
            self.assertTrue(path.exists())
            self.assertGreater(path.stat().st_size, 1000)

    def test_redact_email(self) -> None:
        self.assertEqual(redact_email("buyer@example.com"), "b***@example.com")
        self.assertEqual(redact_email(""), "")

    def test_sales_zip_must_be_ja_package_inside_project(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            output = root / "output"
            output.mkdir()
            good = output / "header-footer-code-manager-1.1.46-ja.zip"
            with zipfile.ZipFile(good, "w") as zf:
                zf.writestr("languages/plugin-ja.po", "x" * 2048)
            self.assertEqual(is_safe_sales_zip(good, root, 20 * 1024 * 1024), "")
            original = output / "original.zip"
            original.write_bytes(good.read_bytes())
            self.assertIn("販売用ZIP", is_safe_sales_zip(original, root, 20 * 1024 * 1024))
            dummy = ensure_test_zip(output / "empty")
            self.assertTrue(dummy.name.endswith("-ja.zip"))
            self.assertEqual(is_safe_sales_zip(dummy, root, 20 * 1024 * 1024), "")

    def test_resolve_sales_zip_from_job_and_title(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            output = root / "output"
            output.mkdir()
            zip_path = output / "header-footer-code-manager-1.1.46-ja.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("languages/plugin-ja.po", "x" * 2048)
            jobs = [{"base_product_id": "155592749", "output_zip": str(zip_path), "plugin_slug": "header-footer-code-manager", "plugin_version": "1.1.46"}]
            found = resolve_sales_zip(
                item_id="155592749",
                title="Header Footer Code Managerの日本語化ファイル",
                identifier="header-footer-code-manager-1.1.46",
                jobs=jobs,
                output_dir=output,
                delivery_map={},
                root=root,
            )
            self.assertEqual(found, zip_path)
            found_title = resolve_sales_zip(
                item_id="999",
                title="Header Footer Code Managerの日本語化ファイル",
                identifier="",
                jobs=[],
                output_dir=output,
                delivery_map={},
                root=root,
            )
            self.assertEqual(found_title, zip_path)

    def test_skip_cancelled_and_already_sent_orders(self) -> None:
        header = {
            "unique_key": "ABC123",
            "buyer": {"mail_address": "buyer@example.com", "last_name": "山田", "first_name": "太郎"},
            "orders": [
                {"id": "1", "item_id": "10", "name": "Aの日本語化ファイル", "status": "cancelled"},
                {"id": "2", "item_id": "11", "name": "Bの日本語化ファイル", "status": "ordered"},
            ],
        }
        plans = parse_order_plans(
            header,
            jobs=[],
            output_dir=Path("."),
            delivery_map={},
            root=Path("."),
            already_sent={("ABC123", "2")},
        )
        self.assertIn("cancelled", plans[0].skip_reason)
        self.assertIn("既に送信済み", plans[1].skip_reason)

    def test_register_flag_forces_public_listing(self) -> None:
        args = parse_args(["--register", "https://wordpress.org/plugins/hello-dolly/"])
        self.assertTrue(args.register)
        self.assertFalse(args.register_draft)
        public = load_settings(overrides={"BASE_PUBLISH_MODE": "public", "DRY_RUN": "false"})
        draft = load_settings(overrides={"BASE_PUBLISH_MODE": "draft", "DRY_RUN": "false"})
        self.assertEqual(public.visible_flag, 1)
        self.assertEqual(public.base_publish_mode, "public")
        self.assertEqual(draft.visible_flag, 0)

    def test_missing_zip_is_actionable_sale_failure(self) -> None:
        header = {
            "unique_key": "XYZ789",
            "buyer": {"mail_address": "buyer@example.com", "last_name": "山田", "first_name": "太郎"},
            "orders": [
                {"id": "9", "item_id": "99", "name": "Cの日本語化ファイル", "status": "ordered"},
            ],
        }
        plans = parse_order_plans(
            header,
            jobs=[],
            output_dir=Path("."),
            delivery_map={},
            root=Path("."),
            already_sent=set(),
        )
        self.assertIn("日本語化ZIP", plans[0].skip_reason)
        self.assertTrue(is_actionable_delivery_failure(plans[0].skip_reason))
        self.assertTrue(is_actionable_delivery_failure("購入者メールアドレスがありません。"))
        self.assertFalse(is_actionable_delivery_failure("この注文は既に送信済みです。"))
        self.assertFalse(is_actionable_delivery_failure("公式デジタルコンテンツのため BASE 側でダウンロード案内されます。"))

    def test_discover_skips_official_ja_pack_and_picks_next(self) -> None:
        packed = {
            "name": "Contact Form 7",
            "slug": "contact-form-7",
            "version": "6.1.7",
            "download_link": "https://downloads.wordpress.org/plugin/contact-form-7.6.1.7.zip",
            "active_installs": 10000000,
            "language_packs": [{"language": "ja", "package": "https://downloads.wordpress.org/translation/plugin/contact-form-7/6.1.7/ja.zip"}],
        }
        open_plugin = {
            "name": "White Label CMS",
            "slug": "white-label-cms",
            "version": "2.2.9",
            "download_link": "https://downloads.wordpress.org/plugin/white-label-cms.2.2.9.zip",
            "active_installs": 40000,
            "language_packs": [{"language": "en_GB"}],
        }
        next_plugin = {
            "name": "Temporary Login Without Password",
            "slug": "temporary-login-without-password",
            "version": "1.9.5",
            "download_link": "https://downloads.wordpress.org/plugin/temporary-login-without-password.1.9.5.zip",
            "active_installs": 20000,
            "language_packs": [],
        }
        self.assertTrue(plugin_has_ja_pack(packed))
        self.assertFalse(plugin_has_ja_pack(open_plugin))
        self.assertIn("language pack", eligibility_reason(packed, min_installs=1000, skip_slugs=set()))
        self.assertEqual(eligibility_reason(open_plugin, min_installs=1000, skip_slugs=set()), "")
        self.assertIn("対象外", eligibility_reason(open_plugin, min_installs=1000, skip_slugs={"white-label-cms"}))

        class FakeWP:
            def query_plugins(self, **_kwargs):
                return [packed, open_plugin, next_plugin], {"page": 1, "pages": 1}

            def glotpress_ja_percent(self, _slug):
                return 0

            def japanese_language_pack(self, *_args, **_kwargs):
                return None

        with tempfile.TemporaryDirectory() as raw:
            db = Database(Path(raw) / "jobs.sqlite3")
            settings = load_settings(
                overrides={
                    "DISCOVER_BROWSE": "popular",
                    "DISCOVER_MAX_PAGES": "1",
                    "DISCOVER_MIN_INSTALLS": "1000",
                    "DISCOVER_SKIP_SLUGS": "hello-dolly,akismet",
                }
            )
            settings.discover_max_pages = 1
            found = discover_plugins(
                FakeWP(),
                db,
                settings,
                logging.getLogger("test"),
                limit=1,
                check_glotpress=False,
                check_translation_api=False,
            )
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].slug, "white-label-cms")
            self.assertTrue(found[0].url.endswith("/white-label-cms/"))
            db.enqueue_discovered(found[0].slug, version=found[0].version, name=found[0].name, url=found[0].url)
            next_found = discover_plugins(
                FakeWP(),
                db,
                settings,
                logging.getLogger("test"),
                limit=1,
                check_glotpress=False,
                check_translation_api=False,
            )
            self.assertEqual(len(next_found), 1)
            self.assertEqual(next_found[0].slug, "temporary-login-without-password")
            db.upsert_job("white-label-cms", "2.2.9", status="completed")
            again = discover_plugins(
                FakeWP(),
                db,
                settings,
                logging.getLogger("test"),
                limit=2,
                check_glotpress=False,
                check_translation_api=False,
            )
            slugs = {item.slug for item in again}
            self.assertNotIn("white-label-cms", slugs)
            db.close()

        args = parse_args(["--register", "--discover"])
        self.assertTrue(args.register)
        self.assertTrue(args.discover)

    def test_legacy_catalog_maps_past_items_and_structured_copy(self) -> None:
        settings = load_settings()
        items = load_legacy_items(settings)
        by_id = {item.item_id: item for item in items}
        self.assertIn("55749997", by_id)
        self.assertTrue(by_id["55749997"].protected)
        self.assertEqual(by_id["55749997"].slug, "wp-members")
        self.assertEqual(by_id["55749950"].slug, "duplicate-post")
        self.assertEqual(
            slug_for_order(item_id="55749886", title="Groupsの日本語化ファイル", items=items),
            "groups",
        )
        self.assertEqual(
            slug_for_order(item_id="999", title="adminimizeの日本語化ファイル", items=items),
            "adminimize",
        )
        detail = structured_product_detail(
            plugin_name="Duplicate Post",
            slug="duplicate-post",
            version="4.5",
            official_url="https://wordpress.org/plugins/duplicate-post/",
            short_description="投稿を複製します。",
            created="2026-08-28",
            settings=settings,
        )
        self.assertIn("■商品について", detail)
        self.assertIn("■導入方法", detail)
        self.assertIn("■注意事項", detail)
        self.assertIn("Duplicate Post", detail)
        self.assertNotIn("オンラインショッピング体験", detail)

        info = SimpleNamespace(
            name="Duplicate Post",
            slug="duplicate-post",
            version="4.5",
            official_url="https://wordpress.org/plugins/duplicate-post/",
            short_description="投稿を複製します。",
            description="投稿を複製します。",
        )
        listing_detail = BaseTemplateService(settings, logging.getLogger("test")).render_description(
            info, SimpleNamespace(), {"created": "2026-08-28", "po_name": "duplicate-post-ja.po", "mo_name": "duplicate-post-ja.mo"}
        )
        self.assertIn("■導入方法", listing_detail)
        self.assertNotIn("オンラインショッピング体験", listing_detail)

        admin = BaseAdminClient(settings, logging.getLogger("test"))
        settings.base_template_product_id = "55749997"
        with self.assertRaises(PipelineError):
            admin.assert_item_copy_allowed("55749997")
        with self.assertRaises(PipelineError):
            admin.assert_item_copy_allowed("55749950", {"protected": True})
        admin.assert_item_copy_allowed("55749950")

        args = parse_args(["--sync-legacy", "--rewrite-pages", "--build-zips"])
        self.assertTrue(args.sync_legacy)
        self.assertTrue(args.rewrite_pages)
        self.assertTrue(args.build_zips)
        self.assertTrue(parse_args(["--test-deliver"]).test_deliver)

    def test_legacy_catalog_zip_is_used_for_past_orders(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            output = root / "output"
            output.mkdir()
            zip_path = output / "duplicate-post-4.5-ja.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("languages/duplicate-post-ja.po", "x" * 2048)
            self.assertEqual(latest_zip_for_slug(output, "duplicate-post"), zip_path)
            items = [
                LegacyItem(
                    item_id="55749950",
                    title="Duplicate Postの日本語化ファイル",
                    slug="duplicate-post",
                )
            ]
            header = {
                "unique_key": "LEGACY1",
                "buyer": {"mail_address": "buyer@example.com", "last_name": "山田", "first_name": "太郎"},
                "orders": [
                    {"id": "1", "item_id": "55749950", "name": "Duplicate Postの日本語化ファイル", "status": "ordered"},
                ],
            }
            plans = parse_order_plans(
                header,
                jobs=[],
                output_dir=output,
                delivery_map={},
                root=root,
                already_sent=set(),
                catalog_slugs={"55749950": "duplicate-post"},
                legacy_items=items,
            )
            self.assertEqual(plans[0].zip_path, zip_path)
            self.assertEqual(plans[0].skip_reason, "")

    def test_zip_only_success_mail_is_not_a_base_registration(self) -> None:
        settings = load_settings()
        captured: dict[str, str] = {}

        class CaptureMailer(Mailer):
            def send(self, subject, body, **_kwargs):
                captured["subject"] = subject
                captured["body"] = body

        CaptureMailer(settings, logging.getLogger("test")).success(
            {
                "zip_only": True,
                "plugin_name": "GiveWP",
                "plugin_version": "4.16.7.2",
                "output_zip": "output/give-4.16.7.2-ja.zip",
            }
        )
        self.assertIn("日本語化ZIP作成完了", captured["subject"])
        self.assertNotIn("BASE商品登録完了", captured["subject"])
        self.assertIn("新規登録はしていません", captured["body"])
        self.assertNotIn("DRY_RUN", captured["body"])


if __name__ == "__main__":
    unittest.main()
