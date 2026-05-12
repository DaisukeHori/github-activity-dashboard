# 🔥 GitHub Activity Dashboard

GitHub活動ダッシュボード — 直近14日間のコミット・PR・Issue・コード増減を自動集計し、GitHub Pagesで公開します。

## 🔗 Live Dashboard

**[https://daisukehori.github.io/github-activity-dashboard/](https://daisukehori.github.io/github-activity-dashboard/)**

## ⚡ 自動更新

GitHub Actionsにより **3時間ごと** に自動で再集計・更新されます。

## 🌐 タイムゾーン対応

クライアント側でタイムゾーンを自由に切り替え可能。「今日」の定義、時間帯別チャート、コミットログの時刻表示がすべて選択したタイムゾーンに連動します。

- JST / UTC / EST / PST / CST / CET プリセット
- カスタム UTC±N 入力対応
- 設定は localStorage に保存

## 📈 表示内容

- **Today's Achievement** — 今日のコミット・PR・コード純増を大きく表示
- **時間帯別コミットヒートマップ** — 今日の0〜23時
- **直近コミットログ** — 最新20件
- **14日間サマリー** — コミット / PR / Issue / コード増減
- **リポジトリ別コミット数**
- **累積コード純増グラフ**

## 🛠 仕組み

1. `scripts/fetch_stats.py` が GitHub API から全コミットのUTC生データを取得
2. `data.json` に出力（タイムゾーン非依存）
3. `index.html`（静的）がクライアント側で集計・描画
4. GitHub Actions が3時間ごとに data.json を更新
