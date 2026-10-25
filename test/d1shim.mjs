// Cloudflare D1 の代役（Node の node:sqlite で、D1 と同じ使い方ができるようにしたもの）。テスト用。
import { DatabaseSync } from "node:sqlite";

const norm = (p) => p.map((v) => (v === undefined ? null : typeof v === "boolean" ? (v ? 1 : 0) : v));

class Stmt {
  constructor(db, sql) { this.db = db; this.sql = sql; this.params = []; }
  bind(...p) { const s = new Stmt(this.db, this.sql); s.params = norm(p); return s; }
  _isRead() { return /^\s*(SELECT|WITH|PRAGMA)/i.test(this.sql); }
  async first(col) { const r = this.db._stmt(this.sql).get(...this.params); if (!r) return null; return col ? r[col] : { ...r }; }
  async all() { return { results: this.db._stmt(this.sql).all(...this.params).map((r) => ({ ...r })), meta: {} }; }
  async run() { return this._run(); }
  _run() {
    if (this._isRead()) return { results: this.db._stmt(this.sql).all(...this.params).map((r) => ({ ...r })), meta: { changes: 0 }, success: true };
    const r = this.db._stmt(this.sql).run(...this.params);
    return { success: true, meta: { changes: Number(r.changes), last_row_id: Number(r.lastInsertRowid) } };
  }
}

export class D1Shim {
  constructor(path = ":memory:") {
    this.sqlite = new DatabaseSync(path);
    this.sqlite.exec("PRAGMA foreign_keys=ON; PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;");
    this.cache = new Map();
    this.queries = 0;
    this.rowsWritten = 0;
  }
  _stmt(sql) { this.queries++; let s = this.cache.get(sql); if (!s) { s = this.sqlite.prepare(sql); this.cache.set(sql, s); } return s; }
  prepare(sql) { return new Stmt(this, sql); }
  async batch(stmts) {
    this.sqlite.exec("BEGIN");
    try {
      const out = stmts.map((s) => s._run());
      this.sqlite.exec("COMMIT");
      return out;
    } catch (e) {
      this.sqlite.exec("ROLLBACK");
      throw e;
    }
  }
  async exec(sql) { this.sqlite.exec(sql); return { count: 1 }; }
}
