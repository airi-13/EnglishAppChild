import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def env_path():
    return os.environ.get("EIGO_ENV_FILE") or os.path.join(ROOT, ".env")


def load_dotenv(path=None):
    """プロジェクト直下の .env を読み込む（外部ライブラリ不要）。すでに設定済みの環境変数は上書きしない。"""
    path = path or env_path()
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = re.sub(r"(^|\s+)#.*$", "", v.strip()).strip().strip("\"'")  # 行末コメントと、値が空でコメントだけの行に対応
            os.environ.setdefault(k.strip(), v)


def update_env(updates, path=None):
    """.env の指定キーだけを書き換える（他の行は残す）。ファイルが無ければ作る。"""
    path = path or env_path()
    lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
    out, done = [], set()
    for ln in lines:
        m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", ln)
        if m and m.group(1) in updates:
            if m.group(1) not in done:
                out.append(f"{m.group(1)}={updates[m.group(1)]}")
                done.add(m.group(1))
        else:
            out.append(ln)
    out += [f"{k}={v}" for k, v in updates.items() if k not in done]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    try:
        os.chmod(path, 0o600)  # 自分だけが読めるようにする
    except OSError:
        pass
    for k, v in updates.items():
        os.environ[k] = str(v)


def load(overrides=None):
    load_dotenv()
    e = os.environ.get
    cfg = {
        "DB_PATH": e("DB_PATH", "data/app.db"),
        "AI_MODE": e("AI_MODE", "gemini"),
        "GEMINI_API_KEY": e("GEMINI_API_KEY", ""),
        "GEMINI_MODEL": e("GEMINI_MODEL", "gemini-flash-latest"),
        "GEMINI_BASE_URL": e("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"),
        "AI_TIMEOUT": float(e("AI_TIMEOUT", "60")),
        "GEMINI_THINKING_BUDGET": e("GEMINI_THINKING_BUDGET", "").strip(),
        "AI_MAX_RETRIES": int(e("AI_MAX_RETRIES", "2")),
        "AI_MAX_RPM": int(e("AI_MAX_RPM", "10")),
        "COOKIE_SECURE": e("COOKIE_SECURE", "0") == "1",
        "SESSION_DAYS": int(e("SESSION_DAYS", "30")),
        "TZ_OFFSET_HOURS": int(e("TZ_OFFSET_HOURS", "9")),
        "MOCK_LATENCY": float(e("MOCK_LATENCY", "0")),
    }
    if overrides:
        cfg.update(overrides)
    return cfg
