"""出題フローの本体。流れ:
  answer(本文) → fillin(間違えた問題だけ空欄補充) → review(それでも間違えた問題の解説＋確認テスト) → done(まとめ)
状態管理と記録はすべてこのプログラムが行い、AIは「採点・補足・解説・確認テスト作成」だけを担当する。"""
import json
import re

from . import ai as aitasks
from .db import tx
from .textnorm import same

PER_ROUND = 5
MAX_ANSWER_LEN = 200
BLANK = "＿＿＿"
DIRECTIONS = {"en2ja": "英日（英語→日本語）", "ja2en": "日英（日本語→英語）"}
ACTIVE_STAGES = ("answer", "fillin", "review")


class ApiError(Exception):
    def __init__(self, status, message, **extra):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


# ------------------------------------------------------------ 問題文の組み立て
def ans_lang(direction):
    return "ja" if direction == "en2ja" else "en"


def source_sentence(q, d):
    return q["en_text"] if d == "en2ja" else q["ja_text"]


def expected_sentence(q, d):
    return q["ja_text"] if d == "en2ja" else q["en_text"]


def expected_word(q, d):
    return q["target_ja"] if d == "en2ja" else q["target_en"]


def _word_pattern(word):
    return re.compile(r"(?<![A-Za-z])" + re.escape(word) + r"(?![A-Za-z])", re.I)


def blank_sentence(q, d):
    if d == "en2ja":
        return q["ja_text"].replace(q["target_ja"], BLANK, 1)
    return _word_pattern(q["target_en"]).sub(BLANK, q["en_text"], count=1)


def target_in_sentence(en, ja, t_en, t_ja):
    return bool(_word_pattern(t_en).search(en)) and t_ja in ja


# ------------------------------------------------------------ 取得系
def _rqs(conn, round_id):
    return conn.execute(
        """SELECT rq.*, q.seq, q.en_text, q.ja_text, q.target_en, q.target_ja, q.grammar_note
           FROM round_questions rq JOIN questions q ON q.id=rq.question_id
           WHERE rq.round_id=? ORDER BY rq.position""", (round_id,)).fetchall()


def _round(conn, round_id, user_id):
    r = conn.execute("SELECT r.*, l.code AS level_code, l.name AS level_name, l.order_no, l.id AS lid FROM rounds r JOIN levels l ON l.id=r.level_id WHERE r.id=?", (round_id,)).fetchone()
    if not r or r["user_id"] != user_id:
        raise ApiError(404, "問題が見つかりません")
    return r


def active_round_id(conn, user_id):
    r = conn.execute("SELECT id FROM rounds WHERE user_id=? AND stage IN ('answer','fillin','review') ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
    return r["id"] if r else None


def levels_with_progress(conn, user_id):
    levels = [dict(r) for r in conn.execute(
        """SELECT l.id, l.code, l.name, l.kind, l.order_no,
                  (SELECT COUNT(*) FROM questions q WHERE q.level_id=l.id) AS total,
                  (SELECT MAX(seq) FROM questions q WHERE q.level_id=l.id) AS max_seq
           FROM levels l ORDER BY l.order_no""")]
    prog = {}
    for p in conn.execute("SELECT level_id, direction, next_seq FROM progress WHERE user_id=?", (user_id,)):
        prog[(p["level_id"], p["direction"])] = p["next_seq"]
    for l in levels:
        l["next"] = {d: prog.get((l["id"], d), 1) for d in DIRECTIONS}
        del l["id"]
    return levels


# ------------------------------------------------------------ 開始
def start_round(conn, user_id, level_code, direction, start_seq):
    active = active_round_id(conn, user_id)
    if active:
        return active, True
    if direction not in DIRECTIONS:
        raise ApiError(400, "英日か日英を選んでください")
    try:
        start_seq = int(start_seq)
    except (TypeError, ValueError):
        raise ApiError(400, "問題番号は数字で入力してください")
    if start_seq < 1:
        raise ApiError(400, "問題番号は1以上にしてください")
    lv = conn.execute("SELECT * FROM levels WHERE code=?", (level_code,)).fetchone()
    if not lv:
        raise ApiError(404, "級・段が見つかりません")
    qs = conn.execute("SELECT id FROM questions WHERE level_id=? AND seq>=? ORDER BY seq LIMIT ?", (lv["id"], start_seq, PER_ROUND)).fetchall()
    if not qs:
        raise ApiError(404, f"{lv['name']}の{start_seq}番以降の問題はまだありません")
    with tx(conn):
        if active_round_id(conn, user_id):  # 同時に2つ作られるのを防ぐ
            return active_round_id(conn, user_id), True
        cur = conn.execute("INSERT INTO rounds(user_id,level_id,direction,start_seq,stage) VALUES (?,?,?,?, 'answer')", (user_id, lv["id"], direction, start_seq))
        rid = cur.lastrowid
        conn.executemany("INSERT INTO round_questions(round_id,question_id,position) VALUES (?,?,?)", [(rid, q["id"], i) for i, q in enumerate(qs, 1)])
    return rid, False


def abandon_round(conn, round_id, user_id):
    r = _round(conn, round_id, user_id)
    if r["stage"] in ACTIVE_STAGES:
        with tx(conn):
            conn.execute("UPDATE rounds SET stage='abandoned', finished_at=datetime('now') WHERE id=? AND stage IN ('answer','fillin','review')", (round_id,))


# ------------------------------------------------------------ 画面用データ
def _next_info(conn, r, rqs):
    last = max(rq["seq"] for rq in rqs)
    mx = conn.execute("SELECT MAX(seq) FROM questions WHERE level_id=?", (r["level_id"],)).fetchone()[0]
    if last < mx:
        return {"level_code": r["level_code"], "level_name": r["level_name"], "start_seq": last + 1}
    nxt = conn.execute("""SELECT l.code, l.name FROM levels l WHERE l.order_no>? AND EXISTS(SELECT 1 FROM questions q WHERE q.level_id=l.id) ORDER BY l.order_no LIMIT 1""", (r["order_no"],)).fetchone()
    if nxt:
        return {"level_code": nxt["code"], "level_name": nxt["name"], "start_seq": 1, "level_up": True}
    return None


def build_view(conn, round_id, user_id):
    r = _round(conn, round_id, user_id)
    rqs = _rqs(conn, round_id)
    d = r["direction"]
    v = {"id": r["id"], "level_code": r["level_code"], "level_name": r["level_name"], "direction": d,
         "direction_label": DIRECTIONS[d], "answer_lang": ans_lang(d), "stage": r["stage"],
         "first_no": rqs[0]["seq"], "last_no": rqs[-1]["seq"], "items": []}
    if r["stage"] == "answer":
        v["items"] = [{"rq_id": q["id"], "no": q["seq"], "text": source_sentence(q, d)} for q in rqs]
    elif r["stage"] == "fillin":
        v["stage1"] = [{"no": q["seq"], "ok": bool(q["s1_ok"])} for q in rqs]
        v["items"] = [{"rq_id": q["id"], "no": q["seq"], "source": source_sentence(q, d), "blank": blank_sentence(q, d), "your_answer": q["s1_answer"]}
                      for q in rqs if q["s1_ok"] == 0]
    elif r["stage"] == "review":
        v["stage1"] = [{"no": q["seq"], "ok": bool(q["s1_ok"])} for q in rqs]
        v["fillin_results"] = [{"no": q["seq"], "ok": bool(q["s2_ok"]), "answer": q["s2_answer"], "feedback": q["s2_feedback"] or ""} for q in rqs if q["s1_ok"] == 0]
        v["items"] = [{"rq_id": q["id"], "no": q["seq"], "source": source_sentence(q, d), "your_answer": q["s1_answer"], "word_answer": q["s2_answer"],
                       "explanation": q["explanation"], "test_prompt": q["test_prompt"]} for q in rqs if q["s2_ok"] == 0]
    elif r["stage"] in ("done", "abandoned"):
        v["summary"] = json.loads(r["summary_json"]) if r["summary_json"] else None
        v["items"] = [{"no": q["seq"], "source": source_sentence(q, d), "expected": expected_sentence(q, d), "word": expected_word(q, d),
                       "s1_ok": q["s1_ok"], "s1_answer": q["s1_answer"], "s2_ok": q["s2_ok"], "s2_answer": q["s2_answer"], "s2_feedback": q["s2_feedback"],
                       "explanation": q["explanation"], "test_prompt": q["test_prompt"], "s3_ok": q["s3_ok"], "s3_answer": q["s3_answer"], "s3_feedback": q["s3_feedback"]} for q in rqs]
        if r["stage"] == "done":
            v["next"] = _next_info(conn, r, rqs)
    return v


# ------------------------------------------------------------ 回答の受付
def _collect(pend, answers, label):
    if not isinstance(answers, dict):
        raise ApiError(400, "回答の形式が正しくありません")
    clean, missing = {}, []
    for q in pend:
        a = answers.get(str(q["id"]))
        a = a.strip() if isinstance(a, str) else ""
        if not a:
            missing.append(q["seq"])
        elif len(a) > MAX_ANSWER_LEN:
            raise ApiError(400, f"No.{q['seq']}の答えが長すぎます（{MAX_ANSWER_LEN}文字まで）")
        else:
            clean[q["id"]] = a
    if missing:
        nums = "、".join(f"No.{n}" for n in missing)
        raise ApiError(400, f"{nums}にも答えてから送ってね", missing=missing)
    return clean


def _insert_attempt(conn, r, q, stage, prompt, expected, answer, verdict, by, fb=""):
    conn.execute(
        """INSERT INTO attempts(round_id,user_id,question_id,direction,stage,prompt_text,expected_text,answer_text,verdict,is_correct,judged_by,feedback_text)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (r["id"], r["user_id"], q["question_id"], r["direction"], stage, prompt, expected, answer, verdict, 1 if verdict == "correct" else 0, by, fb))


def _check_stage(conn, round_id, stage):
    cur = conn.execute("SELECT stage FROM rounds WHERE id=?", (round_id,)).fetchone()
    if not cur or cur["stage"] != stage:
        raise ApiError(409, "この問題はすでに送信されています。画面を更新してください")


def _finish(conn, r, points):
    rqs = _rqs(conn, r["id"])
    d = r["direction"]
    words = [{"no": q["seq"], "en": q["target_en"], "ja": q["target_ja"], "sentence_en": q["en_text"], "sentence_ja": q["ja_text"]} for q in rqs if q["s1_ok"] == 0]
    notes = [q["grammar_note"] for q in rqs if q["s1_ok"] == 0 and q["grammar_note"]]
    pts = list(points or [])
    if not pts and notes:
        pts = list(dict.fromkeys(notes))[:3]
    summary = {"perfect": not words, "points": pts, "words": words}
    conn.execute("UPDATE rounds SET stage='done', summary_json=?, finished_at=datetime('now') WHERE id=?", (json.dumps(summary, ensure_ascii=False), r["id"]))
    last = max(q["seq"] for q in rqs)
    conn.execute("""INSERT INTO progress(user_id,level_id,direction,next_seq) VALUES (?,?,?,?)
                    ON CONFLICT(user_id,level_id,direction) DO UPDATE SET next_seq=excluded.next_seq, updated_at=datetime('now')""",
                 (r["user_id"], r["level_id"], d, last + 1))


def submit(conn, client, round_id, user_id, answers):
    r = _round(conn, round_id, user_id)
    fn = {"answer": _submit_answer, "fillin": _submit_fillin, "review": _submit_confirm}.get(r["stage"])
    if not fn:
        raise ApiError(409, "この問題はもう終わっています")
    warnings = fn(conn, client, r, _rqs(conn, round_id), answers)
    return warnings


DEGRADED_MSG = "AIが混み合っているため、一部をかんたん採点にしました。先生には記録が残ります。"


def _submit_answer(conn, client, r, rqs, answers):
    d, lang = r["direction"], ans_lang(r["direction"])
    clean = _collect(rqs, answers, "answer")
    verdict, ai_items = {}, []
    for q in rqs:
        exp, a = expected_sentence(q, d), clean[q["id"]]
        if same(lang, a, exp):
            verdict[q["id"]] = ("correct", "", "exact")
        else:
            ai_items.append({"i": q["id"], "direction": d, "question": source_sentence(q, d), "expected": exp, "answer": a})
    degraded = False
    if ai_items:
        res, degraded = aitasks.judge_sentences(client, ai_items)
        for it, rs in zip(ai_items, res):
            verdict[it["i"]] = (rs["verdict"], rs["note"], rs["by"])
    any_wrong = any(v[0] != "correct" for v in verdict.values())
    with tx(conn):
        _check_stage(conn, r["id"], "answer")
        for q in rqs:
            v, note, by = verdict[q["id"]]
            _insert_attempt(conn, r, q, 1, source_sentence(q, d), expected_sentence(q, d), clean[q["id"]], v, by, note)
            conn.execute("UPDATE round_questions SET s1_ok=?, s1_answer=? WHERE id=?", (1 if v == "correct" else 0, clean[q["id"]], q["id"]))
        if any_wrong:
            conn.execute("UPDATE rounds SET stage='fillin' WHERE id=?", (r["id"],))
        else:
            _finish(conn, r, [])
    return [DEGRADED_MSG] if degraded else []


def _submit_fillin(conn, client, r, rqs, answers):
    d, lang = r["direction"], ans_lang(r["direction"])
    pend = [q for q in rqs if q["s1_ok"] == 0]
    clean = _collect(pend, answers, "fillin")
    items = []
    for q in pend:
        w = expected_word(q, d)
        items.append({"i": q["id"], "direction": d, "source": source_sentence(q, d), "blank": blank_sentence(q, d), "expected": w,
                      "answer": clean[q["id"]], "exact": same(lang, clean[q["id"]], w),
                      "supp_fallback": f"「{q['target_en']}」は「{q['target_ja']}」という意味だよ。"})
    res, points, degraded = aitasks.grade_fillin(client, items)
    wrong = [q for q, rs in zip(pend, res) if rs["verdict"] != "correct"]
    reviews = {}
    if wrong:
        r_items = []
        for q in wrong:
            r_items.append({"i": q["id"], "direction": d, "source_sentence": source_sentence(q, d), "expected_sentence": expected_sentence(q, d),
                            "expected_word": expected_word(q, d), "grammar_note": q["grammar_note"], "s1_answer": q["s1_answer"], "s2_answer": clean[q["id"]],
                            "fallback_explanation": f"正しい単語は「{expected_word(q, d)}」。正しい文は「{expected_sentence(q, d)}」だよ。" + (f" ポイント: {q['grammar_note']}" if q["grammar_note"] else "")})
        rev, deg2 = aitasks.review(client, r_items)
        degraded = degraded or deg2
        reviews = {q["id"]: rv for q, rv in zip(wrong, rev)}
    with tx(conn):
        _check_stage(conn, r["id"], "fillin")
        for q, rs in zip(pend, res):
            ok = rs["verdict"] == "correct"
            _insert_attempt(conn, r, q, 2, blank_sentence(q, d), expected_word(q, d), clean[q["id"]], rs["verdict"], rs["by"], rs["supplement"])
            conn.execute("UPDATE round_questions SET s2_ok=?, s2_answer=?, s2_feedback=? WHERE id=?", (1 if ok else 0, clean[q["id"]], rs["supplement"], q["id"]))
        if wrong:
            for q in wrong:
                rv = reviews[q["id"]]
                conn.execute("UPDATE round_questions SET explanation=?, test_prompt=?, test_answer=? WHERE id=?", (rv["explanation"], rv["test_prompt"], rv["test_answer"], q["id"]))
            conn.execute("UPDATE rounds SET stage='review' WHERE id=?", (r["id"],))
        else:
            _finish(conn, r, points)
    return [DEGRADED_MSG] if degraded else []


def _submit_confirm(conn, client, r, rqs, answers):
    d, lang = r["direction"], ans_lang(r["direction"])
    pend = [q for q in rqs if q["s2_ok"] == 0]
    clean = _collect(pend, answers, "review")
    items = [{"i": q["id"], "direction": d, "test_prompt": q["test_prompt"], "test_answer": q["test_answer"], "answer": clean[q["id"]],
              "exact": same(lang, clean[q["id"]], q["test_answer"])} for q in pend]
    res, points, degraded = aitasks.finish(client, items)
    with tx(conn):
        _check_stage(conn, r["id"], "review")
        for q, rs in zip(pend, res):
            ok = rs["verdict"] == "correct"
            _insert_attempt(conn, r, q, 3, q["test_prompt"], q["test_answer"], clean[q["id"]], rs["verdict"], rs["by"], rs["feedback"])
            conn.execute("UPDATE round_questions SET s3_ok=?, s3_answer=?, s3_feedback=? WHERE id=?", (1 if ok else 0, clean[q["id"]], rs["feedback"], q["id"]))
        _finish(conn, r, points)
    return [DEGRADED_MSG] if degraded else []
