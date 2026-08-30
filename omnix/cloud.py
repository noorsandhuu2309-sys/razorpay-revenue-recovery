"""Cloud-first model routing: a hedged ladder of NVIDIA models.

WHY THIS EXISTS
---------------
OMNIX is meant to run well for students on modest laptops. Requiring an 8GB GPU
to hold a 7B model excludes most of that audience, so the shipped product answers
from the cloud and local Ollama becomes a development/offline convenience rather
than the safety net.

That inverts the reliability problem. Previously a flaky cloud call fell back to a
local model that was always there; now there is no local net, so the cloud path
itself has to be dependable on a free tier that offers no capacity guarantee. Two
techniques do that:

**A ladder, not a single model.** Each agent names several models in preference
order. If the first can't deliver, the next one does. The tail of every ladder is
a small, consistently-warm model, so there is always something that answers.

**Hedging.** Waiting for rung 1 to time out before trying rung 2 makes the worst
case the sum of every timeout. Instead, if the primary hasn't produced a token
within `HEDGE_AFTER` seconds, the next rung starts *concurrently* and whichever
streams first wins; the loser is abandoned mid-flight. A stalled primary costs
~2.5s, not the full budget. This is the same trick that keeps latency flat under
tail-latency pressure in production search systems.

The winning model is reported back through `on_winner` so the UI can name the
model that ACTUALLY answered rather than the one we hoped would.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Iterator

from . import nvidia_client
from .nvidia_client import CloudError, FatalModelError

log = logging.getLogger("omnix.cloud")

# If the current rung is still silent after this long, start the next one too.
# Tuned to the measured data: the recommended models return a first token in
# ~0.5s, so 2.5s means "something is wrong" rather than "it's just thinking".
HEDGE_AFTER = 2.5


def stream_ladder(models: list[str],
                  messages: list[dict],
                  options: dict | None = None,
                  on_winner=None,
                  hedge_after: float = HEDGE_AFTER,
                  first_token_budget: float | None = None) -> Iterator[str]:
    """Stream from the first model in `models` that actually starts producing.

    Yields text chunks. Raises CloudError only if EVERY rung failed.
    """
    if not models:
        raise CloudError("no cloud models configured")

    q: queue.Queue = queue.Queue()
    lock = threading.Lock()
    winner: list[int | None] = [None]
    failures: dict[int, Exception] = {}
    started: list[int] = []

    def claim(idx: int) -> bool:
        """First model to produce a token owns the response."""
        with lock:
            if winner[0] is None:
                winner[0] = idx
            return winner[0] == idx

    def worker(idx: int, mid: str) -> None:
        try:
            stream = nvidia_client.stream_chat(
                messages, options, model_id=mid,
                first_token_budget=first_token_budget,
                # Abandon promptly once a sibling has won, so we stop paying for
                # tokens nobody will ever see.
                should_stop=lambda: winner[0] is not None and winner[0] != idx,
                on_alive=lambda: q.put(("alive", idx, None)),
            )
            produced = False
            for piece in stream:
                if not claim(idx):
                    return          # a sibling won the race; drop this stream
                produced = True
                q.put(("chunk", idx, piece))
            if produced and winner[0] == idx:
                q.put(("end", idx, None))
            elif not produced:
                q.put(("fail", idx, CloudError(f"{mid} returned nothing")))
        except Exception as e:                      # noqa: BLE001 - reported via queue
            q.put(("fail", idx, e))

    # The same prompt-sized budget each rung will grant itself. Computing it
    # from the flat default here while the rung used a larger one is how the
    # ladder used to kill a research rung it had only just given more time to.
    budget = nvidia_client.first_token_budget_for(messages, first_token_budget)
    launched_at: dict[int, float] = {}
    deadline = [0.0]

    def launch(idx: int) -> None:
        started.append(idx)
        launched_at[idx] = time.monotonic()
        # Each rung gets its own full budget from ITS OWN start, plus headroom
        # for the answer. A single global deadline starved late rungs — the
        # hedge would fire at 2.5s and then be killed moments later.
        deadline[0] = max(deadline[0], launched_at[idx] + budget + 20.0)
        t = threading.Thread(target=worker, args=(idx, models[idx]),
                             name=f"omx-cloud-{idx}", daemon=True)
        t.start()

    launch(0)
    next_rung = 1
    alive: set[int] = set()
    # A model that is actively reasoning is working, not stalled. Hold the hedge
    # while it thinks — otherwise the quality-picked primary is always beaten by
    # a smaller model that skips straight to answering — but never past this cap.
    hedge_cap = hedge_after * 3

    while True:
        now = time.monotonic()
        prev = next_rung - 1
        if next_rung < len(models) and winner[0] is None:
            patience = hedge_cap if prev in alive else hedge_after
            wait = max(0.2, (launched_at[prev] + patience) - now)
        else:
            wait = max(0.2, deadline[0] - now)
        try:
            kind, idx, payload = q.get(timeout=wait)
        except queue.Empty:
            if next_rung < len(models) and winner[0] is None:
                log.info("hedging: %s silent, also trying %s",
                         models[next_rung - 1], models[next_rung])
                launch(next_rung)
                next_rung += 1
                continue
            if time.monotonic() >= deadline[0] and winner[0] is None:
                raise CloudError("no cloud model produced a token in time")
            continue

        if kind == "alive":
            alive.add(idx)
            continue
        if kind == "chunk":
            if winner[0] == idx:
                if on_winner is not None:
                    on_winner(models[idx])
                    on_winner = None      # report once
                yield payload
        elif kind == "end":
            if winner[0] == idx:
                return
        elif kind == "fail":
            failures[idx] = payload
            if winner[0] == idx:
                # The winner died mid-answer; the user already has partial text,
                # so restarting elsewhere would duplicate it. Stop cleanly.
                raise CloudError(f"{models[idx]} failed mid-stream: {payload}")
            log.info("rung %s (%s) failed%s: %s", idx, models[idx],
                     " [fatal]" if isinstance(payload, FatalModelError) else "",
                     payload)
            # A rung failing is exactly when the next one should start.
            if next_rung < len(models) and winner[0] is None:
                launch(next_rung)
                next_rung += 1
            elif winner[0] is None and next_rung >= len(models) \
                    and len(failures) >= len(started):
                raise CloudError(
                    "every cloud model failed: "
                    + "; ".join(f"{models[i]}: {e}" for i, e in failures.items()))


# ---------------------------------------------------------------------------
# Keeping the ladder warm
# ---------------------------------------------------------------------------
_keeper: threading.Thread | None = None
_keeper_stop = threading.Event()


def start_keeper(models: list[str], interval: float = 240.0) -> None:
    """Ping the given models periodically so they stay resident.

    Cold start is the dominant latency risk here: the same model measured 0.5s
    warm and 20s+ cold. A cheap 1-token request every few minutes keeps the
    common path warm, which is what makes the app feel instant to a user who
    opens it once an hour. Idempotent — safe to call more than once.
    """
    global _keeper
    if _keeper is not None and _keeper.is_alive():
        return
    if not models or not nvidia_client.available():
        return

    uniq = list(dict.fromkeys(models))

    def loop() -> None:
        while not _keeper_stop.is_set():
            for mid in uniq:
                if _keeper_stop.is_set():
                    break
                try:
                    nvidia_client.warm(mid)
                except Exception:
                    pass
            _keeper_stop.wait(interval)

    _keeper = threading.Thread(target=loop, name="omx-cloud-keeper", daemon=True)
    _keeper.start()
    log.info("cloud keeper warming %d model(s) every %.0fs", len(uniq), interval)


def stop_keeper() -> None:
    _keeper_stop.set()
