// 同時利用テスト: 生徒40人が同時に2セットずつ最後まで解く（AIはモック・遅延あり）。記録の重複や欠落、固まりがないことを確認する。
import { test } from "node:test";
import assert from "node:assert/strict";
import { Client, exactAnswers, seeded, startRound } from "./helpers.mjs";

test("40人×2セットの同時利用でも、記録が欠けず重複しない", async () => {
  const { env, admin, mock } = await seeded();
  mock.latencyMs = 15;
  const N = 40;
  for (let i = 0; i < N; i += 20) await admin.post("/api/admin/users", { users: Array.from({ length: Math.min(20, N - i) }, (_, k) => ({ login_id: `load${String(i + k).padStart(3, "0")}`, password: "pass1234" })) });
  const errors = [];
  let done = 0;
  await Promise.all(Array.from({ length: N }, async (_, i) => {
    try {
      const c = new Client(env);
      assert.equal((await c.login(`load${String(i).padStart(3, "0")}`, "pass1234")).status, 200);
      const level = ["k20", "k15", "k10", "d1", "d10"][i % 5], dir = i % 2 ? "ja2en" : "en2ja";
      let seq = 1;
      for (let round = 0; round < 2; round++) {
        let v = await startRound(c, level, dir, seq);
        const ans = await exactAnswers(env, v);
        if (i % 3 === 0) Object.keys(ans).slice(0, 2).forEach((k) => (ans[k] = "ちがう答え"));
        const post = async (a) => { const r = await c.post(`/api/rounds/${v.id}/answers`, { answers: a }); assert.equal(r.status, 200, JSON.stringify(r.data)); return r.data.view; };
        v = await post(ans);
        if (v.stage === "fillin") v = await post(Object.fromEntries(v.items.map((it) => [String(it.rq_id), "wrong"])));
        if (v.stage === "review") v = await post(Object.fromEntries(v.items.map((it) => [String(it.rq_id), "x"])));
        assert.equal(v.stage, "done");
        seq = v.next.start_seq;
        done++;
      }
    } catch (e) { errors.push(`student ${i}: ${e.message}`); }
  }));
  assert.deepEqual(errors, []);
  assert.equal(done, N * 2);
  const q = (sql) => env.DB.prepare(sql).first();
  assert.equal((await q("SELECT COUNT(*) n FROM rounds WHERE stage='done'")).n, N * 2);
  assert.equal((await q("SELECT COUNT(*) n FROM rounds WHERE stage NOT IN ('done')")).n, 0);
  assert.equal((await q("SELECT COUNT(*) n FROM attempts WHERE stage=1")).n, N * 2 * 5);
  assert.equal((await q("SELECT COUNT(*) n FROM (SELECT round_id, question_id, stage FROM attempts GROUP BY 1,2,3 HAVING COUNT(*)>1)")).n, 0);
  assert.equal((await q("PRAGMA integrity_check")).integrity_check, "ok");
  assert.equal((await env.DB.prepare("PRAGMA foreign_key_check").all()).results.length, 0);
});
