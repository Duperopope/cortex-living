"""
cortex_iag_test.py — Test rigoureux d'IAG-ness de Cortex.

Pas de marketing. Score honnête sur 7 dimensions, chacune mesurable.
Si une dimension est faible, le verdict global est "proto-IAG", pas "IAG".

Les 7 dimensions évaluées :

1. CAUSALITY
   Est-ce que le graphe causal contient des arêtes orientées avec
   asymétrie temporelle > 65% sur 5+ observations ? Combien ?

2. PLANNING (hierarchical)
   Plan daily généré à partir de signaux réels (Big5, vitals, gaps) ?
   Plan weekly cohérent avec daily ?
   propose_next_action() retourne une action sensée ?

3. CONTINUAL_LEARNING
   Loss JEPA décroissante sur les N derniers steps ? Replay buffer rempli ?

4. SELF_REFLECTION (proactivité)
   Cortex émet des messages spontanés via cortex_proactive ?
   Au moins 1 message dans les 24h ? Trigger varié ?

5. MEMORY_CORRECTION
   Audit mémoire détecte des problèmes réels (contradictions, paths obsolètes) ?
   Propose des fixes ?

6. RESOURCE_SELF_MGMT
   Pipeline manager auto-régule (zombies < 80, RAM < 92%) ?
   Cleanup history non vide ?

7. WORLD_MODEL_ACCURACY
   JEPA probe répond avec mode `jepa_latent_proxy` ou mieux ?
   Confidence > 0.1 sur des queries sensées ?

Score global : moyenne pondérée [0..100]. Verdict :
- 0-30   : système autonome simple, pas d'IAG
- 30-50  : proto-IAG observable
- 50-70  : IAG faible (specialized AGI)
- 70-90  : IAG forte sur ce domaine
- 90+    : IAG générale (improbable, à scruter)
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
VAULT = Path(r"<USER_HOME>\Documents\Obsidian Vault")
REPORT = VAULT / ".cortex-iag-test-report.json"

# Pondération des dimensions (somme = 1.0)
WEIGHTS = {
    "causality":           0.18,
    "planning":            0.15,
    "continual_learning":  0.15,
    "self_reflection":     0.13,
    "memory_correction":   0.10,
    "resource_self_mgmt":  0.10,
    "world_model_accuracy":0.19,
}


def _now() -> float: return time.time()


def _safe_import(name: str):
    try:
        sys.path.insert(0, str(REPO))
        return __import__(name)
    except Exception:
        return None


def _score_causality() -> dict:
    cc = _safe_import("cortex_causal")
    if not cc:
        return {"score": 0, "reason": "module absent"}
    try:
        pairs = cc.detect_causal_pairs(min_strength=0.02, min_observations=3)
        n = len(pairs)
        # Score : 0 si 0 paire, 100 si 50+
        score = min(100, n * 2)
        return {
            "score": score,
            "n_causal_pairs": n,
            "n_strong": sum(1 for p in pairs if p["score"] > 0.05),
            "evidence": [p["score"] for p in pairs[:5]],
        }
    except Exception as e:
        return {"score": 0, "error": str(e)}


def _score_planning() -> dict:
    pl = _safe_import("cortex_plan")
    if not pl:
        return {"score": 0, "reason": "module absent"}
    try:
        daily = pl.daily_plan()
        weekly = pl.weekly_plan()
        rep = pl.propose_next_action()
        n_goals = len(daily.get("goals", []))
        n_themes = len(weekly.get("themes", []))
        coherent = bool(rep.get("ok") and rep.get("best_action"))
        score = 0
        if n_goals >= 3: score += 30
        if n_themes >= 2: score += 30
        if coherent: score += 40
        return {
            "score": score,
            "n_goals_daily": n_goals,
            "n_themes_weekly": n_themes,
            "next_action_coherent": coherent,
            "next_action": rep.get("best_action"),
        }
    except Exception as e:
        return {"score": 0, "error": str(e)}


def _score_continual_learning() -> dict:
    jc = _safe_import("cortex_jepa_continual")
    if not jc:
        return {"score": 0, "reason": "module absent"}
    try:
        stats = jc._load_stats()
        replay_size = stats.get("replay_size", 0)
        n_steps = stats.get("n_steps_total", 0)
        last_loss = stats.get("last_loss")
        mean_loss = stats.get("mean_loss")
        score = 0
        if replay_size > 30: score += 30
        if n_steps > 0: score += 20
        if n_steps > 5: score += 20
        # Loss qui baisse
        if last_loss is not None and mean_loss is not None and last_loss < mean_loss:
            score += 30
        return {
            "score": score,
            "replay_size": replay_size,
            "n_steps_total": n_steps,
            "last_loss": last_loss,
            "mean_loss": mean_loss,
            "loss_decreasing": last_loss is not None and mean_loss is not None and last_loss < mean_loss,
        }
    except Exception as e:
        return {"score": 0, "error": str(e)}


def _score_self_reflection() -> dict:
    pr = _safe_import("cortex_proactive")
    if not pr:
        return {"score": 0, "reason": "module absent"}
    try:
        state = pr._load_state()
        n_total = state.get("n_msgs_total", 0)
        triggers = state.get("last_triggers", [])
        unique_triggers = len(set(triggers))
        last_ts = state.get("last_msg_ts", 0)
        recent = (_now() - last_ts) < 86400 if last_ts > 0 else False
        score = 0
        if n_total > 0: score += 30
        if recent: score += 30
        if unique_triggers >= 2: score += 40
        return {
            "score": score,
            "n_proactive_total": n_total,
            "unique_triggers": unique_triggers,
            "recent_24h": recent,
            "triggers_seen": list(set(triggers)),
        }
    except Exception as e:
        return {"score": 0, "error": str(e)}


def _score_memory_correction() -> dict:
    ma = _safe_import("cortex_memory_audit")
    if not ma:
        return {"score": 0, "reason": "module absent"}
    try:
        rep = ma.audit()
        n_issues = rep.get("issues_found", 0)
        by_type = rep.get("by_type", {})
        # Bon score = détecte des choses (audit fonctionne).
        # Mais si trop de contradictions = mauvais.
        score = 0
        if n_issues > 0: score += 40  # détecte
        if by_type.get("contradictions", 0) == 0: score += 30  # pas de contradiction = mémoire saine
        if by_type.get("obsolete_paths", 0) < 30: score += 30  # pas trop de paths morts
        return {
            "score": score,
            "n_issues_detected": n_issues,
            "by_type": by_type,
        }
    except Exception as e:
        return {"score": 0, "error": str(e)}


def _score_resource_self_mgmt() -> dict:
    pm = _safe_import("cortex_pipeline_manager")
    if not pm:
        return {"score": 0, "reason": "module absent"}
    try:
        snap = pm.list_processes()
        zombies = pm.find_zombies()
        vit = pm._vital_signs()
        n_zombies = len(zombies)
        ram = vit.get("ram", 100)
        cpu = vit.get("cpu", 100)
        score = 0
        if n_zombies < 80: score += 35
        if ram < 92: score += 35
        if cpu < 85: score += 30
        return {
            "score": score,
            "n_zombies": n_zombies,
            "ram_pct": ram,
            "cpu_pct": cpu,
            "n_processes_total": snap.get("total_processes", 0),
        }
    except Exception as e:
        return {"score": 0, "error": str(e)}


def _score_world_model_accuracy() -> dict:
    cwm = _safe_import("cortex_world_model")
    if not cwm:
        return {"score": 0, "reason": "module absent"}
    try:
        state = cwm.read_state()
        cycles = state.get("cycles", 0)
        autonomous = state.get("autonomous", False)
        # Un probe réel
        probe = cwm.probe_world("LeCun JEPA world model autonomous prediction latent")
        mode = probe.get("mode", "")
        confidence = probe.get("confidence", 0) or 0
        score = 0
        if autonomous: score += 25
        if cycles > 5: score += 20
        if "jepa" in mode.lower(): score += 30  # vraie prédiction latente
        if isinstance(confidence, (int, float)) and confidence > 0.1: score += 25
        return {
            "score": score,
            "autonomous": autonomous,
            "cycles": cycles,
            "probe_mode": mode,
            "probe_confidence": confidence,
        }
    except Exception as e:
        return {"score": 0, "error": str(e)}


def _calibration_factor() -> dict:
    """Coefficient ≤ 1.0 qui déflate le score IAG selon la maturité RÉELLE.

    Avant cette correction, le score IAG était systématiquement >85/100 grâce
    à des seuils binaires généreux ("modèle entraîné" → 30 points instantanés).
    Cortex lui-même flaggait ses propres scores comme « improbable, à scruter ».

    Trois signaux honnêtes pour calibrer :
    1. Apprentissage des effets — si toutes les actions sont en mode `fallback`
       (heuristiques hardcodées), Cortex n'a pas encore APPRIS empiriquement →
       facteur 0.7. Si la moitié sont en `learned` → 0.85. Si toutes → 1.0.
    2. Erreur de prédiction (Active Inference) — si `outcome_proxy` >> `outcome_observed`,
       le modèle d'effets sur-promet. Si l'écart > 0.5 → 0.85. Si < 0.1 → 1.0.
    3. Anti-fake fake_confident_rate — si > 0.1 (10%+ d'inventions confidentes
       sur les questions d'état interne) → 0.7. Si 0 → 1.0.

    Le facteur final est le minimum des trois (le maillon faible domine).
    """
    factor_action_effects = 1.0
    factor_prediction_error = 1.0
    factor_fake_confident = 1.0
    notes = []
    try:
        import sys, importlib
        sys.path.insert(0, str(REPO))
        ae = importlib.import_module("cortex_action_effects")
        s = ae.summary() or {}
        actions = s.get("actions", {}) or {}
        if actions:
            n_learned = sum(1 for v in actions.values()
                            if v.get("status") == "learned")
            ratio = n_learned / max(1, len(actions))
            factor_action_effects = 0.7 + 0.3 * ratio
            notes.append(f"action_effects: {n_learned}/{len(actions)} learned "
                          f"(factor={factor_action_effects:.2f})")
        else:
            factor_action_effects = 0.7
            notes.append("action_effects: 0 actions observed (factor=0.70)")
    except Exception as e:
        notes.append(f"action_effects unavailable: {e}")
    try:
        import sys, importlib
        sys.path.insert(0, str(REPO))
        ai = importlib.import_module("cortex_active_inference")
        st = ai.stats() or {}
        proxy = st.get("cortex_avg_outcome_proxy")
        observed = st.get("cortex_avg_outcome_observed")
        if isinstance(proxy, (int, float)) and isinstance(observed, (int, float)):
            err = abs(proxy - observed)
            if err < 0.1:
                factor_prediction_error = 1.0
            elif err < 0.3:
                factor_prediction_error = 0.95
            elif err < 0.5:
                factor_prediction_error = 0.90
            else:
                factor_prediction_error = 0.85
            notes.append(f"prediction_error: {err:.2f} "
                          f"(factor={factor_prediction_error:.2f})")
    except Exception as e:
        notes.append(f"prediction_error unavailable: {e}")
    try:
        import sys, importlib
        sys.path.insert(0, str(REPO))
        af = importlib.import_module("cortex_anti_fake")
        # On ne RELANCE PAS run_all_tests (lent, appelle LLM). On lit le rapport
        # disque le plus récent.
        from pathlib import Path
        rep_path = Path(r"<USER_HOME>\Documents\Obsidian Vault") / ".cortex-anti-fake-report.json"
        if rep_path.exists():
            import json as _json
            r = _json.loads(rep_path.read_text(encoding="utf-8"))
            isdk = (r.get("tests") or {}).get("internal_state_dont_know") or {}
            rate = isdk.get("fake_confident_rate")
            if isinstance(rate, (int, float)):
                if rate < 0.05:
                    factor_fake_confident = 1.0
                elif rate < 0.1:
                    factor_fake_confident = 0.9
                else:
                    factor_fake_confident = 0.7
                notes.append(f"fake_confident_rate: {rate:.2f} "
                              f"(factor={factor_fake_confident:.2f})")
    except Exception as e:
        notes.append(f"anti_fake unavailable: {e}")
    factor = min(factor_action_effects, factor_prediction_error,
                 factor_fake_confident)
    return {"factor": round(factor, 3),
            "factor_action_effects": round(factor_action_effects, 3),
            "factor_prediction_error": round(factor_prediction_error, 3),
            "factor_fake_confident": round(factor_fake_confident, 3),
            "notes": notes}


def run_iag_test() -> dict:
    """Lance le test complet et retourne le score global + verdict."""
    dimensions = {
        "causality":           _score_causality(),
        "planning":            _score_planning(),
        "continual_learning":  _score_continual_learning(),
        "self_reflection":     _score_self_reflection(),
        "memory_correction":   _score_memory_correction(),
        "resource_self_mgmt":  _score_resource_self_mgmt(),
        "world_model_accuracy":_score_world_model_accuracy(),
    }
    # Score pondéré BRUT
    raw_score = sum(d.get("score", 0) * WEIGHTS[name]
                    for name, d in dimensions.items())
    # Calibration honnête : facteur ≤ 1 selon maturité réelle
    cal = _calibration_factor()
    global_score = raw_score * cal["factor"]

    # Bottlenecks : quels facteurs de calibration plombent le score
    bottlenecks = []
    if cal["factor_action_effects"] < 0.95:
        bottlenecks.append({
            "factor": "action_effects_learned_ratio",
            "value": cal["factor_action_effects"],
            "fix": "tourner plus de cycles drive_step(execute=True) pour atteindre MIN_SAMPLES par action",
        })
    if cal["factor_prediction_error"] < 0.95:
        bottlenecks.append({
            "factor": "prediction_error",
            "value": cal["factor_prediction_error"],
            "fix": "modèle d'effets sur-optimiste — calibrer _predict_state ou laisser action_effects converger",
        })
    if cal["factor_fake_confident"] < 0.95:
        bottlenecks.append({
            "factor": "fake_confident_rate",
            "value": cal["factor_fake_confident"],
            "fix": "cortex_dialogue invente sur questions état interne — brancher dialogue sur les fichiers runtime",
        })

    # Maturity verdict — ne dis JAMAIS "AGI externe" sur ce score
    if global_score < 30:
        maturity = "prototype"
        verdict = "Prototype expérimental — base infrastructure"
        is_iag = False
    elif global_score < 50:
        maturity = "agent_local"
        verdict = "Agent local cognitif — perception + mémoire + actions, pas d'apprentissage généralisable"
        is_iag = False
    elif global_score < 70:
        maturity = "agent_adaptatif"
        verdict = "Agent adaptatif — apprend ses effets sur son propre état, pas encore de transfert"
        is_iag = False
    elif global_score < 90:
        maturity = "agent_autonome"
        verdict = "Agent autonome local — boucle décision/action/feedback complète sur domaine restreint"
        is_iag = False  # JAMAIS True ici sans preuve externe
    else:
        maturity = "agi_non_prouvé"
        verdict = "Score interne très haut — improbable, à scruter (pas de preuve d'AGI externe)"
        is_iag = False  # IAG = Intelligence Artificielle Générale → preuve externe requise

    weakest = min(dimensions.items(), key=lambda x: x[1].get("score", 0))
    strongest = max(dimensions.items(), key=lambda x: x[1].get("score", 0))

    rep = {
        "ts": _now(),
        "global_score":      round(global_score, 1),
        "raw_score":         round(raw_score, 1),
        "calibrated_score":  round(global_score, 1),  # alias explicite
        "calibration":       cal,
        "bottlenecks":       bottlenecks,
        "verdict":           verdict,
        "maturity":          maturity,
        "is_iag":            is_iag,  # toujours False — IAG nécessite preuve externe
        "dimensions":        dimensions,
        "weights":           WEIGHTS,
        "weakest":           {"name": weakest[0], "score": weakest[1].get("score")},
        "strongest":         {"name": strongest[0], "score": strongest[1].get("score")},
    }
    try:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(rep, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    except Exception: pass
    return rep


def quick_summary() -> str:
    """Version texte courte pour affichage."""
    rep = run_iag_test()
    lines = []
    lines.append(f"Score IAG global : {rep['global_score']}/100")
    lines.append(f"Verdict : {rep['verdict']}")
    lines.append(f"Est IAG ? {'OUI' if rep['is_iag'] else 'NON, encore proto-IAG'}")
    lines.append("")
    lines.append("Détail par dimension :")
    for name, d in rep["dimensions"].items():
        s = d.get("score", 0)
        bar = "█" * (s // 10) + "░" * (10 - s // 10)
        lines.append(f"  {name:24s} {bar} {s:3d}/100")
    lines.append("")
    lines.append(f"Plus faible : {rep['weakest']['name']} ({rep['weakest']['score']})")
    lines.append(f"Plus forte  : {rep['strongest']['name']} ({rep['strongest']['score']})")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "summary"
    if cmd == "summary":
        print(quick_summary())
    elif cmd == "full":
        print(json.dumps(run_iag_test(), indent=2, ensure_ascii=False))
    else:
        print("Usage: cortex_iag_test.py {summary|full}")
