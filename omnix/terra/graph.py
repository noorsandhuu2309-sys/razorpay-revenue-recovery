"""The TERRA knowledge graph — typed nodes, typed edges, provenance on both.

An in-process adjacency structure instead of Neo4j. That is a deliberate trade:
the operations this feature actually performs are neighbor expansion, k-hop
subgraph extraction and degree ranking over a graph of a few thousand nodes, all
of which are microseconds in a dict. What Neo4j would add — durable
transactions, Cypher, a graph that outgrows RAM — costs a server process the
target machine doesn't have. The public methods here are deliberately the same
verbs a Cypher-backed store would expose (`neighbors`, `subgraph`, `path`), so
swapping the backing store later is a change to this file only.

The property that makes the graph trustworthy rather than decorative:
EVERY EDGE CARRIES ITS SOURCE ARTICLES. Nothing in the UI asserts a relationship
without being able to show the headlines it came from, so a user can always
audit a connection the model drew instead of taking it on faith.
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from . import ontology as onto
from .extract import entity_id

ROOT = Path(__file__).resolve().parents[2]
GRAPH_PATH = ROOT / "omnix_terra_graph.json"

# Bumped to 2 when the LLM-entity grounding check was added: graphs written
# before it contain model-assembled fragment nodes that the new rule would have
# rejected, and there is no way to tell them apart after the fact. _load discards
# a mismatched schema, so the graph reseeds and rebuilds on the next pass.
SCHEMA = 2
# Observations older than this stop contributing to edge weight. Without decay
# a story that dominated a month ago outranks today's forever.
HALF_LIFE_HOURS = 72.0
MAX_EDGE_ARTICLES = 12   # provenance kept per edge; enough to audit, bounded


def _decay(ts: float, now: float) -> float:
    age_h = max(0.0, (now - ts) / 3600.0)
    return 0.5 ** (age_h / HALF_LIFE_HOURS)


def _slim(edge: dict) -> dict:
    """A secondary relation between an already-represented pair."""
    return {"relation": edge["relation"], "label": edge["relation_label"],
            "weight": edge["weight"], "count": edge["count"],
            "sentiment": edge["sentiment"], "static": edge["static"],
            "llm": edge["llm"], "articles": edge.get("articles", [])[:4]}


class KnowledgeGraph:
    def __init__(self, path: Path | str = GRAPH_PATH):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.nodes: dict[str, dict] = {}
        # adjacency: node -> relation -> other -> edge record
        self.adj: dict[str, dict[str, dict[str, dict]]] = defaultdict(
            lambda: defaultdict(dict))
        self._dirty = False
        self._load()
        if not self.nodes:
            self.seed()

    # -- persistence ---------------------------------------------------------
    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        if raw.get("schema") != SCHEMA:
            return
        self.nodes = raw.get("nodes") or {}
        for src, rels in (raw.get("edges") or {}).items():
            for rel, targets in rels.items():
                for dst, rec in targets.items():
                    self.adj[src][rel][dst] = rec

    def save(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            payload = {
                "schema": SCHEMA,
                "saved": time.time(),
                "nodes": self.nodes,
                "edges": {s: {r: dict(t) for r, t in rels.items()}
                          for s, rels in self.adj.items()},
            }
            self._dirty = False
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except Exception:
            pass

    # -- construction --------------------------------------------------------
    def seed(self) -> None:
        """Lay down the ontology's static skeleton — the relationships that are
        true before any news arrives (ASML supplies TSMC; oil transits Hormuz).
        Gives the first-run graph something real to show and gives news edges
        somewhere to attach."""
        with self._lock:
            for iso, meta in onto.countries().items():
                self.upsert_node(entity_id("country", iso), "country",
                                 meta["name"], iso2=iso, lat=meta["lat"],
                                 lon=meta["lon"], seed=True)
            for name, meta in onto.seed_objects().items():
                nid = entity_id(meta.get("type", "organization"), name)
                self.upsert_node(nid, meta.get("type", "organization"), name,
                                 sector=meta.get("sector", ""),
                                 role=meta.get("role", ""),
                                 lat=meta.get("lat"), lon=meta.get("lon"),
                                 seed=True)
            for subj, rel, obj in onto.BASE_EDGES:
                s = self._seed_id(subj)
                o = self._seed_id(obj)
                if s and o:
                    self.link(s, o, rel, weight=1.0, article=None, static=True)
            for name, iso in onto.BASE_COUNTRY_EDGES:
                s = self._seed_id(name)
                if s:
                    self.link(s, entity_id("country", iso), "located_in",
                              weight=1.0, article=None, static=True)
            for person, meta in onto.SEED_PEOPLE.items():
                iso = meta.get("country")
                if iso:
                    self.link(entity_id("person", person),
                              entity_id("country", iso), "located_in",
                              weight=0.8, article=None, static=True)

    @staticmethod
    def _seed_id(name: str) -> str:
        meta = onto.seed_objects().get(name)
        if meta:
            return entity_id(meta.get("type", "organization"), name)
        iso = onto.iso_for(name)
        return entity_id("country", iso) if iso else ""

    def upsert_node(self, nid: str, ntype: str, name: str, **props) -> dict:
        with self._lock:
            node = self.nodes.get(nid)
            if node is None:
                node = {"id": nid, "type": ntype, "name": name,
                        "mentions": 0, "first_seen": time.time(),
                        "last_seen": time.time(), "articles": []}
                self.nodes[nid] = node
            for key, val in props.items():
                if val not in (None, "", []) and not node.get(key):
                    node[key] = val
            if name and len(name) > len(node.get("name", "")) and ntype != "country":
                node["name"] = name       # prefer the fuller surface form
            self._dirty = True
            return node

    def observe(self, nid: str, article_id: str, ts: float, count: int = 1) -> None:
        with self._lock:
            node = self.nodes.get(nid)
            if node is None:
                return
            node["mentions"] = node.get("mentions", 0) + count
            node["last_seen"] = max(node.get("last_seen", 0), ts)
            arts = node.setdefault("articles", [])
            if article_id and article_id not in arts:
                arts.append(article_id)
                if len(arts) > 60:
                    del arts[: len(arts) - 60]
            self._dirty = True

    def link(self, src: str, dst: str, relation: str, *, weight: float = 1.0,
             article: str | None = None, ts: float | None = None,
             static: bool = False, sentiment: float = 0.0,
             llm: bool = False) -> None:
        """Record one observation of a relationship.

        Symmetric relations are stored on both endpoints so neighbor lookups
        never need a reverse scan; the shared `articles` provenance list means
        both directions cite the same evidence.
        """
        if not src or not dst or src == dst:
            return
        relation = onto.relation_ok(relation)
        base = onto.RELATIONS[relation]["weight"]
        ts = ts or time.time()
        pairs = [(src, dst)]
        if onto.RELATIONS[relation]["symmetric"]:
            pairs.append((dst, src))
        with self._lock:
            for a, b in pairs:
                rec = self.adj[a][relation].get(b)
                if rec is None:
                    rec = {"weight": 0.0, "count": 0, "articles": [],
                           "first": ts, "last": ts, "static": static,
                           "sentiment": 0.0, "llm": llm}
                    self.adj[a][relation][b] = rec
                elif not llm:
                    # A relation a verb pattern also found is no longer
                    # model-only, and the UI should stop flagging it as such.
                    rec["llm"] = False
                rec["weight"] += base * weight
                rec["count"] += 1
                rec["last"] = max(rec["last"], ts)
                # Running mean of the tone of the reporting behind this edge —
                # what separates "allied with" coverage from "in conflict".
                n = rec["count"]
                rec["sentiment"] = round(
                    (rec["sentiment"] * (n - 1) + sentiment) / n, 3)
                if article and article not in rec["articles"]:
                    rec["articles"].append(article)
                    if len(rec["articles"]) > MAX_EDGE_ARTICLES:
                        del rec["articles"][0]
            self._dirty = True

    # -- ingestion from analyzed articles -----------------------------------
    def ingest_articles(self, articles: list[dict],
                        llm_triples: list[dict] | None = None) -> dict:
        """Fold a batch of analyzed articles into the graph.

        Co-mention edges are created between every pair of entities in an
        article, but weighted DOWN hard (0.4 base, further divided by how many
        entities the article mentions) so a headline listing eight countries
        doesn't manufacture 28 strong relationships. The typed edges — from the
        verb patterns and from the LLM — are what carry real weight.
        """
        stats = {"nodes": 0, "edges": 0, "articles": 0, "typed": 0}
        for art in articles:
            ents = art.get("entities") or []
            if not ents:
                continue
            ts = art.get("published_ts", time.time())
            aid = art.get("id", "")
            sent = art.get("sentiment", 0.0)
            for ent in ents:
                created = ent["id"] not in self.nodes
                self.upsert_node(ent["id"], ent["type"], ent["name"],
                                 provisional=ent.get("provisional", False))
                self.observe(ent["id"], aid, ts, ent.get("count", 1))
                stats["nodes"] += int(created)
            # Pairwise co-mention, damped by article breadth.
            rel = art.get("relation", "co_mentioned")
            damp = 1.0 / max(1.0, math.sqrt(len(ents)))
            for i, a in enumerate(ents):
                for b in ents[i + 1:]:
                    # The verb-inferred relation applies to the two most
                    # prominent entities only; everything else is co-mention.
                    use = rel if (i == 0 and b is ents[1] and rel != "co_mentioned") \
                        else "co_mentioned"
                    self.link(a["id"], b["id"], use, weight=damp, article=aid,
                              ts=ts, sentiment=sent)
                    stats["edges"] += 1
            stats["articles"] += 1

        # Grounding index for the LLM pass: an entity the model proposes must
        # actually appear in the article it came from. Without this the graph
        # accumulates plausible-looking fragments the model assembled itself —
        # "Trump Saudi", "Senate Russi" — which are indistinguishable from real
        # entities once they are nodes, and are the single fastest way to make a
        # knowledge graph untrustworthy.
        text_of = {a.get("id", ""): (a.get("title", "") + " " +
                                     a.get("summary", "")).lower()
                   for a in articles}

        for tri in llm_triples or []:
            s_type, o_type = tri["subject_type"], tri["object_type"]
            s_key = onto.iso_for(tri["subject"]) if s_type == "country" else tri["subject"]
            o_key = onto.iso_for(tri["object"]) if o_type == "country" else tri["object"]
            if not s_key or not o_key:
                continue
            sid, oid = entity_id(s_type, s_key), entity_id(o_type, o_key)
            source_text = text_of.get(tri.get("article", ""), "")
            if not self._admit(sid, s_type, tri["subject"], source_text):
                continue
            if not self._admit(oid, o_type, tri["object"], source_text):
                continue
            self.upsert_node(sid, s_type,
                             onto.country_name(s_key) if s_type == "country" else tri["subject"])
            self.upsert_node(oid, o_type,
                             onto.country_name(o_key) if o_type == "country" else tri["object"])
            # Weighted BELOW a verb match and flagged, because the failure mode
            # is specific: the model reliably picks real entities out of a
            # headline and then relates the wrong pair of them. Provenance plus
            # a visible "model-derived" marker lets a reader audit that; a
            # confident unmarked edge would not.
            self.link(sid, oid, tri["relation"], weight=0.7,
                      article=tri.get("article"), llm=True)
            stats["typed"] += 1
        return stats

    def _admit(self, nid: str, ntype: str, name: str, source_text: str) -> bool:
        """May an LLM-proposed entity become a node?

        Yes if it already exists (the model is talking about something we know),
        yes for countries (the gazetteer already resolved it to an ISO code).
        Otherwise it must appear verbatim in the article it was extracted from.
        """
        if nid in self.nodes or ntype == "country":
            return True
        if not source_text:
            return False
        return (name or "").strip().lower() in source_text

    def ingest_clusters(self, ranked: list[dict], limit: int = 90) -> int:
        """Put news stories into the graph as nodes.

        Without this the graph and the news feed are two disconnected worlds:
        you can see that Iran and the United States are in conflict but not
        which story says so, and selecting a story in the news view has nothing
        to select in the graph. Making a cluster a node gives every view the
        same entity space to synchronise on.

        A story dominated by military coverage becomes a `conflict` rather than
        a `news_story`, which is the distinction the explorer draws differently.
        """
        added = 0
        for cluster in ranked[:limit]:
            ents = cluster.get("entities") or []
            if len(ents) < 2:
                continue
            domains = cluster.get("domains") or []
            is_conflict = (domains and domains[0] == "military"
                           and cluster.get("severity", 0) >= 0.45)
            ntype = "conflict" if is_conflict else "news_story"
            nid = f"{ntype}:{cluster['id']}"
            self.upsert_node(nid, ntype, cluster["title"][:120],
                             cluster=cluster["id"])
            node = self.nodes[nid]
            node["mentions"] = cluster.get("size", 1)
            node["last_seen"] = cluster.get("last_ts", time.time())
            node["articles"] = list(cluster.get("article_ids", []))[:20]
            added += 1
            # Link the story to the entities it is about. `involved_in` points
            # from the actor to the story so a country's neighbourhood surfaces
            # its live stories.
            for ent in ents[:6]:
                self.link(ent["id"], nid, "involved_in",
                          weight=min(1.6, 0.5 + ent.get("mentions", 1) * 0.12),
                          ts=cluster.get("last_ts"),
                          sentiment=cluster.get("sentiment", 0.0))
        return added

    def prune(self, drop_article_ids: list[str], min_weight: float = 0.15,
              max_nodes: int = 6000) -> None:
        """Forget what fell out of the article window.

        Static ontology edges and seed nodes are never pruned — they are the
        skeleton, not observations.
        """
        drop = set(drop_article_ids or [])
        now = time.time()
        with self._lock:
            for node in self.nodes.values():
                if drop and node.get("articles"):
                    node["articles"] = [a for a in node["articles"] if a not in drop]
            for src, rels in list(self.adj.items()):
                for rel, targets in list(rels.items()):
                    for dst, rec in list(targets.items()):
                        if rec.get("static"):
                            continue
                        if drop:
                            rec["articles"] = [a for a in rec.get("articles", [])
                                               if a not in drop]
                        if rec["weight"] * _decay(rec["last"], now) < min_weight \
                                and not rec.get("articles"):
                            del targets[dst]
                    if not targets:
                        del rels[rel]
                if not rels:
                    del self.adj[src]
            # Drop provisional nodes that lost all their evidence.
            if len(self.nodes) > max_nodes:
                doomed = [nid for nid, n in self.nodes.items()
                          if not n.get("seed") and not n.get("articles")
                          and not self.adj.get(nid)]
                for nid in doomed:
                    del self.nodes[nid]
            self._dirty = True

    # -- queries -------------------------------------------------------------
    def node(self, nid: str) -> dict | None:
        return self.nodes.get(nid)

    def find(self, query: str, limit: int = 12) -> list[dict]:
        """Name search over nodes, ranked by match quality then salience."""
        q = (query or "").strip().lower()
        if not q:
            return []
        scored = []
        for node in self.nodes.values():
            name = node["name"].lower()
            if name == q:
                rank = 0
            elif name.startswith(q):
                rank = 1
            elif q in name:
                rank = 2
            else:
                continue
            scored.append((rank, -node.get("mentions", 0), node))
        scored.sort(key=lambda x: (x[0], x[1]))
        return [self._public(n) for _, _, n in scored[:limit]]

    def neighbors(self, nid: str, limit: int = 24, relation: str | None = None,
                  now: float | None = None) -> list[dict]:
        """Adjacent nodes with the edge that connects them, strongest first."""
        now = now or time.time()
        out = []
        for rel, targets in (self.adj.get(nid) or {}).items():
            if relation and rel != relation:
                continue
            for dst, rec in targets.items():
                node = self.nodes.get(dst)
                if node is None:
                    continue
                strength = rec["weight"] * (1.0 if rec.get("static")
                                            else _decay(rec["last"], now))
                out.append({
                    "node": self._public(node),
                    "relation": rel,
                    "relation_label": onto.RELATIONS[rel]["label"],
                    "weight": round(strength, 3),
                    "count": rec["count"],
                    "sentiment": rec.get("sentiment", 0.0),
                    "static": bool(rec.get("static")),
                    "llm": bool(rec.get("llm")),
                    "articles": list(rec.get("articles", [])),
                })
        out.sort(key=lambda e: -e["weight"])
        return out[:limit]

    def subgraph(self, roots: list[str], hops: int = 1, max_nodes: int = 60,
                 per_node: int = 8) -> dict:
        """Breadth-first expansion around one or more roots.

        Returns a {nodes, edges} payload the frontend renders directly. Widths
        are pre-decayed so the client never needs to know about time decay.
        """
        now = time.time()
        seen: dict[str, dict] = {}
        edges: list[dict] = []
        edge_keys: set[tuple] = set()
        queue: deque = deque()
        for r in roots:
            node = self.nodes.get(r)
            if node:
                seen[r] = {**self._public(node), "root": True, "depth": 0}
                queue.append((r, 0))
        while queue and len(seen) < max_nodes:
            nid, depth = queue.popleft()
            if depth >= hops:
                continue
            for edge in self.neighbors(nid, limit=per_node, now=now):
                other = edge["node"]
                key = tuple(sorted((nid, other["id"]))) + (edge["relation"],)
                if key not in edge_keys:
                    edge_keys.add(key)
                    edges.append({"source": nid, "target": other["id"],
                                  "relation": edge["relation"],
                                  "label": edge["relation_label"],
                                  "weight": edge["weight"],
                                  "count": edge["count"],
                                  "sentiment": edge["sentiment"],
                                  "static": edge["static"],
                                  "llm": edge["llm"],
                                  "articles": edge["articles"]})
                if other["id"] not in seen and len(seen) < max_nodes:
                    seen[other["id"]] = {**other, "depth": depth + 1}
                    queue.append((other["id"], depth + 1))
        # Keep only edges whose both endpoints made the cut.
        edges = [e for e in edges if e["source"] in seen and e["target"] in seen]
        return {"nodes": list(seen.values()), "edges": edges}

    def path_between(self, src: str, dst: str, max_hops: int = 4) -> list[dict]:
        """Shortest relationship chain between two objects — "how is Apple
        connected to the Taiwan Strait". [] if unconnected within max_hops."""
        if src not in self.nodes or dst not in self.nodes:
            return []
        prev: dict[str, tuple[str, str]] = {}
        queue = deque([(src, 0)])
        visited = {src}
        while queue:
            nid, depth = queue.popleft()
            if nid == dst:
                break
            if depth >= max_hops:
                continue
            for rel, targets in (self.adj.get(nid) or {}).items():
                for other in targets:
                    if other in visited:
                        continue
                    visited.add(other)
                    prev[other] = (nid, rel)
                    queue.append((other, depth + 1))
        if dst not in prev and src != dst:
            return []
        chain: list[dict] = []
        cur = dst
        while cur != src:
            parent, rel = prev[cur]
            chain.append({"from": self._public(self.nodes[parent]),
                          "relation": rel,
                          "label": onto.RELATIONS[rel]["label"],
                          "to": self._public(self.nodes[cur])})
            cur = parent
        chain.reverse()
        return chain

    def top_nodes(self, ntype: str | None = None, limit: int = 20,
                  hours: float = 48.0) -> list[dict]:
        """Most salient objects right now — degree-weighted recency, not raw
        mention count, so a node connected to many live stories outranks one
        name repeated in a single story."""
        now = time.time()
        cutoff = now - hours * 3600
        scored = []
        for nid, node in self.nodes.items():
            if ntype and node["type"] != ntype:
                continue
            if node.get("last_seen", 0) < cutoff:
                continue
            degree = sum(len(t) for t in (self.adj.get(nid) or {}).values())
            score = node.get("mentions", 0) * (1 + math.log1p(degree))
            score *= _decay(node.get("last_seen", now), now)
            if score <= 0:
                continue
            rec = self._public(node)
            rec["score"] = round(score, 2)
            rec["degree"] = degree
            scored.append(rec)
        scored.sort(key=lambda n: -n["score"])
        return scored[:limit]

    def _public(self, node: dict) -> dict:
        meta = onto.TYPES.get(node["type"], {})
        sector = node.get("sector", "")
        vclass = onto.visual_class(node["type"], sector)
        vis = onto.VISUAL[vclass]
        return {
            "id": node["id"], "type": node["type"], "name": node["name"],
            "glyph": vis["glyph"], "color": vis["color"],
            "type_label": meta.get("label", node["type"]),
            "vclass": vclass, "vlabel": vis["label"],
            "shape": vis["shape"], "vweight": vis["weight"], "ring": vis["ring"],
            "mentions": node.get("mentions", 0),
            "articles": list(node.get("articles", []))[-12:],
            "iso2": node.get("iso2", ""), "lat": node.get("lat"),
            "lon": node.get("lon"), "sector": sector,
            "role": node.get("role", ""),
            "cluster": node.get("cluster", ""),
            "provisional": bool(node.get("provisional")),
            "seed": bool(node.get("seed")),
            "degree": sum(len(t) for t in (self.adj.get(node["id"]) or {}).values()),
            "importance": round(self.importance(node["id"]), 3),
        }

    # -- progressive exploration ---------------------------------------------
    def importance(self, nid: str) -> float:
        """0..1 salience used for node sizing.

        Weighted degree rather than raw degree: a node attached to one heavily
        attested relationship matters more than one attached to six incidental
        co-mentions, and sizing by raw degree makes the graph reward noise.
        """
        rels = self.adj.get(nid)
        if not rels:
            return 0.05
        total = 0.0
        for targets in rels.values():
            for rec in targets.values():
                total += rec["weight"]
        node = self.nodes.get(nid) or {}
        total += math.log1p(node.get("mentions", 0)) * 1.5
        # log-compress: the top node is 50x the median in raw terms, which would
        # make everything else invisible.
        return min(1.0, math.log1p(total) / 6.0)

    def expand(self, nid: str, limit: int = 8, exclude: set[str] | None = None,
               relations: list[str] | None = None,
               types: list[str] | None = None) -> list[dict]:
        """The most RELEVANT first-degree neighbours of one node.

        This is what makes progressive exploration work: expanding a node must
        show the handful of connections that explain it, not all forty. Relevance
        is edge strength weighted by how important the neighbour is in its own
        right, so expanding "Iran" surfaces the United States before it surfaces
        a provisional two-mention name that happened to co-occur once.
        """
        exclude = exclude or set()
        # Grouped by TARGET, not by edge. Two objects are frequently joined by
        # several relations at once (co-mentioned AND in conflict), and counting
        # those as two results means "expand 8" adds four entities and looks
        # broken. The strongest relation represents the pair; `also` carries the
        # rest so the client can still draw every edge.
        best: dict[str, dict] = {}
        for edge in self.neighbors(nid, limit=300):
            other = edge["node"]
            if other["id"] in exclude or other["id"] == nid:
                continue
            if relations and edge["relation"] not in relations:
                continue
            if types and other["vclass"] not in types:
                continue
            relevance = edge["weight"] * (0.45 + 0.55 * other["importance"])
            # Typed relationships explain more than raw co-occurrence, so they
            # win ties even when a co-mention edge is heavier.
            if edge["relation"] != "co_mentioned":
                relevance *= 1.5
            if other.get("provisional"):
                relevance *= 0.6
            edge["relevance"] = round(relevance, 4)
            prior = best.get(other["id"])
            if prior is None:
                edge["also"] = []
                best[other["id"]] = edge
            elif edge["relevance"] > prior["relevance"]:
                edge["also"] = prior["also"] + [_slim(prior)]
                best[other["id"]] = edge
            else:
                prior["also"].append(_slim(edge))
        scored = sorted(best.values(), key=lambda e: -e["relevance"])
        return scored[:limit]

    def degree_of(self, nid: str, exclude: set[str] | None = None) -> int:
        """Distinct neighbours, for the "N more" affordance on a node."""
        exclude = exclude or set()
        seen = set()
        for targets in (self.adj.get(nid) or {}).values():
            seen.update(t for t in targets if t not in exclude and t != nid)
        return len(seen)

    def communities(self, node_ids: list[str] | None = None,
                    iterations: int = 12) -> dict[str, int]:
        """Community id per node, via label propagation.

        Label propagation rather than Louvain: it is a dozen lines, runs in
        milliseconds on this graph size, and needs no modularity bookkeeping.
        For colouring clusters in a view the user is going to explore by hand,
        the difference in partition quality is not observable.
        """
        scope = set(node_ids) if node_ids else set(self.nodes)
        labels = {nid: i for i, nid in enumerate(sorted(scope))}
        order = sorted(scope)
        for _ in range(iterations):
            changed = 0
            for nid in order:
                tally: dict[int, float] = defaultdict(float)
                for rels in (self.adj.get(nid) or {}).values():
                    for other, rec in rels.items():
                        if other in labels:
                            tally[labels[other]] += rec["weight"]
                if not tally:
                    continue
                best = max(tally.items(), key=lambda kv: (kv[1], -kv[0]))[0]
                if best != labels[nid]:
                    labels[nid] = best
                    changed += 1
            if not changed:
                break
        # Renumber densely so the client can index a palette directly.
        remap: dict[int, int] = {}
        for nid in order:
            remap.setdefault(labels[nid], len(remap))
            labels[nid] = remap[labels[nid]]
        return labels

    def community_summary(self, labels: dict[str, int], limit: int = 12
                          ) -> list[dict]:
        """Name each community by its most important members."""
        groups: dict[int, list[str]] = defaultdict(list)
        for nid, cid in labels.items():
            groups[cid].append(nid)
        out = []
        for cid, members in groups.items():
            if len(members) < 2:
                continue
            ranked = sorted(members, key=lambda n: -self.importance(n))
            names = [self.nodes[n]["name"] for n in ranked[:3] if n in self.nodes]
            out.append({
                "id": cid, "size": len(members),
                "label": " · ".join(names),
                "members": ranked[:24],
                "importance": round(sum(self.importance(n) for n in ranked[:8]), 2),
            })
        out.sort(key=lambda c: -c["size"])
        return out[:limit]

    def stats(self) -> dict:
        by_type: dict[str, int] = defaultdict(int)
        for node in self.nodes.values():
            by_type[node["type"]] += 1
        edge_count = sum(len(t) for rels in self.adj.values()
                         for t in rels.values())
        return {
            "nodes": len(self.nodes),
            "edges": edge_count,
            "by_type": dict(by_type),
            "relations": sorted({r for rels in self.adj.values() for r in rels}),
            "path": str(self.path),
        }


_shared: KnowledgeGraph | None = None
_shared_lock = threading.Lock()


def shared() -> KnowledgeGraph:
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = KnowledgeGraph()
        return _shared
