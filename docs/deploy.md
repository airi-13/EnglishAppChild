# 公開の手順（GitHub → Cloudflare）

所要時間の目安は、30分ほどです。画面の名前は、Cloudflare側の更新で変わることがあります。見当たらないときは、近い名前の項目を探してください。

## 0. 用意するもの
- GitHubのアカウント
- Cloudflareのアカウント（無料。https://dash.cloudflare.com/sign-up）
- Google AI Studioで作った、**新しい**Gemini APIキー（aistudio.google.com/apikey）。過去にチャットなどに貼ったキーは、削除して作り直す。

## 1. GitHubに上げる（Private）
どちらかの方法で、**Privateのリポジトリ**（名前の例: `eigo-dojo`）に、このフォルダの中身を上げます。
- **ブラウザだけ**: github.com/new で、Privateのリポジトリを作る → 「uploading an existing file」に、展開したフォルダの中身をドラッグ＆ドロップ → 「Commit changes」。`python-version` フォルダは、使わなければ上げなくても動きます。
- **コマンド**: 空のPrivateリポジトリを作って、次を実行する（秘密情報が混ざっていないか、自動で調べます）。
```bash
python3 scripts/publish_github.py https://github.com/あなたのユーザー名/eigo-dojo.git
```

## 2. データベース（D1）を作る
1. Cloudflareのダッシュボードで、左のメニューの「Storage & Databases」→「D1 SQL Database」→「Create database」。
2. 名前を `eigo-dojo` にして作成する。場所（ロケーション）は、既定のままでよい。
3. 作成された画面に出る **Database ID**（`xxxxxxxx-xxxx-…` の形）をコピーする。
4. GitHubのリポジトリで `wrangler.toml` を開き（鉛筆アイコンで編集）、次の行の `REPLACE_WITH_YOUR_D1_DATABASE_ID` を、コピーしたIDに書き換えて、「Commit changes」。
```toml
database_id = "ここにコピーしたID"
```
※ テーブルは、アプリが初回アクセスのときに自動で作ります。手作業は要りません。

## 3. Workers にGitHubをつなぐ（自動公開の設定）
1. ダッシュボードの「Workers & Pages」→「Create application」→「Import a repository」（GitHubのリポジトリをインポート）。
2. GitHubへの接続を許可し、`eigo-dojo` のリポジトリを選ぶ。
3. プロジェクト名は、`wrangler.toml` の `name`（`eigo-dojo`）と**同じ名前**にする。
4. ビルドコマンドは空のまま、デプロイコマンドは既定（`npx wrangler deploy`）のまま、「Save and Deploy」。
5. デプロイが成功すると、`https://eigo-dojo.あなたのサブドメイン.workers.dev` のようなURLができる。

以後は、**GitHubにpushするたびに、自動で公開が更新されます**。

## 4. 秘密の設定（シークレット）を登録する
作ったWorkerの「Settings」→「Variables and Secrets」で、次の2つを、種類「**Secret（シークレット）**」で追加します（ビルド用ではなく、実行時の変数です）。

| 名前 | 値 |
|---|---|
| `GEMINI_API_KEY` | 新しいGeminiのAPIキー |
| `SETUP_TOKEN` | 自分で決めた、長いランダムな文字列（最初の管理者を作るときだけ使う。20文字以上を推奨） |

保存したら、反映のために、もう一度デプロイ（Deployments から再デプロイ）されることがあります。

## 5. 最初の設定（画面での操作）
1. 手順3のURLを開く → 「はじめての設定です」の画面が出る。
2. `SETUP_TOKEN` の値と、管理者のパスワード（8文字以上）を入れる → ID `admin` でログインする。
3. 管理画面の「ダッシュボード」→ **「AI接続チェック」**を押す。「AIにつながっています」と出れば成功。出なかったときは、画面の案内（キーの問題、モデル名の問題など）に従う。
4. 「問題データ」→ 自分のCSVを取り込む（または「練習用のサンプル問題を取り込む」）。
5. 「生徒」→ 生徒を登録する（パスワードは自動で作られ、1度だけ表示される）。
6. 生徒の画面（別のブラウザかプライベートウィンドウ）でログインし、**わざと間違えて**答えて、AIの解説が出るか確認する。

## 6. こんなときは
| 症状 | 対処 |
|---|---|
| 画面が真っ白／`Error 1101` など | デプロイのログを確認。`wrangler.toml` の `database_id` が書き換えられているか確認 |
| 「データベースの操作が失敗」が続く | D1の無料枠（1日の行数）を超えた可能性。翌日（UTC 0時＝日本時間9時）にリセットされる。使用量はダッシュボードで確認 |
| 管理画面の集計が遅い／失敗する | 集計期間を短くする（既定は30日） |
| ログインのときだけ「CPU時間を超過」のエラー | `wrangler.toml` の `PBKDF2_ITERS` を小さくする（例: `20000`）。ただし、すでに作った生徒のパスワードは、作成時の回数のまま使われる |
| AIがずっと「代替採点」になる | 管理画面の「AI接続チェック」で原因を確認 |

## 7. 費用と、無料枠の目安
- Workers・D1・GitHubは、無料プランで始められます。無料枠の内容は変わることがあるので、必ず公式ページで最新を確認してください。
- 参考: 2026年10月時点で、D1の無料枠は1日あたり読み取り500万行・書き込み10万行です。1セット（5問）で、書き込みは、索引を含めて30〜60行程度（私の見積もりです）。
- Geminiは、生徒が使う本番では有料枠を推奨します。
