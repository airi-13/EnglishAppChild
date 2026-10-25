// 表記ゆれを吸収して、完全一致かどうかを判定する（Python版と同じ規則）
const JA_DROP = /[\s。、．，.,!?！？「」『』"'“”‘’・…~〜]+/g;

export const normJa = (s) => String(s ?? "").normalize("NFKC").replace(JA_DROP, "");

export function normEn(s) {
  let t = String(s ?? "").normalize("NFKC").replace(/[’‘]/g, "'").replace(/[“”]/g, '"').toLowerCase();
  t = t.replace(/\s+/g, " ").trim().replace(/\s+([.,!?;:])/g, "$1");
  return t.replace(/[.!? ]+$/, "");
}

export function same(lang, a, b) {
  if (lang === "ja") {
    const x = normJa(a);
    return x !== "" && x === normJa(b);
  }
  const x = normEn(a);
  return x !== "" && x === normEn(b);
}
