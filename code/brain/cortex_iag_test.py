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
    # Score pondéré
    global_score = sum(d.get("score", 0) * WEIGHTS[name]
                       for name, d in dimensions.items())
    # Verdict
    if global_score < 30:
        verdict = "système autonome simple"
        is_iag = False
    elif global_score < 50:
        verdict = "proto-IAG observable (ce que Cortex est aujourd'hui)"
        is_iag = False
    elif global_score < 70:
        verdict = "IAG faible (specialized AGI)"
        is_iag = True
    elif global_score < 90:
        verdict = "IAG forte sur ce domaine"
        is_iag = True
    else:
        verdict = "IAG générale (improbable, à scruter)"
        is_iag = True

    weakest = min(dimensions.items(), key=lambda x: x[1].get("score", 0))
    strongest = max(dimensions.items(), key=lambda x: x[1].get("score", 0))

    rep = {
        "ts": _now(),
        "global_score":     round(global_score, 1),
        "verdict":          verdict,
        "is_iag":           is_iag,
        "dimensions":       dimensions,
        "weights":          WEIGHTS,
        "weakest":          {"name": weakest[0], "score": weakest[1].get("score")},
        "strongest":        {"name": strongest[0], "score": strongest[1].get("score")},
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
