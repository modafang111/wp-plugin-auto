import tempfile
import unittest
import zipfile
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
