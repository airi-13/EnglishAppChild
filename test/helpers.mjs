import { readFileSync } from "node:fs";
import worker from "../src/index.js";
import { resetSchemaCache } from "../src/schema.js";
import { MockClient } from "../src/ai.js";
import { D1Shim } from "./d1shim.mjs";

export const SAMPLE_CSV = readFileSync(new URL("../public/sample_questions.csv", import.meta.url), "utf8");
const assets = { fetch: async (req) => new Response(`asset:${new URL(req.url).pathname}`, { headers: { "Content-Type": "text/plain" } }) };

export function makeEnv(extra = {}) {
  resetSchemaCache();
  const mock = new MockClient();
  return { DB: new D1Shim(), ASSETS: assets, AI_MODE: "mock", COOKIE_SECURE: "0", PBKDF2_ITERS: "2000", SETUP_TOKEN: "setup-token-for-tests", __aiClient: mock, mock, ...extra };
}

export class Client {
  constructor(env) { this.env = env; this.cookie = ""; this.pending = []; }
  async req(method, path, body, headers = {}) {
    const h = { ...headers };
    if (body !== undefined || method !== "GET") h["Content-Type"] = h["Content-Type"] || "application/json";
    if (this.cookie) h.Cookie = this.cookie;
    const request = new Request("http://example.test" + path, { method, headers: h, body: method === "GET" ? undefined : body === undefined ? "{}" : typeof body === "string" ? body : JSON.stringify(body) });
    const ctx = { waitUntil: (p) => this.pending.push(p) };
    const res = await worker.fetch(request, this.env, ctx);
    await Promise.all(this.pending.splice(0));
    const sc = res.headers.get("Set-Cookie");
    if (sc) { const v = sc.split(";")[0]; this.cookie = v.endsWith("=") ? "" : v; }
    const text = await res.text();
    let data = null;
    try { data = JSON.parse(text); } catch { data = text; }
    return { status: res.status, data, headers: res.headers };
  }
  get(p) { return this.req("GET", p); }
  post(p, b, h) { return this.req("POST", p, b ?? {}, h); }
  login(id, pw) { return this.post("/api/login", { login_id: id, password: pw }); }
}

/** 管理者・生徒2人・全1,600問を用意した環境 */
export async function seeded(extra = {}) {
  const env = makeEnv(extra);
  const anon = new Client(env);
  let r = await anon.post("/api/setup", { setup_token: "setup-token-for-tests", password: "adminpass1" });
  if (r.status !== 200) throw new Error("setup failed " + JSON.stringify(r.data));
  const admin = new Client(env);
  await admin.login("admin", "adminpass1");
  // 問題を、画面と同じく分割して取り込む
  const lines = SAMPLE_CSV.replace(/^\ufeff/, "").split("\n").filter(Boolean);
  const head = lines[0];
  for (let i = 1; i < lines.length; i += 200) {
    r = await admin.post("/api/admin/questions/import", { csv: [head, ...lines.slice(i, i + 200)].join("\n"), row_offset: i - 1 });
    if (r.status !== 200) throw new Error("import failed " + JSON.stringify(r.data));
  }
  r = await admin.post("/api/admin/users", { users: [{ login_id: "stu1", label: "A1", password: "pass1234" }, { login_id: "stu2", label: "A2", password: "pass5678" }] });
  if (r.data.created.length !== 2) throw new Error("users failed");
  const s1 = new Client(env), s2 = new Client(env);
  await s1.login("stu1", "pass1234");
  await s2.login("stu2", "pass5678");
  return { env, admin, s1, s2, mock: env.mock };
}

/** ラウンドの rq_id → 正解（テスト専用に、DBから直接取り出す） */
export async function truth(env, roundId) {
  const rows = (await env.DB.prepare(`SELECT rq.id AS rq_id, q.* FROM round_questions rq JOIN questions q ON q.id=rq.question_id WHERE rq.round_id=? ORDER BY rq.position`).bind(roundId).all()).results;
  return new Map(rows.map((r) => [r.rq_id, r]));
}
export async function exactAnswers(env, view) {
  const t = await truth(env, view.id);
  const key = view.direction === "en2ja" ? "ja_text" : "en_text";
  return Object.fromEntries(view.items.map((it) => [String(it.rq_id), t.get(it.rq_id)[key]]));
}
export async function startRound(client, level = "k20", direction = "en2ja", seq = 1) {
  const r = await client.post("/api/rounds", { level_code: level, direction, start_seq: seq });
  if (r.status !== 200) throw new Error("start failed " + JSON.stringify(r.data));
  return (await client.get(`/api/rounds/${r.data.round_id}`)).data;
}
