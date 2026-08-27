"""Sync local git with Cursor Cloud Agent branches on the same GitHub remote.

1. git fetch --prune
2. fast-forward the current branch if it is tracking origin
3. push the current branch if it is ahead
4. if CURSOR_API_KEY is set, list Cloud Agents and add/update worktrees
   under .cursor-cloud-worktrees/ for this repository's agent branches
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEST = ROOT / ".cursor-cloud-worktrees"
API = os.environ.get("CURSOR_API_BASE", "https://api.cursor.com").rstrip("/")
SAFE_BRANCH = re.compile(r"[^A-Za-z0-9._-]+")


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def run_git(*args: str, check: bool = True, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        check=check,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        cwd=str(cwd or ROOT),
    )


def git_out(*args: str) -> str:
    return (run_git(*args).stdout or "").strip()


def api_get(path: str, key: str) -> dict:
    req = urllib.request.Request(
        f"{API}{path}",
        headers={"Accept": "application/json", "User-Agent": "base-wp-ja-auto-sync"},
    )
    password_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    password_mgr.add_password(None, API, key, "")
    opener = urllib.request.build_opener(urllib.request.HTTPBasicAuthHandler(password_mgr))
    try:
        with opener.open(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Cursor API {path} failed: HTTP {exc.code} {body[:200]}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Cursor API {path} failed: {exc}") from exc
    return payload if isinstance(payload, dict) else {}


def normalize_repo(url: str) -> str:
    text = (url or "").strip().lower()
    text = re.sub(r"^https?://", "", text)
    text = re.sub(r"^git@", "", text)
    text = text.replace("github.com:", "github.com/")
    text = re.sub(r"\.git$", "", text)
    return text.strip("/")


def this_repo_keys() -> set[str]:
    keys: set[str] = set()
    try:
        remote = git_out("remote", "get-url", "origin")
    except subprocess.CalledProcessError:
        return keys
    keys.add(normalize_repo(remote))
    return {k for k in keys if k}


def belongs_here(repo_url: str, local_keys: set[str]) -> bool:
    remote = normalize_repo(repo_url)
    if not remote or not local_keys:
        return True
    return any(remote == key or remote.endswith("/" + key.split("/", 1)[-1]) for key in local_keys)


def list_agent_ids(key: str) -> list[str]:
    ids: list[str] = []
    cursor = ""
    while True:
        qs = "limit=100&includeArchived=false"
        if cursor:
            qs += f"&cursor={urllib.parse.quote(cursor)}"
        data = api_get(f"/v1/agents?{qs}", key)
        rows = data.get("items") or data.get("agents") or []
        for row in rows:
            if isinstance(row, dict) and row.get("id"):
                ids.append(str(row["id"]))
        cursor = str(data.get("nextCursor") or "")
        if not cursor:
            break
    return ids


def agent_branches(key: str, agent_id: str) -> list[tuple[str, str]]:
    detail = api_get(f"/v1/agents/{agent_id}", key)
    found: list[tuple[str, str]] = []

    def take(payload: dict) -> None:
        git = payload.get("git") if isinstance(payload, dict) else None
        branches = (git or {}).get("branches") if isinstance(git, dict) else None
        if not isinstance(branches, list):
            return
        for item in branches:
            if not isinstance(item, dict):
                continue
            branch = str(item.get("branch") or "").strip()
            repo = str(item.get("repoUrl") or "")
            if branch:
                found.append((branch, repo))

    take(detail)
    run_id = str(detail.get("latestRunId") or "")
    if run_id:
        take(api_get(f"/v1/agents/{agent_id}/runs/{run_id}", key))
    # unique preserve order
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for branch, repo in found:
        if branch in seen:
            continue
        seen.add(branch)
        out.append((branch, repo))
    return out


def ensure_worktree(branch: str) -> str:
    remote_ref = f"origin/{branch}"
    show = run_git("show-ref", "--verify", "--quiet", f"refs/remotes/{remote_ref}", check=False)
    if show.returncode != 0:
        return f"skip  {branch} (origin に未push)"
    safe = SAFE_BRANCH.sub("-", branch).strip("-") or "branch"
    path = DEST / safe
    if path.exists():
        run_git("fetch", "origin", branch, cwd=path)
        run_git("checkout", "--force", branch, check=False, cwd=path)
        pull = run_git("pull", "--ff-only", check=False, cwd=path)
        if pull.returncode != 0:
            return f"warn  {branch} -> {path} (fast-forward できませんでした)"
        return f"ok    {branch} -> {path}"
    add = run_git("worktree", "add", str(path), remote_ref, check=False)
    if add.returncode != 0:
        err = (add.stderr or add.stdout or "").strip().splitlines()
        return f"fail  {branch}: {err[-1] if err else 'worktree add に失敗'}"
    return f"ok    {branch} -> {path}"


def sync_tracking_branch() -> None:
    print("git fetch origin --prune")
    run_git("fetch", "origin", "--prune")
    branch = git_out("rev-parse", "--abbrev-ref", "HEAD")
    upstream = run_git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False)
    if upstream.returncode != 0:
        print(f"現在のブランチ {branch} は upstream 未設定のため pull/push をスキップします。")
        return
    status = git_out("status", "--porcelain")
    if status:
        print("未コミットの変更があるため、現在ブランチの pull --ff-only はしません。")
    else:
        pull = run_git("pull", "--ff-only", check=False)
        if pull.returncode != 0:
            print((pull.stderr or pull.stdout or "git pull に失敗").strip())
        else:
            print(f"ok    pull --ff-only {branch}")
    ahead = git_out("rev-list", "--count", "@{u}..HEAD")
    if ahead.isdigit() and int(ahead) > 0:
        print(f"git push ({ahead} commit ahead)")
        push = run_git("push", "-u", "origin", "HEAD", check=False)
        if push.returncode != 0:
            print((push.stderr or push.stdout or "git push に失敗").strip())
            raise SystemExit(1)
        print(f"ok    pushed {branch}")
    else:
        print(f"ok    {branch} は origin と揃っています（push 不要）")


def main() -> int:
    load_dotenv(ROOT / ".env")
    if not (ROOT / ".git").exists() and run_git("rev-parse", "--show-toplevel", check=False).returncode != 0:
        print("git リポジトリではありません。")
        return 1
    DEST.mkdir(parents=True, exist_ok=True)
    sync_tracking_branch()

    key = (os.environ.get("CURSOR_API_KEY") or "").strip()
    if not key:
        print("CURSOR_API_KEY が無いので Cloud Agent の worktree 作成はスキップします。")
        print("リモートの cursor/* ブランチだけ fetch 済みです。")
        return 0

    local_keys = this_repo_keys()
    print("Cloud Agent を取得しています…")
    ids = list_agent_ids(key)
    print(f"エージェント {len(ids)} 件")
    seen_branches: set[str] = set()
    for agent_id in ids:
        for branch, repo in agent_branches(key, agent_id):
            if branch in seen_branches:
                continue
            if repo and not belongs_here(repo, local_keys):
                print(f"skip  {branch} (別リポジトリ {repo})")
                continue
            seen_branches.add(branch)
            print(ensure_worktree(branch))
    if not seen_branches:
        print("このリポジトリ向けの Cloud Agent ブランチはありませんでした。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or exc.stdout or str(exc)).strip()
        print(err)
        raise SystemExit(1) from exc
