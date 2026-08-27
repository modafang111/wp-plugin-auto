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
Windows では次のバッチをダブルクリックしてもよい。

  setup.bat                     仮想環境・依存関係・Chromium
  test-mail.bat                 自分宛てにメール設定テスト
  test-deliver.bat              自分宛てにZIP付きお届けテスト
  deliver-orders-dry-run.bat    未対応注文の確認（送らない）。初回BASEログイン
  deliver-orders.bat            売れたZIPを購入者へ送る（タスク スケジューラ用）
  register-draft.bat URL        翻訳して非公開登録

コマンド例:

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

お届けメールのテスト（NOTIFY_EMAIL へZIP付き。購入者には送らない）:

     python app.py --test-deliver

売れた通常商品を購入者へメールする（1回）:

     python app.py --deliver-orders

対象確認だけ（送らない）:

     python app.py --deliver-orders --dry-run

5分おきに監視:

     python app.py --deliver-orders --watch

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

公式日本語 language pack が公開されているプラグインはスキップする。
日本語サイトでは WordPress がパックを自動ダウンロードするため、
プラグインを入れるだけで日本語UIになる（自作ファイルの効果が見えない）。

パックが無くても SKIP_IF_JA_PERCENT=95 以上なら自動登録しない。
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

商品画像の文字は英語のみ（Japanese Localization）。日本語フォントが無い環境で
「日本語化」が □□□□ になるのを避けるため。


7.1 .po / .mo の確認
--------------------
.po は人が読める翻訳テキスト。.mo は WordPress が読むバイナリです。

場所（Classic Editor の例）:

  work\classic-editor\1.7.0\translation\classic-editor-ja.po
  work\classic-editor\1.7.0\translation\classic-editor-ja.mo

販売ZIPの中にも同じファイルが入っています。

  output\classic-editor-1.7.0-ja.zip
    languages\classic-editor-ja.po
    languages\classic-editor-ja.mo

Windows での見方:

1) いちばん簡単: Poedit（無料）で .po を開く。
   左が英語（msgid）、右が日本語（msgstr）。保存すると .mo も更新できる。
2) .po だけ読む: メモ帳 / VS Code / サクラエディタ。UTF-8。
3) .mo を直接メモ帳で開くと文字化けする。Poedit か次で中身を出す。

     python -c "import polib; p=polib.pofile(r'work\classic-editor\1.7.0\translation\classic-editor-ja.po'); print(len(p), 'strings'); print(p[5].msgid); print(p[5].msgstr)"

WordPress に入れて確認する場合:

  wp-content\languages\plugins\classic-editor-ja.po
  wp-content\languages\plugins\classic-editor-ja.mo

サイト言語を日本語にして、該当プラグインの画面を見る。


7.2 売れたあとの自動お届け
--------------------------
いちばん確実なのは BASE 公式の無料 App「デジタルコンテンツ販売」です。

  https://apps.thebase.com/detail/20

Apps からインストールすると、商品登録で「デジタルコンテンツ」が選べます。
ZIP を商品に載せておくと、購入完了画面と購入者メールにダウンロードボタンが付きます。
回数は3回、期限は72時間（注文詳細からリセット可）。ファイルは 1KB〜1GB。
決済はクレジットカードと PAY ID あと払いのみ。複数のデジタル商品の同時購入は不可。

このショップに App が入っていないあいだは、通常商品のまま売ります。
自動お届けはクラウドではなく、ショップオーナーの Windows PC で動かします。
（BASE管理画面のログイン状態は PC ごとに保存されるため）

PCでの準備:

  1) 最新コードを取る（このリポジトリの作業ブランチ）

       git fetch origin
       git checkout cursor/base-wp-ja-auto-8d86
       git pull origin cursor/base-wp-ja-auto-8d86

  2) 依存関係

       .venv\Scripts\activate
       python -m pip install -r requirements.txt
       python -m playwright install chromium

  3) .env
       CONTINUE_IF_ALREADY_TRANSLATED=false
       PLAYWRIGHT_HEADLESS=true
       REQUIRE_EMAIL=true  （本番）
       SMTP と NOTIFY_EMAIL は --test-mail が通った設定のまま

  4) このPCで BASE に1回ログインする（必須）
       クラウドで保存した data\playwright\base_state.json は使えない。
       初回だけ PLAYWRIGHT_HEADLESS=false にして画面を出す。

       deliver-orders-dry-run.bat

       メール認証番号を求められたら:

       deliver-orders-dry-run.bat --otp 123456

       成功後は data\playwright\base_state.json がこのPCにできる。
       PLAYWRIGHT_HEADLESS=true に戻す。

  5) お届けメールのテスト（自分宛て。購入者には送らない）

       test-deliver.bat

Windows タスク スケジューラ（推奨。--watch は使わない）:

  --watch は終了しない常駐なので、タスク スケジューラ向きではない。
  5分おきに deliver-orders.bat を1回ずつ実行する。

  1) タスク スケジューラを開く
  2) 「基本タスクの作成」
  3) 名前: base-wp-ja-auto-deliver
  4) トリガー: 毎日 → 繰り返し間隔 5分、期間 無期限
     （詳細で「1日中繰り返す / 5分」でも可）
  5) 操作: プログラムの開始
       プログラム: D:\dev\base-wp-ja-auto\deliver-orders.bat
       開始:       D:\dev\base-wp-ja-auto
     （フォルダが違う場合は、そのフォルダの deliver-orders.bat を指定）
  6) 「ユーザーがログオンしているときのみ実行する」
     Playwright がブラウザを使うため、ログオンなし実行は失敗しやすい。
  7) 「バッテリで開始する」「スリープ解除して実行」を必要ならオン。
     PCがスリープしたままだと動かない。

コマンド一発で作る場合:

     schtasks /create /tn "base-wp-ja-auto-deliver" /sc minute /mo 5 /f /tr "D:\dev\base-wp-ja-auto\deliver-orders.bat"

作成後は「タスクの実行」で1回試し、logs\ の最新ファイルを見る。
「未対応の注文: 0 件」なら正常（売れていないだけ）。

手動確認:

     deliver-orders-dry-run.bat
     deliver-orders.bat

対象ZIPの対応づけ:

  1) このプログラムが登録した商品は jobs.sqlite3 の base_product_id
  2) 既存商品は data\delivery_map.json
     例は data\templates\delivery_map.example.json

購入者のメールアドレスはログに出しません。対応済への更新に失敗しても、
同じ注文へZIPを再送しないよう deliveries テーブルで記録します。
注文のキャンセルはしません。テンプレート商品も変更しません。

公式デジタルコンテンツとして売っている注文は、二重送信しないようスキップします。


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
  DELIVERY_MARK_DISPATCHED
  DELIVERY_POLL_SECONDS
