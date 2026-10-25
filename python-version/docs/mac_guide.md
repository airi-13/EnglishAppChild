# Mac 手順書（最初から最後まで）

## 0. 先にやっておくこと（Googleアカウントでの操作）
チャットに貼ったキーは削除し、新しいキーを作ります。
1. https://aistudio.google.com/apikey を開く → 古いキーを削除。
2. 「Create API key」で新しいキーを作成し、コピーして保管（人に見せない）。
※「キーの確認が終わってから作り直す」場合は、手順7で差し替えます。

## 1. フォルダを用意してターミナルを開く
1. ダウンロードした `eigo-app.zip` をダブルクリックして展開 → `ダウンロード` に `eigo-app` フォルダができる。
2. ターミナルを開く（Spotlight: ⌘+スペース →「ターミナル」）。
3. 次を入力して Enter。
```bash
cd ~/Downloads/eigo-app
ls
```
`README.md  app  setup.py …` が見えれば成功。

## 2. Python を確認する
```bash
python3 --version
```
- **3.9 以上**（例: Python 3.12.x）が出れば次へ。
- 「コマンドデベロッパツールが必要」と出たら「インストール」→ 完了後にもう一度実行。
- 3.8 以下や、入っていない場合: https://www.python.org/downloads/ から macOS 用をダウンロードして実行 → 終わったら、Finder の「アプリケーション > Python 3.x」を開き、**「Install Certificates.command」をダブルクリック**（Googleとの通信に必要）。ターミナルを開き直して `python3 --version` を再確認。

## 3. 仮想環境を作る（このアプリ専用の置き場所）
```bash
python3 -m venv .venv
source .venv/bin/activate
```
行の先頭に `(.venv)` が付けば成功。以降は `python` と入力してよい。ターミナルを開き直したら、`cd ~/Downloads/eigo-app` と `source .venv/bin/activate` を最初にやり直す。

## 4. セットアップ（1コマンド）
```bash
python setup.py
```
聞かれること:
1. APIキー（貼り付けて Enter。**入力は画面に出ません**。キーが無ければ何も入れず Enter ＝ AIなしの簡易モード）
2. 管理者パスワード（8文字以上、2回）

自動で行われること: ライブラリのインストール、`.env` の作成、データベース作成、練習用問題1,600問の取り込み、Geminiへの接続チェック。最後に「接続OK」と出れば完了。
- 「NG」が出たら、その下の「→」の案内に従う。直らなければ、表示を（キーを伏せて）送ってください。

## 5. 起動して使う
```bash
python run.py
```
ブラウザで http://127.0.0.1:5050 を開く。ID `admin` と管理者パスワードでログイン。
- 止めるとき: ターミナルで `Ctrl + C`。
- このMacの中だけで動いています（他の端末からは見えません）。

## 6. 動作確認（AIが本当に使われているか）
1. 管理者画面の「生徒」タブで生徒を登録（例: `kid01,テスト`）。表示されたパスワードを控える。
2. ブラウザの**プライベートウィンドウ**（⌘+Shift+N）で http://127.0.0.1:5050 を開き、生徒でログイン。
3. 20級 → 英日 → 「問題を出す」。**わざと間違えて**全部送る → 空欄補充 → 解説とテスト → まとめ。
4. 「AIが混み合っているため…」の黄色い注意が**出なければ**、Geminiにつながっています。解説が自然な日本語かも確認してください。
5. 管理者画面のダッシュボードで「AI成功率」が表示され、「代替採点になった回数」が0なら正常。

## 7. キーを差し替える
```bash
open -e .env
```
`GEMINI_API_KEY=` の右側を新しいキーに書き換えて保存 → 確認:
```bash
python tools/check_gemini.py
```
その後、古いキーを AI Studio で削除する。

## 8. 自分の問題を入れる
管理者画面 >「問題データ」。CSV（UTF-8）の1行目: `級・段,番号,英文,和文,英単語,日本語の単語,文法メモ`。
- Excel: 「ファイル > 別名で保存 > CSV UTF-8（コンマ区切り）」。Numbers: 「書き出す > CSV…」（文字コードは UTF-8）。
- 英単語は英文の中に、日本語の単語は和文の中に、そのままの形で含まれること。不備があると、行番号つきで理由が出て、1問も入りません。

## 9. GitHubへ上げる
1. https://github.com/new で、`eigo-dojo` という名前の **Private** リポジトリを作る（README・.gitignore・ライセンスの追加はオフ）。
2. ターミナルで（`git --version` で、入っていなければ案内に従ってインストール）:
```bash
python tools/publish_github.py https://github.com/あなたのユーザー名/eigo-dojo.git
```
3. 認証を求められたら、どちらか:
   - GitHub CLI: `brew install gh`（Homebrewがある場合）→ `gh auth login` → `python tools/publish_github.py --create`
   - トークン: GitHub の Settings > Developer settings > Personal access tokens で、リポジトリ権限のトークンを作る → `export GITHUB_TOKEN='トークン'` → もう一度実行
秘密情報（.env・DB・キー）が含まれていると、自動で公開が中止されます。

## 10. うまくいかないとき
| 症状 | 対処 |
|---|---|
| `python: command not found` | `python3` と入力する／手順3の `source .venv/bin/activate` を実行する |
| `CERTIFICATE_VERIFY_FAILED` / SSL証明書の表示 | 手順2の「Install Certificates.command」を実行 |
| `hashlib has no attribute 'scrypt'` | 古い版のアプリです。最新のzipに入れ替えてください（修正済み） |
| `.zshrc: parse error` と出る | アプリとは無関係の、ターミナルの設定ファイルの書き間違いです。動作には影響しません（直したい場合はお知らせください） |
| 接続チェックの「採点テスト」で長く止まる／応答が遅い | 数十秒かかることがあります（最大2分ほど待つ）。`.env` に `GEMINI_THINKING_BUDGET=0` を追加すると速くなることがあります。時間は `python tools/check_gemini.py` で確認できます |
| `pip` の権限エラー | 手順3の仮想環境（`.venv`）の中で実行する |
| 「NG」・401・403・429 | 表示の「→」の案内に従う。詳細は `docs/setup_gemini_github.md` |
| ブラウザで「403 アクセスが拒否されました」 | Macの「AirPlayレシーバー」が5000番を使っているため。このアプリの標準は5050番です。`http://127.0.0.1:5050` を開く（古い版なら `PORT=5050 python run.py` で起動） |
| ブラウザで開けない | `python run.py` が動いているか（ターミナルを閉じていないか）確認 |

## 11. 生徒が自宅から使えるようにするには
今の手順は、あなたのMacの中で動かして確認する段階です。生徒の端末から使うには、インターネット上のサーバーに置く必要があります（HTTPS・gunicorn。README の「4. 本番運用のめやす」）。この段階に進むときは、お知らせください。
