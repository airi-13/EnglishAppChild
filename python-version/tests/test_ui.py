"""ブラウザ(Chromium)で、生徒と管理者の画面を実際に操作して確認する。  python -m tests.test_ui"""
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

from tests.helpers import sample_csv_path

SHOTS = os.environ.get("SHOTS", "/home/claude/shots")
PORT = 5077
BASE = f"http://127.0.0.1:{PORT}"
problems = []


def check(cond, msg):
    print(("  OK  " if cond else "  NG  ") + msg)
    if not cond:
        problems.append(msg)


def truth(db, rid):
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    rows = c.execute("SELECT rq.id rq_id, q.* FROM round_questions rq JOIN questions q ON q.id=rq.question_id WHERE rq.round_id=? ORDER BY rq.position", (rid,)).fetchall()
    c.close()
    return rows


def main():
    tmp = tempfile.TemporaryDirectory()
    db = os.path.join(tmp.name, "ui.db")
    env = dict(os.environ, DB_PATH=db, AI_MODE="mock", PORT=str(PORT))
    run = lambda *a: subprocess.run([sys.executable, "manage.py", *a], env=env, check=True, capture_output=True)
    run("init-db"); run("create-admin", "admin", "adminpass1"); run("import-questions", sample_csv_path())
    srv = subprocess.Popen([sys.executable, "run.py"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(BASE + "/healthz", timeout=1); break
            except Exception:
                time.sleep(0.2)
        with sync_playwright() as p:
            br = p.chromium.launch()
            errs = []

            def newpage(w=1000, h=900):
                ctx = br.new_context(viewport={"width": w, "height": h}, locale="ja-JP")
                pg = ctx.new_page()
                pg.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
                pg.on("console", lambda m: errs.append(f"console.{m.type}: {m.text}") if m.type == "error" and "fonts.g" not in m.text and "ERR_" not in m.text and "Failed to load resource" not in m.text else None)
                pg.on("dialog", lambda d: d.accept())
                return pg

            # ---------- 管理者が生徒を作る
            ad = newpage(1100, 900)
            ad.goto(BASE)
            ad.fill("#login_id", "admin"); ad.fill("#password", "adminpass1"); ad.click("text=ログイン")
            ad.wait_for_selector("text=ダッシュボード")
            check(ad.locator(".stat").count() >= 8, "管理者: ダッシュボードの数値カードが表示される")
            ad.click(".tabs >> text=生徒")
            ad.fill("textarea", "kid01,テスト太郎\nkid02,テスト花子\nbad id,x")
            ad.click("button:has-text('登録する')")
            ad.wait_for_selector("pre.cred")
            creds = {l.split("\t")[0]: l.split("\t")[1] for l in ad.inner_text("pre.cred").splitlines() if "\t" in l}
            check(set(creds) == {"kid01", "kid02"} and all(len(v) == 8 for v in creds.values()), "管理者: 生徒2人が登録され、パスワードが1度だけ表示される")
            check(ad.locator(".banner.err").count() == 1, "管理者: 不正なID(bad id)はエラー表示")
            ad.screenshot(path=f"{SHOTS}/admin_students.png", full_page=True)

            # ---------- 生徒（PCサイズ）
            st = newpage(1000, 900)
            st.goto(BASE)
            st.screenshot(path=f"{SHOTS}/login.png")
            st.fill("#login_id", "kid01"); st.fill("#password", "wrong"); st.click("text=ログイン")
            st.wait_for_selector(".banner.err")
            check("違います" in st.inner_text(".banner.err"), "生徒: 間違ったパスワードでエラーが出る")
            st.fill("#password", creds["kid01"]); st.click("text=ログイン")
            st.wait_for_selector(".ladder")
            check(st.locator(".chip").count() == 40, "生徒: 級・段の帯が40個表示される")
            st.locator(".chip", has_text=re.compile(r"^20級")).click()
            st.screenshot(path=f"{SHOTS}/student_home.png")
            st.click("text=問題を出す")
            st.wait_for_selector(".q")
            check(st.locator(".q").count() == 5, "生徒: 5問が表示される")
            check(st.locator(".steps li.on").inner_text() == "問題", "生徒: 進み具合が「問題」")
            st.screenshot(path=f"{SHOTS}/student_q1.png", full_page=True)
            # 未回答のまま送る
            st.click("text=答えを送る")
            check(st.locator(".banner.err").count() == 1, "生徒: 未回答のままだと送れず、案内が出る")
            rid = int(st.evaluate("() => 0") or 0)
            row = sqlite3.connect(db).execute("SELECT id FROM rounds ORDER BY id DESC LIMIT 1").fetchone()
            rid = row[0]
            rows = truth(db, rid)
            xss = "<img src=x onerror=window.__xss=1>"
            for i, r in enumerate(rows):
                val = xss if i == 0 else ("ぜんぜん違う" if i == 1 else r["ja_text"])
                st.fill(f'[data-rq="{r["rq_id"]}"]', val)
            st.click("text=答えを送る")
            st.wait_for_selector("text=空欄に入る単語")
            check(st.locator(".q").count() == 2 and st.locator(".blank").count() == 2, "生徒: 間違えた2問だけが空欄補充で出る")
            check(st.locator(".steps li.on").inner_text() == "空欄補充", "生徒: 進み具合が「空欄補充」")
            check(not st.evaluate("() => window.__xss"), "セキュリティ: 入力したHTMLタグが実行されない")
            st.screenshot(path=f"{SHOTS}/student_fillin.png", full_page=True)
            # 1問は正解、1問は不正解の単語
            st.fill(f'[data-rq="{rows[0]["rq_id"]}"]', rows[0]["target_ja"])
            st.fill(f'[data-rq="{rows[1]["rq_id"]}"]', "まちがい")
            st.click("text=答えを送る")
            st.wait_for_selector("text=確認テスト")
            check(st.locator(".banner.ok").count() == 1, "生徒: 合っていた単語の補足説明が出る")
            check(st.locator(".expl").count() == 1, "生徒: 解説が出る（不正解の1問だけ）")
            st.screenshot(path=f"{SHOTS}/student_review.png", full_page=True)
            st.fill(f'[data-rq="{rows[1]["rq_id"]}"]', rows[1]["ja_text"])
            st.click("text=テストの答えを送る")
            st.wait_for_selector("text=今回の結果")
            check(st.locator("text=覚える単語リスト").count() == 1, "生徒: まとめ（覚える単語リスト）が出る")
            check(st.locator(".pill.ok").count() == 3, "生徒: 一発正解が3問")
            check(xss in st.inner_text(".card:last-of-type") or xss in st.content().replace("&lt;", "<").replace("&gt;", ">"), "セキュリティ: 入力文字はそのまま文字として表示される")
            check(not st.evaluate("() => window.__xss"), "セキュリティ: 結果画面でもタグは実行されない")
            st.screenshot(path=f"{SHOTS}/student_done.png", full_page=True)
            st.click("text=続きの問題へ")
            st.wait_for_selector("button:has-text('答えを送る')")
            check("No.6〜10" in st.inner_text("h2"), "生徒: 続きの問題がNo.6〜10で出る")
            # 途中でホームに戻って再開
            st.click("text=ホームにもどる")
            st.wait_for_selector("text=途中の問題があります")
            st.click("button:has-text('つづける')")
            st.wait_for_selector(".q")
            check(st.locator(".q").count() == 5, "生徒: 途中の問題を再開できる")

            # ---------- 生徒（スマホ幅）
            ph = newpage(375, 740)
            ph.goto(BASE)
            ph.fill("#login_id", "kid02"); ph.fill("#password", creds["kid02"]); ph.click("text=ログイン")
            ph.wait_for_selector(".ladder")
            ph.click("text=日英（日→英）")
            ph.locator(".chip", has_text="初段").click()
            ph.click("text=問題を出す")
            ph.wait_for_selector(".q")
            over = ph.evaluate("() => document.documentElement.scrollWidth > window.innerWidth + 1")
            check(not over, "スマホ幅(375px): 横にはみ出さない")
            ph.screenshot(path=f"{SHOTS}/mobile_q.png", full_page=True)

            # ---------- 管理者: 集計・CSV取り込み
            ad.click(".tabs >> text=ダッシュボード")
            ad.wait_for_selector(".stat")
            ad.click(".tabs >> text=生徒")
            ad.wait_for_selector("table")
            row1 = ad.locator("tr", has_text="kid01")
            check("100%" not in row1.inner_text() and "%" in row1.inner_text(), "管理者: kid01の正答率が集計されている（5問中3問正解=60%）")
            check("60%" in row1.inner_text(), "管理者: kid01の正答率は60%")
            row1.locator("text=くわしく").click()
            ad.wait_for_selector("text=よく間違える問題")
            check(ad.locator("text=ぜんぜん違う").count() == 0 and ad.locator("table").count() >= 3, "管理者: 生徒の詳細（級別正答率・間違えた問題）が出る")
            ad.screenshot(path=f"{SHOTS}/admin_student_detail.png", full_page=True)
            ad.click(".tabs >> text=問題データ")
            bad = os.path.join(tmp.name, "bad.csv")
            open(bad, "w", encoding="utf-8").write("級・段,番号,英文,和文,英単語,日本語の単語\n20級,1,I have a pen.,私はペンを持っています。,cat,猫\n")
            ad.set_input_files("#csvfile", bad); ad.click("button:has-text('取り込む')")
            ad.wait_for_selector(".banner.err")
            check("1問も取り込んでいません" in ad.inner_text(".banner.err"), "管理者: 不備のあるCSVは取り込まれず、行番号つきで理由が出る")
            good = os.path.join(tmp.name, "good.csv")
            open(good, "w", encoding="utf-8").write("級・段,番号,英文,和文,英単語,日本語の単語,文法メモ\n20級,1,I have a pen.,私はペンを持っています。,pen,ペン,aの使い方\n")
            ad.set_input_files("#csvfile", good); ad.click("button:has-text('取り込む')")
            ad.wait_for_selector(".banner.ok")
            ad.wait_for_timeout(1200)
            ad.screenshot(path=f"{SHOTS}/admin_questions.png", full_page=True)
            ad.click(".tabs >> text=利用状況"); ad.wait_for_selector("table")
            ad.click(".tabs >> text=間違いの多い問題"); ad.wait_for_selector(".card")
            # CSVダウンロード
            ad.click(".tabs >> text=生徒"); ad.wait_for_selector("table")
            with ad.expect_download() as dl:
                ad.click("text=回答ログをCSVでダウンロード")
            body = open(dl.value.path(), encoding="utf-8-sig").read()
            check(body.startswith("日時,ID") and "kid01" in body, "管理者: 回答ログCSVをダウンロードできる")
            # 停止した生徒はログインできない
            ad.locator("tr", has_text="kid02").locator("button:has-text('停止')").click()
            ad.wait_for_selector("text=停止中")
            p3 = newpage()
            p3.goto(BASE); p3.fill("#login_id", "kid02"); p3.fill("#password", creds["kid02"]); p3.click("text=ログイン")
            p3.wait_for_selector(".banner.err")
            check(True, "管理者: 停止した生徒はログインできない")
            # ログアウト
            st.click("text=ログアウト"); st.wait_for_selector("#login_id")
            check(True, "生徒: ログアウトするとログイン画面に戻る")

            check(not errs, "ブラウザのエラー（pageerror/console error）が0件" + (f"  ← {errs[:3]}" if errs else ""))
            br.close()
    finally:
        srv.terminate()
        tmp.cleanup()
    print("\n結果:", "合格" if not problems else f"不合格 {problems}")
    sys.exit(0 if not problems else 1)


if __name__ == "__main__":
    main()
