// 管理者向けの集計。正答率は「最初の本文での正誤（stage=1）」で数える。
// D1 の無料枠は「読んだ行数」にも上限があるため、集計は「直近N日」（既定30日）に限って読む。
import { intEnv } from "./util.js";

const all = async (stmt) => (await stmt.all()).results;
const off = (env) => {
  const h = intEnv(env.TZ_OFFSET_HOURS, 9);
  return `${h >= 0 ? "+" : "-"}${Math.abs(h)} hours`;
};
export const clampDays = (v, d = 30) => {
  const n = Number(v);
  return Number.isFinite(n) && n >= 1 ? Math.min(Math.floor(n), 3650) : d;
};
const since = (days) => `-${days} days`;
const pct = (ok, n) => (n ? Math.round((ok * 1000) / n) / 10 : null);

export async function overview(db, env, days) {
  const w = since(days);
  const [stu, act, rnd, s1, ai, fb, qt] = await Promise.all([
    db.prepare("SELECT COUNT(*) AS n FROM users WHERE role='student' AND is_active=1").first(),
    db.prepare("SELECT COUNT(DISTINCT user_id) AS n FROM attempts WHERE created_at>=datetime('now',?)").bind(w).first(),
    db.prepare("SELECT COUNT(DISTINCT round_id) AS n FROM attempts WHERE created_at>=datetime('now',?)").bind(w).first(),
    db.prepare("SELECT COUNT(*) AS n, COALESCE(SUM(is_correct),0) AS ok FROM attempts WHERE created_at>=datetime('now',?) AND stage=1").bind(w).first(),
    db.prepare("SELECT COUNT(*) AS n, COALESCE(SUM(ok),0) AS ok FROM ai_logs WHERE created_at>=datetime('now',?)").bind(w).first(),
    db.prepare("SELECT COUNT(*) AS n FROM attempts WHERE created_at>=datetime('now',?) AND judged_by='fallback'").bind(w).first(),
    db.prepare("SELECT COALESCE(SUM(total),0) AS n FROM levels").first(),
  ]);
  return {
    window_days: days,
    students: stu.n, active_students: act.n, rounds_answered: rnd.n,
    accuracy: pct(s1.ok, s1.n), answered: s1.n, questions_total: qt.n,
    ai_calls: ai.n, ai_ok_rate: pct(ai.ok, ai.n), fallback_judgments: fb.n,
    ai_config: { mode: env.AI_MODE || "gemini", model: env.GEMINI_MODEL || "gemini-flash-latest", key_set: !!env.GEMINI_API_KEY },
  };
}

export async function students(db, env, days) {
  const users = await all(db.prepare("SELECT id, login_id, label, is_active, created_at FROM users WHERE role='student' ORDER BY login_id"));
  const rows = await all(db.prepare(
    `SELECT user_id, MAX(created_at) AS last_active, COUNT(DISTINCT date(created_at,?)) AS days_n, COUNT(DISTINCT round_id) AS rounds_n,
            SUM(CASE WHEN stage=1 THEN 1 ELSE 0 END) AS s1_n, SUM(CASE WHEN stage=1 THEN is_correct ELSE 0 END) AS s1_ok
     FROM attempts WHERE created_at>=datetime('now',?) GROUP BY user_id`).bind(off(env), since(days)));
  const m = new Map(rows.map((r) => [r.user_id, r]));
  return users.map((u) => {
    const r = m.get(u.id);
    return { ...u, last_active: r ? r.last_active : null, days_n: r ? r.days_n : 0, rounds_n: r ? r.rounds_n : 0, s1_n: r ? r.s1_n : 0, accuracy: r ? pct(r.s1_ok, r.s1_n) : null };
  });
}

export async function studentDetail(db, env, uid, days) {
  const u = await db.prepare("SELECT id, login_id, label, is_active FROM users WHERE id=? AND role='student'").bind(uid).first();
  if (!u) return null;
  const w = since(days);
  const byLevel = (await all(db.prepare(
    `SELECT l.name AS level, l.order_no, a.direction, COUNT(*) AS n, SUM(a.is_correct) AS ok
     FROM attempts a JOIN questions q ON q.id=a.question_id JOIN levels l ON l.id=q.level_id
     WHERE a.user_id=? AND a.stage=1 AND a.created_at>=datetime('now',?) GROUP BY l.id, a.direction ORDER BY l.order_no, a.direction`).bind(uid, w)))
    .map((b) => ({ ...b, accuracy: pct(b.ok, b.n) }));
  const weak = await all(db.prepare(
    `SELECT l.name AS level, q.seq, a.direction, q.en_text, q.ja_text, COUNT(*) AS wrong_n, MAX(a.created_at) AS last_wrong
     FROM attempts a JOIN questions q ON q.id=a.question_id JOIN levels l ON l.id=q.level_id
     WHERE a.user_id=? AND a.stage=1 AND a.is_correct=0 AND a.created_at>=datetime('now',?)
     GROUP BY a.question_id, a.direction ORDER BY wrong_n DESC, last_wrong DESC LIMIT 30`).bind(uid, w));
  const recent = await all(db.prepare(
    `SELECT r.id, l.name AS level, r.direction, r.start_seq, r.stage, r.created_at,
       (SELECT COUNT(*) FROM round_questions rq WHERE rq.round_id=r.id) AS n,
       (SELECT COALESCE(SUM(rq.s1_ok),0) FROM round_questions rq WHERE rq.round_id=r.id) AS ok1
     FROM rounds r JOIN levels l ON l.id=r.level_id WHERE r.user_id=? ORDER BY r.id DESC LIMIT 20`).bind(uid));
  return { window_days: days, user: u, by_level: byLevel, weak, recent_rounds: recent };
}

export async function hardestQuestions(db, days, minAttempts = 3, limit = 30) {
  const rows = await all(db.prepare(
    `SELECT l.name AS level, q.seq, a.direction, q.en_text, q.ja_text, COUNT(*) AS n, SUM(a.is_correct) AS ok, COUNT(DISTINCT a.user_id) AS students
     FROM attempts a JOIN questions q ON q.id=a.question_id JOIN levels l ON l.id=q.level_id
     WHERE a.stage=1 AND a.created_at>=datetime('now',?) GROUP BY a.question_id, a.direction HAVING n>=?
     ORDER BY (ok*1.0/n) ASC, n DESC LIMIT ?`).bind(since(days), minAttempts, limit));
  return rows.map((r) => ({ ...r, accuracy: pct(r.ok, r.n) }));
}

export async function usage(db, env, days) {
  return all(db.prepare(
    `SELECT date(created_at,?) AS day, COUNT(DISTINCT user_id) AS students, COUNT(*) AS attempts
     FROM attempts WHERE created_at>=datetime('now',?) GROUP BY day ORDER BY day`).bind(off(env), since(days)));
}

export const questionCounts = (db) => all(db.prepare("SELECT code, name, order_no, total AS n FROM levels ORDER BY order_no"));

const csvCell = (v) => {
  let s = v === null || v === undefined ? "" : String(v);
  if (/^[=+\-@\t\r]/.test(s)) s = "'" + s; // Excelで数式として実行されないようにする
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};
export const CSV_HEADER = ["日時", "ID", "表示名", "級・段", "方向", "問題番号", "段階(1本文/2空欄/3確認)", "問題", "正解", "生徒の回答", "判定", "採点者(exact/ai/fallback)"];

/** 回答ログを、id順に limit 件ずつ取り出す（大きなCSVを1回で作らないため） */
export async function exportAttempts(db, env, afterId, limit) {
  const rows = await all(db.prepare(
    `SELECT a.id, datetime(a.created_at,?) AS t, u.login_id, u.label, l.name AS level, a.direction, q.seq, a.stage,
            a.prompt_text, a.expected_text, a.answer_text, a.verdict, a.judged_by
     FROM attempts a JOIN users u ON u.id=a.user_id JOIN questions q ON q.id=a.question_id JOIN levels l ON l.id=q.level_id
     WHERE a.id>? ORDER BY a.id LIMIT ?`).bind(off(env), afterId, limit));
  const csv = rows.map((r) => [r.t, r.login_id, r.label, r.level, r.direction, r.seq, r.stage, r.prompt_text, r.expected_text, r.answer_text, r.verdict, r.judged_by].map(csvCell).join(",")).join("\n");
  return { csv, count: rows.length, last_id: rows.length ? rows[rows.length - 1].id : afterId };
}
