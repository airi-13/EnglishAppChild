# えいご道場（Cloudflare版）

小中学生向けの英単語・短文（英日／日英）学習アプリ。IDとパスワードでログインし、回答はすべて記録され、管理者が正答率・間違えた問題・利用頻度を確認できる。AIは Gemini。

**GitHubにpushするだけで、Cloudflare（無料プランで開始可能）に自動で公開される構成**です。サーバーの管理は不要です。

- 公開の手順: **`docs/deploy.md`**（最初に読む）
- 設計: `docs/design.md`　／　ER図: `docs/er_diagram.mermaid`　／　テスト結果: `docs/test_report.md`
- `python-version/`: 手元のパソコンで動かす、Python版（Cloudflareを使わない場合・予備）

## 仕組み
```
生徒・管理者のブラウザ ──▶ Cloudflare Workers（src/）──▶ D1（SQLite互換のデータベース）
        ▲                        └──▶ Gemini API（採点・解説。失敗しても学習は止まらない）
        └── 画面（public/）もWorkersが配信
```
- 出題の流れ・記録・集計は、プログラム（`src/engine.js`）が管理。AIは採点・補足・解説・確認テストの作成だけを担当。
- AIへの指示文は `src/prompts.js`。生徒のID・氏名はAIに送らない。
- 外部ライブラリは使っていません（`package.json` に依存なし）。

## 無料枠で使うときの注意（2026年10月時点）
- **D1の無料枠は、1日の読み取り・書き込みの行数に上限があり、超えると、その日はデータベースの操作が失敗します**（UTCの0時にリセット）。索引の書き込みも行数に数えられます。そのため、管理画面の集計は「直近N日」（既定30日）に限って読む設計です。
- **Workersの無料プランは、1回のリクエストあたりのCPU時間が10ミリ秒**です。ログインのパスワード確認だけが重く、PBKDF2を3万回（約5ミリ秒）にしています（`wrangler.toml` の `PBKDF2_ITERS`）。実際のCloudflareでの値は、管理画面の運用で超過エラーが出ないか確認してください。
- Gemini無料枠は回数制限が厳しく、入力が改善に使われることがあります。生徒が使う本番では、有料枠を推奨します。
- 無料枠の内容は変わることがあります。公開の前に、各サービスの公式ページで最新の条件を確認してください。

## 手元での確認（Node 22 以上）
```bash
npm test                 # 自動テスト（42件）
npm run dev              # http://127.0.0.1:5050 で、簡易モード(AIなし)で動かす（データはメモリ上。終了で消える）
node scripts/measure-cpu.mjs   # 主な処理のCPU時間の目安
```
`DB_FILE=./dev.db npm run dev` にすると、データをファイルに残せます。

## ファイル構成
```
wrangler.toml      Cloudflareの設定（D1のIDをここに書く）
src/               サーバー（index.js=入口, engine.js=出題フロー, ai.js=Gemini連携, stats.js=集計, importer.js=CSV取り込み, auth.js）
public/            画面（index.html, app.js, style.css）とサンプル問題 sample_questions.csv
migrations/        データベースの定義（src/schema.js から自動生成。アプリが初回に自動作成するので、手作業は不要）
test/              テスト一式（D1の代役 d1shim.mjs と、Gemini互換のテスト用サーバー）
scripts/           GitHub公開スクリプト、CPU計測、サンプル問題の生成
python-version/    Python版
```
