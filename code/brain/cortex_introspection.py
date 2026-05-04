"""
cortex_introspection.py — Méta-cognition : Cortex sait ce qu'il sait, ne sait pas, et apprend.

Pour une IAG, savoir où on en est est aussi important que de savoir.
Trois questions auxquelles ce module répond :

1. **Que SAIS-JE** ?
   - Concepts solidement représentés dans le thought_graph (degré élevé)
   - Skills apprises et persistées (cortex_learned_skills)
   - Pairs causales fortes (relations cause→effet établies)
   - Modules dont le self-test passe systématiquement

2. **Que NE SAIS-JE PAS** ?
   - Concepts isolés (orphans, degré ≤ 1)
   - Gaps JEPA non comblés (probe.confidence < 0.2)
   - Issues mémoire non résolues
   - Dimensions IAG faibles

3. **Qu'apprends-je en ce moment** ?
   - Continual learning JEPA en cours (loss qui baisse)
   - Nouvelles activations qui renforcent des edges Hebbian
   - Causalités émergentes (paires qui passent au-dessus du seuil)

Le module retourne un rapport structuré + une représentation textuelle pour humain.

API :
    introspect() → dict structuré avec 3 sections
    confidence_on(topic) → estime la confiance de Cortex sur un sujet
    say_what_i_dont_know() → sélectionne les 3 lacunes prioritaires en français
    self_test()
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
VAULT = Path(r"<USER_HOME>\Documents\Obsidian Vault")
INTRO_LOG = VAULT / ".cortex-introspection-log.jsonl"
GRAPH = VAULT / ".vault-graph.json"


def _now() -> float: return time.time()


def _safe_import(name: str):
    try:
        sys.path.insert(0, str(REPO))
        return __import__(name)
    except Exception:
        return None


def _load_graph() -> dict:
    if not GRAPH.exists(): return {}
    try: return json.loads(GRAPH.read_text(encoding="utf-8"))
    except Exception: return {}


def _short_label(s: str) -> str:
    if not s: return "?"
    last = s.replace("\\", "/").split("/")[-1]
    last = last.replace(".md", "")
    parts = last.split("-", 1)
    if len(parts) > 1 and len(parts[0]) > 6:
        last = parts[1]
    return last[:55]


def _well_known_concepts(top: int = 10) -> list[dict]:
    """Concepts solidement représentés (degré élevé dans le graphe)."""
    g = _load_graph()
    nodes = g.get("nodes", []) or []
    edges = g.get("edges", []) or []
    if not nodes: return []
    deg = [0] * len(nodes)
    for e in edges:
        if isinstance(e, list) and len(e) >= 2:
            a, b = e[0], e[1]
            if isinstance(a, int) and a < len(deg): deg[a] += 1
            if isinstance(b, int) and b < len(deg): deg[b] += 1
    indexed = [(deg[i], i) for i in range(len(nodes))]
    indexed.sort(reverse=True)
    out = []
    for d, idx in indexed[:top]:
        if d == 0: continue
        out.append({"node": str(nodes[idx]), "degree": d,
                    "label": _short_label(str(nodes[idx]))})
    return out


def _isolated_concepts(top: int = 10) -> list[dict]:
    """Concepts isolés ou peu connectés (potentiels gaps de connaissance)."""
    g = _load_graph()
    nodes = g.get("nodes", []) or []
    edges = g.get("edges", []) or []
    if not nodes: return []
    deg = [0] * len(nodes)
    for e in edges:
        if isinstance(e, list) and len(e) >= 2:
            a, b = e[0], e[1]
            if isinstance(a, int) and a < len(deg): deg[a] += 1
            if isinstance(b, int) and b < len(deg): deg[b] += 1
    out = []
    for i, d in enumerate(deg):
        if d <= 1:
            out.append({"node": str(nodes[i]), "degree": d,
                        "label": _short_label(str(nodes[i]))})
            if len(out) >= top: break
    return out


def _strong_causal_facts(top: int = 5) -> list[dict]:
    cc = _safe_import("cortex_causal")
    if not cc: return []
    try:
        pairs = cc.detect_causal_pairs(min_strength=0.05, min_observations=10)
        return [{
            "cause": _short_label(p["cause"]),
            "effect": _short_label(p["effect"]),
            "ratio": p.get("temporal_ratio", 0),
            "score": p.get("score", 0),
        } for p in pairs[:top]]
    except Exception: return []


def _learned_skills_recent(limit: int = 5) -> list[dict]:
    cls = _safe_import("cortex_learned_skills")
    if not cls: return []
    try:
        return cls.list_learned(limit) or []
    except Exception: return []


def _open_jepa_gaps() -> dict:
    cwm = _safe_import("cortex_world_model")
    if not cwm: return {"unavailable": True}
    try:
        state = cwm.read_state()
        gaps = state.get("gaps", []) or []
        return {
            "n_gaps": len(gaps),
            "recent": gaps[-5:] if gaps else [],
            "wm_cycles": state.get("cycles", 0),
        }
    except Exception as e: return {"error": str(e)}


def _learning_in_progress() -> dict:
    """Qu'est-ce que Cortex apprend EN CE MOMENT ?"""
    out = {}
    # JEPA continual
    jc = _safe_import("cortex_jepa_continual")
    if jc:
        try:
            stats = jc._load_stats()
            n_steps = stats.get("n_steps_total", 0)
            last_loss = stats.get("last_loss")
            mean_loss = stats.get("mean_loss")
            improving = (last_loss is not None and mean_loss is not None
                         and last_loss < mean_loss)
            out["jepa"] = {
                "n_steps": n_steps,
                "last_loss": last_loss,
                "improving": improving,
                "improvement_pct": round((1 - last_loss / max(1e-6, mean_loss)) * 100, 1)
                                    if improving else 0,
            }
        except Exception: pass
    # Activations actuelles
    ca = _safe_import("cortex_activation")
    if ca:
        try:
            snap = ca.snapshot()
            active = list((snap.get("active_nodes") or {}).items())[:5]
            out["currently_thinking_about"] = [
                {"label": _short_label(n), "activation": v}
                for n, v in active
            ]
            out["n_active"] = snap.get("n_active", 0)
            out["cum_hebbian_ticks"] = snap.get("cum_hebbian_ticks", 0)
        except Exception: pass
    # Plan en cours
    pl = _safe_import("cortex_plan")
    if pl:
        try:
            d = pl.daily_plan()
            out["working_on_goals"] = [g.get("title", "")[:80]
                                        for g in d.get("goals", [])
                                        if not g.get("completed")][:3]
        except Exception: pass
    return out


def _weak_dimensions() -> list[dict]:
    """Quelles dimensions IAG sont faibles ?"""
    it = _safe_import("cortex_iag_test")
    if not it: return []
    try:
        rep = it.run_iag_test()
        dims = rep.get("dimensions", {})
        weak = []
        for name, d in dims.items():
            score = d.get("score", 0)
            if score < 50:
                weak.append({"dimension": name, "score": score,
                             "human": _humanize_dim(name)})
        return weak
    except Exception: return []


def _humanize_dim(name: str) -> str:
    return {
        "causality":           "comprendre les vraies relations cause→effet",
        "planning":            "planifier sur plusieurs niveaux temporels",
        "continual_learning":  "apprendre en continu sans tout oublier",
        "self_reflection":     "te parler spontanément quand pertinent",
        "memory_correction":   "auditer et corriger ma mémoire",
        "resource_self_mgmt":  "gérer mes ressources matérielles",
        "world_model_accuracy":"prédire ce que je devrais savoir",
    }.get(name, name)


def introspect() -> dict:
    """Rapport d'introspection complet."""
    rep = {
        "ts": _now(),
        "what_i_know": {
            "well_known_concepts": _well_known_concepts(top=8),
            "strong_causal_facts": _strong_causal_facts(top=5),
            "learned_skills":      _learned_skills_recent(limit=5),
        },
        "what_i_dont_know": {
            "isolated_concepts": _isolated_concepts(top=8),
            "open_jepa_gaps":    _open_jepa_gaps(),
            "weak_dimensions":   _weak_dimensions(),
        },
        "what_im_learning_now": _learning_in_progress(),
    }
    # Audit mémoire = je sais que je dois corriger ma mémoire
    ma = _safe_import("cortex_memory_audit")
    if ma:
        try:
            audit_file = VAULT / ".cortex-memory-audit-report.json"
            if audit_file.exists():
                rep["what_i_dont_know"]["memory_issues"] = json.loads(
                    audit_file.read_text(encoding="utf-8")).get("by_type", {})
        except Exception: pass
    try:
        with INTRO_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": _now(),
                                 "n_known": len(rep["what_i_know"]["well_known_concepts"]),
                                 "n_unknown": len(rep["what_i_dont_know"]["isolated_concepts"]),
                                 "n_weak_dims": len(rep["what_i_dont_know"]["weak_dimensions"])},
                                ensure_ascii=False) + "\n")
    except Exception: pass
    return rep


def confidence_on(topic: str) -> dict:
    """Cortex estime sa confiance sur un sujet donné."""
    cwm = _safe_import("cortex_world_model")
    if not cwm:
        return {"ok": False, "reason": "world_model unavailable"}
    try:
        probe = cwm.probe_world(topic)
        confidence = probe.get("confidence", 0) or 0
        gap = probe.get("gap", 0) or 0
        mode = probe.get("mode", "unknown")
        # Mapping confidence → label humain
        if confidence > 0.5:
            label = "je m'y connais bien"
        elif confidence > 0.2:
            label = "j'ai une idée mais c'est flou"
        elif confidence > 0.05:
            label = "très peu d'éléments"
        else:
            label = "je ne sais quasi rien"
        return {
            "ok": True,
            "topic": topic,
            "confidence": round(confidence, 4),
            "gap": round(gap, 4),
            "mode": mode,
            "label": label,
            "reason": probe.get("reason", "")[:200],
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def say_what_i_dont_know() -> str:
    """Texte humain : 3 lacunes prioritaires."""
    rep = introspect()
    weak = rep["what_i_dont_know"].get("weak_dimensions", [])
    isolated = rep["what_i_dont_know"].get("isolated_concepts", [])
    issues = rep["what_i_dont_know"].get("memory_issues", {})
    lines = []
    lines.append("Voici ce que je SAIS que je ne sais pas :")
    if weak:
        lines.append("")
        lines.append("**Mes capacités faibles** (dimensions IAG sous 50/100) :")
        for w in weak[:3]:
            lines.append(f"- {w['human']} (score {w['score']}/100)")
    if isolated:
        n = len(isolated)
        lines.append("")
        lines.append(f"**{n} concepts isolés** dans ma mémoire qui ne sont liés à rien :")
        for c in isolated[:3]:
            lines.append(f"- `{c['label']}` (degré {c['degree']})")
        if n > 3:
            lines.append(f"...et {n - 3} autres")
    if issues:
        lines.append("")
        lines.append("**Problèmes mémoire** détectés :")
        for k, v in issues.items():
            if v > 0:
                k_human = {
                    "contradictions":      "contradictions",
                    "obsolete_paths":      "chemins obsolètes",
                    "incoherent_endpoints":"endpoints morts",
                    "duplicates":          "doublons",
                }.get(k, k)
                lines.append(f"- {v} {k_human}")
    return "\n".join(lines)


def self_test() -> dict:
    tests = []
    rep = introspect()
    tests.append({
        "name": "introspect_returns_3_sections",
        "ok": all(k in rep for k in ("what_i_know", "what_i_dont_know", "what_im_learning_now")),
        "n_known": len(rep["what_i_know"]["well_known_concepts"]),
        "n_unknown": len(rep["what_i_dont_know"]["isolated_concepts"]),
        "n_learning": rep["what_im_learning_now"].get("n_active", 0),
    })
    text = say_what_i_dont_know()
    tests.append({
        "name": "say_what_i_dont_know",
        "ok": isinstance(text, str) and len(text) > 30,
        "preview": text[:200],
    })
    conf = confidence_on("JEPA world model autonomous prediction")
    tests.append({
        "name": "confidence_on",
        "ok": "label" in conf or "error" in conf,
        "label": conf.get("label"),
        "confidence": conf.get("confidence"),
    })
    return {"ok": all(t["ok"] for t in tests), "tests": tests}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "introspect"
    if cmd == "introspect":
        print(json.dumps(introspect(), indent=2, ensure_ascii=False))
    elif cmd == "say":
        print(say_what_i_dont_know())
    elif cmd == "confidence" and len(sys.argv) > 2:
        topic = " ".join(sys.argv[2:])
        print(json.dumps(confidence_on(topic), indent=2, ensure_ascii=False))
    elif cmd == "test":
        print(json.dumps(self_test(), indent=2, ensure_ascii=False))
    else:
        print("Usage: cortex_introspection.py {introspect|say|confidence <topic>|test}")
