# 論文自動収集Bot / Automated Paper Collection Bot

---

## 日本語

### 概要

PubMedのキーワード検索と指定論文の引用追跡を組み合わせ、新着論文をSlackに通知しNotionに自動登録するBotです。平日毎日10:00 JSTに自動実行されます。

---

### ファイル構成

```
.
├── scripts/
│   └── slack_paper_bot.py       # メインスクリプト
├── data/
│   ├── keywords.json            # 検索キーワード一覧
│   ├── watch_papers.json        # 引用追跡対象の論文一覧
│   ├── posted_papers.json       # 投稿済みPMID記録（自動更新）
│   └── notion_database_id.txt   # NotionデータベースID（自動生成）
└── .github/
    └── workflows/
        └── slack_paper_bot.yml  # GitHub Actionsワークフロー
```

---

### キーワードの追加・変更

`data/keywords.json` を編集してください。

```json
[
  "OsHRZ",
  "iron homeostasis rice",
  "追加したいキーワード"
]
```

- PubMedの検索構文（`AND`, `OR` など）が使えます
- 例: `"iron deficiency AND Arabidopsis"`
- キーワードを削除する場合は該当行を取り除くだけです
- **カンマの付け忘れに注意してください**（最後の要素にはカンマ不要）

---

### 引用追跡論文の追加・変更

`data/watch_papers.json` を編集してください。

```json
[
  {
    "title": "Kobayashi et al. 2013 Nat. Commun.",
    "doi": "10.1038/ncomms3792"
  },
  {
    "title": "追加したい論文の識別名",
    "doi": "10.xxxx/xxxx"
  }
]
```

- `title` は通知・Notionに表示される識別名です。自由に設定できます
- `doi` はDOI（`https://doi.org/` 以降の部分）を入力してください
- 論文のDOIはPubMedのページまたはジャーナルのサイトで確認できます

---

### GitHub Secretsの設定

| Secret名 | 内容 |
|---|---|
| `SLACK_BOT_TOKEN` | Slack Bot User OAuth Token（`xoxb-`で始まる） |
| `SLACK_CHANNEL_ID` | 投稿先チャンネルID（`C`で始まる） |
| `NOTION_API_KEY` | Notion Integration Token |
| `NOTION_PARENT_PAGE_ID` | データベースを作成する親ページのID |

---

### 手動実行

GitHubリポジトリの「Actions」タブ →「Slack Paper Bot」→「Run workflow」から手動実行できます。

---

### 実行スケジュールの変更

`.github/workflows/slack_paper_bot.yml` の `cron` を編集してください。

```yaml
- cron: '0 1 * * 1-5'  # 平日（月〜金）10:00 JST
```

---

### 検索期間の変更

`scripts/slack_paper_bot.py` の `search_pubmed()` 呼び出し部分の `days` を変更してください。

```python
pmids = search_pubmed(keyword, days=2)  # 直近2日（デフォルト）
```

---

&nbsp;

---

## English

### Overview

This bot automatically collects new papers via PubMed keyword search and citation tracking of specified papers. Results are posted to Slack and registered in Notion. It runs automatically every weekday at 10:00 JST.

---

### File Structure

```
.
├── scripts/
│   └── slack_paper_bot.py       # Main script
├── data/
│   ├── keywords.json            # Search keyword list
│   ├── watch_papers.json        # Papers to track citations for
│   ├── posted_papers.json       # Record of posted PMIDs (auto-updated)
│   └── notion_database_id.txt   # Notion database ID (auto-generated)
└── .github/
    └── workflows/
        └── slack_paper_bot.yml  # GitHub Actions workflow
```

---

### Adding or Changing Keywords

Edit `data/keywords.json`.

```json
[
  "OsHRZ",
  "iron homeostasis rice",
  "your new keyword here"
]
```

- PubMed search syntax is supported (`AND`, `OR`, etc.)
- Example: `"iron deficiency AND Arabidopsis"`
- To remove a keyword, simply delete the corresponding line
- **Make sure commas are placed correctly** (no comma after the last item)

---

### Adding or Changing Citation-Tracked Papers

Edit `data/watch_papers.json`.

```json
[
  {
    "title": "Kobayashi et al. 2013 Nat. Commun.",
    "doi": "10.1038/ncomms3792"
  },
  {
    "title": "Label for the paper you want to add",
    "doi": "10.xxxx/xxxx"
  }
]
```

- `title` is a display label shown in Slack notifications and Notion. You can set it freely.
- `doi` should be the DOI string only (the part after `https://doi.org/`)
- The DOI can be found on the paper's PubMed page or the journal website

---

### GitHub Secrets

| Secret | Description |
|---|---|
| `SLACK_BOT_TOKEN` | Slack Bot User OAuth Token (starts with `xoxb-`) |
| `SLACK_CHANNEL_ID` | Target channel ID (starts with `C`) |
| `NOTION_API_KEY` | Notion Integration Token |
| `NOTION_PARENT_PAGE_ID` | ID of the Notion parent page where the database will be created |

---

### Manual Execution

Go to the「Actions」tab in your GitHub repository →「Slack Paper Bot」→「Run workflow」.

---

### Changing the Schedule

Edit the `cron` expression in `.github/workflows/slack_paper_bot.yml`.

```yaml
- cron: '0 1 * * 1-5'  # Weekdays (Mon–Fri) at 10:00 JST
```

---

### Changing the Search Window

Edit the `days` argument in the `search_pubmed()` call in `scripts/slack_paper_bot.py`.

```python
pmids = search_pubmed(keyword, days=2)  # Last 2 days (default)
```
