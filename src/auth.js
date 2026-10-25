// パスワード・セッション・ログイン制限
import { ApiError, b64url, fromHex, hex, intEnv, sha256Hex, timingSafeEqual } from "./util.js";

const enc = new TextEncoder();
const MAX_ITERS = 100000; // Cloudflare Workers の PBKDF2 は 10万回まで
const PW_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789";

async function pbkdf2(pw, salt, iters) {
  const key = await crypto.subtle.importKey("raw", enc.encode(pw), "PBKDF2", false, ["deriveBits"]);
  return crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt, iterations: iters }, key, 256);
}

export async function hashPassword(pw, iters = 30000) {
  const it = Math.min(Math.max(1000, iters), MAX_ITERS);
  const salt = crypto.getRandomValues(new Uint8Array(16));
  return `pbkdf2$${it}$${hex(salt)}$${hex(await pbkdf2(pw, salt, it))}`;
}

export async function verifyPassword(pw, stored) {
  try {
    const [kind, it, salt, h] = String(stored).split("$");
    const iters = Number(it);
    if (kind !== "pbkdf2" || !(iters >= 1 && iters <= MAX_ITERS)) return false;
    return timingSafeEqual(hex(await pbkdf2(pw, fromHex(salt), iters)), h);
  } catch {
    return false;
  }
}

export function genPassword(n = 8) {
  const r = crypto.getRandomValues(new Uint8Array(n));
  return [...r].map((b) => PW_ALPHABET[b % PW_ALPHABET.length]).join("");
}

export const iterCount = (env) => intEnv(env.PBKDF2_ITERS, 30000);

// ---- セッション
export function readCookie(request, name) {
  const c = request.headers.get("Cookie") || "";
  for (const part of c.split(";")) {
    const [k, ...v] = part.trim().split("=");
    if (k === name) return v.join("=");
  }
  return null;
}

export async function getSessionUser(db, request) {
  const tok = readCookie(request, "sid");
  if (!tok) return null;
  const row = await db
    .prepare(`SELECT u.id, u.login_id, u.label, u.role FROM sessions s JOIN users u ON u.id=s.user_id
              WHERE s.token_hash=? AND s.expires_at>datetime('now') AND u.is_active=1`)
    .bind(await sha256Hex(tok))
    .first();
  return row ? { ...row } : null;
}

export async function createSession(db, userId, days) {
  const tok = b64url(crypto.getRandomValues(new Uint8Array(32)));
  const exp = new Date(Date.now() + days * 864e5).toISOString().slice(0, 19).replace("T", " ");
  const stmts = [db.prepare("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES (?,?,?)").bind(await sha256Hex(tok), userId, exp)];
  if (Math.random() < 0.05) stmts.push(db.prepare("DELETE FROM sessions WHERE expires_at<datetime('now')")); // たまに期限切れを掃除（書き込み行数の節約）
  await db.batch(stmts);
  return tok;
}

export function cookieHeader(env, tok, days) {
  const secure = String(env.COOKIE_SECURE ?? "1") === "1" ? "; Secure" : "";
  return `sid=${tok}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${days * 86400}${secure}`;
}
export const clearCookieHeader = (env) => `sid=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0${String(env.COOKIE_SECURE ?? "1") === "1" ? "; Secure" : ""}`;

// ---- ログイン（失敗が続くとロック）
export async function login(db, env, request, loginId, password) {
  const lid = String(loginId ?? "").trim();
  const ip = request.headers.get("CF-Connecting-IP") || "local";
  const now = Math.floor(Date.now() / 1000);
  const [f1, f2] = await Promise.all([
    db.prepare("SELECT COUNT(*) AS n FROM login_attempts WHERE login_id=? AND ts>?").bind(lid.toLowerCase(), now - 600).first(),
    db.prepare("SELECT COUNT(*) AS n FROM login_attempts WHERE ip=? AND ts>?").bind(ip, now - 600).first(),
  ]);
  if (f1.n >= 5 || f2.n >= 30) throw new ApiError(429, "間違いが続いたため、10分ほど待ってからもう一度ためしてください");
  const row = await db.prepare("SELECT * FROM users WHERE login_id=?").bind(lid).first();
  const ok = (await verifyPassword(String(password ?? ""), row ? row.password_hash : "pbkdf2$30000$00000000000000000000000000000000$00")) && !!row && !!row.is_active;
  if (!ok) {
    // 失敗だけを記録する（書き込み行数の節約）。成功すると、そのIDの失敗記録は消える
    const stmts = [db.prepare("INSERT INTO login_attempts(login_id,ip,ts,ok) VALUES (?,?,?,0)").bind(lid.toLowerCase(), ip, now)];
    if (Math.random() < 0.05) stmts.push(db.prepare("DELETE FROM login_attempts WHERE ts<?").bind(now - 86400));
    await db.batch(stmts);
    throw new ApiError(401, "IDまたはパスワードが違います");
  }
  if (f1.n > 0) await db.prepare("DELETE FROM login_attempts WHERE login_id=?").bind(lid.toLowerCase()).run();
  const days = intEnv(env.SESSION_DAYS, 30);
  const tok = await createSession(db, row.id, days);
  return { user: { id: row.id, login_id: row.login_id, label: row.label, role: row.role }, cookie: cookieHeader(env, tok, days) };
}
