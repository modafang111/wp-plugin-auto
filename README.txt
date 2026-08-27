base-wp-ja-auto
===============

WordPress.org 公式ディレクトリの無料プラグインを対象に、

  URL指定
    → 公式Plugin APIで情報取得
    → 最新ZIP取得（実行はしない）
    → 翻訳対象抽出
    → 日本語翻訳
    → 品質チェック
    → .po / .mo 生成
    → 販売用ZIP生成
    → 既存BASE商品をテンプレートにした商品情報生成
    → BASE登録（初期は DRY_RUN）
    → メール通知

までを自動化する Windows 向け Python プログラムです。

想定配置場所:

  D:\dev\base-wp-ja-auto


1. 初期セットアップ
-------------------
1) Python 3.10 以上をインストールする。
2) 本フォルダへ移動する。

     cd /d D:\dev\base-wp-ja-auto

3) 仮想環境（推奨）

     python -m venv .venv
     .venv\Scripts\activate
     python -m pip install -r requirements.txt

4) 環境変数ファイルを作る。

     copy .env.example .env

5) .env を編集する。パスワード・APIキーはここだけに書く。
   ソースコードへ直接書いてはいけない。.env は Git 管理外。

   BASE は https://thebase.com/ から進める。最初に入れるのは次だけ。

     BASE_LOGIN_EMAIL=（管理画面のメールアドレス）
     BASE_LOGIN_PASSWORD=（管理画面のパスワード）
     BASE_TEMPLATE_PRODUCT_URL=（既存商品の公開ページURL）
     BASE_TEMPLATE_PRODUCT_ID=（わかる場合。URLの /items/ の後ろの数字でも可）
     SHOP_PUBLIC_BASE_URL=（例: https://yourshop.base.shop）

   client_id / client_secret / access_token は thebase.com のショップ画面では
   発行されない。未記入のままでよい。

6) 最初は必ず次のままにする。

     DRY_RUN=true
     BASE_PUBLISH_MODE=draft

7) 公式API（任意）を使う場合だけ、https://developers.thebase.com/ でアプリ申請し、
   次の scope を付与する。

     read_users read_items write_items

   認可は次のコマンド。

     python app.py --base-auth

   表示されたURLをブラウザで開き、リダイレクト先の code= を貼り付ける。
   トークンは data\base_tokens.json に保存される（Git 対象外）。
   APIの通信先は公式仕様どおり https://api.thebase.in/ のまま。


2. 実行方法
-----------
1件:

     python app.py "https://wordpress.org/plugins/contact-form-7/"

DRY RUN（BASEへ登録しない。初期の安全な確認用）:

     python app.py "https://wordpress.org/plugins/hello-dolly/" --dry-run

複数件（input\plugins.txt に1行1URL）:

     python app.py --input input\plugins.txt

URL省略時、input\plugins.txt があればそれを使う。

途中から再開（翻訳APIをやり直さない）:

     python app.py --resume "https://wordpress.org/plugins/hello-dolly/"

翻訳と販売ZIPのみ:

     python app.py --translate-only "https://wordpress.org/plugins/hello-dolly/"

BASE登録のみ（翻訳成果物がある前提）:

     python app.py --base-only "https://wordpress.org/plugins/hello-dolly/"

公式日本語が十分でも続行:

     python app.py --force "https://wordpress.org/plugins/hello-dolly/"

テンプレート商品を再取得:

     python app.py --fetch-template

メール設定の送信テスト:

     python app.py --test-mail

非公開で実登録（.env の DRY_RUN=true のまま、1件だけ下書き登録）:

     python app.py --register-draft "https://wordpress.org/plugins/classic-editor/"

非公開の接続テスト商品（Hello Dolly 固定）:

     python app.py --test-base

新しい環境からのログインでメール認証番号を求められた場合:

     python app.py --test-base --otp 123456

番号はログに書きません。一度ログインできたブラウザ状態は
data\playwright\base_state.json に保存され、次回は番号なしで進めます。
テンプレート商品の編集・削除はしません。


3. BASE（thebase.com）と公式APIの分担
------------------------------------
ショップの入口は https://thebase.com/ 。
管理画面ログインは https://admin.thebase.com/users/login 。
（2023年3月に thebase.in から thebase.com へ移行。旧URLはリダイレクトされる）

ショップオーナーが .env に書く本線:

  BASE_LOGIN_EMAIL
  BASE_LOGIN_PASSWORD
  BASE_TEMPLATE_PRODUCT_URL
  BASE_TEMPLATE_PRODUCT_ID
  SHOP_PUBLIC_BASE_URL

BASE Developers API は任意。thebase.com のログインだけでは client_id は出ない。
APIを使う場合のドキュメントは https://docs.thebase.in/api/
通信先は https://api.thebase.in/

APIで実施する（資格情報がある場合）:
  - テンプレート商品の参照 GET /1/items/detail/:item_id
  - 商品登録 POST /1/items/add（title, detail, price, stock, visible, identifier）
  - 画像登録 POST /1/items/add_image（公開URLのみ。ローカルファイル不可）
  - カテゴリ POST /1/item_categories/add
  - 登録確認 GET /1/items/detail/:item_id
  - 非公開/公開は visible=0/1（BASE_PUBLISH_MODE=draft|public）

API資格情報が無い場合:
  - ショップログイン（BASE_LOGIN_EMAIL / BASE_LOGIN_PASSWORD）で
    Playwright が管理画面から新規商品を登録する
  - 初期は BASE_PUBLISH_MODE=draft（非公開）
  - python app.py --test-base で非公開のテスト商品を1件だけ実登録できる

APIに存在しない / 使わない:
  - デジタルコンテンツのファイルアップロード
    （Apps「デジタルコンテンツ販売」の管理画面機能。APIエンドポイント無し）
  - 既存商品の削除 POST /1/items/delete は実装しない
  - テンプレート商品の編集・削除はしない（参照専用）
  - デジタルコンテンツ商品はAPIから編集できない
    （公式エラー: デジタルコンテンツの商品は編集できません）

Playwright:
  - ログイン先は BASE_ADMIN_URL（初期値 https://admin.thebase.com/users/login）
  - ログインセッション保存は data\playwright\base_state.json
  - CAPTCHA / 二段階認証 / パスキー確認 / 本人確認が出たら停止し
    「BASEで手動認証が必要です」とメールする。回避コードは持たない。
  - メール認証番号はユーザーが --otp で渡したときだけ入力する。
  - 実画面（2026-08時点）の新規登録は「+ 商品を登録」→ 通常商品
    （/shop_admin/items/add）。公開状態は「非公開」を選ぶ。
  - 「デジタルコンテンツ」がメニューに出ないショップでは ZIP は未添付のまま
    非公開商品だけ登録し、メールでその旨を知らせる。
  - 削除ボタンは押さない。

画像:
  - 公式APIは image_url（jpg/png/gif、4MB以内、推奨640x640）のみ。
  - 初期値 BASE_IMAGE_MODE=wordpress_icon は WordPress.org のアイコンURLを使う。
  - 生成画像は work 配下に保存し、必要なら手動で差し替える。
  - generated / template / skip も .env で切替可能。


4. 商品名と説明文
-----------------
新しい命名ルールは作らない。既存BASE商品を正解のテンプレートにする。

優先順位:
  1) BASE API で取得したテンプレート商品
  2) data\base_template.json のキャッシュ
  3) .env の PRODUCT_NAME_PATTERN / data\templates\product_description.txt

.env の例:

     BASE_TEMPLATE_PRODUCT_URL=https://example.base.shop/items/12345678
     BASE_TEMPLATE_PRODUCT_ID=12345678
     BASE_TEMPLATE_PLUGIN_NAME=既存商品が使っているプラグイン名
     BASE_TEMPLATE_PLUGIN_VERSION=既存商品の対象バージョン
     PRODUCT_NAME_PATTERN={plugin_name} WordPressプラグイン 日本語化ファイル
     PRODUCT_PRICE=500

テンプレート説明文がある場合、改行と構成は維持し、プラグイン名 / バージョン /
公式URL だけ差し替える。AIでセールスコピーを作り直さない。

価格・カテゴリ・在庫はテンプレート値を優先。未取得時は PRODUCT_PRICE 等。


5. 翻訳
-------
- 同梱 .pot があればそれを優先する。
- 無ければ WP-CLI `wp i18n make-pot`（インストール済みの場合）。
- それも無ければ PHP/JS を読み取り専用スキャンする。PHPは実行しないし書き換えない。
- 初期のAIは OpenAI。差し替え点は src\translator.py。
- TRANSLATION_PROVIDER=openai|offline_glossary
  本番は openai。OPENAI_API_KEY 必須。
  DRY_RUN でキーが無いときだけ offline_glossary に倒す。
- 翻訳キャッシュは SQLite（data\jobs.sqlite3）。再開時にAPIを再呼び出ししない。
- プレースホルダ・HTML・URL・固有名詞は保護する。


6. 日本語化済み判定
-------------------
translate.wordpress.org の GlotPress API と
api.wordpress.org/translations/plugins/1.0/ を使う。

SKIP_IF_JA_PERCENT=95 以上なら自動登録しない。
「既に十分日本語化されている可能性があります」をログとメールに残す。
CONTINUE_IF_ALREADY_TRANSLATED=true または --force で続行できる。


7. 販売ZIP
----------
内部では必ず分離保存する。

  work\<slug>\<version>\original\     元プラグイン
  work\<slug>\<version>\translation\  自作 po/mo
  output\<slug>-<version>-ja.zip      販売用

SALE_PACKAGE_MODE=translation_only（初期値）は翻訳ファイルと README.txt のみ。
plugin_and_translation は本体も含めるが、オリジナルと翻訳はフォルダを分ける。

output には登録プレビューも出す。

  output\<slug>-<version>-preview.json
  output\<slug>-<version>-preview.txt


8. ログと履歴
-------------
logs\ に実行ログ。スタックトレースも記録する。
パスワード・APIキーは出さない。

SQLite data\jobs.sqlite3:

  plugin_slug, plugin_name, plugin_version, wordpress_url, download_url,
  translation_date, output_zip, base_product_id, base_product_url,
  status, created_at, updated_at, error_message

同一 slug + version が completed / base_registered なら再登録しない。
新しい version は更新版として別レコードになる。


9. メール通知
-------------
NOTIFY_EMAIL 宛。

成功: 【BASE商品登録完了】プラグイン名 バージョン
エラー: 【BASE商品登録エラー】プラグイン名
要確認: 【要確認】BASE商品登録処理

SMTP未設定でも DRY_RUN は止めない（REQUIRE_EMAIL=false）。
本番運用では REQUIRE_EMAIL=true を推奨。


10. エラー時の復旧
------------------
1) logs\ の最新ログと、必要なら screenshots\ を確認する。
2) 翻訳後に BASE だけ失敗した場合:

     python app.py --resume --base-only "URL"

3) 品質エラーで止まった場合:
   work\<slug>\<version>\quality.json を見る。
   重大エラーがあるうちは実登録しない。

4) 「BASEで手動認証が必要です」:
   ブラウザで管理画面にログインし、CAPTCHA/2FAを自分で完了する。
   data\playwright\base_state.json を消してから再実行してもよい。

5) 対象外（有料・公式ZIPが無い等）:
   自動では処理しない。理由はログとメールに残る。

6) ZIPが巨大 / ZIP Slip:
   上限は .env の MAX_ZIP_BYTES 等。危険なエントリは展開しない。


11. 安全対策
------------
- ダウンロードした PHP は実行しない。データとして読むだけ。
- 外部URLは許可ホストのみ（wordpress.org / thebase.com / admin.thebase.com / api.thebase.in / api.openai.com 等）。
- 既存BASE商品を削除・変更する機能は無い。
- 予期しない状態では止める。
- 初期の公開状態は draft（非表示）。


12. 推奨する確認手順
--------------------
第1段階  WordPress URL から情報取得
第2段階  ZIP取得・展開
第3段階  翻訳文字列抽出
第4段階  AI翻訳
第5段階  .po/.mo 生成
第6段階  販売ZIP
第7段階  BASEテンプレート取得
第8段階  DRY RUN で登録予定内容を確認
第9段階  DRY_RUN=false で非公開のテスト商品を1件だけ登録
第10段階 人が BASE 管理画面で確認
第11段階 問題なければ通常運用（必要なら BASE_PUBLISH_MODE=public）


13. ディレクトリ
----------------
app.py
config.py
.env / .env.example
requirements.txt
README.txt
src\
input\plugins.txt
work\
output\
logs\
data\
screenshots\
backup\


14. 変更しやすい設定
--------------------
詳細は .env.example を見ること。よく変える項目:

  DRY_RUN
  BASE_PUBLISH_MODE
  PRODUCT_NAME_PATTERN
  PRODUCT_PRICE
  SALE_PACKAGE_MODE
  SKIP_IF_JA_PERCENT
  CONTINUE_IF_ALREADY_TRANSLATED
  TRANSLATION_PROVIDER
  OPENAI_MODEL
  BASE_IMAGE_MODE
  BASE_UPLOAD_DIGITAL_FILE
