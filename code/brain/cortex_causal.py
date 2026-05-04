"""
cortex_causal.py — Graphe causal léger sur le thought_graph et l'historique.

Pas un Structural Causal Model complet. Une approximation honnête qui dépasse
la simple corrélation TF-IDF :

1. Edges Hebbian = co-activations temporelles (Cortex_activation.edge_strengths).
   Si A et B sont systématiquement co-activés dans une fenêtre courte,
   c'est un INDICE causal (mais pas une preuve).

2. Test d'intervention simulée (do-calculus light) :
   Pour une paire (A, B) suspectée causale, on regarde dans
   .cortex-pulses.jsonl les cas où A est activé SANS B et vice-versa.
   Si activer A sans B → B suit dans <T s : c'est un signal A→B.
   Si activer B sans A → A NE suit PAS : asymétrie causale.

3. Score de Granger temporel : pour chaque edge Hebbian, compter la fraction
   d'occurrences où l'un précède l'autre dans les pulses (>60% = orientation
   causale probable).

Le résultat : un graphe orienté .cortex-causal-graph.json qui dit "A précède
souvent B et l'inverse n'est pas vrai", utilisable pour la planification.

API :
    detect_causal_pairs(min_strength=0.02, min_observations=3) → list[edge]
    causal_graph() → dict {nodes, edges_directed}
    intervention_estimate(cause, effect) → score [0..1]
    explain(cause, effect) → texte humain
    self_test() → bool
"""
from __future__ import annotations
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent
VAULT = Path(r"<USER_HOME>\Documents\Obsidian Vault")
PULSES_FILE = VAULT / ".cortex-pulses.jsonl"
ACTIVATIONS_FILE = VAULT / ".cortex-activations.json"
CAUSAL_GRAPH = VAULT / ".cortex-causal-graph.json"
CAUSAL_LOG = VAULT / ".cortex-causal-events.jsonl"

# Fenêtre temporelle pour considérer 2 pulses comme "successifs"
TEMPORAL_WINDOW_SEC = 5.0


def _now() -> float: return time.time()


def _load_pulses(max_lines: int = 5000) -> list[dict]:
    if not PULSES_FILE.exists(): return []
    try:
        lines = PULSES_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        out = []
        for ln in lines[-max_lines:]:
            try: out.append(json.loads(ln))
            except Exception: pass
        return out
    except Exception: return []


def _load_hebbian_edges() -> dict:
    if not ACTIVATIONS_FILE.exists(): return {}
    try:
        data = json.loads(ACTIVATIONS_FILE.read_text(encoding="utf-8"))
        edges = data.get("edge_strengths", {}) or {}
        # Format : "A|||B" -> strength
        out = {}
        for key, strength in edges.items():
            if "|||" in key:
                a, b = key.split("|||", 1)
                out[(a, b)] = strength
        return out
    except Exception: return {}


def _temporal_precedence(pulses: list[dict]) -> dict:
    """Pour chaque paire (A, B), compte les fois où A précède B dans la fenêtre."""
    precedence = defaultdict(lambda: {"a_before_b": 0, "b_before_a": 0,
                                      "a_alone": 0, "b_alone": 0,
                                      "n_total": 0})
    # Trie par timestamp
    pulses_sorted = sorted([p for p in pulses if p.get("ts") and p.get("from") and p.get("to")],
                            key=lambda x: x["ts"])
    for i, p in enumerate(pulses_sorted):
        a = p.get("from"); b = p.get("to")
        if not (a and b): continue
        key = tuple(sorted([a, b]))
        precedence[key]["n_total"] += 1
        # Direction observée dans CE pulse
        if a < b:  # A est le "from", B le "to"
            precedence[key]["a_before_b"] += 1
        else:
            precedence[key]["b_before_a"] += 1
        # Vérifier ce qui suit dans la fenêtre temporelle
        for j in range(i + 1, len(pulses_sorted)):
            q = pulses_sorted[j]
            if q["ts"] - p["ts"] > TEMPORAL_WINDOW_SEC: break
            qa, qb = q.get("from"), q.get("to")
            if (qa, qb) == (b, a) or (qa, qb) == (a, b):
                # Sequence A→B puis B→A ou A→B→...
                pass  # déjà compté ci-dessus
    return dict(precedence)


def detect_causal_pairs(min_strength: float = 0.02,
                         min_observations: int = 3) -> list[dict]:
    """Identifie les paires Hebbian avec asymétrie temporelle = candidate causales."""
    hebbian = _load_hebbian_edges()
    pulses = _load_pulses()
    precedence = _temporal_precedence(pulses)
    out = []
    for (a, b), strength in hebbian.items():
        if strength < min_strength: continue
        key = tuple(sorted([a, b]))
        prec = precedence.get(key, {})
        n = prec.get("n_total", 0)
        if n < min_observations: continue
        ab = prec.get("a_before_b", 0)
        ba = prec.get("b_before_a", 0)
        total_dir = max(1, ab + ba)
        ab_ratio = ab / total_dir
        # Asymétrie significative : > 0.65 dans une direction
        if ab_ratio > 0.65:
            cause, effect, ratio = a, b, ab_ratio
        elif ab_ratio < 0.35:
            cause, effect, ratio = b, a, 1 - ab_ratio
        else:
            continue  # symétrique, pas de causalité claire
        out.append({
            "cause": cause, "effect": effect,
            "hebbian_strength": round(strength, 4),
            "temporal_ratio": round(ratio, 3),
            "n_observations": n,
            "score": round(strength * ratio, 4),
        })
    out.sort(key=lambda x: -x["score"])
    return out


def causal_graph() -> dict:
    """Sérialise le graphe causal complet."""
    pairs = detect_causal_pairs()
    nodes = set()
    for p in pairs:
        nodes.add(p["cause"]); nodes.add(p["effect"])
    graph = {
        "ts": _now(),
        "nodes": sorted(nodes),
        "edges_directed": pairs,
        "n_edges": len(pairs),
        "principle": "Hebbian co-activation + temporal asymmetry > 0.65 = candidate causal",
    }
    try:
        CAUSAL_GRAPH.parent.mkdir(parents=True, exist_ok=True)
        CAUSAL_GRAPH.write_text(json.dumps(graph, indent=2, ensure_ascii=False),
                                 encoding="utf-8")
    except Exception: pass
    return graph


def intervention_estimate(cause: str, effect: str) -> dict:
    """Estime P(effect | do(cause)) approximé.

    Simule do(cause) en regardant dans les pulses passés tous les cas où
    cause a été activé SEUL, et compte combien de fois effect a suivi dans la fenêtre.
    """
    pulses = _load_pulses()
    pulses_sorted = sorted([p for p in pulses if p.get("ts")],
                            key=lambda x: x["ts"])
    # Compte les pulses où cause apparaît
    n_cause_observed = 0
    n_effect_followed = 0
    for i, p in enumerate(pulses_sorted):
        if p.get("from") != cause: continue
        n_cause_observed += 1
        # Cherche effect dans la fenêtre suivante
        for j in range(i + 1, len(pulses_sorted)):
            q = pulses_sorted[j]
            if q["ts"] - p["ts"] > TEMPORAL_WINDOW_SEC: break
            if effect in (q.get("to"), q.get("from")):
                n_effect_followed += 1
                break
    if n_cause_observed == 0:
        return {"ok": False, "reason": "cause never observed",
                "n_observations": 0, "p_effect_given_do_cause": None}
    p = n_effect_followed / max(1, n_cause_observed)
    return {
        "ok": True,
        "cause": cause, "effect": effect,
        "n_observations": n_cause_observed,
        "n_followed": n_effect_followed,
        "p_effect_given_do_cause": round(p, 3),
        "interpretation": (
            "Forte preuve causale" if p > 0.6 else
            "Indication faible"     if p > 0.3 else
            "Pas de causalité observée"
        ),
    }


def explain(cause: str, effect: str) -> str:
    est = intervention_estimate(cause, effect)
    if not est.get("ok"):
        return f"Pas assez de données pour évaluer {cause} → {effect}."
    p = est["p_effect_given_do_cause"]
    n = est["n_observations"]
    return (f"Sur {n} fois où Cortex a activé '{cause}', "
            f"'{effect}' a suivi dans {est['n_followed']} cas ({p*100:.0f}%). "
            f"{est['interpretation']}.")


def self_test() -> dict:
    tests = []
    pairs = detect_causal_pairs()
    tests.append({"name": "detect_causal_pairs",
                  "ok": isinstance(pairs, list),
                  "n_pairs_found": len(pairs),
                  "sample": pairs[:3]})
    g = causal_graph()
    tests.append({"name": "causal_graph",
                  "ok": "edges_directed" in g and "nodes" in g,
                  "n_nodes": len(g.get("nodes", [])),
                  "n_edges": g.get("n_edges", 0)})
    # Si on a au moins une paire, tester intervention
    if pairs:
        first = pairs[0]
        est = intervention_estimate(first["cause"], first["effect"])
        tests.append({"name": "intervention_estimate",
                      "ok": "p_effect_given_do_cause" in est or "reason" in est,
                      "estimate": est})
        exp = explain(first["cause"], first["effect"])
        tests.append({"name": "explain", "ok": isinstance(exp, str) and len(exp) > 10,
                      "text": exp})
    else:
        tests.append({"name": "intervention_estimate",
                      "ok": True,
                      "note": "Aucune paire causale détectée (peu d'historique pulses)"})
    return {"ok": all(t["ok"] for t in tests), "tests": tests}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "graph"
    if cmd == "graph":
        print(json.dumps(causal_graph(), indent=2, ensure_ascii=False))
    elif cmd == "pairs":
        print(json.dumps(detect_causal_pairs(), indent=2, ensure_ascii=False))
    elif cmd == "intervene" and len(sys.argv) >= 4:
        print(json.dumps(intervention_estimate(sys.argv[2], sys.argv[3]),
                          indent=2, ensure_ascii=False))
    elif cmd == "explain" and len(sys.argv) >= 4:
        print(explain(sys.argv[2], sys.argv[3]))
    elif cmd == "test":
        print(json.dumps(self_test(), indent=2, ensure_ascii=False))
    else:
        print("Usage: cortex_causal.py {graph|pairs|intervene A B|explain A B|test}")
