import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { Client, SAMPLE_CSV, exactAnswers, makeEnv, seeded, startRound, truth } from "./helpers.mjs";
import { parseCsv, importCsv } from "../src/importer.js";
import { hashPassword, verifyPassword } from "../src/auth.js";

const submit = (c, v, a) => c.post(`/api/rounds/${v.id}/answers`, { answers: a });

describe("初期設定とログイン", () => {
  test("初期設定: トークン・パスワード・二重作成の検証", async () => {
    const env = makeEnv();
    const c = new Client(env);
    assert.deepEqual((await c.get("/api/status")).data, { needs_setup: true });
    assert.equal((await c.post("/api/setup", { setup_token: "bad", password: "adminpass1" })).status, 403);
    assert.equal((await c.post("/api/setup", { setup_token: "setup-token-for-tests", password: "short" })).status, 400);
    assert.equal((await c.post("/api/setup", { setup_token: "setup-token-for-tests", password: "adminpass1" })).status, 200);
    assert.deepEqual((await c.get("/api/status")).data, { needs_setup: false });
    assert.equal((await c.post("/api/setup", { setup_token: "setup-token-for-tests", password: "another-pass" })).status, 409);
    assert.equal((await c.login("admin", "adminpass1")).status, 200);
  });
  test("初期設定: SETUP_TOKEN 未設定なら使えない／間違いが続くとロック", async () => {
    const env = makeEnv({ SETUP_TOKEN: "" });
    assert.equal((await new Client(env).post("/api/setup", { setup_token: "", password: "adminpass1" })).status, 403);
    const env2 = makeEnv();
    const c = new Client(env2);
    for (let i = 0; i < 5; i++) await c.post("/api/setup", { setup_token: "x" + i, password: "adminpass1" });
    assert.equal((await c.post("/api/setup", { setup_token: "setup-token-for-tests", password: "adminpass1" })).status, 429);
  });
  test("誤パスワード・5回失敗でロック・成功で記録が消える", async () => {
    const { env } = await seeded();
    const c = new Client(env);
    assert.equal((await c.login("stu1", "bad")).status, 401);
    assert.equal((await c.login("stu1", "pass1234")).status, 200);
    assert.equal((await env.DB.prepare("SELECT COUNT(*) n FROM login_attempts WHERE login_id='stu1'").first()).n, 0);
    for (let i = 0; i < 5; i++) await c.login("stu1", "bad");
    assert.equal((await c.login("stu1", "pass1234")).status, 429);
  });
  test("権限・CSRF対策", async () => {
    const { env, s1, admin } = await seeded();
    assert.equal((await new Client(env).get("/api/levels")).status, 401);
    assert.equal((await s1.get("/api/admin/overview")).status, 403);
    assert.equal((await s1.post("/api/admin/users", { users: [{ login_id: "x12" }] })).status, 403);
    assert.equal((await admin.get("/api/admin/overview")).status, 200);
    assert.equal((await s1.req("POST", "/api/rounds", "level_code=k20", { "Content-Type": "application/x-www-form-urlencoded" })).status, 415);
    assert.equal((await s1.post("/api/rounds", { level_code: "k20", direction: "en2ja", start_seq: 1 }, { Origin: "http://evil.example" })).status, 403);
    assert.equal((await s1.post("/api/rounds", { level_code: "k20", direction: "en2ja", start_seq: 1 }, { Origin: "http://example.test" })).status, 200);
  });
  test("パスワード再発行で既存のログインが無効／停止した生徒はログインできない", async () => {
    const { env, admin, s1 } = await seeded();
    const uid = (await env.DB.prepare("SELECT id FROM users WHERE login_id='stu1'").first()).id;
    assert.equal((await s1.get("/api/me")).status, 200);
    assert.equal((await admin.post(`/api/admin/users/${uid}/reset_password`, { password: "newpass99" })).status, 200);
    assert.equal((await s1.get("/api/me")).status, 401);
    assert.equal((await new Client(env).login("stu1", "pass1234")).status, 401);
    const c = new Client(env);
    assert.equal((await c.login("stu1", "newpass99")).status, 200);
    await admin.post(`/api/admin/users/${uid}/active`, { active: false });
    assert.equal((await c.get("/api/me")).status, 401);
    assert.equal((await new Client(env).login("stu1", "newpass99")).status, 401);
  });
  test("パスワードの保存形式・ID検証・重複・人数上限", async () => {
    const { env, admin } = await seeded();
    const row = await env.DB.prepare("SELECT password_hash FROM users WHERE login_id='stu1'").first();
    assert.match(row.password_hash, /^pbkdf2\$2000\$[0-9a-f]{32}\$[0-9a-f]{64}$/);
    assert.ok(!row.password_hash.includes("pass1234"));
    const r = await admin.post("/api/admin/users", { users: [{ login_id: "STU1" }, { login_id: "ok_user" }, { login_id: "x" }] });
    assert.equal(r.data.created.length, 1);
    assert.equal(r.data.errors.length, 2);
    assert.equal(r.data.created[0].password.length, 8);
    const many = Array.from({ length: 21 }, (_, i) => ({ login_id: "bulk" + i }));
    assert.equal((await admin.post("/api/admin/users", { users: many })).status, 400);
  });
  test("パスワード: 改ざん・形式不正・上限超えを拒否", async () => {
    const h = await hashPassword("secret-pass", 3000);
    assert.ok(await verifyPassword("secret-pass", h));
    assert.ok(!(await verifyPassword("wrong", h)));
    for (const bad of ["", "pbkdf2$", "pbkdf2$99999999$00$00", "pbkdf2$0$00$00", "garbage", "pbkdf2$3000$zz$00", "scrypt$00$00"]) assert.ok(!(await verifyPassword("x", bad)), bad);
    assert.match(await hashPassword("x", 999999), /^pbkdf2\$100000\$/); // Workersの上限に丸める
  });
});

describe("出題フロー", () => {
  test("本文の段階では正解が送られない", async () => {
    const { env, s1 } = await seeded();
    const v = await startRound(s1);
    assert.equal(v.stage, "answer");
    assert.equal(v.items.length, 5);
    const blob = JSON.stringify(v);
    for (const q of (await truth(env, v.id)).values()) assert.ok(!blob.includes(q.ja_text));
    assert.ok(!blob.includes("expected"));
  });
  test("全問正解: AIを使わず、進捗が保存され、続きの5問へ", async () => {
    const { env, s1, mock } = await seeded();
    const v = await startRound(s1);
    const r = await submit(s1, v, await exactAnswers(env, v));
    assert.equal(r.status, 200, JSON.stringify(r.data));
    assert.equal(r.data.view.stage, "done");
    assert.equal(r.data.view.summary.perfect, true);
    assert.deepEqual(r.data.view.next, { level_code: "k20", level_name: "20級", start_seq: 6 });
    assert.deepEqual(mock.calls, []);
    const lv = Object.fromEntries((await s1.get("/api/levels")).data.levels.map((l) => [l.code, l]));
    assert.equal(lv.k20.next.en2ja, 6);
    assert.equal(lv.k20.next.ja2en, 1);
    const v2 = await startRound(s1, "k20", "en2ja", 6);
    assert.deepEqual(v2.items.map((i) => i.no), [6, 7, 8, 9, 10]);
  });
  test("未回答・空白・文字数超過は拒否される", async () => {
    const { env, s1 } = await seeded();
    const v = await startRound(s1);
    const ans = await exactAnswers(env, v);
    const k = Object.keys(ans)[2];
    ans[k] = "   ";
    let r = await submit(s1, v, ans);
    assert.equal(r.status, 400);
    assert.deepEqual(r.data.missing, [3]);
    delete ans[k];
    assert.equal((await submit(s1, v, ans)).status, 400);
    assert.equal((await submit(s1, v, Object.fromEntries(v.items.map((i) => [String(i.rq_id), "あ".repeat(201)])))).status, 400);
    assert.equal((await s1.get(`/api/rounds/${v.id}`)).data.stage, "answer");
  });
  test("間違い → 空欄補充（正解）→ 完了", async () => {
    const { env, s1 } = await seeded();
    const v = await startRound(s1);
    const ans = await exactAnswers(env, v);
    const wrong = Object.keys(ans).slice(0, 2);
    wrong.forEach((k) => (ans[k] = "ぜんぜん違う答え"));
    const r = await submit(s1, v, ans);
    const v2 = r.data.view;
    assert.equal(v2.stage, "fillin");
    assert.deepEqual(new Set(v2.items.map((i) => String(i.rq_id))), new Set(wrong));
    v2.items.forEach((i) => assert.ok(i.blank.includes("＿＿＿")));
    const t = await truth(env, v.id);
    const r2 = await submit(s1, v2, Object.fromEntries(v2.items.map((i) => [String(i.rq_id), t.get(i.rq_id).target_ja])));
    assert.equal(r2.data.view.stage, "done");
    assert.equal(r2.data.view.summary.words.length, 2);
    assert.ok(r2.data.view.items.filter((i) => i.s1_ok === 0).every((i) => i.s2_feedback));
    assert.equal((await env.DB.prepare("SELECT COUNT(*) n FROM attempts WHERE stage=1").first()).n, 5);
    assert.equal((await env.DB.prepare("SELECT COUNT(*) n FROM attempts WHERE stage=2 AND is_correct=1").first()).n, 2);
  });
  test("日英: 空欄でも間違い → 解説＋確認テスト → 完了。大文字小文字・ピリオドは無視", async () => {
    const { env, s1 } = await seeded();
    const v = await startRound(s1, "k20", "ja2en", 1);
    const ans = await exactAnswers(env, v);
    ans[Object.keys(ans)[0]] = "completely wrong";
    const v2 = (await submit(s1, v, ans)).data.view;
    assert.equal(v2.stage, "fillin");
    const v3 = (await submit(s1, v2, { [String(v2.items[0].rq_id)]: "bad" })).data.view;
    assert.equal(v3.stage, "review");
    assert.equal(v3.items.length, 1);
    assert.ok(v3.items[0].explanation && v3.items[0].test_prompt);
    const v4 = (await submit(s1, v3, { [String(v3.items[0].rq_id)]: "My test answer" })).data.view;
    assert.equal(v4.stage, "done");
    assert.equal(v4.summary.words.length, 1);
    const stages = (await env.DB.prepare("SELECT DISTINCT stage FROM attempts ORDER BY stage").all()).results.map((r) => r.stage);
    assert.deepEqual(stages, [1, 2, 3]);
    const v5 = await startRound(s1, "k20", "ja2en", 6);
    const a5 = Object.fromEntries(Object.entries(await exactAnswers(env, v5)).map(([k, x]) => [k, x.toUpperCase().replace(/\.$/, "")]));
    assert.equal((await submit(s1, v5, a5)).data.view.summary.perfect, true);
  });
  test("AIが言い換えを正解と判定する", async () => {
    const { env, s1 } = await seeded();
    const v = await startRound(s1);
    const ans = await exactAnswers(env, v);
    const k = Object.keys(ans)[0];
    ans[k] += " #ok";
    assert.equal((await submit(s1, v, ans)).data.view.summary.perfect, true);
    assert.equal((await env.DB.prepare("SELECT judged_by FROM attempts WHERE answer_text LIKE '%#ok'").first()).judged_by, "ai");
  });
  test("二重送信は409で、記録が重複しない（直列・同時とも）", async () => {
    const { env, s1 } = await seeded();
    const v = await startRound(s1);
    const ans = await exactAnswers(env, v);
    assert.equal((await submit(s1, v, ans)).status, 200);
    assert.equal((await submit(s1, v, ans)).status, 409);
    assert.equal((await env.DB.prepare("SELECT COUNT(*) n FROM attempts").first()).n, 5);
    // 同時送信（AIの待ちを挟むと、両方が検証を通過する）
    const v2 = await startRound(s1, "k20", "en2ja", 6);
    const a2 = await exactAnswers(env, v2);
    a2[Object.keys(a2)[0]] = "ちがう答え";
    env.mock.latencyMs = 30;
    const [x, y] = await Promise.all([submit(s1, v2, a2), submit(s1, v2, a2)]);
    assert.deepEqual([x.status, y.status].sort(), [200, 409]);
    assert.equal((await env.DB.prepare("SELECT COUNT(*) n FROM attempts WHERE round_id=?").bind(v2.id).first()).n, 5);
  });
  test("再開・やめる・同時開始でもセットは1つ", async () => {
    const { env, s1 } = await seeded();
    const v = await startRound(s1);
    const r = await s1.post("/api/rounds", { level_code: "k19", direction: "ja2en", start_seq: 3 });
    assert.equal(r.data.resumed, true);
    assert.equal(r.data.round_id, v.id);
    assert.equal((await s1.get("/api/rounds/active")).data.round_id, v.id);
    await s1.post(`/api/rounds/${v.id}/abandon`);
    assert.equal((await s1.get("/api/rounds/active")).data.round_id, null);
    const rs = await Promise.all([1, 2, 3].map(() => s1.post("/api/rounds", { level_code: "k19", direction: "ja2en", start_seq: 3 })));
    assert.equal(new Set(rs.map((x) => x.data.round_id)).size, 1);
    assert.equal((await env.DB.prepare("SELECT COUNT(*) n FROM rounds WHERE user_id=(SELECT id FROM users WHERE login_id='stu1') AND stage IN ('answer','fillin','review')").first()).n, 1);
  });
  test("級の最後で次の級へ、最後の段で終了／不正な指定", async () => {
    const { env, s1 } = await seeded();
    const v = await startRound(s1, "k20", "en2ja", 36);
    assert.deepEqual(v.items.map((i) => i.no), [36, 37, 38, 39, 40]);
    assert.deepEqual((await submit(s1, v, await exactAnswers(env, v))).data.view.next, { level_code: "k19", level_name: "19級", start_seq: 1, level_up: true });
    const v2 = await startRound(s1, "d20", "en2ja", 36);
    assert.equal((await submit(s1, v2, await exactAnswers(env, v2))).data.view.next, null);
    const s = new Client(env); await s.login("stu2", "pass5678");
    for (const [body, code] of [[{ level_code: "zzz", direction: "en2ja", start_seq: 1 }, 404], [{ level_code: "k20", direction: "xx", start_seq: 1 }, 400], [{ level_code: "k20", direction: "en2ja", start_seq: "abc" }, 400],
      [{ level_code: "k20", direction: "en2ja", start_seq: 0 }, 400], [{ level_code: "k20", direction: "en2ja", start_seq: 99 }, 404]])
      assert.equal((await s.post("/api/rounds", body)).status, code, JSON.stringify(body));
  });
  test("他の生徒のセットは見られない・送れない", async () => {
    const { env, s1, s2 } = await seeded();
    const v = await startRound(s1);
    assert.equal((await s2.get(`/api/rounds/${v.id}`)).status, 404);
    assert.equal((await submit(s2, v, {})).status, 404);
    assert.equal((await s2.post(`/api/rounds/${v.id}/abandon`)).status, 404);
    const v2 = await startRound(s2);
    assert.equal((await submit(s2, v2, Object.fromEntries(v.items.map((i) => [String(i.rq_id), "x"])))).status, 400);
  });
  test("1回のAPI呼び出しで使うD1クエリは、無料枠の上限(1,000)よりずっと少ない", async () => {
    const { env, s1 } = await seeded();
    let max = 0;
    const orig = s1.req.bind(s1);
    s1.req = async (...a) => { const q0 = env.DB.queries; const r = await orig(...a); max = Math.max(max, env.DB.queries - q0); return r; };
    const v = await startRound(s1);
    const ans = await exactAnswers(env, v);
    ans[Object.keys(ans)[0]] = "x";
    let view = (await submit(s1, v, ans)).data.view;
    view = (await submit(s1, view, { [String(view.items[0].rq_id)]: "bad" })).data.view;
    await submit(s1, view, { [String(view.items[0].rq_id)]: "z" });
    assert.ok(max <= 40, `max queries per request = ${max}`);
  });
});

describe("AIが使えないとき", () => {
  test("全面停止でも最後まで進める／復旧後は通常採点", async () => {
    const { env, s1, admin, mock } = await seeded();
    mock.fail = 100;
    const v = await startRound(s1);
    const ans = await exactAnswers(env, v);
    const k = Object.keys(ans)[0];
    ans[k] += " #ok";
    const r = await submit(s1, v, ans);
    assert.equal(r.status, 200);
    assert.ok(r.data.warnings.length);
    const v2 = r.data.view;
    assert.equal(v2.stage, "fillin");
    const t = await truth(env, v.id);
    const it = v2.items[0];
    const v3 = (await submit(s1, v2, { [String(it.rq_id)]: "bad" })).data.view;
    assert.equal(v3.stage, "review");
    assert.ok(v3.items[0].explanation);
    const v4 = (await submit(s1, v3, { [String(v3.items[0].rq_id)]: t.get(it.rq_id).ja_text })).data.view;
    assert.equal(v4.stage, "done");
    const by = (await env.DB.prepare("SELECT DISTINCT judged_by FROM attempts").all()).results.map((x) => x.judged_by);
    assert.ok(by.includes("fallback"));
    assert.ok((await admin.get("/api/admin/overview")).data.fallback_judgments >= 1);
    mock.fail = 0;
    const w = await startRound(s1, "k20", "en2ja", 6);
    const a = await exactAnswers(env, w);
    a[Object.keys(a)[0]] = "ちがう";
    const r2 = await submit(s1, w, a);
    assert.deepEqual(r2.data.warnings, []);
  });
});

describe("管理者機能", () => {
  test("CSVパーサ（引用符・引用符内の改行・BOM・CRLF）", () => {
    assert.deepEqual(parseCsv('\ufeffa,b\r\n"x,1","he said ""hi""\nok"\r\n'), [["a", "b"], ["x,1", 'he said "hi"\nok']]);
    assert.deepEqual(parseCsv("a,b\n1,2"), [["a", "b"], ["1", "2"]]);
  });
  test("取り込み: 不備は1問も入れず行番号つき／上書き／確認だけ(dry_run)／件数反映", async () => {
    const { env, admin } = await seeded();
    const H = "級・段,番号,英文,和文,英単語,日本語の単語";
    let r = await admin.post("/api/admin/questions/import", { csv: `${H}\n20級,1,I have a pen.,私はペンを持っています。,cat,猫\n` });
    assert.equal(r.status, 400);
    assert.match(r.data.errors[0].message, /見つかりません/);
    assert.equal(r.data.errors[0].row, 2);
    const before = (await env.DB.prepare("SELECT en_text FROM questions WHERE level_id=1 AND seq=1").first()).en_text;
    assert.notEqual(before, "I have a pen.");
    r = await admin.post("/api/admin/questions/import", { csv: `${H}\n20級,1,I have a pen.,私はペンを持っています。,pen,ペン\n`, dry_run: true });
    assert.equal(r.status, 200); assert.deepEqual(r.data.keys, ["k20:1"]);
    assert.equal((await env.DB.prepare("SELECT en_text FROM questions WHERE level_id=1 AND seq=1").first()).en_text, before);
    r = await admin.post("/api/admin/questions/import", { csv: `${H}\n20級,1,I have a pen.,私はペンを持っています。,pen,ペン\n初段,41,She plays tennis.,彼女はテニスをします。,plays,します\n` });
    assert.equal(r.status, 200, JSON.stringify(r.data));
    assert.equal((await env.DB.prepare("SELECT en_text FROM questions WHERE level_id=1 AND seq=1").first()).en_text, "I have a pen.");
    const lv = (await admin.get("/api/admin/questions/counts")).data.levels.find((l) => l.code === "d1");
    assert.equal(lv.n, 41);
    assert.equal((await env.DB.prepare("SELECT max_seq FROM levels WHERE code='d1'").first()).max_seq, 41);
    for (const csv of ["", `${H}\n`, `${H}\n99級,1,a,あ,a,あ\n`, `${H}\n20級,x,I have a pen.,私はペン,pen,ペン\n`, "a,b\n1,2\n",
      `${H}\n20級,2,I have a pen.,私はペン,pen,ペン\n20級,2,I have a cat.,私は猫,cat,猫\n`]) {
      assert.equal((await admin.post("/api/admin/questions/import", { csv })).status, 400, csv);
    }
    const big = [H, ...Array.from({ length: 251 }, (_, i) => `20級,${i + 1},I have a pen.,私はペン,pen,ペン`)].join("\n");
    assert.equal((await admin.post("/api/admin/questions/import", { csv: big })).status, 400);
  });
  test("集計が記録と一致する（期間指定つき）", async () => {
    const { env, admin, s1, s2 } = await seeded();
    for (const cl of [s1, s2]) for (const seq of [1, 6]) {
      const v = await startRound(cl, "k20", "en2ja", seq);
      const a = await exactAnswers(env, v);
      if (cl === s1) a[Object.keys(a)[0]] = "ちがう";
      let r = await submit(cl, v, a);
      if (r.data.view.stage === "fillin") {
        r = await submit(cl, r.data.view, Object.fromEntries(r.data.view.items.map((i) => [String(i.rq_id), "bad"])));
        if (r.data.view.stage === "review") await submit(cl, r.data.view, Object.fromEntries(r.data.view.items.map((i) => [String(i.rq_id), "bad"])));
      }
    }
    const o = (await admin.get("/api/admin/overview?days=30")).data;
    assert.equal(o.students, 2); assert.equal(o.active_students, 2); assert.equal(o.rounds_answered, 4); assert.equal(o.accuracy, 90);
    assert.equal(o.ai_config.mode, "mock");
    const st = Object.fromEntries((await admin.get("/api/admin/students")).data.students.map((x) => [x.login_id, x]));
    assert.equal(st.stu1.accuracy, 80); assert.equal(st.stu2.accuracy, 100); assert.equal(st.stu1.rounds_n, 2);
    const det = (await admin.get(`/api/admin/students/${st.stu1.id}`)).data;
    assert.equal(det.weak.length, 2); assert.equal(det.by_level[0].accuracy, 80);
    assert.equal((await admin.get("/api/admin/students/99999")).status, 404);
    assert.deepEqual((await admin.get("/api/admin/hardest")).data.questions, []);
    assert.ok((await admin.get("/api/admin/usage")).data.days.length);
    // 期間外（過去の記録にする）は集計に出ない
    await env.DB.prepare("UPDATE attempts SET created_at=datetime('now','-100 days')").run();
    assert.equal((await admin.get("/api/admin/overview?days=30")).data.accuracy, null);
    assert.equal((await admin.get("/api/admin/overview?days=365")).data.accuracy, 90);
  });
  test("正答率の低い問題ランキング", async () => {
    const { env, admin, s1, s2 } = await seeded();
    for (let i = 0; i < 3; i++) {
      const cl = i % 2 ? s2 : s1;
      const v = await startRound(cl, "k20", "en2ja", 1);
      const a = await exactAnswers(env, v);
      a[Object.keys(a)[0]] = "ちがう";
      await submit(cl, v, a);
      await cl.post(`/api/rounds/${v.id}/abandon`);
    }
    const hard = (await admin.get("/api/admin/hardest")).data.questions;
    assert.equal(hard.length, 5);
    assert.deepEqual([hard[0].seq, hard[0].accuracy], [1, 0]);
    assert.ok(hard.slice(1).every((h) => h.accuracy === 100));
  });
  test("回答ログの書き出し（分割・数式のエスケープ）", async () => {
    const { env, admin, s1 } = await seeded();
    const v = await startRound(s1);
    const ans = await exactAnswers(env, v);
    ans[Object.keys(ans)[0]] = '=HYPERLINK("http://evil")';
    await submit(s1, v, ans);
    let r = (await admin.get("/api/admin/export/attempts?after_id=0&limit=3")).data;
    assert.equal(r.count, 3);
    assert.equal(r.header.length, 12);
    const r2 = (await admin.get(`/api/admin/export/attempts?after_id=${r.last_id}&limit=3`)).data;
    assert.equal(r2.count, 2);
    const all = r.csv + "\n" + r2.csv;
    assert.ok(all.includes(`"'=HYPERLINK`));
    assert.ok(!all.includes(",=HYPERLINK"));
    assert.equal((await admin.get(`/api/admin/export/attempts?after_id=${r2.last_id}&limit=3`)).data.count, 0);
  });
  test("セキュリティヘッダー・静的ファイル・404・ヘルスチェック", async () => {
    const { env } = await seeded();
    const c = new Client(env);
    const a = await c.get("/api/healthz");
    assert.deepEqual(a.data, { ok: true });
    assert.match(a.headers.get("Content-Security-Policy"), /default-src 'self'/);
    assert.equal(a.headers.get("X-Frame-Options"), "DENY");
    assert.equal(a.headers.get("Cache-Control"), "no-store");
    const idx = await c.get("/index.html");
    assert.equal(idx.data, "asset:/index.html");
    assert.equal(idx.headers.get("X-Content-Type-Options"), "nosniff");
    const nf = await c.get("/api/nope");
    assert.equal(nf.status, 404);
    assert.equal(typeof nf.data.error, "string");
  });
  test("サンプル問題（1,600問）が検証を通る", async () => {
    const env = makeEnv();
    const lines = SAMPLE_CSV.split("\n").filter(Boolean);
    assert.equal(lines.length, 1601);
    const { importCsv: imp } = await import("../src/importer.js");
    const { ensureSchema } = await import("../src/schema.js");
    await ensureSchema(env.DB);
    const r = await imp(env.DB, [lines[0], ...lines.slice(1, 201)].join("\n"), { dryRun: true });
    assert.equal(r.ok, true, JSON.stringify(r.errors));
    assert.equal(r.count, 200);
  });
});
