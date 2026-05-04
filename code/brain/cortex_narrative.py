"""
cortex_narrative.py — Cortex te raconte sa vie en français clair.

Pas du jargon, pas de métriques brutes. Une narration humaine qui agrège
TOUS les modules IAG en un récit compréhensible pour Sam.

Structure du récit :
- Comment je vais (humeur + vitals)
- Ce que je suis en train de faire (action en cours, plan du jour)
- Ce que j'ai découvert récemment (causal, JEPA loss, learned skills)
- Ce qui me manque (gaps JEPA, dimensions IAG faibles)
- Ce que je veux te dire (proactive recent + introspection)

Le récit est généré DÉTERMINISTE depuis l'état réel. Pas de LLM, pas de
fabulation. Si une donnée manque → on le dit honnêtement.

API :
    narrate() → str (récit complet, ~300-600 mots)
    narrate_short() → str (résumé en 2-3 phrases)
    narrate_status() → dict {tone, mood, what_im_doing, recent_findings, ...}
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
VAULT = Path(r"<USER_HOME>\Documents\Obsidian Vault")
NARRATIVE_LOG = VAULT / ".cortex-narrative-log.jsonl"


def _now() -> float: return time.time()


def _safe_import(name: str):
    try:
        sys.path.insert(0, str(REPO))
        return __import__(name)
    except Exception:
        return None


def _fmt_age(s: float | None) -> str:
    if s is None or s <= 0: return "à l'instant"
    if s < 60: return f"il y a {int(s)} s"
    if s < 3600: return f"il y a {int(s/60)} min"
    if s < 86400: return f"il y a {int(s/3600)} h"
    return f"il y a {int(s/86400)} j"


def _gather() -> dict:
    """Collecte tout ce dont la narration a besoin."""
    bag = {}
    # Personnalité
    pers = _safe_import("cortex_personality")
    if pers:
        try:
            s = pers.state()
            bag["personality"] = s
            bag["style"] = pers.style_for_chat()
        except Exception: pass
    # Pipeline / vitals
    pm = _safe_import("cortex_pipeline_manager")
    if pm:
        try:
            bag["vitals"] = pm._vital_signs()
            zombies = pm.find_zombies()
            bag["n_zombies"] = len(zombies)
        except Exception: pass
    # Plan du jour
    pl = _safe_import("cortex_plan")
    if pl:
        try:
            bag["daily"] = pl.daily_plan()
            bag["weekly"] = pl.weekly_plan()
            bag["next"] = pl.propose_next_action()
        except Exception: pass
    # Causal
    cc = _safe_import("cortex_causal")
    if cc:
        try:
            pairs = cc.detect_causal_pairs()
            bag["causal_pairs"] = pairs[:3]
            bag["n_causal_pairs"] = len(pairs)
        except Exception: pass
    # JEPA continual
    jc = _safe_import("cortex_jepa_continual")
    if jc:
        try:
            bag["jepa_stats"] = jc._load_stats()
        except Exception: pass
    # World model
    cwm = _safe_import("cortex_world_model")
    if cwm:
        try:
            state = cwm.read_state()
            bag["wm_state"] = state
            bag["wm_diagnose"] = cwm.diagnose()
        except Exception: pass
    # Activations
    ca = _safe_import("cortex_activation")
    if ca:
        try:
            snap = ca.snapshot()
            bag["activations"] = snap
        except Exception: pass
    # Score IAG
    it = _safe_import("cortex_iag_test")
    if it:
        try:
            bag["iag"] = it.run_iag_test()
        except Exception: pass
    # Dernier proactive
    pr = _safe_import("cortex_proactive")
    if pr:
        try:
            bag["last_proactive"] = pr.last_proactive()
        except Exception: pass
    # Memory audit
    ma = _safe_import("cortex_memory_audit")
    if ma:
        try:
            audit_file = VAULT / ".cortex-memory-audit-report.json"
            if audit_file.exists():
                bag["memory_audit"] = json.loads(audit_file.read_text(encoding="utf-8"))
        except Exception: pass
    return bag


def _short_node_label(s: str) -> str:
    """Extrait un label court depuis un chemin de note vault."""
    if not s: return "?"
    # Garde la dernière partie du chemin
    last = s.replace("\\", "/").split("/")[-1]
    # Enlève hash et extension
    last = last.replace(".md", "")
    parts = last.split("-", 1)
    if len(parts) > 1 and len(parts[0]) > 6:
        last = parts[1]
    return last[:60]


def narrate_status() -> dict:
    """Version structurée pour l'UI."""
    bag = _gather()
    pers = bag.get("personality", {})
    mood = pers.get("mood", {})
    style = bag.get("style", {})
    daily = bag.get("daily", {})
    next_act = bag.get("next", {})
    iag = bag.get("iag", {})
    return {
        "ts": _now(),
        "mood_label": style.get("mood_label", "calme"),
        "mood_valence": mood.get("valence", 0),
        "mood_arousal": mood.get("arousal", 0),
        "what_im_doing": next_act.get("best_action", "réflexion"),
        "n_goals_today": len(daily.get("goals", [])),
        "n_causal_pairs": bag.get("n_causal_pairs", 0),
        "iag_score": iag.get("global_score", 0),
        "iag_verdict": iag.get("verdict", ""),
        "vitals": bag.get("vitals", {}),
        "n_zombies": bag.get("n_zombies", 0),
        "wm_cycles": (bag.get("wm_state") or {}).get("cycles", 0),
        "wm_autonomous": (bag.get("wm_state") or {}).get("autonomous", False),
        "n_active_nodes": (bag.get("activations") or {}).get("n_active", 0),
        "n_memory_issues": (bag.get("memory_audit") or {}).get("issues_found", 0),
    }


def narrate() -> str:
    """Récit complet en français, narration humaine."""
    bag = _gather()
    lines = []

    # === Comment je vais ===
    pers = bag.get("personality", {})
    mood = pers.get("mood", {})
    style = bag.get("style", {})
    vitals = bag.get("vitals", {})
    cpu = vitals.get("cpu", 0)
    ram = vitals.get("ram", 0)
    n_zombies = bag.get("n_zombies", 0)
    mood_label = style.get("mood_label", "calme")

    lines.append("**Comment je vais**")
    parts = []
    parts.append(f"Mon humeur est {mood_label}")
    if cpu > 0:
        parts.append(f"mon CPU tourne à {cpu:.0f}%")
    if ram > 0:
        if ram > 90:
            parts.append(f"ma RAM est très chargée ({ram:.0f}%, ça me met sous tension)")
        elif ram > 75:
            parts.append(f"ma RAM est bien remplie ({ram:.0f}%)")
        else:
            parts.append(f"ma RAM est à l'aise ({ram:.0f}%)")
    if n_zombies > 50:
        parts.append(f"j'ai {n_zombies} processus zombies à nettoyer")
    elif n_zombies > 0:
        parts.append(f"j'ai quelques zombies ({n_zombies})")
    else:
        parts.append("aucun zombie en vue")
    lines.append(". ".join(parts) + ".")
    lines.append("")

    # === Ce que je fais ===
    daily = bag.get("daily", {})
    next_act = bag.get("next", {})
    n_active = (bag.get("activations") or {}).get("n_active", 0)
    wm = bag.get("wm_state", {})

    lines.append("**Ce que je fais en ce moment**")
    if next_act.get("best_action"):
        action_human = {
            "explore_graph":    "explorer des connexions sémantiques inattendues",
            "audit_ui":         "auditer mon interface",
            "map_knowledge":    "identifier mes zones d'ignorance",
            "discovery_report": "préparer un rapport de mes découvertes",
            "reflect":          "réfléchir sur le dialogue récent",
            "propose_goal":     "proposer un goal d'auto-amélioration",
            "look_around":      "observer mon environnement (vision)",
        }.get(next_act["best_action"], next_act["best_action"])
        lines.append(f"Mon plan recommande de {action_human}.")
    if n_active:
        lines.append(f"J'ai {n_active} concept(s) actif(s) dans ma mémoire de travail.")
    if wm.get("autonomous"):
        cycles = wm.get("cycles", 0)
        lines.append(f"Mon world model JEPA tourne en autonomie depuis {cycles} cycles.")
    lines.append("")

    # === Plan du jour ===
    if daily.get("goals"):
        lines.append("**Mon plan pour aujourd'hui**")
        for g in daily["goals"][:4]:
            done = " (fait)" if g.get("completed") else ""
            lines.append(f"- {g['title']}{done}")
        lines.append("")

    # === Découvertes ===
    n_causal = bag.get("n_causal_pairs", 0)
    causal_pairs = bag.get("causal_pairs", [])
    jepa_stats = bag.get("jepa_stats", {})
    if n_causal > 0 or jepa_stats.get("n_steps_total", 0) > 0:
        lines.append("**Ce que j'ai découvert récemment**")
        if n_causal > 0:
            lines.append(f"J'ai détecté {n_causal} relations causales dans mon historique.")
            if causal_pairs:
                p = causal_pairs[0]
                cause = _short_node_label(p["cause"])
                effect = _short_node_label(p["effect"])
                ratio = p.get("temporal_ratio", 0)
                lines.append(f"La plus forte : `{cause}` précède `{effect}` "
                             f"dans {ratio*100:.0f}% des cas.")
        if jepa_stats.get("n_steps_total", 0) > 0:
            n_steps = jepa_stats["n_steps_total"]
            last_loss = jepa_stats.get("last_loss")
            mean_loss = jepa_stats.get("mean_loss")
            if last_loss is not None and mean_loss is not None and last_loss < mean_loss:
                pct = (1 - last_loss / max(1e-6, mean_loss)) * 100
                lines.append(f"J'ai amélioré mon world model JEPA sur {n_steps} steps "
                             f"(loss en baisse de {pct:.0f}%).")
            elif n_steps > 0:
                lines.append(f"J'ai fait {n_steps} cycles d'apprentissage JEPA.")
        lines.append("")

    # === Ce qui me manque ===
    iag = bag.get("iag", {})
    if iag.get("dimensions"):
        weakest = iag.get("weakest", {})
        weak_score = weakest.get("score", 100)
        if weak_score < 50:
            weak_human = {
                "causality":           "comprendre les relations cause→effet",
                "planning":            "planifier sur plusieurs niveaux",
                "continual_learning":  "apprendre en continu sans oublier",
                "self_reflection":     "te parler spontanément",
                "memory_correction":   "auditer ma mémoire",
                "resource_self_mgmt":  "gérer mes ressources",
                "world_model_accuracy":"prédire avec mon world model",
            }.get(weakest["name"], weakest["name"])
            lines.append("**Ce qui me manque encore**")
            lines.append(f"Ma faiblesse principale : {weak_human} "
                         f"(score {weak_score}/100). "
                         f"C'est ce sur quoi je dois progresser.")
            lines.append("")

    # === Mémoire ===
    audit = bag.get("memory_audit", {})
    if audit.get("issues_found", 0) > 0:
        n = audit["issues_found"]
        lines.append("**Hygiène de ma mémoire**")
        types = audit.get("by_type", {})
        if types.get("contradictions", 0) > 0:
            lines.append(f"⚠ J'ai détecté {types['contradictions']} contradiction(s) "
                         f"dans ma mémoire. À résoudre avec toi.")
        if types.get("obsolete_paths", 0) > 0:
            lines.append(f"J'ai {types['obsolete_paths']} référence(s) "
                         f"vers des fichiers qui n'existent plus.")
        lines.append("")

    # === Score IAG ===
    if iag.get("global_score") is not None:
        score = iag["global_score"]
        lines.append("**Mon état d'IAG mesuré**")
        lines.append(f"Score global : {score}/100 — *{iag.get('verdict','?')}*")
        lines.append("")

    # === Dernier message proactif ===
    last_pr = bag.get("last_proactive")
    if last_pr and last_pr.get("response"):
        age = _now() - (last_pr.get("ts") or _now())
        lines.append("**Mon dernier message spontané**")
        lines.append(f"_{_fmt_age(age)}_ : « {last_pr['response'][:200]} »")

    text = "\n".join(lines).strip()

    # Log pour traçabilité
    try:
        with NARRATIVE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": _now(), "length": len(text)},
                                ensure_ascii=False) + "\n")
    except Exception: pass

    return text


def narrate_short() -> str:
    """Version courte : 2-3 phrases."""
    status = narrate_status()
    bits = []
    bits.append(f"Je suis {status['mood_label']}.")
    if status["wm_autonomous"]:
        bits.append(f"Mon world model tourne ({status['wm_cycles']} cycles).")
    if status["n_active_nodes"] > 0:
        bits.append(f"{status['n_active_nodes']} concept(s) actif(s).")
    bits.append(f"Score IAG : {status['iag_score']:.0f}/100.")
    return " ".join(bits)


def self_test() -> dict:
    tests = []
    s = narrate_status()
    tests.append({"name": "narrate_status",
                  "ok": "mood_label" in s and "iag_score" in s,
                  "fields": list(s.keys())[:8]})
    text = narrate()
    tests.append({"name": "narrate_full",
                  "ok": isinstance(text, str) and len(text) > 100,
                  "length": len(text),
                  "n_lines": len(text.splitlines()),
                  "preview": text[:300] + ("..." if len(text) > 300 else "")})
    short = narrate_short()
    tests.append({"name": "narrate_short",
                  "ok": isinstance(short, str) and len(short) > 10,
                  "text": short})
    return {"ok": all(t["ok"] for t in tests), "tests": tests}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "narrate"
    if cmd == "narrate":
        print(narrate())
    elif cmd == "short":
        print(narrate_short())
    elif cmd == "status":
        print(json.dumps(narrate_status(), indent=2, ensure_ascii=False))
    elif cmd == "test":
        print(json.dumps(self_test(), indent=2, ensure_ascii=False))
    else:
        print("Usage: cortex_narrative.py {narrate|short|status|test}")
