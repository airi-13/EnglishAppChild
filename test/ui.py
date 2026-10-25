"""ブラウザ(Chromium)で、初期設定から生徒・管理者の操作までを通しで確認する。  python3 test/ui.py"""
import csv, io, os, re, sqlite3, subprocess, sys, tempfile, time, urllib.request
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.environ.get("SHOTS", "/home/claude/shots_cf")
PORT = 5078
BASE = f"http://127.0.0.1:{PORT}"
problems = []


def check(cond, msg):
    print(("  OK  " if cond else "  NG  ") + msg)
    if not cond:
        problems.append(msg)


def main():
    os.makedirs(SHOTS, exist_ok=True)
    tmp = tempfile.TemporaryDirectory()
    dbf = os.path.join(tmp.name, "ui.db")
    env = dict(os.environ, DB_FILE=dbf, AI_MODE="mock", PORT=str(PORT), SETUP_TOKEN="ui-setup-token", PBKDF2_ITERS="3000")
    srv = subprocess.Popen(["node", "--no-warnings", "dev/server.mjs"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(BASE + "/api/healthz", timeout=1); break
            except Exception:
                time.sleep(0.2)
        with sync_playwright() as p:
            br = p.chromium.launch()
            errs = []

            def newpage(w=1100, h=900):
                pg = br.new_context(viewport={"width": w, "height": h}, locale="ja-JP").new_page()
                pg.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
                pg.on("console", lambda m: errs.append(f"console.{m.type}: {m.text}") if m.type == "error" and "fonts.g" not in m.text and "ERR_" not in m.text and "Failed to load resource" not in m.text else None)
                pg.on("dialog", lambda d: d.accept())
                return pg

            ad = newpage()
            ad.goto(BASE)
            ad.wait_for_selector("text=はじめての設定です")
            check(True, "初期設定: 管理者が未作成のとき、設定画面が出る")
            ad.screenshot(path=f"{SHOTS}/cf_setup.png")
            ad.fill("#setup_token", "wrong-token"); ad.fill("#setup_pw", "adminpass1"); ad.fill("#setup_pw2", "adminpass1")
            ad.click("button:has-text('管理者を作る')"); ad.wait_for_selector(".banner.err")
            check("トークンが違います" in ad.inner_text(".banner.err"), "初期設定: 違うトークンは拒否される")
            ad.fill("#setup_token", "ui-setup-token"); ad.fill("#setup_pw2", "different1")
            ad.click("button:has-text('管理者を作る')")
            check("一致しません" in ad.inner_text(".banner.err"), "初期設定: パスワードの不一致を検出")
            ad.fill("#setup_pw2", "adminpass1"); ad.click("button:has-text('管理者を作る')")
            ad.wait_for_selector("text=管理者を作りました")
            ad.fill("#login_id", "admin"); ad.fill("#password", "adminpass1"); ad.click("button:has-text('ログイン')")
            ad.wait_for_selector("text=ダッシュボード")
            check(ad.locator(".stat").count() >= 8, "管理者: ログインしてダッシュボードが出る")

            # AI接続チェック（簡易モード）
            ad.click("button:has-text('AI接続チェック')"); ad.wait_for_selector("text=AIにつながっています")
            check(True, "管理者: AI接続チェックのボタンが動く（簡易モード）")
            # 期間の切り替え
            ad.select_option("select[aria-label='集計する期間']", "90"); ad.wait_for_selector(".stat")
            check(ad.locator("select[aria-label='集計する期間']").input_value() == "90", "管理者: 集計期間を切り替えられる")

            # サンプル問題（1,600問）を画面から取り込む
            ad.click(".tabs >> text=問題データ"); ad.click("#samplebtn")
            ad.wait_for_selector("text=1600問を取り込みました", timeout=60000)
            check(True, "管理者: サンプル1,600問を、分割して取り込める")
            ad.wait_for_timeout(1500)
            check(ad.locator(".cols div.zero").count() == 0 and "20級：40" in ad.inner_text(".cols"), "管理者: 全40級・段が各40問になっている")
            ad.screenshot(path=f"{SHOTS}/cf_admin_questions.png", full_page=True)
            # 不備のあるCSV
            bad = os.path.join(tmp.name, "bad.csv")
            open(bad, "w", encoding="utf-8").write("級・段,番号,英文,和文,英単語,日本語の単語\n20級,1,I have a pen.,私はペンを持っています。,cat,猫\n")
            ad.set_input_files("#csvfile", bad); ad.click("button:has-text('取り込む')")
            ad.wait_for_selector("text=1問も取り込んでいません")
            check("1問も取り込んでいません" in ad.inner_text(".banner.err"), "管理者: 不備のあるCSVは取り込まれず、理由が出る")
            # ファイル内の重複（別の分割をまたぐ）
            dup = os.path.join(tmp.name, "dup.csv")
            rows = ["級・段,番号,英文,和文,英単語,日本語の単語"] + [f"19級,{i},I have a pen.,私はペンを持っています。,pen,ペン" for i in range(1, 201)] + ["19級,1,I have a cat.,私は猫を持っています。,cat,猫"]
            open(dup, "w", encoding="utf-8").write("\n".join(rows))
            ad.set_input_files("#csvfile", dup); ad.click("button:has-text('取り込む')")
            ad.wait_for_selector("text=ファイル内で重複しています")
            check("1問も取り込んでいません" in ad.inner_text(".banner.err"), "管理者: 分割をまたぐ重複も検出できる")
            check(sqlite3.connect(dbf).execute("SELECT COUNT(*) FROM questions WHERE level_id=(SELECT id FROM levels WHERE code='k19') AND en_text='I have a cat.'").fetchone()[0] == 0, "管理者: 重複があるファイルは、途中まで書き込まれていない")

            # 生徒の登録
            ad.click(".tabs >> text=生徒")
            ad.fill("textarea", "kid01,テスト太郎\nkid02,テスト花子\nbad id,x")
            ad.click("button:has-text('登録する')"); ad.wait_for_selector("pre.cred")
            creds = {l.split("\t")[0]: l.split("\t")[1] for l in ad.inner_text("pre.cred").splitlines() if "\t" in l}
            check(set(creds) == {"kid01", "kid02"} and all(len(v) == 8 for v in creds.values()), "管理者: 生徒2人が登録され、パスワードが1度だけ表示される")
            check(ad.locator(".banner.err").count() == 1, "管理者: 不正なID(bad id)はエラー表示")

            # 生徒
            st = newpage(1000, 900)
            st.goto(BASE)
            st.fill("#login_id", "kid01"); st.fill("#password", "wrong"); st.click("button:has-text('ログイン')")
            st.wait_for_selector(".banner.err")
            check("違います" in st.inner_text(".banner.err"), "生徒: 間違ったパスワードでエラーが出る")
            st.fill("#password", creds["kid01"]); st.click("button:has-text('ログイン')")
            st.wait_for_selector(".ladder")
            check(st.locator(".chip").count() == 40, "生徒: 級・段の帯が40個表示される")
            st.locator(".chip", has_text=re.compile(r"^20級")).click()
            st.click("button:has-text('問題を出す')"); st.wait_for_selector(".q")
            check(st.locator(".q").count() == 5, "生徒: 5問が表示される")
            st.click("button:has-text('答えを送る')")
            check(st.locator(".banner.err").count() == 1, "生徒: 未回答のままだと送れず、案内が出る")
            con = sqlite3.connect(dbf); con.row_factory = sqlite3.Row
            rid = con.execute("SELECT id FROM rounds ORDER BY id DESC LIMIT 1").fetchone()[0]
            rows = con.execute("SELECT rq.id rq_id, q.* FROM round_questions rq JOIN questions q ON q.id=rq.question_id WHERE rq.round_id=? ORDER BY rq.position", (rid,)).fetchall()
            xss = "<img src=x onerror=window.__xss=1>"
            for i, r in enumerate(rows):
                st.fill(f'[data-rq="{r["rq_id"]}"]', xss if i == 0 else ("ぜんぜん違う" if i == 1 else r["ja_text"]))
            st.click("button:has-text('答えを送る')"); st.wait_for_selector("text=空欄に入る単語")
            check(st.locator(".q").count() == 2 and st.locator(".blank").count() == 2, "生徒: 間違えた2問だけが空欄補充で出る")
            check(not st.evaluate("() => window.__xss"), "セキュリティ: 入力したHTMLタグが実行されない")
            st.screenshot(path=f"{SHOTS}/cf_fillin.png", full_page=True)
            st.fill(f'[data-rq="{rows[0]["rq_id"]}"]', rows[0]["target_ja"]); st.fill(f'[data-rq="{rows[1]["rq_id"]}"]', "まちがい")
            st.click("button:has-text('答えを送る')"); st.wait_for_selector("text=確認テスト")
            check(st.locator(".banner.ok").count() == 1 and st.locator(".expl").count() == 1, "生徒: 補足説明（正解の単語）と解説（不正解の単語）が出る")
            st.fill(f'[data-rq="{rows[1]["rq_id"]}"]', rows[1]["ja_text"]); st.click("button:has-text('テストの答えを送る')")
            st.wait_for_selector("text=今回の結果")
            check(st.locator("text=覚える単語リスト").count() == 1 and st.locator(".pill.ok").count() == 3, "生徒: まとめと結果（一発正解3問）が出る")
            check(not st.evaluate("() => window.__xss"), "セキュリティ: 結果画面でもタグは実行されない")
            st.screenshot(path=f"{SHOTS}/cf_done.png", full_page=True)
            st.click("button:has-text('続きの問題へ')"); st.wait_for_selector("button:has-text('答えを送る')")
            check("No.6〜10" in st.inner_text("h2"), "生徒: 続きの問題がNo.6〜10で出る")
            st.click("button:has-text('ホームにもどる')"); st.wait_for_selector("text=途中の問題があります")
            st.click("button:has-text('つづける')"); st.wait_for_selector(".q")
            check(st.locator(".q").count() == 5, "生徒: 途中の問題を再開できる")

            ph = newpage(375, 740)
            ph.goto(BASE); ph.fill("#login_id", "kid02"); ph.fill("#password", creds["kid02"]); ph.click("button:has-text('ログイン')")
            ph.wait_for_selector(".ladder"); ph.click("text=日英（日→英）"); ph.locator(".chip", has_text="初段").click()
            ph.click("button:has-text('問題を出す')"); ph.wait_for_selector(".q")
            check(not ph.evaluate("() => document.documentElement.scrollWidth > window.innerWidth + 1"), "スマホ幅(375px): 横にはみ出さない")
            ph.screenshot(path=f"{SHOTS}/cf_mobile.png", full_page=True)

            # 管理者: 集計・書き出し・停止
            ad.click(".tabs >> text=生徒"); ad.wait_for_selector("table")
            row1 = ad.locator("tr", has_text="kid01")
            check("60%" in row1.inner_text(), "管理者: kid01の正答率が60%（5問中3問正解）と集計されている")
            row1.locator("text=くわしく").click(); ad.wait_for_selector("text=よく間違える問題")
            check(ad.locator("table").count() >= 3, "管理者: 生徒の詳細（級別正答率・間違えた問題）が出る")
            ad.screenshot(path=f"{SHOTS}/cf_admin_detail.png", full_page=True)
            ad.click(".tabs >> text=生徒"); ad.wait_for_selector("table")
            with ad.expect_download() as dl:
                ad.click("#exportbtn")
            body = open(dl.value.path(), encoding="utf-8-sig").read()
            rows_csv = list(csv.reader(io.StringIO(body)))
            n_att = con.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
            check(rows_csv[0][:2] == ["日時", "ID"] and len(rows_csv) == n_att + 1, f"管理者: 回答ログCSVに全{n_att}件が書き出される")
            ad.click(".tabs >> text=利用状況"); ad.wait_for_selector("table")
            ad.click(".tabs >> text=間違いの多い問題"); ad.wait_for_selector(".card")
            ad.click(".tabs >> text=生徒"); ad.wait_for_selector("table")
            ad.locator("tr", has_text="kid02").locator("button:has-text('停止')").click(); ad.wait_for_selector("text=停止中")
            p3 = newpage(); p3.goto(BASE); p3.fill("#login_id", "kid02"); p3.fill("#password", creds["kid02"]); p3.click("button:has-text('ログイン')")
            p3.wait_for_selector(".banner.err")
            check(True, "管理者: 停止した生徒はログインできない")
            st.click("button:has-text('ログアウト')"); st.wait_for_selector("#login_id")
            check(True, "生徒: ログアウトするとログイン画面に戻る")
            check(not errs, "ブラウザのエラー（pageerror/console error）が0件" + (f"  ← {errs[:3]}" if errs else ""))
            br.close()
    finally:
        srv.terminate(); tmp.cleanup()
    print("\n結果:", "合格" if not problems else f"不合格 {problems}")
    sys.exit(0 if not problems else 1)


if __name__ == "__main__":
    main()
