import os
import sqlite3
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY,
  login_id TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('admin','student')),
  label TEXT NOT NULL DEFAULT '',
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions(
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS login_attempts(
  id INTEGER PRIMARY KEY,
  login_id TEXT NOT NULL,
  ip TEXT NOT NULL,
  ts INTEGER NOT NULL,
  ok INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_login_attempts ON login_attempts(login_id, ts);
CREATE TABLE IF NOT EXISTS levels(
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL CHECK(kind IN ('kyu','dan')),
  order_no INTEGER NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS questions(
  id INTEGER PRIMARY KEY,
  level_id INTEGER NOT NULL REFERENCES levels(id),
  seq INTEGER NOT NULL CHECK(seq BETWEEN 1 AND 999),
  en_text TEXT NOT NULL,
  ja_text TEXT NOT NULL,
  target_en TEXT NOT NULL,
  target_ja TEXT NOT NULL,
  grammar_note TEXT NOT NULL DEFAULT '',
  UNIQUE(level_id, seq)
);
CREATE TABLE IF NOT EXISTS progress(
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  level_id INTEGER NOT NULL REFERENCES levels(id),
  direction TEXT NOT NULL CHECK(direction IN ('en2ja','ja2en')),
  next_seq INTEGER NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY(user_id, level_id, direction)
);
CREATE TABLE IF NOT EXISTS rounds(
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  level_id INTEGER NOT NULL REFERENCES levels(id),
  direction TEXT NOT NULL CHECK(direction IN ('en2ja','ja2en')),
  start_seq INTEGER NOT NULL,
  stage TEXT NOT NULL CHECK(stage IN ('answer','fillin','review','done','abandoned')),
  summary_json TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_rounds_user ON rounds(user_id, stage);
CREATE TABLE IF NOT EXISTS round_questions(
  id INTEGER PRIMARY KEY,
  round_id INTEGER NOT NULL REFERENCES rounds(id) ON DELETE CASCADE,
  question_id INTEGER NOT NULL REFERENCES questions(id),
  position INTEGER NOT NULL,
  s1_ok INTEGER, s1_answer TEXT,
  s2_ok INTEGER, s2_answer TEXT, s2_feedback TEXT,
  explanation TEXT, test_prompt TEXT, test_answer TEXT,
  s3_ok INTEGER, s3_answer TEXT, s3_feedback TEXT
);
CREATE INDEX IF NOT EXISTS ix_rq_round ON round_questions(round_id);
CREATE TABLE IF NOT EXISTS attempts(
  id INTEGER PRIMARY KEY,
  round_id INTEGER NOT NULL REFERENCES rounds(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  question_id INTEGER NOT NULL REFERENCES questions(id),
  direction TEXT NOT NULL,
  stage INTEGER NOT NULL CHECK(stage IN (1,2,3)),
  prompt_text TEXT NOT NULL,
  expected_text TEXT NOT NULL,
  answer_text TEXT NOT NULL,
  verdict TEXT NOT NULL CHECK(verdict IN ('correct','close','wrong')),
  is_correct INTEGER NOT NULL,
  judged_by TEXT NOT NULL CHECK(judged_by IN ('exact','ai','fallback')),
  feedback_text TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_att_user ON attempts(user_id, created_at);
CREATE INDEX IF NOT EXISTS ix_att_q ON attempts(question_id, stage);
CREATE INDEX IF NOT EXISTS ix_att_round ON attempts(round_id);
CREATE TABLE IF NOT EXISTS ai_logs(
  id INTEGER PRIMARY KEY,
  purpose TEXT NOT NULL,
  model TEXT NOT NULL,
  ok INTEGER NOT NULL,
  latency_ms INTEGER NOT NULL,
  error TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_ai_logs ON ai_logs(created_at);
"""


def connect(path):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def tx(conn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def level_defs():
    out = []
    for i, n in enumerate(range(20, 0, -1), 1):
        out.append((f"k{n}", f"{n}級", "kyu", i))
    for n in range(1, 21):
        out.append((f"d{n}", "初段" if n == 1 else f"{n}段", "dan", 20 + n))
    return out


def init_db(conn):
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM levels").fetchone()[0] == 0:
        with tx(conn):
            conn.executemany("INSERT INTO levels(code,name,kind,order_no) VALUES (?,?,?,?)", level_defs())
