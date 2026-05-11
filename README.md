# 📊 GitHub Activity Dashboard

GitHub活動ダッシュボード — 直近14日間のコミット・PR・Issue・コード増減を自動集計し、GitHub Pagesで公開します。

## 🔗 Live Dashboard

**[https://daisukehori.github.io/github-activity-dashboard/](https://daisukehori.github.io/github-activity-dashboard/)**

## ⚡ 自動更新

GitHub Actionsにより **3時間ごと** に自動で再集計・更新されます。

## 📈 表示内容

- **コミット数**: 日別・リポジトリ別
- **PR数**: 日別
- **Issue数**: 日別
- **コード増減**: 追加行・削除行・純増（日別 & 累積）

## 🛠 仕組み

1. `scripts/fetch_stats.py` が GitHub API からデータを取得
2. 静的な `index.html` を生成
3. GitHub Actions がコミット＆プッシュ
4. GitHub Pages で自動公開
