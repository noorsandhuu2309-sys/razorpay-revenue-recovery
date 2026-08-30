"""NOVA's conversation thread, scoped to a Space.

The command layer used to be stateless. Each request built a prompt from the
current input alone, which meant a follow-up like "yes" or "go on" reached the
model with nothing to attach to, and it replied by asking for context that the
user had already given. That is the single thing that made NOVA feel broken
rather than terse.

Two decisions worth keeping:

  * **The thread belongs to the Space, not the session.** Closing the tab must
    not lose the conversation, and two views of the same Space are the same
    conversation. This is the same reasoning that makes objects workspace-
    scoped.
  * **History is trimmed by turns and characters, not tokens.** A token count
    would need the tokeniser of whichever model the router happens to pick, and
    a wrong count that looks precise is worse than a generous character budget.
"""

from __future__ import annotations

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select

from .db import session
from .schema import ConversationTurn, iso

# How much prior conversation to replay into a prompt. Ten turns covers the
# "yes, go on" case and any reasonable clarification chain without letting an
# afternoon of chat crowd out the actual question.
MAX_TURNS = 10
MAX_CHARS = 6000


def public_turn(t: ConversationTurn) -> dict:
    return {
        "id": t.id,
        "seq": t.seq,
        "role": t.role,
        "text": t.text or "",
        "intent": t.intent or "",
        "model": t.model or "",
        "executionId": t.execution_id,
        "context": t.context_json or [],
        "createdAt": iso(t.created_at),
    }


def add(workspace_id: str, role: str, text: str, *, intent: str = "",
        model: str = "", execution_id: str | None = None,
        context: list[str] | None = None) -> dict:
    """Append one turn. Returns the public shape."""
    with session() as s:
        # Next slot in this Space's thread. Read inside the same transaction as
        # the insert so two turns written back to back cannot claim the same
        # number — which is exactly what a timestamp could not guarantee.
        nxt = (s.scalar(
            select(func.max(ConversationTurn.seq)).where(
                ConversationTurn.workspace_id == workspace_id)) or 0) + 1

        row = ConversationTurn(
            workspace_id=workspace_id,
            seq=nxt,
            role="assistant" if role == "assistant" else "user",
            text=(text or "")[:20000],
            intent=intent or "", model=model or "",
            execution_id=execution_id,
            context_json=list(context or []),
        )
        s.add(row)
        s.flush()
        return public_turn(row)


def thread(workspace_id: str, limit: int = 100) -> list[dict]:
    """The conversation in the order it happened, oldest first."""
    with session() as s:
        rows = s.scalars(
            select(ConversationTurn)
            .where(ConversationTurn.workspace_id == workspace_id)
            .order_by(ConversationTurn.seq.desc())
            .limit(max(1, limit))
        ).all()
    return [public_turn(t) for t in reversed(rows)]


def history_for_prompt(workspace_id: str, *, max_turns: int = MAX_TURNS,
                       max_chars: int = MAX_CHARS) -> list[dict]:
    """Recent turns as `{role, content}`, oldest first, trimmed to a budget.

    Trimming drops the OLDEST turns, because the reference a follow-up depends
    on is almost always the most recent exchange.
    """
    with session() as s:
        rows = s.scalars(
            select(ConversationTurn)
            .where(ConversationTurn.workspace_id == workspace_id)
            .order_by(ConversationTurn.seq.desc())
            .limit(max_turns)
        ).all()

    out: list[dict] = []
    used = 0
    for t in rows:                      # newest first while budgeting
        text = (t.text or "").strip()
        if not text:
            continue
        if used + len(text) > max_chars and out:
            break
        used += len(text)
        out.append({"role": t.role, "content": text})
    out.reverse()                       # back to chronological for the model
    return out


def clear(workspace_id: str) -> int:
    """Drop the thread for a Space. Returns how many turns went."""
    with session() as s:
        rows = s.scalars(
            select(ConversationTurn).where(
                ConversationTurn.workspace_id == workspace_id)).all()
        n = len(rows)
        s.execute(sql_delete(ConversationTurn).where(
            ConversationTurn.workspace_id == workspace_id))
        return n
