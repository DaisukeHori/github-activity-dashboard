#!/usr/bin/env python3
"""
GitHub Activity Dashboard v4 - Incremental fetch with commits_db.json
Only fetches code stats for new commits (by SHA).
PRs/Issues are cheap (search API) so re-fetched each run.
"""

import os
import sys
import json
import time
import requests
from datetime import datetime, timedelta, timezone

TOKEN = os.environ.get("GH_PAT")
if not TOKEN:
    print("ERROR: GH_PAT environment variable not set")
    sys.exit(1)

HEADERS = {
    "Authorization": f"token {TOKEN}",
    "Accept": "application/vnd.github+json",
}
USERNAME = "DaisukeHori"
LOOKBACK_DAYS = 90
PRUNE_DAYS = 100  # DBは表示窓より少し広めに保持
OUTPUT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(OUTPUT_DIR, "commits_db.json")
DATA_PATH = os.path.join(OUTPUT_DIR, "data.json")


def api_get(url, params=None):
    r = requests.get(url, headers=HEADERS, params=params)
    remaining = int(r.headers.get("x-ratelimit-remaining", 100))
    if remaining < 20:
        reset = int(r.headers.get("x-ratelimit-reset", 0))
        wait = max(reset - time.time(), 1)
        print(f"  Rate limit low ({remaining}), sleeping {wait:.0f}s...")
        time.sleep(min(wait, 65))
    return r


def load_db():
    """Load existing commits DB. Format: {sha: {utc, repo, msg, add, del}}"""
    if os.path.exists(DB_PATH):
        with open(DB_PATH, "r") as f:
            return json.load(f)
    return {}


def save_db(db):
    with open(DB_PATH, "w") as f:
        json.dump(db, f, ensure_ascii=False, separators=(",", ":"))
    size_kb = os.path.getsize(DB_PATH) // 1024
    print(f"  commits_db.json: {len(db)} entries ({size_kb}KB)")


def fetch_repos(since_iso):
    repos = []
    page = 1
    while True:
        r = api_get("https://api.github.com/user/repos",
                     {"per_page": 100, "page": page, "sort": "pushed", "direction": "desc"})
        data = r.json()
        if not data or not isinstance(data, list):
            break
        repos.extend(data)
        page += 1
        if len(data) < 100:
            break
    return [r for r in repos if r.get("pushed_at", "") >= since_iso]


def fetch_commits_and_update_db(repos, since_iso, db):
    """Fetch commits, add new ones to DB, fetch code stats only for new SHAs."""
    new_count = 0
    skipped = 0
    new_shas = []  # (sha, repo_full) for code stats fetch

    for repo_obj in repos:
        repo = repo_obj["full_name"]
        short_name = repo.split("/")[-1]
        page = 1
        while True:
            r = api_get(f"https://api.github.com/repos/{repo}/commits",
                        {"since": since_iso, "author": USERNAME, "per_page": 100, "page": page})
            commits = r.json()
            if not isinstance(commits, list) or not commits:
                break
            for c in commits:
                sha = c["sha"]
                if sha in db:
                    skipped += 1
                    continue
                utc = c["commit"]["author"]["date"]
                msg = c["commit"]["message"].split("\n")[0][:80]
                db[sha] = {
                    "utc": utc,
                    "repo": short_name,
                    "msg": msg,
                    "add": 0,
                    "del": 0,
                }
                new_shas.append((sha, repo))
                new_count += 1
            page += 1
            if len(commits) < 100:
                break
        print(f"  {repo}: done")

    print(f"  New commits: {new_count}, Already in DB: {skipped}")

    # Fetch code stats only for new commits
    if new_shas:
        print(f"  Fetching code stats for {len(new_shas)} new commits...")
        for i, (sha, repo_full) in enumerate(new_shas):
            cr = api_get(f"https://api.github.com/repos/{repo_full}/commits/{sha}")
            if cr.status_code == 200:
                stats = cr.json().get("stats", {})
                db[sha]["add"] = stats.get("additions", 0)
                db[sha]["del"] = stats.get("deletions", 0)
            if (i + 1) % 50 == 0:
                print(f"    {i+1}/{len(new_shas)}")
        print(f"    {len(new_shas)}/{len(new_shas)} done")
    else:
        print("  No new code stats needed ✨")

    return new_count


def fetch_search_timestamps(query_type, since_date):
    timestamps = []
    page = 1
    while True:
        r = api_get("https://api.github.com/search/issues",
                     {"q": f"author:{USERNAME} type:{query_type} created:>={since_date}",
                      "per_page": 100, "page": page})
        items = r.json().get("items", [])
        for item in items:
            timestamps.append(item["created_at"])
        if len(items) < 100:
            break
        page += 1
    return timestamps


def prune_db(db, cutoff_iso):
    """Remove commits older than cutoff to keep DB from growing forever."""
    before = len(db)
    to_remove = [sha for sha, c in db.items() if c["utc"] < cutoff_iso]
    for sha in to_remove:
        del db[sha]
    if to_remove:
        print(f"  Pruned {len(to_remove)} old entries (before: {before}, after: {len(db)})")



def fetch_repo_loc(repos):
    """Estimate lines of code per repo via GitHub languages API (bytes -> LOC)."""
    repo_loc = {}
    for repo_obj in repos:
        repo = repo_obj["full_name"]
        short = repo.split("/")[-1]
        r = api_get(f"https://api.github.com/repos/{repo}/languages")
        if r.status_code == 200:
            langs = r.json()
            total_bytes = sum(langs.values())
            # ~35 bytes per line is a reasonable cross-language average
            repo_loc[short] = round(total_bytes / 35)
        else:
            repo_loc[short] = 0
    return repo_loc


def fetch_open_counts():
    """Fetch total open PRs and Issues across all user repos."""
    open_prs = 0
    r = api_get("https://api.github.com/search/issues",
                {"q": f"author:{USERNAME} type:pr state:open", "per_page": 1})
    if r.status_code == 200:
        open_prs = r.json().get("total_count", 0)

    open_issues = 0
    r = api_get("https://api.github.com/search/issues",
                {"q": f"author:{USERNAME} type:issue state:open", "per_page": 1})
    if r.status_code == 200:
        open_issues = r.json().get("total_count", 0)

    return open_prs, open_issues


def main():
    now = datetime.now(timezone.utc)
    since_dt = now - timedelta(days=LOOKBACK_DAYS)
    since_iso = since_dt.strftime("%Y-%m-%dT00:00:00Z")
    since_date = since_dt.strftime("%Y-%m-%d")
    # Keep DB entries slightly longer than display window for safety
    prune_cutoff = (now - timedelta(days=PRUNE_DAYS)).strftime("%Y-%m-%dT00:00:00Z")

    print(f"=== GitHub Activity Dashboard v4 (Incremental) ===")
    print(f"Period: {since_date} ~ {now.strftime('%Y-%m-%d')}")
    print(f"Fetch time: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}")

    # Load DB
    print("\n[1/8] Loading commits DB...")
    db = load_db()
    print(f"  Existing entries: {len(db)}")

    # Prune old entries
    print("\n[2/8] Pruning old entries...")
    prune_db(db, prune_cutoff)

    # Fetch repos
    print("\n[3/8] Fetching repos...")
    repos = fetch_repos(since_iso)
    print(f"  Active repos: {len(repos)}")

    # Fetch commits (incremental)
    print("\n[4/8] Fetching commits (incremental)...")
    new_count = fetch_commits_and_update_db(repos, since_iso, db)

    # Save updated DB
    save_db(db)

    # Fetch PRs (cheap, always re-fetch)
    print("\n[5/8] Fetching PRs...")
    pr_timestamps = fetch_search_timestamps("pr", since_date)
    print(f"  Total PRs: {len(pr_timestamps)}")

    # Fetch Issues (cheap, always re-fetch)
    print("\n[6/8] Fetching Issues...")
    issue_timestamps = fetch_search_timestamps("issue", since_date)
    print(f"  Total Issues: {len(issue_timestamps)}")

    # Fetch repo LOC
    print("\n[7/8] Fetching repo LOC (languages API)...")
    repo_loc = fetch_repo_loc(repos)
    total_loc = sum(repo_loc.values())
    print(f"  Total estimated LOC: {total_loc:,}")
    for name, loc in sorted(repo_loc.items(), key=lambda x: -x[1])[:5]:
        print(f"    {name}: {loc:,}")

    # Fetch open PR/Issue counts
    print("\n[8/8] Fetching open PR/Issue counts...")
    open_prs, open_issues = fetch_open_counts()
    print(f"  Open PRs: {open_prs}, Open Issues: {open_issues}")

    # Build data.json from DB (only entries within lookback window)
    commits_in_window = []
    for sha, c in db.items():
        if c["utc"] >= since_iso:
            commits_in_window.append({
                "utc": c["utc"],
                "repo": c["repo"],
                "msg": c["msg"],
                "sha": sha[:7],
                "add": c["add"],
                "del": c["del"],
            })

    # Sort by date desc for consistent output
    commits_in_window.sort(key=lambda x: x["utc"], reverse=True)

    data = {
        "updated_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "since_utc": since_iso,
        "commits": commits_in_window,
        "prs": pr_timestamps,
        "issues": issue_timestamps,
        "repo_loc": repo_loc,
        "total_loc": total_loc,
        "open_prs": open_prs,
        "open_issues": open_issues,
    }

    with open(DATA_PATH, "w") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    data_kb = os.path.getsize(DATA_PATH) // 1024
    print(f"\n✅ data.json: {len(commits_in_window)} commits ({data_kb}KB)")
    print(f"✅ New API calls for code stats: {new_count} (saved ~{len(db) - new_count} calls)")


if __name__ == "__main__":
    main()
