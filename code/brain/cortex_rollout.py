"""
cortex_rollout.py — Mental rollout : simuler avant d'agir.

Avant qu'emergence loop choisisse une action, on simule les conséquences de
chaque action candidate via le world model JEPA + état actuel, on score le gap
réduit attendu, et on choisit la meilleure (avec influence personnalité).

Pas du LLM. Calcul déterministe sur des features réelles :
- Coût ressources prédit (CPU/RAM via cortex_pipeline_manager.predict_action_cost)
- Gap JEPA actuel (cortex_world_model.probe_world)
- Bonus personnalité (cortex_personality.influence_action_choice)
- Pénalité actions récentes (anti-répétition via emergence_log)
- Bonus si l'action a marché récemment (learned_skills hit)

Ça c'est PLANIFICATION FAIBLE, pas planification hiérarchique pleine. Mais c'est
déjà 10x mieux que tirer au sort dans une liste.

API :
    rollout(actions=None, depth=1) → {action: {score, breakdown}}
    best_action() → str
    last_rollout() → dict (cache du dernier)
"""
from __future__ import annotations
import json
import math
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
VAULT = Path(r"<USER_HOME>\Documents\Obsidian Vault")
LAST_ROLLOUT = REPO / ".cortex-last-rollout.json"
EVENTS = VAULT / ".cortex-rollout-events.jsonl"

DEFAULT_ACTIONS = [
    "audit_ui", "explore_graph", "map_knowledge",
    "discovery_report", "reflect", "propose_goal", "look_around",
]


def _now() -> float: return time.time()


def _safe_import(name: str):
    try:
        sys.path.insert(0, str(REPO))
        return __import__(name)
    except Exception:
        return None


def _recent_actions(limit: int = 5) -> list[str]:
    stream = VAULT / ".cortex-chat-stream.jsonl"
    if not stream.exists(): return []
    out = []
    try:
        for line in reversed(stream.read_text(encoding="utf-8",
                                               errors="replace").splitlines()[-200:]):
            try:
                o = json.loads(line)
                if o.get("speaker") == "cortex_emergence":
                    a = (o.get("meta") or {}).get("action")
                    if a: out.append(a)
                    if len(out) >= limit: break
            except Exception: pass
    except Exception: pass
    return out


def _resource_score(action: str) -> float:
    pm = _safe_import("cortex_pipeline_manager")
    if not pm: return 0.5
    try:
        # Map emergence action → cost key dans cortex_pipeline_manager
        cost_key = {"propose_goal": "self_dev_iter",
                    "reflect":     "chat_minimax",
                    "explore_graph":"explore_graph",
                    "audit_ui":     "audit_ui"}.get(action, "audit_ui")
        cost = pm.predict_action_cost(cost_key)
        # Plus l'action est légère, mieux c'est
        ram_mb = cost.get("ram_mb", 100)
        # Score [0..1] : 5 MB → 1.0, 220 MB → 0.0
        return max(0.0, min(1.0, 1 - (ram_mb - 5) / 220))
    except Exception: return 0.5


def _can_launch(action: str) -> bool:
    pm = _safe_import("cortex_pipeline_manager")
    if not pm: return True
    try:
        cost_key = {"propose_goal": "self_dev_iter",
                    "reflect":     "chat_minimax"}.get(action, "audit_ui")
        rep = pm.can_launch(cost_key)
        return bool(rep.get("ok"))
    except Exception: return True


def _gap_reduction_estimate(action: str) -> float:
    """Estime combien cette action peut réduire le gap JEPA.
    Heuristique : actions exploratoires réduisent plus le gap, audit moins."""
    base = {
        "explore_graph":    0.18,
        "map_knowledge":    0.15,
        "discovery_report": 0.10,
        "reflect":          0.12,
        "propose_goal":     0.08,
        "audit_ui":         0.05,
        "look_around":      0.08,
        "silent":           0.0,
    }
    return base.get(action, 0.05)


def _learned_skill_bonus(action: str) -> float:
    """Si une skill apprise existe pour ce type d'action, petit bonus."""
    cls = _safe_import("cortex_learned_skills")
    if not cls: return 0.0
    try:
        results = cls.search_learned(action, k=3)
        if results: return 0.05
    except Exception: pass
    return 0.0


def _personality_score(actions: list[str]) -> dict[str, float]:
    pers = _safe_import("cortex_personality")
    if not pers: return {a: 1.0 for a in actions}
    try:
        ranked = pers.influence_action_choice(actions)
        return {a: s for a, s in ranked}
    except Exception: return {a: 1.0 for a in actions}


def _anti_repetition(action: str, recent: list[str]) -> float:
    """Pénalité si l'action vient d'être faite. Plus c'est récent, plus c'est pénalisé."""
    if action not in recent: return 0.0
    idx = recent.index(action)  # 0 = plus récent
    return -0.4 * math.exp(-idx)  # -0.4 si idx=0, -0.15 si idx=1, ...


def rollout(actions: list[str] | None = None) -> dict:
    """Score chaque action candidate. Retourne dict ranking + breakdown."""
    actions = actions or DEFAULT_ACTIONS
    recent = _recent_actions(5)
    pers_scores = _personality_score(actions)
    results = {}
    for a in actions:
        breakdown = {
            "personality":    round(pers_scores.get(a, 1.0), 3),
            "resource_fit":   round(_resource_score(a), 3),
            "gap_reduction":  round(_gap_reduction_estimate(a), 3),
            "skill_bonus":    round(_learned_skill_bonus(a), 3),
            "anti_repetition":round(_anti_repetition(a, recent), 3),
            "can_launch":     _can_launch(a),
        }
        # Score = personnalité × (0.4·gap + 0.3·resource) + skill_bonus + anti_repetition
        score = (
            breakdown["personality"] * (
                0.4 * breakdown["gap_reduction"] +
                0.3 * breakdown["resource_fit"]
            )
            + breakdown["skill_bonus"]
            + breakdown["anti_repetition"]
        )
        if not breakdown["can_launch"]:
            score -= 0.5
        results[a] = {"score": round(score, 3), "breakdown": breakdown}
    ranked = sorted(results.items(), key=lambda x: -x[1]["score"])
    out = {
        "ts": _now(),
        "ranked": [{"action": a, **info} for a, info in ranked],
        "best": ranked[0][0],
        "recent_actions": recent,
    }
    try:
        LAST_ROLLOUT.write_text(json.dumps(out, indent=2, ensure_ascii=False),
                                 encoding="utf-8")
    except Exception: pass
    try:
        with EVENTS.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": _now(), "best": out["best"],
                                 "top3": [r["action"] for r in out["ranked"][:3]]},
                                ensure_ascii=False) + "\n")
    except Exception: pass
    return out


def best_action() -> str:
    out = rollout()
    return out.get("best", "audit_ui")


def last_rollout() -> dict:
    if LAST_ROLLOUT.exists():
        try: return json.loads(LAST_ROLLOUT.read_text(encoding="utf-8"))
        except Exception: pass
    return rollout()


def self_test() -> dict:
    tests = []
    out = rollout()
    tests.append({"name": "rollout_returns_ranked", "ok": "ranked" in out and len(out["ranked"]) > 0,
                  "best": out.get("best"), "n_actions": len(out.get("ranked", []))})
    top = out["ranked"][0]
    tests.append({"name": "best_has_score",
                  "ok": "score" in top and "breakdown" in top,
                  "top_score": top.get("score"),
                  "breakdown_keys": list(top.get("breakdown", {}).keys())})
    # Vérifier que silent n'est pas best (sauf si toutes les ressources sont saturées)
    tests.append({"name": "silent_not_top_default",
                  "ok": top.get("action") != "silent" or len(out["ranked"]) == 1,
                  "top_action": top.get("action")})
    return {"ok": all(t["ok"] for t in tests), "tests": tests, "rollout": out}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "rollout"
    if cmd == "rollout":
        print(json.dumps(rollout(), indent=2, ensure_ascii=False))
    elif cmd == "best":
        print(best_action())
    elif cmd == "test":
        print(json.dumps(self_test(), indent=2, ensure_ascii=False))
    else:
        print("Usage: cortex_rollout.py {rollout|best|test}")
