#!/usr/bin/env python3
"""
GitHub Activity Dashboard v5 - 累積型データベース

設計思想:
- データは永続蓄積（prune しない）
- 初回は LOOKBACK_DAYS_INITIAL 分遡り、以降は差分のみ取得
- PRs/Issues も SHA/ID キーで DB 化
- Streak は GraphQL contributionsCollection で全期間取得（真の値）
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
GRAPHQL_HEADERS = {
    "Authorization": f"bearer {TOKEN}",
    "Content-Type": "application/json",
}
USERNAME = "DaisukeHori"
LOOKBACK_DAYS_INITIAL = 365   # DB が空の場合の初回遡り日数
LOOKBACK_DAYS_OVERLAP = 7     # 差分取得時のオーバーラップ日数
OUTPUT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMITS_DB_PATH = os.path.join(OUTPUT_DIR, "commits_db.json")
PRS_DB_PATH = os.path.join(OUTPUT_DIR, "prs_db.json")
ISSUES_DB_PATH = os.path.join(OUTPUT_DIR, "issues_db.json")
STREAK_DB_PATH = os.path.join(OUTPUT_DIR, "streak_db.json")
DATA_PATH = os.path.join(OUTPUT_DIR, "data.json")


# ============================================================
# 共通ユーティリティ
# ============================================================

def api_get(url, params=None):
    r = requests.get(url, headers=HEADERS, params=params)
    remaining = int(r.headers.get("x-ratelimit-remaining", 100))
    if remaining < 20:
        reset = int(r.headers.get("x-ratelimit-reset", 0))
        wait = max(reset - time.time(), 1)
        print(f"    Rate limit low ({remaining}), sleeping {wait:.0f}s...")
        time.sleep(min(wait, 65))
    return r


def graphql(query, variables):
    r = requests.post("https://api.github.com/graphql",
                      headers=GRAPHQL_HEADERS,
                      json={"query": query, "variables": variables})
    return r.json()


def load_db(path):
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {}


def save_db(path, db):
    with open(path, "w") as f:
        json.dump(db, f, ensure_ascii=False, separators=(",", ":"))
    size_kb = os.path.getsize(path) // 1024
    name = os.path.basename(path)
    if isinstance(db, dict):
        print(f"  {name}: {len(db)} entries ({size_kb}KB)")
    else:
        print(f"  {name}: ({size_kb}KB)")


def determine_since(db, key, contribution_years=None,
                    initial_days=LOOKBACK_DAYS_INITIAL,
                    overlap_days=LOOKBACK_DAYS_OVERLAP):
    """DB から差分取得開始日を決定。

    - DB が空: ターゲット最古日まで遡る
    - DB の最古日がターゲットより新しい: ターゲットまで遡る (バックフィル)
    - DB の最古日がターゲット以前: 通常の差分 (最新-overlap)

    ターゲット最古日:
    - contribution_years 指定時: 最古年の 1/1
    - それ以外: now - initial_days
    """
    now = datetime.now(timezone.utc)
    if contribution_years and len(contribution_years) > 0:
        earliest_year = min(contribution_years)
        target_oldest = datetime(earliest_year, 1, 1, tzinfo=timezone.utc)
    else:
        target_oldest = now - timedelta(days=initial_days)

    if not db:
        return target_oldest
    valid_values = [c[key] for c in db.values() if key in c and c[key]]
    if not valid_values:
        return target_oldest

    oldest_str = min(valid_values)
    latest_str = max(valid_values)
    oldest_dt = datetime.fromisoformat(oldest_str.replace("Z", "+00:00"))
    latest_dt = datetime.fromisoformat(latest_str.replace("Z", "+00:00"))

    # DB の最古日がターゲットより新しい → 遡って取得 (バックフィル)
    if oldest_dt > target_oldest:
        return target_oldest

    # 通常: 最新-オーバーラップで差分取得
    return latest_dt - timedelta(days=overlap_days)


def get_contribution_years(streak_db):
    """contribution_years を streak_db から取得、なければ GraphQL で軽量取得"""
    if streak_db and streak_db.get("contribution_years"):
        return streak_db["contribution_years"]
    q = """query($login: String!) {
        user(login: $login) {
            contributionsCollection { contributionYears }
        }
    }"""
    r = graphql(q, {"login": USERNAME})
    if "data" in r and r["data"].get("user"):
        return r["data"]["user"]["contributionsCollection"]["contributionYears"]
    return None


# ============================================================
# Repos
# ============================================================

def fetch_repos(since_iso=None):
    """全リポジトリ取得。since_iso 指定時はそれ以降に push されたもののみ。"""
    repos = []
    page = 1
    while True:
        r = api_get("https://api.github.com/user/repos",
                    {"per_page": 100, "page": page,
                     "sort": "pushed", "direction": "desc"})
        data = r.json()
        if not data or not isinstance(data, list):
            break
        repos.extend(data)
        page += 1
        if len(data) < 100:
            break
    if since_iso:
        return [r for r in repos if r.get("pushed_at", "") >= since_iso]
    return repos


# ============================================================
# Commits 差分取得 (SHA キー DB)
# ============================================================

def fetch_commits_incremental(repos, since_iso, db):
    new_count = 0
    skipped = 0
    new_shas = []

    for repo_obj in repos:
        repo = repo_obj["full_name"]
        short_name = repo.split("/")[-1]
        page = 1
        while True:
            r = api_get(f"https://api.github.com/repos/{repo}/commits",
                        {"since": since_iso, "author": USERNAME,
                         "per_page": 100, "page": page})
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
                    "utc": utc, "repo": short_name, "msg": msg,
                    "add": 0, "del": 0,
                }
                new_shas.append((sha, repo))
                new_count += 1
            page += 1
            if len(commits) < 100:
                break

    print(f"  New commits: {new_count}, Already in DB: {skipped}")

    if new_shas:
        print(f"  Fetching code stats for {len(new_shas)} new commits...")
        for i, (sha, repo_full) in enumerate(new_shas):
            cr = api_get(f"https://api.github.com/repos/{repo_full}/commits/{sha}")
            if cr.status_code == 200:
                stats = cr.json().get("stats", {})
                db[sha]["add"] = stats.get("additions", 0)
                db[sha]["del"] = stats.get("deletions", 0)
            if (i + 1) % 100 == 0:
                print(f"    {i+1}/{len(new_shas)}")
        print(f"    {len(new_shas)}/{len(new_shas)} done")
    else:
        print("  No new code stats needed ✨")

    return new_count


# ============================================================
# PRs / Issues 差分取得 (ID キー DB)
# ============================================================

def fetch_search_chunked(query_type, since_dt, until_dt, db):
    """期間を月単位で分割して取得し、DB に差分追加。"""
    new_count = 0
    updated_count = 0
    cur = since_dt
    one_month = timedelta(days=30)

    while cur < until_dt:
        nxt = min(cur + one_month, until_dt)
        chunk_from = cur.strftime("%Y-%m-%d")
        chunk_to = nxt.strftime("%Y-%m-%d")
        page = 1
        chunk_total = 0
        while True:
            q = f"author:{USERNAME} type:{query_type} created:{chunk_from}..{chunk_to}"
            r = api_get("https://api.github.com/search/issues",
                        {"q": q, "per_page": 100, "page": page,
                         "sort": "created", "order": "asc"})
            payload = r.json()
            items = payload.get("items", [])
            if not items:
                break
            for item in items:
                iid = str(item["id"])
                url_parts = item["repository_url"].split("/")
                repo_short = url_parts[-1] if url_parts else ""
                entry = {
                    "id": iid,
                    "number": item.get("number"),
                    "repo": repo_short,
                    "title": item.get("title", "")[:120],
                    "created_at": item["created_at"],
                    "closed_at": item.get("closed_at"),
                    "state": item.get("state"),
                }
                if query_type == "pr":
                    entry["merged_at"] = item.get("pull_request", {}).get(
                        "merged_at")
                if iid in db:
                    db[iid].update(entry)
                    updated_count += 1
                else:
                    db[iid] = entry
                    new_count += 1
                chunk_total += 1
            if len(items) < 100:
                break
            page += 1
        if chunk_total > 0:
            print(f"    [{chunk_from}..{chunk_to}] {query_type}: {chunk_total}")
        cur = nxt

    print(f"  {query_type}: New {new_count}, Updated {updated_count}")
    return new_count


# ============================================================
# repo LOC (現時点スナップショット)
# ============================================================

def fetch_repo_loc(repos):
    repo_loc = {}
    for repo_obj in repos:
        repo = repo_obj["full_name"]
        short = repo.split("/")[-1]
        r = api_get(f"https://api.github.com/repos/{repo}/languages")
        if r.status_code == 200:
            langs = r.json()
            total_bytes = sum(langs.values())
            repo_loc[short] = round(total_bytes / 35)
        else:
            repo_loc[short] = 0
    return repo_loc


def fetch_open_counts():
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


# ============================================================
# Streak (全期間 GraphQL contributionsCollection)
# ============================================================

def fetch_full_streak():
    """全 contribution年をループして真の streak と日別 contribution 数を取得。"""
    q1 = """query($login: String!) {
        user(login: $login) {
            contributionsCollection { contributionYears }
            createdAt
        }
    }"""
    r = graphql(q1, {"login": USERNAME})
    if "errors" in r or "data" not in r:
        print(f"  GraphQL error: {r}")
        return None
    user_data = r["data"]["user"]
    years = user_data["contributionsCollection"]["contributionYears"]
    created_at = user_data["createdAt"]
    print(f"  Contribution years: {years}")
    print(f"  Account created: {created_at}")

    day_counts = {}
    total = 0
    q2 = """query($login: String!, $from: DateTime!, $to: DateTime!) {
        user(login: $login) {
            contributionsCollection(from: $from, to: $to) {
                contributionCalendar {
                    totalContributions
                    weeks { contributionDays { date contributionCount } }
                }
            }
        }
    }"""
    for year in sorted(years):
        from_dt = f"{year}-01-01T00:00:00Z"
        to_dt = f"{year}-12-31T23:59:59Z"
        r2 = graphql(q2, {"login": USERNAME, "from": from_dt, "to": to_dt})
        if "errors" in r2:
            print(f"    Year {year}: error {r2['errors']}")
            continue
        cc = r2["data"]["user"]["contributionsCollection"]
        cal = cc["contributionCalendar"]
        year_total = cal["totalContributions"]
        total += year_total
        for w in cal["weeks"]:
            for d in w["contributionDays"]:
                day_counts[d["date"]] = d["contributionCount"]
        print(f"    Year {year}: {year_total} contributions")
        time.sleep(0.3)

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    all_dates = sorted(day_counts.keys())
    if not all_dates:
        return {
            "current_streak": 0, "longest_streak": 0,
            "total_contributions": 0, "first_contribution": None,
            "day_counts": {},
        }

    # Longest streak
    longest = 0
    longest_start = None
    longest_end = None
    cur = 0
    cur_start = None
    for date_str in all_dates:
        cnt = day_counts.get(date_str, 0)
        if cnt > 0:
            if cur == 0:
                cur_start = date_str
            cur += 1
            if cur > longest:
                longest = cur
                longest_start = cur_start
                longest_end = date_str
        else:
            cur = 0
            cur_start = None

    # Current streak (今日から逆向き)
    current = 0
    current_start = None
    check_dt = datetime.strptime(today_str, "%Y-%m-%d")
    if day_counts.get(today_str, 0) == 0:
        check_dt = check_dt - timedelta(days=1)
    while True:
        ds = check_dt.strftime("%Y-%m-%d")
        if day_counts.get(ds, 0) > 0:
            current += 1
            current_start = ds
            check_dt = check_dt - timedelta(days=1)
        else:
            break

    first_contribution = None
    for d in all_dates:
        if day_counts.get(d, 0) > 0:
            first_contribution = d
            break

    return {
        "current_streak": current,
        "current_start": current_start,
        "longest_streak": longest,
        "longest_start": longest_start,
        "longest_end": longest_end,
        "total_contributions": total,
        "first_contribution": first_contribution,
        "account_created_at": created_at,
        "contribution_years": sorted(years),
        "day_counts": day_counts,
        "computed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ============================================================
# Main
# ============================================================

def main():
    now = datetime.now(timezone.utc)
    print(f"=== GitHub Activity Dashboard v5 (累積型) ===")
    print(f"Fetch time: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}")

    # ---- Load all DBs ----
    print("\n[1] Loading DBs...")
    db_commits = load_db(COMMITS_DB_PATH)
    db_prs = load_db(PRS_DB_PATH)
    db_issues = load_db(ISSUES_DB_PATH)
    streak_db_existing = load_db(STREAK_DB_PATH)
    print(f"  commits: {len(db_commits)}, prs: {len(db_prs)}, issues: {len(db_issues)}")

    # ---- Get contribution years (for backfill target) ----
    print("\n[1.5] Getting contribution years (for backfill target)...")
    contribution_years = get_contribution_years(streak_db_existing)
    if contribution_years:
        print(f"  Contribution years: {sorted(contribution_years)} "
              f"(target oldest: {min(contribution_years)}-01-01)")
    else:
        print(f"  No contribution years available, falling back to "
              f"{LOOKBACK_DAYS_INITIAL}d lookback")

    # ---- Determine since (with backfill if DB doesn't cover full range) ----
    commits_since = determine_since(db_commits, "utc", contribution_years)
    prs_since = determine_since(db_prs, "created_at", contribution_years)
    issues_since = determine_since(db_issues, "created_at", contribution_years)
    print(f"\n  Commits since: {commits_since.strftime('%Y-%m-%d')}")
    print(f"  PRs since: {prs_since.strftime('%Y-%m-%d')}")
    print(f"  Issues since: {issues_since.strftime('%Y-%m-%d')}")
    commits_since_iso = commits_since.strftime("%Y-%m-%dT00:00:00Z")

    # ---- Fetch repos active since commits_since ----
    print("\n[2] Fetching active repos...")
    repos = fetch_repos(commits_since_iso)
    print(f"  Active repos (pushed since): {len(repos)}")

    # ---- Commits incremental ----
    print("\n[3] Fetching commits (incremental)...")
    fetch_commits_incremental(repos, commits_since_iso, db_commits)
    save_db(COMMITS_DB_PATH, db_commits)

    # ---- PRs incremental ----
    print("\n[4] Fetching PRs (incremental, chunked monthly)...")
    fetch_search_chunked("pr", prs_since, now, db_prs)
    save_db(PRS_DB_PATH, db_prs)

    # ---- Issues incremental ----
    print("\n[5] Fetching Issues (incremental, chunked monthly)...")
    fetch_search_chunked("issue", issues_since, now, db_issues)
    save_db(ISSUES_DB_PATH, db_issues)

    # ---- All repos (for repo_loc snapshot) ----
    print("\n[6] Fetching all repos for LOC snapshot...")
    all_repos = fetch_repos()
    print(f"  Total repos: {len(all_repos)}")
    repo_loc = fetch_repo_loc(all_repos)
    total_loc = sum(repo_loc.values())
    print(f"  Total LOC: {total_loc:,}")

    # ---- Open counts ----
    print("\n[7] Fetching open PR/Issue counts...")
    open_prs, open_issues = fetch_open_counts()
    print(f"  Open PRs: {open_prs}, Open Issues: {open_issues}")

    # ---- Streak (GraphQL all-time) ----
    print("\n[8] Fetching full streak (GraphQL contributionsCollection)...")
    streak = fetch_full_streak()
    if streak:
        save_db(STREAK_DB_PATH, streak)
        print(f"  Current: {streak['current_streak']}, "
              f"Longest: {streak['longest_streak']}, "
              f"Total: {streak['total_contributions']}")
    else:
        streak = load_db(STREAK_DB_PATH) or {
            "current_streak": 0, "longest_streak": 0,
            "total_contributions": 0, "day_counts": {},
        }

    # ---- Build data.json ----
    print("\n[9] Building data.json...")
    commits_list = []
    for sha, c in db_commits.items():
        commits_list.append({
            "utc": c["utc"], "repo": c["repo"], "msg": c["msg"],
            "sha": sha[:7], "add": c["add"], "del": c["del"],
        })
    commits_list.sort(key=lambda x: x["utc"], reverse=True)

    prs_list = []
    for pid, p in db_prs.items():
        prs_list.append({
            "id": pid, "number": p.get("number"),
            "repo": p.get("repo"), "title": p.get("title"),
            "created_at": p["created_at"],
            "closed_at": p.get("closed_at"),
            "merged_at": p.get("merged_at"),
            "state": p.get("state"),
        })
    prs_list.sort(key=lambda x: x["created_at"], reverse=True)

    issues_list = []
    for iid, ii in db_issues.items():
        issues_list.append({
            "id": iid, "number": ii.get("number"),
            "repo": ii.get("repo"), "title": ii.get("title"),
            "created_at": ii["created_at"],
            "closed_at": ii.get("closed_at"),
            "state": ii.get("state"),
        })
    issues_list.sort(key=lambda x: x["created_at"], reverse=True)

    if commits_list:
        oldest_utc = commits_list[-1]["utc"]
    else:
        oldest_utc = now.strftime("%Y-%m-%dT00:00:00Z")

    # ---- 1年カットオフでアーカイブ分離 ----
    print("\n[9.5] Splitting data.json (recent) / archive/YYYY.json (old)...")
    one_year_ago = now - timedelta(days=365)
    cutoff_iso = one_year_ago.strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"  Cutoff: {cutoff_iso}")

    # 直近1年と過去で分割
    commits_recent = [c for c in commits_list if c["utc"] >= cutoff_iso]
    commits_old = [c for c in commits_list if c["utc"] < cutoff_iso]
    prs_recent = [p for p in prs_list if p["created_at"] >= cutoff_iso]
    prs_old = [p for p in prs_list if p["created_at"] < cutoff_iso]
    issues_recent = [i for i in issues_list if i["created_at"] >= cutoff_iso]
    issues_old = [i for i in issues_list if i["created_at"] < cutoff_iso]
    print(f"  Recent: {len(commits_recent)} commits, "
          f"{len(prs_recent)} PRs, {len(issues_recent)} issues")
    print(f"  Old (archive): {len(commits_old)} commits, "
          f"{len(prs_old)} PRs, {len(issues_old)} issues")

    # 年単位でグルーピング
    archive_dir = os.path.join(OUTPUT_DIR, "archive")
    os.makedirs(archive_dir, exist_ok=True)
    archives_by_year = {}
    for c in commits_old:
        y = c["utc"][:4]
        archives_by_year.setdefault(y, {"commits": [], "prs": [], "issues": []})["commits"].append(c)
    for p in prs_old:
        y = p["created_at"][:4]
        archives_by_year.setdefault(y, {"commits": [], "prs": [], "issues": []})["prs"].append(p)
    for i in issues_old:
        y = i["created_at"][:4]
        archives_by_year.setdefault(y, {"commits": [], "prs": [], "issues": []})["issues"].append(i)

    # 既存 archive を読んで、変化のないものはそのまま (ファイル更新を最小化)
    archive_meta = []
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    for year in sorted(archives_by_year.keys()):
        year_data = archives_by_year[year]
        year_data["commits"].sort(key=lambda x: x["utc"], reverse=True)
        year_data["prs"].sort(key=lambda x: x["created_at"], reverse=True)
        year_data["issues"].sort(key=lambda x: x["created_at"], reverse=True)
        archive_path = os.path.join(archive_dir, f"{year}.json")
        archive_payload = {
            "year": int(year),
            "generated_at_utc": now_iso,
            "commits": year_data["commits"],
            "prs": year_data["prs"],
            "issues": year_data["issues"],
        }
        # 既存ファイルと比較 (timestamps を除く実コンテンツの差分)
        write_needed = True
        if os.path.exists(archive_path):
            try:
                with open(archive_path, "r") as f:
                    existing = json.load(f)
                if (len(existing.get("commits", [])) == len(year_data["commits"])
                        and len(existing.get("prs", [])) == len(year_data["prs"])
                        and len(existing.get("issues", [])) == len(year_data["issues"])):
                    # コンテンツ同一とみなして書き換えスキップ
                    write_needed = False
            except Exception:
                pass
        if write_needed:
            with open(archive_path, "w") as f:
                json.dump(archive_payload, f, ensure_ascii=False,
                          separators=(",", ":"))
            size_kb = os.path.getsize(archive_path) // 1024
            print(f"  archive/{year}.json: "
                  f"{len(year_data['commits'])} commits, "
                  f"{len(year_data['prs'])} PRs, "
                  f"{len(year_data['issues'])} issues ({size_kb}KB) [updated]")
        else:
            size_kb = os.path.getsize(archive_path) // 1024
            print(f"  archive/{year}.json: unchanged ({size_kb}KB) [skip]")
        archive_meta.append({
            "year": int(year),
            "file": f"archive/{year}.json",
            "commits": len(year_data["commits"]),
            "prs": len(year_data["prs"]),
            "issues": len(year_data["issues"]),
        })

    # since_utc は直近1年のオールデスト
    if commits_recent:
        since_utc = commits_recent[-1]["utc"]
    else:
        since_utc = cutoff_iso

    # day_contributions も1年分に絞る (フロント側の月別カードは12ヶ月固定なので)
    day_counts_full = streak.get("day_counts", {})
    cutoff_date = cutoff_iso[:10]
    day_counts_recent = {k: v for k, v in day_counts_full.items()
                         if k >= cutoff_date}

    data = {
        "updated_at_utc": now_iso,
        "since_utc": since_utc,
        "cutoff_utc": cutoff_iso,
        "commits": commits_recent,
        "prs": prs_recent,
        "issues": issues_recent,
        "archives": archive_meta,
        "repo_loc": repo_loc,
        "total_loc": total_loc,
        "open_prs": open_prs,
        "open_issues": open_issues,
        "streak": {
            "current_streak": streak.get("current_streak", 0),
            "current_start": streak.get("current_start"),
            "longest_streak": streak.get("longest_streak", 0),
            "longest_start": streak.get("longest_start"),
            "longest_end": streak.get("longest_end"),
            "total_contributions": streak.get("total_contributions", 0),
            "first_contribution": streak.get("first_contribution"),
            "account_created_at": streak.get("account_created_at"),
        },
        "day_contributions": day_counts_recent,
    }

    with open(DATA_PATH, "w") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    data_kb = os.path.getsize(DATA_PATH) // 1024
    print(f"\n✅ data.json: {len(commits_recent)} commits, "
          f"{len(prs_recent)} PRs, {len(issues_recent)} issues "
          f"({data_kb}KB, 直近1年分)")
    print(f"   archives: {len(archive_meta)} years")


if __name__ == "__main__":
    main()
