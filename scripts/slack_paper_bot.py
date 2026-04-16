import os
import json
import requests
import time

SLACK_WEBHOOK_URL = os.environ["SLACK_WEBHOOK_URL"]
NOTION_API_KEY = os.environ["NOTION_API_KEY"]
NOTION_PARENT_PAGE_ID = os.environ["NOTION_PARENT_PAGE_ID"]

POSTED_FILE = "data/posted_papers.json"
NOTION_DB_ID_FILE = "data/notion_database_id.txt"

KEYWORDS = [
    "OsHRZ",
    "phytosiderophore",
    "iron homeostasis rice",
    "zinc transporter rice",
    "metal homeostasis rice",
]

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
    """親ページ配下に論文収集データベースを新規作成し、IDを返す"""
    payload = {
        "parent": {"type": "page_id", "page_id": NOTION_PARENT_PAGE_ID},
        "title": [{"type": "text", "text": {"content": "論文自動収集"}}],
        "properties": {
            "タイトル": {"title": {}},
            "著者": {"rich_text": {}},
            "Abstract": {"rich_text": {}},
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


def fetch_details(pmids: list[str]) -> list[dict]:
    if not pmids:
        return []
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
    params = {"db": "pubmed", "id": ",".join(pmids), "retmode": "json"}
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    result = r.json()["result"]

    papers = []
    for pmid in pmids:
        item = result.get(pmid)
        if not item or item.get("error"):
            continue

        raw_authors = item.get("authors", [])
        author_names = [a["name"] for a in raw_authors[:3]]
        authors = ", ".join(author_names)
        if len(raw_authors) > 3:
            authors += " et al."

        doi_raw = item.get("elocationid", "")
        doi = doi_raw.replace("doi: ", "").strip() if doi_raw.startswith("doi:") else ""

        papers.append({
            "pmid": pmid,
            "title": item.get("title", "(タイトル不明)"),
            "authors": authors or "(著者不明)",
            "doi": doi,
        })

    return papers


def fetch_abstract(pmid: str) -> str:
    """efetch APIでAbstractを取得する"""
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    params = {"db": "pubmed", "id": pmid, "retmode": "text", "rettype": "abstract"}
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()

    text = r.text
    marker = "Abstract\n"
    idx = text.find(marker)
    if idx != -1:
        abstract = text[idx + len(marker):].strip()
        for stop in ["\nCopyright", "\n©", "\nPMID:"]:
            stop_idx = abstract.find(stop)
            if stop_idx != -1:
                abstract = abstract[:stop_idx].strip()
        return abstract

    return "(Abstract取得できませんでした)"


# ── 通知・登録 ──────────────────────────────────────────────

def post_to_slack(paper: dict) -> None:
    pubmed_url = f"https://pubmed.ncbi.nlm.nih.gov/{paper['pmid']}/"
    doi_line = f"\n🔗 DOI: `{paper['doi']}`" if paper["doi"] else ""

    text = (
        f"*{paper['title']}*\n"
        f"👤 {paper['authors']}"
        f"{doi_line}\n"
        f"🔬 <{pubmed_url}|PubMedで開く>"
    )
    r = requests.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=10)
    r.raise_for_status()


def add_to_notion(db_id: str, paper: dict, keywords: list[str]) -> None:
    pubmed_url = f"https://pubmed.ncbi.nlm.nih.gov/{paper['pmid']}/"
    doi_url = f"https://doi.org/{paper['doi']}" if paper["doi"] else None

    # Abstract は2000文字を上限とする（Notion rich_text の制限対策）
    abstract = paper.get("abstract", "")[:2000]

    payload = {
        "parent": {"database_id": db_id},
        "properties": {
            "タイトル": {
                "title": [{"text": {"content": paper["title"]}}]
            },
            "著者": {
                "rich_text": [{"text": {"content": paper["authors"]}}]
            },
            "Abstract": {
                "rich_text": [{"text": {"content": abstract}}]
            },
            "検索キーワード": {
                "multi_select": [{"name": kw} for kw in keywords]
            },
            "DOI": {"url": doi_url},
            "PubMed": {"url": pubmed_url},
        },
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

    # キーワードごとに検索し、PMIDとヒットキーワードを紐付け
    pmid_to_keywords: dict[str, list[str]] = {}
    for keyword in KEYWORDS:
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

    papers = fetch_details(new_pmids)

    # ヘッダー通知
    requests.post(
        SLACK_WEBHOOK_URL,
        json={"text": f"📚 *論文自動収集Bot* — 新着 {len(papers)} 件"},
        timeout=10,
    )
    time.sleep(0.5)

    for paper in papers:
        # Abstract取得
        paper["abstract"] = fetch_abstract(paper["pmid"])
        time.sleep(0.4)

        # Slack通知
        post_to_slack(paper)
        time.sleep(0.5)

        # Notion登録
        keywords = pmid_to_keywords.get(paper["pmid"], [])
        add_to_notion(notion_db_id, paper, keywords)
        time.sleep(0.5)

        posted.add(paper["pmid"])
        print(f"  完了: {paper['pmid']} / {paper['title'][:50]}")

    save_posted(posted)
    print(f"{len(papers)} 件を通知・登録し、記録を更新しました。")


if __name__ == "__main__":
    main()
