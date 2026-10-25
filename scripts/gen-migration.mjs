// src/schema.js から migrations/0001_init.sql を作る（wrangler d1 migrations apply で使える）
import { writeFileSync } from "node:fs";
import { SCHEMA, levelDefs } from "../src/schema.js";
const q = (s) => `'${String(s).replace(/'/g, "''")}'`;
let sql = "-- えいご道場 初期スキーマ（src/schema.js から自動生成）\n" + SCHEMA.map((s) => s.replace(/\s+/g, " ").trim() + ";").join("\n") + "\n";
sql += levelDefs().map((l) => `INSERT OR IGNORE INTO levels(code,name,kind,order_no) VALUES (${q(l[0])},${q(l[1])},${q(l[2])},${l[3]});`).join("\n") + "\n";
writeFileSync(new URL("../migrations/0001_init.sql", import.meta.url), sql);
console.log("migrations/0001_init.sql を作成しました");
