"""管理者向けの集計。正答率は「最初の本文での正誤（stage=1）」で数える。"""
import csv
import io


def _off(cfg):
    h = cfg["TZ_OFFSET_HOURS"]
    return f"{'+' if h >= 0 else '-'}{abs(h)} hours"


def overview(conn, cfg):
    off = _off(cfg)
    one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]
    students = one("SELECT COUNT(*) FROM users WHERE role='student' AND is_active=1")
    s1_all = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(is_correct),0) ok FROM attempts WHERE stage=1").fetchone()
    s1_7 = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(is_correct),0) ok FROM attempts WHERE stage=1 AND created_at>=datetime('now','-7 days')").fetchone()
    ai7 = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(ok),0) ok FROM ai_logs WHERE created_at>=datetime('now','-7 days')").fetchone()
    return {
        "students": students,
        "active_students_7d": one("SELECT COUNT(DISTINCT user_id) FROM attempts WHERE created_at>=datetime('now','-7 days')"),
        "rounds_done_7d": one("SELECT COUNT(*) FROM rounds WHERE stage='done' AND finished_at>=datetime('now','-7 days')"),
        "accuracy_all": round(s1_all["ok"] * 100 / s1_all["n"], 1) if s1_all["n"] else None,
        "accuracy_7d": round(s1_7["ok"] * 100 / s1_7["n"], 1) if s1_7["n"] else None,
        "questions_total": one("SELECT COUNT(*) FROM questions"),
        "ai_calls_7d": ai7["n"], "ai_ok_rate_7d": round(ai7["ok"] * 100 / ai7["n"], 1) if ai7["n"] else None,
        "fallback_judgments_7d": one("SELECT COUNT(*) FROM attempts WHERE judged_by='fallback' AND created_at>=datetime('now','-7 days')"),
    }


def students(conn, cfg):
    off = _off(cfg)
    rows = conn.execute(
        """SELECT u.id, u.login_id, u.label, u.is_active, u.created_at,
             (SELECT MAX(created_at) FROM attempts a WHERE a.user_id=u.id) AS last_active,
             (SELECT COUNT(DISTINCT date(created_at, ?)) FROM attempts a WHERE a.user_id=u.id AND a.created_at>=datetime('now','-30 days')) AS days_30d,
             (SELECT COUNT(*) FROM rounds r WHERE r.user_id=u.id AND r.stage='done') AS rounds_done,
             (SELECT COUNT(*) FROM attempts a WHERE a.user_id=u.id AND a.stage=1) AS s1_n,
             (SELECT COALESCE(SUM(is_correct),0) FROM attempts a WHERE a.user_id=u.id AND a.stage=1) AS s1_ok
           FROM users u WHERE u.role='student' ORDER BY u.login_id""", (off,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["accuracy"] = round(d["s1_ok"] * 100 / d["s1_n"], 1) if d["s1_n"] else None
        out.append(d)
    return out


def student_detail(conn, cfg, uid):
    u = conn.execute("SELECT id, login_id, label, is_active FROM users WHERE id=? AND role='student'", (uid,)).fetchone()
    if not u:
        return None
    by_level = [dict(r) for r in conn.execute(
        """SELECT l.name AS level, l.order_no, a.direction, COUNT(*) AS n, SUM(a.is_correct) AS ok
           FROM attempts a JOIN questions q ON q.id=a.question_id JOIN levels l ON l.id=q.level_id
           WHERE a.user_id=? AND a.stage=1 GROUP BY l.id, a.direction ORDER BY l.order_no, a.direction""", (uid,))]
    for b in by_level:
        b["accuracy"] = round(b["ok"] * 100 / b["n"], 1)
    weak = [dict(r) for r in conn.execute(
        """SELECT l.name AS level, q.seq, a.direction, q.en_text, q.ja_text, COUNT(*) AS wrong_n, MAX(a.created_at) AS last_wrong
           FROM attempts a JOIN questions q ON q.id=a.question_id JOIN levels l ON l.id=q.level_id
           WHERE a.user_id=? AND a.stage=1 AND a.is_correct=0 GROUP BY a.question_id, a.direction
           ORDER BY wrong_n DESC, last_wrong DESC LIMIT 30""", (uid,))]
    recent = [dict(r) for r in conn.execute(
        """SELECT r.id, l.name AS level, r.direction, r.start_seq, r.stage, r.created_at,
             (SELECT COUNT(*) FROM round_questions rq WHERE rq.round_id=r.id) AS n,
             (SELECT COALESCE(SUM(rq.s1_ok),0) FROM round_questions rq WHERE rq.round_id=r.id) AS ok1
           FROM rounds r JOIN levels l ON l.id=r.level_id WHERE r.user_id=? ORDER BY r.id DESC LIMIT 20""", (uid,))]
    return {"user": dict(u), "by_level": by_level, "weak": weak, "recent_rounds": recent}


def hardest_questions(conn, min_attempts=3, limit=30):
    rows = conn.execute(
        """SELECT l.name AS level, q.seq, a.direction, q.en_text, q.ja_text, COUNT(*) AS n, SUM(a.is_correct) AS ok,
                  COUNT(DISTINCT a.user_id) AS students
           FROM attempts a JOIN questions q ON q.id=a.question_id JOIN levels l ON l.id=q.level_id
           WHERE a.stage=1 GROUP BY a.question_id, a.direction HAVING n>=?
           ORDER BY (ok*1.0/n) ASC, n DESC LIMIT ?""", (min_attempts, limit)).fetchall()
    return [dict(r, accuracy=round(r["ok"] * 100 / r["n"], 1)) for r in rows]


def usage(conn, cfg, days=30):
    off = _off(cfg)
    rows = conn.execute(
        """SELECT date(created_at, ?) AS day, COUNT(DISTINCT user_id) AS students, COUNT(*) AS attempts
           FROM attempts WHERE created_at>=datetime('now', ?) GROUP BY day ORDER BY day""", (off, f"-{int(days)} days")).fetchall()
    return [dict(r) for r in rows]


def question_counts(conn):
    return [dict(r) for r in conn.execute(
        """SELECT l.code, l.name, l.order_no, COUNT(q.id) AS n FROM levels l LEFT JOIN questions q ON q.level_id=l.id GROUP BY l.id ORDER BY l.order_no""")]


def attempts_csv(conn, cfg):
    off = _off(cfg)
    rows = conn.execute(
        """SELECT datetime(a.created_at, ?) AS t, u.login_id, u.label, l.name AS level, a.direction, q.seq, a.stage,
                  a.prompt_text, a.expected_text, a.answer_text, a.verdict, a.judged_by
           FROM attempts a JOIN users u ON u.id=a.user_id JOIN questions q ON q.id=a.question_id JOIN levels l ON l.id=q.level_id
           ORDER BY a.id""", (off,))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["日時", "ID", "表示名", "級・段", "方向", "問題番号", "段階(1本文/2空欄/3確認)", "問題", "正解", "生徒の回答", "判定", "採点者(exact/ai/fallback)"])
    for r in rows:
        w.writerow([_safe(x) for x in tuple(r)])
    return "\ufeff" + buf.getvalue()


def _safe(v):
    """CSVをExcelで開いたときの数式実行を防ぐ"""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v
