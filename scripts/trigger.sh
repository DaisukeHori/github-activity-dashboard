#!/bin/bash
# 外部トリガースクリプト (LXC等のcrontabから5分ごとに実行)
# 使い方:
#   export GH_PAT="your_token_here"
#   crontab: */5 * * * * GH_PAT=your_token /path/to/trigger.sh
curl -s -X POST \
  -H "Authorization: token ${GH_PAT}" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/DaisukeHori/github-activity-dashboard/actions/workflows/update.yml/dispatches \
  -d '{"ref":"main"}' > /dev/null 2>&1
