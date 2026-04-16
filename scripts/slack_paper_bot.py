import os
import json
import requests
import time
import xml.etree.ElementTree as ET

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_CHANNEL_ID = os.environ["SLACK_CHANNEL_ID"]

SLACK_POST_URL = "https://slack.com/api/chat.postMessage"
SLACK_HEADERS = {
    "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
    "Content-Type": "application/json",
}
NOTION_API_KEY = os.environ["NOTION_API_KEY"]
NOTION_PARENT_PAGE_ID = os.environ["NOTION_PARENT_PAGE_ID"]

POSTED_FILE = "data/posted_papers.json"
NOTION_DB_ID_FILE = "data/notion_database_id.txt"

KEYWORDS_FILE = "data/keywords.json"


def load_keywords() -> list[str]:
    if not os.path.exists(KEYWORDS_FILE):
        raise FileNotFoundError(f"{KEYWORDS_FILE} が見つかりません。リポジトリに追加してください。")
    with open(KEYWORDS_FILE) as f:
        return json.load(f)

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}


# ── 既投稿PMID管理 ──────────────────────────────────────────

def load_posted() -> set:
    if os.path.exists(POSTED_FILE):
        with open(POSTED_FILE) as f:
            return set(json.load(f))
    return set()


def save_posted(pmids: set) -> None:
    os.makedirs("data", exist_ok=True)
    with open(POSTED_FILE, "w") as f:
        json.dump(sorted(pmids), f, indent=2)


# ── Notionデータベース管理 ──────────────────────────────────

def load_notion_db_id() -> str | None:
    if os.path.exists(NOTION_DB_ID_FILE):
        with open(NOTION_DB_ID_FILE) as f:
            return f.read().strip() or None
    return None


def save_notion_db_id(db_id: str) -> None:
    os.makedirs("data", exist_ok=True)
    with open(NOTION_DB_ID_FILE, "w") as f:
        f.write(db_id)


def create_notion_database() -> str:
    payload = {
        "parent": {"type": "page_id", "page_id": NOTION_PARENT_PAGE_ID},
        "title": [{"type": "text", "text": {"content": "論文自動収集"}}],
        "properties": {
            "タイトル": {"title": {}},
            "著者": {"rich_text": {}},
            "検索キーワード": {"multi_select": {}},
            "DOI": {"url": {}},
            "PubMed": {"url": {}},
        },
    }
    r = requests.post(
        "https://api.notion.com/v1/databases",
        headers=NOTION_HEADERS,
        json=payload,
        timeout=15,
    )
    r.raise_for_status()
    db_id = r.json()["id"]
    print(f"Notionデータベースを作成しました: {db_id}")
    return db_id


def get_or_create_notion_db() -> str:
    db_id = load_notion_db_id()
    if db_id:
        return db_id
    db_id = create_notion_database()
    save_notion_db_id(db_id)
    return db_id


# ── PubMed API ──────────────────────────────────────────────

def search_pubmed(query: str, days: int = 2, retmax: int = 20) -> list[str]:
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    params = {
        "db": "pubmed",
        "term": query,
        "retmax": retmax,
        "retmode": "json",
        "sort": "date",
        "datetype": "pdat",
        "reldate": days,
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    return r.json()["esearchresult"]["idlist"]


def fetch_papers(pmids: list[str]) -> list[dict]:
    """efetch XML形式でメタデータ＋Abstractを一括取得する"""
    if not pmids:
        return []

    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    params = {"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"}
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()

    root = ET.fromstring(r.text)
    papers = []

    for article in root.findall(".//PubmedArticle"):
        # PMID
        pmid = article.findtext(".//PMID", "")

        # タイトル
        title_el = article.find(".//ArticleTitle")
        title = "".join(title_el.itertext()) if title_el is not None else "(タイトル不明)"

        # 著者（最大3名 + et al.）
        authors_list = []
        for author in article.findall(".//Author"):
            last = author.findtext("LastName", "")
            fore = author.findtext("ForeName", "")
            if last:
                authors_list.append(f"{last} {fore}".strip())
        authors = ", ".join(authors_list[:3])
        if len(authors_list) > 3:
            authors += " et al."

        # DOI
        doi = ""
        for id_el in article.findall(".//ArticleId"):
            if id_el.get("IdType") == "doi":
                doi = id_el.text or ""

        # Abstract（構造化Abstract対応：BACKGROUND: 等のラベルも含める）
        # itertext() でサブ要素（<sup>, <i> 等）内のテキストも結合する
        abstract_parts = article.findall(".//AbstractText")
        abstract = " ".join(
            ((el.get("Label", "") + ": ") if el.get("Label") else "") + "".join(el.itertext())
            for el in abstract_parts
        ).strip()
        if not abstract:
            abstract = "(Abstract取得できませんでした)"

        papers.append({
            "pmid": pmid,
            "title": title,
            "authors": authors or "(著者不明)",
            "doi": doi,
            "abstract": abstract,
        })

    return papers


# ── 通知・登録 ──────────────────────────────────────────────

def post_to_slack(paper: dict) -> None:
    """メインメッセージを投稿し、Abstractをスレッドに投稿する"""
    pubmed_url = f"https://pubmed.ncbi.nlm.nih.gov/{paper['pmid']}/"
    doi_text = f"DOI: {paper['doi']}  |  " if paper["doi"] else ""

    # ── メインメッセージ ──
    main_blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*<{pubmed_url}|{paper['title']}>*",
            },
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"👤 {paper['authors']}"},
            ],
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"{doi_text}<{pubmed_url}|PubMedで開く>"},
            ],
        },
        {"type": "divider"},
    ]

    r = requests.post(
        SLACK_POST_URL,
        headers=SLACK_HEADERS,
        json={"channel": SLACK_CHANNEL_ID, "blocks": main_blocks},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack API エラー: {data.get('error')}")

    # ── スレッドにAbstractを投稿 ──
    abstract = paper.get("abstract", "")
    if abstract and abstract != "(Abstract取得できませんでした)":
        thread_ts = data["ts"]
        requests.post(
            SLACK_POST_URL,
            headers=SLACK_HEADERS,
            json={
                "channel": SLACK_CHANNEL_ID,
                "thread_ts": thread_ts,
                "text": f"*Abstract*\n{abstract}",
            },
            timeout=10,
        ).raise_for_status()


def add_to_notion(db_id: str, paper: dict, keywords: list[str]) -> None:
    pubmed_url = f"https://pubmed.ncbi.nlm.nih.gov/{paper['pmid']}/"
    doi_url = f"https://doi.org/{paper['doi']}" if paper["doi"] else None

    abstract = paper.get("abstract", "")
    # Notion paragraph ブロックは2000文字上限
    abstract_chunks = [abstract[i:i+2000] for i in range(0, len(abstract), 2000)] if abstract else []

    payload = {
        "parent": {"database_id": db_id},
        "properties": {
            "タイトル": {
                "title": [{"text": {"content": paper["title"]}}]
            },
            "著者": {
                "rich_text": [{"text": {"content": paper["authors"]}}]
            },
            "検索キーワード": {
                "multi_select": [{"name": kw} for kw in keywords]
            },
            "DOI": {"url": doi_url},
            "PubMed": {"url": pubmed_url},
        },
        "children": [
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": "Abstract"}}]
                },
            },
            *[
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": chunk}}]
                    },
                }
                for chunk in abstract_chunks
            ],
        ],
    }
    r = requests.post(
        "https://api.notion.com/v1/pages",
        headers=NOTION_HEADERS,
        json=payload,
        timeout=15,
    )
    r.raise_for_status()


# ── メイン ──────────────────────────────────────────────────

def main() -> None:
    posted = load_posted()
    notion_db_id = get_or_create_notion_db()
    keywords = load_keywords()
    print(f"キーワード: {keywords}")

    pmid_to_keywords: dict[str, list[str]] = {}
    for keyword in keywords:
        print(f"検索中: {keyword}")
        pmids = search_pubmed(keyword)
        for pmid in pmids:
            pmid_to_keywords.setdefault(pmid, []).append(keyword)
        time.sleep(0.4)

    new_pmids = [p for p in pmid_to_keywords if p not in posted]
    print(f"新着: {len(new_pmids)} 件")

    if not new_pmids:
        print("通知対象の新着論文はありませんでした。")
        return

    # メタデータ＋Abstractを一括取得
    papers = fetch_papers(new_pmids)

    # ヘッダー通知
    requests.post(
        SLACK_POST_URL,
        headers=SLACK_HEADERS,
        json={"channel": SLACK_CHANNEL_ID, "text": f"📚 *論文自動収集Bot* — 新着 {len(papers)} 件"},
        timeout=10,
    ).raise_for_status()
    time.sleep(0.5)

    for paper in papers:
        post_to_slack(paper)
        time.sleep(0.5)

        keywords = pmid_to_keywords.get(paper["pmid"], [])
        add_to_notion(notion_db_id, paper, keywords)
        time.sleep(0.5)

        posted.add(paper["pmid"])
        print(f"  完了: {paper['pmid']} / {paper['title'][:50]}")

    save_posted(posted)
    print(f"{len(papers)} 件を通知・登録し、記録を更新しました。")


if __name__ == "__main__":
    main()
