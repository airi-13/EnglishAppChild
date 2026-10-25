import json
import os
import sqlite3
import tempfile
import unittest

from app import questions_io, db
from tests.helpers import Client, make_app, rq_truth, seed


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app, self.path = make_app(self.tmp.name)
        seed(self.path)
        self.admin = Client(self.app)
        self.admin.login("admin", "adminpass1")
        s, d, _ = self.admin.post("/api/admin/users", {"users": [{"login_id": "stu1", "label": "A1", "password": "pass1234"}, {"login_id": "stu2", "label": "A2", "password": "pass5678"}]})
        assert s == 200 and len(d["created"]) == 2, d
        self.s1 = Client(self.app)
        self.s1.login("stu1", "pass1234")
        self.s2 = Client(self.app)
        self.s2.login("stu2", "pass5678")
        self.mock = self.app.ai_client

    def tearDown(self):
        self.tmp.cleanup()

    def db(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def start(self, client, level="k20", d="en2ja", seq=1):
        s, data, _ = client.post("/api/rounds", {"level_code": level, "direction": d, "start_seq": seq})
        self.assertEqual(s, 200, data)
        s, view, _ = client.get(f"/api/rounds/{data['round_id']}")
        self.assertEqual(s, 200)
        return view

    def exact(self, view):
        t = rq_truth(self.path, view["id"])
        key = "ja_text" if view["direction"] == "en2ja" else "en_text"
        return {str(it["rq_id"]): t[it["rq_id"]][key] for it in view["items"]}

    def submit(self, client, view, answers):
        return client.post(f"/api/rounds/{view['id']}/answers", {"answers": answers})


class TestAuth(Base):
    def test_wrong_password_and_lockout(self):
        c = Client(self.app)
        s, d, _ = c.login("stu1", "bad")
        self.assertEqual(s, 401)
        for _ in range(4):
            c.login("stu1", "bad")
        s, d, _ = c.login("stu1", "pass1234")
        self.assertEqual(s, 429)  # 5回失敗でロック

    def test_roles_and_csrf(self):
        self.assertEqual(Client(self.app).get("/api/levels")[0], 401)
        self.assertEqual(self.s1.get("/api/admin/overview")[0], 403)
        self.assertEqual(self.s1.post("/api/admin/users", {"users": [{"login_id": "x12"}]})[0], 403)
        self.assertEqual(self.admin.get("/api/admin/overview")[0], 200)
        r = self.s1.c.post("/api/rounds", data="level_code=k20", headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(r.status_code, 415)
        s, d, _ = self.s1.post("/api/rounds", {"level_code": "k20", "direction": "en2ja", "start_seq": 1}, headers={"Origin": "http://evil.example"})
        self.assertEqual(s, 403)

    def test_deactivate_and_reset(self):
        uid = self.db().execute("SELECT id FROM users WHERE login_id='stu1'").fetchone()["id"]
        self.assertEqual(self.s1.get("/api/me")[0], 200)
        s, d, _ = self.admin.post(f"/api/admin/users/{uid}/reset_password", {"password": "newpass99"})
        self.assertEqual(s, 200)
        self.assertEqual(self.s1.get("/api/me")[0], 401)  # 既存セッションは無効
        self.assertEqual(Client(self.app).login("stu1", "pass1234")[0], 401)
        c = Client(self.app)
        self.assertEqual(c.login("stu1", "newpass99")[0], 200)
        self.admin.post(f"/api/admin/users/{uid}/active", {"active": False})
        self.assertEqual(c.get("/api/me")[0], 401)
        self.assertEqual(Client(self.app).login("stu1", "newpass99")[0], 401)

    def test_password_not_plaintext_and_duplicate_id(self):
        row = self.db().execute("SELECT password_hash FROM users WHERE login_id='stu1'").fetchone()
        self.assertTrue(row["password_hash"].startswith("pbkdf2$"))
        self.assertNotIn("pass1234", row["password_hash"])
        s, d, _ = self.admin.post("/api/admin/users", {"users": [{"login_id": "STU1"}, {"login_id": "ok_user"}, {"login_id": "x"}]})
        self.assertEqual(len(d["created"]), 1)
        self.assertEqual(len(d["errors"]), 2)
        self.assertEqual(len(d["created"][0]["password"]), 8)


class TestFlow(Base):
    def test_question_view_never_leaks_answers(self):
        v = self.start(self.s1)
        self.assertEqual(v["stage"], "answer")
        self.assertEqual(len(v["items"]), 5)
        blob = json.dumps(v, ensure_ascii=False)
        t = rq_truth(self.path, v["id"])
        for q in t.values():
            self.assertNotIn(q["ja_text"], blob)  # 英日: 正解の和文は出さない
        self.assertNotIn("expected", blob)

    def test_perfect_round_and_progress(self):
        v = self.start(self.s1)
        s, d, _ = self.submit(self.s1, v, self.exact(v))
        self.assertEqual(s, 200, d)
        self.assertEqual(d["view"]["stage"], "done")
        self.assertTrue(d["view"]["summary"]["perfect"])
        self.assertEqual(d["view"]["next"], {"level_code": "k20", "level_name": "20級", "start_seq": 6})
        self.assertEqual(self.mock.calls, [])  # 完全一致ならAIを使わない
        lv = {l["code"]: l for l in self.s1.get("/api/levels")[1]["levels"]}
        self.assertEqual(lv["k20"]["next"]["en2ja"], 6)
        self.assertEqual(lv["k20"]["next"]["ja2en"], 1)
        # 続きの5問
        v2 = self.start(self.s1, seq=6)
        self.assertEqual([i["no"] for i in v2["items"]], [6, 7, 8, 9, 10])

    def test_partial_or_blank_submit_rejected(self):
        v = self.start(self.s1)
        ans = self.exact(v)
        k = list(ans)[2]
        ans[k] = "   "
        s, d, _ = self.submit(self.s1, v, ans)
        self.assertEqual(s, 400)
        self.assertEqual(d["missing"], [3])
        del ans[k]
        s, d, _ = self.submit(self.s1, v, ans)
        self.assertEqual(s, 400)
        self.assertEqual(self.s1.get(f"/api/rounds/{v['id']}")[1]["stage"], "answer")
        s, d, _ = self.submit(self.s1, v, {str(i["rq_id"]): "あ" * 201 for i in v["items"]})
        self.assertEqual(s, 400)

    def test_full_path_fillin_correct(self):
        v = self.start(self.s1)
        ans = self.exact(v)
        wrong_keys = list(ans)[:2]
        for k in wrong_keys:
            ans[k] = "ぜんぜん違う答え"
        s, d, _ = self.submit(self.s1, v, ans)
        v2 = d["view"]
        self.assertEqual(v2["stage"], "fillin")
        self.assertEqual({str(i["rq_id"]) for i in v2["items"]}, set(wrong_keys))
        for it in v2["items"]:
            self.assertIn("＿＿＿", it["blank"])
        t = rq_truth(self.path, v["id"])
        fill = {str(i["rq_id"]): t[i["rq_id"]]["target_ja"] for i in v2["items"]}
        s, d, _ = self.submit(self.s1, v2, fill)
        v3 = d["view"]
        self.assertEqual(v3["stage"], "done")
        self.assertFalse(v3["summary"]["perfect"])
        self.assertEqual(len(v3["summary"]["words"]), 2)
        self.assertTrue(all(it["s2_feedback"] for it in v3["items"] if it["s1_ok"] == 0))
        con = self.db()
        self.assertEqual(con.execute("SELECT COUNT(*) FROM attempts WHERE stage=1").fetchone()[0], 5)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM attempts WHERE stage=2 AND is_correct=1").fetchone()[0], 2)

    def test_full_path_review_and_confirm(self):
        v = self.start(self.s1, d="ja2en")
        ans = self.exact(v)
        k = list(ans)[0]
        ans[k] = "completely wrong"
        s, d, _ = self.submit(self.s1, v, ans)
        v2 = d["view"]
        self.assertEqual(v2["stage"], "fillin")
        s, d, _ = self.submit(self.s1, v2, {str(v2["items"][0]["rq_id"]): "bad"})
        v3 = d["view"]
        self.assertEqual(v3["stage"], "review")
        it = v3["items"][0]
        self.assertTrue(it["explanation"] and it["test_prompt"])
        self.assertEqual(len(v3["items"]), 1)
        s, d, _ = self.submit(self.s1, v3, {str(it["rq_id"]): "My test answer"})
        v4 = d["view"]
        self.assertEqual(v4["stage"], "done")
        self.assertEqual(len(v4["summary"]["words"]), 1)
        con = self.db()
        self.assertEqual([r[0] for r in con.execute("SELECT DISTINCT stage FROM attempts ORDER BY stage")], [1, 2, 3])
        # 日英は大文字小文字・文末ピリオドを無視して完全一致
        v5 = self.start(self.s1, d="ja2en", seq=6)
        a5 = {k: x.upper().rstrip(".") for k, x in self.exact(v5).items()}
        s, d, _ = self.submit(self.s1, v5, a5)
        self.assertTrue(d["view"]["summary"]["perfect"])

    def test_ai_judges_alternative_answer_correct(self):
        v = self.start(self.s1)
        ans = self.exact(v)
        k = list(ans)[0]
        ans[k] = ans[k] + " #ok"
        s, d, _ = self.submit(self.s1, v, ans)
        self.assertTrue(d["view"]["summary"]["perfect"])
        row = self.db().execute("SELECT judged_by FROM attempts WHERE stage=1 AND answer_text LIKE '%#ok'").fetchone()
        self.assertEqual(row["judged_by"], "ai")

    def test_double_submit_conflict(self):
        v = self.start(self.s1)
        ans = self.exact(v)
        self.assertEqual(self.submit(self.s1, v, ans)[0], 200)
        s, d, _ = self.submit(self.s1, v, ans)
        self.assertEqual(s, 409)
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 5)

    def test_resume_and_abandon(self):
        v = self.start(self.s1)
        s, d, _ = self.s1.post("/api/rounds", {"level_code": "k19", "direction": "ja2en", "start_seq": 3})
        self.assertTrue(d["resumed"])
        self.assertEqual(d["round_id"], v["id"])
        self.assertEqual(self.s1.get("/api/rounds/active")[1]["round_id"], v["id"])
        self.s1.post(f"/api/rounds/{v['id']}/abandon")
        self.assertIsNone(self.s1.get("/api/rounds/active")[1]["round_id"])
        v2 = self.start(self.s1, level="k19", d="ja2en", seq=3)
        self.assertEqual(v2["level_code"], "k19")

    def test_level_rollover_and_end_of_data(self):
        v = self.start(self.s1, seq=36)
        self.assertEqual([i["no"] for i in v["items"]], [36, 37, 38, 39, 40])
        s, d, _ = self.submit(self.s1, v, self.exact(v))
        self.assertEqual(d["view"]["next"], {"level_code": "k19", "level_name": "19級", "start_seq": 1, "level_up": True})
        # 最後の段の最後
        v = self.start(self.s1, level="d20", seq=36)
        s, d, _ = self.submit(self.s1, v, self.exact(v))
        self.assertIsNone(d["view"]["next"])

    def test_bad_start_params(self):
        for body, code in [({"level_code": "zzz", "direction": "en2ja", "start_seq": 1}, 404), ({"level_code": "k20", "direction": "xx", "start_seq": 1}, 400),
                           ({"level_code": "k20", "direction": "en2ja", "start_seq": "abc"}, 400), ({"level_code": "k20", "direction": "en2ja", "start_seq": 0}, 400),
                           ({"level_code": "k20", "direction": "en2ja", "start_seq": 99}, 404)]:
            self.assertEqual(self.s1.post("/api/rounds", body)[0], code, body)

    def test_users_are_isolated(self):
        v = self.start(self.s1)
        self.assertEqual(self.s2.get(f"/api/rounds/{v['id']}")[0], 404)
        self.assertEqual(self.s2.post(f"/api/rounds/{v['id']}/answers", {"answers": {}})[0], 404)
        self.assertEqual(self.s2.post(f"/api/rounds/{v['id']}/abandon")[0], 404)
        self.assertEqual(self.s1.get(f"/api/rounds/{v['id']}")[0], 200)
        # 他人のrq_idを混ぜても無視される（自分の問題として扱われない）
        v2 = self.start(self.s2)
        s, d, _ = self.submit(self.s2, v2, {str(i["rq_id"]): "x" for i in v["items"]})
        self.assertEqual(s, 400)


class TestAIFailure(Base):
    def test_whole_flow_survives_ai_outage(self):
        self.mock.fail = 100  # AIが全部失敗
        v = self.start(self.s1)
        ans = self.exact(v)
        k = list(ans)[0]
        ans[k] = ans[k] + " #ok"  # 本来はAIが正解にする回答 → 代替採点では不正解になる
        s, d, _ = self.submit(self.s1, v, ans)
        self.assertEqual(s, 200, d)
        self.assertTrue(d["warnings"])
        v2 = d["view"]
        self.assertEqual(v2["stage"], "fillin")
        t = rq_truth(self.path, v["id"])
        it = v2["items"][0]
        s, d, _ = self.submit(self.s1, v2, {str(it["rq_id"]): "bad"})
        v3 = d["view"]
        self.assertEqual(v3["stage"], "review")
        self.assertTrue(v3["items"][0]["explanation"])  # 代替の解説
        s, d, _ = self.submit(self.s1, v3, {str(v3["items"][0]["rq_id"]): t[it["rq_id"]]["ja_text"]})  # 確認テストは完全一致で正解
        self.assertEqual(d["view"]["stage"], "done")
        by = {r[0] for r in self.db().execute("SELECT judged_by FROM attempts")}
        self.assertIn("fallback", by)
        # 管理画面の集計に代替採点の回数が出る
        self.assertGreaterEqual(self.admin.get("/api/admin/overview")[1]["fallback_judgments_7d"], 1)

    def test_recovers_after_outage(self):
        self.mock.fail = 1
        v = self.start(self.s1)
        ans = self.exact(v)
        k = list(ans)[0]
        ans[k] = "ちがう"
        s, d, _ = self.submit(self.s1, v, ans)
        self.assertTrue(d["warnings"])  # 1回目は失敗
        v2 = d["view"]
        s, d, _ = self.submit(self.s1, v2, {str(v2["items"][0]["rq_id"]): "x"})
        self.assertEqual(s, 200)
        self.assertFalse(d["warnings"])  # 回復後は通常採点


class TestAdmin(Base):
    def test_import_validation_and_upsert(self):
        con = db.connect(self.path)
        bad = "級・段,番号,英文,和文,英単語,日本語の単語\n20級,1,I have a pen.,私はペンを持っています。,cat,猫\n"
        r = questions_io.import_csv(con, bad)
        self.assertFalse(r["ok"])
        self.assertIn("見つかりません", r["errors"][0]["message"])
        before = con.execute("SELECT en_text FROM questions WHERE seq=1 AND level_id=1").fetchone()[0]
        self.assertNotEqual(before, "I have a pen.")
        good = "級・段,番号,英文,和文,英単語,日本語の単語\n20級,1,I have a pen.,私はペンを持っています。,pen,ペン\n初段,41,She plays tennis.,彼女はテニスをします。,plays,します\n"
        r = questions_io.import_csv(con, good)
        self.assertTrue(r["ok"], r)
        self.assertEqual(con.execute("SELECT en_text FROM questions WHERE seq=1 AND level_id=1").fetchone()[0], "I have a pen.")
        self.assertEqual(con.execute("SELECT COUNT(*) FROM questions").fetchone()[0], 1601)
        for csv_text in ["", "級・段,番号,英文,和文,英単語,日本語の単語\n", "級・段,番号,英文,和文,英単語,日本語の単語\n99級,1,a,あ,a,あ\n", "級・段,番号,英文,和文,英単語,日本語の単語\n20級,x,I have a pen.,私はペン,pen,ペン\n"]:
            self.assertFalse(questions_io.import_csv(con, csv_text)["ok"])
        dup = "級・段,番号,英文,和文,英単語,日本語の単語\n20級,2,I have a pen.,私はペン,pen,ペン\n20級,2,I have a cat.,私は猫,cat,猫\n"
        self.assertFalse(questions_io.import_csv(con, dup)["ok"])

    def test_import_via_api_and_levels_visible(self):
        csv_text = "級・段,番号,英文,和文,英単語,日本語の単語\n20級,1,I have a pen.,私はペンを持っています。,pen,ペン\n"
        s, d, _ = self.admin.post("/api/admin/questions/import", {"csv": csv_text})
        self.assertEqual(s, 200)
        s, d, _ = self.admin.post("/api/admin/questions/import", {"csv": "x\n1"})
        self.assertEqual(s, 400)

    def test_stats_and_export(self):
        for cl in (self.s1, self.s2):
            for seq in (1, 6):
                v = self.start(cl, seq=seq)
                ans = self.exact(v)
                if cl is self.s1:
                    ans[list(ans)[0]] = "ちがう"
                s, d, _ = self.submit(cl, v, ans)
                if d["view"]["stage"] == "fillin":
                    s, d, _ = self.submit(cl, d["view"], {str(i["rq_id"]): "bad" for i in d["view"]["items"]})
                    if d["view"]["stage"] == "review":
                        self.submit(cl, d["view"], {str(i["rq_id"]): "bad" for i in d["view"]["items"]})
        o = self.admin.get("/api/admin/overview")[1]
        self.assertEqual(o["students"], 2)
        self.assertEqual(o["active_students_7d"], 2)
        self.assertEqual(o["rounds_done_7d"], 4)
        self.assertEqual(o["accuracy_all"], 90.0)  # 20問中、s1が2問間違い
        st = {x["login_id"]: x for x in self.admin.get("/api/admin/students")[1]["students"]}
        self.assertEqual(st["stu1"]["accuracy"], 80.0)
        self.assertEqual(st["stu2"]["accuracy"], 100.0)
        self.assertEqual(st["stu1"]["rounds_done"], 2)
        det = self.admin.get(f"/api/admin/students/{st['stu1']['id']}")[1]
        self.assertEqual(len(det["weak"]), 2)
        self.assertEqual(det["by_level"][0]["accuracy"], 80.0)
        self.assertEqual(self.admin.get("/api/admin/students/99999")[0], 404)
        hard = self.admin.get("/api/admin/hardest")[1]["questions"]
        self.assertEqual(hard, [])  # 同じ問題への回答が3回未満
        self.assertTrue(self.admin.get("/api/admin/usage")[1]["days"])
        counts = self.admin.get("/api/admin/questions/counts")[1]["levels"]
        self.assertEqual(len(counts), 40)
        self.assertTrue(all(c["n"] == 40 for c in counts))
        s, _, r = self.admin.get("/api/admin/export/attempts.csv")
        self.assertEqual(s, 200)
        body = r.get_data(as_text=True)
        self.assertTrue(body.startswith("\ufeff日時"))
        self.assertEqual(len(body.strip().splitlines()), 1 + self.db().execute("SELECT COUNT(*) FROM attempts").fetchone()[0])

    def test_csv_formula_injection_escaped(self):
        v = self.start(self.s1)
        ans = self.exact(v)
        ans[list(ans)[0]] = "=HYPERLINK(\"http://evil\")"
        self.submit(self.s1, v, ans)
        body = self.admin.get("/api/admin/export/attempts.csv")[2].get_data(as_text=True)
        self.assertIn("'=HYPERLINK", body)
        self.assertNotIn(",=HYPERLINK", body)

    def test_hardest_questions_ranking(self):
        cls = [self.s1, self.s2]
        for i in range(3):  # 同じ問題に3回回答
            cl = cls[i % 2]
            v = self.start(cl, seq=1)
            ans = self.exact(v)
            ans[list(ans)[0]] = "ちがう"
            s, d, _ = self.submit(cl, v, ans)
            self.s1.post(f"/api/rounds/{v['id']}/abandon") if cl is self.s1 else self.s2.post(f"/api/rounds/{v['id']}/abandon")
        hard = self.admin.get("/api/admin/hardest")[1]["questions"]
        self.assertEqual(len(hard), 5)  # No.1〜5が各3回回答された
        self.assertEqual((hard[0]["seq"], hard[0]["accuracy"]), (1, 0.0))  # 毎回間違えたNo.1が先頭
        self.assertTrue(all(h["accuracy"] == 100.0 for h in hard[1:]))

    def test_security_headers_and_static(self):
        c = Client(self.app).c
        r = c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("default-src 'self'", r.headers["Content-Security-Policy"])
        self.assertEqual(r.headers["X-Frame-Options"], "DENY")
        self.assertEqual(c.get("/static/app.js").status_code, 200)
        self.assertEqual(c.get("/healthz").get_json(), {"ok": True})
        self.assertEqual(c.get("/api/nope").status_code, 404)
        self.assertEqual(c.get("/api/nope").get_json()["error"][:3], "The")  # JSONで返る


if __name__ == "__main__":
    unittest.main()


class TestDotenv(unittest.TestCase):
    def test_dotenv_loaded_without_overriding_existing(self):
        from app import config
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".env")
            with open(p, "w", encoding="utf-8") as f:
                f.write('# コメント\nTEST_KEY_A=abc123   # 行末コメント\nTEST_KEY_B="quoted value"\nTEST_KEY_C=fromfile\nTEST_KEY_D=      # 値は空でコメントだけ\n\n不正な行\n')
            os.environ.pop("TEST_KEY_A", None); os.environ.pop("TEST_KEY_B", None); os.environ.pop("TEST_KEY_D", None)
            os.environ["TEST_KEY_C"] = "fromenv"
            config.load_dotenv(p)
            self.assertEqual(os.environ["TEST_KEY_A"], "abc123")
            self.assertEqual(os.environ["TEST_KEY_B"], "quoted value")
            self.assertEqual(os.environ["TEST_KEY_C"], "fromenv")  # 環境変数が優先
            self.assertEqual(os.environ["TEST_KEY_D"], "")  # .env.example の「KEY=   # 説明」が説明文を値にしない
            for k in ("TEST_KEY_A", "TEST_KEY_B", "TEST_KEY_C", "TEST_KEY_D"):
                os.environ.pop(k, None)


class TestPasswordHash(unittest.TestCase):
    def test_works_without_hashlib_scrypt(self):
        """Macの標準Python(3.9, LibreSSL)には hashlib.scrypt が無い。その環境でも動くこと"""
        import hashlib
        from app import hash_password, verify_password
        saved = getattr(hashlib, "scrypt", None)
        if saved is not None:
            del hashlib.scrypt
        try:
            h = hash_password("secret-pass")
            self.assertTrue(h.startswith("pbkdf2$600000$"))
            self.assertTrue(verify_password("secret-pass", h))
            self.assertFalse(verify_password("wrong", h))
            self.assertFalse(verify_password("x", "scrypt$00$00"))  # scryptが無い環境では旧形式は検証不可（クラッシュしない）
        finally:
            if saved is not None:
                hashlib.scrypt = saved

    def test_legacy_scrypt_hash_still_verifies_when_available(self):
        import hashlib
        import os
        from app import verify_password
        if not hasattr(hashlib, "scrypt"):
            self.skipTest("scrypt not available")
        salt = os.urandom(16)
        h = hashlib.scrypt(b"old-pass", salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
        self.assertTrue(verify_password("old-pass", f"scrypt${salt.hex()}${h.hex()}"))
        self.assertFalse(verify_password("nope", f"scrypt${salt.hex()}${h.hex()}"))

    def test_tampered_hash_is_rejected(self):
        from app import verify_password
        for bad in ("", "pbkdf2$", "pbkdf2$99999999999$00$00", "pbkdf2$0$00$00", "garbage", "pbkdf2$600000$zz$00"):
            self.assertFalse(verify_password("x", bad))
