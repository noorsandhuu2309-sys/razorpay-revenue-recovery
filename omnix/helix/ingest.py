"""Build the HELIX corpus from PubMed, via NCBI E-utilities.

Run:  python -m omni.helix.ingest            (incremental — keeps what it has)
      python -m omnix.helix.ingest --rebuild  (start from empty)

WHY E-UTILITIES DIRECTLY
------------------------
NCBI's E-utilities are the same public endpoints every PubMed client uses, they
need no key at the rates below, and they return structured XML with the fields
that matter: PMID, DOI, title, abstract, journal, year, authors and MeSH terms.
Scraping search-result pages would give worse data and break more often.

RATE LIMITS ARE REAL
--------------------
NCBI allows 3 requests/second without an API key and asks for a tool and email
in the query string so they can contact you rather than block you. Both are
sent. `NCBI_API_KEY` in the environment raises the ceiling to 10/s and is used
if present. The sleep between requests is not politeness decoration — exceeding
the limit gets the host banned, and the corpus is built once and read forever.

WHAT A "PAPER" IS HERE
----------------------
Only records with BOTH a title and an abstract are kept. An abstract-free record
cannot be retrieved against or grounded on, so storing it would inflate the
corpus count while making answers worse. Records are deduplicated by PMID
across topics, and a paper found under several topics keeps all of them — that
overlap is real signal about which subfields talk to each other.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

from .topics import TOPICS, Topic

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
CORPUS_PATH = Path(__file__).resolve().parent.parent.parent / "omnix_helix_corpus.json"

# NCBI asks every automated client to identify itself.
TOOL_NAME = "omnix-helix"
CONTACT = os.environ.get("NCBI_CONTACT_EMAIL", "").strip()

# 3/s without a key, 10/s with one. Stay under, not at, the limit.
_API_KEY = os.environ.get("NCBI_API_KEY", "").strip()
_DELAY = 0.12 if _API_KEY else 0.36


def _retrying(fn, what: str, attempts: int = 4):
    """Run `fn`, retrying on transient upstream failure with backoff.

    E-utilities answers a 502 or 429 under load often enough that a single
    attempt loses whole batches — the first build of this corpus dropped 50 of
    55 papers for one topic to one 502. Retrying is the difference between a
    reproducible corpus and one that silently depends on how busy NCBI was.
    """
    delay = 1.0
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            if code not in (429, 500, 502, 503, 504) or attempt == attempts:
                raise
            print(f"[helix] {what}: HTTP {code}, retry {attempt}/{attempts - 1} "
                  f"in {delay:.0f}s")
        except (httpx.TimeoutException, httpx.TransportError) as e:
            if attempt == attempts:
                raise
            print(f"[helix] {what}: {type(e).__name__}, retry "
                  f"{attempt}/{attempts - 1} in {delay:.0f}s")
        time.sleep(delay)
        delay *= 2
    raise RuntimeError("unreachable")


def _params(**kw) -> dict:
    p = {"tool": TOOL_NAME, **kw}
    if CONTACT:
        p["email"] = CONTACT
    if _API_KEY:
        p["api_key"] = _API_KEY
    return p


def _text(node, path: str, default: str = "") -> str:
    found = node.find(path)
    return (found.text or default).strip() if found is not None else default


def search(client: httpx.Client, query: str, retmax: int) -> list[str]:
    """PMIDs for a query, most relevant first."""
    def once():
        r = client.get(f"{EUTILS}/esearch.fcgi", params=_params(
            db="pubmed", term=query, retmax=str(retmax), retmode="json",
            sort="relevance"))
        r.raise_for_status()
        return r

    r = _retrying(once, "esearch")
    return list(r.json().get("esearchresult", {}).get("idlist", []))


def _abstract_of(article) -> str:
    """Join a structured abstract into one string, keeping its section labels.

    A structured abstract arrives as several `AbstractText` nodes with `Label`
    attributes (BACKGROUND / METHODS / RESULTS). Concatenating the text alone
    loses the reader's only cue about which part is a finding and which is
    setup, and the answer layer uses that distinction.
    """
    parts: list[str] = []
    for node in article.iter("AbstractText"):
        text = "".join(node.itertext()).strip()
        if not text:
            continue
        label = (node.get("Label") or "").strip()
        parts.append(f"{label.title()}: {text}" if label else text)
    return "\n".join(parts)


def fetch(client: httpx.Client, pmids: list[str]) -> list[dict]:
    """Full records for a batch of PMIDs."""
    if not pmids:
        return []
    def once():
        r = client.post(f"{EUTILS}/efetch.fcgi", data=_params(
            db="pubmed", id=",".join(pmids), retmode="xml"))
        r.raise_for_status()
        return r

    r = _retrying(once, f"efetch({len(pmids)})")
    root = ET.fromstring(r.text)

    out: list[dict] = []
    for article in root.iter("PubmedArticle"):
        pmid = _text(article, ".//PMID")
        title = "".join(
            article.find(".//ArticleTitle").itertext()
        ).strip() if article.find(".//ArticleTitle") is not None else ""
        abstract = _abstract_of(article)
        if not pmid or not title or not abstract:
            continue  # unusable for retrieval or grounding

        authors: list[str] = []
        for a in article.iter("Author"):
            last, initials = _text(a, "LastName"), _text(a, "Initials")
            if last:
                authors.append(f"{last} {initials}".strip())

        doi = ""
        for aid in article.iter("ArticleId"):
            if aid.get("IdType") == "doi" and aid.text:
                doi = aid.text.strip()
                break

        year = (_text(article, ".//PubDate/Year")
                or _text(article, ".//PubDate/MedlineDate")[:4])

        out.append({
            "pmid": pmid,
            "doi": doi,
            "title": title,
            "abstract": abstract,
            "journal": (_text(article, ".//Journal/ISOAbbreviation")
                        or _text(article, ".//Journal/Title")),
            "year": year,
            "authors": authors[:12],
            "mesh": sorted({_text(m, "DescriptorName")
                            for m in article.iter("MeshHeading")} - {""}),
            "pubtypes": sorted({(p.text or "").strip()
                                for p in article.iter("PublicationType")} - {""}),
            "topics": [],
        })
    return out


def build(rebuild: bool = False, only: str | None = None,
          multiplier: float = 1.0) -> dict:
    """Ingest every topic (or one) into the corpus.

    `multiplier` scales each topic's `depth`. The per-topic depths encode the
    relative size of each subfield; the multiplier scales the whole corpus
    without disturbing that shape, so a bigger build is still proportionate
    rather than flat. Ingest is incremental, so raising it and re-running adds
    papers instead of starting over.
    """
    existing: dict[str, dict] = {}
    if not rebuild and CORPUS_PATH.exists():
        try:
            prior = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
            existing = {p["pmid"]: p for p in prior.get("papers", [])}
            print(f"[helix] resuming from {len(existing)} existing papers")
        except Exception as e:
            print(f"[helix] existing corpus unreadable ({e}); rebuilding")

    topics: tuple[Topic, ...] = TOPICS
    if only:
        topics = tuple(t for t in TOPICS if t.key == only)
        if not topics:
            raise SystemExit(f"no such topic: {only}")

    headers = {"User-Agent": f"{TOOL_NAME} (+https://github.com/omnix)"}
    with httpx.Client(timeout=60.0, headers=headers) as client:
        for topic in topics:
            try:
                want = max(1, min(int(topic.depth * multiplier), 9999))
                pmids = search(client, topic.query, want)
            except Exception as e:
                print(f"[helix] {topic.key}: search failed: {e}")
                continue
            time.sleep(_DELAY)

            got = 0
            for i in range(0, len(pmids), 50):     # efetch batches of 50
                batch = pmids[i:i + 50]
                try:
                    records = fetch(client, batch)
                except Exception as e:
                    print(f"[helix] {topic.key}: fetch failed: {e}")
                    continue
                time.sleep(_DELAY)
                for rec in records:
                    prev = existing.get(rec["pmid"])
                    if prev:
                        # Seen under another topic: keep both labels.
                        if topic.key not in prev["topics"]:
                            prev["topics"].append(topic.key)
                    else:
                        rec["topics"] = [topic.key]
                        existing[rec["pmid"]] = rec
                        got += 1
            print(f"[helix] {topic.key:<15} {len(pmids):>3} hits -> "
                  f"{got:>3} new  (corpus {len(existing)})")

    papers = sorted(existing.values(), key=lambda p: (p.get("year") or "", p["pmid"]),
                    reverse=True)
    corpus = {
        "version": 1,
        "source": "PubMed (NCBI E-utilities)",
        "built": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "topics": [t.key for t in TOPICS],
        "count": len(papers),
        "papers": papers,
    }
    CORPUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CORPUS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(corpus, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, CORPUS_PATH)
    print(f"[helix] wrote {len(papers)} papers to {CORPUS_PATH}")
    return corpus


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the HELIX bioinformatics corpus")
    ap.add_argument("--rebuild", action="store_true",
                    help="discard the existing corpus instead of adding to it")
    ap.add_argument("--only", help="ingest a single topic key")
    ap.add_argument("--multiplier", type=float, default=1.0,
                    help="scale every topic's depth (4 gives ~4x the corpus)")
    args = ap.parse_args(argv)
    build(rebuild=args.rebuild, only=args.only, multiplier=args.multiplier)
    return 0


if __name__ == "__main__":
    sys.exit(main())
