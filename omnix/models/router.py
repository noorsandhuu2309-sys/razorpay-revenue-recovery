"""The model router — the single place OMNIX asks a model for anything.

Application code names a capability, never a model:

    router.generate(Capability.FAST, system="...", user="...",
                    workspace_id=ws, execution_id=eid, agent="nova")

Everything else is the router's problem: which models satisfy that capability,
in what order for the caller's mode, what to do when a rung fails, and — the
part that did not exist anywhere before — writing a `model_call` row for every
attempt including the failures.

That last point is the whole reason PULSE can be honest. A model that burned
twenty seconds and returned nothing is a real cost and a real reliability
signal; the old path discarded it silently and left the error center blind.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .capabilities import BY_ID, Capability, ladder
from .providers import LLMResponse, ProviderError, Usage, get as get_provider

# Unknown models bill at a conservative (high) rate so a forgotten registry
# entry over-reports rather than hides spend. Same policy as avalon/credits.py.
_FALLBACK_PRICE = (5.00, 15.00)

# Per-attempt timeouts.
#
# Trading streaming for exact token counts cost the old hedge: cloud.stream_ladder
# abandoned a model that produced no first token in ~8s, whereas a non-streaming
# request only fails when the whole response times out. Measured in development:
# a stuck gpt-oss-20b burned 120s before the ladder moved on, against ~2.5s
# before. So early rungs get a short leash and only the last rung — the one with
# nothing to fall back to — gets the full budget.
_EARLY_RUNG_TIMEOUT = float(__import__("os").environ.get(
    "OMNIX_ROUTER_RUNG_TIMEOUT", "40"))
_DEFAULT_TIMEOUT = float(__import__("os").environ.get(
    "OMNIX_ROUTER_TIMEOUT", "90"))


def cost_usd(model: str, usage: Usage) -> float:
    entry = BY_ID.get(model)
    cin, cout = (entry.cost_in, entry.cost_out) if entry else _FALLBACK_PRICE
    return ((usage.input_tokens / 1_000_000) * cin
            + (usage.output_tokens / 1_000_000) * cout)


@dataclass
class RouterResult:
    text: str = ""
    model: str = ""
    provider: str = ""
    capability: str = ""
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0
    latency_ms: int = 0
    attempts: list[dict] = field(default_factory=list)
    ok: bool = True
    error: str = ""


class ModelRouter:
    # Set once when a ledger write fails, so the warning is reported but does
    # not repeat on every call.
    _warned_ledger = False

    def __init__(self) -> None:
        self._local_only: bool | None = None

    # -- selection ------------------------------------------------------------
    def select(self, capability: str | Capability, *, mode: str = "auto",
               model: str | None = None,
               models: list[str] | None = None) -> list[str]:
        """Ordered candidates. An explicit `model` wins but keeps the rest as
        fallback, so MANUAL mode degrades instead of hard-failing.

        `models` overrides selection entirely. That exists for the migration:
        the squad's tier ladders and ORACLE's per-role ladders were chosen from
        live measurements, so callers can move onto the router — and gain
        metering and real token counts — without their model choice changing
        underneath them. New code should pass a capability instead.
        """
        cap = capability.value if isinstance(capability, Capability) else capability
        if models:
            return [m for m in models if m]
        if self._use_local():
            rungs = ladder(cap, local=True, mode=mode)
        else:
            rungs = ladder(cap, mode=mode)
            if not rungs:
                rungs = ladder(cap, local=True, mode=mode)
        if model:
            rungs = [model] + [m for m in rungs if m != model]
        return rungs

    def _use_local(self) -> bool:
        try:
            from ..config import local_only, nvidia_enabled
            from .. import nvidia_client
            if local_only():
                return True
            return not (nvidia_enabled() and nvidia_client.available())
        except Exception:
            return True

    # -- execution ------------------------------------------------------------
    def generate(self, capability: str | Capability, *, system: str = "",
                 user: str = "", messages: list[dict] | None = None,
                 temperature: float = 0.2, max_tokens: int | None = None,
                 json_mode: bool = False, mode: str = "auto",
                 model: str | None = None, models: list[str] | None = None,
                 timeout: float | None = None, thinking: bool | None = None,
                 workspace_id: str | None = None, execution_id: str | None = None,
                 step_id: str | None = None, agent: str | None = None,
                 record: bool = True) -> RouterResult:
        """Try each rung until one answers. Never raises — a RouterResult with
        `ok=False` is returned instead, because every agent in OMNIX is written
        to degrade rather than crash when models are unavailable.

        Attribution falls back to the ambient call context, so a call made deep
        inside an agent is still charged to the right execution without the
        agent knowing it exists.
        """
        from .callctx import current as _current_ctx

        cap = capability.value if isinstance(capability, Capability) else capability
        cc = _current_ctx()
        workspace_id = workspace_id if workspace_id is not None else cc.workspace_id
        execution_id = execution_id if execution_id is not None else cc.execution_id
        step_id = step_id if step_id is not None else cc.step_id
        agent = agent if agent is not None else cc.agent

        msgs = messages or [{"role": "system", "content": system},
                            {"role": "user", "content": user}]
        rungs = self.select(cap, mode=mode, model=model, models=models)
        if not rungs:
            return RouterResult(capability=cap, ok=False,
                                error=f"no model available for capability '{cap}'")

        total_budget = timeout if timeout is not None else _DEFAULT_TIMEOUT
        attempts: list[dict] = []
        for i, mid in enumerate(rungs):
            entry = BY_ID.get(mid)
            provider_name = entry.provider if entry else "nvidia"
            # Short leash on every rung that has a fallback behind it; the final
            # rung gets the full budget because giving up there returns nothing.
            is_last = (i == len(rungs) - 1)
            attempt_timeout = (total_budget if is_last
                               else min(total_budget, _EARLY_RUNG_TIMEOUT))
            t0 = time.time()
            try:
                provider = get_provider(provider_name)
                resp = provider.generate(mid, msgs, temperature=temperature,
                                         max_tokens=max_tokens,
                                         json_mode=json_mode,
                                         timeout=attempt_timeout,
                                         thinking=thinking)
                if not resp.text.strip():
                    raise ProviderError("empty response")
                c = cost_usd(resp.model, resp.usage)
                if record:
                    self._record(workspace_id, execution_id, step_id, agent, cap,
                                 provider_name, resp.model, resp.usage, c,
                                 resp.latency_ms, "ok", "")
                attempts.append({"model": mid, "status": "ok",
                                 "latencyMs": resp.latency_ms})
                return RouterResult(
                    text=resp.text, model=resp.model, provider=provider_name,
                    capability=cap, usage=resp.usage, cost_usd=c,
                    latency_ms=resp.latency_ms, attempts=attempts, ok=True)
            except Exception as e:
                latency = int((time.time() - t0) * 1000)
                detail = f"{type(e).__name__}: {e}"[:300]
                if record:
                    self._record(workspace_id, execution_id, step_id, agent, cap,
                                 provider_name, mid, Usage(), 0.0, latency,
                                 "error", detail)
                attempts.append({"model": mid, "status": "error",
                                 "latencyMs": latency, "error": detail})
                continue

        return RouterResult(capability=cap, attempts=attempts, ok=False,
                            error=f"all {len(rungs)} model(s) failed for '{cap}'")

    def generate_json(self, capability, *, default=None, **kw):
        """generate() coerced to JSON, tolerant of fences and stray prose."""
        from ..squad.base import extract_json
        res = self.generate(capability, json_mode=True, **kw)
        if not res.ok:
            return default, res
        parsed = extract_json(res.text)
        return (parsed if parsed is not None else default), res

    # -- metering -------------------------------------------------------------
    def _record(self, workspace_id, execution_id, step_id, agent, capability,
                provider, model, usage: Usage, cost: float, latency_ms: int,
                status: str, error: str) -> None:
        """Write the call to the ledger. Best-effort: metering must never be
        the reason an agent run fails.

        Swallowed, but not silent. A metering failure means cost and usage are
        quietly wrong everywhere downstream, which is worse than an obvious
        crash — so the first one per process is printed. (A schema drift bug hid
        here during development for exactly this reason.)
        """
        try:
            from ..core.db import session
            from ..core.schema import ModelCall
            with session() as s:
                s.add(ModelCall(
                    workspace_id=workspace_id or "", execution_id=execution_id,
                    step_id=step_id, agent=agent, provider=provider, model=model,
                    capability=capability, input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens, cost_usd=cost,
                    tokens_estimated=usage.estimated, latency_ms=latency_ms,
                    status=status, error=error))
        except Exception as e:
            if not ModelRouter._warned_ledger:
                ModelRouter._warned_ledger = True
                print(f"[omnix.models] ledger write failed — usage and cost will "
                      f"be under-reported: {type(e).__name__}: {e}")


router = ModelRouter()
