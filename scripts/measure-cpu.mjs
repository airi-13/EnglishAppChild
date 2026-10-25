// 主なAPIのCPU時間（目安）を測る。Cloudflare無料プランの上限は、1回のリクエストあたり10ミリ秒。
// Node(V8)での値なので、実際のCloudflareとは差があるが、桁を知るために使う。  node scripts/measure-cpu.mjs
import { Client, exactAnswers, seeded, startRound } from "../test/helpers.mjs";
import { hashPassword, verifyPassword } from "../src/auth.js";

const cpu = async (fn, times = 20) => {
  await fn(); // 暖機
  const t0 = process.cpuUsage();
  for (let i = 0; i < times; i++) await fn();
  const t = process.cpuUsage(t0);
  return (t.user + t.system) / 1000 / times;
};
const { env, admin, s1 } = await seeded({ PBKDF2_ITERS: "30000" });
const rows = [];
for (const it of [10000, 30000, 60000, 100000]) { const h = await hashPassword("x", it); rows.push([`パスワード確認（PBKDF2 ${it}回）`, await cpu(() => verifyPassword("x", h), 10)]); }
rows.push(["レベル一覧（ホーム画面）", await cpu(() => s1.get("/api/levels"))]);
rows.push(["自分の状態 /api/me", await cpu(() => s1.get("/api/me"))]);
let v = await startRound(s1);
rows.push(["セットの表示", await cpu(() => s1.get(`/api/rounds/${v.id}`))]);
const ansSets = [];
const submitCost = async () => { // 全問正解の送信（採点〜完了まで）
  const w = await startRound(s1, "k20", "en2ja", 1 + 5 * ansSets.length++ % 35);
  const a = await exactAnswers(env, w);
  const t0 = process.cpuUsage();
  await s1.post(`/api/rounds/${w.id}/answers`, { answers: a });
  const t = process.cpuUsage(t0);
  return (t.user + t.system) / 1000;
};
const xs = []; for (let i = 0; i < 12; i++) xs.push(await submitCost());
rows.push(["回答の送信（5問・全問正解）", xs.slice(2).reduce((a, b) => a + b, 0) / (xs.length - 2)]);
rows.push(["管理者: ダッシュボード", await cpu(() => admin.get("/api/admin/overview"))]);
rows.push(["管理者: 生徒一覧", await cpu(() => admin.get("/api/admin/students"))]);
const lines = (await (await import("node:fs/promises")).readFile(new URL("../public/sample_questions.csv", import.meta.url), "utf8")).replace(/^\ufeff/, "").split("\n").filter(Boolean);
const chunkCsv = [lines[0], ...lines.slice(1, 201)].join("\n");
rows.push(["管理者: 問題CSV 200行の取り込み", await cpu(() => admin.post("/api/admin/questions/import", { csv: chunkCsv }), 5)]);
for (const [n, ms] of rows) console.log(`${ms.toFixed(1).padStart(7)} ms  ${n}`);
