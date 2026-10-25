#!/usr/bin/env python3
"""GitHubへ公開するスクリプト（秘密情報の混入チェックつき）。
  python3 scripts/publish_github.py https://github.com/ユーザー名/eigo-dojo.git     既存の空リポジトリへ
  python3 scripts/publish_github.py --create [--name eigo-dojo]                     gh コマンドで非公開リポジトリを作って公開
オプション: --yes（確認を省く） --branch main --message "コミットメッセージ" --user-name 名前 --user-email メール
認証: gh auth login 済み、または環境変数 GITHUB_TOKEN（Personal Access Token）。トークンは .git/config に保存しません。"""
import base64
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRET_PATTERNS = [(re.compile(r"AQ\.[A-Za-z0-9_\-]{20,}"), "Google APIキー(AQ.)"), (re.compile(r"AIza[0-9A-Za-z_\-]{30,}"), "Google APIキー(AIza)"),
                   (re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"), "GitHubトークン"), (re.compile(r"github_pat_[A-Za-z0-9_]{30,}"), "GitHubトークン"),
                   (re.compile(r"sk-[A-Za-z0-9_\-]{30,}"), "APIキー(sk-)")]
FORBIDDEN_NAMES = re.compile(r"(^|/)(\.env|\.dev\.vars|[^/]*\.db|[^/]*\.db-(wal|shm))$")


def git(*args, check=True, extra=None, capture=True):
    cmd = ["git", *(extra or []), *args]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=capture, text=True)
    if check and r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "").strip() or f"git {' '.join(args)} failed")
    return r


def scan(files):
    """コミット対象に、秘密情報・DB・.env が含まれていないか調べる。見つけたものの一覧を返す。"""
    bad = []
    for f in files:
        if FORBIDDEN_NAMES.search(f) and not f.endswith((".env.example", ".dev.vars.example")):
            bad.append((f, 0, "コミットしてはいけないファイル（.env / データベース）"))
            continue
        path = os.path.join(ROOT, f)
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                for n, line in enumerate(fh, 1):
                    for pat, label in SECRET_PATTERNS:
                        if pat.search(line):
                            bad.append((f, n, label))
        except (IsADirectoryError, FileNotFoundError):
            pass
    return bad


def main(argv):
    yes = "--yes" in argv
    opt = lambda name, default=None: argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else default
    branch, msg = opt("--branch", "main"), opt("--message", "えいご道場: 更新")
    url = next((a for a in argv if a.startswith(("http://", "https://", "git@", "file://", "/")) or a.endswith(".git")), None)
    create = "--create" in argv
    if not url and not create:
        print(__doc__)
        return 1
    try:
        git("--version")
    except Exception:
        print("[NG] Git が見つかりません。https://git-scm.com/ からインストールしてください。")
        return 1

    if not os.path.isdir(os.path.join(ROOT, ".git")):
        r = git("init", "-b", branch, check=False)
        if r.returncode != 0:
            git("init")
            git("checkout", "-B", branch)
        print(f"[OK] Gitリポジトリを初期化しました（ブランチ: {branch}）")
    if not git("config", "user.name", check=False).stdout.strip():
        git("config", "user.name", opt("--user-name", "eigo-dojo"))
    if not git("config", "user.email", check=False).stdout.strip():
        git("config", "user.email", opt("--user-email", "eigo-dojo@users.noreply.github.com"))

    # 秘密情報チェック（追加前・追加後の両方）
    cand = git("ls-files", "--cached", "--others", "--exclude-standard").stdout.splitlines()
    bad = scan(cand)
    if bad:
        print("[NG] 公開してはいけないものが見つかりました。公開を中止します:")
        for f, n, why in bad:
            print(f"     {f}{':' + str(n) if n else ''}  ← {why}")
        print("     該当の行を削除するか、.gitignore に追加してから、もう一度実行してください。")
        return 1
    print(f"[OK] 秘密情報チェック: {len(cand)}ファイルを確認、問題なし")
    if os.path.exists(os.path.join(ROOT, ".env")):
        ign = git("check-ignore", "-q", ".env", check=False).returncode == 0
        print(("[OK] .env はGitの対象外です" if ign else "[NG] .env が対象外になっていません"))
        if not ign:
            return 1

    git("add", "-A")
    staged = git("diff", "--cached", "--name-only").stdout.splitlines()
    bad = scan(staged)
    if bad:
        print("[NG] ステージされたファイルに問題があります:", bad)
        git("reset", "-q", check=False)
        return 1
    if staged:
        git("commit", "-q", "-m", msg)
        print(f"[OK] コミットしました（{len(staged)}ファイル）")
    else:
        print("[OK] 新しい変更はありません（すでにコミット済み）")

    if not yes:
        target = url or "新しい非公開リポジトリ（gh で作成）"
        if input(f"{target} に公開します。よろしいですか？ [y/N] ").strip().lower() != "y":
            print("中止しました")
            return 1

    token = os.environ.get("GITHUB_TOKEN", "")
    extra = None
    if token and url and url.startswith("https://"):  # 設定ファイルに保存せず、この1回だけ認証ヘッダーを付ける
        b = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        extra = ["-c", f"http.extraHeader=Authorization: Basic {b}"]
    try:
        if create:
            if subprocess.run(["gh", "--version"], capture_output=True).returncode != 0:
                print("[NG] gh コマンドがありません。https://cli.github.com/ をインストールして `gh auth login` を実行するか、URLを指定して実行してください。")
                return 1
            name = opt("--name", "eigo-dojo")
            r = subprocess.run(["gh", "repo", "create", name, "--private", "--source", ".", "--remote", "origin", "--push"], cwd=ROOT, text=True, capture_output=True)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip() or r.stdout.strip())
            print("[OK] 非公開リポジトリを作成して公開しました:", r.stdout.strip() or name)
        else:
            if git("remote", check=False).stdout.split().count("origin"):
                git("remote", "set-url", "origin", url)
            else:
                git("remote", "add", "origin", url)
            git("push", "-u", "origin", branch, extra=extra)
            print(f"[OK] 公開しました: {url}")
            print("     リポジトリの公開範囲が「Private」になっているか、GitHubの Settings で確認してください。")
        return 0
    except RuntimeError as e:
        msg_ = str(e).replace(token, "***") if token else str(e)
        print("[NG] 送信に失敗しました:", msg_)
        low = msg_.lower()
        if "authentication" in low or "403" in low or "could not read" in low or "denied" in low or "401" in low:
            print("     認証が必要です。次のどれかを行ってください:")
            print("       ・ gh auth login を実行して、もう一度実行する")
            print("       ・ GitHub の Settings > Developer settings > Personal access tokens でトークン(repo権限)を作り、")
            print("         環境変数 GITHUB_TOKEN に入れて、もう一度実行する")
        elif "not found" in low or "does not exist" in low:
            print("     リポジトリが見つかりません。URLと、リポジトリを先に作成したか（空のまま）を確認してください。")
        elif "rejected" in low or "non-fast-forward" in low:
            print("     リモートに別の内容があります。空のリポジトリを使うか、先に git pull を行ってください。")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
