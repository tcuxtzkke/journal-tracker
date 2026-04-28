# journal-tracker
# Journal Tracker 構成まとめ

最終更新: 2026-04-28

---

## 全体構成

```
cron-job.org（毎朝8:00 JST）
　↓ GitHub Actions API をトリガー
GitHub Actions（journal-tracker リポジトリ）
　↓ tracker.py を実行
OpenAlex API　→　論文データ取得（無料）
Claude API　　→　英語・日本語要約生成（有料）
　↓
GitHubリポジトリに data/papers.db・output/report.html を自動コミット
　↓ 新着論文があった場合のみ
Slack通知（レポートリンク付き）
```

---

## 各サービスのURL・管理画面

| サービス | URL |
|---|---|
| GitHubリポジトリ | https://github.com/tcuxtzkke/journal-tracker |
| cron-job.org | https://console.cron-job.org/dashboard |
| Anthropic Console（APIキー・課金） | https://console.anthropic.com |
| Slack Webhook管理 | https://api.slack.com/apps |

---

## ファイル構成（GitHubリポジトリ）

```
journal-tracker/
├── tracker.py                      # メインスクリプト
├── config.json                     # キーワード・設定
├── requirements.txt                # Pythonライブラリ
├── data/
│   └── papers.db                   # 論文DB（自動蓄積）
├── output/
│   └── report.html                 # ダッシュボード（自動更新）
└── .github/
    └── workflows/
        └── tracker.yml             # GitHub Actions設定
```

---

## GitHub Secrets（Settings → Secrets and variables → Actions）

| Secret名 | 内容 |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic APIキー（sk-ant-...） |
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook URL |

---

## cron-job.org 設定

| 項目 | 値 |
|---|---|
| URL | `https://api.github.com/repos/tcuxtzkke/journal-tracker/actions/workflows/tracker.yml/dispatches` |
| 実行時刻 | 毎日 8:00 AM（Asia/Tokyo） |
| Request method | POST |
| Header: Authorization | `token ghp_...`（GitHub Personal Access Token） |
| Header: Accept | `application/vnd.github.v3+json` |
| Header: Content-Type | `application/json` |
| Request body | `{"ref":"main"}` |

---

## GitHub Actions（tracker.yml）

- **トリガー**: cron-job.orgからのworkflow_dispatch / 手動実行
- **実行環境**: ubuntu-latest / Python 3.11
- **実行後**: papers.db と report.html を自動コミット・プッシュ

---

## config.json（キーワード変更方法）

GitHubで `config.json` を直接編集してコミットするだけで反映されます。

```json
{
  "keywords": [
    "AI",
    "artificial intelligence",
    "shared consumption",
    "joint consumption"
  ],
  "slack_webhook_url": "",
  "fetch_days": 90,
  "fetch_days_after": 14
}
```

※ `slack_webhook_url` は空のままでOK（Secretsで管理）
※ `fetch_days_after` は2回目以降の取得期間（日数）

---

## 対象ジャーナル

| 略称 | ジャーナル名 | ISSN |
|---|---|---|
| JCR | Journal of Consumer Research | 0093-5301 |
| JMR | Journal of Marketing Research | 0022-2437 |
| JM | Journal of Marketing | 0022-2429 |
| JCP | Journal of Consumer Psychology | 1057-7408 |
| P&M | Psychology & Marketing | 0742-6046 |

---

## レポートの見方

- **🆕 最新追加タブ**: 今回の実行で新たに追加された論文
- **🔍 キーワードタブ**: config.jsonのキーワードにマッチした論文
- **JCR / JMR / JM / JCP / P&M タブ**: 雑誌別に全論文を表示

---

## よくある操作

### キーワードを追加・変更したい
→ GitHubで `config.json` を編集してコミット

### 手動で今すぐ実行したい
→ GitHubリポジトリの「Actions」→「Journal Tracker」→「Run workflow」

### 実行時刻を変えたい
→ cron-job.orgの「EDIT」からスケジュールを変更

### 最新レポートをMacで見たい
```bash
cd ~/journal_tracker
git pull
open output/report.html
```

### 実行ログを確認したい
→ GitHubリポジトリの「Actions」タブで履歴を確認

### APIの使用料金を確認したい
→ https://console.anthropic.com の「Billing」

---

## コスト目安

| 項目 | コスト |
|---|---|
| OpenAlex API | 無料 |
| cron-job.org | 無料 |
| GitHub Actions | 無料（月2,000分まで） |
| Claude API（新着なし） | $0 |
| Claude API（新着10〜20本） | 約$0.1〜0.3 |
| Claude API（初回・100本） | 約$1〜2 |
