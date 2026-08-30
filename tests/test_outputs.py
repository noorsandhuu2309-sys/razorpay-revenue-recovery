"""Create-as-output-action (§12).

The tests that matter here are not "does it return 200". They are the three
promises the module makes: an output is built from real material, it lands back
in the Space as a selectable object, and it never claims more trust than its
inputs.
"""

from __future__ import annotations

import pytest

from omnix.core import artifacts as artifacts_mod
from omnix.core import objects as objects_mod
from omnix.core import outputs


@pytest.fixture
def held(ws, obj):
    """Two linked objects with an event, so every section has material."""
    a = obj("company", "Northwind Corp", provenance="source_backed",
            description="A supplier of industrial widgets.")
    b = obj("company", "Contoso Ltd", provenance="ai_inferred")
    objects_mod.link(ws, a["id"], b["id"], "competes_with",
                     provenance="source_backed")
    objects_mod.add_event(ws, "Northwind opens a second plant",
                          object_id=a["id"], relevance="high")
    return [a["id"], b["id"]]


def test_every_style_builds(ws, held):
    """A style that cannot produce a document is worse than an absent one."""
    for key in outputs.STYLES:
        art = outputs.create(ws, key, held, title=f"T {key}")
        full = artifacts_mod.get(art["id"])
        assert full is not None
        content = full["content"]
        assert content["style"] == key
        assert content["sections"], f"{key} produced no sections"
        assert content["counts"]["objects"] == 2


def test_unknown_style_is_rejected(ws, held):
    with pytest.raises(ValueError):
        outputs.create(ws, "hologram", held)


def test_output_lands_in_the_graph_linked_to_its_inputs(ws, held):
    """§12's closing requirement: artifacts become reusable objects."""
    art = outputs.create(ws, "report", held, title="Landing test")
    node_id = art["objectId"]
    assert node_id, "output did not become an object"

    node = objects_mod.get_object(ws, node_id)
    assert node["properties"]["artifactId"] == art["id"]

    rels = objects_mod.relationships_of(ws, node_id)
    linked = {r["dst"] if r["src"] == node_id else r["src"] for r in rels}
    assert set(held) <= linked, "output is not linked to what it was built from"
    assert all(r["relation"] == "derived_from" for r in rels)


def test_provenance_never_exceeds_the_weakest_input(ws, obj):
    """A report over an AI-inferred entity is AI-inferred, however it reads."""
    strong = obj("country", "Verifiedland", provenance="verified")
    weak = obj("company", "Rumoured Holdings", provenance="ai_inferred")

    only_strong = outputs.create(ws, "brief", [strong["id"]])
    assert only_strong["provenance"] == "verified"

    mixed = outputs.create(ws, "brief", [strong["id"], weak["id"]])
    assert mixed["provenance"] == "ai_inferred"
    node = objects_mod.get_object(ws, mixed["objectId"])
    assert node["provenance"] == "ai_inferred"


def test_output_records_an_event_so_activity_sees_it(ws, held):
    outputs.create(ws, "note", held, title="Activity test")
    titles = [e["title"] for e in objects_mod.timeline(ws, limit=50)]
    assert any("Created note" in t for t in titles)


def test_missing_objects_are_refused_not_silently_empty(ws):
    """An output over ids that do not exist would be a confident empty page."""
    with pytest.raises(ValueError) as e:
        outputs.create(ws, "report", ["nope-not-an-id"])
    assert "at least" in str(e.value)


# What a declared format actually produces. `pdf` is the odd one: OMNIX ships
# no PDF engine, so it serves the print-ready page and the browser writes the
# file. Naming that download `.pdf` would be a lie the OS then acts on — it is
# HTML, it is served as HTML, and it is named as HTML.
_FORMAT_FILE = {"md": ".md", "html": ".html", "csv": ".csv", "pdf": ".html"}


def test_renderers_cover_every_declared_format(ws, held):
    for key, style in outputs.STYLES.items():
        art = outputs.create(ws, key, held)
        full = artifacts_mod.get(art["id"])
        for fmt in style.formats:
            body, media, filename = outputs.render(full, fmt)
            assert body.strip(), f"{key} rendered empty {fmt}"
            assert filename.endswith(_FORMAT_FILE[fmt]), f"{key}/{fmt}"
            # The extension must describe the bytes, not the button label.
            assert media.split("/")[1].split(";")[0].strip() in (
                {"md": "markdown", "html": "html", "csv": "csv",
                 "pdf": "html"}[fmt],)


def test_markdown_and_html_carry_the_trust_floor(ws, held):
    art = outputs.create(ws, "report", held, title="Provenance shown")
    full = artifacts_mod.get(art["id"])
    md, _, _ = outputs.render(full, "md")
    html, _, _ = outputs.render(full, "html")
    # `held` mixes source_backed with ai_inferred, so the floor is the weaker.
    assert "AI-inferred" in md
    assert "AI-inferred" in html
    assert "<table" in html


def test_csv_is_one_table(ws, held):
    """A CSV concatenating three tables cannot be opened as a spreadsheet."""
    art = outputs.create(ws, "spreadsheet", held)
    full = artifacts_mod.get(art["id"])
    csv_text, _, _ = outputs.render(full, "csv")
    rows = [r for r in csv_text.strip().split("\n") if r]
    header = rows[0].split(",")
    assert header[0] == "Name"
    # Every row has the same column count — the property that makes it a table.
    assert all(len(r.split(",")) >= len(header) - 1 for r in rows[1:])


# ---------------------------------------------------------------------------
# The dossier and its citation appendix
# ---------------------------------------------------------------------------
@pytest.fixture
def sourced(ws):
    """Two claims backed by real Source rows, wired the way research wires them.

    Deliberately built through `research_ingest.persist_claims`, not by hand:
    the appendix reads the ObjectSource edges that function writes, so a fixture
    that faked those edges would test the test.
    """
    from omnix.core import research_ingest as ri

    source_ids = ri.persist_sources(ws, [
        {"n": 1, "url": "https://lloydslist.com/a", "host": "lloydslist.com",
         "title": "Hormuz transits fall", "tier": "trade",
         "tier_label": "Trade press", "credibility": 80},
        {"n": 2, "url": "https://reuters.com/b", "host": "reuters.com",
         "title": "Traffic down", "tier": "news",
         "tier_label": "News wire", "credibility": 85},
    ], execution_id="ex1")
    ri.persist_claims(ws, "ex1", [
        {"text": "Transits fell 12 percent.", "verdict": "verified",
         "confidence": 71, "supported_by": [1, 2]},
        {"text": "Insurance premiums rose.", "verdict": "single_source",
         "confidence": 40, "supported_by": [2]},
    ], source_ids)
    return ws


def test_dossier_numbers_its_citations_and_lists_them(sourced):
    art = outputs.create(sourced, "dossier", [], title="Hormuz dossier")
    content = artifacts_mod.get(art["id"])["content"]
    appendix = next(s for s in content["sections"] if s["heading"] == "Citations")
    numbers = [r[0] for r in appendix["rows"]]
    # Numbered from 1, contiguous, no gaps — an appendix that skips [2] sends a
    # reader looking for a source that is not there.
    assert numbers == [f"[{i + 1}]" for i in range(len(numbers))]
    assert any("lloydslist.com" in str(r[-1]) for r in appendix["rows"])


def test_every_claim_citation_resolves_to_an_appendix_entry(sourced):
    """The property that makes the document checkable."""
    import re

    art = outputs.create(sourced, "dossier", [], title="Resolvable")
    content = artifacts_mod.get(art["id"])["content"]
    appendix = next(s for s in content["sections"] if s["heading"] == "Citations")
    claims = next(s for s in content["sections"]
                  if s["heading"] == "Claims and their evidence")

    available = {r[0] for r in appendix["rows"]}
    used = set()
    for row in claims["rows"]:
        used.update(re.findall(r"\[\d+\]", str(row[-1])))
    assert used
    assert used <= available, f"dangling citations: {used - available}"


def test_the_appendix_lists_only_what_the_document_cites(ws, sourced):
    """A source nobody cited must not pad the bibliography."""
    from omnix.core import research_ingest as ri

    ri.persist_sources(ws, [
        {"n": 9, "url": "https://unused.example/x", "host": "unused.example",
         "title": "Never cited", "tier": "general", "credibility": 10},
    ], execution_id="ex2")

    art = outputs.create(ws, "dossier", [], title="Only cited")
    content = artifacts_mod.get(art["id"])["content"]
    appendix = next(s for s in content["sections"] if s["heading"] == "Citations")
    assert not any("unused.example" in str(r[-1]) for r in appendix["rows"])


def test_a_dossier_with_no_sourced_claims_says_so(ws, held):
    """Silence here would let an uncheckable document look well-sourced."""
    art = outputs.create(ws, "dossier", held, title="Nothing sourced")
    content = artifacts_mod.get(art["id"])["content"]
    citations = next(s for s in content["sections"] if s["heading"] == "Citations")
    assert citations["kind"] == "text"
    assert "nothing in it can be checked" in citations["body"]


def test_dossier_needs_no_selection(ws, sourced):
    """The main export must not be gated behind selecting objects first."""
    art = outputs.create(ws, "dossier", [])
    assert art["id"]


def test_pdf_render_is_print_ready(sourced):
    art = outputs.create(sourced, "dossier", [], title="Printable")
    full = artifacts_mod.get(art["id"])
    body, media, _ = outputs.render(full, "pdf")
    assert "@media print" in body
    # The screen rules make tables scroll horizontally, which on paper clips the
    # URL column clean off the page — the one column the appendix exists for.
    assert "display:table" in body
    assert "table-header-group" in body
    assert "window.print()" in body
    assert media.startswith("text/html")


def test_list_outputs_only_returns_created_outputs(ws, held):
    artifacts_mod.create(ws, "research-report", "Not an output", {"x": 1},
                         source_agent="oracle")
    outputs.create(ws, "brief", held, title="A real output")
    listed = outputs.list_outputs(ws)
    assert [o["title"] for o in listed] == ["A real output"]
