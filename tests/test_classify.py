"""Intent routing — `classify()` decides which engine answers a question.

This function had no test until a dry run for a demo caught it sending
"Latest on India UPI international expansion" to the code agent, because
"test" is a substring of "latest". Hint matching was `substring in text`,
so short hints fired inside longer, unrelated words all over the product's
main path.

The misroute is invisible as a routing bug to whoever is using it: you ask a
research question and a code agent answers, so the product looks broken. Every
substring collision found is pinned below by name.
"""

import pytest

from omnix.api.nova import classify


# ---------------------------------------------------------------------------
# The substring collisions. Each of these was a live misroute.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("question, hint_inside", [
    ("What is Meta's latest AI model?", "eta in Meta"),
    ("Research the beta launch in Japan", "eta in beta"),
    ("Who is the Secretary of State?", "eta in Secretary"),
    ("Summarise the report on semiconductor exports", "repo in report"),
    ("What caused the Volkswagen scandal?", "scan in scandal"),
    ("Latest on India UPI international expansion", "test in latest"),
    ("Which countries protest the sanctions?", "test in protest"),
    ("What is the greatest risk to shipping?", "test in greatest"),
])
def test_short_hints_do_not_fire_inside_longer_words(question, hint_inside):
    """A hint must match a whole word, not a fragment of an unrelated one."""
    assert classify(question, []) in ("research", "direct", "query"), hint_inside


# ---------------------------------------------------------------------------
# Research. The demo question is here because it is the one users type.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("question", [
    "What is the current state of India UPI international expansion?",
    "What is the latest on India UPI international expansion?",
    "Latest on current state of India UPI international expansion",
    "Research the Strait of Hormuz shipping risk",
    "Find out who are the competitors in this market",
    "Investigating the semiconductor landscape",
    "Compare the two proposals",
])
def test_research_questions_reach_oracle(question):
    assert classify(question, []) == "research"


# ---------------------------------------------------------------------------
# The other branches still route. Bounding the hints must not lose them.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("question, expected", [
    ("Scan https://example.com for vulnerabilities", "agent:sentinel"),
    ("Audit this config for secrets", "agent:sentinel"),
    ("Is there a CVE for this version?", "agent:sentinel"),
    ("Refactor this function", "agent:forge"),
    ("Write a test for the repo", "agent:forge"),
    ("There is a bug in the codebase", "agent:forge"),
    ("Help me debug this implementation", "agent:forge"),
    ("What connects these two objects?", "query"),
    ("Show me everything related to sanctions", "query"),
    ("What is the path between them?", "query"),
])
def test_branches_still_route(question, expected):
    assert classify(question, []) == expected


def test_spatial_needs_more_than_a_hint_word():
    """Weather is spatial only with a position; otherwise it is a question."""
    q = "What is the weather like in the Taiwan Strait for shipping?"
    assert classify(q, [], has_position=False) == "direct"
    assert classify(q, [], has_position=True) == "spatial"


def test_selection_makes_relational_questions_graph_queries():
    """With objects selected, "compare these" is a traversal, not research."""
    assert classify("Compare these", []) == "research"
    assert classify("Compare these", ["obj-1", "obj-2"]) == "query"


@pytest.mark.parametrize("question", ["", "   ", "hello", "Tell me a joke"])
def test_unremarkable_text_is_answered_directly(question):
    assert classify(question, []) == "direct"
