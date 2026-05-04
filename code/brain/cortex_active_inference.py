"""
cortex_active_inference.py — Active Inference / Free Energy (Friston 2010).

Cadre mathématique unifié : tout (perception, action, apprentissage) minimise
une seule quantité, la Variational Free Energy (VFE).

VFE = -log p(observations|model) + KL(q(states)||p(states))
    = expected_surprise + complexity_cost

Pour Cortex :
- observations  = état réel (active_nodes, vitals, gaps JEPA observés)
- predictions   = état prédit par le world model (JEPA + plan + personality)
- surprise      = écart entre prédit et observé
- action choice = celle qui minimise EXPECTED future free energy

Anti-fake intégré :
- baseline_random : score du même choix par random sampling (référence)
- divergence_from_random : si AI choice ≈ random → l'agent ne fait rien d'utile
- log append-only de chaque calcul (.cortex-active-inference-log.jsonl)
- surprise tracking : doit DIMINUER dans le temps si l'agent apprend

API :
    measure_surprise() → float : écart prédit vs observé courant
    expected_free_energy(action) → float : EFE pour une action candidate
    select_action(actions) → dict : action choisie + comparaison à random
    self_test()
    drive_step() : un cycle complet (mesure + log)

Référence :
    Friston, K. (2010). "The free-energy principle: a unified brain theory?"
    Nature Reviews Neuroscience 11, 127-138.
    Friston et al. (2017). "Active inference, curiosity and insight."
    Neural Computation 29, 2633-2683.
"""
from __future__ import annotations
import json
import math
import random
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
VAULT = Path(r"<USER_HOME>\Documents\Obsidian Vault")
STATE = VAULT / ".cortex-active-inference-state.json"
LOG = VAULT / ".cortex-active-inference-log.jsonl"


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
        "version": "active-inference-1",
        "n_steps": 0,
        "surprise_history": [],
        "vfe_history": [],
        "n_better_than_random": 0,
        "n_worse_than_random": 0,
        "n_equal_to_random": 0,
        "last_observed_state": None,
        "last_predicted_state": None,
    }


def _save_state(s: dict) -> None:
    s["updated_at"] = _now()
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception: pass


def _log_event(ev: dict) -> None:
    """Append-only audit pour traçabilité totale (anti-fake)."""
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": _now(), **ev}, ensure_ascii=False) + "\n")
    except Exception: pass


def _observe_state() -> dict:
    """État observable réel de Cortex maintenant."""
    obs = {"ts": _now()}
    ca = _safe_import("cortex_activation")
    if ca:
        try:
            snap = ca.snapshot()
            obs["n_active"] = snap.get("n_active", 0)
            obs["n_pulses_cum"] = snap.get("cum_pulses", 0)
            obs["n_hebbian_cum"] = snap.get("cum_hebbian_ticks", 0)
        except Exception: pass
    pm = _safe_import("cortex_pipeline_manager")
    if pm:
        try:
            v = pm._vital_signs()
            obs["cpu"] = v.get("cpu", 0)
            obs["ram"] = v.get("ram", 0)
            obs["n_zombies"] = len(pm.find_zombies())
        except Exception: pass
    # Compression error proxy LIGHT (sans LM Studio) : juste isolated_ratio + jepa_loss
    # On évite cortex_curiosity.measure_compression_error() qui peut être lent
    # car il appelle probe_world. La version light suffit pour Active Inference.
    try:
        from pathlib import Path as _P
        g_path = VAULT / ".vault-graph.json"
        if g_path.exists():
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
                obs["compression_error"] = round(0.6 * (isolated / n), 4)
    except Exception:
        obs["compression_error"] = 0.5
    return obs


def _predict_state(action: str | None = None) -> dict:
    """Prédiction du prochain état si on prend `action`.

    Pas du LLM. Modèle linéaire : pour chaque action, on a des effets attendus
    encodés explicitement (apprentissage des effets via cortex_causal possible).
    """
    obs = _observe_state()
    pred = dict(obs)
    if action == "audit_ui":
        pred["cpu"] = max(0, obs.get("cpu", 0) + 2)
        pred["compression_error"] = obs.get("compression_error", 0.5) - 0.005
    elif action == "explore_graph":
        pred["n_active"] = obs.get("n_active", 0) + 2
        pred["n_pulses_cum"] = obs.get("n_pulses_cum", 0) + 5
        pred["compression_error"] = obs.get("compression_error", 0.5) - 0.02
    elif action == "map_knowledge":
        pred["compression_error"] = obs.get("compression_error", 0.5) - 0.015
    elif action == "discovery_report":
        pred["n_active"] = obs.get("n_active", 0) + 1
    elif action == "reflect":
        pred["n_active"] = obs.get("n_active", 0) + 3
        pred["compression_error"] = obs.get("compression_error", 0.5) - 0.01
    elif action == "propose_goal":
        pred["compression_error"] = obs.get("compression_error", 0.5) - 0.008
    elif action == "look_around":
        pred["n_active"] = obs.get("n_active", 0) + 1
    elif action == "silent":
        pass  # rien ne change
    pred["action_taken"] = action
    return pred


def measure_surprise() -> dict:
    """Surprise = écart entre dernière prédiction et observation actuelle."""
    state = _load_state()
    obs = _observe_state()
    last_pred = state.get("last_predicted_state")
    if not last_pred:
        # Premier cycle : pas de prédiction passée à comparer
        return {"ok": True, "surprise": 0.0, "reason": "first_cycle"}
    # Surprise = somme normalisée des écarts |obs - pred| pour les champs numériques
    diffs = {}
    surprise = 0.0
    n = 0
    for k, v in obs.items():
        if not isinstance(v, (int, float)): continue
        if k not in last_pred or not isinstance(last_pred[k], (int, float)): continue
        # Normalise par échelle attendue
        scale = {"cpu": 100, "ram": 100, "n_active": 10, "n_pulses_cum": 100,
                 "n_hebbian_cum": 100, "n_zombies": 100,
                 "compression_error": 1.0}.get(k, 10)
        delta = abs(v - last_pred[k]) / scale
        diffs[k] = round(delta, 4)
        surprise += delta
        n += 1
    if n > 0: surprise /= n
    return {"ok": True, "surprise": round(surprise, 4),
            "n_fields": n, "diffs": diffs,
            "observed": obs, "predicted": last_pred}


def expected_free_energy(action: str) -> float:
    """EFE pour une action candidate. Plus c'est BAS, mieux c'est.

    EFE = epistemic_value (info gain prédit) + pragmatic_value (utilité prédite).
    On veut MINIMISER l'opposé : maximiser le gain d'info + utilité.

    Formule simplifiée :
    EFE = - reduction_compression_error  (epistemic, on aime les actions qui apprennent)
        - utility_score                  (pragmatic, ça dépend du plan courant)
    """
    pred = _predict_state(action)
    obs = _observe_state()
    # Epistemic value = combien on s'attend à réduire la compression error
    epistemic = obs.get("compression_error", 0.5) - pred.get("compression_error", 0.5)
    # Pragmatic value = est-ce que cette action sert un goal du plan actuel ?
    pragmatic = 0.0
    pl = _safe_import("cortex_plan")
    if pl:
        try:
            d = pl.daily_plan()
            for g in d.get("goals", []):
                if action in g.get("actions", []):
                    pragmatic += 0.1
                    if not g.get("completed"): pragmatic += 0.05
        except Exception: pass
    # Personnalité : si openness élevée et action exploratoire → bonus
    pers = _safe_import("cortex_personality")
    if pers:
        try:
            big5 = pers.state().get("big5", {})
            if action in ("explore_graph", "map_knowledge", "look_around"):
                pragmatic += (big5.get("openness", 0.5) - 0.5) * 0.1
            if action in ("audit_ui", "propose_goal"):
                pragmatic += (big5.get("conscientiousness", 0.5) - 0.5) * 0.1
        except Exception: pass
    # EFE : on minimise donc -epistemic - pragmatic = on choisit l'action qui maximise les deux
    efe = -epistemic - pragmatic
    return round(efe, 5)


def select_action(actions: list[str] | None = None) -> dict:
    """Choisit l'action via Active Inference, avec comparaison à random baseline."""
    actions = actions or ["audit_ui", "explore_graph", "map_knowledge",
                          "discovery_report", "reflect", "propose_goal",
                          "look_around", "silent"]
    # Score chaque action
    scored = [(a, expected_free_energy(a)) for a in actions]
    scored.sort(key=lambda x: x[1])  # plus bas = mieux
    chosen = scored[0][0]
    chosen_efe = scored[0][1]
    # Baseline random : qu'est-ce qu'un random aurait choisi ?
    random_action = random.choice(actions)
    random_efe = expected_free_energy(random_action)
    # Comparaison : l'agent fait-il mieux que random ?
    if chosen_efe < random_efe - 0.001:
        comparison = "better_than_random"
    elif chosen_efe > random_efe + 0.001:
        comparison = "worse_than_random"
    else:
        comparison = "equal_to_random"
    out = {
        "ok": True,
        "ts": _now(),
        "chosen_action": chosen,
        "chosen_efe": chosen_efe,
        "random_action": random_action,
        "random_efe": random_efe,
        "comparison": comparison,
        "ranked": [{"action": a, "efe": e} for a, e in scored[:5]],
    }
    return out


def drive_step() -> dict:
    """UN cycle complet d'Active Inference : observe + measure surprise + select + log."""
    state = _load_state()
    surprise = measure_surprise()
    selection = select_action()
    # Calcule la VFE actuelle = surprise observée
    vfe = surprise.get("surprise", 0)
    # Mémorise la prédiction pour le prochain cycle
    new_prediction = _predict_state(selection["chosen_action"])
    state["last_observed_state"] = surprise.get("observed")
    state["last_predicted_state"] = new_prediction
    state["n_steps"] = state.get("n_steps", 0) + 1
    history = state.get("surprise_history", [])
    history.append({"ts": _now(), "surprise": vfe})
    state["surprise_history"] = history[-30:]
    vfe_history = state.get("vfe_history", [])
    vfe_history.append({"ts": _now(), "vfe": vfe,
                        "chosen": selection["chosen_action"],
                        "comparison": selection["comparison"]})
    state["vfe_history"] = vfe_history[-50:]
    if selection["comparison"] == "better_than_random":
        state["n_better_than_random"] = state.get("n_better_than_random", 0) + 1
    elif selection["comparison"] == "worse_than_random":
        state["n_worse_than_random"] = state.get("n_worse_than_random", 0) + 1
    else:
        state["n_equal_to_random"] = state.get("n_equal_to_random", 0) + 1
    _save_state(state)
    rep = {
        "ok": True,
        "ts": _now(),
        "surprise": vfe,
        "chosen_action": selection["chosen_action"],
        "chosen_efe": selection["chosen_efe"],
        "comparison_to_random": selection["comparison"],
        "n_steps": state["n_steps"],
    }
    _log_event({"type": "drive_step", **{k: v for k, v in rep.items() if k != "ts"}})
    return rep


def stats() -> dict:
    s = _load_state()
    history = s.get("surprise_history", [])
    if len(history) >= 2:
        early = sum(h["surprise"] for h in history[:5]) / max(1, len(history[:5]))
        late = sum(h["surprise"] for h in history[-5:]) / max(1, len(history[-5:]))
        surprise_trend = late - early  # négatif = en baisse = bon
    else:
        early = late = surprise_trend = None
    n_total = (s.get("n_better_than_random", 0) +
               s.get("n_worse_than_random", 0) +
               s.get("n_equal_to_random", 0))
    return {
        "n_steps": s.get("n_steps", 0),
        "n_better_than_random": s.get("n_better_than_random", 0),
        "n_worse_than_random": s.get("n_worse_than_random", 0),
        "n_equal_to_random": s.get("n_equal_to_random", 0),
        "fraction_better_than_random": (s.get("n_better_than_random", 0) / max(1, n_total)),
        "early_avg_surprise": round(early, 4) if early is not None else None,
        "late_avg_surprise": round(late, 4) if late is not None else None,
        "surprise_trend": round(surprise_trend, 4) if surprise_trend is not None else None,
        "is_learning": (surprise_trend is not None and surprise_trend < 0),
    }


def self_test() -> dict:
    tests = []
    obs = _observe_state()
    tests.append({"name": "observe_state",
                  "ok": isinstance(obs, dict) and "ts" in obs,
                  "n_fields": len(obs)})
    pred = _predict_state("explore_graph")
    tests.append({"name": "predict_state",
                  "ok": "compression_error" in pred,
                  "predicted_action": pred.get("action_taken")})
    surprise = measure_surprise()
    tests.append({"name": "measure_surprise",
                  "ok": "surprise" in surprise,
                  "value": surprise.get("surprise")})
    sel = select_action()
    tests.append({"name": "select_action",
                  "ok": sel.get("chosen_action") is not None and "comparison" in sel,
                  "chosen": sel.get("chosen_action"),
                  "comparison": sel.get("comparison")})
    step = drive_step()
    tests.append({"name": "drive_step",
                  "ok": step.get("ok") and "comparison_to_random" in step,
                  "comparison": step.get("comparison_to_random")})
    s = stats()
    tests.append({"name": "stats",
                  "ok": "n_steps" in s,
                  "n_steps": s.get("n_steps"),
                  "is_learning": s.get("is_learning")})
    return {"ok": all(t["ok"] for t in tests), "tests": tests}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "step"
    if cmd == "step":
        print(json.dumps(drive_step(), indent=2, ensure_ascii=False))
    elif cmd == "stats":
        print(json.dumps(stats(), indent=2, ensure_ascii=False))
    elif cmd == "select":
        print(json.dumps(select_action(), indent=2, ensure_ascii=False))
    elif cmd == "surprise":
        print(json.dumps(measure_surprise(), indent=2, ensure_ascii=False))
    elif cmd == "test":
        print(json.dumps(self_test(), indent=2, ensure_ascii=False))
    else:
        print("Usage: cortex_active_inference.py {step|stats|select|surprise|test}")
