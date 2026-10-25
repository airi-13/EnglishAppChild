// 出題フローの本体。流れ:
//   answer(本文) → fillin(間違えた問題だけ空欄補充) → review(それでも間違えた問題の解説＋確認テスト) → done(まとめ)
// 状態管理と記録はすべてこのプログラムが行い、AIは「採点・補足・解説・確認テスト作成」だけを担当する。
// D1 は対話的なトランザクションが無いため、1回の送信の書き込みは batch() にまとめ、
// 「いま想定した段階のときだけ書く」条件を各文に付けて、二重送信・同時送信でも記録が重複しないようにしている。
import * as aitasks from "./ai.js";
import { ApiError } from "./util.js";
import { same } from "./textnorm.js";

export const PER_ROUND = 5;
export const MAX_ANSWER_LEN = 200;
export const BLANK = "＿＿＿";
export const DIRECTIONS = { en2ja: "英日（英語→日本語）", ja2en: "日英（日本語→英語）" };
const ACTIVE = "('answer','fillin','review')";

// ---------------------------------------------------------------- 問題文の組み立て
export const ansLang = (d) => (d === "en2ja" ? "ja" : "en");
export const sourceSentence = (q, d) => (d === "en2ja" ? q.en_text : q.ja_text);
export const expectedSentence = (q, d) => (d === "en2ja" ? q.ja_text : q.en_text);
export const expectedWord = (q, d) => (d === "en2ja" ? q.target_ja : q.target_en);
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
export const wordPattern = (w) => new RegExp(`(?<![A-Za-z])${esc(w)}(?![A-Za-z])`, "i");
export function blankSentence(q, d) {
  if (d === "en2ja") return q.ja_text.replace(q.target_ja, BLANK);
  return q.en_text.replace(wordPattern(q.target_en), BLANK);
}
export const targetInSentence = (en, ja, tEn, tJa) => wordPattern(tEn).test(en) && ja.includes(tJa);

// ---------------------------------------------------------------- 取得
const all = async (stmt) => (await stmt.all()).results;

async function getRqs(db, roundId) {
  return all(db.prepare(`SELECT rq.*, q.seq, q.en_text, q.ja_text, q.target_en, q.target_ja, q.grammar_note
    FROM round_questions rq JOIN questions q ON q.id=rq.question_id WHERE rq.round_id=? ORDER BY rq.position`).bind(roundId));
}

async function getRound(db, roundId, userId) {
  const r = await db.prepare(`SELECT r.*, l.code AS level_code, l.name AS level_name, l.order_no FROM rounds r JOIN levels l ON l.id=r.level_id WHERE r.id=?`).bind(roundId).first();
  if (!r || r.user_id !== userId) throw new ApiError(404, "問題が見つかりません");
  return r;
}

export async function activeRoundId(db, userId) {
  const r = await db.prepare(`SELECT id FROM rounds WHERE user_id=? AND stage IN ${ACTIVE} ORDER BY id DESC LIMIT 1`).bind(userId).first();
  return r ? r.id : null;
}

export async function levelsWithProgress(db, userId) {
  const levels = await all(db.prepare("SELECT id, code, name, kind, order_no, total, max_seq FROM levels ORDER BY order_no"));
  const prog = new Map();
  for (const p of await all(db.prepare("SELECT level_id, direction, next_seq FROM progress WHERE user_id=?").bind(userId))) prog.set(`${p.level_id}:${p.direction}`, p.next_seq);
  return levels.map(({ id, ...l }) => ({ ...l, next: Object.fromEntries(Object.keys(DIRECTIONS).map((d) => [d, prog.get(`${id}:${d}`) ?? 1])) }));
}

// ---------------------------------------------------------------- 開始・中止
export async function startRound(db, userId, levelCode, direction, startSeq) {
  const active = await activeRoundId(db, userId);
  if (active) return [active, true];
  if (!(direction in DIRECTIONS)) throw new ApiError(400, "英日か日英を選んでください");
  const seq = Number(startSeq);
  if (!Number.isInteger(seq)) throw new ApiError(400, "問題番号は数字で入力してください");
  if (seq < 1) throw new ApiError(400, "問題番号は1以上にしてください");
  const lv = await db.prepare("SELECT * FROM levels WHERE code=?").bind(levelCode).first();
  if (!lv) throw new ApiError(404, "級・段が見つかりません");
  const qs = await all(db.prepare("SELECT id FROM questions WHERE level_id=? AND seq>=? ORDER BY seq LIMIT ?").bind(lv.id, seq, PER_ROUND));
  if (!qs.length) throw new ApiError(404, `${lv.name}の${seq}番以降の問題はまだありません`);
  // 同時に2つ作られないよう、「進行中のセットが無いときだけ」作る
  const res = await db
    .prepare(`INSERT INTO rounds(user_id,level_id,direction,start_seq,stage) SELECT ?,?,?,?,'answer'
              WHERE NOT EXISTS (SELECT 1 FROM rounds WHERE user_id=? AND stage IN ${ACTIVE})`)
    .bind(userId, lv.id, direction, seq, userId)
    .run();
  if (res.meta.changes === 0) return [await activeRoundId(db, userId), true];
  const rid = res.meta.last_row_id;
  try {
    await db.batch(qs.map((q, i) => db.prepare("INSERT INTO round_questions(round_id,question_id,position) VALUES (?,?,?)").bind(rid, q.id, i + 1)));
  } catch (e) {
    await db.prepare("DELETE FROM rounds WHERE id=?").bind(rid).run();
    throw e;
  }
  return [rid, false];
}

export async function abandonRound(db, roundId, userId) {
  await getRound(db, roundId, userId);
  await db.prepare(`UPDATE rounds SET stage='abandoned', finished_at=datetime('now') WHERE id=? AND stage IN ${ACTIVE}`).bind(roundId).run();
}

// ---------------------------------------------------------------- 画面用データ
async function nextInfo(db, r, rqs) {
  const last = Math.max(...rqs.map((q) => q.seq));
  const lv = await db.prepare("SELECT max_seq FROM levels WHERE id=?").bind(r.level_id).first();
  if (last < lv.max_seq) return { level_code: r.level_code, level_name: r.level_name, start_seq: last + 1 };
  const nxt = await db.prepare("SELECT code, name FROM levels WHERE order_no>? AND total>0 ORDER BY order_no LIMIT 1").bind(r.order_no).first();
  return nxt ? { level_code: nxt.code, level_name: nxt.name, start_seq: 1, level_up: true } : null;
}

export async function buildView(db, roundId, userId) {
  const r = await getRound(db, roundId, userId);
  const rqs = await getRqs(db, roundId);
  if (!rqs.length) throw new ApiError(404, "問題が見つかりません");
  const d = r.direction;
  const v = { id: r.id, level_code: r.level_code, level_name: r.level_name, direction: d, direction_label: DIRECTIONS[d], answer_lang: ansLang(d), stage: r.stage, first_no: rqs[0].seq, last_no: rqs[rqs.length - 1].seq, items: [] };
  if (r.stage === "answer") {
    v.items = rqs.map((q) => ({ rq_id: q.id, no: q.seq, text: sourceSentence(q, d) }));
  } else if (r.stage === "fillin") {
    v.stage1 = rqs.map((q) => ({ no: q.seq, ok: !!q.s1_ok }));
    v.items = rqs.filter((q) => q.s1_ok === 0).map((q) => ({ rq_id: q.id, no: q.seq, source: sourceSentence(q, d), blank: blankSentence(q, d), your_answer: q.s1_answer }));
  } else if (r.stage === "review") {
    v.stage1 = rqs.map((q) => ({ no: q.seq, ok: !!q.s1_ok }));
    v.fillin_results = rqs.filter((q) => q.s1_ok === 0).map((q) => ({ no: q.seq, ok: !!q.s2_ok, answer: q.s2_answer, feedback: q.s2_feedback || "" }));
    v.items = rqs.filter((q) => q.s2_ok === 0).map((q) => ({ rq_id: q.id, no: q.seq, source: sourceSentence(q, d), your_answer: q.s1_answer, word_answer: q.s2_answer, explanation: q.explanation, test_prompt: q.test_prompt }));
  } else {
    v.summary = r.summary_json ? JSON.parse(r.summary_json) : null;
    v.items = rqs.map((q) => ({ no: q.seq, source: sourceSentence(q, d), expected: expectedSentence(q, d), word: expectedWord(q, d), s1_ok: q.s1_ok, s1_answer: q.s1_answer, s2_ok: q.s2_ok, s2_answer: q.s2_answer, s2_feedback: q.s2_feedback,
      explanation: q.explanation, test_prompt: q.test_prompt, s3_ok: q.s3_ok, s3_answer: q.s3_answer, s3_feedback: q.s3_feedback }));
    if (r.stage === "done") v.next = await nextInfo(db, r, rqs);
  }
  return v;
}

// ---------------------------------------------------------------- 回答の受付
function collect(pend, answers) {
  if (!answers || typeof answers !== "object" || Array.isArray(answers)) throw new ApiError(400, "回答の形式が正しくありません");
  const clean = new Map();
  const missing = [];
  for (const q of pend) {
    const a = answers[String(q.id)];
    const t = typeof a === "string" ? a.trim() : "";
    if (!t) missing.push(q.seq);
    else if ([...t].length > MAX_ANSWER_LEN) throw new ApiError(400, `No.${q.seq}の答えが長すぎます（${MAX_ANSWER_LEN}文字まで）`);
    else clean.set(q.id, t);
  }
  if (missing.length) throw new ApiError(400, `${missing.map((n) => `No.${n}`).join("、")}にも答えてから送ってね`, { missing });
  return clean;
}

const GUARD = "WHERE EXISTS (SELECT 1 FROM rounds WHERE id=? AND stage=?)";
const insAttempt = (db, r, q, stage, prompt, expected, answer, verdict, by, fb, guard) =>
  db.prepare(`INSERT INTO attempts(round_id,user_id,question_id,direction,stage,prompt_text,expected_text,answer_text,verdict,is_correct,judged_by,feedback_text)
              SELECT ?,?,?,?,?,?,?,?,?,?,?,? ${GUARD}`).bind(r.id, r.user_id, q.question_id, r.direction, stage, prompt, expected, answer, verdict, verdict === "correct" ? 1 : 0, by, fb, r.id, guard);

function finishStatements(db, r, rqs, s1ok, points, guard) {
  const d = r.direction;
  const wrong1 = rqs.filter((q) => !(s1ok.get(q.id) ?? q.s1_ok));
  const words = wrong1.map((q) => ({ no: q.seq, en: q.target_en, ja: q.target_ja, sentence_en: q.en_text, sentence_ja: q.ja_text }));
  let pts = [...(points || [])];
  if (!pts.length) pts = [...new Set(wrong1.map((q) => q.grammar_note).filter(Boolean))].slice(0, 3);
  const summary = { perfect: !words.length, points: pts, words };
  const last = Math.max(...rqs.map((q) => q.seq));
  return [
    db.prepare(`INSERT INTO progress(user_id,level_id,direction,next_seq) SELECT ?,?,?,? ${GUARD}
                ON CONFLICT(user_id,level_id,direction) DO UPDATE SET next_seq=excluded.next_seq`).bind(r.user_id, r.level_id, d, last + 1, r.id, guard),
    db.prepare("UPDATE rounds SET stage='done', summary_json=?, finished_at=datetime('now') WHERE id=? AND stage=?").bind(JSON.stringify(summary), r.id, guard),
  ];
}

async function commit(db, stmts) {
  const res = await db.batch(stmts);
  if (res[res.length - 1].meta.changes !== 1) throw new ApiError(409, "この問題はすでに送信されています。画面を更新してください");
}

const DEGRADED_MSG = "AIが混み合っているため、一部をかんたん採点にしました。先生には記録が残ります。";

export async function submit(db, client, roundId, userId, answers) {
  const r = await getRound(db, roundId, userId);
  const fn = { answer: submitAnswer, fillin: submitFillin, review: submitConfirm }[r.stage];
  if (!fn) throw new ApiError(409, "この問題はもう終わっています");
  return fn(db, client, r, await getRqs(db, roundId), answers);
}

async function submitAnswer(db, client, r, rqs, answers) {
  const d = r.direction, lang = ansLang(d);
  const clean = collect(rqs, answers);
  const verdict = new Map();
  const aiItems = [];
  for (const q of rqs) {
    const exp = expectedSentence(q, d), a = clean.get(q.id);
    if (same(lang, a, exp)) verdict.set(q.id, ["correct", "", "exact"]);
    else aiItems.push({ i: q.id, direction: d, question: sourceSentence(q, d), expected: exp, answer: a });
  }
  let degraded = false;
  if (aiItems.length) {
    const [res, deg] = await aitasks.judgeSentences(client, aiItems);
    degraded = deg;
    aiItems.forEach((it, k) => verdict.set(it.i, [res[k].verdict, res[k].note, res[k].by]));
  }
  const s1ok = new Map(rqs.map((q) => [q.id, verdict.get(q.id)[0] === "correct" ? 1 : 0]));
  const stmts = [];
  for (const q of rqs) {
    const [v, note, by] = verdict.get(q.id);
    stmts.push(insAttempt(db, r, q, 1, sourceSentence(q, d), expectedSentence(q, d), clean.get(q.id), v, by, note, "answer"));
    stmts.push(db.prepare(`UPDATE round_questions SET s1_ok=?, s1_answer=? WHERE id=? AND EXISTS (SELECT 1 FROM rounds WHERE id=? AND stage='answer')`).bind(s1ok.get(q.id), clean.get(q.id), q.id, r.id));
  }
  if ([...s1ok.values()].some((x) => !x)) stmts.push(db.prepare("UPDATE rounds SET stage='fillin' WHERE id=? AND stage='answer'").bind(r.id));
  else stmts.push(...finishStatements(db, r, rqs, s1ok, [], "answer"));
  await commit(db, stmts);
  return degraded ? [DEGRADED_MSG] : [];
}

async function submitFillin(db, client, r, rqs, answers) {
  const d = r.direction, lang = ansLang(d);
  const pend = rqs.filter((q) => q.s1_ok === 0);
  const clean = collect(pend, answers);
  const items = pend.map((q) => {
    const w = expectedWord(q, d);
    return { i: q.id, direction: d, source: sourceSentence(q, d), blank: blankSentence(q, d), expected: w, answer: clean.get(q.id), exact: same(lang, clean.get(q.id), w),
      supp_fallback: `「${q.target_en}」は「${q.target_ja}」という意味だよ。` };
  });
  const [res, points, deg1] = await aitasks.gradeFillin(client, items);
  let degraded = deg1;
  const wrong = pend.filter((_, k) => res[k].verdict !== "correct");
  const reviews = new Map();
  if (wrong.length) {
    const rItems = wrong.map((q) => ({ i: q.id, direction: d, source_sentence: sourceSentence(q, d), expected_sentence: expectedSentence(q, d), expected_word: expectedWord(q, d), grammar_note: q.grammar_note,
      s1_answer: q.s1_answer, s2_answer: clean.get(q.id),
      fallback_explanation: `正しい単語は「${expectedWord(q, d)}」。正しい文は「${expectedSentence(q, d)}」だよ。` + (q.grammar_note ? ` ポイント: ${q.grammar_note}` : "") }));
    const [rev, deg2] = await aitasks.review(client, rItems);
    degraded = degraded || deg2;
    wrong.forEach((q, k) => reviews.set(q.id, rev[k]));
  }
  const stmts = [];
  pend.forEach((q, k) => {
    const ok = res[k].verdict === "correct" ? 1 : 0;
    stmts.push(insAttempt(db, r, q, 2, blankSentence(q, d), expectedWord(q, d), clean.get(q.id), res[k].verdict, res[k].by, res[k].supplement, "fillin"));
    stmts.push(db.prepare(`UPDATE round_questions SET s2_ok=?, s2_answer=?, s2_feedback=? WHERE id=? AND EXISTS (SELECT 1 FROM rounds WHERE id=? AND stage='fillin')`).bind(ok, clean.get(q.id), res[k].supplement, q.id, r.id));
  });
  for (const q of wrong) {
    const rv = reviews.get(q.id);
    stmts.push(db.prepare(`UPDATE round_questions SET explanation=?, test_prompt=?, test_answer=? WHERE id=? AND EXISTS (SELECT 1 FROM rounds WHERE id=? AND stage='fillin')`).bind(rv.explanation, rv.test_prompt, rv.test_answer, q.id, r.id));
  }
  if (wrong.length) stmts.push(db.prepare("UPDATE rounds SET stage='review' WHERE id=? AND stage='fillin'").bind(r.id));
  else stmts.push(...finishStatements(db, r, rqs, new Map(), points, "fillin"));
  await commit(db, stmts);
  return degraded ? [DEGRADED_MSG] : [];
}

async function submitConfirm(db, client, r, rqs, answers) {
  const d = r.direction, lang = ansLang(d);
  const pend = rqs.filter((q) => q.s2_ok === 0);
  const clean = collect(pend, answers);
  const items = pend.map((q) => ({ i: q.id, direction: d, test_prompt: q.test_prompt, test_answer: q.test_answer, answer: clean.get(q.id), exact: same(lang, clean.get(q.id), q.test_answer) }));
  const [res, points, degraded] = await aitasks.finish(client, items);
  const stmts = [];
  pend.forEach((q, k) => {
    const ok = res[k].verdict === "correct" ? 1 : 0;
    stmts.push(insAttempt(db, r, q, 3, q.test_prompt, q.test_answer, clean.get(q.id), res[k].verdict, res[k].by, res[k].feedback, "review"));
    stmts.push(db.prepare(`UPDATE round_questions SET s3_ok=?, s3_answer=?, s3_feedback=? WHERE id=? AND EXISTS (SELECT 1 FROM rounds WHERE id=? AND stage='review')`).bind(ok, clean.get(q.id), res[k].feedback, q.id, r.id));
  });
  stmts.push(...finishStatements(db, r, rqs, new Map(), points, "review"));
  await commit(db, stmts);
  return degraded ? [DEGRADED_MSG] : [];
}
