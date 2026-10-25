"""setup.py / check_gemini / publish_github のテスト（実際のGoogle・GitHubには接続しない）"""
import contextlib
import io
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from app import config
from tests.fake_gemini import FakeGemini
from tools import check_gemini

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAKE_KEY = "AQ" + "." + "TestKeyForUnitTests" + "x" * 24  # ソースに鍵らしき文字列を残さない


def run_check(env, args=()):
    buf = io.StringIO()
    with mock.patch.dict(os.environ, env, clear=False), contextlib.redirect_stdout(buf):
        code = check_gemini.main(list(args))
    return code, buf.getvalue()


class TestCheckGemini(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.envfile = os.path.join(self.tmp.name, ".env")
        self.base = {"EIGO_ENV_FILE": self.envfile, "GEMINI_API_KEY": FAKE_KEY, "GEMINI_MODEL": "gemini-flash-latest", "AI_MODE": "gemini", "AI_MAX_RPM": "0"}

    def tearDown(self):
        self.tmp.cleanup()

    def test_success_and_key_never_printed(self):
        with FakeGemini() as f:
            code, out = run_check(dict(self.base, GEMINI_BASE_URL=f.url))
        self.assertEqual(code, 0, out)
        self.assertIn("接続OK", out)
        self.assertIn("解説のサンプル", out)
        self.assertRegex(out, r"採点テスト.*\d+\.\d秒")  # 応答時間が表示される
        self.assertNotIn(FAKE_KEY, out)
        self.assertEqual(f.requests[0]["headers"]["x-goog-api-key"], FAKE_KEY)  # ヘッダーで送る
        self.assertNotIn(FAKE_KEY, f.requests[0]["path"])  # URLには入れない

    def test_thinking_budget_option_is_sent_only_when_set(self):
        with FakeGemini() as f:
            run_check(dict(self.base, GEMINI_BASE_URL=f.url))
            run_check(dict(self.base, GEMINI_BASE_URL=f.url, GEMINI_THINKING_BUDGET="0"))
        posts = [r["body"]["generationConfig"] for r in f.requests if r["body"]]
        self.assertTrue(all("thinkingConfig" not in g for g in posts[:2]))
        self.assertTrue(all(g.get("thinkingConfig") == {"thinkingBudget": 0} for g in posts[2:]))

    def test_token_type_unsupported_gives_specific_advice(self):
        with FakeGemini() as f:
            f.get_script = [("status", 401, {"code": 401, "status": "UNAUTHENTICATED", "message": "x", "details": [{"reason": "ACCESS_TOKEN_TYPE_UNSUPPORTED"}]})]
            code, out = run_check(dict(self.base, GEMINI_BASE_URL=f.url))
        self.assertEqual(code, 1)
        self.assertIn("別のプロジェクト", out)
        self.assertNotIn(FAKE_KEY, out)

    def test_invalid_key_and_disabled_api(self):
        for body, expect in [({"code": 400, "status": "INVALID_ARGUMENT", "message": "API key not valid. Please pass a valid API key."}, "キーが無効"),
                             ({"code": 403, "status": "PERMISSION_DENIED", "message": "Generative Language API has not been used in project 1 before or it is disabled."}, "有効になっていません")]:
            with FakeGemini() as f:
                f.get_script = [("status", body["code"], body)]
                code, out = run_check(dict(self.base, GEMINI_BASE_URL=f.url))
            self.assertEqual(code, 1)
            self.assertIn(expect, out)

    def test_model_not_listed_is_fixed_with_write_env(self):
        with FakeGemini() as f:
            code, out = run_check(dict(self.base, GEMINI_BASE_URL=f.url, GEMINI_MODEL="gemini-old-flash"), ["--write-env"])
        self.assertIn("gemini-9.5-flash に自動で切り替えました", out)
        txt = open(self.envfile, encoding="utf-8").read()
        self.assertIn("GEMINI_MODEL=gemini-9.5-flash", txt)
        self.assertIn("AI_MODE=gemini", txt)
        self.assertNotIn(FAKE_KEY, txt)  # 診断では .env にキーを書かない
        self.assertEqual(code, 0, out)

    def test_model_not_listed_without_write_env_reports_ng(self):
        with FakeGemini() as f:
            code, out = run_check(dict(self.base, GEMINI_BASE_URL=f.url, GEMINI_MODEL="gemini-old-flash"))
        self.assertEqual(code, 1)
        self.assertIn("おすすめ: gemini-9.5-flash", out)
        self.assertFalse(os.path.exists(self.envfile))

    def test_missing_or_malformed_key_and_network_down(self):
        code, out = run_check(dict(self.base, GEMINI_API_KEY="", GEMINI_BASE_URL="http://127.0.0.1:1/v1beta"))
        self.assertEqual(code, 1); self.assertIn("設定されていません", out)
        code, out = run_check(dict(self.base, GEMINI_API_KEY="abc def", GEMINI_BASE_URL="http://127.0.0.1:1/v1beta"))
        self.assertEqual(code, 1); self.assertIn("空白や引用符", out)
        code, out = run_check(dict(self.base, GEMINI_BASE_URL="http://127.0.0.1:1/v1beta"))
        self.assertEqual(code, 1); self.assertIn("接続できません", out)

    def test_ssl_certificate_error_gets_mac_advice(self):
        import ssl
        import urllib.error
        err = urllib.error.URLError(ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED"))
        with mock.patch("tools.check_gemini.urllib.request.urlopen", side_effect=err):
            code, out = run_check(dict(self.base, GEMINI_BASE_URL="https://example.invalid/v1beta"))
        self.assertEqual(code, 1)
        self.assertIn("Install Certificates.command", out)

    def test_pick_model(self):
        ms = [{"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
              {"name": "models/gemini-3-flash", "supportedGenerationMethods": ["generateContent"]},
              {"name": "models/gemini-3-flash-image", "supportedGenerationMethods": ["generateContent"]},
              {"name": "models/embedding-1", "supportedGenerationMethods": ["embedContent"]}]
        self.assertEqual(check_gemini.pick_model(ms, "gemini-2.5-flash")[:2], ("gemini-2.5-flash", True))
        self.assertEqual(check_gemini.pick_model(ms, "zzz")[:2], ("gemini-3-flash", False))
        self.assertEqual(check_gemini.pick_model([], "zzz")[0], None)

    def test_update_env_preserves_and_protects(self):
        with open(self.envfile, "w", encoding="utf-8") as f:
            f.write("# コメント\nA=1\nGEMINI_MODEL=old   # メモ\nGEMINI_MODEL=dup\nB=2\n")
        config.update_env({"GEMINI_MODEL": "new", "C": "3"}, self.envfile)
        lines = open(self.envfile, encoding="utf-8").read().splitlines()
        self.assertEqual(lines, ["# コメント", "A=1", "GEMINI_MODEL=new", "B=2", "C=3"])
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(os.stat(self.envfile).st_mode), 0o600)
        for k in ("C",):
            os.environ.pop(k, None)
        os.environ["GEMINI_MODEL"] = "gemini-flash-latest"


def copy_project(dst):
    shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.db", "*.db-*", ".env"))
    os.makedirs(os.path.join(dst, "data"), exist_ok=True)


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GEMINI_", "AI_", "EIGO_", "DB_PATH", "ADMIN_", "GITHUB_", "MOCK_"))}
    env.update(extra)
    return env


class TestSetup(unittest.TestCase):
    def run_setup(self, proj, env, *args):
        return subprocess.run([sys.executable, "setup.py", "--non-interactive", "--skip-install", *args], cwd=proj, env=env, capture_output=True, text=True, timeout=120)

    def test_setup_without_key_then_with_key(self):
        with tempfile.TemporaryDirectory() as d:
            proj = os.path.join(d, "p")
            copy_project(proj)
            r = self.run_setup(proj, clean_env(ADMIN_PASSWORD="adminpass123"))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            env_txt = open(os.path.join(proj, ".env"), encoding="utf-8").read()
            self.assertIn("AI_MODE=mock", env_txt)
            import sqlite3
            c = sqlite3.connect(os.path.join(proj, "data", "app.db"))
            self.assertEqual(c.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM questions").fetchone()[0], 1600)
            c.close()
            r2 = self.run_setup(proj, clean_env())  # 再実行しても壊れない
            self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
            self.assertIn("すでにあります", r2.stdout)
            with FakeGemini() as f:
                r3 = self.run_setup(proj, clean_env(GEMINI_API_KEY=FAKE_KEY, GEMINI_BASE_URL=f.url, AI_MAX_RPM="0"))
            self.assertEqual(r3.returncode, 0, r3.stdout + r3.stderr)
            self.assertIn("接続OK", r3.stdout)
            self.assertNotIn(FAKE_KEY, r3.stdout + r3.stderr)
            env_txt = open(os.path.join(proj, ".env"), encoding="utf-8").read()
            self.assertIn("AI_MODE=gemini", env_txt)
            self.assertIn("GEMINI_API_KEY=" + FAKE_KEY, env_txt)

    def test_setup_rejects_short_admin_password(self):
        with tempfile.TemporaryDirectory() as d:
            proj = os.path.join(d, "p")
            copy_project(proj)
            r = self.run_setup(proj, clean_env(ADMIN_PASSWORD="short"), "--no-sample")
            self.assertEqual(r.returncode, 1)
            self.assertIn("8文字以上", r.stdout)


class TestPublish(unittest.TestCase):
    def publish(self, proj, remote, *args, env=None):
        return subprocess.run([sys.executable, "tools/publish_github.py", remote, "--yes", "--user-name", "t", "--user-email", "t@example.com", *args],
                              cwd=proj, env=env or clean_env(), capture_output=True, text=True, timeout=120)

    def test_publish_to_local_remote_excludes_secrets(self):
        with tempfile.TemporaryDirectory() as d:
            proj, remote = os.path.join(d, "p"), os.path.join(d, "remote.git")
            copy_project(proj)
            subprocess.run(["git", "init", "--bare", "-q", remote], check=True)
            with open(os.path.join(proj, ".env"), "w", encoding="utf-8") as f:
                f.write("GEMINI_API_KEY=" + FAKE_KEY + "\n")  # .env はGit対象外のはず
            open(os.path.join(proj, "data", "app.db"), "wb").write(b"SQLite format 3\x00")
            r = self.publish(proj, remote)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("公開しました", r.stdout)
            files = subprocess.run(["git", "--git-dir", remote, "ls-tree", "-r", "--name-only", "main"], capture_output=True, text=True).stdout.splitlines()
            self.assertIn("app/engine.py", files)
            self.assertIn(".env.example", files)
            self.assertIn(".gitignore", files)
            self.assertNotIn(".env", files)
            self.assertFalse([f for f in files if f.endswith(".db")])
            hist = subprocess.run(["git", "--git-dir", remote, "log", "-p", "--all"], capture_output=True, text=True).stdout
            self.assertNotIn(FAKE_KEY, hist)  # 履歴にもキーが無い
            # 変更を加えて再公開
            open(os.path.join(proj, "NOTE.md"), "w", encoding="utf-8").write("update")
            r = self.publish(proj, remote)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(subprocess.run(["git", "--git-dir", remote, "rev-list", "--count", "main"], capture_output=True, text=True).stdout.strip(), "2")

    def test_blocks_when_secret_in_source(self):
        with tempfile.TemporaryDirectory() as d:
            proj, remote = os.path.join(d, "p"), os.path.join(d, "remote.git")
            copy_project(proj)
            subprocess.run(["git", "init", "--bare", "-q", remote], check=True)
            with open(os.path.join(proj, "app", "notes.py"), "w", encoding="utf-8") as f:
                f.write('KEY = "' + FAKE_KEY + '"\n')
            r = self.publish(proj, remote)
            self.assertEqual(r.returncode, 1)
            self.assertIn("公開を中止", r.stdout)
            self.assertIn("notes.py:1", r.stdout)
            self.assertNotIn(FAKE_KEY, r.stdout + r.stderr)
            refs = subprocess.run(["git", "--git-dir", remote, "for-each-ref"], capture_output=True, text=True).stdout
            self.assertEqual(refs.strip(), "")  # 何も送られていない

    def test_auth_failure_message_and_token_not_leaked(self):
        with tempfile.TemporaryDirectory() as d:
            proj = os.path.join(d, "p")
            copy_project(proj)
            token = "ghp_" + "Z" * 36
            r = self.publish(proj, "https://127.0.0.1:1/x/y.git", env=clean_env(GITHUB_TOKEN=token))
            self.assertEqual(r.returncode, 1)
            self.assertIn("送信に失敗", r.stdout)
            self.assertNotIn(token, r.stdout + r.stderr)
            cfg = open(os.path.join(proj, ".git", "config"), encoding="utf-8").read()
            self.assertNotIn(token, cfg)
            self.assertNotIn("Authorization", cfg)

    def test_project_itself_has_no_secrets(self):
        from tools import publish_github
        files = []
        for dp, dn, fn in os.walk(ROOT):
            dn[:] = [x for x in dn if x not in (".git", "__pycache__", "data")]
            files += [os.path.relpath(os.path.join(dp, x), ROOT) for x in fn if not x.endswith((".png", ".zip"))]
        self.assertEqual([b for b in publish_github.scan(files) if not b[0].endswith(".env")], [])


if __name__ == "__main__":
    unittest.main()
