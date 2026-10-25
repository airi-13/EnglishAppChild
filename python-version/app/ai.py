"""AI（Gemini）との通信と、出題フローで使う4つのAIタスク。
・APIキー、モデル名、1分あたりの呼び出し上限は環境変数で変更できます。
・AIが使えないとき（混雑・通信障害・キー不備）は、各タスクが「安全な代替結果」を返し、学習は止まりません。
・生徒のID・氏名はAIに送りません（問題文と回答だけを送ります）。"""
import collections
import difflib
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.request

from . import prompts
from .textnorm import norm_en, norm_ja

VERDICTS = ("correct", "close", "wrong")


class AIError(Exception):
    pass


def parse_json_text(text):
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t).strip()
    try:
        return json.loads(t)
    except ValueError:
        m = re.search(r"\{.*\}", t, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


class RateLimiter:
    def __init__(self, rpm):
        self.rpm = rpm
        self.ts = collections.deque()
        self.lock = threading.Lock()

    def acquire(self, max_wait=20.0):
        if self.rpm <= 0:
            return
        deadline = time.monotonic() + max_wait
        while True:
            with self.lock:
                now = time.monotonic()
                while self.ts and now - self.ts[0] >= 60:
                    self.ts.popleft()
                if len(self.ts) < self.rpm:
                    self.ts.append(now)
                    return
                wait = 60 - (now - self.ts[0])
            if time.monotonic() + wait > deadline:
                raise AIError("rate limit: wait too long")
            time.sleep(min(wait, 1.0))


class GeminiClient:
    def __init__(self, api_key, model, base_url, timeout=60, max_retries=2, rpm=10, logger=None, thinking_budget=None):
        self.api_key, self.model, self.base_url = api_key, model, base_url.rstrip("/")
        self.thinking_budget = thinking_budget
        self.timeout, self.max_retries = timeout, max_retries
        self.limiter = RateLimiter(rpm)
        self.logger = logger or (lambda *a, **k: None)

    def generate_json(self, system, user, purpose, data=None):
        if not self.api_key:
            self.logger(purpose, self.model, False, 0, "no api key")
            raise AIError("GEMINI_API_KEY is not set")
        url = f"{self.base_url}/models/{self.model}:generateContent"
        gen_cfg = {"temperature": 0.3, "responseMimeType": "application/json"}
        if self.thinking_budget is not None:
            gen_cfg["thinkingConfig"] = {"thinkingBudget": self.thinking_budget}
        body = json.dumps({
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": gen_cfg,
        }).encode("utf-8")
        headers = {"Content-Type": "application/json", "x-goog-api-key": self.api_key}
        last = AIError("unknown")
        for attempt in range(self.max_retries + 1):
            t0 = time.monotonic()
            delay = 0.0
            try:
                self.limiter.acquire()
                t0 = time.monotonic()
                req = urllib.request.Request(url, data=body, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    payload = json.loads(r.read().decode("utf-8"))
                cands = payload.get("candidates") or []
                if not cands:
                    raise ValueError("no candidates")
                parts = cands[0].get("content", {}).get("parts") or []
                text = "".join(p.get("text", "") for p in parts)
                obj = parse_json_text(text)
                if not isinstance(obj, dict):
                    raise ValueError("not an object")
                self.logger(purpose, self.model, True, int((time.monotonic() - t0) * 1000), None)
                return obj
            except urllib.error.HTTPError as e:
                ms = int((time.monotonic() - t0) * 1000)
                self.logger(purpose, self.model, False, ms, f"HTTP {e.code}")
                last = AIError(f"HTTP {e.code}")
                if e.code not in (429, 500, 502, 503, 504):
                    break
                ra = e.headers.get("Retry-After") if e.headers else None
                delay = float(ra) if ra and ra.replace(".", "", 1).isdigit() else 1.5 * (2 ** attempt)
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
                kind = "ssl certificate" if "CERTIFICATE_VERIFY_FAILED" in str(e) else f"network: {type(e).__name__}"
                self.logger(purpose, self.model, False, int((time.monotonic() - t0) * 1000), kind)
                last = AIError("network error")
                delay = 1.0 * (2 ** attempt)
            except AIError as e:  # レート制限の待ち時間超過
                self.logger(purpose, self.model, False, 0, str(e))
                last = e
                break
            except (ValueError, KeyError, TypeError, IndexError) as e:
                self.logger(purpose, self.model, False, int((time.monotonic() - t0) * 1000), f"bad response: {type(e).__name__}")
                last = AIError("invalid response")
                delay = 0.3
            if attempt < self.max_retries:
                time.sleep(min(delay, 8.0))
        raise last


class MockClient:
    """APIキー不要の動作確認用。判定は文字列の類似度で行う（テストしやすい決まった結果を返す）。
    回答に「#ok」を含めると、言い換え正解の例として correct になる。"""

    def __init__(self, latency=0.0, logger=None):
        self.latency = latency
        self.fail = 0  # テスト用: 次のN回の呼び出しを失敗させる
        self.calls = []
        self.logger = logger or (lambda *a, **k: None)
        self.model = "mock"
        self._lock = threading.Lock()

    @staticmethod
    def _verdict(answer, expected):
        if "#ok" in answer:
            return "correct"
        a, b = (norm_ja(answer), norm_ja(expected)) if re.search(r"[぀-ヿ一-鿿]", expected) else (norm_en(answer), norm_en(expected))
        if a == b and a:
            return "correct"
        return "close" if difflib.SequenceMatcher(None, a, b).ratio() >= 0.85 else "wrong"

    def generate_json(self, system, user, purpose, data=None):
        with self._lock:
            self.calls.append(purpose)
            if self.fail > 0:
                self.fail -= 1
                self.logger(purpose, "mock", False, 0, "injected failure")
                raise AIError("injected failure")
        time.sleep(self.latency)
        items = (data or {}).get("items", [])
        if purpose == "judge_sentences":
            out = {"results": [{"i": it["i"], "verdict": self._verdict(it["answer"], it["expected"]), "note": "mock"} for it in items]}
        elif purpose == "grade_fillin":
            res = []
            for it in items:
                v = "correct" if it["exact"] else self._verdict(it["answer"], it["expected"])
                res.append({"i": it["i"], "verdict": v, "supplement": f"【補足】{it['expected']} はこの文の大事な単語だよ。" if v == "correct" else ""})
            out = {"results": res, "points": ["単語のスペルと形を確認しよう"]}
        elif purpose == "review":
            out = {"items": [{"i": it["i"], "explanation": f"【解説】「{it['expected_word']}」に注目しよう。あなたの答えは「{it['s2_answer']}」だったね。",
                              "test_prompt": it["source_sentence"], "test_answer": it["expected_sentence"]} for it in items]}
        elif purpose == "finish":
            res = []
            for it in items:
                v = "correct" if it["exact"] else self._verdict(it["answer"], it["test_answer"])
                res.append({"i": it["i"], "verdict": v, "feedback": f"正解例: {it['test_answer']}"})
            out = {"results": res, "points": ["間違えた単語をもう一度書いて覚えよう"]}
        else:
            raise AIError("unknown purpose")
        self.logger(purpose, "mock", True, int(self.latency * 1000), None)
        return out


# ---------------------------------------------------------------- タスク

def _norm_verdict(v):
    return v if v in VERDICTS else None


def _by_i(lst):
    out = {}
    for r in lst or []:
        if isinstance(r, dict) and "i" in r:
            out[r["i"]] = r
    return out


def judge_sentences(client, items):
    """文の採点（完全一致でなかった回答だけ）。戻り値: (結果リスト, 代替採点になったか)"""
    try:
        obj = client.generate_json(prompts.SYSTEM, prompts.judge_sentences(items), "judge_sentences", {"items": items})
        got = _by_i(obj.get("results"))
        out, degraded = [], False
        for it in items:
            r = got.get(it["i"])
            v = _norm_verdict(r.get("verdict")) if r else None
            if v:
                out.append({"verdict": v, "note": str(r.get("note", ""))[:200], "by": "ai"})
            else:
                out.append({"verdict": "wrong", "note": "AI判定なし", "by": "fallback"})
                degraded = True
        return out, degraded
    except AIError:
        return [{"verdict": "wrong", "note": "AI利用不可", "by": "fallback"} for _ in items], True


def grade_fillin(client, items):
    """空欄補充の採点＋補足説明。戻り値: (結果リスト, points, 代替になったか)"""
    try:
        obj = client.generate_json(prompts.SYSTEM, prompts.grade_fillin(items), "grade_fillin", {"items": items})
        got = _by_i(obj.get("results"))
        points = [str(p)[:80] for p in (obj.get("points") or []) if isinstance(p, (str, int, float))][:3]
        out, degraded = [], False
        for it in items:
            r = got.get(it["i"])
            v = _norm_verdict(r.get("verdict")) if r else None
            if it["exact"]:
                v = "correct"
            if v is None:
                out.append({"verdict": "wrong", "supplement": "", "by": "fallback"})
                degraded = True
                continue
            supp = str(r.get("supplement", "")).strip() if r else ""
            if v == "correct" and not supp:
                supp = it["supp_fallback"]
            out.append({"verdict": v, "supplement": supp if v == "correct" else "", "by": "exact" if it["exact"] else "ai"})
        return out, points, degraded
    except AIError:
        out = [{"verdict": "correct" if it["exact"] else "wrong", "supplement": it["supp_fallback"] if it["exact"] else "",
                "by": "exact" if it["exact"] else "fallback"} for it in items]
        return out, [], True


def review(client, items):
    """解説＋確認テストの作成。AIが使えないときは、問題をもう一度出す形に切り替える。"""
    try:
        obj = client.generate_json(prompts.SYSTEM, prompts.review(items), "review", {"items": items})
        got = _by_i(obj.get("items"))
        out, degraded = [], False
        for it in items:
            r = got.get(it["i"]) or {}
            ex, tp, ta = (str(r.get(k, "")).strip() for k in ("explanation", "test_prompt", "test_answer"))
            if ex and tp and ta:
                out.append({"explanation": ex, "test_prompt": tp, "test_answer": ta, "by": "ai"})
            else:
                out.append({"explanation": it["fallback_explanation"], "test_prompt": it["source_sentence"], "test_answer": it["expected_sentence"], "by": "fallback"})
                degraded = True
        return out, degraded
    except AIError:
        return [{"explanation": it["fallback_explanation"], "test_prompt": it["source_sentence"], "test_answer": it["expected_sentence"], "by": "fallback"} for it in items], True


def finish(client, items):
    """確認テストの採点＋まとめ。戻り値: (結果リスト, points, 代替になったか)"""
    try:
        obj = client.generate_json(prompts.SYSTEM, prompts.finish(items), "finish", {"items": items})
        got = _by_i(obj.get("results"))
        points = [str(p)[:80] for p in (obj.get("points") or []) if isinstance(p, (str, int, float))][:4]
        out, degraded = [], False
        for it in items:
            r = got.get(it["i"])
            v = _norm_verdict(r.get("verdict")) if r else None
            if it["exact"]:
                v = "correct"
            if v is None:
                out.append({"verdict": "wrong", "feedback": f"正解例: {it['test_answer']}", "by": "fallback"})
                degraded = True
            else:
                fb = str(r.get("feedback", "")).strip() if r else ""
                out.append({"verdict": v, "feedback": fb or f"正解例: {it['test_answer']}", "by": "exact" if it["exact"] else "ai"})
        return out, points, degraded
    except AIError:
        out = [{"verdict": "correct" if it["exact"] else "wrong", "feedback": f"正解例: {it['test_answer']}", "by": "exact" if it["exact"] else "fallback"} for it in items]
        return out, [], True


def make_client(cfg, logger):
    if cfg["AI_MODE"] == "mock":
        return MockClient(cfg.get("MOCK_LATENCY", 0), logger)
    return GeminiClient(cfg["GEMINI_API_KEY"], cfg["GEMINI_MODEL"], cfg["GEMINI_BASE_URL"],
                        cfg["AI_TIMEOUT"], cfg["AI_MAX_RETRIES"], cfg["AI_MAX_RPM"], logger,
                        int(cfg["GEMINI_THINKING_BUDGET"]) if str(cfg.get("GEMINI_THINKING_BUDGET", "")).lstrip("-").isdigit() else None)
