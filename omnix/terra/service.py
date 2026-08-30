"""TERRA Intelligence service — state, refresh loop, and the job runner.

One process-wide object holds the derived state (corpus, index, graph, clusters,
risk, alerts) so an HTTP request never triggers a crawl. The pipeline runs on a
background thread and requests read whatever the last pass produced, which is
what keeps the map responsive while thirty RSS feeds are being fetched.

Pipeline, in order, because each stage consumes the previous one's output:

    ingest -> analyze -> persist -> graph -> index -> cluster -> risk -> detect

Only the LLM-heavy products (six-analyst analysis, situation reports, what-if
simulations) run as jobs, because they take tens of seconds and the console
streams their progress. Everything else is a synchronous read off cached state.

Nothing here raises into a request handler. A failed refresh leaves the previous
state intact and is reported through `status()`, because stale intelligence
clearly labeled as stale is more useful than an error page.
"""

from __future__ import annotations

import itertools
import json
import threading
import time
import traceback
from collections import deque

from . import (agents, cluster as cluster_mod, country as country_mod, detect,
               extract, graph as graph_mod, ingest, layers as layers_mod, nlp,
               ontology as onto, predict, reports, risk as risk_mod, search as
               search_mod, store as store_mod, verify as verify_mod)

REFRESH_MINUTES = 15
# Cluster/rank over a shorter window than the corpus retains: events older than
# this are history, and including them makes "what is happening now" wrong.
ACTIVE_WINDOW_HOURS = 60.0


class TerraService:
    def __init__(self):
        self._lock = threading.RLock()
        self.store = store_mod.shared()
        self.graph = graph_mod.shared()
        self.index = nlp.TfIdf()
        self.clusters: dict[str, dict] = {}
        self.ranked: list[dict] = []
        self.risk: dict[str, dict] = {}
        self.prev_risk: dict[str, dict] = {}
        self.risk_deltas: dict[str, float] = {}
        self.alerts: list[dict] = []
        self.analysis: dict | None = None
        self.last_refresh = 0.0
        self.last_error = ""
        self.refreshing = False
        self.refresh_count = 0
        self.timings: dict[str, float] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._boot_index()

    # -- boot ---------------------------------------------------------------
    def _boot_index(self) -> None:
        """Rebuild the derived state from the persisted corpus on startup, so a
        restart doesn't show an empty map for fifteen minutes."""
        articles = self.store.recent(ACTIVE_WINDOW_HOURS)
        if not articles:
            return
        try:
            for art in articles:
                self.index.add(art["id"], art["title"] + " " +
                               art.get("summary", ""))
            self._derive(articles)
            # Adopt the persisted corpus's age rather than leaving last_refresh
            # at zero. Otherwise a restart reports "refreshed never" over data
            # that is in fact minutes old, which reads as a broken pipeline.
            self.last_refresh = self.store.meta.get("last_ingest", 0.0)
        except Exception:
            self.last_error = traceback.format_exc(limit=3)

    # -- pipeline -----------------------------------------------------------
    def refresh(self, use_llm_extraction: bool = True) -> dict:
        """One full pass. Returns a summary of what changed."""
        with self._lock:
            if self.refreshing:
                return {"status": "busy", "last_refresh": self.last_refresh}
            self.refreshing = True
        started = time.time()
        timings: dict[str, float] = {}
        result = {"status": "ok"}
        try:
            t = time.time()
            fresh = ingest.run()
            timings["ingest"] = round(time.time() - t, 2)

            t = time.time()
            extract.analyze_all(fresh)
            timings["extract"] = round(time.time() - t, 2)

            t = time.time()
            new_count = 0
            for art in fresh:
                _, is_new = self.store.upsert(art)
                new_count += int(is_new)
            dropped = self.store.prune()
            for aid in dropped:
                self.index.remove(aid)
            timings["store"] = round(time.time() - t, 2)

            # LLM relationship extraction only on genuinely new articles — the
            # rest already contributed their triples on a previous pass.
            triples: list[dict] = []
            if use_llm_extraction and new_count:
                t = time.time()
                try:
                    only_new = [a for a in fresh if a["id"] in self.store.articles
                                and self.store.articles[a["id"]].get("seen_count", 1) == 1]
                    triples = extract.llm_relations(only_new or fresh)
                except Exception:
                    triples = []
                timings["llm_extract"] = round(time.time() - t, 2)

            t = time.time()
            self.graph.ingest_articles(fresh, triples)
            self.graph.prune(dropped)
            self.graph.save()
            timings["graph"] = round(time.time() - t, 2)

            t = time.time()
            for art in fresh:
                self.index.add(art["id"], art["title"] + " " +
                               art.get("summary", ""))
            self.index.build()
            timings["index"] = round(time.time() - t, 2)

            active = self.store.recent(ACTIVE_WINDOW_HOURS)
            self._derive(active, timings)

            self.store.meta["last_ingest"] = time.time()
            self.store.save(force=True)

            result.update({
                "fetched": len(fresh), "new": new_count,
                "dropped": len(dropped), "triples": len(triples),
                "articles": len(self.store.articles),
                "clusters": len(self.clusters), "alerts": len(self.alerts),
            })
            self.last_error = ""
            self.refresh_count += 1
        except Exception:
            self.last_error = traceback.format_exc(limit=4)
            result = {"status": "error", "error": self.last_error.split("\n")[-2]
                      if "\n" in self.last_error else self.last_error}
        finally:
            timings["total"] = round(time.time() - started, 2)
            with self._lock:
                self.timings = timings
                self.last_refresh = time.time()
                self.refreshing = False
        result["timings"] = timings
        return result

    def _derive(self, active: list[dict], timings: dict | None = None) -> None:
        """Clusters, risk and alerts from the active window."""
        timings = timings if timings is not None else {}
        t = time.time()
        clusters = cluster_mod.build(active, self.index)
        ranked = cluster_mod.rank(clusters, limit=80)
        timings["cluster"] = round(time.time() - t, 2)

        t = time.time()
        scores = risk_mod.compute(active)
        timings["risk"] = round(time.time() - t, 2)

        t = time.time()
        alerts = detect.detect(ranked, active)
        timings["detect"] = round(time.time() - t, 2)

        # Stories become graph nodes AFTER ranking, so only events that made the
        # cut are admitted — putting all 790 clusters in would swamp the graph
        # with singletons nobody will ever expand.
        try:
            self.graph.ingest_clusters(ranked)
        except Exception:
            pass

        with self._lock:
            self.clusters = clusters
            self.ranked = ranked
            self.prev_risk = self.risk
            self.risk = scores
            self.risk_deltas = risk_mod.deltas(scores, self.prev_risk)
            self.alerts = alerts

    # -- background loop ----------------------------------------------------
    def start(self, interval_minutes: int = REFRESH_MINUTES) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def loop():
            # First pass immediately: on a cold install there is no corpus and
            # every panel would be empty until the first interval elapsed.
            if not self.store.articles or \
                    time.time() - self.store.meta.get("last_ingest", 0) > 1800:
                self.refresh()
            while not self._stop.wait(interval_minutes * 60):
                self.refresh()

        self._thread = threading.Thread(target=loop, name="terra-refresh",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # -- reads --------------------------------------------------------------
    def articles(self, hours: float = ACTIVE_WINDOW_HOURS) -> list[dict]:
        return self.store.recent(hours)

    def by_id(self) -> dict[str, dict]:
        return {a["id"]: a for a in self.store.all()}

    def status(self) -> dict:
        stats = self.store.stats()
        graph_stats = self.graph.stats()
        return {
            "corpus": stats,
            "graph": graph_stats,
            "clusters": len(self.clusters),
            "events_ranked": len(self.ranked),
            "countries_scored": len(self.risk),
            "alerts": len(self.alerts),
            "last_refresh": self.last_refresh,
            "refresh_count": self.refresh_count,
            "refreshing": self.refreshing,
            "age_minutes": round((time.time() - self.last_refresh) / 60, 1)
                           if self.last_refresh else None,
            "interval_minutes": REFRESH_MINUTES,
            "window_hours": ACTIVE_WINDOW_HOURS,
            "error": self.last_error.split("\n")[-2].strip()
                     if self.last_error and "\n" in self.last_error else self.last_error,
            "timings": self.timings,
            "llm": _llm_available(),
            "sources": sorted(self.store.meta.get("sources_seen", {}),
                              key=lambda s: -self.store.meta["sources_seen"][s])[:30],
        }

    def overview(self) -> dict:
        """Everything the console's landing view needs, in one call."""
        return {
            "events": self.ranked[:30],
            "alerts": self.alerts,
            "alert_lines": [detect.alert_line(a) for a in self.alerts[:6]],
            "risk": risk_mod.summary(self.risk),
            "risk_deltas": self.risk_deltas,
            "graph": {
                "top": self.graph.top_nodes(limit=18),
                "stats": self.graph.stats(),
            },
            "domains": extract.DOMAINS,
            "status": self.status(),
        }

    def heatmap(self) -> dict:
        return {
            "scores": self.risk,
            "deltas": self.risk_deltas,
            "summary": risk_mod.summary(self.risk),
        }

    def event(self, cluster_id: str, deep: bool = False) -> dict:
        cluster = self.clusters.get(cluster_id)
        if cluster is None:
            return {"status": "unknown", "error": f"no such event: {cluster_id}"}
        ranked = next((c for c in self.ranked if c["id"] == cluster_id), cluster)
        by_id = self.by_id()
        payload = {
            "status": "ok",
            "event": ranked,
            "timeline": cluster_mod.timeline(cluster, by_id),
            "articles": [by_id[a] for a in cluster["article_ids"] if a in by_id],
            "subgraph": self.graph.subgraph(
                [e["id"] for e in cluster.get("entities", [])[:5]],
                hops=1, max_nodes=40),
        }
        if deep:
            payload["verification"] = verify_mod.verify(cluster, by_id)
            payload["predictions"] = predict.predict_event(self.graph, ranked, by_id)
            payload["economics"] = predict.economic_impact(ranked, self.graph)
            payload["history"] = reports.analogues(ranked)
        return payload

    def search(self, query: str, synthesize: bool = True) -> dict:
        result = search_mod.search(query, self.articles(168.0), self.index,
                                   self.graph)
        if synthesize and result["results"]:
            result["synthesis"] = search_mod.synthesize(query, result["results"])
        return result

    def country_card(self, iso: str) -> dict:
        return country_mod.card(iso, self.articles(168.0), self.risk, self.graph)

    def layers(self, keys: list[str] | None = None) -> dict:
        return layers_mod.all_layers(self.articles(72.0), keys)

    def brief(self, fmt: str) -> dict:
        return reports.brief(fmt, self.ranked, self.risk, self.alerts,
                             self.analysis)

    def graph_view(self, node_id: str | None = None, hops: int = 1) -> dict:
        if node_id:
            node = self.graph.node(node_id)
            if node is None:
                return {"status": "unknown", "error": f"no such node: {node_id}"}
            by_id = self.by_id()
            return {
                "status": "ok",
                "focus": self.graph._public(node),
                "neighbors": self.graph.neighbors(node_id, limit=30),
                "subgraph": self.graph.subgraph([node_id], hops=hops,
                                                max_nodes=70),
                "articles": [by_id[a] for a in node.get("articles", [])[-14:]
                             if a in by_id],
            }
        roots = [n["id"] for n in self.graph.top_nodes(limit=10)]
        return {"status": "ok", "focus": None,
                "subgraph": self.graph.subgraph(roots, hops=1, max_nodes=80),
                "top": self.graph.top_nodes(limit=24)}

    # -- progressive graph exploration ---------------------------------------
    def seed_view(self, node_id: str | None, degree: int = 8) -> dict:
        """The opening state of the explorer: one focus and its best neighbours.

        Deliberately NOT the whole graph. A 500-node hairball tells a reader
        nothing and takes a GPU to draw; one entity plus the eight relationships
        that explain it is a starting point they can actually read, and every
        further node arrives because they asked for it.
        """
        kg = self.graph
        if not node_id:
            top = kg.top_nodes(limit=1)
            node_id = top[0]["id"] if top else ""
        node = kg.node(node_id) if node_id else None
        if node is None:
            return {"status": "empty", "nodes": [], "edges": [],
                    "focus": None, "legend": onto.VISUAL}

        focus = kg._public(node)
        edges_out = kg.expand(node_id, limit=degree)
        nodes = {node_id: {**focus, "focus": True, "depth": 0, "expanded": True,
                           "remaining": kg.degree_of(node_id)}}
        edges = []
        for e in edges_out:
            other = e["node"]
            nodes.setdefault(other["id"], {
                **other, "depth": 1, "expanded": False,
                "remaining": max(0, kg.degree_of(other["id"], {node_id}))})
            edges.append(_edge_payload(node_id, other["id"], e))
            for extra in e.get("also", []):
                edges.append(_edge_payload(node_id, other["id"], extra))
        nodes[node_id]["remaining"] = max(0, nodes[node_id]["remaining"] - len(edges_out))
        return {
            "status": "ok",
            "focus": focus,
            "nodes": list(nodes.values()),
            "edges": edges,
            "legend": onto.VISUAL,
            "relations": {k: v["label"] for k, v in onto.RELATIONS.items()},
        }

    def expand_node(self, node_id: str, have: list[str], degree: int = 6,
                    relations: list[str] | None = None,
                    types: list[str] | None = None) -> dict:
        """One click-to-expand step. Returns only what is NEW.

        `have` is what the client already has on screen. Sending the delta keeps
        the payload small and, more importantly, lets the client animate the
        arrival of exactly the new nodes instead of re-laying-out everything.
        """
        kg = self.graph
        node = kg.node(node_id)
        if node is None:
            return {"status": "unknown", "nodes": [], "edges": []}
        known = set(have or [])
        picked = kg.expand(node_id, limit=degree, exclude=known,
                           relations=relations, types=types)

        nodes, edges = [], []
        for e in picked:
            other = e["node"]
            nodes.append({**other, "depth": 1, "expanded": False,
                          "remaining": max(0, kg.degree_of(other["id"], known | {node_id}))})
            edges.append(_edge_payload(node_id, other["id"], e))
            for extra in e.get("also", []):
                edges.append(_edge_payload(node_id, other["id"], extra))

        # Also return edges BETWEEN the new nodes and what is already on screen.
        # Without this the graph grows as a tree, when the interesting thing
        # about a knowledge graph is where the branches reconnect.
        new_ids = {n["id"] for n in nodes}
        for nid in new_ids:
            for e in kg.neighbors(nid, limit=40):
                other_id = e["node"]["id"]
                if other_id == node_id:
                    continue
                if other_id in known or (other_id in new_ids and other_id > nid):
                    edges.append(_edge_payload(nid, other_id, e))

        return {"status": "ok", "nodes": nodes, "edges": edges,
                "exhausted": len(picked) < degree,
                "remaining": max(0, kg.degree_of(node_id, known) - len(nodes))}

    def entity(self, node_id: str) -> dict:
        """Everything the right-hand intelligence panel shows for one entity.

        One endpoint for every entity kind, because the panel is the same panel
        whether the user arrived from the map, the graph, the timeline or the
        news feed — and the views can only stay synchronised if they all resolve
        a selection the same way.
        """
        kg = self.graph
        node = kg.node(node_id)
        if node is None:
            # Only the most significant clusters are promoted to graph nodes, but
            # the timeline offers EVERY event in the window as selectable — so an
            # event the graph never took up would resolve to nothing and the
            # panel would show a bare 404 next to a story the reader can plainly
            # see. Anything the views offer has to be inspectable, so a story
            # selection falls back to the cluster it came from.
            story = self._story_entity(node_id)
            if story is not None:
                return story
            return {"status": "unknown", "id": node_id}
        pub = kg._public(node)
        # The forms this entity is actually written as in copy. The news view
        # matches raw headlines against these, which the canonical name alone
        # cannot do — no headline says "United States of America".
        pub["aliases"] = onto.surface_forms(
            pub.get("name", ""), pub.get("type", ""), pub.get("iso2", ""))
        by_id = self.by_id()

        neighbors = kg.expand(node_id, limit=18)
        rel_groups: dict[str, list] = {}
        for e in neighbors:
            rel_groups.setdefault(e["relation"], []).append({
                "id": e["node"]["id"], "name": e["node"]["name"],
                "vclass": e["node"]["vclass"], "color": e["node"]["color"],
                "glyph": e["node"]["glyph"], "weight": e["weight"],
                "sentiment": e["sentiment"], "count": e["count"],
                "static": e["static"], "llm": e["llm"],
                "articles": e["articles"],
            })

        articles = [by_id[a] for a in node.get("articles", [])[-24:] if a in by_id]
        articles.sort(key=lambda a: -a.get("published_ts", 0))

        # Stories the entity appears in, for the timeline view.
        stories = []
        for cluster in self.ranked[:80]:
            if any(e["id"] == node_id for e in cluster.get("entities", [])):
                stories.append({
                    "id": cluster["id"], "title": cluster["title"],
                    "url": cluster.get("url", ""),
                    "first_ts": cluster["first_ts"], "last_ts": cluster["last_ts"],
                    "size": cluster["size"], "sources": cluster["source_count"],
                    "sentiment": cluster["sentiment"],
                    "corroboration": cluster["corroboration"],
                    "domains": cluster.get("domains", []),
                    "status": cluster.get("status", {}).get("state", ""),
                })
        stories.sort(key=lambda s: -s["last_ts"])

        payload = {
            "status": "ok",
            "node": pub,
            "relations": rel_groups,
            "relation_labels": {k: onto.RELATIONS[k]["label"] for k in rel_groups},
            "articles": articles[:14],
            "article_count": len(node.get("articles", [])),
            "stories": stories[:12],
            "risk": None,
            "sentiment_series": _sentiment_series(articles),
        }
        if pub["type"] == "country" and pub.get("iso2"):
            payload["risk"] = self.risk.get(pub["iso2"])
        if pub["type"] in ("news_story", "conflict") and node.get("cluster"):
            cluster = self.clusters.get(node["cluster"])
            if cluster:
                payload["event"] = next(
                    (c for c in self.ranked if c["id"] == cluster["id"]), cluster)
                payload["timeline"] = cluster_mod.timeline(cluster, by_id)
        return payload

    def _story_entity(self, node_id: str) -> dict | None:
        """The entity panel for a story that has no graph node of its own.

        Same shape as entity(), built from the cluster instead of the graph, so
        the panel renders it without knowing the difference. The cluster's own
        entities become the relations, which is genuinely what the reader wants
        from a story: who is in it.
        """
        prefix, _, cid = (node_id or "").partition(":")
        if prefix not in ("news_story", "conflict") or not cid:
            return None
        cluster = self.clusters.get(cid) or next(
            (c for c in self.ranked if c["id"] == cid), None)
        if cluster is None:
            return None

        vis = onto.visual_of(prefix)
        pub = {
            "id": node_id, "type": prefix, "name": cluster.get("title", cid),
            "glyph": vis["glyph"], "color": vis["color"],
            "type_label": onto.TYPES.get(prefix, {}).get("label", prefix),
            "vclass": onto.visual_class(prefix), "vlabel": vis["label"],
            "shape": vis["shape"], "vweight": vis["weight"], "ring": vis["ring"],
            "mentions": cluster.get("size", 0),
            "degree": len(cluster.get("entities", []) or []),
            "importance": float(cluster.get("score", 0.0) or 0.0),
            "cluster": cid, "url": cluster.get("url", ""),
            "sentiment": cluster.get("sentiment", 0.0),
            "severity": cluster.get("severity", 0.0),
            "aliases": [],
            # Not in the graph, and the panel should be honest about that rather
            # than implying a promotion that never happened.
            "provisional": True,
        }

        by_id = self.by_id()
        art_ids = cluster.get("article_ids", []) or []
        articles = [by_id[a] for a in art_ids if a in by_id]
        articles.sort(key=lambda a: -a.get("published_ts", 0))

        rel_groups: dict[str, list] = {}
        for ent in (cluster.get("entities", []) or []):
            ev = onto.visual_of(ent.get("type", ""), ent.get("sector", ""))
            rel_groups.setdefault("involved_in", []).append({
                "id": ent["id"], "name": ent.get("name", ent["id"]),
                "vclass": onto.visual_class(ent.get("type", ""), ent.get("sector", "")),
                "color": ev["color"], "glyph": ev["glyph"],
                "weight": float(ent.get("count", 1) or 1),
                "sentiment": cluster.get("sentiment", 0.0),
                "count": ent.get("count", 1), "static": False, "llm": False,
                "articles": [],
            })
        for group in rel_groups.values():
            group.sort(key=lambda r: -r["weight"])

        return {
            "status": "ok",
            "node": pub,
            "relations": rel_groups,
            "relation_labels": {k: onto.RELATIONS[k]["label"] for k in rel_groups
                                if k in onto.RELATIONS},
            "articles": articles[:14],
            "article_count": len(art_ids),
            "stories": [],
            "risk": None,
            "sentiment_series": _sentiment_series(articles),
            "event": cluster,
            "timeline": cluster_mod.timeline(cluster, by_id),
        }

    def timeline_view(self, hours: float = 72.0, limit: int = 60,
                      node_id: str | None = None) -> dict:
        """Events on a time axis, optionally filtered to one entity."""
        events = self.ranked
        if node_id:
            events = [c for c in events
                      if any(e["id"] == node_id for e in c.get("entities", []))]
        events = events[:limit]
        now = time.time()
        cutoff = now - hours * 3600
        lanes = {d: [] for d in extract.DOMAINS}
        for c in events:
            if c["last_ts"] < cutoff:
                continue
            domain = (c.get("domains") or ["news"])[0]
            lanes.setdefault(domain, []).append({
                "id": c["id"],
                "node_id": _story_node_id(c),
                "title": c["title"], "url": c.get("url", ""),
                "first_ts": c["first_ts"], "last_ts": c["last_ts"],
                "size": c["size"], "sources": c["source_count"],
                "severity": c["severity"], "sentiment": c["sentiment"],
                "corroboration": c["corroboration"],
                "countries": c.get("countries", []),
                "status": c.get("status", {}).get("state", ""),
                "entities": [{"id": e["id"], "name": e["name"],
                              "vclass": onto.visual_class(e["type"])}
                             for e in c.get("entities", [])[:5]],
            })
        return {
            "lanes": [{"domain": d, **extract.DOMAINS[d], "events": ev}
                      for d, ev in lanes.items() if ev],
            "window_hours": hours,
            "from_ts": cutoff, "to_ts": now,
            "filtered_to": node_id or "",
            "total": sum(len(ev) for ev in lanes.values()),
        }

    def relationship_view(self, relations: list[str] | None = None,
                          types: list[str] | None = None,
                          limit: int = 120) -> dict:
        """The strongest relationships in the graph, as an inspectable list.

        The graph view answers "what is connected to this"; this answers "what
        are the strongest connections of this kind anywhere", which is a
        different question and a bad fit for a node-link diagram.
        """
        kg = self.graph
        seen: set[tuple] = set()
        rows = []
        for src, rels in kg.adj.items():
            src_node = kg.node(src)
            if src_node is None:
                continue
            for rel, targets in rels.items():
                if relations and rel not in relations:
                    continue
                for dst, rec in targets.items():
                    key = tuple(sorted((src, dst))) + (rel,)
                    if key in seen:
                        continue
                    seen.add(key)
                    dst_node = kg.node(dst)
                    if dst_node is None:
                        continue
                    a, b = kg._public(src_node), kg._public(dst_node)
                    if types and not (a["vclass"] in types or b["vclass"] in types):
                        continue
                    rows.append({
                        "source": {"id": a["id"], "name": a["name"],
                                   "vclass": a["vclass"], "color": a["color"],
                                   "glyph": a["glyph"]},
                        "target": {"id": b["id"], "name": b["name"],
                                   "vclass": b["vclass"], "color": b["color"],
                                   "glyph": b["glyph"]},
                        "relation": rel,
                        "label": onto.RELATIONS[rel]["label"],
                        "weight": round(rec["weight"], 2),
                        "count": rec["count"],
                        "sentiment": rec.get("sentiment", 0.0),
                        "static": bool(rec.get("static")),
                        "llm": bool(rec.get("llm")),
                        "articles": list(rec.get("articles", []))[:6],
                    })
        rows.sort(key=lambda r: -r["weight"])
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["relation"]] = counts.get(r["relation"], 0) + 1
        return {"rows": rows[:limit], "total": len(rows),
                "by_relation": counts,
                "relations": {k: v["label"] for k, v in onto.RELATIONS.items()},
                "legend": onto.VISUAL}

    def communities_view(self, limit: int = 10) -> dict:
        """Detected communities over the salient part of the graph."""
        kg = self.graph
        scope = [n["id"] for n in kg.top_nodes(limit=180, hours=168)]
        labels = kg.communities(scope)
        return {"communities": kg.community_summary(labels, limit=limit),
                "labels": labels, "scope": len(scope)}


def _edge_payload(src: str, dst: str, edge: dict) -> dict:
    """Wire shape for one edge.

    Accepts both a full neighbour record (`relation_label`) and the slimmed
    secondary-relation record from graph.expand (`label`), because both end up
    on the same wire and the client should not have to know which is which.
    """
    return {"source": src, "target": dst, "relation": edge["relation"],
            "label": edge.get("relation_label") or edge.get("label", ""),
            "weight": edge["weight"],
            "count": edge["count"], "sentiment": edge["sentiment"],
            "static": edge["static"], "llm": edge["llm"],
            "articles": edge.get("articles", [])[:6],
            "relevance": edge.get("relevance", edge["weight"])}


def _story_node_id(cluster: dict) -> str:
    domains = cluster.get("domains") or []
    is_conflict = (domains and domains[0] == "military"
                   and cluster.get("severity", 0) >= 0.45)
    return f"{'conflict' if is_conflict else 'news_story'}:{cluster['id']}"


def _sentiment_series(articles: list[dict], buckets: int = 12) -> list[dict]:
    """Hourly tone series for the entity panel sparkline."""
    if not articles:
        return []
    now = time.time()
    span = 72 * 3600
    out = []
    for i in range(buckets):
        lo = now - span * (buckets - i) / buckets
        hi = now - span * (buckets - i - 1) / buckets
        window = [a for a in articles if lo <= a.get("published_ts", 0) < hi]
        out.append({
            "ts": hi,
            "count": len(window),
            "sentiment": round(sum(a.get("sentiment", 0) for a in window)
                               / len(window), 3) if window else 0.0,
        })
    return out


def _llm_available() -> bool:
    try:
        from ..config import cloud_active, local_fallback_enabled
        return bool(cloud_active() or local_fallback_enabled())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Job runner for the long LLM products
#
# Deliberately minimal and local to TERRA rather than reusing squad/jobs.py:
# that manager is built around Unit objects with a fixed result shape, and these
# products return four different shapes. Same SSE contract on the wire, so the
# frontend's existing event handling works unchanged.
# ---------------------------------------------------------------------------
_ids = itertools.count(1)


class Job:
    def __init__(self, kind: str, params: dict):
        self.id = f"tj_{int(time.time())}_{next(_ids)}"
        self.kind = kind
        self.params = params
        self.status = "running"
        self.created = time.time()
        self.finished = 0.0
        self.result: dict | None = None
        self.error = ""
        self.events: deque = deque(maxlen=200)
        self.done = threading.Event()

    def emit(self, stage: str, detail: str = "") -> None:
        self.events.append({"stage": stage, "detail": detail,
                            "at": time.time()})

    def public(self, with_result: bool = False) -> dict:
        data = {"id": self.id, "kind": self.kind, "status": self.status,
                "created": self.created, "finished": self.finished,
                "elapsed": round((self.finished or time.time()) - self.created, 1),
                "error": self.error, "params": self.params}
        if with_result:
            data["result"] = self.result
        return data


class JobManager:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def start(self, kind: str, params: dict, fn) -> Job:
        job = Job(kind, params)
        with self._lock:
            self._jobs[job.id] = job
            # Bounded history — these hold full report payloads.
            if len(self._jobs) > 40:
                for old in sorted(self._jobs.values(),
                                  key=lambda j: j.created)[:10]:
                    self._jobs.pop(old.id, None)

        def run():
            try:
                job.emit("start", f"{kind} started")
                job.result = fn(job.emit)
                job.status = "done"
                job.emit("done", "complete")
            except Exception as exc:
                job.status = "error"
                job.error = f"{type(exc).__name__}: {exc}"
                job.emit("error", job.error)
            finally:
                job.finished = time.time()
                job.done.set()

        threading.Thread(target=run, name=f"terra-{kind}", daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[dict]:
        return [j.public() for j in
                sorted(self._jobs.values(), key=lambda j: -j.created)[:20]]

    def stream(self, job_id: str, poll_timeout: float = 30.0):
        """SSE generator: replays what already happened, then follows live."""
        job = self.get(job_id)
        if job is None:
            yield ("event: error\ndata: " +
                   json.dumps({"message": "unknown job"}) + "\n\n")
            return
        seen = 0
        deadline = time.time() + poll_timeout
        while True:
            events = list(job.events)
            while seen < len(events):
                ev = events[seen]
                seen += 1
                yield (f"event: {ev['stage']}\ndata: " +
                       json.dumps(ev) + "\n\n")
                deadline = time.time() + poll_timeout
            if job.done.is_set() and seen >= len(list(job.events)):
                yield ("event: result\ndata: " +
                       json.dumps(job.public(with_result=True)) + "\n\n")
                return
            if time.time() > deadline:
                yield "event: ping\ndata: {}\n\n"
                deadline = time.time() + poll_timeout
            time.sleep(0.25)


manager = JobManager()

_service: TerraService | None = None
_service_lock = threading.Lock()


def shared() -> TerraService:
    global _service
    with _service_lock:
        if _service is None:
            _service = TerraService()
        return _service


# ---------------------------------------------------------------------------
# Job entry points
# ---------------------------------------------------------------------------
def start_analysis() -> Job:
    svc = shared()

    def run(emit):
        result = agents.run_all(svc.ranked[:40], use_llm=True, emit=emit)
        svc.analysis = result
        return result

    return manager.start("analysis", {}, run)


def start_situation(key: str) -> Job:
    svc = shared()

    def run(emit):
        emit("collect", f"Collecting events for {key}…")
        by_id = svc.by_id()
        emit("analyze", "Writing situation report…")
        return reports.situation(key, svc.ranked, by_id, svc.graph, svc.risk)

    return manager.start("situation", {"theatre": key}, run)


def start_whatif(scenario: str) -> Job:
    svc = shared()

    def run(emit):
        emit("resolve", "Resolving scenario against the ontology…")
        emit("simulate", "Walking the graph and simulating…")
        return predict.what_if(svc.graph, scenario, svc.articles(72.0))

    return manager.start("whatif", {"scenario": scenario}, run)


def start_deep_event(cluster_id: str) -> Job:
    svc = shared()

    def run(emit):
        cluster = svc.clusters.get(cluster_id)
        if cluster is None:
            raise ValueError(f"no such event: {cluster_id}")
        ranked = next((c for c in svc.ranked if c["id"] == cluster_id), cluster)
        by_id = svc.by_id()
        emit("verify", "Comparing coverage across outlets…")
        verification = verify_mod.verify(cluster, by_id)
        emit("predict", "Deriving second-order predictions…")
        predictions = predict.predict_event(svc.graph, ranked, by_id)
        emit("economics", "Estimating market impact…")
        economics = predict.economic_impact(ranked, svc.graph)
        emit("history", "Searching for historical analogues…")
        history = reports.analogues(ranked)
        return {"verification": verification, "predictions": predictions,
                "economics": economics, "history": history}

    return manager.start("deep_event", {"cluster": cluster_id}, run)


def start_brief(fmt: str) -> Job:
    svc = shared()

    def run(emit):
        emit("write", f"Writing {fmt} briefing…")
        return svc.brief(fmt)

    return manager.start("brief", {"format": fmt}, run)


__all__ = ["shared", "manager", "TerraService", "start_analysis",
           "start_situation", "start_whatif", "start_deep_event",
           "start_brief", "onto", "reports"]
