// データベースの定義（D1 = SQLite）。無料枠では「書き込んだ行数」も制限されるため、索引は必要最小限にしている。
export const SCHEMA = [
  `CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY, login_id TEXT NOT NULL UNIQUE COLLATE NOCASE, password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin','student')), label TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT (datetime('now')))`,
  `CREATE TABLE IF NOT EXISTS sessions(
    token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL)`,
  `CREATE TABLE IF NOT EXISTS login_attempts(id INTEGER PRIMARY KEY, login_id TEXT NOT NULL, ip TEXT NOT NULL, ts INTEGER NOT NULL, ok INTEGER NOT NULL)`,
  `CREATE INDEX IF NOT EXISTS ix_login_attempts ON login_attempts(login_id, ts)`,
  `CREATE TABLE IF NOT EXISTS levels(
    id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK(kind IN ('kyu','dan')), order_no INTEGER NOT NULL UNIQUE,
    total INTEGER NOT NULL DEFAULT 0, max_seq INTEGER NOT NULL DEFAULT 0)`,
  `CREATE TABLE IF NOT EXISTS questions(
    id INTEGER PRIMARY KEY, level_id INTEGER NOT NULL REFERENCES levels(id),
    seq INTEGER NOT NULL CHECK(seq BETWEEN 1 AND 999), en_text TEXT NOT NULL, ja_text TEXT NOT NULL,
    target_en TEXT NOT NULL, target_ja TEXT NOT NULL, grammar_note TEXT NOT NULL DEFAULT '',
    UNIQUE(level_id, seq))`,
  `CREATE TABLE IF NOT EXISTS progress(
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, level_id INTEGER NOT NULL REFERENCES levels(id),
    direction TEXT NOT NULL CHECK(direction IN ('en2ja','ja2en')), next_seq INTEGER NOT NULL,
    PRIMARY KEY(user_id, level_id, direction))`,
  `CREATE TABLE IF NOT EXISTS rounds(
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    level_id INTEGER NOT NULL REFERENCES levels(id), direction TEXT NOT NULL CHECK(direction IN ('en2ja','ja2en')),
    start_seq INTEGER NOT NULL, stage TEXT NOT NULL CHECK(stage IN ('answer','fillin','review','done','abandoned')),
    summary_json TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')), finished_at TEXT)`,
  `CREATE INDEX IF NOT EXISTS ix_rounds_user ON rounds(user_id, stage)`,
  `CREATE TABLE IF NOT EXISTS round_questions(
    id INTEGER PRIMARY KEY, round_id INTEGER NOT NULL REFERENCES rounds(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL REFERENCES questions(id), position INTEGER NOT NULL,
    s1_ok INTEGER, s1_answer TEXT, s2_ok INTEGER, s2_answer TEXT, s2_feedback TEXT,
    explanation TEXT, test_prompt TEXT, test_answer TEXT, s3_ok INTEGER, s3_answer TEXT, s3_feedback TEXT)`,
  `CREATE INDEX IF NOT EXISTS ix_rq_round ON round_questions(round_id)`,
  `CREATE TABLE IF NOT EXISTS attempts(
    id INTEGER PRIMARY KEY, round_id INTEGER NOT NULL REFERENCES rounds(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, question_id INTEGER NOT NULL REFERENCES questions(id),
    direction TEXT NOT NULL, stage INTEGER NOT NULL CHECK(stage IN (1,2,3)), prompt_text TEXT NOT NULL,
    expected_text TEXT NOT NULL, answer_text TEXT NOT NULL, verdict TEXT NOT NULL CHECK(verdict IN ('correct','close','wrong')),
    is_correct INTEGER NOT NULL, judged_by TEXT NOT NULL CHECK(judged_by IN ('exact','ai','fallback')),
    feedback_text TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT (datetime('now')))`,
  `CREATE INDEX IF NOT EXISTS ix_att_user ON attempts(user_id, created_at)`,
  `CREATE INDEX IF NOT EXISTS ix_att_created ON attempts(created_at)`,
  `CREATE TABLE IF NOT EXISTS ai_logs(
    id INTEGER PRIMARY KEY, purpose TEXT NOT NULL, model TEXT NOT NULL, ok INTEGER NOT NULL,
    latency_ms INTEGER NOT NULL, error TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')))`,
  `CREATE INDEX IF NOT EXISTS ix_ai_logs ON ai_logs(created_at)`,
];

export function levelDefs() {
  const out = [];
  let i = 1;
  for (let n = 20; n >= 1; n--) out.push([`k${n}`, `${n}級`, "kyu", i++]);
  for (let n = 1; n <= 20; n++) out.push([`d${n}`, n === 1 ? "初段" : `${n}段`, "dan", 20 + n]);
  return out;
}

let ready = null; // 起動ごとに1回だけ確認する
export function ensureSchema(db) {
  if (!ready) {
    ready = (async () => {
      const t = await db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='ai_logs'").first();
      if (!t) await db.batch(SCHEMA.map((s) => db.prepare(s)));
      const n = await db.prepare("SELECT COUNT(*) AS n FROM levels").first();
      if (!n || n.n === 0) {
        await db.batch(levelDefs().map((l) => db.prepare("INSERT OR IGNORE INTO levels(code,name,kind,order_no) VALUES (?,?,?,?)").bind(...l)));
      }
    })().catch((e) => {
      ready = null;
      throw e;
    });
  }
  return ready;
}
export const resetSchemaCache = () => { ready = null; };
