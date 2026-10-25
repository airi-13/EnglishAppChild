#!/usr/bin/env python3
"""管理コマンド。  python manage.py <コマンド>
  init-db                       DBを作成（既にあれば何もしない）
  create-admin <ID> [パスワード]   管理者を作成（パスワード省略時は自動生成して表示）
  import-questions <CSVファイル>   問題を取り込む（同じ級・番号は上書き）
  sample-csv <出力先>             動作確認用のサンプル問題CSV（40級・段×40問）を作る
"""
import sys

from app import config, db, create_app, hash_password, gen_password, questions_io


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd, cfg = argv[1], config.load()
    if cmd == "init-db":
        c = db.connect(cfg["DB_PATH"]); db.init_db(c); print("OK:", cfg["DB_PATH"]); return 0
    if cmd == "create-admin" and len(argv) >= 3:
        c = db.connect(cfg["DB_PATH"]); db.init_db(c)
        pw = argv[3] if len(argv) > 3 else gen_password(12)
        if len(pw) < 8:
            print("管理者のパスワードは8文字以上にしてください"); return 1
        c.execute("INSERT INTO users(login_id,password_hash,role,label) VALUES (?,?, 'admin', '管理者')", (argv[2], hash_password(pw)))
        print(f"管理者を作成しました  ID: {argv[2]}  パスワード: {pw}"); return 0
    if cmd == "import-questions" and len(argv) >= 3:
        c = db.connect(cfg["DB_PATH"]); db.init_db(c)
        res = questions_io.import_csv(c, open(argv[2], encoding="utf-8-sig").read())
        if res["ok"]:
            print(f"取り込み完了: {res['imported']}問"); return 0
        for e in res["errors"]:
            print(f"  行{e['row']}: {e['message']}")
        print(f"エラー {res.get('error_count', len(res['errors']))}件。1件も取り込んでいません。"); return 1
    if cmd == "sample-csv" and len(argv) >= 3:
        from tools.make_sample_csv import write_csv
        write_csv(argv[2]); print("作成:", argv[2]); return 0
    print(__doc__); return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
