"""Artifacts — typed, referenceable outputs.

This module is the whole cross-agent story. Before it, an agent's result was a
`blocks` array inside a job JSON: renderable, but not addressable, so "send this
research to FORGE" had nowhere to point. An artifact is that address.

Two rules make the handoff generic rather than per-agent glue:

  * Content is opaque here. The envelope (id, type, title, provenance) is
    uniform; what is inside `content` is the producing agent's business and the
    renderer registry's problem. Adding an agent must never require changing
    this file.
  * Edits fork rather than mutate. `revise()` writes a new row pointing at its
    parent, so an artifact that fed a completed run keeps saying what it said
    when that run consumed it. Provenance you can rewrite is not provenance.
"""

from __future__ import annotations

from sqlalchemy import select

from .db import session
from .schema import Artifact, ArtifactRef, iso


def create(workspace_id: str, type: str, title: str, content: dict,
           *, source_agent: str = "", execution_id: str | None = None,
           tags: list[str] | None = None,
           references: list[tuple[str, str]] | None = None) -> dict:
    """Create an artifact.

    `references` is a list of (to_artifact_id, relation) pairs — typically
    [("<report id>", "derived_from")] when one agent consumes another's output.
    """
    with session() as s:
        a = Artifact(
            workspace_id=workspace_id, type=type, title=(title or "")[:300],
            content_json=content or {}, source_agent=source_agent,
            execution_id=execution_id, tags_json=list(tags or []),
        )
        s.add(a)
        s.flush()
        for to_id, relation in (references or []):
            s.add(ArtifactRef(from_artifact_id=a.id, to_artifact_id=to_id,
                              relation=relation))
        s.flush()
        return _public(a)


def revise(artifact_id: str, content: dict, *, title: str | None = None,
           execution_id: str | None = None) -> dict | None:
    """Fork an artifact into a new version. The original stays intact."""
    with session() as s:
        old = s.get(Artifact, artifact_id)
        if old is None:
            return None
        new = Artifact(
            workspace_id=old.workspace_id, type=old.type,
            title=(title if title is not None else old.title),
            content_json=content or {}, source_agent=old.source_agent,
            execution_id=execution_id or old.execution_id,
            tags_json=list(old.tags_json or []),
            version=(old.version or 1) + 1, parent_id=old.id,
        )
        s.add(new)
        s.flush()
        s.add(ArtifactRef(from_artifact_id=new.id, to_artifact_id=old.id,
                          relation="revision_of"))
        s.flush()
        return _public(new)


def get(artifact_id: str, *, with_content: bool = True) -> dict | None:
    with session() as s:
        a = s.get(Artifact, artifact_id)
        if a is None:
            return None
        out = _public(a, with_content=with_content)
        out["references"] = _refs_of(s, a.id)
        return out


def list_for(workspace_id: str, *, type: str | None = None,
             execution_id: str | None = None, limit: int = 100) -> list[dict]:
    """Envelope only — content is deliberately omitted so a workspace listing
    does not drag every research report through memory."""
    with session() as s:
        q = select(Artifact).where(Artifact.workspace_id == workspace_id)
        if type:
            q = q.where(Artifact.type == type)
        if execution_id:
            q = q.where(Artifact.execution_id == execution_id)
        rows = s.scalars(q.order_by(Artifact.created_at.desc()).limit(limit)).all()
        return [_public(a, with_content=False) for a in rows]


def link(from_artifact_id: str, to_artifact_id: str,
         relation: str = "references") -> None:
    with session() as s:
        s.add(ArtifactRef(from_artifact_id=from_artifact_id,
                          to_artifact_id=to_artifact_id, relation=relation))


def lineage(artifact_id: str) -> dict:
    """Both directions of the reference graph.

    `derived_from` walks back to the research a change came from; `used_by`
    walks forward to everything built on it. This is what a provenance panel
    and, later, requirement traceability both read.
    """
    with session() as s:
        out_edges = s.scalars(
            select(ArtifactRef).where(ArtifactRef.from_artifact_id == artifact_id)).all()
        in_edges = s.scalars(
            select(ArtifactRef).where(ArtifactRef.to_artifact_id == artifact_id)).all()
        ids = {e.to_artifact_id for e in out_edges} | {e.from_artifact_id for e in in_edges}
        titles = {}
        if ids:
            for a in s.scalars(select(Artifact).where(Artifact.id.in_(ids))).all():
                titles[a.id] = {"id": a.id, "type": a.type, "title": a.title,
                                "source_agent": a.source_agent}
        return {
            "references": [{**titles.get(e.to_artifact_id, {"id": e.to_artifact_id}),
                            "relation": e.relation} for e in out_edges],
            "referenced_by": [{**titles.get(e.from_artifact_id, {"id": e.from_artifact_id}),
                               "relation": e.relation} for e in in_edges],
        }


def delete(artifact_id: str) -> bool:
    with session() as s:
        a = s.get(Artifact, artifact_id)
        if a is None:
            return False
        s.delete(a)
        return True


def _refs_of(s, artifact_id: str) -> list[dict]:
    rows = s.scalars(
        select(ArtifactRef).where(ArtifactRef.from_artifact_id == artifact_id)).all()
    return [{"to": r.to_artifact_id, "relation": r.relation} for r in rows]


def _public(a: Artifact, with_content: bool = True) -> dict:
    out = {
        "id": a.id,
        "workspaceId": a.workspace_id,
        "type": a.type,
        "title": a.title,
        "sourceAgent": a.source_agent,
        "executionId": a.execution_id,
        "version": a.version,
        "parentId": a.parent_id,
        "tags": a.tags_json or [],
        "createdAt": iso(a.created_at),
        "updatedAt": iso(a.updated_at),
    }
    if with_content:
        out["content"] = a.content_json or {}
    return out
