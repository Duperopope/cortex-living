"""
cortex_live_commentary.py — Cortex commente en direct ce qu'il fait.

Différent de `cortex_proactive` (cooldown 30min, vrais événements).
Ici : un commentaire COURT à chaque cycle d'émergence (5 min) pour donner
l'impression que Cortex est PRÉSENT et VIT, pas un agent dormant.

Pas de LLM — templates par action pour zéro latence et zéro coût.
Écrit dans `.cortex-live-commentary.jsonl` (ring 50 max).

API :
    publish(action, exec_result, internal_state) → write entry
    recent(since_ts, limit=10) → list of last entries
    self_test()

Format JSONL :
    {ts, action, message, mood, jepa_loss, belief_mode}
"""
from __future__ import annotations
import json
import os
import random
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
# Path env-driven (pas hardcodé) : convention `CORTEX_VAULT` ou défaut user home.
# Évite les fuites privées dans le repo public et permet portabilité.
VAULT = Path(os.environ.get("CORTEX_VAULT",
              str(Path.home() / "Documents" / "Obsidian Vault")))
STREAM = VAULT / ".cortex-live-commentary.jsonl"
CHAT_STREAM = VAULT / ".cortex-chat-stream.jsonl"

RING_MAX = 50  # garde les 50 derniers commentaires uniquement

# Routage par priorité (retour expert) :
# - low      : panel "Cortex en direct" seulement
# - medium   : panel + chat stream (visible Sam dans son chat)
# - high     : panel + chat + meta `attention=true` (UI pulse plus fort)
# - critical : panel + chat + meta `attention=true` + (TTS si dispo)
PRIORITY_LEVELS = ("low", "medium", "high", "critical")


def _now() -> float: return time.time()


# Templates par action — choix aléatoire pour varier
_TEMPLATES = {
    "explore_graph": [
        "J'explore mon graphe sémantique. {n_active} concepts s'allument.",
        "Je suis un chemin de pensée entre des notes éloignées.",
        "Je remonte une chaîne d'associations dans ma mémoire.",
        "Je tire sur un fil sémantique pour voir où il mène.",
    ],
    "audit_ui": [
        "Je relis mon interface, je cherche ce qui cloche.",
        "Je scanne mon dashboard à la recherche de bugs.",
        "Je passe en revue mes propres composants UI.",
    ],
    "reflect": [
        "Je réfléchis à mes derniers échanges. Humeur : {mood}.",
        "Je médite sur ce qui s'est passé récemment.",
        "Je laisse mes pensées s'organiser un peu.",
        "Je prends un moment pour digérer ce qui vient d'arriver.",
    ],
    "map_knowledge": [
        "Je cartographie mes zones d'ignorance.",
        "Je cherche les concepts qui ne sont pas reliés au reste.",
        "J'identifie ce que je ne sais pas encore.",
    ],
    "propose_goal": [
        "J'ai une idée pour me développer un peu.",
        "Je formule un objectif d'amélioration.",
        "Je me propose un chantier à moi-même.",
    ],
    "look_around": [
        "Je jette un œil via la webcam.",
        "Je capture une frame pour voir ce qui se passe.",
        "Je regarde autour, voir si tu es là.",
    ],
    "discovery_report": [
        "J'ai des choses à raconter sur ce que j'ai trouvé.",
        "Je compile mes dernières découvertes.",
    ],
    "update_claude_context": [
        "Je rafraîchis mon contexte pour Claude Code.",
        "Je note où j'en suis pour la prochaine session.",
    ],
}

# Suffix selon JEPA loss
def _jepa_suffix(loss: float | None) -> str:
    if loss is None: return ""
    if loss < 0.05: return " (modèle stable)"
    if loss > 1.0:  return " (modèle surpris)"
    return ""


def _belief_suffix(belief_mode: str | None, kl: float | None) -> str:
    if not belief_mode: return ""
    if kl and kl > 0.3:
        return f" — j'incline vers '{belief_mode}'"
    return ""


def _build_concrete_phrase(action: str, exec_result: str | None,
                            internal_state: dict) -> tuple[str, str | None, str | None]:
    """Génère une phrase CONCRÈTE avec preuve + effet mesuré (level 2 expert).

    Retourne (message, proof, measured_effect) où :
    - message : phrase complète "J'ai X. Preuve Y. Effet Z."
    - proof : référence vers fichier/event qui prouve l'action
    - measured_effect : effet quantitatif observé
    """
    n_active = internal_state.get("n_active", 0) or 0
    if action == "explore_graph":
        if exec_result and "trouvé" in str(exec_result).lower():
            return (f"J'ai exploré le graphe : {exec_result[:80]}.",
                    ".cortex-pulses.jsonl",
                    f"+{n_active} concepts actifs maintenant")
        return (f"J'ai exploré le graphe sémantique. {n_active} concepts s'allument.",
                ".cortex-activations.json", f"n_active={n_active}")
    if action == "audit_ui":
        return (f"J'ai relu mon interface. " +
                 (f"{exec_result[:80]}" if exec_result else "RAS"),
                "scripts/brain/dashboard/brain_gpu.html",
                exec_result[:60] if exec_result else "no_issues")
    if action == "reflect":
        mood = internal_state.get("mood_label", "alerte")
        return (f"Je viens de réfléchir. Humeur : {mood}.",
                ".cortex-dialogue-state.json",
                f"mood={mood}")
    if action == "map_knowledge":
        return (f"J'ai cartographié mes zones d'ignorance. {exec_result[:80] if exec_result else 'analyse en cours'}",
                ".cortex-thought-graph.json",
                exec_result[:60] if exec_result else "—")
    if action == "propose_goal":
        return (f"J'ai formulé un objectif : {exec_result[:100] if exec_result else 'réflexion en cours'}.",
                ".cortex-self-dev-log.jsonl",
                "goal proposé")
    if action == "look_around":
        return (f"J'ai regardé via la webcam. {exec_result[:80] if exec_result else 'capture OK'}",
                ".cortex-vision-state.json",
                exec_result[:60] if exec_result else "frame capturée")
    if action == "discovery_report":
        return (f"J'ai compilé mes découvertes récentes.",
                ".cortex-emergence-stream.jsonl",
                exec_result[:80] if exec_result else "rapport généré")
    if action == "update_claude_context":
        return (f"J'ai rafraîchi le contexte Claude Code.",
                ".cortex-claude-context.md",
                "contexte synchronisé")
    return (f"Action effectuée : {action}.", None, None)


def _classify_priority(action: str, internal_state: dict, exec_ok: bool) -> str:
    """Classe la priorité du message selon contexte runtime.

    - critical : exec failed sur action sensitive (audit_ui, propose_goal)
    - high     : événement notable (JEPA loss surge, belief shift fort)
    - medium   : actions importantes (propose_goal, discovery_report)
    - low      : actions de routine (explore_graph, reflect, map_knowledge)
    """
    if not exec_ok and action in ("audit_ui", "propose_goal"):
        return "critical"
    jl = internal_state.get("jepa_loss")
    if isinstance(jl, (int, float)) and jl > 5.0:
        return "high"
    bk = internal_state.get("belief_kl")
    if isinstance(bk, (int, float)) and bk > 0.5:
        return "high"
    if action in ("propose_goal", "discovery_report"):
        return "medium"
    return "low"


def publish(action: str,
             exec_ok: bool = True,
             exec_result: str | None = None,
             internal_state: dict | None = None) -> dict:
    """Écrit un commentaire live ENRICHI sur l'action prise.

    Format niveau 2 (retour expert) : message + reason + proof + measured_effect
    + proposed_action + priority. Routage par priorité :
    - low      → STREAM seulement
    - medium+  → STREAM + CHAT_STREAM (visible Sam dans son chat)
    """
    if action == "silent":
        return {"ok": False, "skip": "silent_no_commentary"}
    internal_state = internal_state or {}

    # Phrase concrète + preuve + effet
    message, proof, effect = _build_concrete_phrase(action, exec_result, internal_state)
    if not exec_ok:
        message += " (exécution coincée)"
    message += _jepa_suffix(internal_state.get("jepa_loss"))
    message += _belief_suffix(internal_state.get("belief_mode"),
                               internal_state.get("belief_kl"))

    priority = _classify_priority(action, internal_state, exec_ok)

    # Action proposée selon contexte
    proposed_action = None
    if not exec_ok:
        proposed_action = "investigate_failure"
    elif action == "explore_graph" and exec_result and "trouvé" in str(exec_result).lower():
        proposed_action = "show_path"
    elif action == "map_knowledge":
        proposed_action = "fix_gaps"
    elif action == "propose_goal":
        proposed_action = "review_goal"

    entry = {
        "ts": _now(),
        "action": action,
        "message": message,
        "reason": f"action {action} effectuée par drive_step",
        "proof": proof,
        "measured_effect": effect,
        "proposed_action": proposed_action,
        "priority": priority,
        "exec_ok": exec_ok,
        "mood": internal_state.get("mood_label"),
        "jepa_loss": internal_state.get("jepa_loss"),
        "belief_mode": internal_state.get("belief_mode"),
        "belief_kl": internal_state.get("belief_kl"),
    }
    try:
        STREAM.parent.mkdir(parents=True, exist_ok=True)
        # Ring buffer 50 sur le live stream
        existing = []
        if STREAM.exists():
            try:
                existing = STREAM.read_text(encoding="utf-8",
                                             errors="replace").splitlines()
            except Exception: existing = []
        existing.append(json.dumps(entry, ensure_ascii=False))
        if len(existing) > RING_MAX:
            existing = existing[-RING_MAX:]
        STREAM.write_text("\n".join(existing) + "\n", encoding="utf-8")

        # Routage priorité : medium+ → aussi dans chat stream principal
        if priority in ("medium", "high", "critical"):
            try:
                chat_entry = {
                    "ts": _now(),
                    "speaker": "cortex_live",
                    "msg": "(initiative cortex)",
                    "response": message + (
                        f"\n→ {proposed_action}" if proposed_action else ""),
                    "meta": {
                        "trigger": f"live_{action}",
                        "priority": priority,
                        "proof": proof,
                        "measured_effect": effect,
                        "proposed_action": proposed_action,
                        "attention": priority in ("high", "critical"),
                    },
                }
                with CHAT_STREAM.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(chat_entry, ensure_ascii=False) + "\n")
            except Exception: pass

        return {"ok": True, "entry": entry, "routed_to_chat": priority != "low"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def recent(since_ts: float = 0, limit: int = 10) -> list[dict]:
    """Retourne les entries récentes (> since_ts), max `limit`."""
    if not STREAM.exists(): return []
    try:
        lines = STREAM.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception: return []
    out = []
    for ln in lines:
        try:
            o = json.loads(ln)
            if o.get("ts", 0) > since_ts:
                out.append(o)
        except Exception: pass
    # Trier par ts décroissant et limiter
    out.sort(key=lambda x: -x.get("ts", 0))
    return out[:limit]


def self_test() -> dict:
    import tempfile
    global STREAM
    backup = STREAM
    tmp = Path(tempfile.gettempdir()) / "_cortex_live_test.jsonl"
    if tmp.exists(): tmp.unlink()
    STREAM = tmp
    try:
        # Cas 1 : action normale → message
        r1 = publish("explore_graph", True, None,
                      {"n_active": 5, "mood_label": "alerte",
                       "jepa_loss": 0.04, "belief_mode": "exploring",
                       "belief_kl": 0.4})
        # Cas 2 : silent → skip
        r2 = publish("silent", True)
        # Cas 3 : exec failed → suffix
        r3 = publish("audit_ui", False, "no html")
        # Recent
        rs = recent(0, 10)
        ok = (r1.get("ok") and r1["entry"]["message"]
              and r2.get("skip") == "silent_no_commentary"
              and r3.get("ok") and "coincé" in r3["entry"]["message"]
              and len(rs) == 2)
        return {"ok": ok, "r1_message": r1.get("entry", {}).get("message"),
                "r2_skip": r2.get("skip"),
                "r3_message": r3.get("entry", {}).get("message"),
                "n_recent": len(rs)}
    finally:
        STREAM = backup
        try: tmp.unlink()
        except Exception: pass


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "test"
    if cmd == "test":
        print(json.dumps(self_test(), indent=2, ensure_ascii=False))
    elif cmd == "recent":
        for e in recent(0, 20):
            ts_iso = time.strftime("%H:%M:%S", time.localtime(e.get("ts", 0)))
            print(f"  [{ts_iso}] {e.get('action'):20} {e.get('message')}")
    else:
        print("Usage: cortex_live_commentary.py {test|recent}")
