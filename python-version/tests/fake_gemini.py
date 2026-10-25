"""Gemini APIの形をまねたテスト用サーバー（本物のGoogleには接続しない）。
script に動作を積むと順番に実行し、空になったら auto（プロンプトから用途を判断してモック応答）で返す。"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app import ai


def purpose_of(prompt):
    for head, p in (("次の各問題について", "judge_sentences"), ("空欄補充の問題です", "grade_fillin"), ("生徒が空欄補充でも間違えた", "review"), ("確認テストの採点", "finish")):
        if prompt.startswith(head):
            return p
    return None


class FakeGemini:
    def __init__(self, fenced=False):
        self.script, self.requests, self.fenced = [], [], fenced
        self.get_script = []  # GET(/models) 用。("status", code, body_dict) など
        self.models = [{"name": "models/gemini-flash-latest", "supportedGenerationMethods": ["generateContent"]},
                       {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
                       {"name": "models/gemini-9.5-flash", "supportedGenerationMethods": ["generateContent"]},
                       {"name": "models/text-embedding-9", "supportedGenerationMethods": ["embedContent"]}]
        self.mock = ai.MockClient()
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": None})
                act = outer.get_script.pop(0) if outer.get_script else ("ok",)
                if act[0] == "status":
                    payload = json.dumps({"error": act[2]}).encode()
                    self.send_response(act[1])
                else:
                    payload = json.dumps({"models": outer.models}).encode()
                    self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n))
                outer.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                act = outer.script.pop(0) if outer.script else ("auto",)
                kind = act[0]
                if kind == "status":
                    self.send_response(act[1])
                    for k, v in (act[2] if len(act) > 2 else {}).items():
                        self.send_header(k, v)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if kind == "sleep":
                    time.sleep(act[1])
                if kind == "text":
                    text = act[1]
                else:
                    prompt = body["contents"][0]["parts"][0]["text"]
                    purpose = purpose_of(prompt)
                    data = json.loads(prompt.split("【データ】\n", 1)[1])
                    text = json.dumps(outer.mock.generate_json("", prompt, purpose, data), ensure_ascii=False)
                    if outer.fenced:
                        text = "```json\n" + text + "\n```"
                payload = json.dumps({"candidates": [{"content": {"parts": [{"text": text}]}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # クライアントがタイムアウトで切断した場合

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1beta"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()
