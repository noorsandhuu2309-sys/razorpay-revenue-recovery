"""The model ladders, and the two failures that made chat feel broken.

Both bugs here were live and neither raised anything anyone saw: one killed a
long answer in the middle of a sentence, the other threw a KeyError the first
time a ladder named a model with no settings switch. They are pinned separately
from the roster's contents, because the roster will change again the next time
the account's models do — these must not.
"""

from __future__ import annotations

import httpx
import pytest

from omnix import model_catalog as mc
from omnix import nvidia_client as nc
from omnix.config import CLOUD_LADDER, CLOUD_TIERS, WARM_MODELS
from omnix.models.capabilities import BY_ID, CAPABILITIES, ladder
from omnix.squad.challenge import PANEL
from omnix.squad.oracle_models import ORACLE_LADDERS

_ROLES = ("chat", "code", "vision", "research", "fast")


def _every_live_rung() -> set[str]:
    rungs: set[str] = set()
    for table in (CLOUD_LADDER, CLOUD_TIERS, ORACLE_LADDERS):
        for entry in table.values():
            rungs.update(entry)
    rungs.update(m for m, _ in PANEL)
    rungs.update(WARM_MODELS)
    rungs.update(mc.WARM)
    for role in _ROLES:
        rungs.update(mc.ladder_for_role(role))
    for cap in CAPABILITIES:
        rungs.update(ladder(cap))
    return rungs


class TestLadderResolution:
    """`_allowed` used to raise on a rung that has no on/off switch."""

    def test_a_rung_outside_the_catalogue_is_kept_not_crashed_on(self):
        # The membership guard was written as a second `if` clause, so it ran
        # AFTER the dict lookup it was meant to guard. Any ladder naming a model
        # with no settings toggle — the second vision rung, for one — raised
        # KeyError instead of keeping it.
        assert mc._allowed(["definitely/not-in-the-catalogue"]) == \
            ["definitely/not-in-the-catalogue"]

    def test_a_switched_off_model_is_dropped_from_hedge_rungs_too(self):
        pick = next(m for m in mc.CATALOG
                    if not m.get("locked") and m["role"] != "vision")
        mc.set_enabled(pick["id"], False)
        try:
            for role in _ROLES:
                assert pick["model"] not in mc.ladder_for_role(role)
        finally:
            mc.set_enabled(pick["id"], True)

    @pytest.mark.parametrize("role", _ROLES)
    def test_every_role_resolves_to_at_least_one_model(self, role):
        assert mc.ladder_for_role(role)

    @pytest.mark.parametrize("model_id", sorted(mc.BY_ID))
    def test_every_catalogue_model_resolves_its_own_ladder(self, model_id):
        rungs = mc.ladder_for_model(model_id)
        assert rungs and rungs[0] == mc.BY_ID[model_id]["model"]

    def test_vision_never_falls_back_to_a_text_model(self):
        # A text model asked about an image does not fail — it invents a
        # description, which is worse than an error.
        vision_rungs = set(mc.ladder_for_role("vision"))
        assert mc.ANCHOR not in vision_rungs
        assert all("v" in r.rsplit("/", 1)[-1] for r in vision_rungs)


class TestRosterHygiene:
    def test_every_live_rung_has_a_price(self):
        """An unpriced model bills at the deliberately-high unknown rate, which
        silently overstates cost everywhere PULSE reports it."""
        unpriced = sorted(m for m in _every_live_rung() if m not in BY_ID)
        assert not unpriced, f"no cost entry for: {unpriced}"

    def test_the_anchor_can_never_be_switched_off(self):
        anchor = next(m for m in mc.CATALOG if m["model"] == mc.ANCHOR)
        mc.set_enabled(anchor["id"], False)
        assert mc.is_enabled(anchor["id"])

    def test_the_anchor_closes_every_text_ladder(self):
        for role in ("chat", "code", "research", "fast"):
            assert mc.ANCHOR in mc.ladder_for_role(role)

    def test_the_challenge_panel_is_four_distinct_vendors(self):
        assert len({v for _, v in PANEL}) == len(PANEL)
        assert len({m for m, _ in PANEL}) == len(PANEL)

    def test_the_warm_list_covers_every_ladder_lead(self):
        """The keeper exists because a cold MoE instance answers 502. A lead
        rung missing from it pays that cold start on a real user's first turn."""
        warm = set(WARM_MODELS) | set(mc.WARM)
        leads = {rungs[0] for rungs in CLOUD_LADDER.values()}
        assert leads <= warm, f"cold leads: {sorted(leads - warm)}"


class TestFirstTokenBudget:
    """A flat stopwatch judged a 9k-character research prompt by the same 8s a
    one-line chat turn got, and declared every rung dead before it had finished
    reading the question."""

    def test_the_budget_grows_with_the_prompt(self):
        short = nc.first_token_budget_for([{"role": "user", "content": "hi"}])
        long = nc.first_token_budget_for(
            [{"role": "user", "content": "x" * 9000}])
        assert long > short >= nc.FIRST_TOKEN_BUDGET

    def test_the_budget_is_capped(self):
        huge = nc.first_token_budget_for(
            [{"role": "user", "content": "x" * 5_000_000}])
        assert huge == nc.MAX_FIRST_TOKEN_BUDGET

    def test_non_string_content_does_not_break_it(self):
        # Vision turns carry a list of content parts, not a string.
        assert nc.first_token_budget_for(
            [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]) \
            == nc.FIRST_TOKEN_BUDGET


class TestContextFit:
    """The anchor rejected every chat turn with HTTP 400 until this existed."""

    def test_the_anchor_fits_the_real_chat_prompt(self):
        # `nemotron-mini-4b` holds 4,096 tokens — not the 8k its catalogue entry
        # claimed. OMNIX's chat system prompt is ~2,078 of them and the chat
        # agent asks for 4,096 of output, so the request was ~6,174 against a
        # 4,096 window. `stream_chat` treats 400 as fatal, so the rung was not
        # retried but struck off — leaving the ladder with no tail at all.
        from omnix.config import AGENTS
        msgs = [{"role": "system", "content": AGENTS["chat"]["system_prompt"]},
                {"role": "user", "content": "hello"}]
        want = AGENTS["chat"]["options"]["max_tokens"]
        got = nc.fit_max_tokens(mc.ANCHOR, msgs, want)
        prompt_tokens = len(AGENTS["chat"]["system_prompt"]) / 4.0
        assert got < want, "the anchor's budget must be cut to fit its window"
        assert prompt_tokens + got < nc._CONTEXT[mc.ANCHOR]

    @pytest.mark.parametrize("agent", ["chat", "coding", "reasoning",
                                       "research", "vision"])
    def test_every_agent_fits_every_rung_it_can_reach(self, agent):
        from omnix.config import AGENTS, CLOUD_LADDER
        spec = AGENTS[agent]
        msgs = [{"role": "system", "content": spec["system_prompt"]},
                {"role": "user", "content": "x" * 2000}]
        want = spec["options"]["max_tokens"]
        for rung in CLOUD_LADDER[agent] + [mc.ANCHOR]:
            got = nc.fit_max_tokens(rung, msgs, want)
            context = nc._CONTEXT.get(rung, nc._DEFAULT_CONTEXT)
            used = len(spec["system_prompt"]) / 4.0 + 500 + got
            assert used < context, f"{agent} overflows {rung}"

    def test_a_large_window_is_left_alone(self):
        msgs = [{"role": "user", "content": "hi"}]
        assert nc.fit_max_tokens("nvidia/nemotron-3-nano-30b-a3b",
                                 msgs, 4096) == 4096

    def test_it_never_returns_a_non_positive_budget(self):
        msgs = [{"role": "user", "content": "x" * 500_000}]
        assert nc.fit_max_tokens(mc.ANCHOR, msgs, 4096) >= 1


class TestThinkingSwitch:
    def test_it_is_only_sent_to_models_that_read_it(self):
        payload: dict = {}
        nc._apply_thinking(payload, "meta/llama-3.1-8b-instruct", False)
        assert "chat_template_kwargs" not in payload

        nc._apply_thinking(payload, "nvidia/nemotron-3-nano-30b-a3b", False)
        assert payload["chat_template_kwargs"] == {"thinking": False}

    def test_none_means_leave_the_model_alone(self):
        payload: dict = {}
        nc._apply_thinking(payload, "nvidia/nemotron-3-nano-30b-a3b", None)
        assert payload == {}

    def test_every_agent_states_a_thinking_preference(self):
        """Left unset it silently reverts to the model's default, which for the
        chat lead is 3-18s of deliberation before the first word."""
        from omnix.config import AGENTS
        for name, spec in AGENTS.items():
            assert "thinking" in spec["options"], f"{name} has no preference"

    def test_the_new_leads_are_recognised_as_reasoning_models(self):
        """The token FLOOR keys off the same family test. A lead that is not
        recognised gets a caller's 256-token budget and returns nothing."""
        from omnix.config import CLOUD_LADDER
        for rung in ("nvidia/nemotron-3-nano-30b-a3b",
                     "nvidia/nemotron-3-super-120b-a12b",
                     "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"):
            assert nc._is_reasoning_model(rung)
            assert nc._supports_thinking_switch(rung)
        assert not nc._is_reasoning_model(CLOUD_LADDER["chat"][1])


class TestStreamTimeouts:
    def test_the_read_timeout_is_the_stream_gap_not_the_first_token_budget(
            self, monkeypatch):
        """THE research bug. `read` is the gap allowed between two chunks, so it
        governs the whole stream. Setting it to the first-token budget capped
        every answer at ~10s of silence: a model pausing longer than that
        mid-sentence raised ReadTimeout with tokens already delivered, which
        surfaces as "stream broke mid-answer" and leaves half a reply on screen.
        """
        seen: list[httpx.Timeout] = []

        class _FakeStream:
            def __enter__(self):
                raise RuntimeError("only the timeout matters")

            def __exit__(self, *_):
                return False

        def capture(*_a, timeout=None, **_kw):
            seen.append(timeout)
            return _FakeStream()

        monkeypatch.setattr(nc, "api_key", lambda: "nvapi-test")
        monkeypatch.setattr(httpx, "stream", capture)

        with pytest.raises(Exception):
            list(nc.stream_chat([{"role": "user", "content": "x" * 9000}]))

        assert seen, "httpx.stream was never called"
        assert seen[0].read == nc.STREAM_GAP_TIMEOUT
        assert nc.STREAM_GAP_TIMEOUT > nc.MAX_FIRST_TOKEN_BUDGET
