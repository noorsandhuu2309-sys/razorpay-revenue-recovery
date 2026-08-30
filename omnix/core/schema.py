"""OMNIX platform tables.

Written SQLite-first but Postgres-compatible on purpose: string UUID primary
keys (no AUTOINCREMENT), timezone-aware timestamps, and JSON columns rather
than SQLite-only types. Moving to Postgres should be a URL change, not a
migration of every model.

Two conventions worth knowing before editing:

  * Anything a user can see is scoped by `workspace_id`. That column is not
    decoration — it is what makes the product multi-tenant later without
    revisiting every query. New user-visible tables get one.
  * JSON columns hold shapes the application owns (`content_json`,
    `payload_json`). They are deliberately schemaless because artifact content
    and event payloads differ per type, and forcing them into columns would
    mean a migration every time an agent learns a new output.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index,
                        Integer, String, Text)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    """Serialise a stored timestamp as unambiguous UTC.

    `DateTime(timezone=True)` is a lie on SQLite: it has no timezone type, so
    values come back naive even though they were written aware. `.isoformat()`
    on a naive value emits no offset, and a browser then parses it as *local*
    time — every duration in the UI was silently wrong by the viewer's UTC
    offset. Timestamps are always stored as UTC, so naive means UTC here.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "user"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), default="")
    display_name: Mapped[str] = mapped_column(String(120), default="")
    # free | pro. Entitlements read this; billing writes it.
    plan: Mapped[str] = mapped_column(String(32), default="free")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    workspaces: Mapped[list["Workspace"]] = relationship(back_populates="user")


class Workspace(Base):
    """A project: the container every artifact and execution belongs to."""
    __tablename__ = "workspace"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("user.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    settings_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                 onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="workspaces")


# ---------------------------------------------------------------------------
# Artifacts — the cross-agent handoff mechanism
# ---------------------------------------------------------------------------
# Declared types. Kept as a tuple rather than an Enum column because SQLite
# cannot alter a CHECK constraint, and agents will add types faster than we
# want to write migrations.
ARTIFACT_TYPES = (
    "research-report", "source", "dataset", "code", "repository-analysis",
    "architecture", "task-plan", "security-report", "document", "table",
    "chart", "prompt", "note", "diff", "finding-set", "execution-summary",
    # Produced by the Create verb (§12) — see core/outputs.py, which owns the
    # mapping from an output style to the type recorded here.
    "brief", "dashboard", "timeline", "presentation", "page",
)


class Artifact(Base):
    __tablename__ = "artifact"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id"), index=True)
    type: Mapped[str] = mapped_column(String(48), index=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    source_agent: Mapped[str] = mapped_column(String(32), default="")
    execution_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    # Versioning is a chain, not a number bump: editing an artifact creates a
    # new row pointing at its parent, so a report that fed a FORGE run can
    # never be mutated out from under that run's provenance.
    version: Mapped[int] = mapped_column(Integer, default=1)
    parent_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tags_json: Mapped[list] = mapped_column(JSON, default=list)
    content_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                 onupdate=utcnow)

    __table_args__ = (Index("ix_artifact_ws_created", "workspace_id", "created_at"),)


class ArtifactRef(Base):
    """A typed edge between artifacts.

    This is what the spec's `references` field becomes. It carries the citation
    graph (claim -> source), FORGE requirement traceability (requirement ->
    change -> test), and the provenance trail when one agent consumes another's
    output ("derived_from").
    """
    __tablename__ = "artifact_ref"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    from_artifact_id: Mapped[str] = mapped_column(ForeignKey("artifact.id"), index=True)
    to_artifact_id: Mapped[str] = mapped_column(ForeignKey("artifact.id"), index=True)
    relation: Mapped[str] = mapped_column(String(48), default="references")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------------------
# Executions
# ---------------------------------------------------------------------------
# The spec's status vocabulary. `waiting` is what GUIDED mode parks in while a
# user approves a plan; `planning` is compile-time before any step has run.
EXECUTION_STATUSES = ("queued", "planning", "running", "waiting",
                      "completed", "failed", "cancelled")
TERMINAL_STATUSES = ("completed", "failed", "cancelled")


class Execution(Base):
    __tablename__ = "execution"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id"), index=True)
    agent: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    # auto | guided | manual — NOVA's routing mode, recorded so a run can be
    # replayed the way it was actually authorised.
    mode: Mapped[str] = mapped_column(String(16), default="auto")
    title: Mapped[str] = mapped_column(String(300), default="")
    input_json: Mapped[dict] = mapped_column(JSON, default=dict)
    plan_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # Set when this run was launched by a handoff from another execution.
    parent_execution_id: Mapped[str | None] = mapped_column(String(32), index=True,
                                                            nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)

    steps: Mapped[list["ExecutionStep"]] = relationship(
        back_populates="execution", cascade="all, delete-orphan",
        order_by="ExecutionStep.idx")

    __table_args__ = (Index("ix_execution_ws_created", "workspace_id", "created_at"),)


class ExecutionStep(Base):
    """One node of an execution's DAG.

    A single-agent run is a one-step DAG, which is why NOVA's workflows need no
    separate engine — a compiled workflow is the same table with dependencies
    filled in.
    """
    __tablename__ = "execution_step"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    execution_id: Mapped[str] = mapped_column(ForeignKey("execution.id"), index=True)
    idx: Mapped[int] = mapped_column(Integer, default=0)
    key: Mapped[str] = mapped_column(String(64), default="")   # stable id for depends_on
    agent: Mapped[str] = mapped_column(String(32), default="")
    capability: Mapped[str] = mapped_column(String(32), default="")
    title: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(16), default="queued")
    input_json: Mapped[dict] = mapped_column(JSON, default=dict)
    output_json: Mapped[dict] = mapped_column(JSON, default=dict)
    depends_on_json: Mapped[list] = mapped_column(JSON, default=list)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)

    execution: Mapped[Execution] = relationship(back_populates="steps")


class Event(Base):
    """The event bus. The table IS the bus.

    Rows are written in the same transaction as the state change they describe,
    which is what lets SSE, history and PULSE all read one source without a
    broker. `seq` is per-execution and monotonic so a reconnecting client can
    replay from an index rather than from the beginning.
    """
    __tablename__ = "event"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    execution_id: Mapped[str] = mapped_column(ForeignKey("execution.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(String(32), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    type: Mapped[str] = mapped_column(String(48), index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_event_exec_seq", "execution_id", "seq"),)


EVENT_TYPES = (
    "execution.created", "execution.started", "execution.progress",
    "execution.completed", "execution.failed", "execution.cancelled",
    "artifact.created", "agent.handoff",
    "step.started", "step.completed", "step.failed",
    "tool.started", "tool.completed", "tool.failed",
    "model.request", "model.response", "model.error",
)


class ModelCall(Base):
    """One provider request. This table is why PULSE can show real cost.

    Written by the model router on every call, including failures — a model
    that burned 20s and produced nothing is exactly what the error center and
    the health probe need to see.
    """
    __tablename__ = "model_call"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(32), index=True)
    execution_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    step_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    agent: Mapped[str] = mapped_column(String(32), default="")
    provider: Mapped[str] = mapped_column(String(32), default="")
    model: Mapped[str] = mapped_column(String(160), default="", index=True)
    capability: Mapped[str] = mapped_column(String(32), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    # Real provider cost. Never a guess: 0.0 means "unpriced model", and the UI
    # must show tokens instead of inventing a number.
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    # True when token counts were derived from character length rather than
    # reported by the provider (the streaming path cannot report usage). PULSE
    # must label these — an estimated cost presented as measured is exactly the
    # invented number the product rules forbid.
    tokens_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ttft_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="ok")   # ok | error
    error: Mapped[str] = mapped_column(Text, default="")
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    __table_args__ = (Index("ix_modelcall_ws_ts", "workspace_id", "ts"),)


# ---------------------------------------------------------------------------
# Workspace intelligence
# ---------------------------------------------------------------------------
class MemoryItem(Base):
    """Transparent workspace memory. Always user-visible, always editable."""
    __tablename__ = "memory_item"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="fact")
    text: Mapped[str] = mapped_column(Text)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    source_execution_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Source(Base):
    """A retrieved document. Persisted so research can be diffed over time."""
    __tablename__ = "source"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id"), index=True)
    execution_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    url: Mapped[str] = mapped_column(Text, default="")
    title: Mapped[str] = mapped_column(Text, default="")
    publisher: Mapped[str] = mapped_column(String(200), default="")
    tier: Mapped[str] = mapped_column(String(32), default="general")
    tier_label: Mapped[str] = mapped_column(String(80), default="")
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    credibility: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_of: Mapped[str | None] = mapped_column(String(32), nullable=True)
    snippet: Mapped[str] = mapped_column(Text, default="")
    # Lets a re-run detect that a page changed without storing it twice.
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Claim(Base):
    """An extracted claim and its verification verdict.

    Mirrors oracle_evidence.Claim so the existing deterministic engine can
    write straight through without a translation layer.
    """
    __tablename__ = "claim"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id"), index=True)
    execution_id: Mapped[str] = mapped_column(String(32), index=True)
    text: Mapped[str] = mapped_column(Text)
    verdict: Mapped[str] = mapped_column(String(24), default="")  # verified|weak|unsupported
    confidence: Mapped[int] = mapped_column(Integer, default=0)
    supported_by_json: Mapped[list] = mapped_column(JSON, default=list)
    contradicted_by_json: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Finding(Base):
    """A SENTINEL security finding.

    `status` plus `fixed_by_execution_id` is the closed remediation loop: a
    finding becomes `fixed` because a rescan stopped reproducing it, never
    because a model said it was fixed.
    """
    __tablename__ = "finding"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id"), index=True)
    execution_id: Mapped[str] = mapped_column(String(32), index=True)
    # Stable identity across scans — this is what makes a delta possible.
    rule_id: Mapped[str] = mapped_column(String(80), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True, default="")
    title: Mapped[str] = mapped_column(String(300))
    severity: Mapped[str] = mapped_column(String(16), default="info")
    confidence: Mapped[str] = mapped_column(String(16), default="firm")
    target: Mapped[str] = mapped_column(Text, default="")
    location_json: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence_json: Mapped[dict] = mapped_column(JSON, default=dict)
    impact: Mapped[str] = mapped_column(Text, default="")
    remediation: Mapped[str] = mapped_column(Text, default="")
    references_json: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|fixed|accepted
    fixed_by_execution_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Repo(Base):
    """A repository FORGE has ingested."""
    __tablename__ = "repo"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id"), index=True)
    kind: Mapped[str] = mapped_column(String(24), default="local")  # local|github|upload
    url: Mapped[str] = mapped_column(Text, default="")
    name: Mapped[str] = mapped_column(String(200), default="")
    local_path: Mapped[str] = mapped_column(Text, default="")
    default_branch: Mapped[str] = mapped_column(String(120), default="")
    map_json: Mapped[dict] = mapped_column(JSON, default=dict)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Task(Base):
    """NOVA planning task (the useful half of ATLAS).

    Deliberately not a Jira competitor: tasks exist to structure AI execution,
    so `execution_id` links a task to the run that satisfied it.
    """
    __tablename__ = "task"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id"), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    title: Mapped[str] = mapped_column(Text)
    detail: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="todo")
    depends_on_json: Mapped[list] = mapped_column(JSON, default=list)
    effort: Mapped[str] = mapped_column(String(32), default="")
    risk: Mapped[str] = mapped_column(Text, default="")
    order_idx: Mapped[int] = mapped_column(Integer, default=0)
    execution_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------------------
# The intelligence graph
# ---------------------------------------------------------------------------
# Everything meaningful in a workspace is an object; relationships between them
# are first-class rows, not a JSON blob hanging off a node. This is the part
# that makes OMNIX a workspace rather than a chat log: research, repositories
# and news all land in the same two tables and therefore connect to each other.
#
# TERRA's graph stays where it is (a JSON file, article-shaped, global). It is
# projected through this model rather than migrated into it — see
# `core/terra_bridge.py`. Nothing about TERRA's refresh loop changes.

# Weakest-first, and the default is the weakest. An object an LLM invented is
# `ai_inferred` until evidence attaches to it; nothing silently claims to be
# verified. Mirrors core.ontology.PROVENANCE.
PROVENANCE_VALUES = ("user_created", "verified", "source_backed", "ai_inferred")


class ObjectNode(Base):
    """A node in the workspace intelligence graph.

    Named `ObjectNode` rather than `Object` because `object` is a builtin and
    the confusion is not worth the elegance. The table is `object`.

    `properties_json` is schemaless on purpose: a Company carries `sector` and
    `founded`, a File carries `path` and `language`, and forcing those into
    columns means a migration every time an agent learns a new object type.
    """
    __tablename__ = "object"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id"), index=True)
    type: Mapped[str] = mapped_column(String(48), index=True)
    name: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")

    # Stable cross-run identity — "country:US", "repo:omnix#omnix/server.py".
    # This is what makes re-running research update NVIDIA rather than creating
    # a second NVIDIA, and therefore what makes research diffing possible.
    external_id: Mapped[str] = mapped_column(String(300), default="", index=True)

    properties_json: Mapped[dict] = mapped_column(JSON, default=dict)
    tags_json: Mapped[list] = mapped_column(JSON, default=list)

    provenance: Mapped[str] = mapped_column(String(24), default="ai_inferred", index=True)
    # NULL means "not measured", and the UI must render it that way. A
    # decorative 50% is exactly the invented number the product rules forbid.
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Computed salience for sizing and ranking. Never shown as a percentage.
    salience: Mapped[float] = mapped_column(Float, default=0.0)

    # §21 live objects. `tracked` is the user's intent; the scheduler reads it.
    tracked: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # Nullable, and only meaningful for types the ontology marks `geo`. Presence
    # is what makes an object appear on the Map at all.
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Which run created it, so provenance survives back to an execution.
    execution_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)

    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                onupdate=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        # The dedup lookup. Not unique: an empty external_id is legal and
        # common for objects that have no natural key.
        Index("ix_object_ws_ext", "workspace_id", "external_id"),
        Index("ix_object_ws_type", "workspace_id", "type"),
    )


class Relationship(Base):
    """A typed edge between two objects. First-class, per the spec.

    Symmetric relations are stored ONCE, unlike TERRA's graph which stores both
    directions for lookup speed. In SQL the reverse lookup is an index scan, so
    duplicating rows would only create the possibility of the two halves
    disagreeing.
    """
    __tablename__ = "relationship"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id"), index=True)
    src_id: Mapped[str] = mapped_column(ForeignKey("object.id"), index=True)
    dst_id: Mapped[str] = mapped_column(ForeignKey("object.id"), index=True)
    relation: Mapped[str] = mapped_column(String(48), index=True)

    # Accumulated salience across observations, mirroring TERRA's edge weight.
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    observations: Mapped[int] = mapped_column(Integer, default=1)
    # Running mean of the tone of the evidence behind this edge. TERRA uses it
    # to separate "allied with" coverage from "in conflict" coverage.
    sentiment: Mapped[float] = mapped_column(Float, default=0.0)

    provenance: Mapped[str] = mapped_column(String(24), default="ai_inferred", index=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    properties_json: Mapped[dict] = mapped_column(JSON, default=dict)

    execution_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                onupdate=utcnow)

    __table_args__ = (
        # One row per (src, dst, relation). Re-observing an edge bumps weight
        # instead of inserting a duplicate.
        Index("ix_rel_unique", "workspace_id", "src_id", "dst_id", "relation",
              unique=True),
        Index("ix_rel_ws_dst", "workspace_id", "dst_id"),
    )


class ObjectEvent(Base):
    """Something that happened to an object, at a time.

    Distinct from `Event`, which is the execution bus. This one is user-facing
    temporal intelligence and is what the Timeline draws: a funding round, a
    commit, a detected change on a tracked object, a security finding opening.

    `occurred_at` and `detected_at` are separate because they routinely differ —
    news reports Tuesday's announcement on Thursday, and a timeline that plots
    the detection time is wrong in a way readers notice.
    """
    __tablename__ = "object_event"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id"), index=True)
    object_id: Mapped[str | None] = mapped_column(ForeignKey("object.id"), index=True,
                                                  nullable=True)
    type: Mapped[str] = mapped_column(String(48), default="update", index=True)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, default="")

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                  index=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # low | medium | high — §22's brief ranks on this. Set from measurable
    # signals (tracked object, edge weight, severity), never from a model's
    # unprompted opinion.
    relevance: Mapped[str] = mapped_column(String(16), default="medium", index=True)
    provenance: Mapped[str] = mapped_column(String(24), default="ai_inferred")
    properties_json: Mapped[dict] = mapped_column(JSON, default=dict)
    execution_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    dismissed: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (Index("ix_objevent_ws_occurred", "workspace_id", "occurred_at"),)


class ObjectSource(Base):
    """Provenance edge: the evidence behind an object, relationship or event.

    Exactly one of the three target columns is set. Kept as one table rather
    than three because every consumer asks the same question — "what backs
    this?" — and one table means one answer path.

    The existence of a row here is what promotes something from `ai_inferred`
    to `source_backed`. That promotion is the only honest one available without
    a human or a re-run confirming it.
    """
    __tablename__ = "object_source"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("source.id"), index=True)

    object_id: Mapped[str | None] = mapped_column(ForeignKey("object.id"), index=True,
                                                  nullable=True)
    relationship_id: Mapped[str | None] = mapped_column(ForeignKey("relationship.id"),
                                                        index=True, nullable=True)
    event_id: Mapped[str | None] = mapped_column(ForeignKey("object_event.id"),
                                                 index=True, nullable=True)

    # The passage that actually justified it. Without this, "cited" degenerates
    # into "this URL was open at the time".
    excerpt: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SavedView(Base):
    """A named view state — filters, layout, pinned context, camera.

    Saved rather than derived because the value of an intelligence workspace is
    returning to a question exactly as you left it.
    """
    __tablename__ = "saved_view"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    view: Mapped[str] = mapped_column(String(32), default="graph")
    state_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ConversationTurn(Base):
    """One exchange with NOVA, scoped to a Space.

    Without this the command layer was stateless: every request built a fresh
    prompt from the input alone, so answering "yes" to "shall I go into detail?"
    arrived at the model with no idea what "yes" referred to, and it asked for
    context again. A thread per Space is also the honest shape for the product
    — a Space is the thing you return to, so the conversation belongs to it
    rather than to a browser tab.

    `context_json` records which objects were held when the turn was made, so
    the thread stays readable later: "compare these two" means nothing on its
    own a week afterwards.
    """
    __tablename__ = "conversation_turn"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id"), index=True)
    # Ordering key, per workspace. `created_at` cannot do this job: Windows'
    # clock resolution is ~15ms, so consecutive turns share a timestamp and
    # ordering by it returns the question after the answer.
    seq: Mapped[int] = mapped_column(Integer, default=0, index=True)
    role: Mapped[str] = mapped_column(String(16))          # user | assistant
    text: Mapped[str] = mapped_column(Text, default="")
    # What the turn resolved to: direct | query | research | agent:<code>
    intent: Mapped[str] = mapped_column(String(32), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    execution_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    context_json: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=utcnow, index=True)


# ---------------------------------------------------------------------------
# Intents — the fourth primitive
# ---------------------------------------------------------------------------
# Object = something that exists, Agent = something that works, Space = where
# the work lives, Intent = something the user WANTS. The blueprint's §11.
#
# An Intent is not a task and not a saved prompt. A task completes; an Intent
# keeps standing. What makes it real rather than decorative is `last_checked_at`
# plus the `IntentHit` rows: a monitor that cannot show you what it caught, and
# when it last actually looked, is a checkbox pretending to be a feature.
INTENT_STATUSES = ("active", "paused", "archived")


class Intent(Base):
    __tablename__ = "intent"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)

    # What it watches. Objects are the strong form ("monitor NVIDIA"); keywords
    # catch material that has not become an object yet.
    object_ids_json: Mapped[list] = mapped_column(JSON, default=list)
    keywords_json: Mapped[list] = mapped_column(JSON, default=list)

    # Noise control. An Intent that fires on every low-relevance event trains
    # the user to ignore it, which is worse than not having it.
    relevance_floor: Mapped[str] = mapped_column(String(16), default="medium")
    cadence_minutes: Mapped[int] = mapped_column(Integer, default=60)

    # The honesty columns. `last_checked_at` is NULL until something has
    # genuinely evaluated this Intent, and the UI must say "never checked"
    # rather than implying silence means all-clear.
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                             nullable=True)
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                         nullable=True)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                 onupdate=utcnow)

    __table_args__ = (Index("ix_intent_ws_status", "workspace_id", "status"),)


class IntentHit(Base):
    """One thing an Intent caught.

    `ref_id` points at whatever triggered it (an event, a relationship, a
    claim), and the unique index on (intent, kind, ref) is what stops a poll
    loop from reporting the same headline every hour for a week.
    """
    __tablename__ = "intent_hit"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(32), index=True)
    intent_id: Mapped[str] = mapped_column(ForeignKey("intent.id"), index=True)

    kind: Mapped[str] = mapped_column(String(24), default="event")
    ref_id: Mapped[str] = mapped_column(String(32), default="")
    object_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)

    title: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    relevance: Mapped[str] = mapped_column(String(16), default="medium")
    matched_json: Mapped[dict] = mapped_column(JSON, default=dict)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=utcnow, index=True)

    __table_args__ = (
        Index("ix_intenthit_unique", "intent_id", "kind", "ref_id", unique=True),
    )


class UsageCounter(Base):
    """Per-workspace metering for plan limits and budget thresholds."""
    __tablename__ = "usage_counter"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id"), index=True)
    period: Mapped[str] = mapped_column(String(16), index=True)   # YYYY-MM-DD or YYYY-MM
    metric: Mapped[str] = mapped_column(String(48), index=True)   # executions|tokens|cost_usd
    value: Mapped[float] = mapped_column(Float, default=0.0)

    __table_args__ = (Index("ix_usage_unique", "workspace_id", "period", "metric",
                            unique=True),)


# ---------------------------------------------------------------------------
# TERRA — geospatial intelligence
# ---------------------------------------------------------------------------
# Six tables, and a deliberate absence: there is no geometry column type here.
#
# PostGIS and SpatiaLite were both considered and both rejected for the reason
# the rest of TERRA gives — the spatial workload is a few thousand user-owned
# points, and the query it actually runs is "what is within R metres of here".
# An indexed lat/lon bounding-box pre-filter followed by a haversine refine in
# Python answers that in well under a millisecond at this scale, with no
# extension to install on the laptop this ships to.
#
# The columns are plain floats precisely so that moving to PostGIS later is an
# ALTER and a backfill rather than a redesign: every spatial predicate lives
# behind a function in `terra/geo/spatial.py`, so there is exactly one file to
# change when the row count justifies real GIS.


class GeoCache(Base):
    """The durable tier of the geospatial cache.

    Keyed by the hash `terra.geo.cache.key_for` builds, so one row per distinct
    question. `stored_at` is a float epoch rather than a DateTime because it is
    compared arithmetically against TTLs on the hot path, and round-tripping
    through Python datetimes there was pure overhead.

    This table is what makes degraded mode work at all. Emptying it is always
    safe and never loses user data — everything in it is re-fetchable.
    """
    __tablename__ = "geo_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True, default="")
    provider: Mapped[str] = mapped_column(String(32), default="")
    value_json: Mapped[dict] = mapped_column(JSON, default=dict)
    stored_at: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    ttl_s: Mapped[float] = mapped_column(Float, default=3600.0)


class GeoPlace(Base):
    """A location the user has named — home, college, work, a favourite.

    This is the table that makes the cost rule real: once "college" is here, no
    question mentioning college ever geocodes again. `visit_count` and
    `last_visit_at` are what "frequently visited" is computed from, and both
    are written only while history is enabled.
    """
    __tablename__ = "geo_place"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(32), index=True)

    label: Mapped[str] = mapped_column(String(120), default="")
    #: Normalised label for lookup — "My College" and "my college" are one place.
    slug: Mapped[str] = mapped_column(String(120), index=True, default="")
    kind: Mapped[str] = mapped_column(String(32), default="saved")
    lat: Mapped[float] = mapped_column(Float, index=True)
    lon: Mapped[float] = mapped_column(Float, index=True)
    address: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(64), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    tags_json: Mapped[list] = mapped_column(JSON, default=list)
    meta_json: Mapped[dict] = mapped_column(JSON, default=dict)

    visit_count: Mapped[int] = mapped_column(Integer, default=0)
    last_visit_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=utcnow)

    __table_args__ = (
        Index("ix_geoplace_ws_slug", "workspace_id", "slug", unique=True),
        Index("ix_geoplace_bbox", "workspace_id", "lat", "lon"),
    )


class GeoVisit(Base):
    """One observed location fix.

    The most sensitive table in OMNIX, and the only one whose retention policy
    is enforced in code rather than left to the operator: `terra.geo.memory`
    refuses to write here in privacy mode, and prunes past the configured
    retention on every write.

    Not one row per GPS tick. `memory.observe` collapses fixes within a
    threshold of the previous one into a dwell, so a stationary user produces
    one row rather than one per second.
    """
    __tablename__ = "geo_visit"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(32), index=True)

    lat: Mapped[float] = mapped_column(Float, index=True)
    lon: Mapped[float] = mapped_column(Float, index=True)
    accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    place_id: Mapped[str | None] = mapped_column(String(32), index=True,
                                                 nullable=True)
    label: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(24), default="browser")

    arrived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=utcnow, index=True)
    departed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    dwell_s: Mapped[float] = mapped_column(Float, default=0.0)

    __table_args__ = (Index("ix_geovisit_ws_time", "workspace_id", "arrived_at"),)


class GeoRoute(Base):
    """A route the user actually asked for, and which alternative they took.

    `chosen_index` is the preference-learning signal and the reason this table
    exists: TERRA offers several alternatives, and which one the user takes —
    repeatedly, against the one the scorer ranked first — is the only honest
    evidence of what they prefer. `factors_json` records the scoring inputs at
    the time, so a learned preference can be attributed rather than guessed at.
    """
    __tablename__ = "geo_route"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(32), index=True)

    origin_lat: Mapped[float] = mapped_column(Float)
    origin_lon: Mapped[float] = mapped_column(Float)
    dest_lat: Mapped[float] = mapped_column(Float)
    dest_lon: Mapped[float] = mapped_column(Float)
    origin_label: Mapped[str] = mapped_column(Text, default="")
    dest_label: Mapped[str] = mapped_column(Text, default="")

    mode: Mapped[str] = mapped_column(String(16), default="driving")
    provider: Mapped[str] = mapped_column(String(32), default="")
    distance_m: Mapped[float] = mapped_column(Float, default=0.0)
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    alternatives: Mapped[int] = mapped_column(Integer, default=1)
    chosen_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    factors_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=utcnow, index=True)


class Geofence(Base):
    """A watched area. Circle or polygon, never both.

    `shape` decides which columns mean anything — a circle uses lat/lon/radius,
    a polygon uses `polygon_json`. Keeping both in one table rather than two
    keeps evaluation a single query, which matters because it runs on every
    position update.
    """
    __tablename__ = "geofence"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(32), index=True)

    label: Mapped[str] = mapped_column(String(120), default="")
    shape: Mapped[str] = mapped_column(String(16), default="circle")
    lat: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    lon: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    radius_m: Mapped[float] = mapped_column(Float, default=200.0)
    polygon_json: Mapped[list] = mapped_column(JSON, default=list)

    #: enter|exit|both — which transition fires.
    trigger: Mapped[str] = mapped_column(String(16), default="both")
    #: What OMNIX should do. Deliberately an instruction in words rather than a
    #: command: it reaches the agent layer as intent and is never executed as a
    #: string by anything.
    action: Mapped[str] = mapped_column(Text, default="notify")
    action_payload_json: Mapped[dict] = mapped_column(JSON, default=dict)

    active: Mapped[bool] = mapped_column(Boolean, default=True)
    #: Last known containment, so a TRANSITION is detected rather than the same
    #: state re-firing on every poll.
    inside: Mapped[bool] = mapped_column(Boolean, default=False)
    last_event_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=utcnow)


class GeofenceEvent(Base):
    """A crossing that happened. Append-only; the agent layer reads it."""
    __tablename__ = "geofence_event"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(32), index=True)
    geofence_id: Mapped[str] = mapped_column(ForeignKey("geofence.id"), index=True)

    transition: Mapped[str] = mapped_column(String(16), default="enter")
    lat: Mapped[float] = mapped_column(Float, default=0.0)
    lon: Mapped[float] = mapped_column(Float, default=0.0)
    label: Mapped[str] = mapped_column(Text, default="")
    dispatched: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=utcnow, index=True)


class GeoPreference(Base):
    """Declared and learned routing preferences, per workspace.

    One row per (workspace, key). `weight` is the scoring coefficient and
    `locked` means the user set it themselves — a locked preference is never
    overwritten by learning, which is what "subject to explicit user control"
    has to mean if it is to mean anything.
    """
    __tablename__ = "geo_preference"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(32), index=True)
    key: Mapped[str] = mapped_column(String(48), index=True)
    weight: Mapped[float] = mapped_column(Float, default=0.0)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    observations: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("ix_geopref_unique", "workspace_id", "key",
                            unique=True),)
