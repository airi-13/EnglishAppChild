"""問題データ（CSV）の取り込み。1行でも不備があれば全体を取り込まず、行番号つきでエラーを返す。"""
import csv
import io
import re
import unicodedata

from .db import tx
from .engine import target_in_sentence

COLS = {
    "level": ("level", "級段", "級・段", "級"),
    "seq": ("seq", "番号", "問題番号", "no"),
    "en": ("en", "英文", "英語", "en_text"),
    "ja": ("ja", "和文", "日本語", "ja_text"),
    "target_en": ("target_en", "英単語", "対象英単語"),
    "target_ja": ("target_ja", "日本語の単語", "対象日本語", "和単語"),
    "grammar_note": ("grammar_note", "文法メモ", "文法", "メモ"),
}
MAX_LEN = 300


def level_code(name):
    s = unicodedata.normalize("NFKC", (name or "").strip())
    if s == "初段":
        return "d1"
    m = re.fullmatch(r"(\d{1,2})\s*級", s)
    if m and 1 <= int(m.group(1)) <= 20:
        return f"k{int(m.group(1))}"
    m = re.fullmatch(r"(\d{1,2})\s*段", s)
    if m and 1 <= int(m.group(1)) <= 20:
        return f"d{int(m.group(1))}"
    if re.fullmatch(r"[kd]\d{1,2}", s.lower()):
        return s.lower()
    return None


def _pick(row, key):
    for name in COLS[key]:
        for k in row:
            if k and k.strip().lower() == name:
                return (row[k] or "").strip()
    return None


def import_csv(conn, text):
    text = (text or "").lstrip("\ufeff")
    if not text.strip():
        return {"ok": False, "errors": [{"row": 0, "message": "CSVが空です"}], "imported": 0}
    rdr = csv.DictReader(io.StringIO(text))
    levels = {r["code"]: r["id"] for r in conn.execute("SELECT id, code FROM levels")}
    rows, errors, seen = [], [], set()
    for n, row in enumerate(rdr, start=2):
        if not any((v or "").strip() for v in row.values() if isinstance(v, str)):
            continue
        g = {k: _pick(row, k) for k in COLS}
        err = None
        code = level_code(g["level"]) if g["level"] is not None else None
        if code is None or code not in levels:
            err = f"級・段が読み取れません（{g['level']!r}）。例: 20級 / 1級 / 初段 / 20段"
        elif not (g["seq"] or "").isdigit() or not (1 <= int(g["seq"]) <= 999):
            err = f"問題番号が数字ではありません（{g['seq']!r}）"
        elif not all(g[k] for k in ("en", "ja", "target_en", "target_ja")):
            err = "英文・和文・英単語・日本語の単語のどれかが空です"
        elif any(len(g[k] or "") > MAX_LEN for k in ("en", "ja", "target_en", "target_ja", "grammar_note")):
            err = f"{MAX_LEN}文字を超える項目があります"
        elif not target_in_sentence(g["en"], g["ja"], g["target_en"], g["target_ja"]):
            err = "英単語が英文の中に、または日本語の単語が和文の中に見つかりません（空欄問題が作れません）"
        else:
            key = (code, int(g["seq"]))
            if key in seen:
                err = f"{g['level']}の{g['seq']}番がCSV内で重複しています"
            seen.add(key)
        if err:
            errors.append({"row": n, "message": err})
        else:
            rows.append((levels[code], int(g["seq"]), g["en"], g["ja"], g["target_en"], g["target_ja"], g["grammar_note"] or ""))
    if not rows and not errors:
        errors.append({"row": 0, "message": "データ行がありません。1行目に見出し（級・段,番号,英文,和文,英単語,日本語の単語,文法メモ）が必要です"})
    if errors:
        return {"ok": False, "errors": errors[:50], "error_count": len(errors), "imported": 0}
    with tx(conn):
        conn.executemany(
            """INSERT INTO questions(level_id,seq,en_text,ja_text,target_en,target_ja,grammar_note) VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(level_id,seq) DO UPDATE SET en_text=excluded.en_text, ja_text=excluded.ja_text,
                 target_en=excluded.target_en, target_ja=excluded.target_ja, grammar_note=excluded.grammar_note""", rows)
    return {"ok": True, "imported": len(rows), "errors": []}
