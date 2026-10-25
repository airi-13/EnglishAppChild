import os
import sqlite3
import tempfile

from app import create_app, db, hash_password
from tools.make_sample_csv import write_csv

_CSV = None


def sample_csv_path():
    global _CSV
    if _CSV is None:
        _CSV = os.path.join(tempfile.gettempdir(), "eigo_sample_1600.csv")
        write_csv(_CSV)
    return _CSV


def make_app(tmpdir, **overrides):
    cfg = {"DB_PATH": os.path.join(tmpdir, "t.db"), "AI_MODE": "mock", "AI_MAX_RPM": 0}
    cfg.update(overrides)
    app = create_app(cfg)
    return app, cfg["DB_PATH"]


def seed(path, with_questions=True):
    c = db.connect(path)
    c.execute("INSERT OR IGNORE INTO users(login_id,password_hash,role,label) VALUES ('admin',?, 'admin','管理者')", (hash_password("adminpass1"),))
    c.close()
    if with_questions:
        from app import questions_io
        c = db.connect(path)
        with open(sample_csv_path(), encoding="utf-8-sig") as f:
            res = questions_io.import_csv(c, f.read())
        c.close()
        assert res["ok"], res


class Client:
    """Flask test client の薄いラッパー（JSON専用）"""

    def __init__(self, app):
        self.c = app.test_client()

    def req(self, method, url, body=None, headers=None):
        kw = {"headers": headers or {}}
        if body is not None or method != "GET":
            kw["json"] = body if body is not None else {}
        r = getattr(self.c, method.lower())(url, **kw)
        try:
            data = r.get_json()
        except Exception:
            data = None
        return r.status_code, data, r

    def get(self, url):
        return self.req("GET", url)

    def post(self, url, body=None, headers=None):
        return self.req("POST", url, body if body is not None else {}, headers)

    def login(self, lid, pw):
        return self.post("/api/login", {"login_id": lid, "password": pw})


def rq_truth(path, round_id):
    """rq_id -> 問題行（正解を得るためのテスト専用ヘルパー）"""
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    rows = c.execute("""SELECT rq.id rq_id, q.* FROM round_questions rq JOIN questions q ON q.id=rq.question_id WHERE rq.round_id=?""", (round_id,)).fetchall()
    c.close()
    return {r["rq_id"]: dict(r) for r in rows}
