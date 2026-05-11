#!/usr/bin/env python3
"""
GitHub Activity Dashboard - Data Fetcher
Fetches commit/PR/Issue/code stats and generates index.html
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

JST = timezone(timedelta(hours=9))


def api_get(url, params=None):
    """GitHub API GET with rate-limit handling."""
    r = requests.get(url, headers=HEADERS, params=params)
    remaining = int(r.headers.get("x-ratelimit-remaining", 100))
    if remaining < 20:
        reset = int(r.headers.get("x-ratelimit-reset", 0))
        wait = max(reset - time.time(), 1)
        print(f"  Rate limit low ({remaining}), sleeping {wait:.0f}s...")
        time.sleep(min(wait, 65))
    return r


def fetch_repos(since_iso):
    """Get all repos with activity since the given date."""
    repos = []
    page = 1
    while True:
        r = api_get(
            "https://api.github.com/user/repos",
            {"per_page": 100, "page": page, "sort": "pushed", "direction": "desc"},
        )
        data = r.json()
        if not data or not isinstance(data, list):
            break
        repos.extend(data)
        page += 1
        if len(data) < 100:
            break
    return [r for r in repos if r.get("pushed_at", "") >= since_iso]


def fetch_commits(repos, since_iso):
    """Fetch commits per day and per repo."""
    daily = defaultdict(int)
    per_repo = defaultdict(int)
    sha_map = defaultdict(list)

    for repo_obj in repos:
        repo = repo_obj["full_name"]
        page = 1
        while True:
            r = api_get(
                f"https://api.github.com/repos/{repo}/commits",
                {"since": since_iso, "author": USERNAME, "per_page": 100, "page": page},
            )
            commits = r.json()
            if not isinstance(commits, list) or not commits:
                break
            for c in commits:
                d = c["commit"]["author"]["date"][:10]
                daily[d] += 1
                per_repo[repo] += 1
                sha_map[repo].append((c["sha"], d))
            page += 1
            if len(commits) < 100:
                break

    return daily, per_repo, sha_map


def fetch_search(query_type, since_date):
    """Fetch PRs or Issues via search API."""
    daily = defaultdict(int)
    page = 1
    while True:
        r = api_get(
            "https://api.github.com/search/issues",
            {"q": f"author:{USERNAME} type:{query_type} created:>={since_date}", "per_page": 100, "page": page},
        )
        data = r.json()
        items = data.get("items", [])
        for item in items:
            daily[item["created_at"][:10]] += 1
        if len(items) < 100:
            break
        page += 1
    return daily


def fetch_code_stats(sha_map):
    """Fetch additions/deletions per commit."""
    daily_add = defaultdict(int)
    daily_del = defaultdict(int)

    ordered = sorted(sha_map.keys(), key=lambda r: len(sha_map[r]))
    for repo in ordered:
        items = sha_map[repo]
        if not items:
            continue
        for sha, date_str in items:
            cr = api_get(f"https://api.github.com/repos/{repo}/commits/{sha}")
            if cr.status_code == 200:
                stats = cr.json().get("stats", {})
                daily_add[date_str] += stats.get("additions", 0)
                daily_del[date_str] += stats.get("deletions", 0)
        print(f"  Code stats done: {repo} ({len(items)} commits)")

    return daily_add, daily_del


def generate_html(dates, daily_commits, daily_prs, daily_issues,
                   daily_add, daily_del, repo_commits, updated_at):
    """Generate the dashboard HTML."""
    # Prepare arrays
    labels = [f"{int(d[5:7])}/{int(d[8:10])}" for d in dates]
    commits_arr = [daily_commits.get(d, 0) for d in dates]
    prs_arr = [daily_prs.get(d, 0) for d in dates]
    issues_arr = [daily_issues.get(d, 0) for d in dates]
    add_arr = [daily_add.get(d, 0) for d in dates]
    del_arr = [daily_del.get(d, 0) for d in dates]

    total_commits = sum(commits_arr)
    total_prs = sum(prs_arr)
    total_issues = sum(issues_arr)
    total_add = sum(add_arr)
    total_del = sum(del_arr)
    total_net = total_add - total_del
    num_days = len(dates)

    # Repo data sorted by count
    sorted_repos = sorted(repo_commits.items(), key=lambda x: -x[1])
    repo_names_js = json.dumps([r.split("/")[-1] for r, _ in sorted_repos])
    repo_counts_js = json.dumps([c for _, c in sorted_repos])
    num_repos = len([c for _, c in sorted_repos if c > 0])

    # Color palette for repos
    repo_colors = ["#8b7cf7", "#34d399", "#fbbf24", "#f97066", "#60a5fa",
                    "#c084fc", "#fb923c", "#9b99a5"]
    while len(repo_colors) < len(sorted_repos):
        repo_colors.append("#9b99a5")
    repo_colors_js = json.dumps(repo_colors[:len(sorted_repos)])

    period_start = dates[0]
    period_end = dates[-1]
    period_str = f"{period_start[0:4]}/{int(period_start[5:7]):02d}/{int(period_start[8:10]):02d} 〜 {int(period_end[5:7]):02d}/{int(period_end[8:10]):02d}（{num_days}日間）"

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GitHub活動ダッシュボード - DaisukeHori</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📊</text></svg>">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@300;400;500;600&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  :root {{
    --bg: #0f1117; --surface: #1a1d27; --border: rgba(255,255,255,0.06);
    --text: #e8e6e3; --text2: #9b99a5; --text3: #6c6a78;
    --purple: #8b7cf7; --green: #34d399; --coral: #f97066; --blue: #60a5fa;
    --radius: 14px;
  }}
  body {{ font-family: 'Noto Sans JP', sans-serif; background: var(--bg); color: var(--text); padding: 2rem; min-height: 100vh; }}
  .header {{ display: flex; align-items: baseline; gap: 16px; margin-bottom: 2rem; flex-wrap: wrap; }}
  .header h1 {{ font-size: 22px; font-weight: 500; letter-spacing: -0.5px; }}
  .header .period {{ font-size: 13px; color: var(--text3); background: var(--surface); padding: 4px 12px; border-radius: 20px; }}
  .header .updated {{ font-size: 11px; color: var(--text3); margin-left: auto; }}
  .header .auto {{ font-size: 10px; color: var(--green); background: rgba(52,211,153,0.1); padding: 3px 8px; border-radius: 10px; }}
  .metrics {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 2rem; }}
  .metric {{ background: var(--surface); border-radius: var(--radius); padding: 1.25rem; border: 1px solid var(--border); position: relative; overflow: hidden; }}
  .metric::before {{ content: ''; position: absolute; top: 0; left: 0; width: 100%; height: 3px; }}
  .metric:nth-child(1)::before {{ background: linear-gradient(90deg, var(--purple), transparent); }}
  .metric:nth-child(2)::before {{ background: linear-gradient(90deg, var(--green), transparent); }}
  .metric:nth-child(3)::before {{ background: linear-gradient(90deg, var(--coral), transparent); }}
  .metric:nth-child(4)::before {{ background: linear-gradient(90deg, var(--blue), transparent); }}
  .metric .label {{ font-size: 12px; color: var(--text3); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 8px; }}
  .metric .value {{ font-size: 28px; font-weight: 600; letter-spacing: -1px; }}
  .metric:nth-child(1) .value {{ color: var(--purple); }}
  .metric:nth-child(2) .value {{ color: var(--green); }}
  .metric:nth-child(3) .value {{ color: var(--coral); }}
  .metric:nth-child(4) .value {{ color: var(--blue); }}
  .metric .sub {{ font-size: 11px; color: var(--text3); margin-top: 4px; }}
  .charts-grid {{ display: grid; grid-template-columns: 1fr; gap: 14px; }}
  .chart-card {{ background: var(--surface); border-radius: var(--radius); padding: 1.5rem; border: 1px solid var(--border); }}
  .chart-card h2 {{ font-size: 15px; font-weight: 500; margin-bottom: 6px; }}
  .legend {{ display: flex; gap: 18px; margin-bottom: 14px; font-size: 12px; color: var(--text2); flex-wrap: wrap; }}
  .legend span {{ display: flex; align-items: center; gap: 5px; }}
  .legend .dot {{ width: 8px; height: 8px; border-radius: 2px; display: inline-block; }}
  .legend .line-dot {{ width: 14px; height: 2px; border-radius: 1px; display: inline-block; }}
  .chart-wrap {{ position: relative; width: 100%; }}
  .chart-wrap.h280 {{ height: 280px; }}
  .chart-wrap.h260 {{ height: 260px; }}
  .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
  .footer {{ margin-top: 2rem; text-align: center; font-size: 11px; color: var(--text3); }}
  .footer a {{ color: var(--purple); text-decoration: none; }}
  @media (max-width: 768px) {{ body {{ padding: 1rem; }} .metrics {{ grid-template-columns: repeat(2, 1fr); }} .two-col {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<div class="header">
  <h1>GitHub活動ダッシュボード</h1>
  <span class="period">{period_str}</span>
  <span class="auto">⚡ 3時間ごと自動更新</span>
  <span class="updated">最終更新: {updated_at}</span>
</div>
<div class="metrics">
  <div class="metric">
    <div class="label">Commits</div>
    <div class="value">{total_commits:,}</div>
    <div class="sub">日平均 {total_commits/max(num_days,1):.1f} / {num_repos}リポジトリ</div>
  </div>
  <div class="metric">
    <div class="label">Pull Requests</div>
    <div class="value">{total_prs:,}</div>
    <div class="sub">日平均 {total_prs/max(num_days,1):.1f}</div>
  </div>
  <div class="metric">
    <div class="label">Issues</div>
    <div class="value">{total_issues:,}</div>
    <div class="sub">{sum(1 for d in dates if daily_issues.get(d,0) > 0)}日間にアクティビティ</div>
  </div>
  <div class="metric">
    <div class="label">Net Lines</div>
    <div class="value">+{total_net:,}</div>
    <div class="sub">追加 {total_add:,} / 削除 {total_del:,}</div>
  </div>
</div>
<div class="charts-grid">
  <div class="chart-card">
    <h2>コミット / PR / Issue（日別）</h2>
    <div class="legend">
      <span><span class="dot" style="background:#8b7cf7"></span>コミット</span>
      <span><span class="dot" style="background:#34d399"></span>PR</span>
      <span><span class="dot" style="background:#f97066"></span>Issue</span>
    </div>
    <div class="chart-wrap h280"><canvas id="c1"></canvas></div>
  </div>
  <div class="chart-card">
    <h2>コード増減（行数 / 日別）</h2>
    <div class="legend">
      <span><span class="dot" style="background:#34d399"></span>追加行</span>
      <span><span class="dot" style="background:#f97066"></span>削除行</span>
      <span><span class="line-dot" style="background:#8b7cf7"></span>純増（右軸）</span>
    </div>
    <div class="chart-wrap h280"><canvas id="c2"></canvas></div>
  </div>
  <div class="two-col">
    <div class="chart-card">
      <h2>リポジトリ別コミット数</h2>
      <div class="chart-wrap h260"><canvas id="c3"></canvas></div>
    </div>
    <div class="chart-card">
      <h2>日別累積コード純増</h2>
      <div class="chart-wrap h260"><canvas id="c4"></canvas></div>
    </div>
  </div>
</div>
<div class="footer">
  Powered by <a href="https://github.com/DaisukeHori/github-activity-dashboard">GitHub Actions</a> — auto-generated every 3 hours
</div>
<script>
Chart.defaults.font.family = "'Noto Sans JP', sans-serif";
Chart.defaults.color = '#6c6a78';
const L = {json.dumps(labels)};
const cm = {json.dumps(commits_arr)};
const pr = {json.dumps(prs_arr)};
const is_ = {json.dumps(issues_arr)};
const ad = {json.dumps(add_arr)};
const dl = {json.dumps(del_arr)};
const nt = ad.map((a,i) => a - dl[i]);
const cu = []; nt.reduce((a,v,i) => {{ cu[i]=a+v; return cu[i]; }}, 0);
const g = 'rgba(255,255,255,0.04)', t = '#6c6a78';
const tt = {{ backgroundColor:'#242736', titleColor:'#e8e6e3', bodyColor:'#9b99a5', borderColor:'rgba(255,255,255,0.08)', borderWidth:1 }};

new Chart(document.getElementById('c1'), {{
  type:'bar',
  data:{{ labels:L, datasets:[
    {{ label:'コミット', data:cm, backgroundColor:'#8b7cf7', borderRadius:4, barPercentage:.65, categoryPercentage:.8 }},
    {{ label:'PR', data:pr, backgroundColor:'#34d399', borderRadius:4, barPercentage:.65, categoryPercentage:.8 }},
    {{ label:'Issue', data:is_, backgroundColor:'#f97066', borderRadius:4, barPercentage:.65, categoryPercentage:.8 }}
  ]}},
  options:{{ responsive:true, maintainAspectRatio:false,
    plugins:{{ legend:{{display:false}}, tooltip:{{mode:'index', intersect:false, ...tt}} }},
    scales:{{ x:{{grid:{{display:false}}, ticks:{{color:t, font:{{size:11}}, autoSkip:false}}}}, y:{{grid:{{color:g}}, ticks:{{color:t, font:{{size:11}}}}, beginAtZero:true}} }}
  }}
}});

new Chart(document.getElementById('c2'), {{
  type:'bar',
  data:{{ labels:L, datasets:[
    {{ label:'追加', data:ad, backgroundColor:'rgba(52,211,153,0.7)', borderRadius:4, barPercentage:.55, categoryPercentage:.85, yAxisID:'y' }},
    {{ label:'削除', data:dl.map(d=>-d), backgroundColor:'rgba(249,112,102,0.7)', borderRadius:4, barPercentage:.55, categoryPercentage:.85, yAxisID:'y' }},
    {{ label:'純増', data:nt, type:'line', borderColor:'#8b7cf7', borderWidth:2.5, pointRadius:3, pointBackgroundColor:'#8b7cf7', fill:false, yAxisID:'y2', tension:.35 }}
  ]}},
  options:{{ responsive:true, maintainAspectRatio:false,
    plugins:{{ legend:{{display:false}},
      tooltip:{{ mode:'index', intersect:false, ...tt,
        callbacks:{{ label: ctx => {{ const v=ctx.raw; return ctx.dataset.label+': '+(v<0?'-':'+')+Math.abs(v).toLocaleString()+' 行'; }} }}
      }}
    }},
    scales:{{
      x:{{ grid:{{display:false}}, ticks:{{color:t, font:{{size:11}}, autoSkip:false}}, stacked:true }},
      y:{{ grid:{{color:g}}, ticks:{{color:t, font:{{size:11}}, callback:v=>(v<0?'-':'')+(Math.abs(v)/1000).toFixed(0)+'k'}}, stacked:true, position:'left' }},
      y2:{{ grid:{{display:false}}, ticks:{{color:'#8b7cf7', font:{{size:11}}, callback:v=>(v/1000).toFixed(0)+'k'}}, position:'right', beginAtZero:true }}
    }}
  }}
}});

new Chart(document.getElementById('c3'), {{
  type:'bar',
  data:{{ labels:{repo_names_js}, datasets:[{{ data:{repo_counts_js}, backgroundColor:{repo_colors_js}, borderRadius:4, barPercentage:.6 }}] }},
  options:{{ indexAxis:'y', responsive:true, maintainAspectRatio:false,
    plugins:{{ legend:{{display:false}}, tooltip:{{...tt, callbacks:{{label:ctx=>ctx.raw.toLocaleString()+' commits'}}}} }},
    scales:{{ x:{{grid:{{color:g}}, ticks:{{color:t, font:{{size:11}}}}, beginAtZero:true}}, y:{{grid:{{display:false}}, ticks:{{color:'#9b99a5', font:{{size:11}}}}}} }}
  }}
}});

new Chart(document.getElementById('c4'), {{
  type:'line',
  data:{{ labels:L, datasets:[{{
    label:'累積純増', data:cu, borderColor:'#60a5fa', borderWidth:2.5,
    pointRadius:3, pointBackgroundColor:'#60a5fa', fill:true,
    backgroundColor:'rgba(96,165,250,0.08)', tension:.3
  }}] }},
  options:{{ responsive:true, maintainAspectRatio:false,
    plugins:{{ legend:{{display:false}}, tooltip:{{...tt, callbacks:{{label:ctx=>'+'+ctx.raw.toLocaleString()+' 行'}}}} }},
    scales:{{ x:{{grid:{{display:false}}, ticks:{{color:t, font:{{size:11}}, autoSkip:false}}}}, y:{{grid:{{color:g}}, ticks:{{color:t, font:{{size:11}}, callback:v=>(v/1000).toFixed(0)+'k'}}, beginAtZero:true}} }}
  }}
}});
</script>
</body>
</html>"""
    return html


def main():
    now = datetime.now(JST)
    since_dt = now - timedelta(days=LOOKBACK_DAYS)
    since_iso = since_dt.strftime("%Y-%m-%dT00:00:00Z")
    since_date = since_dt.strftime("%Y-%m-%d")

    print(f"=== GitHub Activity Dashboard ===")
    print(f"Period: {since_date} ~ {now.strftime('%Y-%m-%d')}")
    print(f"Fetch time: {now.strftime('%Y-%m-%d %H:%M JST')}")

    # 1. Repos
    print("\n[1/5] Fetching repos...")
    repos = fetch_repos(since_iso)
    print(f"  Active repos: {len(repos)}")
    for r in repos:
        print(f"    {r['full_name']} (pushed: {r['pushed_at']})")

    # 2. Commits
    print("\n[2/5] Fetching commits...")
    daily_commits, repo_commits, sha_map = fetch_commits(repos, since_iso)
    print(f"  Total commits: {sum(daily_commits.values())}")

    # 3. PRs
    print("\n[3/5] Fetching PRs...")
    daily_prs = fetch_search("pr", since_date)
    print(f"  Total PRs: {sum(daily_prs.values())}")

    # 4. Issues
    print("\n[4/5] Fetching Issues...")
    daily_issues = fetch_search("issue", since_date)
    print(f"  Total Issues: {sum(daily_issues.values())}")

    # 5. Code stats
    print("\n[5/5] Fetching code stats...")
    daily_add, daily_del = fetch_code_stats(sha_map)
    total_net = sum(daily_add.values()) - sum(daily_del.values())
    print(f"  Net lines: +{total_net:,}")

    # Generate date range
    dates = []
    d = since_dt
    while d.date() <= now.date():
        dates.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)

    # Generate HTML
    updated_at = now.strftime("%Y/%m/%d %H:%M JST")
    html = generate_html(
        dates, daily_commits, daily_prs, daily_issues,
        daily_add, daily_del, repo_commits, updated_at
    )

    # Write output
    output_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "index.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n✅ Generated: {output_path}")

    # Also save raw data as JSON
    data_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data.json")
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump({
            "updated_at": updated_at,
            "period": {"start": since_date, "end": now.strftime("%Y-%m-%d")},
            "daily_commits": dict(daily_commits),
            "daily_prs": dict(daily_prs),
            "daily_issues": dict(daily_issues),
            "daily_additions": dict(daily_add),
            "daily_deletions": dict(daily_del),
            "repo_commits": dict(repo_commits),
        }, f, ensure_ascii=False, indent=2)
    print(f"✅ Generated: {data_path}")


if __name__ == "__main__":
    main()
