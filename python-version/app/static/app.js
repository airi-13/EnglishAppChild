"use strict";
/* えいご道場 フロントエンド。ユーザー入力は textContent だけで表示する（innerHTML は使わない）。 */
const $app = document.getElementById("app");
const S = { user: null, levels: [], dirs: {}, sel: { code: null, dir: "en2ja", seq: 1 }, view: null, busy: false, msg: null, warn: [], active: null, tab: "dash" };

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) el.append(k.nodeType ? k : document.createTextNode(String(k)));
  return el;
}

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {}, credentials: "same-origin" };
  if (opts.body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
  let res;
  try { res = await fetch(path, init); } catch (e) { throw Object.assign(new Error("通信できませんでした。ネットワークを確認してください"), { status: 0 }); }
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("json") ? await res.json() : await res.text();
  if (!res.ok) {
    if (res.status === 401 && S.user) { S.user = null; S.view = null; render(); }
    throw Object.assign(new Error((data && data.error) || "エラーが起きました"), { status: res.status, data });
  }
  return data;
}

const fmtTime = (s) => (s ? new Date(s.replace(" ", "T") + "Z").toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—");
const pct = (v) => (v === null || v === undefined ? "—" : v + "%");

function mount(...nodes) { $app.replaceChildren(...nodes); window.scrollTo(0, 0); }
function topbar() {
  return h("div", { class: "top" }, h("div", { class: "brand" }, "えいご道場"),
    h("div", { class: "who" }, h("span", {}, S.user.label || S.user.login_id),
      S.user.role === "admin" ? h("button", { class: "btn small ghost", onclick: () => { S.view = null; S.tab = "dash"; render(); } }, "管理") : null,
      S.user.role === "admin" ? h("button", { class: "btn small ghost", onclick: () => { S.adminOff = true; S.view = null; render(); } }, "生徒画面") : null,
      h("button", { class: "btn small ghost", onclick: async () => { try { await api("/api/logout", { method: "POST", body: {} }); } catch (e) {} S.user = null; S.view = null; render(); } }, "ログアウト")));
}
function banner(kind, text) { return text ? h("div", { class: "banner " + kind, role: kind === "err" ? "alert" : "status" }, text) : null; }

/* ---------------------------------------------------------------- ログイン */
function loginScreen() {
  const id = h("input", { type: "text", id: "login_id", autocomplete: "username", autocapitalize: "off", spellcheck: "false" });
  const pw = h("input", { type: "password", id: "password", autocomplete: "current-password" });
  const err = h("div");
  const btn = h("button", { class: "btn big", type: "button" }, "ログイン");
  const go = async () => {
    btn.disabled = true; err.replaceChildren();
    try {
      S.user = await api("/api/login", { method: "POST", body: { login_id: id.value, password: pw.value } });
      S.adminOff = false; await boot();
    } catch (e) { err.replaceChildren(banner("err", e.message)); btn.disabled = false; }
  };
  btn.addEventListener("click", go);
  pw.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.isComposing) go(); });
  mount(h("div", { class: "login wrap" }, h("h1", {}, "えいご道場"), h("p", { class: "sub" }, "先生からもらったIDとパスワードを入れてね"),
    h("div", { class: "card" }, h("label", { for: "login_id" }, "ID"), id, h("label", { for: "password" }, "パスワード"), pw, err, h("p", {}, btn))));
}

/* ---------------------------------------------------------------- 生徒: ホーム */
function beltClass(l) { if (l.kind === "dan") return "black"; const n = l.order_no; return n <= 4 ? "" : n <= 8 ? "yellow" : n <= 12 ? "orange" : n <= 16 ? "green" : "brown"; }
function suggestSeq(l, dir) { const n = l.next[dir]; return l.max_seq && n <= l.max_seq ? n : 1; }
function pickLevel(code) {
  S.sel.code = code;
  const l = S.levels.find((x) => x.code === code);
  S.sel.seq = l ? suggestSeq(l, S.sel.dir) : 1;
}

function homeScreen() {
  const cur = S.levels.find((l) => l.code === S.sel.code);
  const chip = (l) => h("button", { class: `chip ${beltClass(l)} ${l.code === S.sel.code ? "sel" : ""}`, disabled: l.total === 0, "aria-pressed": l.code === S.sel.code ? "true" : "false",
    onclick: () => { pickLevel(l.code); homeScreen(); } }, l.name,
    h("small", {}, l.total === 0 ? "準備中" : l.next[S.sel.dir] > 1 ? `${Math.min(l.next[S.sel.dir] - 1, l.total)}/${l.total}` : `${l.total}問`));
  const seq = h("input", { type: "number", min: "1", max: "999", value: S.sel.seq, id: "seq", class: "num", inputmode: "numeric" });
  seq.addEventListener("input", () => { S.sel.seq = seq.value; });
  const err = h("div");
  const start = h("button", { class: "btn big", disabled: !cur, onclick: async () => {
    err.replaceChildren(); start.disabled = true;
    try {
      const r = await api("/api/rounds", { method: "POST", body: { level_code: S.sel.code, direction: S.sel.dir, start_seq: Number(seq.value) } });
      S.view = await api(`/api/rounds/${r.round_id}`); S.warn = []; S.msg = null; roundScreen();
    } catch (e) { err.replaceChildren(banner("err", e.message)); start.disabled = false; }
  } }, "問題を出す");
  const seg = h("div", { class: "seg", role: "group", "aria-label": "問題の向き" },
    Object.entries(S.dirs).map(([k, label]) => h("button", { class: S.sel.dir === k ? "on" : "", "aria-pressed": S.sel.dir === k ? "true" : "false",
      onclick: () => { S.sel.dir = k; pickLevel(S.sel.code); homeScreen(); } }, k === "en2ja" ? "英日（英→日）" : "日英（日→英）")));
  const activeBox = S.active ? h("div", { class: "banner warn" }, "途中の問題があります。", " ",
    h("button", { class: "btn small", onclick: async () => { S.view = await api(`/api/rounds/${S.active}`); S.warn = []; roundScreen(); } }, "つづける"), " ",
    h("button", { class: "btn small ghost", onclick: async () => { await api(`/api/rounds/${S.active}/abandon`, { method: "POST", body: {} }); S.active = null; await boot(); } }, "やめる")) : null;
  mount(topbar(), h("div", { class: "wrap" }, activeBox,
    h("div", { class: "card" }, h("h2", {}, "どの級・段にする？"),
      h("p", { class: "muted" }, "級（20級→1級）をクリアしたら、初段・2段…と進むよ。"),
      h("div", { class: "ladder" }, S.levels.map(chip)),
      h("div", { class: "row" }, h("div", {}, h("label", {}, "向き"), seg), h("div", {}, h("label", { for: "seq" }, "問題番号"), seq),
        h("div", {}, cur ? h("span", { class: "muted" }, `${cur.name}：全${cur.total}問`) : h("span", { class: "muted" }, "級・段を選んでね"))),
      h("p", {}, start), err)));
}

/* ---------------------------------------------------------------- 生徒: 問題 */
const STEPS = ["問題", "空欄補充", "解説とテスト", "まとめ"];
const STAGE_IDX = { answer: 0, fillin: 1, review: 2, done: 3, abandoned: 3 };
function stepper(stage) {
  const i = STAGE_IDX[stage];
  return h("ol", { class: "steps", "aria-label": "進み具合" }, STEPS.map((s, k) => h("li", { class: k === i ? "on" : k < i ? "past" : "" }, s)));
}
function roundHeader(v) {
  return h("div", {}, h("h2", {}, `${v.level_name}　${v.direction === "en2ja" ? "英日" : "日英"}　No.${v.first_no}〜${v.last_no}`), stepper(v.stage));
}
function answerInput(v, rqId, rows) {
  const isEn = v.answer_lang === "en";
  const el = h(rows > 1 ? "textarea" : "input", { "data-rq": rqId, rows: rows > 1 ? rows : null, type: rows > 1 ? null : "text", lang: isEn ? "en" : "ja",
    autocomplete: "off", autocapitalize: isEn ? "off" : null, autocorrect: isEn ? "off" : null, spellcheck: "false", maxlength: "200",
    "aria-label": `No.${rqId}の答え` });
  return el;
}

function roundScreen() {
  const v = S.view;
  if (!v) return homeScreen();
  if (v.stage === "done" || v.stage === "abandoned") return doneScreen(v);
  const errBox = h("div");
  const inputs = [];
  const make = (item, rows) => { const el = answerInput(v, item.rq_id, rows); el.setAttribute("aria-label", `No.${item.no}の答え`); inputs.push(el); return el; };
  let body = [], btnLabel = "答えを送る", busyText = "AIが採点しています…";
  if (v.stage === "answer") {
    body = v.items.map((it) => h("div", { class: "q" }, h("div", { class: "no" }, `No.${it.no}`), h("div", { class: "text" }, it.text), make(it, v.direction === "en2ja" ? 2 : 2)));
    body.unshift(h("p", { class: "muted" }, v.direction === "en2ja" ? "英語を日本語になおそう。全部の問題に答えてから送ってね。" : "日本語を英語になおそう。全部の問題に答えてから送ってね。"));
  } else if (v.stage === "fillin") {
    body = [h("p", { class: "muted" }, "間違えた問題だよ。空欄に入る単語を答えよう。わからない単語は、自分で調べてから答えてね！"),
      ...v.items.map((it) => h("div", { class: "q" }, h("div", { class: "no" }, `No.${it.no}`), h("div", { class: "muted" }, it.source),
        h("div", { class: "text" }, blankNodes(it.blank)), make(it, 1)))];
    busyText = "AIが採点して、解説を作っています…（少し時間がかかるよ）";
  } else if (v.stage === "review") {
    body = [];
    for (const r of v.fillin_results.filter((x) => x.ok)) body.push(h("div", { class: "banner ok" }, h("b", {}, `No.${r.no} 合ってたよ！ `), r.feedback));
    body.push(h("p", { class: "muted" }, "解説を読んでから、確認テストに答えてね。"));
    for (const it of v.items) body.push(h("div", { class: "q" }, h("div", { class: "no" }, `No.${it.no}`), h("div", { class: "muted" }, it.source),
      h("div", { class: "expl" }, it.explanation), h("h3", {}, "確認テスト"), h("div", { class: "text" }, it.test_prompt), make(it, 2)));
    btnLabel = "テストの答えを送る"; busyText = "AIが採点して、まとめを作っています…";
  }
  const btn = h("button", { class: "btn big" }, btnLabel);
  btn.addEventListener("click", async () => {
    if (S.busy) return;
    const answers = {}; let firstEmpty = null;
    for (const el of inputs) { answers[el.dataset.rq] = el.value; if (!el.value.trim() && !firstEmpty) firstEmpty = el; }
    if (firstEmpty) { errBox.replaceChildren(banner("err", "まだ答えていない問題があるよ。全部に答えてから送ってね")); firstEmpty.focus(); return; }
    S.busy = true; btn.disabled = true; errBox.replaceChildren(banner("warn", busyText));
    try {
      const r = await api(`/api/rounds/${v.id}/answers`, { method: "POST", body: { answers } });
      S.view = r.view; S.warn = r.warnings || []; S.busy = false; roundScreen();
    } catch (e) {
      S.busy = false; btn.disabled = false;
      if (e.status === 409) { S.view = await api(`/api/rounds/${v.id}`); roundScreen(); return; }
      errBox.replaceChildren(banner("err", e.message));
    }
  });
  const back = h("button", { class: "btn ghost small", onclick: async () => { S.active = v.id; await boot(true); } }, "ホームにもどる（あとで続ける）");
  mount(topbar(), h("div", { class: "wrap" }, roundHeader(v), ...S.warn.map((w) => banner("warn", w)), h("div", { class: "card" }, body, errBox, h("p", {}, btn)), h("p", {}, back)));
  if (inputs[0]) inputs[0].focus();
}
function blankNodes(text) {
  const parts = text.split("＿＿＿");
  const out = [];
  parts.forEach((p, i) => { out.push(p); if (i < parts.length - 1) out.push(h("span", { class: "blank" }, "＿＿＿")); });
  return out;
}

function doneScreen(v) {
  const mark = (ok) => h("span", { class: "mark " + (ok ? "ok" : "ng"), "aria-label": ok ? "正解" : "不正解" }, ok ? "○" : "×");
  const rows = v.items.map((it) => {
    const first = it.s1_ok === 1;
    let tag = first ? h("span", { class: "pill ok" }, "一発正解") : it.s2_ok === 1 ? h("span", { class: "pill gold" }, "空欄で正解") : it.s3_ok === 1 ? h("span", { class: "pill gold" }, "テストで正解") : h("span", { class: "pill ng" }, "もう一度やろう");
    return h("div", { class: "q" }, h("div", { class: "no" }, mark(first), `No.${it.no}`, tag), h("div", { class: "muted" }, it.source),
      h("div", { class: "text" }, it.expected),
      !first && it.s1_answer ? h("div", { class: "muted" }, `はじめの答え：${it.s1_answer}`) : null,
      it.s3_feedback ? h("div", { class: "muted" }, `確認テスト：${it.test_prompt} → ${it.s3_answer}（${it.s3_ok ? "○" : "×"}） ${it.s3_feedback}`) : null);
  });
  const sm = v.summary;
  let summary = null;
  if (sm && sm.perfect) summary = h("div", { class: "banner ok" }, "全部、一発で正解！すばらしい！");
  else if (sm) summary = h("div", { class: "card" }, h("h3", {}, "次までに覚えること"),
    sm.points.length ? h("ul", {}, sm.points.map((p) => h("li", {}, p))) : null,
    h("h3", {}, "覚える単語リスト"),
    h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "No."), h("th", {}, "英語"), h("th", {}, "日本語"))), h("tbody", {}, sm.words.map((w) => h("tr", {}, h("td", {}, w.no), h("td", {}, w.en), h("td", {}, w.ja))))));
  const nx = v.next;
  const nextBtn = nx ? h("button", { class: "btn big", onclick: async () => {
    try { const r = await api("/api/rounds", { method: "POST", body: { level_code: nx.level_code, direction: v.direction, start_seq: nx.start_seq } });
      S.view = await api(`/api/rounds/${r.round_id}`); S.warn = []; roundScreen(); } catch (e) { alert(e.message); } } },
    nx.level_up ? `${nx.level_name}へ進む` : `続きの問題へ（No.${nx.start_seq}〜）`) : h("div", { class: "banner ok" }, "用意されている問題は、ぜんぶ終わったよ！");
  mount(topbar(), h("div", { class: "wrap" }, roundHeader(v), ...S.warn.map((w) => banner("warn", w)), summary,
    h("div", { class: "card" }, h("h3", {}, "今回の結果"), rows), h("p", {}, nextBtn, " ", h("button", { class: "btn ghost", onclick: async () => { S.view = null; S.warn = []; await boot(true); } }, "ホームへ"))));
}

/* ---------------------------------------------------------------- 管理者 */
const TABS = [["dash", "ダッシュボード"], ["students", "生徒"], ["hard", "間違いの多い問題"], ["usage", "利用状況"], ["questions", "問題データ"]];
async function adminScreen() {
  const box = h("div", { class: "wrap wide" });
  const tabs = h("div", { class: "tabs", role: "tablist" }, TABS.map(([k, label]) => h("button", { class: S.tab === k ? "on" : "", role: "tab", "aria-selected": S.tab === k ? "true" : "false", onclick: () => { S.tab = k; S.studentId = null; adminScreen(); } }, label)));
  mount(topbar(), box); box.append(tabs);
  const body = h("div"); box.append(body);
  body.append(h("p", { class: "muted" }, "読み込み中…"));
  try {
    const fn = { dash: tabDash, students: tabStudents, hard: tabHard, usage: tabUsage, questions: tabQuestions }[S.tab];
    body.replaceChildren(...(await fn()));
  } catch (e) { body.replaceChildren(banner("err", e.message)); }
}
const stat = (n, label) => h("div", { class: "stat" }, h("b", {}, n), h("span", {}, label));
async function tabDash() {
  const o = await api("/api/admin/overview");
  return [h("div", { class: "stats" }, stat(o.students, "登録している生徒"), stat(o.active_students_7d, "直近7日に使った生徒"), stat(o.rounds_done_7d, "直近7日の完了セット"),
    stat(pct(o.accuracy_7d), "直近7日の正答率（最初の回答）"), stat(pct(o.accuracy_all), "全期間の正答率"), stat(o.questions_total, "登録問題数"),
    stat(o.ai_calls_7d, "AI呼び出し（7日）"), stat(pct(o.ai_ok_rate_7d), "AI成功率（7日）"), stat(o.fallback_judgments_7d, "代替採点になった回数（7日）")),
    h("p", { class: "muted" }, "正答率は、各問題に最初に答えたときの正解の割合です（空欄補充や確認テストは含みません）。")];
}
async function tabStudents() {
  if (S.studentId) return studentDetail(S.studentId);
  const d = await api("/api/admin/students");
  const out = [];
  const ta = h("textarea", { rows: "4", placeholder: "1行に1人: ID,表示名（表示名は省略可）\nstudent01,A組1番\nstudent02,A組2番" });
  const res = h("div");
  out.push(h("div", { class: "card" }, h("h3", {}, "生徒を登録する"), h("p", { class: "muted" }, "パスワードは自動で作られ、この画面に1度だけ表示されます。表示名には本名を入れず、ニックネームや出席番号を使うと安全です。"), ta,
    h("p", {}, h("button", { class: "btn", onclick: async () => {
      const users = ta.value.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => { const [login_id, ...r] = l.split(/[,、\t]/); return { login_id: login_id.trim(), label: r.join(",").trim() }; });
      if (!users.length) return;
      try {
        const r = await api("/api/admin/users", { method: "POST", body: { users } });
        res.replaceChildren(r.created.length ? h("div", { class: "banner ok" }, h("b", {}, "作成しました（このパスワードは再表示されません。控えてください）"),
          h("pre", { class: "cred" }, r.created.map((c) => `${c.login_id}\t${c.password}\t${c.label}`).join("\n"))) : null,
          ...r.errors.map((e) => banner("err", `${e.login_id}: ${e.message}`)));
        ta.value = "";
      } catch (e) { res.replaceChildren(banner("err", e.message)); }
    } }, "登録する")), res));
  out.push(h("div", { class: "card scroll" }, h("h3", {}, "生徒一覧"),
    h("table", {}, h("thead", {}, h("tr", {}, ["ID", "表示名", "最終利用", "30日の利用日数", "完了セット", "正答率", ""].map((x) => h("th", {}, x)))),
      h("tbody", {}, d.students.map((s) => h("tr", {}, h("td", {}, s.login_id, s.is_active ? null : h("span", { class: "pill ng" }, "停止中")), h("td", {}, s.label), h("td", {}, fmtTime(s.last_active)), h("td", {}, s.days_30d),
        h("td", {}, s.rounds_done), h("td", {}, pct(s.accuracy)),
        h("td", {}, h("button", { class: "btn small ghost", onclick: () => { S.studentId = s.id; adminScreen(); } }, "くわしく"), " ",
          h("button", { class: "btn small ghost", onclick: async () => { if (!confirm(`${s.login_id} のパスワードを再発行しますか？`)) return; const r = await api(`/api/admin/users/${s.id}/reset_password`, { method: "POST", body: {} }); alert(`新しいパスワード: ${r.password}`); } }, "PW再発行"), " ",
          h("button", { class: "btn small danger", onclick: async () => { await api(`/api/admin/users/${s.id}/active`, { method: "POST", body: { active: !s.is_active } }); adminScreen(); } }, s.is_active ? "停止" : "再開")))))),
    d.students.length ? null : h("p", { class: "muted" }, "まだ生徒がいません。上のフォームから登録してください。"),
    h("p", {}, h("a", { class: "btn small ghost", href: "/api/admin/export/attempts.csv" }, "回答ログをCSVでダウンロード"))));
  return out;
}
async function studentDetail(id) {
  const d = await api(`/api/admin/students/${id}`);
  const dirName = (x) => (x === "en2ja" ? "英日" : "日英");
  return [h("p", {}, h("button", { class: "btn small ghost", onclick: () => { S.studentId = null; adminScreen(); } }, "← 一覧へ")),
    h("h2", {}, `${d.user.login_id}（${d.user.label || "表示名なし"}）`),
    h("div", { class: "card scroll" }, h("h3", {}, "級・段ごとの正答率"), d.by_level.length ? h("table", {}, h("thead", {}, h("tr", {}, ["級・段", "向き", "回答数", "正答率", ""].map((x) => h("th", {}, x)))),
      h("tbody", {}, d.by_level.map((b) => h("tr", {}, h("td", {}, b.level), h("td", {}, dirName(b.direction)), h("td", {}, b.n), h("td", {}, pct(b.accuracy)), h("td", {}, h("div", { class: "bar" }, (() => { const i = h("i"); i.style.width = b.accuracy + "%"; return i; })())))))) : h("p", { class: "muted" }, "まだ回答がありません")),
    h("div", { class: "card scroll" }, h("h3", {}, "よく間違える問題"), d.weak.length ? h("table", {}, h("thead", {}, h("tr", {}, ["級・段", "No.", "向き", "問題", "間違い回数", "最後に間違えた日時"].map((x) => h("th", {}, x)))),
      h("tbody", {}, d.weak.map((w) => h("tr", {}, h("td", {}, w.level), h("td", {}, w.seq), h("td", {}, dirName(w.direction)), h("td", {}, w.en_text, h("br"), h("span", { class: "muted" }, w.ja_text)), h("td", {}, w.wrong_n), h("td", {}, fmtTime(w.last_wrong)))))) : h("p", { class: "muted" }, "間違いはまだありません")),
    h("div", { class: "card scroll" }, h("h3", {}, "最近のセット"), h("table", {}, h("thead", {}, h("tr", {}, ["日時", "級・段", "向き", "範囲", "最初の正解", "状態"].map((x) => h("th", {}, x)))),
      h("tbody", {}, d.recent_rounds.map((r) => h("tr", {}, h("td", {}, fmtTime(r.created_at)), h("td", {}, r.level), h("td", {}, dirName(r.direction)), h("td", {}, `No.${r.start_seq}〜`), h("td", {}, `${r.ok1}/${r.n}`), h("td", {}, { answer: "回答待ち", fillin: "空欄補充中", review: "解説・テスト中", done: "完了", abandoned: "中止" }[r.stage]))))))];
}
async function tabHard() {
  const d = await api("/api/admin/hardest");
  return [h("div", { class: "card scroll" }, h("h3", {}, "正答率が低い問題（3回以上回答されたもの）"), d.questions.length ?
    h("table", {}, h("thead", {}, h("tr", {}, ["級・段", "No.", "向き", "問題", "回答数", "正答率"].map((x) => h("th", {}, x)))),
      h("tbody", {}, d.questions.map((q) => h("tr", {}, h("td", {}, q.level), h("td", {}, q.seq), h("td", {}, q.direction === "en2ja" ? "英日" : "日英"), h("td", {}, q.en_text, h("br"), h("span", { class: "muted" }, q.ja_text)), h("td", {}, q.n), h("td", {}, pct(q.accuracy)))))) : h("p", { class: "muted" }, "まだ集計できるデータがありません"))];
}
async function tabUsage() {
  const d = await api("/api/admin/usage");
  const max = Math.max(1, ...d.days.map((x) => x.attempts));
  return [h("div", { class: "card scroll" }, h("h3", {}, "直近30日の利用（日本時間）"), d.days.length ? h("table", {}, h("thead", {}, h("tr", {}, ["日付", "利用した生徒数", "回答数", ""].map((x) => h("th", {}, x)))),
    h("tbody", {}, d.days.map((x) => h("tr", {}, h("td", {}, x.day), h("td", {}, x.students), h("td", {}, x.attempts), h("td", {}, h("div", { class: "bar" }, (() => { const i = h("i"); i.style.width = (x.attempts * 100 / max) + "%"; return i; })())))))) : h("p", { class: "muted" }, "まだ利用がありません"))];
}
async function tabQuestions() {
  const c = await api("/api/admin/questions/counts");
  const res = h("div");
  const file = h("input", { type: "file", accept: ".csv,text/csv", id: "csvfile" });
  return [h("div", { class: "card" }, h("h3", {}, "問題をCSVで取り込む"),
    h("p", { class: "muted" }, "1行目の見出し: 級・段,番号,英文,和文,英単語,日本語の単語,文法メモ（文法メモは空でも可）。同じ級・番号は上書きされます。1行でも不備があると、1問も取り込まれません。"),
    h("label", { for: "csvfile" }, "CSVファイル"), file,
    h("p", {}, h("button", { class: "btn", onclick: async () => {
      if (!file.files[0]) { res.replaceChildren(banner("err", "ファイルを選んでください")); return; }
      const text = await file.files[0].text();
      try { const r = await api("/api/admin/questions/import", { method: "POST", body: { csv: text } }); res.replaceChildren(banner("ok", `${r.imported}問を取り込みました`)); setTimeout(adminScreen, 900); }
      catch (e) { const er = (e.data && e.data.errors) || []; res.replaceChildren(banner("err", `取り込めませんでした（${(e.data && e.data.error_count) || er.length}件のエラー）。1問も取り込んでいません。`), ...er.slice(0, 20).map((x) => h("div", { class: "muted" }, `行${x.row}: ${x.message}`))); }
    } }, "取り込む")), res),
    h("div", { class: "card" }, h("h3", {}, "級・段ごとの登録数（各40問が目安）"), h("div", { class: "cols" }, c.levels.map((l) => h("div", { class: l.n === 0 ? "zero" : "" }, `${l.name}：${l.n}`))))];
}

/* ---------------------------------------------------------------- 起動 */
async function boot(keepSel) {
  try {
    if (!S.user) S.user = await api("/api/me");
  } catch (e) { S.user = null; }
  if (!S.user) return loginScreen();
  if (S.user.role === "admin" && !S.adminOff && !S.view) return adminScreen();
  try {
    const l = await api("/api/levels");
    S.levels = l.levels; S.dirs = l.directions;
    const a = await api("/api/rounds/active");
    S.active = a.round_id;
    if (!keepSel || !S.sel.code) { const first = S.levels.find((x) => x.total > 0); pickLevel(first ? first.code : null); }
    else pickLevel(S.sel.code);
    homeScreen();
  } catch (e) { mount(topbar(), h("div", { class: "wrap" }, banner("err", e.message))); }
}
function render() { if (!S.user) return loginScreen(); if (S.view) return roundScreen(); return boot(true); }
boot();
