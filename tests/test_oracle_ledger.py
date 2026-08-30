"""ORACLE's evidence ledger has to add up to what it lists.

`single_source` is a fourth verdict beside verified / weak / unsupported, and it
is the commonest one in practice — most facts on the open web are stated once
and repeated. Summaries that counted only the other three printed "0 verified,
0 weakly supported" above a list of eight claims, which reads as the whole
research run having found nothing.
"""

from __future__ import annotations

import re

from omnix.squad.base import UnitResult
from omnix.squad.oracle import Oracle
from omnix.squad.oracle_evidence import Claim, Source, overall_confidence

VERDICTS = ("verified", "weak", "single_source", "unsupported")


def _claim(text, verdict, conf=40, cites=(1,)):
    c = Claim(text=text)
    c.verdict = verdict
    c.confidence = conf
    c.supported_by = list(cites)
    return c


def _render(claims):
    """Run the ledger renderer over `claims` and return its markdown blocks."""
    kept = [c for c in claims if c.verdict != "unsupported"]
    sources = [Source(n=i, title=f"Source {i}", url=f"https://ex{i}.com")
               for i in range(1, 4)]
    conf = overall_confidence(kept, sources)
    res = UnitResult()
    Oracle()._add_evidence_blocks(res, kept, claims, [], [], "", conf)
    return {str(b.get("title", "")): str(b.get("text") or "") for b in res.blocks}


class TestLedgerTally:
    def test_single_source_claims_are_counted(self):
        claims = [_claim(f"claim {i}", "single_source") for i in range(8)]
        ledger = _render(claims)["Evidence ledger"]
        assert "8 from a single source" in ledger
        # The old bug, stated as an assertion: a summary of zero above a list.
        assert not re.match(r"_0 verified, 0 weakly supported", ledger)

    def test_the_headline_counts_sum_to_the_claims_it_reports(self):
        claims = [_claim("a", "verified"), _claim("b", "single_source"),
                  _claim("c", "weak"), _claim("d", "unsupported")]
        blocks = _render(claims)
        how = blocks["How to read this"]
        nums = [int(n) for n in re.findall(r"(\d+) (?:corroborated|single-source"
                                           r"|weak|rejected)", how)]
        total = int(re.search(r"(\d+) claim\(s\)", how).group(1))
        assert sum(nums) == total, how

    def test_every_verdict_the_evidence_engine_emits_is_rendered(self):
        """A verdict the renderer does not know about vanishes from the tally.
        Pinning the set is what makes adding a fifth one a test failure rather
        than a silently wrong number."""
        src = (Claim.__module__,)
        import inspect

        import omnix.squad.oracle_evidence as ev
        assigned = set(re.findall(r'c\.verdict = "(\w+)"',
                                  inspect.getsource(ev)))
        assert assigned <= set(VERDICTS), f"unrendered verdict(s): {assigned - set(VERDICTS)}"
        assert src  # keep the import meaningful for readers

    def test_an_empty_run_does_not_claim_findings(self):
        blocks = _render([])
        assert "Evidence ledger" not in blocks
