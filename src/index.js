// えいご道場 — Cloudflare Workers エントリポイント（API と、画面(静的ファイル)の配信）
import { ApiError, intEnv, jsonResponse, timingSafeEqual } from "./util.js";
import { ensureSchema } from "./schema.js";
import * as auth from "./auth.js";
import * as engine from "./engine.js";
import * as stats from "./stats.js";
import { checkConnection, makeClient } from "./ai.js";
import { importCsv } from "./importer.js";

const LOGIN_RE = /^[A-Za-z0-9_.-]{3,32}$/;
const CSP = "default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data:; frame-ancestors 'none'";

function secure(resp, isApi) {
  const r = new Response(resp.body, resp);
  r.headers.set("X-Content-Type-Options", "nosniff");
  r.headers.set("X-Frame-Options", "DENY");
  r.headers.set("Referrer-Policy", "same-origin");
  r.headers.set("Content-Security-Policy", CSP);
  if (isApi) r.headers.set("Cache-Control", "no-store");
  return r;
}

async function flushAiLogs(db, logs) {
  try {
    await db.batch(logs.map(([purpose, model, ok, ms, error]) => db.prepare("INSERT INTO ai_logs(purpose,model,ok,latency_ms,error) VALUES (?,?,?,?,?)").bind(purpose, model, ok ? 1 : 0, Math.round(ms || 0), error)));
  } catch (e) {
    console.error("ai_logs write failed", e);
  }
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (!url.pathname.startsWith("/api/")) return secure(await env.ASSETS.fetch(request), false);
    const aiLogs = [];
    let resp;
    try {
      await ensureSchema(env.DB);
      resp = await route(request, env, url, aiLogs);
    } catch (e) {
      if (e instanceof ApiError) resp = jsonResponse({ error: e.message, ...e.extra }, e.status);
      else {
        console.error("unhandled", e && e.stack ? e.stack : e);
        resp = jsonResponse({ error: "サーバーでエラーが起きました。少し待ってから、もう一度ためしてください。" }, 500);
      }
    }
    if (aiLogs.length) ctx.waitUntil(flushAiLogs(env.DB, aiLogs));
    return secure(resp, true);
  },
};

async function route(request, env, url, aiLogs) {
  const db = env.DB;
  const method = request.method;
  const path = url.pathname;

  if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
    const origin = request.headers.get("Origin");
    if (origin) {
      let host = "";
      try { host = new URL(origin).host; } catch { /* 不正なOrigin */ }
      if (host !== url.host) throw new ApiError(403, "不正なリクエストです");
    }
    if (!(request.headers.get("Content-Type") || "").includes("application/json")) throw new ApiError(415, "JSON形式で送信してください");
  }
  const body = method === "GET" ? {} : await request.json().catch(() => ({}));
  const days = stats.clampDays(url.searchParams.get("days"));
  const ok = (obj, headers) => jsonResponse(obj, 200, headers);

  if (path === "/api/healthz" && method === "GET") {
    await db.prepare("SELECT 1").first();
    return ok({ ok: true });
  }
  if (path === "/api/status" && method === "GET") {
    const a = await db.prepare("SELECT 1 AS x FROM users WHERE role='admin' LIMIT 1").first();
    return ok({ needs_setup: !a });
  }
  if (path === "/api/setup" && method === "POST") return setup(db, env, request, body, ok);
  if (path === "/api/login" && method === "POST") {
    const { user, cookie } = await auth.login(db, env, request, body.login_id, body.password);
    return ok(user, { "Set-Cookie": cookie });
  }
  if (path === "/api/logout" && method === "POST") {
    const tok = auth.readCookie(request, "sid");
    if (tok) {
      const { sha256Hex } = await import("./util.js");
      await db.prepare("DELETE FROM sessions WHERE token_hash=?").bind(await sha256Hex(tok)).run();
    }
    return ok({ ok: true }, { "Set-Cookie": auth.clearCookieHeader(env) });
  }

  const user = await auth.getSessionUser(db, request);
  const needUser = () => { if (!user) throw new ApiError(401, "ログインしてください"); return user; };
  const needAdmin = () => { if (needUser().role !== "admin") throw new ApiError(403, "この操作は管理者だけができます"); return user; };
  let m;

  // ------------------------------------------------------------ 生徒
  if (path === "/api/me" && method === "GET") return ok(needUser());
  if (path === "/api/levels" && method === "GET") return ok({ levels: await engine.levelsWithProgress(db, needUser().id), directions: engine.DIRECTIONS });
  if (path === "/api/rounds/active" && method === "GET") return ok({ round_id: await engine.activeRoundId(db, needUser().id) });
  if (path === "/api/rounds" && method === "POST") {
    const [rid, resumed] = await engine.startRound(db, needUser().id, String(body.level_code ?? ""), String(body.direction ?? ""), body.start_seq ?? 1);
    return ok({ round_id: rid, resumed });
  }
  if ((m = path.match(/^\/api\/rounds\/(\d+)$/)) && method === "GET") return ok(await engine.buildView(db, +m[1], needUser().id));
  if ((m = path.match(/^\/api\/rounds\/(\d+)\/answers$/)) && method === "POST") {
    const u = needUser();
    const warnings = await engine.submit(db, makeClient(env, (...a) => aiLogs.push(a)), +m[1], u.id, body.answers);
    return ok({ view: await engine.buildView(db, +m[1], u.id), warnings });
  }
  if ((m = path.match(/^\/api\/rounds\/(\d+)\/abandon$/)) && method === "POST") {
    await engine.abandonRound(db, +m[1], needUser().id);
    return ok({ ok: true });
  }
  if (path === "/api/my/stats" && method === "GET") {
    const u = needUser();
    const r = await db.prepare(`SELECT COUNT(*) AS n, COALESCE(SUM(is_correct),0) AS ok, COUNT(DISTINCT date(created_at)) AS days FROM attempts WHERE user_id=? AND stage=1 AND created_at>=datetime('now','-90 days')`).bind(u.id).first();
    return ok({ answered: r.n, accuracy: r.n ? Math.round((r.ok * 1000) / r.n) / 10 : null, days: r.days });
  }

  // ------------------------------------------------------------ 管理者
  if (path === "/api/admin/overview" && method === "GET") { needAdmin(); return ok(await stats.overview(db, env, days)); }
  if (path === "/api/admin/students" && method === "GET") { needAdmin(); return ok({ window_days: days, students: await stats.students(db, env, days) }); }
  if ((m = path.match(/^\/api\/admin\/students\/(\d+)$/)) && method === "GET") {
    needAdmin();
    const d = await stats.studentDetail(db, env, +m[1], days);
    if (!d) throw new ApiError(404, "生徒が見つかりません");
    return ok(d);
  }
  if (path === "/api/admin/hardest" && method === "GET") { needAdmin(); return ok({ window_days: days, questions: await stats.hardestQuestions(db, days) }); }
  if (path === "/api/admin/usage" && method === "GET") { needAdmin(); return ok({ window_days: days, days: await stats.usage(db, env, days) }); }
  if (path === "/api/admin/questions/counts" && method === "GET") { needAdmin(); return ok({ levels: await stats.questionCounts(db) }); }
  if (path === "/api/admin/export/attempts" && method === "GET") {
    needAdmin();
    const after = Math.max(0, Number(url.searchParams.get("after_id")) || 0);
    const limit = Math.min(Math.max(1, Number(url.searchParams.get("limit")) || 500), 1000);
    return ok({ header: stats.CSV_HEADER, ...(await stats.exportAttempts(db, env, after, limit)) });
  }
  if (path === "/api/admin/ai_check" && method === "POST") {
    needAdmin();
    const logs = [];
    const res = await checkConnection(env.__aiClient ? env : { ...env, __logger: (...a) => logs.push(a) });
    return ok(res);
  }
  if (path === "/api/admin/users" && method === "POST") { needAdmin(); return ok(await createUsers(db, env, body.users)); }
  if ((m = path.match(/^\/api\/admin\/users\/(\d+)\/reset_password$/)) && method === "POST") {
    needAdmin();
    await studentOr404(db, +m[1]);
    const pw = String(body.password ?? "").trim() || auth.genPassword();
    if ([...pw].length < 6) throw new ApiError(400, "パスワードは6文字以上にしてください");
    await db.batch([db.prepare("UPDATE users SET password_hash=? WHERE id=?").bind(await auth.hashPassword(pw, auth.iterCount(env)), +m[1]), db.prepare("DELETE FROM sessions WHERE user_id=?").bind(+m[1])]);
    return ok({ password: pw });
  }
  if ((m = path.match(/^\/api\/admin\/users\/(\d+)\/active$/)) && method === "POST") {
    needAdmin();
    await studentOr404(db, +m[1]);
    const flag = body.active ? 1 : 0;
    const stmts = [db.prepare("UPDATE users SET is_active=? WHERE id=?").bind(flag, +m[1])];
    if (!flag) stmts.push(db.prepare("DELETE FROM sessions WHERE user_id=?").bind(+m[1]));
    await db.batch(stmts);
    return ok({ ok: true, active: !!flag });
  }
  if (path === "/api/admin/questions/import" && method === "POST") {
    needAdmin();
    const res = await importCsv(db, String(body.csv ?? ""), { dryRun: !!body.dry_run, rowOffset: Math.max(0, Number(body.row_offset) || 0) });
    return jsonResponse(res, res.ok ? 200 : 400);
  }
  throw new ApiError(404, "見つかりません");
}

async function studentOr404(db, id) {
  const row = await db.prepare("SELECT id FROM users WHERE id=? AND role='student'").bind(id).first();
  if (!row) throw new ApiError(404, "生徒が見つかりません");
  return row;
}

async function setup(db, env, request, body, ok) {
  const token = String(env.SETUP_TOKEN || "");
  if (!token) throw new ApiError(403, "SETUP_TOKEN が設定されていません。Cloudflareの「変数とシークレット」に、シークレットとして登録してください");
  const ip = request.headers.get("CF-Connecting-IP") || "local";
  const now = Math.floor(Date.now() / 1000);
  const f = await db.prepare("SELECT COUNT(*) AS n FROM login_attempts WHERE login_id='__setup__' AND ts>?").bind(now - 600).first();
  if (f.n >= 5) throw new ApiError(429, "間違いが続いたため、10分ほど待ってからもう一度ためしてください");
  if (!timingSafeEqual(String(body.setup_token ?? ""), token)) {
    await db.prepare("INSERT INTO login_attempts(login_id,ip,ts,ok) VALUES ('__setup__',?,?,0)").bind(ip, now).run();
    throw new ApiError(403, "セットアップ用のトークンが違います");
  }
  const pw = String(body.password ?? "");
  if ([...pw].length < 8) throw new ApiError(400, "管理者のパスワードは8文字以上にしてください");
  const res = await db.prepare("INSERT INTO users(login_id,password_hash,role,label) SELECT 'admin',?, 'admin','管理者' WHERE NOT EXISTS (SELECT 1 FROM users WHERE role='admin')")
    .bind(await auth.hashPassword(pw, auth.iterCount(env))).run();
  if (res.meta.changes === 0) throw new ApiError(409, "管理者はすでに作成されています");
  return ok({ ok: true });
}

async function createUsers(db, env, list) {
  if (!Array.isArray(list) || !list.length || list.length > 20) throw new ApiError(400, "生徒の一覧（1回に1〜20人）を送ってください");
  const created = [], errors = [];
  for (const it of list) {
    const lid = String(it?.login_id ?? "").trim();
    const label = String(it?.label ?? "").trim().slice(0, 40);
    let pw = String(it?.password ?? "").trim();
    if (!LOGIN_RE.test(lid)) { errors.push({ login_id: lid, message: "IDは半角英数字と _ . - の3〜32文字にしてください" }); continue; }
    if (pw && [...pw].length < 6) { errors.push({ login_id: lid, message: "パスワードは6文字以上にしてください" }); continue; }
    pw = pw || auth.genPassword();
    try {
      await db.prepare("INSERT INTO users(login_id,password_hash,role,label) VALUES (?,?, 'student', ?)").bind(lid, await auth.hashPassword(pw, auth.iterCount(env)), label).run();
      created.push({ login_id: lid, label, password: pw });
    } catch (e) {
      if (/UNIQUE/i.test(String(e.message))) errors.push({ login_id: lid, message: "このIDはすでに使われています" });
      else throw e;
    }
  }
  return { created, errors };
}
