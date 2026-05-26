#!/usr/bin/env python3
"""
gene_extractor.py
Phase 3: 新規論文から Claude API で遺伝子情報を自動抽出し
Gene/Protein DB・Evidence Table を Notion に書き込む。

使い方:
  python gene_extractor.py           # 未処理論文を最大 BATCH_SIZE 件処理
  python gene_extractor.py --dry-run # 書き込まずに抽出内容だけ確認
"""

import os, json, time, re, sys, argparse, logging
import requests
from notion_client import Client

# ── ログ設定 ───────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── 設定 ──────────────────────────────────────────────────────────────────
NOTION_TOKEN       = os.environ["NOTION_TOKEN"]
ANTHROPIC_API_KEY  = os.environ["ANTHROPIC_API_KEY"]

# Notion database IDs（ダッシュなし）
PAPERS_DB_ID   = "34468fa947ec8172ac5ee77c83190d83"
GENE_DB_ID     = "a83e794bae574012b5ed4c06a63af6ea"
EVIDENCE_DB_ID = "aa641fc06246431684b105e946f59192"

notion = Client(auth=NOTION_TOKEN)

# Claude API
CLAUDE_MODEL   = "claude-sonnet-4-20250514"
CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
MAX_TOKENS     = 2000
BATCH_SIZE     = 20     # 1回の実行で処理する最大論文数

# Notion スキーマと一致させる有効値
VALID_SPECIES         = {"Oryza sativa", "Arabidopsis thaliana", "Other"}
VALID_FE_GENE         = {"Fe sufficient", "Fe deficient", "Both"}
VALID_FE_EVIDENCE     = {"Fe sufficient", "Fe deficient", "Both", "Not specified"}
VALID_EVIDENCE_TYPE   = {
    "Expression", "Protein level", "Mutant phenotype",
    "Interaction", "Localization", "Other",
}
VALID_METHOD          = {
    "qPCR", "Western blot", "GUS/GFP reporter", "ICP-OES",
    "CRISPR/knockout", "RNAi", "ChIP", "Co-IP", "SPAD",
}

# ── System Prompt ─────────────────────────────────────────────────────────
SYSTEM_PROMPT = """\
You are a plant molecular biology assistant specializing in iron homeostasis \
in rice (Oryza sativa) and Arabidopsis thaliana.

Given a paper title and abstract, extract ALL genes/proteins related to iron \
homeostasis and summarize the key evidence as structured JSON.

Focus on:
- Iron uptake, transport, storage, and recycling genes
- Iron-sensing and signaling factors (E3 ligases, transcription factors, peptides)
- Genes regulated by iron status (Fe-sufficient / Fe-deficient conditions)
- Genes whose mutation or overexpression affects iron homeostasis

Return ONLY a valid JSON object. No preamble, no markdown, no code fences.

Required JSON format:
{
  "genes": [
    {
      "name": "Gene symbol (e.g. OsHRZ1)",
      "aliases": "comma-separated aliases",
      "species": "Oryza sativa OR Arabidopsis thaliana OR Other",
      "protein_family": "protein family or class",
      "domains": "domain names (e.g. RING, bHLH, Hemerythrin)",
      "function_summary": "1-3 sentence functional summary in Japanese",
      "fe_condition": ["Fe sufficient", "Fe deficient", "Both"],
      "tissue": "root, shoot, seed, etc."
    }
  ],
  "evidence": [
    {
      "gene_name": "must match a 'name' field in the genes array",
      "title": "short evidence title (max 100 chars)",
      "finding": "2-4 sentence detailed finding in Japanese",
      "evidence_type": "Expression OR Protein level OR Mutant phenotype OR Interaction OR Localization OR Other",
      "method": ["qPCR", "Western blot", "GUS/GFP reporter", "ICP-OES",
                 "CRISPR/knockout", "RNAi", "ChIP", "Co-IP", "SPAD"],
      "fe_condition": "Fe deficient OR Fe sufficient OR Both OR Not specified",
      "plant_material": "e.g. Oryza sativa cv. Nipponbare"
    }
  ]
}

If no relevant genes/evidence are found, return: {"genes": [], "evidence": []}
"""

# ── Notion ヘルパー ───────────────────────────────────────────────────────

def _text(props, key):
    """Notion ページプロパティから plain text を取り出す"""
    p = props.get(key)
    if not p:
        return ""
    t = p.get("type", "")
    if t == "title":
        return "".join(r["plain_text"] for r in p.get("title", []))
    if t == "rich_text":
        return "".join(r["plain_text"] for r in p.get("rich_text", []))
    if t == "url":
        return p.get("url") or ""
    return ""


def get_unprocessed_papers():
    """Gene Extracted = false（未処理）の論文を Papers DB から取得する"""
    results, cursor = [], None
    while len(results) < BATCH_SIZE:
        kwargs = {
            "database_id": PAPERS_DB_ID,
            "filter": {"property": "Gene Extracted", "checkbox": {"equals": False}},
            "page_size": min(BATCH_SIZE - len(results), 20),
        }
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = notion.databases.query(**kwargs)
        results.extend(resp["results"])
        if not resp.get("has_more"):
            break
        cursor = resp["next_cursor"]
    return results


def find_gene_page(gene_name):
    """Gene/Protein DB で遺伝子名が完全一致するページ ID を返す（なければ None）"""
    resp = notion.databases.query(
        database_id=GENE_DB_ID,
        filter={"property": "Gene Name", "title": {"equals": gene_name}},
        page_size=1,
    )
    return resp["results"][0]["id"] if resp["results"] else None


def create_gene_page(gene, dry_run=False):
    """Gene/Protein DB に新しい遺伝子エントリを作成し、ページ ID を返す"""
    species = gene.get("species", "Other")
    if species not in VALID_SPECIES:
        species = "Other"

    fe_multi = [
        {"name": c} for c in gene.get("fe_condition", [])
        if c in VALID_FE_GENE
    ]

    props = {
        "Gene Name":      {"title":     [{"text": {"content": gene["name"]}}]},
        "Aliases":        {"rich_text": [{"text": {"content": gene.get("aliases", "")[:500]}}]},
        "Species":        {"select":    {"name": species}},
        "Protein Family": {"rich_text": [{"text": {"content": gene.get("protein_family", "")[:500]}}]},
        "Domains":        {"rich_text": [{"text": {"content": gene.get("domains", "")[:500]}}]},
        "Function Summary":{"rich_text":[{"text": {"content": gene.get("function_summary", "")[:2000]}}]},
        "Tissue":         {"rich_text": [{"text": {"content": gene.get("tissue", "")[:200]}}]},
    }
    if fe_multi:
        props["Fe Condition"] = {"multi_select": fe_multi}

    if dry_run:
        return f"[dry-run:{gene['name']}]"

    page = notion.pages.create(
        parent={"database_id": GENE_DB_ID},
        properties=props,
    )
    return page["id"]


def create_evidence_page(ev, gene_page_id, paper_page_id, dry_run=False):
    """Evidence Table に知見エントリを作成する"""
    et = ev.get("evidence_type", "Other")
    if et not in VALID_EVIDENCE_TYPE:
        et = "Other"

    fe = ev.get("fe_condition", "Not specified")
    if fe not in VALID_FE_EVIDENCE:
        fe = "Not specified"

    methods = [{"name": m} for m in ev.get("method", []) if m in VALID_METHOD]

    title_text = ev.get("title", "")[:100]
    finding_text = ev.get("finding", "")[:2000]

    props = {
        "Title":         {"title":     [{"text": {"content": title_text}}]},
        "Gene":          {"relation":  [{"id": gene_page_id}]},
        "Paper":         {"relation":  [{"id": paper_page_id}]},
        "Finding":       {"rich_text": [{"text": {"content": finding_text}}]},
        "Evidence Type": {"select":    {"name": et}},
        "Fe Condition":  {"select":    {"name": fe}},
        "Plant Material":{"rich_text": [{"text": {"content": ev.get("plant_material", "")[:300]}}]},
    }
    if methods:
        props["Method"] = {"multi_select": methods}

    if dry_run:
        return

    notion.pages.create(
        parent={"database_id": EVIDENCE_DB_ID},
        properties=props,
    )


def mark_paper_processed(paper_id, dry_run=False):
    """Papers DB の Gene Extracted チェックボックスを True にする"""
    if dry_run:
        return
    notion.pages.update(
        page_id=paper_id,
        properties={"Gene Extracted": {"checkbox": True}},
    )


# ── Claude API ────────────────────────────────────────────────────────────

def call_claude(title, abstract):
    """Claude API を呼び出し、JSON 文字列を返す（失敗時は None）"""
    user_content = (
        f"Title: {title}\n\n"
        f"Abstract: {abstract if abstract else '(abstract not available)'}"
    )
    headers = {
        "x-api-key":         ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type":      "application/json",
    }
    body = {
        "model":      CLAUDE_MODEL,
        "max_tokens": MAX_TOKENS,
        "system":     SYSTEM_PROMPT,
        "messages":   [{"role": "user", "content": user_content}],
    }
    for attempt in range(3):
        try:
            resp = requests.post(CLAUDE_API_URL, headers=headers, json=body, timeout=60)
            resp.raise_for_status()
            return resp.json()["content"][0]["text"].strip()
        except requests.RequestException as e:
            log.warning(f"Claude API error (attempt {attempt + 1}/3): {e}")
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
    return None


def parse_claude_response(raw):
    """Claude 応答を JSON としてパースする（失敗時は None）"""
    if not raw:
        return None
    # コードフェンスを除去（念のため）
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("` \n").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        log.error(f"JSON parse error: {e}")
        log.debug(f"Raw response: {raw[:300]}")
        return None


# ── メイン処理 ────────────────────────────────────────────────────────────

def main(dry_run=False):
    log.info(f"=== Gene Extractor Bot starting (dry_run={dry_run}) ===")

    papers = get_unprocessed_papers()
    log.info(f"Unprocessed papers: {len(papers)}")
    if not papers:
        log.info("Nothing to process. Exiting.")
        return

    gene_id_cache    = {}  # gene_name → page_id のセッションキャッシュ
    n_genes_created  = 0
    n_evidence_added = 0
    n_papers_done    = 0
    n_papers_failed  = 0

    for paper in papers:
        props    = paper["properties"]
        title    = _text(props, "Title")
        abstract = _text(props, "Abstract")
        paper_id = paper["id"]

        log.info(f"\n--- {title[:80]}...")

        # ── Claude API 呼び出し ──────────────────────────────
        raw       = call_claude(title, abstract)
        extracted = parse_claude_response(raw)

        if not extracted:
            log.warning("  Parse failed. Marking as processed and skipping.")
            mark_paper_processed(paper_id, dry_run)
            n_papers_failed += 1
            continue

        genes_data    = extracted.get("genes", [])
        evidence_data = extracted.get("evidence", [])
        log.info(f"  → genes: {len(genes_data)}, evidence: {len(evidence_data)}")

        # ── Gene/Protein DB への書き込み ────────────────────
        local_gene_ids = {}
        for gene in genes_data:
            name = gene.get("name", "").strip()
            if not name:
                continue

            if name in gene_id_cache:
                gid = gene_id_cache[name]
                log.info(f"  [cache] {name}")
            else:
                gid = find_gene_page(name)
                if gid:
                    log.info(f"  [exists] {name}")
                else:
                    gid = create_gene_page(gene, dry_run)
                    log.info(f"  [created] {name}")
                    n_genes_created += 1
                gene_id_cache[name] = gid

            local_gene_ids[name] = gid
            time.sleep(0.3)

        # ── Evidence Table への書き込み ─────────────────────
        for ev in evidence_data:
            gene_name = ev.get("gene_name", "").strip()
            gid = local_gene_ids.get(gene_name) or gene_id_cache.get(gene_name)
            if not gid:
                log.warning(f"  Evidence skipped (gene not found): '{gene_name}'")
                continue
            create_evidence_page(ev, gid, paper_id, dry_run)
            log.info(f"  [evidence] {ev.get('title', '')[:60]}")
            n_evidence_added += 1
            time.sleep(0.3)

        mark_paper_processed(paper_id, dry_run)
        n_papers_done += 1
        time.sleep(1.0)   # 次論文処理前クールダウン

    log.info(
        f"\n=== Done: {n_papers_done} papers processed, "
        f"{n_papers_failed} failed, "
        f"{n_genes_created} genes created, "
        f"{n_evidence_added} evidence entries added ==="
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gene Extractor Bot")
    parser.add_argument("--dry-run", action="store_true",
                        help="Notion への書き込みを行わず、抽出内容だけ確認する")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
