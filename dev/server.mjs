// 手元の確認用サーバー（Cloudflare Workers の代わりに Node で動かす）。  node dev/server.mjs
// データベースは node:sqlite（D1の代役）。本番のCloudflareでは使われません。
import http from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";
import worker from "../src/index.js";
import { D1Shim } from "../test/d1shim.mjs";

const PUBLIC = fileURLToPath(new URL("../public/", import.meta.url));
const TYPES = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".csv": "text/csv; charset=utf-8" };
const assets = {
  async fetch(req) {
    let p = new URL(req.url).pathname;
    if (p === "/") p = "/index.html";
    const file = normalize(join(PUBLIC, p));
    if (!file.startsWith(PUBLIC)) return new Response("forbidden", { status: 403 });
    try { return new Response(await readFile(file), { headers: { "Content-Type": TYPES[extname(file)] || "application/octet-stream" } }); }
    catch { return new Response("not found", { status: 404 }); }
  },
};
const e = process.env;
const env = {
  DB: new D1Shim(e.DB_FILE || ":memory:"), ASSETS: assets,
  AI_MODE: e.AI_MODE || "mock", GEMINI_API_KEY: e.GEMINI_API_KEY || "", GEMINI_MODEL: e.GEMINI_MODEL || "gemini-flash-latest", GEMINI_BASE_URL: e.GEMINI_BASE_URL,
  SETUP_TOKEN: e.SETUP_TOKEN || "dev-setup-token", COOKIE_SECURE: "0", PBKDF2_ITERS: e.PBKDF2_ITERS || "30000", TZ_OFFSET_HOURS: "9",
};
const port = Number(e.PORT || 5050);
http.createServer(async (req, res) => {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const request = new Request(`http://${req.headers.host}${req.url}`, { method: req.method, headers: req.headers, body: ["GET", "HEAD"].includes(req.method) ? undefined : Buffer.concat(chunks) });
  const pending = [];
  const r = await worker.fetch(request, env, { waitUntil: (p) => pending.push(p) });
  const headers = {};
  r.headers.forEach((v, k) => { if (k !== "set-cookie") headers[k] = v; });
  const cookies = r.headers.getSetCookie ? r.headers.getSetCookie() : [];
  if (cookies.length) headers["set-cookie"] = cookies;
  res.writeHead(r.status, headers);
  res.end(Buffer.from(await r.arrayBuffer()));
  await Promise.all(pending);
}).listen(port, "127.0.0.1", () => console.log(`http://127.0.0.1:${port} （AI_MODE=${env.AI_MODE}）`));
