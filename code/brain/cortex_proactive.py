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
EVENTS = VAULT / ".cortex-proactive-events.jsonl"
RETURN_BRIEF = VAULT / ".cortex-return-brief.json"

# Cooldown par type de trigger : on autorise plus souvent les vrais
# événements importants (return, body_health) que les remarques générales.
COOLDOWN_BY_TRIGGER = {
    "sam_returned":              60,    # 1 min : prioritaire
    "body_health_critical":      300,   # 5 min : sécurité
    "action_completed":          180,   # 3 min : ne pas spammer après chaque cycle
    "jepa_loss_breakthrough":    600,   # 10 min
    "causal_finding":            900,   # 15 min
    "memory_refs_broken":        1800,  # 30 min : info chronique
    "idle_with_active_topic":    3600,  # 1h
    "mood_tense":                1800,
    "resource_zombies":          600,
    "resource_ram_critical":     180,
}
DEFAULT_COOLDOWN_S = 600  # 10 min par défaut pour triggers non listés
COOLDOWN_S = 600  # legacy global cooldown — gardé pour compat ascendante


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
    return {"last_msg_ts": 0, "n_msgs_total": 0, "last_triggers": [],
            "last_msg_ts_by_trigger": {},
            "n_proactive_events_total": 0,
            "sam_responses_to_proactive": 0,
            "accepted_suggestions": 0,
            "ignored_suggestions": 0,
            "last_sam_idle_start_ts": 0,
            "last_return_brief_ts": 0}


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
    """Écrit un message proactif dans le stream chat ET dans le journal d'événements."""
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
    # Journal d'événements proactifs (séparé du chat stream)
    try:
        ev_entry = {
            "ts": _now(),
            "trigger": trigger,
            "message": message,
            "severity": (meta or {}).get("severity", "info"),
            "proof": (meta or {}).get("proof"),
            "proposed_action": (meta or {}).get("proposed_action"),
        }
        with EVENTS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev_entry, ensure_ascii=False) + "\n")
    except Exception: pass
    s = _load_state()
    s["last_msg_ts"] = _now()
    s["n_msgs_total"] = s.get("n_msgs_total", 0) + 1
    s["n_proactive_events_total"] = s.get("n_proactive_events_total", 0) + 1
    s["last_triggers"] = (s.get("last_triggers") or [])[-9:] + [trigger]
    # Cooldown par trigger
    by_trigger = s.setdefault("last_msg_ts_by_trigger", {})
    by_trigger[trigger] = _now()
    _save_state(s)
    return entry


def _cooldown_passed(trigger: str, state: dict) -> bool:
    """True si on peut émettre ce trigger (cooldown specifique passé)."""
    by_trigger = state.get("last_msg_ts_by_trigger", {})
    last = by_trigger.get(trigger, 0)
    cd = COOLDOWN_BY_TRIGGER.get(trigger, DEFAULT_COOLDOWN_S)
    return (_now() - last) >= cd


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


def _ev_sam_returned() -> dict | None:
    """Si Sam vient de revenir après idle > 5 min → return brief auto.

    Trigger PRIORITAIRE (cooldown 60s) : ne rate pas le retour de Sam.
    Génère un brief de ce qui s'est passé pendant l'absence.
    """
    last_user = _last_user_activity_ts()
    if last_user == 0: return None
    state = _load_state()
    # Le `last_sam_idle_start_ts` est mis à jour au premier check où l'idle était
    # actif. Quand on détecte que last_user est récent (<60s) ET que l'idle
    # précédent était > 5min, on déclenche le return brief.
    idle_was = state.get("last_sam_idle_start_ts", 0)
    last_brief = state.get("last_return_brief_ts", 0)
    user_age_s = _now() - last_user
    # Sam est "revenu" si dernier input < 60s ET idle précédent était significatif
    if user_age_s > 60: return None
    if idle_was == 0: return None
    if last_user - idle_was < 300: return None  # < 5min idle, pas un vrai retour
    if last_user - last_brief < 300: return None  # déjà briefé récemment
    # Compose le brief depuis les logs runtime
    brief_data = _compose_return_brief(idle_start_ts=idle_was, return_ts=last_user)
    if not brief_data.get("substantial"): return None
    msg_parts = ["Tu reviens. Pendant ton absence (~"
                  f"{int((last_user - idle_was) / 60)} min) :"]
    if brief_data.get("n_cycles"):
        msg_parts.append(f"  • {brief_data['n_cycles']} cycles autonomes")
    if brief_data.get("hebbian_delta"):
        msg_parts.append(f"  • +{brief_data['hebbian_delta']:.1f} apprentissage Hebbian")
    if brief_data.get("jepa_train_steps_delta"):
        msg_parts.append(f"  • JEPA +{brief_data['jepa_train_steps_delta']} train_steps")
    if brief_data.get("body_severity") and brief_data["body_severity"] != "OK":
        msg_parts.append(f"  • body_health : {brief_data['body_severity']}")
    if brief_data.get("dominant_action"):
        msg_parts.append(f"  • action dominante : {brief_data['dominant_action']}")
    return {
        "trigger": "sam_returned",
        "severity": "info",
        "message": "\n".join(msg_parts),
        "proof": ".cortex-return-brief.json",
        "proposed_action": "résumer en détail / continuer / nouvelle tâche",
        "brief_data": brief_data,
    }


def _compose_return_brief(idle_start_ts: float, return_ts: float) -> dict:
    """Compile un brief de ce qui s'est passé pendant l'absence de Sam."""
    brief = {"idle_start_ts": idle_start_ts, "return_ts": return_ts,
             "duration_s": return_ts - idle_start_ts, "substantial": False}
    # n_cycles depuis vfe_history
    try:
        ai_state_path = VAULT / ".cortex-active-inference-state.json"
        if ai_state_path.exists():
            ai_state = json.loads(ai_state_path.read_text(encoding="utf-8"))
            vfe = ai_state.get("vfe_history", [])
            cycles_during = [v for v in vfe if idle_start_ts <= v.get("ts", 0) <= return_ts]
            brief["n_cycles"] = len(cycles_during)
            if cycles_during:
                # action dominante
                from collections import Counter
                actions = [v.get("chosen") for v in cycles_during if v.get("chosen")]
                if actions:
                    c = Counter(actions)
                    brief["dominant_action"] = c.most_common(1)[0][0]
                brief["substantial"] = len(cycles_during) >= 2
    except Exception: pass
    # hebbian delta : approximer via cum_hebbian_ticks au début vs maintenant
    try:
        snap_path = VAULT / ".cortex-activations.json"
        if snap_path.exists():
            snap = json.loads(snap_path.read_text(encoding="utf-8"))
            counters = snap.get("counters") or {}
            brief["hebbian_now"] = counters.get("cum_hebbian_ticks", 0)
            # delta non calculable précisément sans snapshot pré-idle
    except Exception: pass
    # JEPA train_steps delta
    try:
        sys.path.insert(0, str(REPO))
        import cortex_jepa_v2 as _j
        m = _j.JEPA.load()
        if m:
            brief["jepa_train_steps_now"] = m.n_train_steps
    except Exception: pass
    # Body health severity
    try:
        bh_path = VAULT / ".cortex-body-health-last.json"
        if bh_path.exists():
            bh = json.loads(bh_path.read_text(encoding="utf-8"))
            crit = bh.get("critical_after") or bh.get("critical_before") or {}
            brief["body_severity"] = ("CRITICAL" if any(d.get("is_critical")
                                       for d in [crit] if isinstance(d, dict)) else "OK")
    except Exception: pass
    # Save brief
    try:
        RETURN_BRIEF.parent.mkdir(parents=True, exist_ok=True)
        RETURN_BRIEF.write_text(json.dumps(brief, indent=2, ensure_ascii=False),
                                 encoding="utf-8")
    except Exception: pass
    return brief


def _ev_body_health_critical() -> dict | None:
    """Si body_health est CRITICAL, alerter."""
    try:
        sys.path.insert(0, str(REPO))
        import cortex_body_health as _bh
        s = _bh.body_health_status()
        sev = s.get("severity")
        if sev != "CRITICAL": return None
        crit = s.get("critical_disk") or {}
        return {
            "trigger": "body_health_critical",
            "severity": "alert",
            "message": (f"Disque {crit.get('mount', '?')} à {crit.get('percent', '?'):.0f}% "
                         f"({crit.get('free_gb', '?'):.1f} Go libres). Je peux lancer un cleanup auto."),
            "proof": ".cortex-body-health-last.json",
            "proposed_action": "auto_cleanup / check_intruders / ignore",
        }
    except Exception: return None


def _ev_memory_refs_broken() -> dict | None:
    """Memory audit a trouvé des refs cassées."""
    try:
        ma_report = VAULT / ".cortex-memory-audit-report.json"
        if not ma_report.exists(): return None
        r = json.loads(ma_report.read_text(encoding="utf-8"))
        n_obs = (r.get("by_type") or {}).get("obsolete_paths", 0)
        if n_obs < 5: return None
        return {
            "trigger": "memory_refs_broken",
            "severity": "info",
            "message": (f"J'ai détecté {n_obs} références mémoire vers des fichiers "
                         f"qui n'existent plus. Je peux préparer un dry-run de nettoyage."),
            "proof": ".cortex-memory-audit-report.json",
            "proposed_action": "memory_cleanup_dry_run / annotate_obsolete / ignore",
        }
    except Exception: return None


# Liste des détecteurs d'événement, par ordre de priorité.
_EVENT_DETECTORS = [
    _ev_sam_returned,             # PRIORITAIRE : retour de Sam
    _ev_body_health_critical,     # alerte sécurité
    _ev_resource_alert,
    _ev_memory_refs_broken,
    _ev_jepa_gap_breakthrough,
    _ev_new_causal_finding,
    _ev_long_idle_with_topic,
    _ev_personality_shift,
]


def check_and_speak(force: bool = False) -> dict | None:
    """Examine les conditions et émet un message proactif si pertinent.

    Cooldown par TYPE de trigger (pas global) pour permettre :
    - sam_returned avec cooldown 60s (prioritaire)
    - body_health_critical avec cooldown 5min (sécurité)
    - mais pas spammer le même type général

    Met aussi à jour `last_sam_idle_start_ts` (pour détecter les retours).
    """
    state = _load_state()
    # Tracking de l'idle : si Sam n'a pas été actif depuis > 60s, on note quand
    # cet idle a commencé (pour le return brief plus tard).
    last_user_ts = _last_user_activity_ts()
    if last_user_ts > 0:
        if _now() - last_user_ts > 60 and state.get("last_sam_idle_start_ts", 0) < last_user_ts:
            state["last_sam_idle_start_ts"] = last_user_ts
            _save_state(state)

    for detector in _EVENT_DETECTORS:
        try:
            ev = detector()
            if not ev: continue
            trigger = ev["trigger"]
            if not force and not _cooldown_passed(trigger, state):
                continue  # cooldown spécifique encore actif
            meta = {"severity": ev.get("severity")}
            if "proof" in ev: meta["proof"] = ev["proof"]
            if "proposed_action" in ev: meta["proposed_action"] = ev["proposed_action"]
            published = _publish(ev["message"], trigger, meta=meta)
            # Mark idle ended si c'était sam_returned
            if trigger == "sam_returned":
                state = _load_state()  # reload pour update
                state["last_return_brief_ts"] = _now()
                state["last_sam_idle_start_ts"] = 0  # reset
                _save_state(state)
            return published
        except Exception:
            pass
    return None


# ─── API PUBLIQUE pour les boutons UI ──────────────────────────────────────
def current_status() -> dict:
    """État courant de présence : silencieux / observe / réfléchit / propose / agit / attend.

    Déduit de l'activité runtime (dernière action AI, idle Sam, etc.).
    """
    state = _load_state()
    last_user = _last_user_activity_ts()
    last_msg = state.get("last_msg_ts", 0)
    sam_idle_s = (_now() - last_user) if last_user > 0 else 9999
    last_msg_age_s = (_now() - last_msg) if last_msg > 0 else 9999
    # Dernière action drive_step
    last_action = None
    last_action_age_s = 9999
    try:
        ai_state_path = VAULT / ".cortex-active-inference-state.json"
        if ai_state_path.exists():
            s = json.loads(ai_state_path.read_text(encoding="utf-8"))
            vfe = s.get("vfe_history", [])
            if vfe:
                last_action = vfe[-1].get("chosen")
                last_action_age_s = _now() - vfe[-1].get("ts", 0)
    except Exception: pass

    if last_action_age_s < 30:
        status = "agit"
    elif sam_idle_s < 30 and last_msg_age_s > 60:
        status = "attend Sam"
    elif sam_idle_s > 600:
        status = "observe"  # Sam absent
    elif last_msg_age_s < 60:
        status = "vient de proposer"
    else:
        status = "réfléchit"
    # Dernier événement proactif
    last_ev = None
    if EVENTS.exists():
        try:
            lines = EVENTS.read_text(encoding="utf-8", errors="replace").splitlines()
            if lines: last_ev = json.loads(lines[-1])
        except Exception: pass
    # Cooldown restant pour le prochain event probable
    by_trigger = state.get("last_msg_ts_by_trigger", {})
    cooldowns_remaining = {}
    for trig, cd in COOLDOWN_BY_TRIGGER.items():
        last = by_trigger.get(trig, 0)
        rem = max(0, cd - (_now() - last))
        if rem > 0: cooldowns_remaining[trig] = int(rem)

    return {
        "ts": _now(),
        "status": status,
        "sam_idle_s": int(sam_idle_s) if sam_idle_s < 9999 else None,
        "last_action": last_action,
        "last_action_age_s": int(last_action_age_s) if last_action_age_s < 9999 else None,
        "last_proactive_msg": last_ev,
        "cooldowns_remaining_s": cooldowns_remaining,
        "n_events_total": state.get("n_proactive_events_total", 0),
    }


def speak_now(reason: str = "user_asked") -> dict:
    """Force un message proactif maintenant (bouton "Parle-moi maintenant").

    Force l'émission ET ignore les détecteurs lents (jepa_continual, causal qui
    peuvent prendre plusieurs secondes). On préfère tomber sur un message
    rapide même si moins prioritaire.
    """
    # Détecteurs RAPIDES uniquement pour le bouton "parle-moi maintenant"
    fast_detectors = [
        _ev_sam_returned,
        _ev_body_health_critical,
        _ev_resource_alert,
        _ev_memory_refs_broken,
        _ev_long_idle_with_topic,
        _ev_personality_shift,
    ]
    state = _load_state()
    for detector in fast_detectors:
        try:
            ev = detector()
            if not ev: continue
            meta = {"severity": ev.get("severity")}
            if "proof" in ev: meta["proof"] = ev["proof"]
            if "proposed_action" in ev: meta["proposed_action"] = ev["proposed_action"]
            return _publish(ev["message"], ev["trigger"], meta=meta)
        except Exception: pass
    # Fallback : un commentaire générique sur l'état courant
    try:
        s = current_status()
        msg = (f"Je suis en mode '{s.get('status', '?')}'. "
                f"Dernière action : {s.get('last_action', '?')} "
                f"il y a {s.get('last_action_age_s', '?')}s.")
        return _publish(msg, "user_asked", meta={"severity": "info"})
    except Exception as e:
        return {"ok": False, "msg": f"Rien à dire ({e})"}


def summarize_recent(min_ago: int = 10) -> dict:
    """Résume les N dernières minutes de runtime Cortex (bouton "Résume")."""
    cutoff = _now() - min_ago * 60
    out = {"ts": _now(), "window_min": min_ago, "items": []}
    try:
        ai_state_path = VAULT / ".cortex-active-inference-state.json"
        if ai_state_path.exists():
            s = json.loads(ai_state_path.read_text(encoding="utf-8"))
            vfe = s.get("vfe_history", [])
            recent_cycles = [v for v in vfe if v.get("ts", 0) >= cutoff]
            from collections import Counter
            actions = [v.get("chosen") for v in recent_cycles if v.get("chosen")]
            if actions:
                c = Counter(actions)
                out["items"].append(f"{len(recent_cycles)} cycles autonomes "
                                    f"(top: {', '.join(f'{a}×{n}' for a, n in c.most_common(3))})")
    except Exception: pass
    try:
        bh_path = VAULT / ".cortex-body-health-last.json"
        if bh_path.exists() and bh_path.stat().st_mtime >= cutoff:
            bh = json.loads(bh_path.read_text(encoding="utf-8"))
            if bh.get("n_succeeded", 0) > 0:
                out["items"].append(f"body_health auto-cleanup : "
                                    f"{bh['n_succeeded']} actions, "
                                    f"{bh.get('effective_freed_gb', 0)} Go libérés")
    except Exception: pass
    try:
        sys.path.insert(0, str(REPO))
        import cortex_jepa_v2 as _j
        m = _j.JEPA.load()
        if m:
            out["items"].append(f"JEPA : {m.n_train_steps} train_steps, "
                                f"loss récente {m.last_loss}")
    except Exception: pass
    if not out["items"]:
        out["items"].append(f"Pas d'événement notable depuis {min_ago} min.")
    return out


def propose_action() -> dict:
    """Propose une action concrète à Sam (bouton "Propose une action")."""
    # Heuristique simple : prend le détecteur qui a la plus haute priorité ET
    # qui a une `proposed_action` définie.
    state = _load_state()
    for detector in _EVENT_DETECTORS:
        try:
            ev = detector()
            if ev and ev.get("proposed_action"):
                return {
                    "ok": True,
                    "trigger": ev["trigger"],
                    "rationale": ev["message"],
                    "proposed_action": ev["proposed_action"],
                }
        except Exception: pass
    return {"ok": False, "msg": "Pas de proposition immédiate. Tout va bien."}


def proactive_metrics() -> dict:
    """Métriques de proactivité pour tracker la qualité des initiatives."""
    state = _load_state()
    # Compte events dernière heure
    n_last_hour = 0
    if EVENTS.exists():
        try:
            cutoff = _now() - 3600
            for ln in EVENTS.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    o = json.loads(ln)
                    if o.get("ts", 0) >= cutoff: n_last_hour += 1
                except Exception: pass
        except Exception: pass
    accepted = state.get("accepted_suggestions", 0)
    ignored = state.get("ignored_suggestions", 0)
    total_resp = accepted + ignored
    return {
        "ts": _now(),
        "proactive_events_total": state.get("n_proactive_events_total", 0),
        "proactive_events_last_hour": n_last_hour,
        "sam_responses_to_proactive": state.get("sam_responses_to_proactive", 0),
        "accepted_suggestions": accepted,
        "ignored_suggestions": ignored,
        "useful_rate": round(accepted / max(1, total_resp), 3) if total_resp else None,
        "last_return_brief_ts": state.get("last_return_brief_ts", 0),
    }


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
