// 問題データ（CSV）の取り込み。1回の送信（数百行まで）の中で、1行でも不備があれば全体を取り込まず、行番号つきで返す。
// 大きなファイルは、画面側で分割して送る（無料枠のCPU時間に収めるため）。
import { chunk } from "./util.js";
import { targetInSentence } from "./engine.js";

const COLS = {
  level: ["level", "級段", "級・段", "級"], seq: ["seq", "番号", "問題番号", "no"], en: ["en", "英文", "英語", "en_text"], ja: ["ja", "和文", "日本語", "ja_text"],
  target_en: ["target_en", "英単語", "対象英単語"], target_ja: ["target_ja", "日本語の単語", "対象日本語", "和単語"], grammar_note: ["grammar_note", "文法メモ", "文法", "メモ"],
};
const MAX_LEN = 300;
export const MAX_ROWS_PER_REQUEST = 250;

/** RFC4180 準拠の簡易CSVパーサ（引用符・引用符内の改行とカンマに対応） */
export function parseCsv(text) {
  const rows = [];
  let row = [], cell = "", inQ = false;
  const s = String(text).replace(/^\ufeff/, "");
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (inQ) {
      if (c === '"') { if (s[i + 1] === '"') { cell += '"'; i++; } else inQ = false; }
      else cell += c;
    } else if (c === '"') inQ = true;
    else if (c === ",") { row.push(cell); cell = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && s[i + 1] === "\n") i++;
      row.push(cell); cell = "";
      rows.push(row); row = [];
    } else cell += c;
  }
  if (cell !== "" || row.length) { row.push(cell); rows.push(row); }
  return rows;
}

export function levelCode(name) {
  const s = String(name ?? "").trim().normalize("NFKC");
  if (s === "初段") return "d1";
  let m = s.match(/^(\d{1,2})\s*級$/);
  if (m && +m[1] >= 1 && +m[1] <= 20) return `k${+m[1]}`;
  m = s.match(/^(\d{1,2})\s*段$/);
  if (m && +m[1] >= 1 && +m[1] <= 20) return `d${+m[1]}`;
  return /^[kd]\d{1,2}$/i.test(s) ? s.toLowerCase() : null;
}

export async function importCsv(db, text, { dryRun = false, rowOffset = 0 } = {}) {
  const table = parseCsv(text).filter((r) => r.some((c) => c.trim() !== ""));
  if (!table.length) return { ok: false, errors: [{ row: 0, message: "CSVが空です" }], imported: 0 };
  const head = table[0].map((h) => h.trim().toLowerCase());
  const idx = {};
  for (const [k, names] of Object.entries(COLS)) idx[k] = head.findIndex((h) => names.includes(h));
  if (["level", "seq", "en", "ja", "target_en", "target_ja"].some((k) => idx[k] < 0))
    return { ok: false, errors: [{ row: 1 + rowOffset, message: "1行目に見出し（級・段,番号,英文,和文,英単語,日本語の単語,文法メモ）が必要です" }], imported: 0 };
  if (table.length - 1 > MAX_ROWS_PER_REQUEST) return { ok: false, errors: [{ row: 0, message: `1回に送れるのは${MAX_ROWS_PER_REQUEST}行までです` }], imported: 0 };
  const levels = new Map((await db.prepare("SELECT id, code FROM levels").all()).results.map((l) => [l.code, l.id]));
  const rows = [], errors = [], seen = new Set();
  table.slice(1).forEach((r, k) => {
    const n = k + 2 + rowOffset;
    const g = Object.fromEntries(Object.keys(COLS).map((c) => [c, idx[c] >= 0 ? (r[idx[c]] ?? "").trim() : ""]));
    const code = levelCode(g.level);
    let err = null;
    if (!code || !levels.has(code)) err = `級・段が読み取れません（${JSON.stringify(g.level)}）。例: 20級 / 1級 / 初段 / 20段`;
    else if (!/^\d+$/.test(g.seq) || +g.seq < 1 || +g.seq > 999) err = `問題番号が数字ではありません（${JSON.stringify(g.seq)}）`;
    else if (!(g.en && g.ja && g.target_en && g.target_ja)) err = "英文・和文・英単語・日本語の単語のどれかが空です";
    else if ([g.en, g.ja, g.target_en, g.target_ja, g.grammar_note].some((x) => [...x].length > MAX_LEN)) err = `${MAX_LEN}文字を超える項目があります`;
    else if (!targetInSentence(g.en, g.ja, g.target_en, g.target_ja)) err = "英単語が英文の中に、または日本語の単語が和文の中に見つかりません（空欄問題が作れません）";
    else {
      const key = `${code}:${+g.seq}`;
      if (seen.has(key)) err = `${g.level}の${g.seq}番がCSV内で重複しています`;
      seen.add(key);
    }
    if (err) errors.push({ row: n, message: err });
    else rows.push([levels.get(code), +g.seq, g.en, g.ja, g.target_en, g.target_ja, g.grammar_note, `${code}:${+g.seq}`]);
  });
  if (!rows.length && !errors.length) errors.push({ row: 0, message: "データ行がありません" });
  if (errors.length) return { ok: false, errors: errors.slice(0, 50), error_count: errors.length, imported: 0 };
  const keys = rows.map((r) => r[7]);
  if (dryRun) return { ok: true, dry_run: true, imported: 0, count: rows.length, keys, errors: [] };
  const up = db.prepare(`INSERT INTO questions(level_id,seq,en_text,ja_text,target_en,target_ja,grammar_note) VALUES (?,?,?,?,?,?,?)
    ON CONFLICT(level_id,seq) DO UPDATE SET en_text=excluded.en_text, ja_text=excluded.ja_text, target_en=excluded.target_en, target_ja=excluded.target_ja, grammar_note=excluded.grammar_note`);
  const touched = [...new Set(rows.map((r) => r[0]))];
  const stmts = rows.map((r) => up.bind(...r.slice(0, 7)));
  for (const ids of chunk(touched, 20))
    stmts.push(db.prepare(`UPDATE levels SET total=(SELECT COUNT(*) FROM questions WHERE level_id=levels.id), max_seq=COALESCE((SELECT MAX(seq) FROM questions WHERE level_id=levels.id),0)
      WHERE id IN (${ids.map(() => "?").join(",")})`).bind(...ids));
  await db.batch(stmts);
  return { ok: true, imported: rows.length, errors: [], keys };
}
