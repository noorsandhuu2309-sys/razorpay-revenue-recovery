"""CHALLENGE's contract — especially the parts that must NOT overclaim.

This unit asks several models to attack an idea. The permanent risk is that it
starts sounding like validation: "3 of 4 models agree" is one careless render
away from being read as evidence, when it only measures overlap in training
data. These tests pin the honesty properties as hard as the functional ones.
"""
import pytest

from omnix.squad.challenge import PANEL, Challenge, _cluster, _similar


class TestClustering:
    def test_same_point_phrased_differently_merges(self):
        groups = _cluster([
            ("Solid-state timelines have slipped repeatedly at the pilot stage",
             "OpenAI"),
            ("Every prior solid-state timeline slipped during the pilot stage",
             "Meta"),
        ])
        assert len(groups) == 1
        assert groups[0]["vendors"] == {"OpenAI", "Meta"}

    def test_distinct_points_stay_separate(self):
        groups = _cluster([
            ("Manufacturing yields will not survive scale-up", "OpenAI"),
            ("Consumer demand for premium electric vehicles is untested",
             "Meta"),
        ])
        assert len(groups) == 2

    def test_one_vendor_repeating_itself_counts_once(self):
        """Otherwise a chatty model manufactures its own corroboration."""
        groups = _cluster([
            ("Manufacturing yields will not survive the scale-up step",
             "OpenAI"),
            ("Manufacturing yields will not survive scale-up at volume",
             "OpenAI"),
        ])
        assert len(groups) == 1
        assert groups[0]["vendors"] == {"OpenAI"}

    def test_longest_phrasing_is_kept(self):
        groups = _cluster([
            ("Yields will not survive scale-up", "OpenAI"),
            ("Yields will not survive scale-up because pilot lines run at "
             "much lower throughput", "Meta"),
        ])
        assert "pilot lines" in groups[0]["text"]

    def test_ordered_by_how_many_vendors_raised_it(self):
        groups = _cluster([
            ("Consumer demand for premium vehicles is untested", "OpenAI"),
            ("Manufacturing yields will not survive scale-up", "Meta"),
            ("Manufacturing yields will not survive the scale-up", "Mistral"),
            ("Manufacturing yields will not survive scale-up steps", "NVIDIA"),
        ])
        assert len(groups[0]["vendors"]) == 3

    def test_empty_and_blank_entries_are_dropped(self):
        assert _cluster([("", "OpenAI"), ("   ", "Meta")]) == []

    def test_similarity_is_false_for_unrelated_text(self):
        assert not _similar("Battery chemistry limits energy density",
                            "Regulatory approval in Europe takes years")


class TestPanelIndependence:
    def test_every_panellist_is_a_different_vendor(self):
        vendors = [v for _, v in PANEL]
        assert len(vendors) == len(set(vendors))

    def test_every_panellist_is_a_different_model(self):
        models = [m for m, _ in PANEL]
        assert len(models) == len(set(models))


class _FakeChallenge(Challenge):
    """Challenge with the network replaced by scripted panel replies."""

    def __init__(self, replies):
        super().__init__()
        self._replies = replies

    # `emit` is accepted and ignored throughout this file. The real `_ask`
    # reports each seat's state so the view can draw a live panel, and run()
    # passes the callback positionally: a stub that omits it raises TypeError
    # inside the worker thread, where `_ask`'s own except clause turns it into
    # a silently dropped seat that looks exactly like a vendor outage.
    def _ask(self, model, vendor, idea, emit=None):
        return self._replies.get(vendor)


def _reply(vendor, stance="mixed", counter="Timelines have always slipped "
                                            "at exactly this stage",
           assumptions=("Yields survive scale-up",),
           questions=("What gigafactory dates are announced?",)):
    return {"vendor": vendor, "model": f"m/{vendor}",
            "assumptions": list(assumptions), "counterargument": counter,
            "stance": stance, "stance_note": "hinges on yields",
            "research_questions": list(questions)}


def _run(unit, idea="Solid-state batteries ship at volume by 2028"):
    return unit.run({"input": idea}, lambda *a, **k: None)


class TestRunOutput:
    def test_research_questions_are_exposed_for_handoff(self):
        """The whole point: CHALLENGE feeds the research run."""
        unit = _FakeChallenge({
            "OpenAI": _reply("OpenAI",
                             questions=("What gigafactory dates exist?",)),
            "Meta": _reply("Meta",
                           questions=("What yields have pilots published?",)),
        })
        res = _run(unit)
        assert len(res.meta["researchQuestions"]) == 2

    def test_agreement_is_counted_over_models_that_answered(self):
        """Two of four answering must not be reported as '2 of 4'."""
        unit = _FakeChallenge({"OpenAI": _reply("OpenAI"),
                               "Meta": _reply("Meta")})
        res = _run(unit)
        assert res.meta["answered"] == 2
        assert res.meta["panelSize"] == len(PANEL)
        assert "2 of 2" in res.summary

    def test_meta_carries_everything_the_view_needs(self):
        """An execution's step output carries meta and a COUNT of blocks — the
        prose is filed in the artifact. The view renders from the execution, so
        anything it needs has to travel in meta. Reading `blocks` as a list
        from there is what blanked the whole app once."""
        unit = _FakeChallenge({v: _reply(v) for _, v in PANEL})
        meta = _run(unit).meta
        for key in ("headline", "split", "assumptions", "counterarguments",
                    "researchQuestions", "answered", "panelSize"):
            assert meta.get(key), f"meta.{key} missing"
        assert isinstance(meta["split"], list)

    def test_headline_matches_the_summary(self):
        unit = _FakeChallenge({v: _reply(v) for _, v in PANEL})
        res = _run(unit)
        assert res.meta["headline"] == res.summary

    def test_output_is_always_marked_as_opinion_not_evidence(self):
        unit = _FakeChallenge({"OpenAI": _reply("OpenAI")})
        res = _run(unit)
        assert res.meta["evidence"] == "model_opinion"
        assert "not evidence" in res.meta["disclaimer"].lower()

    def test_a_unanimous_panel_is_not_reported_as_support(self):
        """The dangerous case: four models agree and it reads as validation."""
        unit = _FakeChallenge({v: _reply(v, stance="plausible")
                               for _, v in PANEL})
        res = _run(unit)
        split = next(b for b in res.blocks
                     if "split" in str(b.get("title", "")).lower())
        text = " ".join(str(x) for x in split["items"]).lower()
        assert "not as support" in text
        assert "shared training data" in text

    def test_a_split_panel_reports_the_split(self):
        unit = _FakeChallenge({
            "OpenAI": _reply("OpenAI", stance="plausible"),
            "Meta": _reply("Meta", stance="doubtful"),
        })
        res = _run(unit)
        assert res.meta["stances"] == {"plausible": ["OpenAI"],
                                       "doubtful": ["Meta"]}

    def test_total_panel_failure_says_so_rather_than_looking_clean(self):
        """An empty critique must never read as 'no objections found'."""
        unit = _FakeChallenge({})
        res = _run(unit)
        assert res.meta["error"] == "panel_unavailable"
        assert "not been stress-tested" in res.summary
        assert not res.meta.get("researchQuestions")

    def test_empty_idea_is_rejected(self):
        res = _FakeChallenge({}).run({"input": "   "}, lambda *a, **k: None)
        assert "No idea provided" in res.summary

    def test_missing_panellists_are_disclosed(self):
        unit = _FakeChallenge({"OpenAI": _reply("OpenAI")})
        res = _run(unit)
        panel_block = next(b for b in res.blocks
                           if str(b.get("title", "")) == "Panel")
        assert "1 of 4" in " ".join(str(x) for x in panel_block["items"])


# The seat these tests knock out, taken FROM the panel rather than named. A
# literal vendor string here quietly stopped testing anything the day that
# vendor left the roster: "Mistral" never appeared, so nothing ever failed and
# the retry path went unexercised while the assertions still read as if it had.
_FAILING_SEAT = PANEL[1][1]
_LIVE_SEAT = PANEL[0][1]


class TestRetry:
    def test_a_seat_that_fails_once_is_retried_and_recovered(self):
        """Free-tier concurrency drops a different vendor on nearly every run,
        and a lost seat quietly lowers the 'N of M' denominator."""
        calls: list[str] = []

        class Flaky(Challenge):
            def _ask(self, model, vendor, idea, emit=None):
                calls.append(vendor)
                if vendor == _FAILING_SEAT and calls.count(_FAILING_SEAT) == 1:
                    return None
                return _reply(vendor)

        res = _run(Flaky())
        assert res.meta["answered"] == len(PANEL)
        assert calls.count(_FAILING_SEAT) == 2

    def test_a_seat_is_retried_only_once(self):
        calls: list[str] = []

        class Dead(Challenge):
            def _ask(self, model, vendor, idea, emit=None):
                calls.append(vendor)
                return None if vendor == _FAILING_SEAT else _reply(vendor)

        res = _run(Dead())
        assert res.meta["answered"] == len(PANEL) - 1
        assert calls.count(_FAILING_SEAT) == 2

    def test_retries_are_bounded_so_the_run_cannot_outlast_the_view(self):
        """One unresponsive vendor once pinned an execution in `running`
        forever. Timeout plus a retry cap is what bounds the worst case."""
        from omnix.squad.challenge import _ASK_TIMEOUT, _MAX_RETRIES
        calls: list[str] = []

        class MostlyDead(Challenge):
            def _ask(self, model, vendor, idea, emit=None):
                calls.append(vendor)
                return _reply(vendor) if vendor == _LIVE_SEAT else None

        _run(MostlyDead())
        # 4 in the parallel wave, then at most _MAX_RETRIES sequential ones.
        assert len(calls) == len(PANEL) + _MAX_RETRIES
        # The view waits 180s; the worst path must fit inside it.
        assert _ASK_TIMEOUT * (1 + _MAX_RETRIES) <= 180

    def test_every_panel_call_carries_a_timeout(self, monkeypatch):
        """A call with no timeout is the bug that hung the execution."""
        from omnix.models.router import router as real_router

        seen: list[dict] = []

        def capture(*_a, **kw):
            seen.append(kw)
            raise RuntimeError("stop here — only the kwargs matter")

        monkeypatch.setattr(real_router, "generate", capture)
        _run(Challenge())

        assert len(seen) == len(PANEL), "every seat should have been called"
        assert all(kw.get("timeout") for kw in seen)

    def test_no_retry_storm_when_the_whole_panel_is_down(self):
        """If nothing answered the backend is down, not rate-limited."""
        calls: list[str] = []

        class AllDead(Challenge):
            def _ask(self, model, vendor, idea, emit=None):
                calls.append(vendor)
                return None

        res = _run(AllDead())
        assert res.meta["error"] == "panel_unavailable"
        assert len(calls) == len(PANEL)


class TestRegistration:
    def test_challenge_is_in_the_product_catalogue(self):
        from omnix.squad import units
        assert "challenge" in [u["code"] for u in units.catalog()]

    def test_challenge_is_a_primary_agent(self):
        from omnix.agents_v2 import adapter
        assert "challenge" in adapter.PRIMARY_AGENTS


@pytest.mark.parametrize("stance", ["plausible", "doubtful", "mixed"])
def test_all_stances_render(stance):
    unit = _FakeChallenge({"OpenAI": _reply("OpenAI", stance=stance)})
    assert _run(unit).meta["stances"]
