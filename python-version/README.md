# えいご道場

小中学生向けの英単語・短文（英日／日英）学習アプリ。IDとパスワードでログインし、回答はすべて記録され、管理者が正答率・間違えた問題・利用頻度を確認できます。AIは Gemini を使います。

- 設計書: `docs/design.md`　ER図: `docs/er_diagram.mermaid`　テスト結果: `docs/test_report.md`

## 1. いちばん簡単な始め方（1コマンド）
> Macでは `python` ではなく **`python3`** と入力します（仮想環境 `.venv` を有効にした後は `python` でも動きます）。詳しくは `docs/mac_guide.md`。

```bash
python setup.py
```
ライブラリのインストール、`.env` の作成、APIキーの登録（入力は画面に表示されません）、データベースと管理者の作成、練習用の問題1,600問の取り込み、Geminiへの接続チェックまで、質問に答えるだけで終わります。キーが無い場合は、何も入れずに Enter でAIなしの簡易モードで始められます（あとで再実行すればキーを登録できます）。

終わったら `python run.py` を実行し、http://127.0.0.1:5050 を開いて、ID `admin` でログインします。

<details><summary>手動で行う場合</summary>

```bash
pip install -r requirements.txt
cp .env.example .env            # .env は自動で読み込まれる（GitHubには上げない）
python manage.py init-db
python manage.py create-admin admin 'あなたの管理者パスワード(8文字以上)'
python manage.py sample-csv sample.csv
python manage.py import-questions sample.csv
python run.py
```
</details>

接続だけ確認したいときは `python tools/check_gemini.py`（`--write-env` を付けると、使えるモデル名を自動で .env に反映）。

## 2. Geminiにつなぐ（`python setup.py` が自動で行います。詳しい手順は `docs/setup_gemini_github.md`）
1. Google AI Studio（aistudio.google.com/apikey）でAPIキーを発行する。
2. `.env` に書く（または環境変数で設定する）。
```
AI_MODE=gemini
GEMINI_API_KEY=発行したキー
GEMINI_MODEL=gemini-flash-latest      # モデル名は変わることがあるので公式の一覧で確認
```
```bash
python run.py
```
- キーは**絶対に公開しない**（GitHubに上げない）。
- 無料枠は1分あたりの回数が少ないので、`AI_MAX_RPM`（既定10）を契約内容に合わせる。生徒が多い場合は有料枠を推奨。
- キーが無い／AIが落ちていても、**代替採点**で学習は止まらない（管理画面に回数が出る）。

## 3. 自分の問題データを入れる
CSV（UTF-8）の1行目に見出しを付ける。`管理者画面 > 問題データ` からも取り込める。
```
級・段,番号,英文,和文,英単語,日本語の単語,文法メモ
20級,1,I have a pen.,私はペンを持っています。,pen,ペン,a / an の使い分け
```
- 級・段は `20級`〜`1級`、`初段`、`2段`〜`20段`。番号は1〜40。
- **英単語は英文の中に、日本語の単語は和文の中に、そのままの形で含まれる**こと（空欄問題を作るため）。
- 1行でも不備があると、1問も取り込まれず、行番号つきで理由が出る。同じ級・番号は上書き。

## 4. 本番運用のめやす
```bash
pip install -r requirements.txt
gunicorn -w 1 --threads 8 -b 127.0.0.1:8000 --timeout 120 'app:create_app()'
```
- HTTPS（nginx / Caddy など）の後ろに置き、`COOKIE_SECURE=1` にする。nginx では `proxy_set_header Host $host;` を渡す。
- `data/app.db` を定期的にバックアップする（SQLiteファイルをコピーするだけ）。
- 管理者パスワードは長いものにする。生徒の表示名は本名にしない。

## 5. GitHubへ公開する
```bash
python tools/publish_github.py https://github.com/ユーザー名/eigo-dojo.git   # 空のPrivateリポジトリを先に作る
# または、GitHub CLI(gh)でログイン済みなら:
python tools/publish_github.py --create
```
`.env`・データベース・APIキーらしい文字列が含まれていないか自動で調べ、見つかれば公開を中止します。認証は `gh auth login`、または環境変数 `GITHUB_TOKEN`（Personal Access Token）を使います（トークンは保存されません）。

## 6. テスト
```bash
python -m unittest discover -s tests -t .      # API・AI連携・セットアップ・公開スクリプト（58件）
python -m tests.load_test 30 2                 # 30人が同時に2セットずつ解く負荷テスト
python -m tests.test_ui                        # ブラウザ操作テスト（Playwright + Chromium が必要）
```

## 7. ファイル構成
```
app/            バックエンド（engine.py=出題フロー, ai.py=Gemini連携, prompts.py=AIへの指示, stats.py=集計）
app/static/     画面（index.html, app.js, style.css）
setup.py        かんたんセットアップ
manage.py       管理コマンド（DB作成・管理者作成・問題取り込み・サンプル作成）
tools/          サンプル問題の生成、Gemini接続チェック、GitHub公開
tests/          テスト一式
docs/           設計書・ER図・テスト結果
```
