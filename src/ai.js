// Gemini との通信と、出題フローで使う4つのAIタスク。
// ・AIが使えないとき（混雑・通信障害・キー不備）は、各タスクが「安全な代替結果」を返し、学習は止まらない。
// ・生徒のID・氏名はAIに送らない（問題文と回答だけを送る）。
import * as prompts from "./prompts.js";
import { intEnv, sleep } from "./util.js";
import { normEn, normJa } from "./textnorm.js";

export class AIError extends Error {}
const VERDICTS = ["correct", "close", "wrong"];

export function parseJsonText(text) {
  let t = String(text ?? "").trim().replace(/^```(?:json)?\s*|\s*```$/g, "").trim();
  try {
    return JSON.parse(t);
  } catch {
    const m = t.match(/\{[\s\S]*\}/);
    if (!m) throw new Error("no json");
    return JSON.parse(m[0]);
  }
}

export class GeminiClient {
  constructor({ apiKey, model, baseUrl, timeoutMs = 25000, maxRetries = 2, thinkingBudget = null, logger = () => {}, fetchFn = (...a) => fetch(...a) }) {
    Object.assign(this, { apiKey, model, timeoutMs, maxRetries, thinkingBudget, logger, fetchFn });
    this.baseUrl = baseUrl.replace(/\/+$/, "");
  }

  async generateJson(system, user, purpose) {
    if (!this.apiKey) {
      this.logger(purpose, this.model, false, 0, "no api key");
      throw new AIError("GEMINI_API_KEY is not set");
    }
    const genCfg = { temperature: 0.3, responseMimeType: "application/json" };
    if (this.thinkingBudget !== null) genCfg.thinkingConfig = { thinkingBudget: this.thinkingBudget };
    const body = JSON.stringify({
      systemInstruction: { parts: [{ text: system }] },
      contents: [{ role: "user", parts: [{ text: user }] }],
      generationConfig: genCfg,
    });
    const url = `${this.baseUrl}/models/${this.model}:generateContent`;
    let last = new AIError("unknown");
    for (let attempt = 0; attempt <= this.maxRetries; attempt++) {
      const t0 = Date.now();
      let delay = 0;
      const ctl = new AbortController();
      const timer = setTimeout(() => ctl.abort(), this.timeoutMs);
      try {
        const res = await this.fetchFn(url, { method: "POST", headers: { "Content-Type": "application/json", "x-goog-api-key": this.apiKey }, body, signal: ctl.signal });
        if (!res.ok) {
          this.logger(purpose, this.model, false, Date.now() - t0, `HTTP ${res.status}`);
          last = new AIError(`HTTP ${res.status}`);
          if (![429, 500, 502, 503, 504].includes(res.status)) break;
          const ra = res.headers.get("Retry-After");
          delay = ra && /^\d+(\.\d+)?$/.test(ra) ? Number(ra) * 1000 : 1500 * 2 ** attempt;
        } else {
          const payload = await res.json();
          const cands = payload.candidates || [];
          if (!cands.length) throw new Error("no candidates");
          const text = (cands[0].content?.parts || []).map((p) => p.text || "").join("");
          const obj = parseJsonText(text);
          if (!obj || typeof obj !== "object" || Array.isArray(obj)) throw new Error("not an object");
          this.logger(purpose, this.model, true, Date.now() - t0, null);
          return obj;
        }
      } catch (e) {
        if (e.name === "AbortError") {
          this.logger(purpose, this.model, false, Date.now() - t0, "timeout");
          last = new AIError("timeout");
          delay = 500;
        } else if (e instanceof SyntaxError || /no candidates|not an object|no json/.test(e.message)) {
          this.logger(purpose, this.model, false, Date.now() - t0, "bad response");
          last = new AIError("invalid response");
          delay = 300;
        } else {
          this.logger(purpose, this.model, false, Date.now() - t0, "network");
          last = new AIError("network error");
          delay = 1000 * 2 ** attempt;
        }
      } finally {
        clearTimeout(timer);
      }
      if (attempt < this.maxRetries) await sleep(Math.min(delay, 8000));
    }
    throw last;
  }
}

function similarity(a, b) {
  if (a === b) return 1;
  if (!a.length || !b.length) return 0;
  const dp = Array.from({ length: a.length + 1 }, (_, i) => [i, ...Array(b.length).fill(0)]);
  for (let j = 1; j <= b.length; j++) dp[0][j] = j;
  for (let i = 1; i <= a.length; i++)
    for (let j = 1; j <= b.length; j++) dp[i][j] = Math.min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
  return 1 - dp[a.length][b.length] / Math.max(a.length, b.length);
}

/** APIキー不要の動作確認用。判定は文字列の類似度で行う。回答に「#ok」を含めると言い換え正解になる。 */
export class MockClient {
  constructor({ logger = () => {}, latencyMs = 0 } = {}) {
    this.logger = logger;
    this.latencyMs = latencyMs;
    this.fail = 0; // テスト用: 次のN回の呼び出しを失敗させる
    this.calls = [];
    this.model = "mock";
  }

  static verdict(answer, expected) {
    if (String(answer).includes("#ok")) return "correct";
    const ja = /[぀-ヿ一-鿿]/.test(expected);
    const [a, b] = ja ? [normJa(answer), normJa(expected)] : [normEn(answer), normEn(expected)];
    if (a === b && a) return "correct";
    return similarity(a, b) >= 0.85 ? "close" : "wrong";
  }

  async generateJson(_system, _user, purpose, data = {}) {
    this.calls.push(purpose);
    if (this.fail > 0) {
      this.fail--;
      this.logger(purpose, "mock", false, 0, "injected failure");
      throw new AIError("injected failure");
    }
    if (this.latencyMs) await sleep(this.latencyMs);
    const items = data.items || [];
    let out;
    if (purpose === "judge_sentences") out = { results: items.map((it) => ({ i: it.i, verdict: MockClient.verdict(it.answer, it.expected), note: "mock" })) };
    else if (purpose === "grade_fillin")
      out = {
        results: items.map((it) => {
          const v = it.exact ? "correct" : MockClient.verdict(it.answer, it.expected);
          return { i: it.i, verdict: v, supplement: v === "correct" ? `【補足】${it.expected} はこの文の大事な単語だよ。` : "" };
        }),
        points: ["単語のスペルと形を確認しよう"],
      };
    else if (purpose === "review")
      out = { items: items.map((it) => ({ i: it.i, explanation: `【解説】「${it.expected_word}」に注目しよう。あなたの答えは「${it.s2_answer}」だったね。`, test_prompt: it.source_sentence, test_answer: it.expected_sentence })) };
    else if (purpose === "finish")
      out = { results: items.map((it) => ({ i: it.i, verdict: it.exact ? "correct" : MockClient.verdict(it.answer, it.test_answer), feedback: `正解例: ${it.test_answer}` })), points: ["間違えた単語をもう一度書いて覚えよう"] };
    else throw new AIError("unknown purpose");
    this.logger(purpose, "mock", true, this.latencyMs, null);
    return out;
  }
}

// ---------------------------------------------------------------- タスク
const normVerdict = (v) => (VERDICTS.includes(v) ? v : null);
const byI = (list) => {
  const m = new Map();
  for (const r of Array.isArray(list) ? list : []) if (r && typeof r === "object" && "i" in r) m.set(r.i, r);
  return m;
};
const strList = (v, n) => (Array.isArray(v) ? v.filter((p) => ["string", "number"].includes(typeof p)).map((p) => String(p).slice(0, 80)).slice(0, n) : []);

export async function judgeSentences(client, items) {
  try {
    const obj = await client.generateJson(prompts.SYSTEM, prompts.judgeSentences(items), "judge_sentences", { items });
    const got = byI(obj.results);
    let degraded = false;
    const out = items.map((it) => {
      const r = got.get(it.i);
      const v = r ? normVerdict(r.verdict) : null;
      if (v) return { verdict: v, note: String(r.note ?? "").slice(0, 200), by: "ai" };
      degraded = true;
      return { verdict: "wrong", note: "AI判定なし", by: "fallback" };
    });
    return [out, degraded];
  } catch (e) {
    if (!(e instanceof AIError)) throw e;
    return [items.map(() => ({ verdict: "wrong", note: "AI利用不可", by: "fallback" })), true];
  }
}

export async function gradeFillin(client, items) {
  try {
    const obj = await client.generateJson(prompts.SYSTEM, prompts.gradeFillin(items), "grade_fillin", { items });
    const got = byI(obj.results);
    const points = strList(obj.points, 3);
    let degraded = false;
    const out = items.map((it) => {
      const r = got.get(it.i);
      let v = r ? normVerdict(r.verdict) : null;
      if (it.exact) v = "correct";
      if (v === null) {
        degraded = true;
        return { verdict: "wrong", supplement: "", by: "fallback" };
      }
      let supp = r ? String(r.supplement ?? "").trim() : "";
      if (v === "correct" && !supp) supp = it.supp_fallback;
      return { verdict: v, supplement: v === "correct" ? supp : "", by: it.exact ? "exact" : "ai" };
    });
    return [out, points, degraded];
  } catch (e) {
    if (!(e instanceof AIError)) throw e;
    return [items.map((it) => ({ verdict: it.exact ? "correct" : "wrong", supplement: it.exact ? it.supp_fallback : "", by: it.exact ? "exact" : "fallback" })), [], true];
  }
}

export async function review(client, items) {
  const fb = (it) => ({ explanation: it.fallback_explanation, test_prompt: it.source_sentence, test_answer: it.expected_sentence, by: "fallback" });
  try {
    const obj = await client.generateJson(prompts.SYSTEM, prompts.review(items), "review", { items });
    const got = byI(obj.items);
    let degraded = false;
    const out = items.map((it) => {
      const r = got.get(it.i) || {};
      const [ex, tp, ta] = ["explanation", "test_prompt", "test_answer"].map((k) => String(r[k] ?? "").trim());
      if (ex && tp && ta) return { explanation: ex, test_prompt: tp, test_answer: ta, by: "ai" };
      degraded = true;
      return fb(it);
    });
    return [out, degraded];
  } catch (e) {
    if (!(e instanceof AIError)) throw e;
    return [items.map(fb), true];
  }
}

export async function finish(client, items) {
  try {
    const obj = await client.generateJson(prompts.SYSTEM, prompts.finish(items), "finish", { items });
    const got = byI(obj.results);
    const points = strList(obj.points, 4);
    let degraded = false;
    const out = items.map((it) => {
      const r = got.get(it.i);
      let v = r ? normVerdict(r.verdict) : null;
      if (it.exact) v = "correct";
      if (v === null) {
        degraded = true;
        return { verdict: "wrong", feedback: `正解例: ${it.test_answer}`, by: "fallback" };
      }
      const f = r ? String(r.feedback ?? "").trim() : "";
      return { verdict: v, feedback: f || `正解例: ${it.test_answer}`, by: it.exact ? "exact" : "ai" };
    });
    return [out, points, degraded];
  } catch (e) {
    if (!(e instanceof AIError)) throw e;
    return [items.map((it) => ({ verdict: it.exact ? "correct" : "wrong", feedback: `正解例: ${it.test_answer}`, by: it.exact ? "exact" : "fallback" })), [], true];
  }
}

export function makeClient(env, logger) {
  if (env.__aiClient) {
    env.__aiClient.logger = logger; // テストで差し込んだクライアントにも、ログ記録を付ける
    return env.__aiClient;
  }
  if (env.AI_MODE === "mock") return new MockClient({ logger });
  const tb = String(env.GEMINI_THINKING_BUDGET ?? "").trim();
  return new GeminiClient({
    apiKey: env.GEMINI_API_KEY || "",
    model: env.GEMINI_MODEL || "gemini-flash-latest",
    baseUrl: env.GEMINI_BASE_URL || "https://generativelanguage.googleapis.com/v1beta",
    timeoutMs: intEnv(env.AI_TIMEOUT_MS, 25000),
    maxRetries: intEnv(env.AI_MAX_RETRIES, 2),
    thinkingBudget: /^-?\d+$/.test(tb) ? Number(tb) : null,
    logger,
  });
}

/** 管理者の「AI接続チェック」。キー・モデル名・採点の動作を確認し、原因の案内を返す（キーは返さない）。 */
export async function checkConnection(env) {
  const out = { mode: env.AI_MODE || "gemini", model: env.GEMINI_MODEL || "gemini-flash-latest", steps: [] };
  const step = (ok, msg) => out.steps.push({ ok, msg });
  if (env.AI_MODE === "mock" || env.__aiClient) {
    step(true, "AI_MODE=mock（簡易モード）です。Geminiには接続していません");
    out.ok = true;
    return out;
  }
  const key = env.GEMINI_API_KEY || "";
  if (!key) {
    step(false, "GEMINI_API_KEY が設定されていません（Cloudflareの「変数とシークレット」に、シークレットとして登録してください）");
    out.ok = false;
    return out;
  }
  if (/\s|["'`]/.test(key)) {
    step(false, "キーに空白や引用符が含まれています。登録し直してください");
    out.ok = false;
    return out;
  }
  step(true, `キーの形: ${key.startsWith("AQ.") ? "新方式（AQ.）" : key.startsWith("AIza") ? "従来方式（AIza）" : "不明な形式"}、${key.length}文字`);
  const base = (env.GEMINI_BASE_URL || "https://generativelanguage.googleapis.com/v1beta").replace(/\/+$/, "");
  try {
    const res = await fetch(`${base}/models?pageSize=1000`, { headers: { "x-goog-api-key": key } });
    if (!res.ok) {
      let e = {};
      try { e = (await res.json()).error || {}; } catch { /* 本文なし */ }
      const reasons = (e.details || []).map((d) => d.reason || "").join(",");
      step(false, `キーを確認できません（HTTP ${res.status} ${e.status || ""} ${reasons}）`);
      if (/ACCESS_TOKEN_TYPE_UNSUPPORTED/.test(reasons)) step(false, "新方式（AQ.）のキーで報告されている問題です。AI Studioで別のプロジェクトを選んで、キーを作り直してください");
      else if (res.status === 400) step(false, "キーが無効です。貼り間違い・削除済み・制限の設定を確認してください");
      else if (res.status === 403) step(false, "権限エラーです。Gemini APIの有効化、キーのAPI制限、課金・地域の制限を確認してください");
      out.ok = false;
      return out;
    }
    const models = ((await res.json()).models || []).filter((m) => (m.supportedGenerationMethods || []).includes("generateContent")).map((m) => m.name.replace(/^models\//, ""));
    step(true, `キーは有効です（使えるモデル ${models.length} 件）`);
    if (models.includes(out.model)) step(true, `モデル ${out.model} は使えます`);
    else {
      const stable = models.filter((n) => /^gemini-\d+(\.\d+)?-flash$/.test(n)).sort((a, b) => parseFloat(b.match(/\d+(\.\d+)?/)[0]) - parseFloat(a.match(/\d+(\.\d+)?/)[0]));
      out.suggest = stable[0] || models.find((n) => n.includes("flash")) || models[0] || null;
      step(false, `モデル ${out.model} は一覧にありません。おすすめ: ${out.suggest}（Cloudflareの変数 GEMINI_MODEL を変更してください）`);
      out.ok = false;
      return out;
    }
  } catch {
    step(false, "Googleに接続できませんでした。時間をおいて、もう一度試してください");
    out.ok = false;
    return out;
  }
  const logs = [];
  const client = makeClient(env, (...a) => logs.push(a));
  const t0 = Date.now();
  const [res] = await judgeSentences(client, [{ i: 1, direction: "ja2en", question: "彼女は毎日テニスをします。", expected: "She plays tennis every day.", answer: "She play tennis every day." }]);
  const ms = Date.now() - t0;
  if (res[0].by === "ai") step(true, `採点テスト: 判定 ${res[0].verdict}（close か wrong なら正常） / ${(ms / 1000).toFixed(1)}秒`);
  else step(false, `採点テストに失敗しました（${logs.map((l) => l[4]).filter(Boolean).pop() || "不明"}）`);
  out.ok = out.steps.every((s) => s.ok);
  out.latency_ms = ms;
  return out;
}
