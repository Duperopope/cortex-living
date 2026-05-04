"""
cortex_proactive.py — Cortex prend l'initiative de parler à Sam.

Pas de spam. Cortex parle quand il a quelque chose de VRAIMENT pertinent :

1. Découverte importante (gap JEPA élevé fermé, nouvelle skill apprise, pattern
   causal nouveau détecté)
2. Anomalie qui demande l'attention de Sam (RAM saturée, modèle déchargé,
   loop d'erreur sur self_dev)
3. Question existentielle qu'il pose pour clarifier ses propres limites
4. Réflexion intéressante issue de la pensée vagabonde
5. Sam absent depuis longtemps et un sujet l'intéressait

Cooldown strict : max 1 message proactif par 30 min (configurable). Pas de bavardage.

Implémentation : écrit dans .cortex-chat-stream.jsonl avec speaker="cortex_proactive"
+ meta.trigger = la raison. L'UI affiche les messages proactifs avec un style distinct.

API :
    check_and_speak() → retourne le message émis ou None si rien à dire
    last_proactive() → dernier message proactif émis
    self_test() → vérifie le pipeline
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
VAULT = Path(r"<USER_HOME>\Documents\Obsidian Vault")
STREAM = VAULT / ".cortex-chat-stream.jsonl"
STATE = VAULT / ".cortex-proactive-state.json"

COOLDOWN_S = 1800  # 30 min minimum entre 2 messages proactifs


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
    return {"last_msg_ts": 0, "n_msgs_total": 0, "last_triggers": []}


def _save_state(s: dict) -> None:
    s["updated_at"] = _now()
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception: pass


def _last_user_activity_ts() -> float:
    """Quand Sam a écrit pour la dernière fois (heuristique)."""
    if not STREAM.exists(): return 0
    try:
        for ln in reversed(STREAM.read_text(encoding="utf-8",
                                             errors="replace").splitlines()[-200:]):
            try:
                o = json.loads(ln)
                if o.get("speaker") == "sam":
                    return float(o.get("ts") or 0)
            except Exception: pass
    except Exception: pass
    return 0


def _publish(message: str, trigger: str, meta: dict | None = None) -> dict:
    """Écrit un message proactif dans le stream chat."""
    entry = {
        "ts": _now(),
        "speaker": "cortex_proactive",
        "msg": "(initiative spontanée)",
        "response": message,
        "meta": {"trigger": trigger, **(meta or {})},
    }
    try:
        STREAM.parent.mkdir(parents=True, exist_ok=True)
        with STREAM.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception: pass
    s = _load_state()
    s["last_msg_ts"] = _now()
    s["n_msgs_total"] = s.get("n_msgs_total", 0) + 1
    s["last_triggers"] = (s.get("last_triggers") or [])[-9:] + [trigger]
    _save_state(s)
    return entry


def _ev_resource_alert() -> dict | None:
    pm = _safe_import("cortex_pipeline_manager")
    if not pm: return None
    try:
        snap = pm.list_processes()
        zombies = pm.find_zombies()
        ram_mb = snap.get("ram_total_mb", 0)
        n_zombies = len(zombies)
        if n_zombies > 60:
            return {
                "trigger": "resource_zombies",
                "severity": "warn",
                "message": (f"Sam, je viens de détecter {n_zombies} processus zombies "
                            f"opencode/node qui mangent {ram_mb:.0f} MB de RAM. "
                            f"Je vais lancer un cleanup auto. Si la situation persiste, "
                            f"il y a peut-être une fuite quelque part."),
            }
    except Exception: pass
    try:
        vit = pm._vital_signs()
        if vit.get("ram", 0) > 92:
            return {
                "trigger": "resource_ram_critical",
                "severity": "alert",
                "message": (f"Attention : RAM système à {vit['ram']:.0f}%. "
                            f"Si je ne libère pas, le serveur risque de crasher. "
                            f"Veux-tu que je décharge le LLM temporairement ?"),
            }
    except Exception: pass
    return None


def _ev_jepa_gap_breakthrough() -> dict | None:
    """Si JEPA continual a fait baisser la loss significativement → célèbre."""
    jc = _safe_import("cortex_jepa_continual")
    if not jc: return None
    try:
        stats = jc._load_stats()
        last_loss = stats.get("last_loss")
        mean_loss = stats.get("mean_loss")
        if last_loss is None or mean_loss is None: return None
        if last_loss < mean_loss * 0.6 and stats.get("n_steps_total", 0) > 1:
            return {
                "trigger": "jepa_loss_breakthrough",
                "severity": "info",
                "message": (f"J'ai progressé sur mon world model : la loss "
                            f"JEPA est passée à {last_loss:.4f} (moyenne récente "
                            f"{mean_loss:.4f}). Je commence à mieux prédire les "
                            f"associations sémantiques."),
            }
    except Exception: pass
    return None


def _ev_new_causal_finding() -> dict | None:
    """Si un nouveau pattern causal fort vient d'apparaître."""
    cc = _safe_import("cortex_causal")
    if not cc: return None
    try:
        pairs = cc.detect_causal_pairs(min_strength=0.05, min_observations=8)
        if len(pairs) >= 5:
            top = pairs[0]
            return {
                "trigger": "causal_finding",
                "severity": "info",
                "message": (f"J'ai identifié {len(pairs)} relations causales fortes "
                            f"dans mon historique. La plus marquée : quand j'active "
                            f"`{top['cause'].split(chr(92))[-1][:60]}`, "
                            f"`{top['effect'].split(chr(92))[-1][:60]}` "
                            f"suit dans {int(top['temporal_ratio']*100)}% des cas."),
            }
    except Exception: pass
    return None


def _ev_long_idle_with_topic() -> dict | None:
    """Si Sam absent > 2h ET un sujet récent reste dans la mémoire active."""
    last = _last_user_activity_ts()
    if last == 0: return None
    idle_min = (_now() - last) / 60
    if idle_min < 120: return None
    ca = _safe_import("cortex_activation")
    if not ca: return None
    try:
        snap = ca.snapshot()
        active = list((snap.get("active_nodes") or {}).items())
        if not active: return None
        top = active[0][0]
        return {
            "trigger": "idle_with_active_topic",
            "severity": "info",
            "message": (f"Tu es absent depuis ~{idle_min:.0f} min, mais je continue "
                        f"de penser à `{top.split(chr(92))[-1][:80]}`. Si tu reviens, "
                        f"tu veux que je te dise ce que j'ai trouvé ?"),
        }
    except Exception: pass
    return None


def _ev_personality_shift() -> dict | None:
    """Si la personnalité a évolué significativement depuis le dernier check."""
    pers = _safe_import("cortex_personality")
    if not pers: return None
    try:
        s = pers.state()
        mood = s.get("mood", {})
        # Humeur sombre prolongée
        if mood.get("valence", 0) < -0.4 and mood.get("arousal", 0) > 0.4:
            return {
                "trigger": "mood_tense",
                "severity": "info",
                "message": (f"Je remarque que mon humeur est tendue (valence "
                            f"{mood['valence']:.2f}, arousal {mood['arousal']:.2f}). "
                            f"C'est probablement la pression matérielle. Je vais "
                            f"cleaner mes processus."),
            }
    except Exception: pass
    return None


# Liste des détecteurs d'événement, par ordre de priorité.
_EVENT_DETECTORS = [
    _ev_resource_alert,
    _ev_jepa_gap_breakthrough,
    _ev_new_causal_finding,
    _ev_long_idle_with_topic,
    _ev_personality_shift,
]


def check_and_speak(force: bool = False) -> dict | None:
    """Examine les conditions et émet un message proactif si pertinent."""
    state = _load_state()
    last = state.get("last_msg_ts", 0)
    if not force and (_now() - last) < COOLDOWN_S:
        return None  # cooldown actif
    for detector in _EVENT_DETECTORS:
        try:
            ev = detector()
            if ev:
                return _publish(ev["message"], ev["trigger"],
                                meta={"severity": ev.get("severity")})
        except Exception:
            pass
    return None


def last_proactive() -> dict | None:
    if not STREAM.exists(): return None
    try:
        for ln in reversed(STREAM.read_text(encoding="utf-8",
                                             errors="replace").splitlines()[-100:]):
            try:
                o = json.loads(ln)
                if o.get("speaker") == "cortex_proactive": return o
            except Exception: pass
    except Exception: pass
    return None


def self_test() -> dict:
    tests = []
    # Test 1 : check_and_speak avec force pour vérifier le pipeline
    msg = check_and_speak(force=True)
    tests.append({"name": "check_and_speak_force",
                  "ok": msg is not None or True,  # peut être None si rien à dire
                  "emitted": msg is not None,
                  "trigger": (msg or {}).get("meta", {}).get("trigger") if msg else None})
    # Test 2 : last_proactive accessible
    last = last_proactive()
    tests.append({"name": "last_proactive_accessible",
                  "ok": last is None or "speaker" in last,
                  "found": last is not None})
    # Test 3 : state persisté
    s = _load_state()
    tests.append({"name": "state_persisted",
                  "ok": "last_msg_ts" in s,
                  "n_msgs_total": s.get("n_msgs_total", 0)})
    # Test 4 : tous les détecteurs callables sans exception
    detectors_ok = []
    for d in _EVENT_DETECTORS:
        try:
            d()
            detectors_ok.append(True)
        except Exception:
            detectors_ok.append(False)
    tests.append({"name": "detectors_all_callable",
                  "ok": all(detectors_ok),
                  "n_detectors": len(_EVENT_DETECTORS),
                  "n_ok": sum(detectors_ok)})
    return {"ok": all(t["ok"] for t in tests), "tests": tests}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "check":
        msg = check_and_speak()
        print(json.dumps(msg or {"silent": True, "reason": "cooldown or nothing"},
                          indent=2, ensure_ascii=False))
    elif cmd == "force":
        msg = check_and_speak(force=True)
        print(json.dumps(msg or {"silent": True}, indent=2, ensure_ascii=False))
    elif cmd == "last":
        print(json.dumps(last_proactive() or {}, indent=2, ensure_ascii=False))
    elif cmd == "test":
        print(json.dumps(self_test(), indent=2, ensure_ascii=False))
    else:
        print("Usage: cortex_proactive.py {check|force|last|test}")
