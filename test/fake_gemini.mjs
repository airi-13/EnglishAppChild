// Gemini API の形をまねたテスト用サーバー（本物のGoogleには接続しない）
import http from "node:http";
import { MockClient } from "../src/ai.js";

const head = [["次の各問題について", "judge_sentences"], ["空欄補充の問題です", "grade_fillin"], ["生徒が空欄補充でも間違えた", "review"], ["確認テストの採点", "finish"]];

export class FakeGemini {
  constructor({ fenced = false } = {}) {
    this.script = []; this.getScript = []; this.requests = []; this.fenced = fenced;
    this.mock = new MockClient();
    this.models = [
      { name: "models/gemini-flash-latest", supportedGenerationMethods: ["generateContent"] },
      { name: "models/gemini-2.5-flash", supportedGenerationMethods: ["generateContent"] },
      { name: "models/gemini-9.5-flash", supportedGenerationMethods: ["generateContent"] },
      { name: "models/text-embedding-9", supportedGenerationMethods: ["embedContent"] },
    ];
    this.server = http.createServer((req, res) => {
      let raw = "";
      req.on("data", (c) => (raw += c));
      req.on("end", async () => {
        const hdr = Object.fromEntries(Object.entries(req.headers));
        const body = raw ? JSON.parse(raw) : null;
        this.requests.push({ method: req.method, path: req.url, headers: hdr, body });
        const send = (code, obj, headers = {}) => { res.writeHead(code, { "Content-Type": "application/json", ...headers }); res.end(obj === null ? "" : JSON.stringify(obj)); };
        if (req.method === "GET") {
          const a = this.getScript.shift();
          return a ? send(a[1], { error: a[2] }) : send(200, { models: this.models });
        }
        const act = this.script.shift() || ["auto"];
        if (act[0] === "status") return send(act[1], null, act[2] || {});
        if (act[0] === "sleep") await new Promise((r) => setTimeout(r, act[1]));
        let text;
        if (act[0] === "text") text = act[1];
        else {
          const prompt = body.contents[0].parts[0].text;
          const purpose = head.find(([h]) => prompt.startsWith(h))?.[1];
          const data = JSON.parse(prompt.split("【データ】\n")[1]);
          text = JSON.stringify(await this.mock.generateJson("", prompt, purpose, data));
          if (this.fenced) text = "```json\n" + text + "\n```";
        }
        send(200, { candidates: [{ content: { parts: [{ text }] } }] });
      });
    });
  }
  async start() { await new Promise((r) => this.server.listen(0, "127.0.0.1", r)); this.url = `http://127.0.0.1:${this.server.address().port}/v1beta`; return this; }
  async stop() { this.server.closeAllConnections?.(); await new Promise((r) => this.server.close(r)); }
}
