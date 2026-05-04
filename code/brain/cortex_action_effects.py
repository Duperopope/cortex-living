"""
cortex_action_effects.py — Apprentissage empirique des effets d'action.

L'expert reviewer du repo public a pointé que les effets d'action dans
`cortex_active_inference._predict_state` sont hard-codés :

    if action == "explore_graph":
        pred["n_active"] += 2
        pred["compression_error"] -= 0.02

Ça pénalise la calibration (cf `cortex_avg_outcome_observed=0.0` vs
`cortex_avg_outcome_proxy=0.425` au démarrage du banc) et empêche tout
apprentissage. Pour passer d'un agent heuristique à un agent adaptatif,
il faut **apprendre les effets observés**.

Approche v1 — simple et auditable :

1. Chaque cycle drive_step écrit `(pre_obs, action, post_obs)` dans
   `.cortex-action-effects.jsonl` (append-only, pas de réécriture).
2. `predict_effect(action, context)` retourne les deltas moyens observés
   pour cette action sur les N derniers exemples (par défaut 30).
3. Si moins de `MIN_SAMPLES` exemples pour une action, on retourne `None`
   et le caller (active_inference) tombe sur ses heuristiques. Pas de
   bascule brutale : graceful degradation.

Ce qui n'est PAS dans la v1 (volontairement, pour rester auditable) :
- pas de modèle paramétrique (linéaire / GP / NN), juste de la moyenne par
  bucket d'action — on ajoutera un modèle quand on aura plus de données et
  qu'on aura un signal clair de non-linéarité
- pas encore de conditionnement sur le contexte (CPU, RAM, n_active courant) ;
  v2 ajoutera des buckets contextuels (low/mid/high CPU, etc.)
- pas d'oubli pondéré ; v2 ajoutera une fenêtre glissante avec poids ~exp(-Δt/τ)

Référence : prédicteur empirique inspiré de la littérature RL "model-based"
(Sutton & Barto 2018, ch. 8) — ici on apprend la fonction T(s, a) = E[s'-s]
sans planifier dessus, juste pour informer le scoring EFE.
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
VAULT = Path(r"<USER_HOME>\Documents\Obsidian Vault")
LOG = VAULT / ".cortex-action-effects.jsonl"
SUMMARY = VAULT / ".cortex-action-effects-summary.json"

# Champs numériques qu'on suit dans les obs.
# IMPORTANT : seuls les champs *instantanés* sont trackés. On EXCLUT les
# compteurs cumulatifs (`n_pulses_cum`, `n_hebbian_cum`, `n_steps`) qui montent
# monotone et faussent les deltas (un delta de 100 entre 2 cycles ne signifie
# pas qu'une action a eu un effet de 100, juste que le compteur global a
# avancé). Vu en live : prediction_error_avg=9.49 alors que les outcomes
# observed/proxy étaient cohérents — bug causé par ces cumulatifs.
TRACKED_FIELDS = ("n_active", "compression_error", "cpu", "ram")
# Champs cumulatifs : conservés en lecture pour ne pas casser les events
# historiques mais EXCLUS de la mesure prediction_error.
LEGACY_CUMULATIVE_FIELDS = ("n_pulses_cum", "n_hebbian_cum", "n_steps")
# Champs ENVIRONNEMENT : indépendants des actions Cortex (CPU/RAM bougent
# selon ce que Sam fait, pas selon `explore_graph` ou `reflect`). Exclus
# de prediction_error pour ne pas polluer la mesure d'erreur du modèle d'effets.
ENV_NOISE_FIELDS = ("cpu", "ram")
# Champs CIBLES : ceux que les actions sont censées affecter.
ACTION_TARGET_FIELDS = ("n_active", "compression_error")

# Combien d'exemples par action avant d'utiliser la prédiction empirique
# à la place des heuristiques hardcoded. < ce seuil → return None (fallback).
MIN_SAMPLES = 8

# Combien d'exemples max retenus dans la moyenne (récency bias léger).
WINDOW = 30


def _now() -> float: return time.time()


def _safe_load_jsonl(path: Path, max_lines: int = 2000) -> list[dict]:
    if not path.exists(): return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        out = []
        for ln in lines[-max_lines:]:
            try: out.append(json.loads(ln))
            except Exception: pass
        return out
    except Exception:
        return []


def record_observation(action: str, pre_obs: dict, post_obs: dict) -> dict:
    """Append-only : sauvegarde un exemple `(action, pre, post)` pour apprentissage.

    Garbage-in garbage-out : si pre_obs/post_obs ne sont pas des dicts ou si
    `action` est vide, on skip silencieusement (pas d'exception à propager au
    drive_step, qui doit rester robuste).
    """
    if not action or not isinstance(pre_obs, dict) or not isinstance(post_obs, dict):
        return {"ok": False, "reason": "bad inputs"}
    delta = {}
    for k in TRACKED_FIELDS:
        if isinstance(pre_obs.get(k), (int, float)) and \
           isinstance(post_obs.get(k), (int, float)):
            delta[k] = post_obs[k] - pre_obs[k]
    if not delta:
        return {"ok": False, "reason": "no tracked fields"}
    ev = {
        "ts": _now(),
        "action": action,
        "pre":   {k: pre_obs.get(k)  for k in TRACKED_FIELDS if k in pre_obs},
        "post":  {k: post_obs.get(k) for k in TRACKED_FIELDS if k in post_obs},
        "delta": {k: round(v, 4) for k, v in delta.items()},
    }
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except Exception:
        return {"ok": False, "reason": "write failed"}
    return {"ok": True, "delta": delta}


def _events_by_action() -> dict[str, list[dict]]:
    """Groupe les events par action, garde les WINDOW plus récents."""
    events = _safe_load_jsonl(LOG)
    by_action: dict[str, list[dict]] = {}
    for e in events:
        a = e.get("action")
        if a:
            by_action.setdefault(a, []).append(e)
    # Trim window : on garde les N plus récents (events sont déjà ordonnés ts asc)
    for a in by_action:
        by_action[a] = by_action[a][-WINDOW:]
    return by_action


def predict_effect(action: str) -> dict | None:
    """Retourne le delta moyen par champ pour `action`, ou None si insuffisant.

    Format de retour :
        {
          "n_active":          {"mean": 1.4, "std": 0.7, "n": 12},
          "compression_error": {"mean": -0.012, "std": 0.005, "n": 12},
          ...
        }

    Utilisation depuis active_inference :
        learned = predict_effect("explore_graph")
        if learned and learned.get("n_active", {}).get("n", 0) >= MIN_SAMPLES:
            pred["n_active"] = obs["n_active"] + learned["n_active"]["mean"]
        else:
            pred["n_active"] = obs["n_active"] + 2  # heuristique fallback
    """
    by_action = _events_by_action()
    bucket = by_action.get(action) or []
    if len(bucket) < MIN_SAMPLES:
        return None
    out: dict[str, dict] = {}
    for k in TRACKED_FIELDS:
        deltas = [e["delta"].get(k) for e in bucket
                  if isinstance(e.get("delta", {}).get(k), (int, float))]
        if not deltas: continue
        n = len(deltas)
        m = sum(deltas) / n
        var = sum((d - m) ** 2 for d in deltas) / n if n > 1 else 0.0
        out[k] = {
            "mean": round(m, 5),
            "std":  round(var ** 0.5, 5),
            "n":    n,
        }
    return out or None


def _classify_observability(per_action_means: dict) -> str:
    """Classification heuristique : action ↔ effet observable.

    high   : delta moyen > 0.5 sur n_active ou >0.005 sur compression_error
    medium : delta présent mais sous le seuil
    low    : pas de delta significatif (toutes les moyennes ≈ 0)
    """
    if not per_action_means:
        return "low"
    n_active_m = abs(per_action_means.get("n_active", {}).get("mean", 0))
    ce_m = abs(per_action_means.get("compression_error", {}).get("mean", 0))
    if n_active_m > 0.5 or ce_m > 0.005:
        return "high"
    if n_active_m > 0.1 or ce_m > 0.001:
        return "medium"
    return "low"


def stats() -> dict:
    """Stats étoffées : ratio learned/fallback, prediction error par action,
    top fiables / surévaluées, observabilité par action.

    Avant : juste un resumé. Maintenant : diagnostic complet utilisable
    pour calibration IAG et UI.
    """
    by_action = _events_by_action()
    per_action: dict[str, dict] = {}
    n_total = 0
    n_learned = 0
    n_fallback = 0
    pred_errors_global: list[float] = []
    pred_errors_by_action: dict[str, list[float]] = {}

    for a, bucket in by_action.items():
        n = len(bucket)
        learned = predict_effect(a)
        means_with_stats = learned or {}
        # Prediction error : sur les champs CIBLES (n_active, compression_error)
        # uniquement. On exclut les cumulatifs et le bruit environnement.
        per_errs: list[float] = []
        for ev in bucket:
            obs = ev.get("post", {})
            pre = ev.get("pre", {})
            if learned:
                for field, stats_field in learned.items():
                    if field not in ACTION_TARGET_FIELDS: continue
                    if field in pre and field in obs:
                        real_delta = obs[field] - pre[field]
                        pred_delta = stats_field.get("mean", 0)
                        per_errs.append(abs(real_delta - pred_delta))
        if per_errs:
            pred_errors_by_action[a] = sum(per_errs) / len(per_errs)
            pred_errors_global.extend(per_errs)
        per_action[a] = {
            "n_examples": n,
            "status": "learned" if learned else "fallback",
            "fields_with_signal": (sorted(means_with_stats.keys())
                                    if means_with_stats else []),
            "means": {k: {"mean": v["mean"], "std": v["std"], "n": v["n"]}
                       for k, v in means_with_stats.items()},
            "prediction_error_avg": (round(pred_errors_by_action.get(a, 0), 5)
                                      if a in pred_errors_by_action else None),
            "observability": _classify_observability(means_with_stats),
        }
        n_total += 1
        if learned: n_learned += 1
        else: n_fallback += 1

    empirical_ratio = (n_learned / n_total) if n_total > 0 else 0.0
    pred_err_avg_global = (sum(pred_errors_global) / len(pred_errors_global)
                            if pred_errors_global else None)

    # Top over-optimistic : actions avec mean delta positif élevé mais
    # observed proche de 0 sur les outcomes mesurés (proxy par std haute)
    over_optim = sorted(
        [(a, v) for a, v in per_action.items() if v.get("means")],
        key=lambda kv: -max(
            (s.get("std", 0) for s in kv[1].get("means", {}).values()), default=0))[:3]

    # Top reliable : actions avec n_examples >= MIN_SAMPLES et std faible
    reliable = sorted(
        [(a, v) for a, v in per_action.items()
         if v.get("status") == "learned" and v.get("means")],
        key=lambda kv: max(
            (s.get("std", 0) for s in kv[1].get("means", {}).values()), default=1.0))[:3]

    # Compat ancien format : `actions` = {action_name: {n_examples, status, fields_with_signal}}
    actions_compat = {
        a: {"n_examples": v["n_examples"], "status": v["status"],
            "fields_with_signal": v["fields_with_signal"]}
        for a, v in per_action.items()
    }
    out = {
        "ts": _now(),
        "min_samples": MIN_SAMPLES,
        "window": WINDOW,
        "n_actions_observed": n_total,
        "empirical_predictions_count": n_learned,
        "heuristic_fallback_count": n_fallback,
        "empirical_ratio": round(empirical_ratio, 3),
        "prediction_error_avg_global": (round(pred_err_avg_global, 5)
                                          if pred_err_avg_global is not None else None),
        "actions": actions_compat,  # rétrocompat
        "per_action": per_action,   # détail enrichi
        "top_overoptimistic_actions": [a for a, _ in over_optim],
        "top_reliable_actions": [a for a, _ in reliable],
    }
    try:
        SUMMARY.parent.mkdir(parents=True, exist_ok=True)
        SUMMARY.write_text(json.dumps(out, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    except Exception: pass
    return out


# Compat : ancien nom conservé (utilisé par cortex_iag_test._calibration_factor)
def summary() -> dict:
    return stats()


def self_test() -> dict:
    """Vérifie que record + predict marchent sur des données synthétiques.

    On utilise un fichier log temporaire pour ne pas polluer le runtime réel.
    """
    import tempfile
    global LOG
    original = LOG
    tmp = Path(tempfile.gettempdir()) / "_test_action_effects.jsonl"
    if tmp.exists(): tmp.unlink()
    LOG = tmp
    try:
        # Pas assez d'exemples → None
        for _ in range(MIN_SAMPLES - 1):
            record_observation(
                "X",
                {"n_active": 0, "compression_error": 0.5},
                {"n_active": 2, "compression_error": 0.48},
            )
        r1 = predict_effect("X")
        # MIN_SAMPLES atteint → dict avec mean/std/n
        record_observation(
            "X",
            {"n_active": 0, "compression_error": 0.5},
            {"n_active": 2, "compression_error": 0.48},
        )
        r2 = predict_effect("X")
        ok = (r1 is None
              and isinstance(r2, dict)
              and "n_active" in r2
              and abs(r2["n_active"]["mean"] - 2.0) < 1e-6
              and abs(r2["compression_error"]["mean"] - (-0.02)) < 1e-6
              and r2["n_active"]["n"] == MIN_SAMPLES)
        return {
            "ok": ok,
            "r1_below_min": r1 is None,
            "r2": r2,
            "min_samples": MIN_SAMPLES,
        }
    finally:
        LOG = original
        try: tmp.unlink()
        except Exception: pass


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "summary"
    if cmd in ("summary", "stats"):
        print(json.dumps(stats(), indent=2, ensure_ascii=False))
    elif cmd == "predict":
        a = sys.argv[2] if len(sys.argv) > 2 else "explore_graph"
        print(json.dumps(predict_effect(a), indent=2, ensure_ascii=False))
    elif cmd == "test":
        print(json.dumps(self_test(), indent=2, ensure_ascii=False))
    else:
        print("Usage: cortex_action_effects.py {summary|stats|predict <action>|test}")
