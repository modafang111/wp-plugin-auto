"""Build the sales ZIP, README, and a simple product image. Original plugin stays separate."""

from __future__ import annotations

import logging
import zipfile
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from config import Settings
from src.utils import safe_filename, write_json
from src.wordpress import PluginInfo


README_TEMPLATE = """{plugin_name} 日本語化ファイル
================================

本ZIPに含まれているのは「日本語翻訳ファイル」です。
対象の WordPress プラグイン本体ではありません。
プラグイン本体は WordPress 公式ディレクトリから別途入手してください。

対象プラグイン名:
  {plugin_name}

対象バージョン:
  {version}

作成日:
  {created}

公式プラグインURL:
  {official_url}

Text Domain:
  {text_domain}

日本語化対象:
  プラグインが表示する管理画面・フロントの翻訳対象文字列

導入方法
--------
1. WordPress 公式ディレクトリから「{plugin_name}」バージョン {version} をインストールします。
2. 本ZIPを展開します。
3. 次のいずれかの場所へ翻訳ファイルを配置します。

   (A) プラグイン同梱 languages フォルダ
       wp-content/plugins/{slug}/languages/{po_name}
       wp-content/plugins/{slug}/languages/{mo_name}

   (B) WordPress 共通 languages フォルダ（推奨）
       wp-content/languages/plugins/{po_name}
       wp-content/languages/plugins/{mo_name}

4. WordPress 管理画面の「設定 → 一般」でサイト言語を「日本語」にします。
5. プラグインを有効化し、管理画面の表示を確認します。

注意事項
--------
- 本商品は日本語化ファイルです。プラグインの機能そのものを改変するものではありません。
- オリジナルプラグインの著作権・ライセンスは原作者に帰属します。
- プラグインが更新されると、新しい文字列が未翻訳になる場合があります。
- WordPress.org およびプラグイン作者とは関係のない、第三者による翻訳ファイルです。
- 本ファイルを配置しても、有料アドオンや外部サービス契約は含まれません。

ライセンス情報（元プラグイン）:
  {license}
"""


class PackageBuilder:
    def __init__(self, settings: Settings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger

    def build(
        self,
        info: PluginInfo,
        text_domain: str,
        translation_dir: Path,
        work_dir: Path,
        plugin_root: Path,
        license_name: str,
    ) -> dict:
        created = datetime.now().strftime("%Y-%m-%d")
        po_name = f"{text_domain}-ja.po"
        mo_name = f"{text_domain}-ja.mo"
        readme = README_TEMPLATE.format(
            plugin_name=info.name,
            version=info.version,
            created=created,
            official_url=info.official_url,
            text_domain=text_domain,
            slug=info.slug,
            po_name=po_name,
            mo_name=mo_name,
            license=license_name or "(プラグインヘッダ / readme を参照)",
        )
        staging = work_dir / "sales_staging"
        if staging.exists():
            for path in staging.rglob("*"):
                if path.is_file():
                    path.unlink()
        languages = staging / "languages"
        languages.mkdir(parents=True, exist_ok=True)
        copied = []
        for src in translation_dir.iterdir():
            if src.suffix.lower() in {".po", ".mo", ".json"} and src.name != "translations.json":
                dest = languages / src.name
                dest.write_bytes(src.read_bytes())
                copied.append(str(dest.relative_to(staging)))
        (staging / "README.txt").write_text(readme, encoding="utf-8")
        meta = {
            "plugin_name": info.name,
            "plugin_slug": info.slug,
            "plugin_version": info.version,
            "official_url": info.official_url,
            "text_domain": text_domain,
            "created": created,
            "sale_package_mode": self.settings.sale_package_mode,
            "includes_plugin": self.settings.sale_package_mode == "plugin_and_translation",
        }
        write_json(staging / "meta.json", meta)

        if self.settings.sale_package_mode == "plugin_and_translation":
            plugin_dest = staging / "plugin" / plugin_root.name
            self._copy_tree(plugin_root, plugin_dest)

        zip_name = safe_filename(f"{info.slug}-{info.version}-ja.zip")
        output_zip = self.settings.output_dir / zip_name
        self.settings.output_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in staging.rglob("*"):
                if path.is_file():
                    zf.write(path, path.relative_to(staging).as_posix())
        self.logger.info("販売ZIP生成: %s", output_zip)
        image_path = self.generate_image(info, work_dir / "product_image.png")
        return {
            "output_zip": str(output_zip),
            "readme": readme,
            "files": copied + ["README.txt", "meta.json"],
            "image_path": str(image_path) if image_path else "",
            "created": created,
            "po_name": po_name,
            "mo_name": mo_name,
        }

    def generate_image(self, info: PluginInfo, dest: Path) -> Path | None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        width, height = 640, 640
        image = Image.new("RGB", (width, height), (22, 43, 72))
        draw = ImageDraw.Draw(image)
        draw.rectangle([24, 24, width - 24, height - 24], outline=(232, 196, 104), width=4)
        font_large = self._font(42)
        font_mid = self._font(28)
        font_small = self._font(22)
        name = info.name if len(info.name) < 42 else info.name[:39] + "..."
        self._centered(draw, name, 180, font_large, (255, 255, 255), width)
        self._centered(draw, "日本語化", 300, font_mid, (232, 196, 104), width)
        self._centered(draw, "WordPress Plugin", 380, font_small, (200, 214, 230), width)
        self._centered(draw, f"v{info.version}", 460, font_small, (170, 184, 200), width)
        image.save(dest, "PNG")
        self.logger.info("商品画像生成: %s", dest)
        return dest

    def _font(self, size: int) -> ImageFont.ImageFont:
        candidates = [
            "C:/Windows/Fonts/meiryo.ttc",
            "C:/Windows/Fonts/msgothic.ttc",
            "C:/Windows/Fonts/YuGothM.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        ]
        for path in candidates:
            if Path(path).exists():
                try:
                    return ImageFont.truetype(path, size=size)
                except OSError:
                    continue
        return ImageFont.load_default()

    def _centered(self, draw: ImageDraw.ImageDraw, text: str, y: int, font, fill, width: int) -> None:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        draw.text(((width - tw) / 2, y), text, font=font, fill=fill)

    def _copy_tree(self, src: Path, dest: Path) -> None:
        dest.mkdir(parents=True, exist_ok=True)
        for path in src.rglob("*"):
            rel = path.relative_to(src)
            target = dest / rel
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(path.read_bytes())
