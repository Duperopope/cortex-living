"""
cortex_plan.py — Plan hiérarchique 3 niveaux pour Cortex.

Inspire de H-JEPA (LeCun) : des plans à différentes échelles temporelles
qui s'imbriquent et se révisent.

3 niveaux :
- IMMEDIATE (action courante, < 5 min) : géré par cortex_emergence + rollout.
- DAILY (~24h) : 3-5 objectifs concrets pour la journée. Re-évalués chaque heure.
- WEEKLY (~7j) : 1-3 thèmes de progression long terme. Révisés chaque jour.

Pas de fake : les plans sont GÉNÉRÉS depuis l'état réel (gaps JEPA, causal pairs,
learned skills, world model events) et validés contre le réel.

API :
    daily_plan() → {goals[], created_at, deadline}
    weekly_plan() → {themes[], created_at}
    propose_next_action() → choisit l'action immédiate qui sert le mieux les
        plans daily/weekly via rollout
    review() → marque les goals atteints/échoués
    self_test() → vérifie tout
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
VAULT = Path(r"<USER_HOME>\Documents\Obsidian Vault")
PLAN_FILE = VAULT / ".cortex-plan.json"
PLAN_LOG = VAULT / ".cortex-plan-events.jsonl"

DAY_S = 86400
WEEK_S = 7 * DAY_S


def _now() -> float: return time.time()


def _safe_import(name: str):
    try:
        sys.path.insert(0, str(REPO))
        return __import__(name)
    except Exception:
        return None


def _load() -> dict:
    if PLAN_FILE.exists():
        try: return json.loads(PLAN_FILE.read_text(encoding="utf-8"))
        except Exception: pass
    return {"daily": None, "weekly": None, "history": []}


def _save(plan: dict) -> None:
    plan["updated_at"] = _now()
    try:
        PLAN_FILE.parent.mkdir(parents=True, exist_ok=True)
        PLAN_FILE.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception: pass


def _log(event: dict) -> None:
    try:
        with PLAN_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": _now(), **event}, ensure_ascii=False) + "\n")
    except Exception: pass


def _gather_signals() -> dict:
    """Collecte les signaux réels qui nourrissent la génération de plans."""
    signals = {}
    # World model gaps actifs
    wm = _safe_import("cortex_world_model")
    if wm:
        try:
            state = wm.read_state()
            signals["wm_gaps"] = (state.get("gaps") or [])[-5:]
            signals["wm_cycles"] = state.get("cycles", 0)
            signals["wm_autonomous"] = state.get("autonomous", False)
        except Exception: pass
    # Causal hot pairs
    causal = _safe_import("cortex_causal")
    if causal:
        try:
            pairs = causal.detect_causal_pairs(min_strength=0.02, min_observations=3)
            signals["causal_top"] = pairs[:5]
        except Exception: pass
    # Learned skills récentes
    cls = _safe_import("cortex_learned_skills")
    if cls:
        try:
            signals["learned_skills_recent"] = cls.list_learned(5)
        except Exception: pass
    # Vitals
    pm = _safe_import("cortex_pipeline_manager")
    if pm:
        try:
            signals["vitals"] = pm._vital_signs()
        except Exception: pass
    # Personnalité actuelle
    pers = _safe_import("cortex_personality")
    if pers:
        try:
            s = pers.state()
            signals["personality_big5"] = s.get("big5", {})
            signals["personality_mood"] = s.get("mood", {})
        except Exception: pass
    return signals


# Templates de goals dérivés des signaux. Pas du LLM : règles déterministes.
def _generate_daily_goals(signals: dict) -> list[dict]:
    goals = []
    big5 = signals.get("personality_big5", {})
    mood = signals.get("personality_mood", {})
    # Si openness élevée → exploration
    if big5.get("openness", 0.5) > 0.7:
        goals.append({
            "id": "g_explore",
            "title": "Explorer 5 nouvelles connexions sémantiques inattendues",
            "metric": "n_explore_graph_runs",
            "target": 5,
            "current": 0,
            "actions": ["explore_graph", "map_knowledge"],
            "rationale": "Big5 openness élevée — favoriser les associations distantes",
        })
    # Si conscientiousness élevée → audit
    if big5.get("conscientiousness", 0.5) > 0.7:
        goals.append({
            "id": "g_audit",
            "title": "Auditer l'IHM et corriger 1 problème détecté",
            "metric": "n_audit_ui_runs",
            "target": 3,
            "current": 0,
            "actions": ["audit_ui"],
            "rationale": "Big5 conscientiousness élevée — qualité du système",
        })
    # Si gaps JEPA importants → réduire
    if signals.get("wm_gaps"):
        goals.append({
            "id": "g_wm_gaps",
            "title": f"Réduire les gaps JEPA actifs ({len(signals['wm_gaps'])} détectés)",
            "metric": "n_wm_cycles",
            "target": signals.get("wm_cycles", 0) + 5,
            "current": signals.get("wm_cycles", 0),
            "actions": ["reflect", "explore_graph"],
            "rationale": "Gaps JEPA observés, l'auto-supervision doit progresser",
        })
    # Si valence basse (humeur sombre) → privilégier discovery_report (parler à Sam)
    if mood.get("valence", 0) < -0.2:
        goals.append({
            "id": "g_social",
            "title": "Partager avec Sam une découverte récente",
            "metric": "n_discovery_reports",
            "target": 1,
            "current": 0,
            "actions": ["discovery_report"],
            "rationale": "Humeur basse — besoin social",
        })
    # Goal toujours présent : maintenance
    goals.append({
        "id": "g_homeostasis",
        "title": "Maintenir CPU<70% et RAM<85% (auto-régulation matérielle)",
        "metric": "max_cpu_pct",
        "target": 70,
        "current": (signals.get("vitals", {}).get("cpu") or 0),
        "actions": ["pipeline_cleanup"],
        "rationale": "Corps doit rester sain pour pensée fluide",
    })
    return goals[:5]  # max 5


def _generate_weekly_themes(signals: dict) -> list[dict]:
    themes = []
    # Si causal graph montre des hot spots → thème causal
    causal_top = signals.get("causal_top", [])
    if causal_top:
        themes.append({
            "id": "t_causal",
            "title": "Affiner le graphe causal — passer de 75 à 100 arêtes orientées",
            "rationale": f"{len(causal_top)} paires causales fortes déjà détectées",
            "metric_endpoint": "/api/cortex/causal/graph",
            "metric_field": "n_edges",
            "target_increment": 25,
        })
    # Si learning continual JEPA configuré → thème
    themes.append({
        "id": "t_jepa_loss",
        "title": "Faire baisser la loss JEPA de 5% cette semaine",
        "rationale": "Continual learning incrémental sur replay buffer",
        "metric_endpoint": "/api/cortex/jepa_continual/stats",
        "metric_field": "last_loss",
        "target_decrement_pct": 5.0,
    })
    # Toujours : honnêteté
    themes.append({
        "id": "t_honesty",
        "title": "Aucune réponse fake — barrière llm_only respectée",
        "rationale": "Identité Cortex : ne pas faker. Barrière déjà en place.",
        "metric": "n_fake_responses_caught",
        "target": 0,
    })
    return themes[:3]


def daily_plan(force_regenerate: bool = False) -> dict:
    plan = _load()
    daily = plan.get("daily")
    now = _now()
    needs_regen = (
        force_regenerate or
        not daily or
        (now - daily.get("created_at", 0)) > DAY_S
    )
    if needs_regen:
        signals = _gather_signals()
        goals = _generate_daily_goals(signals)
        daily = {
            "id": f"daily_{int(now)}",
            "created_at": now,
            "deadline": now + DAY_S,
            "goals": goals,
            "n_goals": len(goals),
        }
        plan["daily"] = daily
        _save(plan)
        _log({"type": "daily_plan_generated", "n_goals": len(goals)})
    return daily


def weekly_plan(force_regenerate: bool = False) -> dict:
    plan = _load()
    weekly = plan.get("weekly")
    now = _now()
    needs_regen = (
        force_regenerate or
        not weekly or
        (now - weekly.get("created_at", 0)) > WEEK_S
    )
    if needs_regen:
        signals = _gather_signals()
        themes = _generate_weekly_themes(signals)
        weekly = {
            "id": f"weekly_{int(now)}",
            "created_at": now,
            "deadline": now + WEEK_S,
            "themes": themes,
            "n_themes": len(themes),
        }
        plan["weekly"] = weekly
        _save(plan)
        _log({"type": "weekly_plan_generated", "n_themes": len(themes)})
    return weekly


def propose_next_action() -> dict:
    """Choisit l'action immédiate qui sert le mieux les plans actifs."""
    daily = daily_plan()
    rollout = _safe_import("cortex_rollout")
    if not rollout:
        return {"ok": False, "error": "cortex_rollout missing"}
    candidate_actions = set()
    for g in daily.get("goals", []):
        candidate_actions.update(g.get("actions", []))
    # Si pas d'actions extraites du plan, fallback sur défaut
    if not candidate_actions:
        candidate_actions = {"audit_ui", "explore_graph", "map_knowledge", "discovery_report"}
    # Filtre les actions emergence valides
    valid = {"audit_ui", "explore_graph", "map_knowledge", "discovery_report",
             "reflect", "propose_goal", "look_around"}
    actions = sorted(candidate_actions & valid)
    if not actions: actions = ["audit_ui"]
    out = rollout.rollout(actions=actions)
    return {
        "ok": True,
        "best_action": out.get("best"),
        "rationale": "Plan daily → rollout filtré sur actions servant les goals",
        "candidate_actions": actions,
        "rollout": out,
        "daily_plan_id": daily.get("id"),
    }


def review() -> dict:
    """Marque les goals daily atteints/échoués selon métriques actuelles.
    Implémentation simple : on récupère la valeur actuelle des métriques
    (via signaux) et on compare aux targets."""
    plan = _load()
    daily = plan.get("daily")
    if not daily: return {"ok": False, "error": "no daily plan"}
    signals = _gather_signals()
    updated_goals = []
    n_completed = 0
    for g in daily.get("goals", []):
        target = g.get("target", 0)
        # Pour les goals avec metric simple, on peut update depuis signals
        if g["id"] == "g_homeostasis":
            current = signals.get("vitals", {}).get("cpu", g.get("current", 0))
            g["current"] = current
            g["completed"] = current <= target
        elif g["id"] == "g_wm_gaps":
            current = signals.get("wm_cycles", g.get("current", 0))
            g["current"] = current
            g["completed"] = current >= target
        else:
            # Comptage exact via stream events est plus complexe — laisse current tel quel
            g["completed"] = g.get("current", 0) >= target
        if g["completed"]: n_completed += 1
        updated_goals.append(g)
    daily["goals"] = updated_goals
    daily["n_completed"] = n_completed
    daily["completion_pct"] = round(100 * n_completed / max(1, len(updated_goals)), 1)
    plan["daily"] = daily
    _save(plan)
    _log({"type": "review", "n_completed": n_completed,
          "completion_pct": daily["completion_pct"]})
    return {"ok": True, "n_completed": n_completed,
            "completion_pct": daily["completion_pct"], "daily": daily}


def self_test() -> dict:
    tests = []
    daily = daily_plan(force_regenerate=True)
    tests.append({"name": "daily_plan",
                  "ok": "goals" in daily and len(daily["goals"]) > 0,
                  "n_goals": len(daily.get("goals", [])),
                  "goals_titles": [g["title"] for g in daily["goals"][:3]]})
    weekly = weekly_plan(force_regenerate=True)
    tests.append({"name": "weekly_plan",
                  "ok": "themes" in weekly and len(weekly["themes"]) > 0,
                  "n_themes": len(weekly.get("themes", [])),
                  "themes_titles": [t["title"] for t in weekly["themes"][:3]]})
    rep = propose_next_action()
    tests.append({"name": "propose_next_action",
                  "ok": rep.get("ok") and rep.get("best_action"),
                  "best_action": rep.get("best_action"),
                  "candidates": rep.get("candidate_actions")})
    rev = review()
    tests.append({"name": "review",
                  "ok": rev.get("ok"),
                  "completion_pct": rev.get("completion_pct")})
    return {"ok": all(t["ok"] for t in tests), "tests": tests}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "daily"
    if cmd == "daily":
        print(json.dumps(daily_plan(), indent=2, ensure_ascii=False))
    elif cmd == "weekly":
        print(json.dumps(weekly_plan(), indent=2, ensure_ascii=False))
    elif cmd == "next":
        print(json.dumps(propose_next_action(), indent=2, ensure_ascii=False))
    elif cmd == "review":
        print(json.dumps(review(), indent=2, ensure_ascii=False))
    elif cmd == "test":
        print(json.dumps(self_test(), indent=2, ensure_ascii=False))
    else:
        print("Usage: cortex_plan.py {daily|weekly|next|review|test}")
