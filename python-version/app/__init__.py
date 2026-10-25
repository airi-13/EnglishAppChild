import hashlib
import hmac
import logging
import os
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from flask import Flask, Response, g, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from . import ai, config, db, engine, questions_io, stats
from .engine import ApiError

PW_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
LOGIN_RE = __import__("re").compile(r"^[A-Za-z0-9_.-]{3,32}$")


PBKDF2_ITERS = 600_000  # OWASPの推奨値（PBKDF2-HMAC-SHA256）。どのPython/OSでも標準で使える方式


def hash_password(pw):
    salt = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, PBKDF2_ITERS, dklen=32)
    return f"pbkdf2${PBKDF2_ITERS}${salt.hex()}${h.hex()}"


def verify_password(pw, stored):
    try:
        parts = stored.split("$")
        if parts[0] == "pbkdf2":
            _, it, salt, h = parts
            if not (1 <= int(it) <= 5_000_000):
                return False
            calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it), dklen=32)
        elif parts[0] == "scrypt" and hasattr(hashlib, "scrypt"):  # 旧版で作った保存形式（互換用）
            _, salt, h = parts
            calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
        else:
            return False
        return hmac.compare_digest(calc.hex(), h)
    except Exception:
        return False


DUMMY_HASH = hash_password("dummy-password-for-timing")


def gen_password(n=8):
    return "".join(secrets.choice(PW_ALPHABET) for _ in range(n))


def create_app(overrides=None):
    cfg = config.load(overrides)
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config["CFG"] = cfg
    app.config["MAX_CONTENT_LENGTH"] = 3 * 1024 * 1024
    log = logging.getLogger("eigo")

    conn0 = db.connect(cfg["DB_PATH"])
    db.init_db(conn0)
    conn0.close()

    def ai_logger(purpose, model, ok, ms, error):
        try:
            c = db.connect(cfg["DB_PATH"])
            try:
                c.execute("INSERT INTO ai_logs(purpose,model,ok,latency_ms,error) VALUES (?,?,?,?,?)", (purpose, model, 1 if ok else 0, ms, error))
            finally:
                c.close()
        except Exception:  # ログ失敗で学習を止めない
            log.exception("ai_logs write failed")

    app.ai_client = ai.make_client(cfg, ai_logger)
    if cfg["AI_MODE"] == "gemini" and not cfg["GEMINI_API_KEY"]:
        log.warning("GEMINI_API_KEY が未設定です。AI採点は「代替採点」になります。")

    # ---------------------------------------------------------- 共通
    def get_db():
        if "db" not in g:
            g.db = db.connect(cfg["DB_PATH"])
        return g.db

    @app.teardown_appcontext
    def close_db(_):
        c = g.pop("db", None)
        if c is not None:
            c.close()

    def current_user():
        if "user" in g:
            return g.user
        g.user = None
        tok = request.cookies.get("sid")
        if tok:
            h = hashlib.sha256(tok.encode()).hexdigest()
            row = get_db().execute(
                """SELECT u.id, u.login_id, u.label, u.role FROM sessions s JOIN users u ON u.id=s.user_id
                   WHERE s.token_hash=? AND s.expires_at>datetime('now') AND u.is_active=1""", (h,)).fetchone()
            g.user = dict(row) if row else None
        return g.user

    def need_user():
        u = current_user()
        if not u:
            raise ApiError(401, "ログインしてください")
        return u

    def need_admin():
        u = need_user()
        if u["role"] != "admin":
            raise ApiError(403, "この操作は管理者だけができます")
        return u

    @app.before_request
    def guard():
        if request.path.startswith("/api/") and request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("Origin")
            if origin and urlparse(origin).netloc != request.host:
                raise ApiError(403, "不正なリクエストです")
            if not request.is_json:
                raise ApiError(415, "JSON形式で送信してください")

    @app.after_request
    def headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
                                                   "font-src https://fonts.gstatic.com; img-src 'self' data:; frame-ancestors 'none'")
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.errorhandler(ApiError)
    def on_api_error(e):
        return jsonify({"error": e.message, **e.extra}), e.status

    @app.errorhandler(Exception)
    def on_error(e):
        if isinstance(e, HTTPException):
            if request.path.startswith("/api/"):
                return jsonify({"error": e.description}), e.code
            return e
        log.exception("unhandled error")
        return jsonify({"error": "サーバーでエラーが起きました。少し待ってから、もう一度ためしてください。"}), 500

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/healthz")
    def healthz():
        get_db().execute("SELECT 1").fetchone()
        return jsonify({"ok": True})

    # ---------------------------------------------------------- 認証
    @app.post("/api/login")
    def login():
        body = request.get_json(silent=True) or {}
        lid, pw = str(body.get("login_id", "")).strip(), str(body.get("password", ""))
        conn, ip, now = get_db(), request.remote_addr or "", int(time.time())
        f_id = conn.execute("SELECT COUNT(*) FROM login_attempts WHERE login_id=? AND ok=0 AND ts>?", (lid.lower(), now - 600)).fetchone()[0]
        f_ip = conn.execute("SELECT COUNT(*) FROM login_attempts WHERE ip=? AND ok=0 AND ts>?", (ip, now - 600)).fetchone()[0]
        if f_id >= 5 or f_ip >= 30:
            raise ApiError(429, "間違いが続いたため、10分ほど待ってからもう一度ためしてください")
        row = conn.execute("SELECT * FROM users WHERE login_id=?", (lid,)).fetchone()
        ok = verify_password(pw, row["password_hash"] if row else DUMMY_HASH) and bool(row) and bool(row["is_active"])
        with db.tx(conn):
            conn.execute("INSERT INTO login_attempts(login_id,ip,ts,ok) VALUES (?,?,?,?)", (lid.lower(), ip, now, 1 if ok else 0))
            if ok:
                conn.execute("DELETE FROM login_attempts WHERE login_id=? OR ts<?", (lid.lower(), now - 86400))
        if not ok:
            raise ApiError(401, "IDまたはパスワードが違います")
        tok = secrets.token_urlsafe(32)
        exp = (datetime.now(timezone.utc) + timedelta(days=cfg["SESSION_DAYS"])).strftime("%Y-%m-%d %H:%M:%S")
        with db.tx(conn):
            conn.execute("DELETE FROM sessions WHERE expires_at<datetime('now')")
            conn.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES (?,?,?)", (hashlib.sha256(tok.encode()).hexdigest(), row["id"], exp))
            conn.execute("UPDATE users SET last_login_at=datetime('now') WHERE id=?", (row["id"],))
        resp = jsonify({"id": row["id"], "login_id": row["login_id"], "label": row["label"], "role": row["role"]})
        resp.set_cookie("sid", tok, httponly=True, samesite="Lax", secure=cfg["COOKIE_SECURE"], max_age=cfg["SESSION_DAYS"] * 86400, path="/")
        return resp

    @app.post("/api/logout")
    def logout():
        tok = request.cookies.get("sid")
        if tok:
            with db.tx(get_db()):
                get_db().execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(tok.encode()).hexdigest(),))
        resp = jsonify({"ok": True})
        resp.delete_cookie("sid", path="/")
        return resp

    @app.get("/api/me")
    def me():
        return jsonify(need_user())

    # ---------------------------------------------------------- 生徒
    @app.get("/api/levels")
    def levels():
        u = need_user()
        return jsonify({"levels": engine.levels_with_progress(get_db(), u["id"]), "directions": engine.DIRECTIONS})

    @app.get("/api/rounds/active")
    def active():
        u = need_user()
        rid = engine.active_round_id(get_db(), u["id"])
        return jsonify({"round_id": rid})

    @app.post("/api/rounds")
    def new_round():
        u = need_user()
        b = request.get_json(silent=True) or {}
        rid, resumed = engine.start_round(get_db(), u["id"], str(b.get("level_code", "")), str(b.get("direction", "")), b.get("start_seq", 1))
        return jsonify({"round_id": rid, "resumed": resumed})

    @app.get("/api/rounds/<int:rid>")
    def round_view(rid):
        u = need_user()
        return jsonify(engine.build_view(get_db(), rid, u["id"]))

    @app.post("/api/rounds/<int:rid>/answers")
    def round_submit(rid):
        u = need_user()
        b = request.get_json(silent=True) or {}
        warnings = engine.submit(get_db(), app.ai_client, rid, u["id"], b.get("answers"))
        return jsonify({"view": engine.build_view(get_db(), rid, u["id"]), "warnings": warnings})

    @app.post("/api/rounds/<int:rid>/abandon")
    def round_abandon(rid):
        u = need_user()
        engine.abandon_round(get_db(), rid, u["id"])
        return jsonify({"ok": True})

    @app.get("/api/my/stats")
    def my_stats():
        u = need_user()
        r = get_db().execute(
            """SELECT COUNT(*) n, COALESCE(SUM(is_correct),0) ok, COUNT(DISTINCT date(created_at,?)) days FROM attempts
               WHERE user_id=? AND stage=1""", (stats._off(cfg), u["id"])).fetchone()
        done = get_db().execute("SELECT COUNT(*) FROM rounds WHERE user_id=? AND stage='done'", (u["id"],)).fetchone()[0]
        return jsonify({"answered": r["n"], "accuracy": round(r["ok"] * 100 / r["n"], 1) if r["n"] else None, "days": r["days"], "rounds_done": done})

    # ---------------------------------------------------------- 管理者
    @app.get("/api/admin/overview")
    def a_overview():
        need_admin()
        return jsonify(stats.overview(get_db(), cfg))

    @app.get("/api/admin/students")
    def a_students():
        need_admin()
        return jsonify({"students": stats.students(get_db(), cfg)})

    @app.get("/api/admin/students/<int:uid>")
    def a_student(uid):
        need_admin()
        d = stats.student_detail(get_db(), cfg, uid)
        if not d:
            raise ApiError(404, "生徒が見つかりません")
        return jsonify(d)

    @app.get("/api/admin/hardest")
    def a_hardest():
        need_admin()
        return jsonify({"questions": stats.hardest_questions(get_db())})

    @app.get("/api/admin/usage")
    def a_usage():
        need_admin()
        return jsonify({"days": stats.usage(get_db(), cfg)})

    @app.get("/api/admin/questions/counts")
    def a_counts():
        need_admin()
        return jsonify({"levels": stats.question_counts(get_db())})

    @app.get("/api/admin/export/attempts.csv")
    def a_export():
        need_admin()
        return Response(stats.attempts_csv(get_db(), cfg), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=attempts.csv"})

    @app.post("/api/admin/users")
    def a_create_users():
        need_admin()
        b = request.get_json(silent=True) or {}
        lst = b.get("users")
        if not isinstance(lst, list) or not lst or len(lst) > 200:
            raise ApiError(400, "生徒の一覧（1〜200人）を送ってください")
        created, errors = [], []
        conn = get_db()
        for i, it in enumerate(lst):
            lid = str(it.get("login_id", "")).strip() if isinstance(it, dict) else ""
            label = str(it.get("label", "")).strip()[:40] if isinstance(it, dict) else ""
            pw = str(it.get("password", "")).strip() if isinstance(it, dict) else ""
            if not LOGIN_RE.match(lid):
                errors.append({"login_id": lid, "message": "IDは半角英数字と _ . - の3〜32文字にしてください"})
                continue
            if pw and len(pw) < 6:
                errors.append({"login_id": lid, "message": "パスワードは6文字以上にしてください"})
                continue
            pw = pw or gen_password()
            try:
                with db.tx(conn):
                    conn.execute("INSERT INTO users(login_id,password_hash,role,label) VALUES (?,?, 'student', ?)", (lid, hash_password(pw), label))
                created.append({"login_id": lid, "label": label, "password": pw})
            except sqlite3.IntegrityError:
                errors.append({"login_id": lid, "message": "このIDはすでに使われています"})
        return jsonify({"created": created, "errors": errors})

    def _student_or_404(uid):
        row = get_db().execute("SELECT id, login_id FROM users WHERE id=? AND role='student'", (uid,)).fetchone()
        if not row:
            raise ApiError(404, "生徒が見つかりません")
        return row

    @app.post("/api/admin/users/<int:uid>/reset_password")
    def a_reset(uid):
        need_admin()
        _student_or_404(uid)
        b = request.get_json(silent=True) or {}
        pw = str(b.get("password", "")).strip() or gen_password()
        if len(pw) < 6:
            raise ApiError(400, "パスワードは6文字以上にしてください")
        with db.tx(get_db()):
            get_db().execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(pw), uid))
            get_db().execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        return jsonify({"password": pw})

    @app.post("/api/admin/users/<int:uid>/active")
    def a_active(uid):
        need_admin()
        _student_or_404(uid)
        active_flag = 1 if (request.get_json(silent=True) or {}).get("active") else 0
        with db.tx(get_db()):
            get_db().execute("UPDATE users SET is_active=? WHERE id=?", (active_flag, uid))
            if not active_flag:
                get_db().execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        return jsonify({"ok": True, "active": bool(active_flag)})

    @app.post("/api/admin/questions/import")
    def a_import():
        need_admin()
        b = request.get_json(silent=True) or {}
        res = questions_io.import_csv(get_db(), str(b.get("csv", "")))
        return jsonify(res), (200 if res["ok"] else 400)

    return app
