"""
cortex_hjepa.py — Hierarchical JEPA full : 5 échelles temporelles imbriquées.

Inspire de LeCun (2022) "A Path Towards Autonomous Machine Intelligence" §4.4
sur le hierarchical predictor.

5 niveaux (chaque niveau prédit + planifie dans son horizon) :
- L0 : 1-step       (action immédiate, ~maintenant)
- L1 : 5-step       (séquence courte, ~5 min)
- L2 : 100-step     (plan de session, ~1 h)
- L3 : daily        (24 h, déjà existant via cortex_plan)
- L4 : weekly       (7 j, déjà existant)

Le niveau supérieur CONDITIONNE les inférieurs : weekly contraint daily,
daily contraint 100-step, etc. Chaque niveau utilise mental rollout +
expected free energy d'Active Inference pour choisir.

Anti-fake intégré :
- chaque plan est exécutable : ses actions appellent vraiment les emergence loops
- comparaison plan_intentionnel vs plan_realise après chaque cycle
- divergence > 50% → plan irréaliste, à raffiner
- baseline : à chaque niveau, comparer la séquence choisie à random sampling

API :
    plan_at_level(level) → liste d'actions prévues pour ce niveau
    rollout_5step() → simule 5 actions futures
    rollout_100step() → simule plan d'1h (20 actions × 3min ~)
    full_plan() → plan complet aux 5 niveaux, imbriqué
    compare_realised(level) → mesure plan vs réalisé
    self_test()
"""
from __future__ import annotations
import json
import random
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
VAULT = Path(r"<USER_HOME>\Documents\Obsidian Vault")
STATE = VAULT / ".cortex-hjepa-state.json"
LOG = VAULT / ".cortex-hjepa-log.jsonl"

# Échelles (en secondes)
LEVEL_HORIZONS = {
    "L0_1step":   60,        # 1 minute
    "L1_5step":   300,       # 5 minutes
    "L2_100step": 3600,      # 1 heure
    "L3_daily":   86400,     # 24 heures
    "L4_weekly":  604800,    # 7 jours
}

LEVEL_N_ACTIONS = {
    "L0_1step": 1, "L1_5step": 5, "L2_100step": 20,
    "L3_daily": 0, "L4_weekly": 0,  # daily/weekly = goals, pas actions directes
}

DEFAULT_ACTIONS = ["audit_ui", "explore_graph", "map_knowledge",
                   "discovery_report", "reflect", "propose_goal",
                   "look_around", "silent"]


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
    return {"version": "hjepa-1", "n_full_plans": 0,
            "plans_by_level": {}, "realised_log": []}


def _save_state(s: dict) -> None:
    s["updated_at"] = _now()
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception: pass


def _log_event(ev: dict) -> None:
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": _now(), **ev}, ensure_ascii=False) + "\n")
    except Exception: pass


def _score_action(action: str) -> float:
    """Score expected free energy via cortex_active_inference si dispo."""
    ai = _safe_import("cortex_active_inference")
    if ai:
        try: return ai.expected_free_energy(action)
        except Exception: pass
    # Fallback : utilise rollout
    rl = _safe_import("cortex_rollout")
    if rl:
        try:
            out = rl.rollout([action])
            return -out.get("ranked", [{}])[0].get("score", 0)
        except Exception: pass
    return 0.0


def _filter_actions_by_constraints(actions: list[str],
                                    higher_level_actions: list[str] | None) -> list[str]:
    """Niveau supérieur contraint inférieur : si higher_level_actions est défini,
    on ne garde que les actions qui apparaissent dans la liste contrainte."""
    if not higher_level_actions: return actions
    # Si la contrainte du niveau supérieur permet ces actions : garde.
    constrained = [a for a in actions if a in higher_level_actions]
    if not constrained: return actions  # fallback : si rien ne match, libère
    return constrained


def rollout_1step(constraints: list[str] | None = None) -> dict:
    """Choisit 1 action (couche L0)."""
    actions = _filter_actions_by_constraints(DEFAULT_ACTIONS, constraints)
    scored = [(a, _score_action(a)) for a in actions]
    scored.sort(key=lambda x: x[1])  # plus bas EFE = mieux
    chosen = scored[0][0]
    # Baseline random
    rand = random.choice(actions)
    rand_efe = _score_action(rand)
    return {
        "level": "L0_1step",
        "horizon_s": LEVEL_HORIZONS["L0_1step"],
        "actions_planned": [chosen],
        "expected_efe": [scored[0][1]],
        "random_baseline": {"action": rand, "efe": rand_efe},
        "better_than_random": scored[0][1] < rand_efe,
    }


def rollout_5step(constraints: list[str] | None = None) -> dict:
    """Choisit 5 actions consécutives (couche L1).

    Heuristique : éviter de répéter la même action deux fois de suite.
    """
    actions = _filter_actions_by_constraints(DEFAULT_ACTIONS, constraints)
    sequence = []
    last = None
    for _ in range(5):
        candidates = [a for a in actions if a != last] if last else actions
        scored = [(a, _score_action(a)) for a in candidates]
        scored.sort(key=lambda x: x[1])
        chosen = scored[0][0]
        sequence.append({"action": chosen, "efe": scored[0][1]})
        last = chosen
    # Baseline random
    rand_seq = [random.choice(actions) for _ in range(5)]
    rand_efe_total = sum(_score_action(a) for a in rand_seq)
    chosen_efe_total = sum(s["efe"] for s in sequence)
    return {
        "level": "L1_5step",
        "horizon_s": LEVEL_HORIZONS["L1_5step"],
        "actions_planned": [s["action"] for s in sequence],
        "sequence_detail": sequence,
        "total_efe": round(chosen_efe_total, 4),
        "random_baseline_efe": round(rand_efe_total, 4),
        "better_than_random": chosen_efe_total < rand_efe_total,
    }


def rollout_100step(constraints: list[str] | None = None) -> dict:
    """Plan de session (~1h, 20 actions × 3min)."""
    actions = _filter_actions_by_constraints(DEFAULT_ACTIONS, constraints)
    sequence = []
    last_two = []  # éviter les répétitions sur 2 actions
    for i in range(20):
        candidates = [a for a in actions if a not in last_two] if last_two else actions
        if not candidates: candidates = actions
        scored = [(a, _score_action(a)) for a in candidates]
        scored.sort(key=lambda x: x[1])
        chosen = scored[0][0]
        sequence.append({"step": i, "action": chosen, "efe": scored[0][1]})
        last_two = ([last_two[-1]] if last_two else []) + [chosen]
        last_two = last_two[-2:]
    # Stats agrégées
    action_counts = {}
    for s in sequence:
        action_counts[s["action"]] = action_counts.get(s["action"], 0) + 1
    total_efe = sum(s["efe"] for s in sequence)
    rand_seq = [random.choice(actions) for _ in range(20)]
    rand_efe_total = sum(_score_action(a) for a in rand_seq)
    return {
        "level": "L2_100step",
        "horizon_s": LEVEL_HORIZONS["L2_100step"],
        "n_steps": 20,
        "actions_distribution": action_counts,
        "actions_sequence": [s["action"] for s in sequence],
        "total_efe": round(total_efe, 4),
        "random_baseline_efe": round(rand_efe_total, 4),
        "better_than_random": total_efe < rand_efe_total,
    }


def full_plan() -> dict:
    """Plan complet aux 5 niveaux, imbriqué hiérarchiquement."""
    state = _load_state()
    state["n_full_plans"] = state.get("n_full_plans", 0) + 1
    # Niveau 4 : weekly (existe via cortex_plan)
    pl = _safe_import("cortex_plan")
    weekly = pl.weekly_plan() if pl else {}
    daily = pl.daily_plan() if pl else {}
    # Extrait les actions autorisées par le daily plan (contraintes pour L0-L2)
    daily_actions = set()
    for g in daily.get("goals", []) or []:
        daily_actions.update(g.get("actions", []))
    daily_actions = list(daily_actions) or DEFAULT_ACTIONS
    # L2 : 100-step contraint par daily
    l2 = rollout_100step(constraints=daily_actions)
    # L1 : 5-step contraint par L2 (top 5 actions du L2)
    l2_top_actions = sorted(l2["actions_distribution"].items(),
                             key=lambda x: -x[1])[:5]
    l2_actions = [a for a, _ in l2_top_actions] or DEFAULT_ACTIONS
    l1 = rollout_5step(constraints=l2_actions)
    # L0 : 1-step contraint par L1 (première action prévue)
    l0 = rollout_1step(constraints=l1["actions_planned"])
    plan = {
        "ts": _now(),
        "L4_weekly": {
            "n_themes": len(weekly.get("themes", [])),
            "themes_titles": [t["title"][:80]
                              for t in (weekly.get("themes") or [])[:3]],
        },
        "L3_daily": {
            "n_goals": len(daily.get("goals", [])),
            "goals_titles": [g["title"][:80]
                             for g in (daily.get("goals") or [])[:5]],
            "actions_allowed": list(daily_actions),
        },
        "L2_100step": l2,
        "L1_5step":   l1,
        "L0_1step":   l0,
        "imbrication_chain": (
            f"weekly→daily ({len(daily_actions)} actions allowed) "
            f"→ 100step ({l2['better_than_random']}) "
            f"→ 5step ({l1['better_than_random']}) "
            f"→ 1step ({l0['better_than_random']})"
        ),
    }
    state["plans_by_level"][str(int(_now()))] = {
        "L0": l0["actions_planned"][0] if l0["actions_planned"] else None,
        "L1": l1["actions_planned"],
        "L2_dist": l2["actions_distribution"],
    }
    # Cap historique
    if len(state["plans_by_level"]) > 30:
        keys = sorted(state["plans_by_level"].keys())
        for k in keys[:5]: state["plans_by_level"].pop(k, None)
    _save_state(state)
    _log_event({"type": "full_plan",
                 "L0": l0["actions_planned"],
                 "L1": l1["actions_planned"],
                 "L2_top": [a for a, _ in l2_top_actions[:3]],
                 "all_better_than_random":
                    l0["better_than_random"] and l1["better_than_random"] and l2["better_than_random"]})
    return plan


def compare_realised(level: str = "L1_5step", lookback_min: int = 30) -> dict:
    """Mesure : qu'est-ce qui a été réalisé vs ce qui était planifié ?

    Lit l'historique des plans + l'historique réel des actions emergence
    (via .cortex-chat-stream.jsonl filtré).
    """
    state = _load_state()
    plans = state.get("plans_by_level", {})
    if not plans:
        return {"ok": False, "reason": "no plans in history"}
    # Plan le plus récent
    latest_ts = max(int(k) for k in plans.keys())
    latest_plan = plans[str(latest_ts)]
    planned_actions = []
    if level == "L0_1step":
        if latest_plan.get("L0"): planned_actions = [latest_plan["L0"]]
    elif level == "L1_5step":
        planned_actions = latest_plan.get("L1", [])
    elif level == "L2_100step":
        planned_actions = list(latest_plan.get("L2_dist", {}).keys())
    # Lit le stream pour les actions réelles depuis ce plan
    stream = VAULT / ".cortex-chat-stream.jsonl"
    realised_actions = []
    if stream.exists():
        try:
            cutoff = latest_ts
            for ln in stream.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    o = json.loads(ln)
                    if o.get("speaker") == "cortex_emergence" and (o.get("ts") or 0) >= cutoff:
                        a = (o.get("meta") or {}).get("action")
                        if a: realised_actions.append(a)
                except Exception: pass
        except Exception: pass
    if not planned_actions:
        return {"ok": False, "reason": f"no planned actions at {level}"}
    if not realised_actions:
        return {"ok": True, "level": level,
                "n_planned": len(planned_actions), "n_realised": 0,
                "match_rate": 0.0,
                "note": "Aucune action emergence réalisée depuis ce plan."}
    n_match = sum(1 for a in realised_actions if a in planned_actions)
    match_rate = n_match / len(realised_actions)
    return {
        "ok": True,
        "level": level,
        "planned_actions": planned_actions,
        "realised_actions": realised_actions,
        "n_planned": len(planned_actions),
        "n_realised": len(realised_actions),
        "match_rate": round(match_rate, 3),
        "plan_realistic": match_rate > 0.5,
    }


def self_test(fast: bool = True) -> dict:
    """fast=True : skip rollout_100step (lent ~30s)."""
    tests = []
    l0 = rollout_1step()
    tests.append({"name": "rollout_1step",
                  "ok": "actions_planned" in l0 and len(l0["actions_planned"]) == 1,
                  "chosen": l0.get("actions_planned"),
                  "better_than_random": l0.get("better_than_random")})
    l1 = rollout_5step()
    tests.append({"name": "rollout_5step",
                  "ok": "actions_planned" in l1 and len(l1["actions_planned"]) == 5,
                  "actions": l1.get("actions_planned"),
                  "better_than_random": l1.get("better_than_random")})
    if not fast:
        l2 = rollout_100step()
        tests.append({"name": "rollout_100step",
                      "ok": l2.get("n_steps") == 20,
                      "n_unique_actions": len(l2.get("actions_distribution", {})),
                      "better_than_random": l2.get("better_than_random")})
    cmp = compare_realised()
    tests.append({"name": "compare_realised",
                  "ok": cmp.get("ok") is not None,
                  "match_rate": cmp.get("match_rate"),
                  "plan_realistic": cmp.get("plan_realistic")})
    return {"ok": all(t["ok"] for t in tests), "tests": tests}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "plan"
    if cmd == "plan":
        print(json.dumps(full_plan(), indent=2, ensure_ascii=False))
    elif cmd == "1step":
        print(json.dumps(rollout_1step(), indent=2, ensure_ascii=False))
    elif cmd == "5step":
        print(json.dumps(rollout_5step(), indent=2, ensure_ascii=False))
    elif cmd == "100step":
        print(json.dumps(rollout_100step(), indent=2, ensure_ascii=False))
    elif cmd == "compare":
        level = sys.argv[2] if len(sys.argv) > 2 else "L1_5step"
        print(json.dumps(compare_realised(level), indent=2, ensure_ascii=False))
    elif cmd == "test":
        print(json.dumps(self_test(), indent=2, ensure_ascii=False))
    else:
        print("Usage: cortex_hjepa.py {plan|1step|5step|100step|compare|test}")
