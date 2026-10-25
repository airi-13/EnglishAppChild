# GeminiのAPIキー取得と連携、GitHubへの公開手順

## 最短手順（自動化済み）
1. AI Studio でAPIキーを作る（下の A の1〜4）。※ここだけは本人のGoogleアカウントでの操作が必要です。
2. `python setup.py` を実行し、キーと管理者パスワードを入力する（接続チェックまで自動）。
3. GitHub に空の **Private** リポジトリを作り、`python tools/publish_github.py リポジトリのURL`（またはgh導入済みなら `--create`）を実行する。

以下は、各手順の詳細です。

## A. GeminiのAPIキーを取る（約5分・クレジットカード不要）
1. 個人のGoogleアカウントでログインする（学校・会社のアカウントは管理者が制限していることがある）。
2. ブラウザで **https://aistudio.google.com/apikey** を開く。初回は利用規約の同意画面が出る（マーケティング用メールの同意チェックは外したままでよい）。
3. 「**Create API key**（APIキーを作成）」をクリックし、名前（例: `eigo-dojo`）を付け、「新しいプロジェクトにキーを作成」か既存プロジェクトを選んで作成する。
4. 表示されたキーをコピーして、パスワード管理アプリなどに保管する。**2026年現在、AI Studioは `AQ.` で始まる新方式のキー（サービスアカウントに紐づく「認証キー」）を発行する。従来の `AIza…` 形式とは別物だが、使い方（`x-goog-api-key` ヘッダー）は同じ。****人に見せない・GitHubに上げない・画面共有に映さない。**
5. （確認）Google Cloud Console →「APIとサービス」→「認証情報」でそのキーを開く。新方式のキーは、**「API の制限」が「Gemini API」の1つだけに設定済み**で、サービスアカウントにも自動で紐づいている。画面にその表示があれば追加の作業は不要（「アプリケーションの制限」は「なし」のままでよい。サーバーのIPアドレスが固定されたら、「IPアドレス」で制限するとさらに安全）。「ウェブサイト」制限は、サーバーから呼ぶこのアプリでは使えない。

### キーが動くかを確かめる（アプリを動かす前の単独テスト）
Mac / Linux（ターミナル）:
```bash
export GEMINI_API_KEY='コピーしたキー'
curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent" \
  -H "x-goog-api-key: $GEMINI_API_KEY" -H "Content-Type: application/json" \
  -d '{"contents":[{"parts":[{"text":"日本語で一言あいさつして"}]}]}'
```
Windows（PowerShell）:
```powershell
$env:GEMINI_API_KEY = "コピーしたキー"
curl.exe "https://generativelanguage.googleapis.com/v1beta/models" -H "x-goog-api-key: $env:GEMINI_API_KEY"
```
- キーは必ず **`x-goog-api-key` ヘッダー**で送る（URLに `?key=` と付ける方法は、キーがログに残りやすいので使わない）。
- 後者（`/models`）は、**そのキーで使えるモデル名の一覧**が出る。モデル名は入れ替わるので、ここで確認した名前を `GEMINI_MODEL` に設定する。

## B. このアプリにつなぐ
1. プロジェクトのフォルダで `.env.example` を `.env` という名前にコピーする。
2. `.env` を開いて、次のように書く。
```
AI_MODE=gemini
GEMINI_API_KEY=コピーしたキー
GEMINI_MODEL=gemini-flash-latest
AI_MAX_RPM=10
```
   - `AI_MODE=gemini` に変える（`mock` は動作確認用の簡易モック）。
   - `AI_MAX_RPM` は1分あたりのAI呼び出し上限。無料枠なら 5〜10 から始め、制限エラーが出るなら下げる。有料枠なら上げてよい。
   - `.env` は起動時に自動で読み込まれる（`.gitignore` により、GitHubには上がらない）。
3. 起動する: `python run.py` →  http://127.0.0.1:5050
4. **つながったかの確認**
   - 生徒でログインし、わざと間違えて答える。解説が出て、画面に「AIが混み合っているため…」の注意が**出なければ**、AIにつながっている。
   - 管理者の「ダッシュボード」の「AI呼び出し」「AI成功率」「代替採点になった回数」で確認できる。成功率が低い・代替採点が増えるときは、下の表を見る。

### うまくいかないとき
| 症状 | 原因と対処 |
|---|---|
| 注意が毎回出る／AI成功率0% | キーの貼り間違い（前後の空白）、`AI_MODE` が `mock` のまま、`.env` の場所が違う（プロジェクト直下に置く） |
| 401 `ACCESS_TOKEN_TYPE_UNSUPPORTED` | 新方式（AQ.）のキーで報告されている問題。Gemini APIが有効か、キーが「Gemini API」に制限されサービスアカウントに紐づいているかを確認し、**別のプロジェクトで新しいキーを作り直して**再テストする。直らなければGoogleの開発者フォーラムで報告する |
| 400 / 403 / 404 | キーが無効、キーの制限が厳しすぎる、**モデル名が古い・存在しない**（`/models` の一覧で確認） |
| 429 | 1分または1日の回数制限。`AI_MAX_RPM` を下げる／待つ／有料枠にする |
| 通信エラー | サーバーがインターネットに出られない、会社・学校のフィルター |

## C. 無料枠と有料枠
- キーを作ると、自動で**無料枠**が使える（支払い設定なし）。ただし回数制限が厳しく、**無料枠の入力は Google の製品改善に使われることがある**。
- 生徒が多い・本格運用では、キーを作ったGoogle Cloudプロジェクトで**請求（Cloud Billing）を有効化**する（キーも接続先も同じまま、上限だけが上がる）。
- 有料にするときは、**予算アラート**を設定して使いすぎを防ぐ。
- 無料枠で使えるモデルは入れ替わる。料金・制限は公式の料金ページで確認する。

## D. GitHubに上げる（非公開リポジトリ推奨）
**リポジトリは必ず「Private（非公開）」にする。** 教材データや運用情報が含まれるため。

### 方法1: ブラウザだけ（いちばん簡単）
1. https://github.com/new を開く → Repository name に `eigo-dojo` → **Private** を選ぶ → 「Create repository」。
2. 作成後の画面の「uploading an existing file」をクリック。
3. ダウンロードした `eigo-app.zip` を**展開**し、中の `eigo-app` フォルダの**中身**（`app`、`tests`、`README.md` など）を、ブラウザにドラッグ＆ドロップする。
4. 下の「Commit changes」を押す。
- 注意: `.env`（キー入り）は絶対に入れない。`.gitignore` と `.env.example` は入れてよい。一度に100ファイルまで。足りなければ2回に分ける。

### 方法2: コマンド（Git をインストール済みの場合）
```bash
cd eigo-app
git init -b main
git add .
git status            # ← .env と data/*.db が一覧に無いことを必ず確認
git commit -m "えいご道場: 初版"
git remote add origin https://github.com/あなたのユーザー名/eigo-dojo.git
git push -u origin main
```
パスワードを聞かれたら、GitHubのパスワードではなく **Personal Access Token**（GitHub → Settings → Developer settings → Personal access tokens）か、GitHub Desktop / `gh auth login` でのログインを使う。

### もしキーをGitHubに上げてしまったら
1. すぐにAI Studioでそのキーを**削除**し、新しいキーを作る（履歴に残るので、消しただけでは足りない）。
2. 新しいキーを `.env` に入れ直す。

## E. 公開サーバーで動かすとき
- キーは、サーバーの**環境変数**（ホスティングの設定画面）に登録する。コードや `.env` を公開しない。
- `COOKIE_SECURE=1`、HTTPSの後ろで動かす。詳細は README の「4. 本番運用のめやす」。
