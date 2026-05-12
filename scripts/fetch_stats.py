#!/usr/bin/env python3
"""
GitHub Activity Dashboard v3 - Timezone-agnostic raw data output
All aggregation done client-side with selectable timezone.
"""

import os
import sys
import json
import time
import requests
from datetime import datetime, timedelta, timezone
from collections import defaultdict

TOKEN = os.environ.get("GH_PAT")
if not TOKEN:
    print("ERROR: GH_PAT environment variable not set")
    sys.exit(1)

HEADERS = {
    "Authorization": f"token {TOKEN}",
    "Accept": "application/vnd.github+json",
}
USERNAME = "DaisukeHori"
LOOKBACK_DAYS = 14


def api_get(url, params=None):
    r = requests.get(url, headers=HEADERS, params=params)
    remaining = int(r.headers.get("x-ratelimit-remaining", 100))
    if remaining < 20:
        reset = int(r.headers.get("x-ratelimit-reset", 0))
        wait = max(reset - time.time(), 1)
        print(f"  Rate limit low ({remaining}), sleeping {wait:.0f}s...")
        time.sleep(min(wait, 65))
    return r


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


def fetch_all_commits(repos, since_iso):
    """Fetch all commits with UTC timestamps."""
    all_commits = []
    sha_repo_map = {}  # sha -> (repo, date_str)

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
                utc = c["commit"]["author"]["date"]
                sha = c["sha"]
                msg = c["commit"]["message"].split("\n")[0][:80]
                all_commits.append({
                    "utc": utc,
                    "repo": short_name,
                    "msg": msg,
                    "sha": sha[:7],
                    "full_sha": sha,
                    "full_repo": repo,
                })
                sha_repo_map[sha] = (repo, utc[:10])
            page += 1
            if len(commits) < 100:
                break
        print(f"  Commits: {repo} done")

    return all_commits, sha_repo_map


def fetch_code_stats(all_commits):
    """Fetch additions/deletions per commit and attach to commit objects."""
    for i, c in enumerate(all_commits):
        repo = c["full_repo"]
        sha_full = c.get("full_sha", c["sha"])
        if len(sha_full) < 10:
            # short sha, skip
            c["add"] = 0
            c["del"] = 0
            continue
        cr = api_get(f"https://api.github.com/repos/{repo}/commits/{sha_full}")
        if cr.status_code == 200:
            stats = cr.json().get("stats", {})
            c["add"] = stats.get("additions", 0)
            c["del"] = stats.get("deletions", 0)
        else:
            c["add"] = 0
            c["del"] = 0

        if (i + 1) % 100 == 0:
            print(f"  Code stats: {i+1}/{len(all_commits)}")

    print(f"  Code stats: {len(all_commits)}/{len(all_commits)} done")


def fetch_search_timestamps(query_type, since_date):
    """Fetch PR or Issue creation UTC timestamps."""
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


def main():
    now = datetime.now(timezone.utc)
    since_dt = now - timedelta(days=LOOKBACK_DAYS)
    since_iso = since_dt.strftime("%Y-%m-%dT00:00:00Z")
    since_date = since_dt.strftime("%Y-%m-%d")

    print(f"=== GitHub Activity Dashboard v3 (TZ-agnostic) ===")
    print(f"Period since: {since_date}")
    print(f"Fetch time: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}")

    print("\n[1/5] Fetching repos...")
    repos = fetch_repos(since_iso)
    print(f"  Active repos: {len(repos)}")

    print("\n[2/5] Fetching commits...")
    all_commits, sha_repo_map = fetch_all_commits(repos, since_iso)
    print(f"  Total commits: {len(all_commits)}")

    print("\n[3/5] Fetching code stats...")
    fetch_code_stats(all_commits)
    total_add = sum(c["add"] for c in all_commits)
    total_del = sum(c["del"] for c in all_commits)
    print(f"  Total: +{total_add:,} / -{total_del:,}")

    print("\n[4/5] Fetching PRs...")
    pr_timestamps = fetch_search_timestamps("pr", since_date)
    print(f"  Total PRs: {len(pr_timestamps)}")

    print("\n[5/5] Fetching Issues...")
    issue_timestamps = fetch_search_timestamps("issue", since_date)
    print(f"  Total Issues: {len(issue_timestamps)}")

    # Clean commit objects for JSON (remove internal fields)
    clean_commits = []
    for c in all_commits:
        clean_commits.append({
            "utc": c["utc"],
            "repo": c["repo"],
            "msg": c["msg"],
            "sha": c["sha"],
            "add": c["add"],
            "del": c["del"],
        })

    data = {
        "updated_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "since_utc": since_iso,
        "commits": clean_commits,
        "prs": pr_timestamps,
        "issues": issue_timestamps,
    }

    output_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    with open(os.path.join(output_dir, "data.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"\n✅ data.json ({len(json.dumps(data))//1024}KB)")

    # Copy index.html template (static, all logic in JS)
    # index.html is maintained separately
    print("✅ Done (index.html is static, reads data.json)")


if __name__ == "__main__":
    main()
