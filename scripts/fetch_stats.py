#!/usr/bin/env python3
"""
GitHub Activity Dashboard v2 - "Today's Achievement" focused
"""

import os
import sys
import json
import time
import html as html_mod
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


def fetch_commits_detailed(repos, since_iso):
    daily = defaultdict(int)
    hourly_today = defaultdict(int)
    per_repo = defaultdict(int)
    sha_map = defaultdict(list)
    recent_commits = []

    now_jst = datetime.now(JST)
    today_str = now_jst.strftime("%Y-%m-%d")

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
                commit_dt_str = c["commit"]["author"]["date"]
                d = commit_dt_str[:10]
                daily[d] += 1
                per_repo[repo] += 1
                sha_map[repo].append((c["sha"], d))

                try:
                    commit_utc = datetime.fromisoformat(commit_dt_str.replace("Z", "+00:00"))
                    commit_jst = commit_utc.astimezone(JST)
                    if commit_jst.strftime("%Y-%m-%d") == today_str:
                        hourly_today[commit_jst.hour] += 1
                except:
                    pass

                if len(recent_commits) < 200:
                    msg = c["commit"]["message"].split("\n")[0][:80]
                    recent_commits.append({
                        "repo": short_name,
                        "message": msg,
                        "date": commit_dt_str,
                        "sha": c["sha"][:7],
                    })
            page += 1
            if len(commits) < 100:
                break

    recent_commits.sort(key=lambda x: x["date"], reverse=True)
    recent_commits = recent_commits[:20]
    return daily, hourly_today, per_repo, sha_map, recent_commits


def fetch_search(query_type, since_date):
    daily = defaultdict(int)
    page = 1
    while True:
        r = api_get("https://api.github.com/search/issues",
                     {"q": f"author:{USERNAME} type:{query_type} created:>={since_date}", "per_page": 100, "page": page})
        items = r.json().get("items", [])
        for item in items:
            daily[item["created_at"][:10]] += 1
        if len(items) < 100:
            break
        page += 1
    return daily


def fetch_code_stats(sha_map):
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
                   daily_add, daily_del, repo_commits, hourly_today,
                   recent_commits, updated_at, today_str):

    labels_js = json.dumps([f"{int(d[5:7])}/{int(d[8:10])}" for d in dates])
    commits_arr = [daily_commits.get(d, 0) for d in dates]
    prs_arr = [daily_prs.get(d, 0) for d in dates]
    issues_arr = [daily_issues.get(d, 0) for d in dates]
    add_arr = [daily_add.get(d, 0) for d in dates]
    del_arr = [daily_del.get(d, 0) for d in dates]
    net_arr = [a - d for a, d in zip(add_arr, del_arr)]

    total_commits = sum(commits_arr)
    total_prs = sum(prs_arr)
    total_issues = sum(issues_arr)
    total_add = sum(add_arr)
    total_del = sum(del_arr)
    total_net = total_add - total_del

    t_commits = daily_commits.get(today_str, 0)
    t_prs = daily_prs.get(today_str, 0)
    t_add = daily_add.get(today_str, 0)
    t_del = daily_del.get(today_str, 0)
    t_net = t_add - t_del

    streak = 0
    for d in reversed(dates):
        if daily_commits.get(d, 0) > 0:
            streak += 1
        else:
            break

    hourly_arr_js = json.dumps([hourly_today.get(h, 0) for h in range(24)])
    hourly_labels_js = json.dumps([f"{h:02d}" for h in range(24)])

    sorted_repos = sorted(repo_commits.items(), key=lambda x: -x[1])
    active_repos = [(r, c) for r, c in sorted_repos if c > 0]
    repo_names_js = json.dumps([r.split("/")[-1] for r, _ in active_repos])
    repo_counts_js = json.dumps([c for _, c in active_repos])
    num_repos = len(active_repos)
    palette = ["#8b7cf7","#34d399","#fbbf24","#f97066","#60a5fa","#c084fc","#fb923c","#a78bfa","#38bdf8","#4ade80"]
    while len(palette) < num_repos:
        palette.append("#9b99a5")
    repo_colors_js = json.dumps(palette[:num_repos])

    commits_html = ""
    for c in recent_commits:
        try:
            dt = datetime.fromisoformat(c["date"].replace("Z", "+00:00")).astimezone(JST)
            time_s = dt.strftime("%H:%M")
            date_d = dt.strftime("%m/%d")
        except:
            time_s = ""
            date_d = ""
        msg = html_mod.escape(c["message"])
        repo = html_mod.escape(c["repo"])
        commits_html += f'<div class="commit-item"><span class="commit-time">{date_d} {time_s}</span><span class="commit-repo">{repo}</span><span class="commit-msg">{msg}</span><span class="commit-sha">{c["sha"]}</span></div>\n'

    num_days = len(dates)
    period_start = dates[0]
    period_end = dates[-1]
    period_str = f"{period_start[0:4]}/{int(period_start[5:7]):02d}/{int(period_start[8:10]):02d} 〜 {int(period_end[5:7]):02d}/{int(period_end[8:10]):02d}（{num_days}日間）"

    peak_day = max(dates, key=lambda d: daily_commits.get(d, 0))
    peak_count = daily_commits.get(peak_day, 0)
    peak_display = f"{int(peak_day[5:7])}/{int(peak_day[8:10])}"

    cum = []
    acc = 0
    for n in net_arr:
        acc += n
        cum.append(acc)
    cum_js = json.dumps(cum)

    weekdays_jp = ['月','火','水','木','金','土','日']
    now_jst = datetime.now(JST)
    wd = weekdays_jp[now_jst.weekday()]

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GitHub活動ダッシュボード - DaisukeHori</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🔥</text></svg>">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
:root{{--bg:#09090b;--surface:#18181b;--surface2:#27272a;--border:rgba(255,255,255,0.06);--text:#fafafa;--text2:#a1a1aa;--text3:#71717a;--purple:#8b7cf7;--green:#22c55e;--coral:#ef4444;--blue:#3b82f6;--amber:#f59e0b;--cyan:#06b6d4;--radius:16px;--radius-sm:10px}}
body{{font-family:'Noto Sans JP',sans-serif;background:var(--bg);color:var(--text);min-height:100vh}}
.container{{max-width:1200px;margin:0 auto;padding:2rem}}
.hero{{background:linear-gradient(135deg,rgba(139,124,247,0.08),rgba(34,197,94,0.06));border:1px solid var(--border);border-radius:20px;padding:2.5rem;margin-bottom:1.5rem;position:relative;overflow:hidden}}
.hero::before{{content:'';position:absolute;top:-50%;right:-20%;width:400px;height:400px;background:radial-gradient(circle,rgba(139,124,247,0.08),transparent 70%);pointer-events:none}}
.hero-top{{display:flex;justify-content:space-between;align-items:center;margin-bottom:1.5rem;flex-wrap:wrap;gap:12px}}
.hero-label{{font-size:13px;color:var(--text3);letter-spacing:2px;text-transform:uppercase}}
.hero-date{{font-size:15px;font-weight:500;color:var(--text2)}}
.hero-badge{{display:flex;align-items:center;gap:8px}}
.badge{{font-size:11px;padding:4px 10px;border-radius:20px;font-weight:500}}
.badge-auto{{background:rgba(34,197,94,0.15);color:var(--green)}}
.badge-streak{{background:rgba(245,158,11,0.15);color:var(--amber)}}
.hero-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:20px}}
.hero-stat{{text-align:center}}
.hero-stat .num{{font-family:'JetBrains Mono',monospace;font-size:48px;font-weight:700;line-height:1;margin-bottom:4px;background:linear-gradient(135deg,var(--purple),var(--cyan));-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}}
.hero-stat:nth-child(2) .num{{background:linear-gradient(135deg,var(--green),var(--cyan));-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}}
.hero-stat:nth-child(3) .num{{background:linear-gradient(135deg,var(--amber),var(--coral));-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}}
.hero-stat:nth-child(4) .num{{background:linear-gradient(135deg,var(--blue),var(--cyan));-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}}
.hero-stat .label{{font-size:12px;color:var(--text3);text-transform:uppercase;letter-spacing:1px}}
.grid-2{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}}
.grid-3{{display:grid;grid-template-columns:2fr 1fr;gap:14px;margin-bottom:14px}}
.card{{background:var(--surface);border-radius:var(--radius);padding:1.5rem;border:1px solid var(--border)}}
.card h2{{font-size:14px;font-weight:500;color:var(--text2);margin-bottom:12px;display:flex;align-items:center;gap:8px}}
.card h2 .icon{{font-size:16px}}
.chart-wrap{{position:relative;width:100%}}
.h180{{height:180px}}.h220{{height:220px}}.h260{{height:260px}}
.summary-bar{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:14px}}
.summary-item{{background:var(--surface);border-radius:var(--radius-sm);padding:1rem;border:1px solid var(--border);text-align:center}}
.summary-item .val{{font-family:'JetBrains Mono',monospace;font-size:22px;font-weight:600}}
.summary-item .lbl{{font-size:11px;color:var(--text3);margin-top:2px}}
.c-purple{{color:var(--purple)}}.c-green{{color:var(--green)}}.c-coral{{color:var(--coral)}}.c-blue{{color:var(--blue)}}
.commit-list{{max-height:320px;overflow-y:auto;scrollbar-width:thin;scrollbar-color:var(--surface2) transparent}}
.commit-item{{display:grid;grid-template-columns:80px 100px 1fr 50px;gap:8px;padding:8px 0;border-bottom:1px solid var(--border);align-items:center;font-size:12px}}
.commit-item:last-child{{border-bottom:none}}
.commit-time{{color:var(--text3);font-family:'JetBrains Mono',monospace;font-size:11px}}
.commit-repo{{color:var(--purple);font-size:11px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.commit-msg{{color:var(--text2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.commit-sha{{color:var(--text3);font-family:'JetBrains Mono',monospace;font-size:10px;text-align:right}}
.legend{{display:flex;gap:16px;margin-bottom:10px;font-size:11px;color:var(--text3);flex-wrap:wrap}}
.legend span{{display:flex;align-items:center;gap:4px}}
.dot{{width:8px;height:8px;border-radius:2px;display:inline-block}}
.line-dot{{width:12px;height:2px;border-radius:1px;display:inline-block}}
.footer{{text-align:center;padding:2rem 0 1rem;font-size:11px;color:var(--text3)}}
.footer a{{color:var(--purple);text-decoration:none}}
.num{{animation:fadeUp .6s ease-out both}}
@keyframes fadeUp{{from{{opacity:0;transform:translateY(12px)}}to{{opacity:1;transform:translateY(0)}}}}
.hero-stat:nth-child(2) .num{{animation-delay:.1s}}
.hero-stat:nth-child(3) .num{{animation-delay:.2s}}
.hero-stat:nth-child(4) .num{{animation-delay:.3s}}
@media(max-width:768px){{.container{{padding:1rem}}.hero-grid{{grid-template-columns:repeat(2,1fr);gap:12px}}.hero-stat .num{{font-size:36px}}.grid-2,.grid-3,.summary-bar{{grid-template-columns:1fr}}.commit-item{{grid-template-columns:70px 80px 1fr 40px}}}}
</style>
</head>
<body>
<div class="container">
  <div class="hero">
    <div class="hero-top">
      <div>
        <div class="hero-label">Today's Achievement</div>
        <div class="hero-date">\U0001f4c5 {today_str[0:4]}/{int(today_str[5:7])}/{int(today_str[8:10])}（{wd}）</div>
      </div>
      <div class="hero-badge">
        <span class="badge badge-streak">\U0001f525 {streak}日連続</span>
        <span class="badge badge-auto">\u26a1 3h自動更新</span>
      </div>
    </div>
    <div class="hero-grid">
      <div class="hero-stat"><div class="num">{t_commits}</div><div class="label">Commits</div></div>
      <div class="hero-stat"><div class="num">{t_prs}</div><div class="label">Pull Requests</div></div>
      <div class="hero-stat"><div class="num">+{t_net:,}</div><div class="label">Net Lines</div></div>
      <div class="hero-stat"><div class="num">{num_repos}</div><div class="label">Active Repos</div></div>
    </div>
  </div>

  <div class="grid-3">
    <div class="card">
      <h2><span class="icon">\u23f0</span> 今日の時間帯別コミット</h2>
      <div class="chart-wrap h180"><canvas id="hourly"></canvas></div>
    </div>
    <div class="card">
      <h2><span class="icon">\U0001f4cb</span> 直近コミットログ</h2>
      <div class="commit-list">{commits_html}</div>
    </div>
  </div>

  <div style="font-size:13px;color:var(--text3);margin-bottom:8px;display:flex;justify-content:space-between;align-items:center">
    <span>\U0001f4ca {period_str}</span>
    <span>ピーク: {peak_display}（{peak_count}コミット）</span>
  </div>
  <div class="summary-bar">
    <div class="summary-item"><div class="val c-purple">{total_commits:,}</div><div class="lbl">Total Commits</div></div>
    <div class="summary-item"><div class="val c-green">{total_prs:,}</div><div class="lbl">Total PRs</div></div>
    <div class="summary-item"><div class="val c-coral">{total_issues:,}</div><div class="lbl">Total Issues</div></div>
    <div class="summary-item"><div class="val c-blue">+{total_net:,}</div><div class="lbl">Net Lines</div></div>
  </div>

  <div class="grid-2">
    <div class="card">
      <h2><span class="icon">\U0001f4c8</span> コミット / PR / Issue</h2>
      <div class="legend"><span><span class="dot" style="background:#8b7cf7"></span>コミット</span><span><span class="dot" style="background:#34d399"></span>PR</span><span><span class="dot" style="background:#ef4444"></span>Issue</span></div>
      <div class="chart-wrap h220"><canvas id="c1"></canvas></div>
    </div>
    <div class="card">
      <h2><span class="icon">\U0001f4bb</span> コード増減</h2>
      <div class="legend"><span><span class="dot" style="background:#22c55e"></span>追加</span><span><span class="dot" style="background:#ef4444"></span>削除</span><span><span class="line-dot" style="background:#8b7cf7"></span>純増</span></div>
      <div class="chart-wrap h220"><canvas id="c2"></canvas></div>
    </div>
  </div>
  <div class="grid-2">
    <div class="card">
      <h2><span class="icon">\U0001f4e6</span> リポジトリ別</h2>
      <div class="chart-wrap h220"><canvas id="c3"></canvas></div>
    </div>
    <div class="card">
      <h2><span class="icon">\U0001f680</span> 累積コード純増</h2>
      <div class="chart-wrap h220"><canvas id="c4"></canvas></div>
    </div>
  </div>

  <div class="footer">最終更新: {updated_at}　|　Powered by <a href="https://github.com/DaisukeHori/github-activity-dashboard">GitHub Actions</a></div>
</div>

<script>
Chart.defaults.font.family="'Noto Sans JP',sans-serif";
Chart.defaults.color='#71717a';
const g='rgba(255,255,255,0.04)',tc='#52525b';
const tt={{backgroundColor:'#27272a',titleColor:'#fafafa',bodyColor:'#a1a1aa',borderColor:'rgba(255,255,255,0.08)',borderWidth:1,padding:10,cornerRadius:8}};

const hArr={hourly_arr_js};
new Chart(document.getElementById('hourly'),{{type:'bar',data:{{labels:{hourly_labels_js},datasets:[{{data:hArr,backgroundColor:function(ctx){{const v=ctx.raw;const m=Math.max(...hArr);return v===m?'#8b7cf7':v>0?'rgba(139,124,247,0.4)':'rgba(139,124,247,0.08)'}},borderRadius:4,barPercentage:.7}}]}},options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}},tooltip:{{...tt,callbacks:{{label:c=>c.raw+' commits'}}}}}},scales:{{x:{{grid:{{display:false}},ticks:{{color:tc,font:{{size:9}}}}}},y:{{display:false,beginAtZero:true}}}}}}}});

const L={labels_js};
const cm={json.dumps(commits_arr)};const pr={json.dumps(prs_arr)};const is_={json.dumps(issues_arr)};
new Chart(document.getElementById('c1'),{{type:'bar',data:{{labels:L,datasets:[{{label:'コミット',data:cm,backgroundColor:'#8b7cf7',borderRadius:4,barPercentage:.6,categoryPercentage:.8}},{{label:'PR',data:pr,backgroundColor:'#34d399',borderRadius:4,barPercentage:.6,categoryPercentage:.8}},{{label:'Issue',data:is_,backgroundColor:'#ef4444',borderRadius:4,barPercentage:.6,categoryPercentage:.8}}]}},options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}},tooltip:{{mode:'index',intersect:false,...tt}}}},scales:{{x:{{grid:{{display:false}},ticks:{{color:tc,font:{{size:10}},autoSkip:false}}}},y:{{grid:{{color:g}},ticks:{{color:tc,font:{{size:10}}}},beginAtZero:true}}}}}}}});

const ad={json.dumps(add_arr)};const dl={json.dumps(del_arr)};const nt=ad.map((a,i)=>a-dl[i]);
new Chart(document.getElementById('c2'),{{type:'bar',data:{{labels:L,datasets:[{{label:'追加',data:ad,backgroundColor:'rgba(34,197,94,0.6)',borderRadius:4,barPercentage:.5,categoryPercentage:.85,yAxisID:'y'}},{{label:'削除',data:dl.map(d=>-d),backgroundColor:'rgba(239,68,68,0.6)',borderRadius:4,barPercentage:.5,categoryPercentage:.85,yAxisID:'y'}},{{label:'純増',data:nt,type:'line',borderColor:'#8b7cf7',borderWidth:2,pointRadius:2.5,pointBackgroundColor:'#8b7cf7',fill:false,yAxisID:'y2',tension:.35}}]}},options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}},tooltip:{{mode:'index',intersect:false,...tt,callbacks:{{label:c=>{{const v=c.raw;return c.dataset.label+': '+(v<0?'-':'+')+Math.abs(v).toLocaleString()+' 行'}}}}}}}},scales:{{x:{{grid:{{display:false}},ticks:{{color:tc,font:{{size:10}},autoSkip:false}},stacked:true}},y:{{grid:{{color:g}},ticks:{{color:tc,font:{{size:10}},callback:v=>(v<0?'-':'')+(Math.abs(v)/1000).toFixed(0)+'k'}},stacked:true,position:'left'}},y2:{{grid:{{display:false}},ticks:{{color:'#8b7cf7',font:{{size:10}},callback:v=>(v/1000).toFixed(0)+'k'}},position:'right',beginAtZero:true}}}}}}}});

new Chart(document.getElementById('c3'),{{type:'bar',data:{{labels:{repo_names_js},datasets:[{{data:{repo_counts_js},backgroundColor:{repo_colors_js},borderRadius:4,barPercentage:.55}}]}},options:{{indexAxis:'y',responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}},tooltip:{{...tt,callbacks:{{label:c=>c.raw.toLocaleString()+' commits'}}}}}},scales:{{x:{{grid:{{color:g}},ticks:{{color:tc,font:{{size:10}}}},beginAtZero:true}},y:{{grid:{{display:false}},ticks:{{color:'#a1a1aa',font:{{size:10}}}}}}}}}}}});

const cu={cum_js};
new Chart(document.getElementById('c4'),{{type:'line',data:{{labels:L,datasets:[{{label:'累積純増',data:cu,borderColor:'#3b82f6',borderWidth:2.5,pointRadius:2.5,pointBackgroundColor:'#3b82f6',fill:true,backgroundColor:function(ctx){{const c=ctx.chart.ctx;const gr=c.createLinearGradient(0,0,0,ctx.chart.height);gr.addColorStop(0,'rgba(59,130,246,0.15)');gr.addColorStop(1,'rgba(59,130,246,0)');return gr}},tension:.3}}]}},options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}},tooltip:{{...tt,callbacks:{{label:c=>'+'+c.raw.toLocaleString()+' 行'}}}}}},scales:{{x:{{grid:{{display:false}},ticks:{{color:tc,font:{{size:10}},autoSkip:false}}}},y:{{grid:{{color:g}},ticks:{{color:tc,font:{{size:10}},callback:v=>(v/1000).toFixed(0)+'k'}},beginAtZero:true}}}}}}}});
</script>
</body>
</html>"""


def main():
    now = datetime.now(JST)
    since_dt = now - timedelta(days=LOOKBACK_DAYS)
    since_iso = since_dt.strftime("%Y-%m-%dT00:00:00Z")
    since_date = since_dt.strftime("%Y-%m-%d")
    today_str = now.strftime("%Y-%m-%d")

    print(f"=== GitHub Activity Dashboard v2 ===")
    print(f"Period: {since_date} ~ {today_str}")
    print(f"Fetch time: {now.strftime('%Y-%m-%d %H:%M JST')}")

    print("\n[1/5] Fetching repos...")
    repos = fetch_repos(since_iso)
    print(f"  Active repos: {len(repos)}")

    print("\n[2/5] Fetching commits (detailed)...")
    daily_commits, hourly_today, repo_commits, sha_map, recent_commits = \
        fetch_commits_detailed(repos, since_iso)
    print(f"  Total commits: {sum(daily_commits.values())}")
    print(f"  Today commits: {daily_commits.get(today_str, 0)}")

    print("\n[3/5] Fetching PRs...")
    daily_prs = fetch_search("pr", since_date)
    print(f"  Total PRs: {sum(daily_prs.values())}")

    print("\n[4/5] Fetching Issues...")
    daily_issues = fetch_search("issue", since_date)
    print(f"  Total Issues: {sum(daily_issues.values())}")

    print("\n[5/5] Fetching code stats...")
    daily_add, daily_del = fetch_code_stats(sha_map)
    print(f"  Net lines: +{sum(daily_add.values()) - sum(daily_del.values()):,}")

    dates = []
    d = since_dt
    while d.date() <= now.date():
        dates.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)

    updated_at = now.strftime("%Y/%m/%d %H:%M JST")
    html = generate_html(dates, daily_commits, daily_prs, daily_issues,
                          daily_add, daily_del, repo_commits, hourly_today,
                          recent_commits, updated_at, today_str)

    output_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(output_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n✅ Generated: index.html")

    with open(os.path.join(output_dir, "data.json"), "w", encoding="utf-8") as f:
        json.dump({
            "updated_at": updated_at,
            "today": today_str,
            "period": {"start": since_date, "end": today_str},
            "daily_commits": dict(daily_commits),
            "daily_prs": dict(daily_prs),
            "daily_issues": dict(daily_issues),
            "daily_additions": dict(daily_add),
            "daily_deletions": dict(daily_del),
            "repo_commits": dict(repo_commits),
            "hourly_today": dict(hourly_today),
        }, f, ensure_ascii=False, indent=2)
    print(f"✅ Generated: data.json")


if __name__ == "__main__":
    main()
