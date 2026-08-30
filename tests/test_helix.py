"""HELIX — the bioinformatics corpus, its index, and the grounded answer layer.

The retrieval tests build a small synthetic corpus rather than loading the real
one. The real corpus is 4,000 PubMed records and 5MB on disk; a test that needs
it is a test that cannot run on a fresh checkout, and one that asserts against
whatever PubMed returned the day it was built is not a test at all.

The tests that genuinely need the shipped corpus are marked and skip without it.
"""

from __future__ import annotations

import pytest

from omnix.helix import answer as helix_answer
from omnix.helix import index as helix_index
from omnix.helix import topics as helix_topics


# ---------------------------------------------------------------------------
# A corpus small enough to reason about by hand
# ---------------------------------------------------------------------------
def _paper(pmid, title, abstract, topics, year="2024", journal="Test J"):
    return {
        "pmid": pmid, "doi": f"10.0000/{pmid}", "title": title,
        "abstract": abstract, "journal": journal, "year": year,
        "authors": ["Doe J", "Roe A"], "mesh": [], "pubtypes": ["Journal Article"],
        "topics": topics,
    }


@pytest.fixture(scope="module")
def ix() -> helix_index.Index:
    return helix_index.Index([
        _paper("1", "minimap2: pairwise alignment for nucleotide sequences",
               "minimap2 is a general-purpose aligner for long noisy reads. "
               "It is faster than BWA-MEM on long-read data.", ["alignment"]),
        _paper("2", "MAFFT multiple sequence alignment improvements",
               "MAFFT performs multiple sequence alignment of protein families "
               "and is widely used for phylogenetic work.", ["alignment"]),
        _paper("3", "MUSCLE: multiple sequence alignment with high accuracy",
               "MUSCLE aligns protein sequences and compares favourably with "
               "other multiple sequence alignment programs.", ["alignment"]),
        _paper("4", "Clustal Omega for large alignments",
               "Clustal Omega scales multiple sequence alignment to very large "
               "numbers of protein sequences.", ["alignment"]),
        _paper("5", "A benchmark of batch-effect correction methods",
               "We benchmark fourteen batch correction methods for single-cell "
               "RNA sequencing and recommend Harmony first.", ["singlecell"]),
        _paper("6", "Spatial transcriptomics deconvolution of tissue spots",
               "Spot deconvolution assigns cell types to spatial "
               "transcriptomics measurements across a tissue section.",
               ["spatial"]),
    ])


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------
def test_a_named_tool_outranks_its_own_topic(ix):
    """The query beats the expansion, and this is the reason expansion is weighted.

    "minimap2 or BWA" expands through the alignment topic to the whole alignment
    toolbox — MAFFT, MUSCLE, Clustal. At equal weight those three papers
    outscored the one paper that actually names minimap2, so asking about a
    specific tool returned everything except that tool. Expansion is a
    tie-breaker, never a vote of its own.
    """
    hits = ix.search("Is minimap2 or BWA better for long reads?", limit=3)
    assert hits, "no results at all"
    top = hits[0][1]
    assert "minimap2" in top["title"].lower(), (
        "expansion buried the named tool; top hit was %r" % top["title"])


def test_expansion_reaches_a_paper_the_query_does_not_word_match(ix):
    """Weighted does not mean disabled.

    "scRNA-seq integration" shares no content word with the benchmark paper,
    which says "single-cell RNA sequencing" and "Harmony". The alias table maps
    the acronym to the single-cell topic and expansion contributes that topic's
    vocabulary, which is what connects the two.
    """
    hits = ix.search("scRNA-seq integration", limit=3)
    assert hits, "expansion contributed nothing"
    assert any(p["pmid"] == "5" for _, p in hits), (
        "expansion did not reach the single-cell paper; got %s"
        % [p["pmid"] for _, p in hits])


def test_expansion_terms_never_outweigh_typed_terms(ix):
    """The weighting itself, asserted directly rather than through a ranking."""
    weights = ix.expand("minimap2 alignment")
    assert weights["minimap2"] == 1.0
    expansion = {t: w for t, w in weights.items() if t not in ("minimap2", "alignment")}
    assert expansion, "nothing was expanded"
    assert max(expansion.values()) < 1.0


def test_a_topic_filter_only_returns_that_topic(ix):
    hits = ix.search("alignment of sequences", limit=10, topic="spatial")
    assert all("spatial" in p["topics"] for _, p in hits)


def test_scores_are_ordered(ix):
    hits = ix.search("multiple sequence alignment protein", limit=4)
    scores = [s for s, _ in hits]
    assert scores == sorted(scores, reverse=True)


def test_an_empty_query_returns_nothing_rather_than_everything(ix):
    assert ix.search("", limit=5) == []
    assert ix.search("   ", limit=5) == []


def test_the_index_reports_what_it_holds(ix):
    stats = ix.stats()
    assert stats["papers"] == 6
    assert stats["vocabulary"] > 20
    assert stats["byTopic"]["alignment"] == 4


# ---------------------------------------------------------------------------
# Topic routing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("question, expected", [
    ("what is AlphaFold", "structure"),
    ("kraken2 taxonomic classification", "metagenomics"),
    ("which tools for RNA-seq differential expression", "rnaseq"),
    ("polygenic risk score portability", "gwas"),
    ("snakemake vs nextflow", "infrastructure"),
])
def test_a_question_routes_to_its_subfield(question, expected):
    found = helix_topics.find(question)
    assert found, "no topic matched %r" % question
    assert found[0].key == expected


def test_an_unrelated_question_matches_no_topic():
    assert helix_topics.find("what is the capital of France") == []


# ---------------------------------------------------------------------------
# The fast path
# ---------------------------------------------------------------------------
def test_a_definition_is_answered_without_a_model(monkeypatch, ix):
    """"What is X" is a structural question and must never reach a model."""
    monkeypatch.setattr(helix_index, "_index", ix)
    plan = helix_answer.plan("What is spatial transcriptomics?")
    assert plan.kind == "definition"
    assert plan.instant, "a definition fell through to the model path"
    assert "Spatial" in plan.instant


def test_a_tool_question_is_answered_without_a_model(monkeypatch, ix):
    monkeypatch.setattr(helix_index, "_index", ix)
    plan = helix_answer.plan("Which tools should I use for metagenomics?")
    assert plan.kind == "tools"
    assert "Kraken2" in plan.instant


def test_an_open_question_does_not_get_a_canned_answer(monkeypatch, ix):
    """The table knows what a subfield IS. It does not know what is true in it,
    so anything genuinely open has to be grounded in papers."""
    monkeypatch.setattr(helix_index, "_index", ix)
    plan = helix_answer.plan(
        "Do batch correction methods erase real biological signal?")
    assert plan.kind == "open"
    assert plan.instant == ""
    assert plan.sources, "an open question retrieved nothing to ground on"


def test_a_topic_merely_mentioned_is_not_answered_from_the_table(monkeypatch, ix):
    """"Does Seurat handle spatial data" names a topic but is not about it.

    The table knows what Seurat is FOR, not what it handles. Answering from it
    would be exactly the confident-and-wrong reply this feature exists to
    avoid.
    """
    monkeypatch.setattr(helix_index, "_index", ix)
    plan = helix_answer.plan("Does Seurat handle spatial data properly?")
    assert plan.instant == ""


# ---------------------------------------------------------------------------
# The prompt
# ---------------------------------------------------------------------------
def test_the_prompt_carries_numbered_sources_the_model_can_cite(monkeypatch, ix):
    monkeypatch.setattr(helix_index, "_index", ix)
    plan = helix_answer.plan("Do batch correction methods erase real signal?")
    messages = helix_answer.prompt(plan)
    assert messages[0]["role"] == "system"
    user = messages[1]["content"]
    for src in plan.sources:
        assert f"[{src.n}]" in user
        assert src.pmid in user
    assert "QUESTION:" in user


def test_the_system_prompt_forbids_answering_from_memory():
    assert "ONLY from the numbered papers" in helix_answer.SYSTEM


# ---------------------------------------------------------------------------
# Streaming hygiene
# ---------------------------------------------------------------------------
def test_a_foreign_citation_marker_is_normalised_across_chunks():
    """Some models emit `【1†L1-L4】` instead of `[1]`, and it can straddle a
    chunk boundary — a per-chunk regex misses exactly those."""
    chunks = ["one erases structure", "【1†L", "1-L4】 while MNN", " keeps it 【2】."]
    assert "".join(helix_answer.sanitised(chunks)) == (
        "one erases structure[1] while MNN keeps it [2].")


def test_ordinary_citations_are_left_alone():
    assert "".join(helix_answer.sanitised(["see [1] and [2]"])) == "see [1] and [2]"


def test_an_unterminated_marker_is_flushed_rather_than_swallowed():
    """A stream that ends mid-artefact must still deliver its last characters."""
    assert "".join(helix_answer.sanitised(["trailing 【3"])) == "trailing 【3"


# ---------------------------------------------------------------------------
# Model choice
# ---------------------------------------------------------------------------
def test_the_ladders_end_in_something_that_is_always_warm():
    """Every ladder must terminate in a model the keeper holds open, or a cold
    tier turns a 2s answer into a timeout."""
    from omnix.config import WARM_MODELS

    for ladder in (helix_answer.QUICK_LADDER, helix_answer.DEEP_LADDER):
        assert ladder, "empty ladder"
        assert ladder[-1] in WARM_MODELS, (
            "%s does not end in a warmed model" % ladder)


def test_deep_reads_more_than_quick():
    assert helix_answer.DEEP_SOURCES > helix_answer.QUICK_SOURCES


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", [
    "/api/helix/status",
    "/api/helix/topics",
    "/api/helix/search",
    "/api/helix/ask",
    "/api/helix/paper/{pmid}",
])
def test_the_routes_are_mounted_on_the_real_app(path):
    server = pytest.importorskip("omnix.server")
    assert path in server.app.openapi()["paths"]


def test_search_rejects_an_empty_query(monkeypatch):
    from fastapi.testclient import TestClient

    server = pytest.importorskip("omnix.server")
    monkeypatch.setenv("OMNIX_AUTH", "off")
    with TestClient(server.app) as client:
        assert client.get("/api/helix/search?q=").status_code == 400


def test_ask_rejects_an_empty_question(monkeypatch):
    from fastapi.testclient import TestClient

    server = pytest.importorskip("omnix.server")
    monkeypatch.setenv("OMNIX_AUTH", "off")
    with TestClient(server.app) as client:
        assert client.post("/api/helix/ask", json={}).status_code == 400


def test_ask_rejects_an_absurdly_long_question(monkeypatch):
    from fastapi.testclient import TestClient

    server = pytest.importorskip("omnix.server")
    monkeypatch.setenv("OMNIX_AUTH", "off")
    with TestClient(server.app) as client:
        r = client.post("/api/helix/ask", json={"question": "x" * 5000})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# The shipped corpus, when it is present
# ---------------------------------------------------------------------------
corpus_only = pytest.mark.skipif(
    not helix_index.CORPUS_PATH.exists(),
    reason="no corpus built; run python -m omnix.helix.ingest")


@corpus_only
def test_every_paper_in_the_shipped_corpus_is_usable():
    """Ingest drops records without an abstract. A record that slipped through
    would be retrieved and then grounded on nothing."""
    papers = helix_index.load_corpus()
    assert len(papers) > 500
    for p in papers[:400]:
        assert p["pmid"] and p["title"] and p["abstract"]
        assert p["topics"], "%s carries no topic" % p["pmid"]
        assert all(t in helix_topics.BY_KEY for t in p["topics"])


@corpus_only
def test_every_subfield_is_actually_covered():
    """A topic in the taxonomy with no papers behind it is a promise the UI
    makes and the corpus cannot keep."""
    stats = helix_index.shared().stats()
    empty = [k for k, n in stats["byTopic"].items() if n == 0]
    assert not empty, "subfields with no papers: %s" % empty
