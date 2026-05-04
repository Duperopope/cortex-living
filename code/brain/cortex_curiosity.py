"""
cortex_curiosity.py — Récompense intrinsèque de curiosité (Schmidhuber-style).

Pas un goal externe. Une PRESSION INTERNE qui pousse Cortex à explorer ce qui
réduit le plus son incertitude.

Principe (J. Schmidhuber, "Formal theory of creativity, fun, and intrinsic
motivation", 2010) : la récompense intrinsèque vient de la RÉDUCTION DE
COMPRESSION ERROR — apprendre des choses qui SIMPLIFIENT son modèle du monde.

Adaptation pour Cortex :
- compression_error_t = JEPA gap moyen sur les concepts actifs
- reward = compression_error_{t-1} - compression_error_t  (improvement)
- Si reward > seuil : trace un événement de "curiosité satisfaite"
- Si reward < 0 : "frustration cognitive" → propose une nouvelle exploration

Génère des QUESTIONS de curiosité concrètes que Cortex se pose à lui-même :
- "Pourquoi `concept_A` est-il isolé ?"
- "Quel lien entre `causal_pair_X` et `concept_proche_Y` ?"
- "Ma loss JEPA stagne sur `topic_Z`, qu'est-ce qui m'échappe ?"

API :
    measure_compression_error() → float (gap moyen actuel)
    intrinsic_reward() → float (delta vs measure précédente)
    generate_questions(n=3) → list[str] questions concrètes
    drive_step() → exécute UN cycle : mesure + question + log
    stats() → état historique
    self_test()
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
VAULT = Path(r"<USER_HOME>\Documents\Obsidian Vault")
STATE = VAULT / ".cortex-curiosity-state.json"
LOG = VAULT / ".cortex-curiosity-events.jsonl"

REWARD_THRESHOLD = 0.05  # delta minimum pour considérer une vraie réduction
FRUSTRATION_THRESHOLD = -0.03  # delta négatif → frustration


def _now() -> float: return time.time()


def _safe_import(name: str):
    try:
        sys.path.insert(0, str(REPO))
        return __import__(name)
    except Exception:
        return None


def _load_state() -> dict:
    if STATE.exists():
        try: return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception: pass
    return {
        "version": "curiosity-1",
        "history": [],
        "n_steps": 0,
        "n_curiosity_satisfied": 0,
        "n_frustrations": 0,
        "questions_asked": [],
        "questions_answered": [],
    }


def _save_state(s: dict) -> None:
    s["updated_at"] = _now()
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(s, indent=2, ensure_ascii=False),
                          encoding="utf-8")
    except Exception: pass


def _log_event(ev: dict) -> None:
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": _now(), **ev}, ensure_ascii=False) + "\n")
    except Exception: pass


def _short_label(s: str) -> str:
    if not s: return "?"
    last = s.replace("\\", "/").split("/")[-1]
    last = last.replace(".md", "")
    parts = last.split("-", 1)
    if len(parts) > 1 and len(parts[0]) > 6:
        last = parts[1]
    return last[:55]


def measure_compression_error() -> dict:
    """Mesure la 'compression error' actuelle = combien Cortex échoue à
    prédire ce qu'il devrait savoir. Ici proxy via :
    1) gap JEPA mesuré sur quelques topics actifs
    2) ratio de concepts isolés vs total
    3) loss JEPA continual la plus récente
    """
    sources = {}
    # Source 1 : isolated ratio
    g_path = VAULT / ".vault-graph.json"
    isolated_ratio = 0.5
    if g_path.exists():
        try:
            g = json.loads(g_path.read_text(encoding="utf-8"))
            nodes = g.get("nodes", [])
            edges = g.get("edges", [])
            n = len(nodes)
            if n > 0:
                deg = [0] * n
                for e in edges:
                    if isinstance(e, list) and len(e) >= 2:
                        a, b = e[0], e[1]
                        if isinstance(a, int) and a < n: deg[a] += 1
                        if isinstance(b, int) and b < n: deg[b] += 1
                isolated = sum(1 for d in deg if d <= 1)
                isolated_ratio = isolated / n
                sources["isolated_ratio"] = round(isolated_ratio, 4)
        except Exception: pass

    # Source 2 : JEPA loss
    jepa_loss = 1.0
    jc = _safe_import("cortex_jepa_continual")
    if jc:
        try:
            stats = jc._load_stats()
            ll = stats.get("last_loss")
            if ll is not None:
                jepa_loss = float(ll)
                sources["jepa_loss"] = round(jepa_loss, 6)
        except Exception: pass

    # Source 3 : JEPA gap moyen (probe sur quelques sujets actifs)
    avg_gap = 0.5
    cwm = _safe_import("cortex_world_model")
    ca = _safe_import("cortex_activation")
    if cwm and ca:
        try:
            snap = ca.snapshot()
            active = list((snap.get("active_nodes") or {}).keys())[:3]
            if active:
                gaps = []
                for label in active:
                    try:
                        probe = cwm.probe_world(_short_label(label))
                        gap = probe.get("gap")
                        if isinstance(gap, (int, float)):
                            gaps.append(gap)
                    except Exception: pass
                if gaps:
                    avg_gap = sum(gaps) / len(gaps)
                    sources["jepa_gap_avg"] = round(avg_gap, 4)
        except Exception: pass

    # Compression error = combinaison normalisée de [0..1]
    compression_error = (
        0.4 * isolated_ratio +
        0.3 * min(1.0, jepa_loss / 5.0) +  # normalise loss à [0..1]
        0.3 * avg_gap
    )
    return {
        "ts": _now(),
        "compression_error": round(compression_error, 4),
        "sources": sources,
    }


def intrinsic_reward() -> dict:
    """Delta de compression error vs mesure précédente."""
    state = _load_state()
    history = state.get("history", [])
    current = measure_compression_error()
    if not history:
        # Pas d'historique : reward = 0, on ajoute juste la mesure
        history.append(current)
        state["history"] = history[-30:]
        state["n_steps"] = state.get("n_steps", 0) + 1
        _save_state(state)
        return {"ok": True, "reward": 0.0, "first_measurement": True,
                "current_error": current["compression_error"]}
    previous = history[-1]
    reward = previous["compression_error"] - current["compression_error"]
    # Update history
    history.append(current)
    state["history"] = history[-30:]
    state["n_steps"] = state.get("n_steps", 0) + 1
    if reward > REWARD_THRESHOLD:
        state["n_curiosity_satisfied"] = state.get("n_curiosity_satisfied", 0) + 1
        outcome = "curiosity_satisfied"
    elif reward < FRUSTRATION_THRESHOLD:
        state["n_frustrations"] = state.get("n_frustrations", 0) + 1
        outcome = "frustration"
    else:
        outcome = "neutral"
    _save_state(state)
    _log_event({"type": outcome, "reward": round(reward, 4),
                 "current_error": current["compression_error"],
                 "previous_error": previous["compression_error"]})
    return {
        "ok": True,
        "reward": round(reward, 4),
        "outcome": outcome,
        "current_error": current["compression_error"],
        "previous_error": previous["compression_error"],
    }


def generate_questions(n: int = 3) -> list[str]:
    """Cortex se pose des questions de curiosité concrètes."""
    questions = []
    intro = _safe_import("cortex_introspection")
    cc = _safe_import("cortex_causal")
    cwm = _safe_import("cortex_world_model")

    # Type 1 : pourquoi ce concept isolé ?
    if intro:
        try:
            rep = intro.introspect()
            isolated = rep.get("what_i_dont_know", {}).get("isolated_concepts", [])
            for c in isolated[:max(1, n // 3)]:
                questions.append(
                    f"Pourquoi `{c['label']}` est-il isolé dans mon graphe ? "
                    f"Quels concepts existants devraient s'y connecter ?")
        except Exception: pass

    # Type 2 : qu'est-ce qui suit cette cause ?
    if cc:
        try:
            pairs = cc.detect_causal_pairs(min_strength=0.04, min_observations=5)
            for p in pairs[:max(1, n // 3)]:
                questions.append(
                    f"`{_short_label(p['cause'])}` précède souvent "
                    f"`{_short_label(p['effect'])}` ({int(p['temporal_ratio']*100)}%). "
                    f"Pourquoi ? Quelle est la mécanique sous-jacente ?")
        except Exception: pass

    # Type 3 : sujet où le world model est faible
    if cwm:
        try:
            state = cwm.read_state()
            gaps = state.get("gaps", []) or []
            for g in gaps[-max(1, n // 3):]:
                if isinstance(g, dict) and g.get("query"):
                    questions.append(
                        f"Mon world model est faible sur « {g['query'][:80]} ». "
                        f"Quelle question dois-je creuser ?")
                elif isinstance(g, str):
                    questions.append(
                        f"Mon world model est faible sur « {g[:80]} ». À approfondir.")
        except Exception: pass

    # Garde-fou : si aucune question générée, fallback générique
    if not questions:
        questions.append(
            "Quelles connexions inattendues peuvent exister dans mon graphe sémantique ?")
    return questions[:n]


def drive_step() -> dict:
    """UN cycle complet de curiosité : mesure + reward + génère questions."""
    measurement = intrinsic_reward()
    questions = generate_questions(n=3)
    state = _load_state()
    state["questions_asked"] = (state.get("questions_asked", []) + questions)[-30:]
    _save_state(state)
    rep = {
        "ok": True,
        "ts": _now(),
        "compression_error": measurement.get("current_error"),
        "reward": measurement.get("reward"),
        "outcome": measurement.get("outcome", "first_measurement"),
        "questions_generated": questions,
    }
    _log_event({"type": "drive_step", **{k: v for k, v in rep.items() if k != "ts"}})
    return rep


def stats() -> dict:
    s = _load_state()
    history = s.get("history", [])
    if len(history) >= 2:
        deltas = [history[i+1]["compression_error"] - history[i]["compression_error"]
                  for i in range(len(history) - 1)]
        avg_delta = sum(deltas) / len(deltas)
    else:
        avg_delta = 0
    return {
        "n_steps": s.get("n_steps", 0),
        "n_curiosity_satisfied": s.get("n_curiosity_satisfied", 0),
        "n_frustrations": s.get("n_frustrations", 0),
        "current_error": history[-1]["compression_error"] if history else None,
        "history_size": len(history),
        "avg_delta_per_step": round(avg_delta, 5),
        "improving_overall": avg_delta < 0,  # delta négatif = error qui baisse
        "recent_questions": s.get("questions_asked", [])[-3:],
    }


def self_test() -> dict:
    tests = []
    m = measure_compression_error()
    tests.append({"name": "measure_compression_error",
                  "ok": "compression_error" in m and isinstance(m["compression_error"], (int, float)),
                  "value": m.get("compression_error"),
                  "sources": list((m.get("sources") or {}).keys())})
    r = intrinsic_reward()
    tests.append({"name": "intrinsic_reward",
                  "ok": "reward" in r,
                  "reward": r.get("reward"),
                  "outcome": r.get("outcome")})
    qs = generate_questions(3)
    tests.append({"name": "generate_questions",
                  "ok": isinstance(qs, list) and len(qs) > 0 and all(isinstance(q, str) for q in qs),
                  "n_questions": len(qs),
                  "sample": qs[:1]})
    step = drive_step()
    tests.append({"name": "drive_step",
                  "ok": step.get("ok") and "questions_generated" in step,
                  "compression_error": step.get("compression_error")})
    s = stats()
    tests.append({"name": "stats",
                  "ok": "n_steps" in s,
                  "n_steps": s.get("n_steps")})
    return {"ok": all(t["ok"] for t in tests), "tests": tests}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "step"
    if cmd == "step":
        print(json.dumps(drive_step(), indent=2, ensure_ascii=False))
    elif cmd == "measure":
        print(json.dumps(measure_compression_error(), indent=2, ensure_ascii=False))
    elif cmd == "questions":
        print(json.dumps({"questions": generate_questions(5)},
                          indent=2, ensure_ascii=False))
    elif cmd == "stats":
        print(json.dumps(stats(), indent=2, ensure_ascii=False))
    elif cmd == "test":
        print(json.dumps(self_test(), indent=2, ensure_ascii=False))
    else:
        print("Usage: cortex_curiosity.py {step|measure|questions|stats|test}")
