import tempfile
import unittest
import zipfile
from pathlib import Path

from config import Settings, load_settings
from src.base_admin import forbidden_control_name, is_login_page, is_protected_item_url, is_two_factor_page
from src.base_template import normalize_shop_fields
from src.exceptions import PipelineError
from src.plugin_analyzer import extract_php_strings
from src.utils import extract_plugin_slug, placeholder_tokens, safe_extract_zip


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


if __name__ == "__main__":
    unittest.main()
