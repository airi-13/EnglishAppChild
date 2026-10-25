#!/usr/bin/env python3
"""えいご道場 かんたんセットアップ（Windows / Mac / Linux 共通）。  python setup.py
やること: ①ライブラリのインストール ②.env の作成とAPIキーの登録 ③データベース作成 ④管理者の作成
          ⑤練習用の問題の取り込み ⑥Geminiへの接続チェック
オプション: --skip-install（pipを実行しない） --skip-check（接続チェックを省く） --no-sample（練習用問題を入れない）
            --non-interactive（質問せず、環境変数 GEMINI_API_KEY / ADMIN_PASSWORD を使う）"""
import getpass
import os
import re
import secrets
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)


def say(msg=""):
    print(msg, flush=True)


def ask_secret(prompt):
    return getpass.getpass(prompt)  # 入力は画面に表示されない


def main(argv):
    inter = "--non-interactive" not in argv
    if sys.version_info < (3, 9):
        say("Python 3.9 以上が必要です。https://www.python.org/ から新しい版をインストールしてください。")
        return 1

    say("== 1/6 ライブラリのインストール ==")
    if "--skip-install" in argv:
        say("  （スキップ）")
    else:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
        if r.returncode != 0:
            say("  [NG] インストールに失敗しました。ネットワークを確認し、`python -m pip install -r requirements.txt` を手動で実行してください。")
            return 1

    from app import config, db, hash_password, questions_io  # noqa: E402

    say("\n== 2/6 設定ファイル(.env)とAPIキー ==")
    env_path = os.path.join(ROOT, ".env")
    if not os.path.exists(env_path):
        shutil.copyfile(os.path.join(ROOT, ".env.example"), env_path)
        say("  .env を作りました（GitHubには上がりません）")
    config.load_dotenv()
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if inter and not key:
        say("  Google AI Studio で作ったAPIキーを貼り付けてください（画面には表示されません）。")
        say("  まだ無い場合は、何も入れずに Enter を押すと、AIなしの簡易モードで動作確認できます。")
        key = ask_secret("  APIキー: ").strip().strip("\"'")
    elif key:
        key = key.strip("\"'")
    if re.search(r"\s", key):
        say("  [NG] キーに空白が含まれています。貼り直してください。")
        return 1
    if key:
        config.update_env({"GEMINI_API_KEY": key, "AI_MODE": "gemini"})
        say("  APIキーを .env に保存しました")
    else:
        config.update_env({"AI_MODE": "mock"})
        say("  キーなし → AI_MODE=mock（簡易モード）にしました。あとで python setup.py を再実行するとキーを登録できます")

    say("\n== 3/6 データベース ==")
    cfg = config.load()
    conn = db.connect(cfg["DB_PATH"])
    db.init_db(conn)
    say(f"  OK: {cfg['DB_PATH']}")

    say("\n== 4/6 管理者アカウント ==")
    admin = conn.execute("SELECT login_id FROM users WHERE role='admin' LIMIT 1").fetchone()
    if admin:
        say(f"  すでにあります（ID: {admin['login_id']}）")
    else:
        pw = os.environ.get("ADMIN_PASSWORD", "")
        if inter and not pw:
            while True:
                pw = ask_secret("  管理者パスワード（8文字以上・画面には表示されません）: ")
                if len(pw) < 8:
                    say("  8文字以上にしてください")
                    continue
                if pw != ask_secret("  もう一度: "):
                    say("  一致しません")
                    continue
                break
        elif not pw:
            pw = secrets.token_urlsafe(12)
            say(f"  管理者パスワードを自動で作りました: {pw}   ← 控えてください")
        if len(pw) < 8:
            say("  [NG] 管理者パスワードは8文字以上にしてください")
            return 1
        conn.execute("INSERT INTO users(login_id,password_hash,role,label) VALUES ('admin',?, 'admin','管理者')", (hash_password(pw),))
        say("  作成しました（ID: admin）")

    say("\n== 5/6 問題データ ==")
    n = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    if n:
        say(f"  すでに {n} 問あります")
    elif "--no-sample" in argv:
        say("  （スキップ）管理者画面の「問題データ」からCSVを取り込んでください")
    else:
        from tools.make_sample_csv import write_csv
        import tempfile
        tmp = os.path.join(tempfile.gettempdir(), "eigo_sample.csv")
        write_csv(tmp)
        with open(tmp, encoding="utf-8-sig") as f:
            res = questions_io.import_csv(conn, f.read())
        os.remove(tmp)
        say(f"  練習用の問題を {res['imported']} 問入れました（本番の教材は、管理者画面からCSVで入れ替えてください）")
    conn.close()

    say("\n== 6/6 Geminiへの接続チェック ==")
    code = 0
    if key and "--skip-check" not in argv:
        from tools import check_gemini
        code = check_gemini.main(["--write-env"])
    else:
        say("  （スキップ：キー未設定または --skip-check）")

    say("\n完了です。次のコマンドで起動します:\n    python run.py\nブラウザで http://127.0.0.1:5050 を開き、ID「admin」でログインしてください。")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
