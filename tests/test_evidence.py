"""The evidence engine is the product. These tests are its contract.

OMNIX sells one thing: a claim you can defend. That promise is kept or broken
entirely inside `oracle_evidence`, so the behaviours below are not
implementation details — they are the thing customers pay for.

The cases come from a real failure. A live run on "Strait of Hormuz shipping
risk" returned 31 claims, all 31 stamped `verified`, including
`"...the sea state wave height being at --"` (an unpopulated dashboard widget)
and a fleet of claims prefixed `"The Strait of Hormuz blockade has resulted
in..."` when no source said a blockade had occurred.
"""
from omnix.squad.oracle_evidence import (
    Claim, Source, is_junk_claim, is_meta_claim, imported_premise,
    verify_claims, overall_confidence,
)


def _src(n, text, url="https://example.com/a", title="T", credibility=58):
    # `credibility` is populated by build_sources() in the real pipeline, not
    # by the constructor, so a hand-built Source scores 0 unless it is set —
    # which would make every confidence assertion below trivially pass at 0.
    return Source(n=n, title=title, url=url, snippet="", content=text,
                  credibility=credibility)


# ---------------------------------------------------------------------------
# Junk: extractor output that carries no assertable value
# ---------------------------------------------------------------------------
class TestJunkClaims:
    def test_placeholder_dash_is_junk(self):
        # The exact string that shipped to a user.
        assert is_junk_claim(
            "The Strait of Hormuz blockade has resulted in the sea state "
            "wave height being at --.")

    def test_various_placeholder_tokens_are_junk(self):
        for token in ("--", "—", "N/A", "n/a", "TBD", "null", "undefined",
                      "...", "[]"):
            assert is_junk_claim(f"The utilisation rate is at {token}."), token

    def test_loading_state_is_junk(self):
        assert is_junk_claim("The tracker shows Loading... for vessel count.")

    def test_a_real_claim_with_a_number_is_not_junk(self):
        assert not is_junk_claim(
            "The ADCOP pipeline is running at 71% utilisation.")

    def test_a_real_qualitative_claim_is_not_junk(self):
        assert not is_junk_claim(
            "Iran has threatened to close the strait on multiple occasions.")

    def test_hyphenated_words_are_not_placeholders(self):
        # A single hyphen inside a word must not trip the placeholder rule.
        assert not is_junk_claim(
            "The state-owned operator reported record throughput.")

    def test_numeric_ranges_survive(self):
        # "12-15 million" is a real figure, not a placeholder.
        assert not is_junk_claim(
            "Daily flow ranged between 12-15 million barrels.")


# ---------------------------------------------------------------------------
# Imported premise: the failure that makes "verified" a lie
# ---------------------------------------------------------------------------
class TestImportedPremise:
    def test_causal_frame_absent_from_source_is_flagged(self):
        src = _src(1, "The ADCOP pipeline is operating at 71% of capacity "
                      "according to terminal data.")
        assert imported_premise(
            "The Strait of Hormuz blockade has resulted in the ADCOP "
            "pipeline being at 71% utilisation.", [src])

    def test_causal_frame_present_in_source_is_not_flagged(self):
        src = _src(1, "Following the blockade of the strait, the ADCOP "
                      "pipeline rose to 71% of capacity.")
        assert not imported_premise(
            "The blockade has resulted in the ADCOP pipeline being at 71% "
            "utilisation.", [src])

    def test_plain_claim_without_causal_language_is_not_flagged(self):
        src = _src(1, "The ADCOP pipeline is operating at 71% of capacity.")
        assert not imported_premise(
            "The ADCOP pipeline is at 71% utilisation.", [src])

    def test_no_sources_is_not_flagged_here(self):
        # Citation-less claims are already handled as `unsupported`; this
        # guard must not double-punish them with a confusing note.
        assert not imported_premise("X caused Y.", [])


# ---------------------------------------------------------------------------
# The verdict must reflect both guards
# ---------------------------------------------------------------------------
class TestVerdictHonesty:
    def test_claim_with_imported_premise_cannot_be_verified(self):
        src = _src(1, "The ADCOP pipeline is operating at 71% of capacity "
                      "according to terminal data.")
        c = Claim(text="The Strait of Hormuz blockade has resulted in the "
                       "ADCOP pipeline being at 71% utilisation.",
                  sources=[1])
        verify_claims([c], [src])
        assert c.verdict != "verified"
        assert "premise" in c.note.lower()

    def test_well_supported_claim_still_verifies(self):
        # The premise guard must not cost us true positives: a corroborated
        # claim with no invented framing still earns the badge.
        a = _src(1, "The ADCOP pipeline is operating at 71% of capacity "
                    "according to terminal data from the operator.")
        b = _src(2, "ADCOP pipeline throughput stands at 71% of capacity.",
                 url="https://reuters.com/x")
        c = Claim(text="The ADCOP pipeline is operating at 71% of capacity.",
                  sources=[1, 2])
        verify_claims([c], [a, b])
        assert c.verdict == "verified"
        assert c.confidence > 0

    def test_number_mismatch_still_fails(self):
        # Pre-existing behaviour that must survive the change.
        src = _src(1, "The ADCOP pipeline is operating at 50% of capacity.")
        c = Claim(text="The ADCOP pipeline is operating at 71% of capacity.",
                  sources=[1])
        verify_claims([c], [src])
        assert c.verdict == "unsupported"

    def test_single_source_claim_is_not_called_verified(self):
        """The badge has to mean something.

        Claims are EXTRACTED from a source, so checking one against the source
        it came from is very nearly a tautology — it will almost always pass.
        A live run returned 15 claims and stamped all 15 `verified`, which
        tells a reader nothing at all. `verified` is reserved for a claim a
        SECOND independent source also states.
        """
        src = _src(1, "The ADCOP pipeline is operating at 71% of capacity.")
        c = Claim(text="The ADCOP pipeline is operating at 71% of capacity.",
                  sources=[1])
        verify_claims([c], [src])
        assert c.verdict == "single_source"
        assert c.independent == 1

    def test_two_independent_sources_earn_verified(self):
        a = _src(1, "The ADCOP pipeline is operating at 71% of capacity.")
        b = _src(2, "Terminal data puts ADCOP pipeline throughput at 71% "
                    "of capacity this quarter.",
                 url="https://reuters.com/x")
        c = Claim(text="The ADCOP pipeline is operating at 71% of capacity.",
                  sources=[1, 2])
        verify_claims([c], [a, b])
        assert c.independent == 2
        assert c.verdict == "verified"

    def test_duplicate_sources_do_not_earn_verified(self):
        """Two copies of one story are one witness, not two."""
        a = _src(1, "The ADCOP pipeline is operating at 71% of capacity.")
        b = _src(2, "The ADCOP pipeline is operating at 71% of capacity.",
                 url="https://syndicated.example/x")
        b.duplicate_of = 1
        c = Claim(text="The ADCOP pipeline is operating at 71% of capacity.",
                  sources=[1, 2])
        verify_claims([c], [a, b])
        assert c.independent == 1
        assert c.verdict == "single_source"

    def test_single_source_scores_below_corroborated(self):
        src = _src(1, "The ADCOP pipeline is operating at 71% of capacity.")
        lone = Claim(text="The ADCOP pipeline is operating at 71% of capacity.",
                     sources=[1])
        verify_claims([lone], [src])

        b = _src(2, "Terminal data puts ADCOP pipeline throughput at 71% of "
                    "capacity.", url="https://reuters.com/x")
        both = Claim(text="The ADCOP pipeline is operating at 71% of capacity.",
                     sources=[1, 2])
        verify_claims([both], [src, b])
        assert both.confidence > lone.confidence

    def test_overall_confidence_drops_when_claims_are_downgraded(self):
        src = _src(1, "The pipeline is operating at 71% of capacity.")
        honest = Claim(text="The pipeline is operating at 71% of capacity.",
                       sources=[1])
        imported = Claim(text="The blockade has caused the pipeline to be at "
                              "71% of capacity.", sources=[1])
        verify_claims([honest, imported], [src])
        summary = overall_confidence([honest, imported], [src])
        assert summary["claims"] == 2
        # One source in play, so nothing can reach `verified`; the honest
        # claim is single-sourced and the imported-premise one is downgraded.
        assert summary["verified"] == 0
        assert summary["single_source"] == 1
        assert summary["weak"] == 1
