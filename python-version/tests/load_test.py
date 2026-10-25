"""負荷テスト: 生徒N人が同時に1セット（5問）を最後まで解く。実サーバー(HTTP)＋AIはモック(遅延あり)。
使い方: python -m tests.load_test [人数] [セット数/人]"""
import http.cookiejar
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.request

from tests.helpers import make_app, seed, rq_truth

N = int(sys.argv[1]) if len(sys.argv) > 1 else 30
ROUNDS = int(sys.argv[2]) if len(sys.argv) > 2 else 2


class Http:
    def __init__(self, base):
        self.base = base
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.lat = []

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else (b"{}" if method == "POST" else None)
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={"Content-Type": "application/json"})
        t0 = time.time()
        try:
            with self.op.open(req, timeout=60) as r:
                out = json.loads(r.read())
        finally:
            self.lat.append(time.time() - t0)
        return out


def student(i, base, path, errors, lat, done):
    try:
        c = Http(base)
        c.call("POST", "/api/login", {"login_id": f"load{i:03d}", "password": "pass1234"})
        lv = ["k20", "k15", "k10", "d1", "d10"][i % 5]
        direction = "en2ja" if i % 2 == 0 else "ja2en"
        seq = 1
        for _ in range(ROUNDS):
            c.call("GET", "/api/levels")
            r = c.call("POST", "/api/rounds", {"level_code": lv, "direction": direction, "start_seq": seq})
            v = c.call("GET", f"/api/rounds/{r['round_id']}")
            t = rq_truth(path, v["id"])
            key = "ja_text" if direction == "en2ja" else "en_text"
            ans = {str(it["rq_id"]): t[it["rq_id"]][key] for it in v["items"]}
            if i % 3 == 0:  # 3人に1人は2問間違える → 空欄補充・解説・確認テストまで通る
                for k in list(ans)[:2]:
                    ans[k] = "ちがう答え"
            v = c.call("POST", f"/api/rounds/{v['id']}/answers", {"answers": ans})["view"]
            if v["stage"] == "fillin":
                v = c.call("POST", f"/api/rounds/{v['id']}/answers", {"answers": {str(it["rq_id"]): "wrong" for it in v["items"]}})["view"]
            if v["stage"] == "review":
                v = c.call("POST", f"/api/rounds/{v['id']}/answers", {"answers": {str(it["rq_id"]): "x" for it in v["items"]}})["view"]
            assert v["stage"] == "done", v["stage"]
            seq = v["next"]["start_seq"]
            done.append(1)
        lat.extend(c.lat)
    except Exception as e:  # noqa
        errors.append(f"student {i}: {type(e).__name__}: {e}")


def main():
    from werkzeug.serving import make_server
    tmp = tempfile.TemporaryDirectory()
    app, path = make_app(tmp.name, MOCK_LATENCY=0.3)
    seed(path)
    from app import db, hash_password
    c = db.connect(path)
    pw = hash_password("pass1234")
    c.executemany("INSERT INTO users(login_id,password_hash,role,label) VALUES (?,?, 'student', ?)", [(f"load{i:03d}", pw, f"L{i}") for i in range(N)])
    c.close()
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    base = f"http://127.0.0.1:{srv.server_port}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    errors, lat, done = [], [], []
    ths = [threading.Thread(target=student, args=(i, base, path, errors, lat, done)) for i in range(N)]
    t0 = time.time()
    [t.start() for t in ths]
    [t.join() for t in ths]
    wall = time.time() - t0
    srv.shutdown()
    con = sqlite3.connect(path)
    integ = con.execute("PRAGMA integrity_check").fetchone()[0]
    fk = con.execute("PRAGMA foreign_key_check").fetchall()
    rounds = con.execute("SELECT COUNT(*) FROM rounds WHERE stage='done'").fetchone()[0]
    stuck = con.execute("SELECT COUNT(*) FROM rounds WHERE stage NOT IN ('done')").fetchone()[0]
    a1 = con.execute("SELECT COUNT(*) FROM attempts WHERE stage=1").fetchone()[0]
    dup = con.execute("SELECT COUNT(*) FROM (SELECT round_id, question_id, stage FROM attempts GROUP BY 1,2,3 HAVING COUNT(*)>1)").fetchone()[0]
    ailog = con.execute("SELECT COUNT(*), SUM(ok) FROM ai_logs").fetchone()
    lat.sort()
    p = lambda q: lat[int(len(lat) * q) - 1] if lat else 0
    print(f"生徒{N}人 x {ROUNDS}セット / 所要{wall:.1f}秒 / リクエスト{len(lat)}件")
    print(f"  エラー: {len(errors)}  完了セット: {rounds}/{N * ROUNDS}  未完了: {stuck}  stage1回答数: {a1}（期待 {N * ROUNDS * 5}）  重複記録: {dup}")
    print(f"  応答時間 p50={p(.5):.2f}s p95={p(.95):.2f}s max={lat[-1]:.2f}s   AI呼び出し: {ailog[0]}件 成功{ailog[1]}")
    print(f"  DB integrity_check={integ} foreign_key_violations={len(fk)}")
    for e in errors[:5]:
        print("  ", e)
    ok = not errors and rounds == N * ROUNDS and not stuck and a1 == N * ROUNDS * 5 and not dup and integ == "ok" and not fk
    print("結果:", "合格" if ok else "不合格")
    tmp.cleanup()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
