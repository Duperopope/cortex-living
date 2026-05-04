"""
cortex_thought_graph.py — Graphe sémantique de pensée avec A*.

Chaque note est un nœud. Arêtes pondérées par similarité TF-IDF cosine.
A* trouve le chemin le plus court entre deux pensées (heuristique = cosine direct).

Use cases :
- Tracer la genèse d'une idée
- Détecter analogies (court chemin entre concepts éloignés)
- Trouver zones d'ignorance (nœuds isolés)
"""
import heapq
import json
import re
import time
from pathlib import Path
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

VAULT_PATH    = Path(r"<USER_HOME>\Documents\Obsidian Vault")
CLAUDE_MEMORY = Path.home() / ".claude" / "projects" / "h--Code-Paperclip" / "memory"
GRAPH_CACHE   = Path(r"<CORTEX_REPO>\scripts\brain\.cortex_graph_cache.npz")

_state = {"nodes": None, "vectors": None, "vectorizer": None, "built_at": 0}


def _collect_notes() -> list[dict]:
    """Liste {id, source, text, kind} pour toutes les notes pertinentes."""
    notes = []
    # 1. Mémoire Claude
    if CLAUDE_MEMORY.exists():
        for md in CLAUDE_MEMORY.glob("*.md"):
            try:
                notes.append({"id": f"claude:{md.stem}", "source": str(md.name),
                              "text": md.read_text(encoding="utf-8", errors="replace")[:3000],
                              "kind": "claude_memory"})
            except Exception: pass
    # 2. Sémantique vault
    sem = VAULT_PATH / "08 - Semantic"
    if sem.exists():
        for md in sem.rglob("*.md"):
            try:
                rel = str(md.relative_to(VAULT_PATH))
                notes.append({"id": f"semantic:{md.stem}", "source": rel,
                              "text": md.read_text(encoding="utf-8", errors="replace")[:2000],
                              "kind": "semantic"})
            except Exception: pass
    # 3. Épisodiques (limit 100 récents)
    ing = VAULT_PATH / "07 - Ingested" / "conversations"
    if ing.exists():
        all_eps = []
        for day in sorted(ing.glob("*"), reverse=True):
            if day.is_dir():
                for note in day.glob("*.md"):
                    try: all_eps.append((note.stat().st_mtime, note))
                    except: pass
        all_eps.sort(reverse=True)
        for _, note in all_eps[:100]:
            try:
                rel = str(note.relative_to(VAULT_PATH))
                notes.append({"id": f"episodic:{note.stem}", "source": rel,
                              "text": note.read_text(encoding="utf-8", errors="replace")[:1500],
                              "kind": "episodic"})
            except Exception: pass
    return notes


def build_graph(force: bool = False) -> dict:
    """Vectorise toutes les notes, calcule similarité, construit adjacency."""
    if not force and _state["nodes"] and time.time() - _state["built_at"] < 300:
        return _state
    notes = _collect_notes()
    if len(notes) < 2:
        return {"nodes": [], "error": "not enough notes"}
    texts = [n["text"] for n in notes]
    vectorizer = TfidfVectorizer(max_features=2000, stop_words=None, ngram_range=(1, 2))
    vectors = vectorizer.fit_transform(texts)
    _state.update({"nodes": notes, "vectors": vectors, "vectorizer": vectorizer,
                   "built_at": time.time()})
    return _state


def _sim(i: int, j: int) -> float:
    v = _state["vectors"]
    return float(cosine_similarity(v[i], v[j])[0, 0])


def _find_node(query: str) -> int | None:
    """Trouve le nœud le plus pertinent pour une query (par similarité TF-IDF)."""
    if not _state["nodes"]:
        build_graph()
    # Si query est un id direct, lookup
    for i, n in enumerate(_state["nodes"]):
        if n["id"] == query or query.lower() in n["source"].lower():
            return i
    # Sinon par similarité TF-IDF
    qv = _state["vectorizer"].transform([query])
    sims = cosine_similarity(qv, _state["vectors"])[0]
    best = int(np.argmax(sims))
    return best if sims[best] > 0.05 else None


def astar_path(start_query: str, goal_query: str, max_neighbors: int = 8) -> dict:
    """A* entre deux pensées. Retourne path + détails."""
    build_graph()
    if not _state["nodes"]:
        return {"ok": False, "error": "graph empty"}
    start = _find_node(start_query)
    goal  = _find_node(goal_query)
    if start is None: return {"ok": False, "error": f"start not found: {start_query!r}"}
    if goal  is None: return {"ok": False, "error": f"goal not found: {goal_query!r}"}
    if start == goal:
        n = _state["nodes"][start]
        return {"ok": True, "path": [n], "cost": 0.0, "note": "start == goal"}

    nodes = _state["nodes"]
    n_total = len(nodes)
    # Heuristique : 1 - cosine(current, goal) — admissible
    def h(i): return 1.0 - _sim(i, goal)

    # A*
    open_set = [(h(start), 0.0, start, [start])]
    visited = set()
    while open_set:
        f, g, current, path = heapq.heappop(open_set)
        if current == goal:
            # Active tous les nœuds traversés (Spreading Activation)
            try:
                import cortex_activation as _ca
                _ca.co_activate([nodes[i]["source"] for i in path])
            except Exception: pass
            return {
                "ok": True,
                "path": [{"index": i, **{k: v for k, v in nodes[i].items() if k != "text"}}
                         for i in path],
                "cost": round(g, 3),
                "steps": len(path) - 1,
                "summary": " → ".join(nodes[i]["source"][:30] for i in path),
            }
        if current in visited: continue
        visited.add(current)
        # Top-K voisins par similarité (évite N² complet)
        sims = cosine_similarity(_state["vectors"][current], _state["vectors"])[0]
        # Indices triés par similarité descendante, exclure soi-même
        neighbors = [(i, sims[i]) for i in range(n_total) if i != current]
        neighbors.sort(key=lambda x: -x[1])
        for nb, sim in neighbors[:max_neighbors]:
            if nb in visited: continue
            cost = 1.0 - sim  # plus le voisin est proche, moins coûteux
            if cost > 0.95: continue  # ignore liens quasi-nuls
            heapq.heappush(open_set, (g + cost + h(nb), g + cost, nb, path + [nb]))
    return {"ok": False, "error": "no path found"}


def find_isolated(min_top_sim: float = 0.15, top_n: int = 10) -> list[dict]:
    """Trouve les nœuds 'orphelins' : ceux dont la meilleure similarité avec
    n'importe quel autre nœud est faible. Ce sont les zones d'ignorance."""
    build_graph()
    nodes = _state["nodes"]
    isolated = []
    for i in range(len(nodes)):
        sims = cosine_similarity(_state["vectors"][i], _state["vectors"])[0]
        sims[i] = 0
        top = float(np.max(sims))
        if top < min_top_sim:
            isolated.append({"source": nodes[i]["source"], "kind": nodes[i]["kind"],
                             "top_sim": round(top, 3)})
    isolated.sort(key=lambda x: x["top_sim"])
    return isolated[:top_n]


def stats() -> dict:
    build_graph()
    nodes = _state["nodes"]
    if not nodes: return {"nodes": 0}
    by_kind = {}
    for n in nodes:
        by_kind[n["kind"]] = by_kind.get(n["kind"], 0) + 1
    return {"nodes": len(nodes), "by_kind": by_kind,
            "vocab_size": len(_state["vectorizer"].vocabulary_),
            "built_at": _state["built_at"]}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2:
        print("Usage:")
        print("  cortex_thought_graph.py stats")
        print("  cortex_thought_graph.py path '<from>' '<to>'")
        print("  cortex_thought_graph.py isolated")
        sys.exit(0)
    cmd = sys.argv[1]
    if cmd == "stats":
        print(json.dumps(stats(), ensure_ascii=False, indent=2))
    elif cmd == "path" and len(sys.argv) >= 4:
        print(json.dumps(astar_path(sys.argv[2], sys.argv[3]), ensure_ascii=False, indent=2)[:3000])
    elif cmd == "isolated":
        print(json.dumps(find_isolated(), ensure_ascii=False, indent=2))
    else:
        print("Unknown command")
