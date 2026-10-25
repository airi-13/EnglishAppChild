#!/usr/bin/env python3
"""Geminiへの接続チェック（キーは画面に表示しません）。
  python tools/check_gemini.py              診断だけ行う
  python tools/check_gemini.py --write-env  使えるモデルが見つかったら .env の GEMINI_MODEL / AI_MODE を自動で直す
診断内容: ①キーの形 ②キーが有効か＋使えるモデル一覧 ③モデル名の確認 ④アプリと同じ方法で採点・解説を1回ずつ実行"""
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import ai, config  # noqa: E402

SAMPLE_JUDGE = [{"i": 1, "direction": "ja2en", "question": "彼女は毎日テニスをします。", "expected": "She plays tennis every day.", "answer": "She play tennis every day."}]
SAMPLE_REVIEW = [{"i": 1, "direction": "ja2en", "source_sentence": "彼女は毎日テニスをします。", "expected_sentence": "She plays tennis every day.", "expected_word": "plays",
                  "grammar_note": "主語が1人（she / he / 名前）のとき、動詞の最後に s / es をつける", "s1_answer": "She play tennis every day.", "s2_answer": "play",
                  "fallback_explanation": "(代替)"}]


def mask(text, key):
    return text.replace(key, "***") if key else text


def advice(code, status, reason, msg):
    t = f"{status} {reason} {msg}".lower()
    if "access_token_type_unsupported" in t:
        return ("新方式（AQ.）のキーで報告されている問題です。Google AI Studio で**別のプロジェクトを選んで新しいキーを作り直し**、もう一度試してください。"
                "Cloud Console で「Gemini API」が有効か、キーのAPI制限が「Gemini API」になっているかも確認してください。")
    if "api key not valid" in t or "api_key_invalid" in t or code == 400:
        return "キーが無効です。貼り間違い（前後の空白・引用符）、削除済みのキー、制限の設定ミスが考えられます。AI Studio でキーを確認し、必要なら作り直してください。"
    if "service_disabled" in t or "has not been used" in t or "is disabled" in t:
        return "このプロジェクトで Gemini API（Generative Language API）が有効になっていません。Cloud Console の「APIとサービス」→「ライブラリ」で有効にしてください。"
    if code == 403:
        return "権限エラーです。キーのAPI制限・アプリケーション制限（「なし」にする）、プロジェクトの課金・地域の制限を確認してください。"
    if code == 401:
        return "認証エラーです。キーが正しいか、AI Studio で作ったキーかを確認してください。"
    if code == 404:
        return "モデル名または接続先が見つかりません。下の「使えるモデル」から選んで .env の GEMINI_MODEL を直してください。"
    if code == 429:
        return "回数制限に達しています。少し待つか、AI_MAX_RPM を下げる、または有料枠にしてください。"
    return "Google側の一時的なエラーかもしれません。少し待ってからやり直してください。"


def http_json(url, key, timeout=20):
    req = urllib.request.Request(url, headers={"x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def pick_model(models, current):
    names = [m["name"].split("/", 1)[-1] for m in models if "generateContent" in (m.get("supportedGenerationMethods") or [])]
    if current in names:
        return current, True, names
    stable = [n for n in names if re.fullmatch(r"gemini-\d+(?:\.\d+)?-flash", n)]
    if stable:
        return max(stable, key=lambda n: float(re.search(r"gemini-(\d+(?:\.\d+)?)-flash", n).group(1))), False, names
    flash = sorted(n for n in names if "flash" in n and not any(x in n for x in ("image", "tts", "live", "audio", "embedding", "thinking")))
    return (flash[-1] if flash else (names[0] if names else None)), False, names


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    write = "--write-env" in argv
    config.load_dotenv()
    cfg = config.load()
    key, base, model = cfg["GEMINI_API_KEY"], cfg["GEMINI_BASE_URL"].rstrip("/"), cfg["GEMINI_MODEL"]
    results = []

    def report(ok, msg):
        results.append(ok)
        print(("  [OK] " if ok else "  [NG] ") + msg)

    print("Gemini 接続チェック")
    # ① キーの形
    if not key:
        report(False, "GEMINI_API_KEY が設定されていません（.env に GEMINI_API_KEY=キー を書く、または python setup.py を実行）")
        return 1
    if re.search(r"\s|[\"'`]", key):
        report(False, "キーに空白や引用符が含まれています。.env の GEMINI_API_KEY=… の右側を、キーだけにしてください")
        return 1
    kind = "新方式（AQ.）" if key.startswith("AQ.") else "従来方式（AIza）" if key.startswith("AIza") else "不明な形式"
    report(True, f"キーの形: {kind}、{len(key)}文字（キーそのものは表示しません）")

    # ② キーの有効性とモデル一覧
    try:
        data = http_json(f"{base}/models?pageSize=1000", key)
        models = data.get("models") or []
        report(True, f"キーは有効です（モデル {len(models)} 件を取得）")
    except urllib.error.HTTPError as e:
        body = {}
        try:
            body = json.loads(e.read().decode("utf-8")).get("error", {})
        except Exception:
            pass
        reason = ", ".join(d.get("reason", "") for d in body.get("details", []) if isinstance(d, dict))
        report(False, f"キーを確認できません（HTTP {e.code} {body.get('status', '')} {reason}）")
        print("      → " + mask(advice(e.code, body.get("status", ""), reason, body.get("message", "")), key))
        return 1
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(e):
            report(False, "SSL証明書の確認に失敗しました（Pythonの証明書の設定が未完了の可能性が高いです）")
            print("      → Mac: Finder で「アプリケーション」>「Python 3.x」フォルダを開き、「Install Certificates.command」をダブルクリックして実行し、もう一度試してください")
            print("      → それでも直らなければ: pip install --upgrade certifi を実行してから、もう一度試してください")
        else:
            report(False, "Googleに接続できません。インターネット接続、プロキシ・フィルターを確認してください")
        return 1

    # ③ モデル名
    best, exact, names = pick_model(models, model)
    if best is None:
        report(False, "generateContent に使えるモデルが見つかりません（キーの制限を確認してください）")
        return 1
    if exact:
        report(True, f"モデル {model} は使えます")
    elif write:
        config.update_env({"GEMINI_MODEL": best})
        report(True, f"モデル {model} は一覧にないため、{best} に自動で切り替えました（.env を書き換え済み）")
        print("      使えるモデルの例: " + ", ".join(names[:12]))
        cfg["GEMINI_MODEL"] = model = best
    else:
        report(False, f"モデル {model} は一覧にありません。おすすめ: {best}")
        print("      使えるモデルの例: " + ", ".join(names[:12]))
        print("      → --write-env を付けて実行すると、.env を自動で直します")
        model = best

    # ④ アプリと同じ方法で採点・解説
    logs = []
    tb = cfg.get("GEMINI_THINKING_BUDGET", "")
    client = ai.GeminiClient(key, model, base, timeout=cfg["AI_TIMEOUT"], max_retries=1, rpm=cfg["AI_MAX_RPM"], logger=lambda *a: logs.append(a),
                             thinking_budget=int(tb) if tb.lstrip("-").isdigit() else None)
    print(f"  … 採点テストを実行中（最大{int(cfg['AI_TIMEOUT'])}秒×2回まで待ちます。止まって見えても、そのままお待ちください）", flush=True)
    t0 = time.time()
    res, degraded = ai.judge_sentences(client, SAMPLE_JUDGE)
    dt = time.time() - t0
    if degraded:
        err = next((l[4] for l in reversed(logs) if not l[2]), "不明")
        report(False, f"採点テストに失敗しました（{mask(str(err), key)} / {dt:.1f}秒）")
        print("      → " + mask(advice(0, "", str(err), ""), key) + " エラー内容を確認してください")
    else:
        v = res[0]["verdict"]
        report(True, f"採点テスト: 「She play tennis every day.」→ 判定 {v}（close か wrong なら正常。三単現のsが抜けています）／ {dt:.1f}秒")
        if dt > 15:
            print("      → 応答が遅めです（生徒を待たせます）。.env に GEMINI_THINKING_BUDGET=0 を追加して、もう一度試すと速くなることがあります")
    print("  … 解説・確認テストの作成を実行中", flush=True)
    t0 = time.time()
    rv, deg2 = ai.review(client, SAMPLE_REVIEW)
    dt2 = time.time() - t0
    if deg2:
        report(False, "解説・確認テストの作成に失敗しました（応答がJSONで返らない等）")
    else:
        report(True, f"解説・確認テストの作成: 成功 / {dt2:.1f}秒")
        print("\n  --- 解説のサンプル（わかりやすさを確認してください）---")
        print("  " + rv[0]["explanation"].replace("\n", "\n  ")[:500])
        print(f"\n  確認テスト: {rv[0]['test_prompt']}  （正解例: {rv[0]['test_answer']}）")

    ok = all(results)
    if ok and write:
        config.update_env({"AI_MODE": "gemini"})
    print("\n結果:", "接続OK。python run.py で起動できます。" if ok else "NGがあります。上の「→」の案内を確認してください。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
