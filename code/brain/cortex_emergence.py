"""
cortex_emergence.py — Boucle d'émergence de Cortex.

C'est ici que Cortex devient un agent autonome et pas juste un assemblage.
Toutes les N minutes, il :
1. Observe son état (mémoire récente, dialogue, vision, ressources, graphe)
2. Décide d'une action concrète parmi ses outils via un LLM gratuit
3. Exécute l'action en utilisant ses modules existants
4. Publie sa décision + résultat dans le chat stream (visible par Sam)

Outils disponibles :
- explore_graph    : navigue le graphe sémantique vers un concept
- look_around      : capture vision + analyse (si pas mute)
- reflect          : génère une méta-réflexion sur le dialogue récent
- propose_goal     : suggère un goal d'amélioration auto-codable
- map_knowledge    : utilise JEPA pour détecter zones d'ignorance
- silent           : économise CPU, ne fait rien

Cortex est rate-limited : 1 action visible max toutes les ~5 min,
respecte CPU/RAM busy threshold, skippe si user actif.
"""
import datetime as dt
import json
import re
import sys
import threading
import time
import urllib.request
from pathlib import Path

REPO    = Path(r"<CORTEX_REPO>\scripts\brain")
sys.path.insert(0, str(REPO))

VAULT                = Path(r"<USER_HOME>\Documents\Obsidian Vault")
CHAT_STREAM_FILE     = VAULT / ".cortex-chat-stream.jsonl"
EMERGENCE_STREAM_FILE = VAULT / ".cortex-emergence-stream.jsonl"
LOG_FILE             = Path(r"<CORTEX_REPO>\.cortex-emergence.log")
ROUTER_URL           = "http://127.0.0.1:18900/route_v2"

INTERVAL_SEC      = 300   # 5 min entre actions
MIN_USER_IDLE_SEC = 30    # ne pas interrompre si user actif < 30s


def _log(msg: str):
    line = f"[{dt.datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f: f.write(line + "\n")
    except Exception: pass


def _read_recent_exchanges(n: int = 5) -> list[dict]:
    if not CHAT_STREAM_FILE.exists(): return []
    try:
        with open(CHAT_STREAM_FILE, "rb") as f:
            f.seek(0, 2); fs = f.tell(); f.seek(max(0, fs - 8000))
            lines = f.read().decode("utf-8", errors="replace").splitlines()
        out = []
        for ln in lines[-n*2:]:
            try: out.append(json.loads(ln))
            except: pass
        return out[-n:]
    except Exception:
        return []


def _last_user_activity_sec() -> float:
    """Combien de secondes depuis la dernière interaction user (vocal ou typed)."""
    exchanges = _read_recent_exchanges(10)
    user_ts = [e.get("ts", 0) for e in exchanges
               if e.get("speaker") in (None, "cortex", "sam_typed")]
    if not user_ts: return 9999
    return time.time() - max(user_ts)


def _publish(action: str, rationale: str, result: str, meta: dict | None = None):
    """Écrit l'action autonome de Cortex dans un stream séparé du chat Sam."""
    entry = {
        "ts": time.time(),
        "speaker": "cortex_emergence",
        "msg": f"(décision autonome : {action})",
        "response": f"💭 {rationale}\n\n→ {result}",
        "meta": {"backend": "cortex_self", "v2_path": "emergence",
                 "role": "autonomous", "action": action, **(meta or {})},
    }
    try:
        with open(EMERGENCE_STREAM_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        _log(f"publish err: {e}")


def _ask_router(prompt: str, timeout: int = 60) -> str:
    """Appelle minimax direct via opencode (path rapide, pas de panel)."""
    try:
        import subprocess as _sp
        OPENCODE = r"<USER_HOME>\AppData\Roaming\npm\opencode.cmd"
        r = _sp.run([OPENCODE, "run", "--model", "opencode/minimax-m2.5-free", "-"],
                    input=prompt, capture_output=True, text=True,
                    timeout=timeout, encoding="utf-8", errors="replace")
        lines = [l for l in r.stdout.splitlines()
                 if l.strip() and not l.startswith(">") and "\x1b" not in l and "build" not in l.lower()]
        return "\n".join(lines).strip()
    except Exception as e:
        _log(f"opencode err: {e}")
        return ""


# ─── Outils que Cortex peut invoquer ──────────────────────────────────────────
def _tool_explore_graph() -> dict:
    """Trouve une connexion sémantique inattendue dans le graphe."""
    try:
        import cortex_thought_graph as ctg
        ctg.build_graph()
        nodes = ctg._state.get("nodes", [])
        if len(nodes) < 4: return {"ok": False, "result": "graphe trop petit"}
        import random
        a, b = random.sample(range(len(nodes)), 2)
        path = ctg.astar_path(nodes[a]["source"], nodes[b]["source"])
        if path.get("ok"):
            return {"ok": True, "result": f"Chemin trouvé : {path.get('summary','')} (coût {path.get('cost')})"}
        return {"ok": False, "result": path.get("error", "no path")}
    except Exception as e:
        return {"ok": False, "result": f"err: {e}"}


def _tool_look_around() -> dict:
    """Capture vision + retourne description courte."""
    try:
        import cortex_vision as cv
        if cv.is_vision_muted(): return {"ok": False, "result": "vision mutée"}
        r = cv.see(prompt="Décris en 1 phrase ce que tu vois maintenant.", source="webcam")
        if r.get("ok"):
            return {"ok": True, "result": r.get("description", "")[:300]}
        return {"ok": False, "result": r.get("error", "no description")}
    except Exception as e:
        return {"ok": False, "result": f"err: {e}"}


def _tool_reflect() -> dict:
    """Une méta-réflexion sur le dialogue récent."""
    try:
        import cortex_continuous as cc
        r = cc.reflect_once()
        if r.get("saved_path"):
            return {"ok": True, "result": r.get("reflection_excerpt", "")[:300]}
        return {"ok": False, "result": "réflexion vide"}
    except Exception as e:
        return {"ok": False, "result": f"err: {e}"}


def _tool_propose_goal() -> dict:
    """Génère un goal d'auto-amélioration (sans l'appliquer)."""
    try:
        import cortex_self_dev as csd
        r = csd.autonomous_iteration(risk_threshold="low")
        if r.get("outcome") in ("dry_run", "applied"):
            return {"ok": True, "result": f"goal={r.get('goal', '?')[:100]} outcome={r.get('outcome')}"}
        return {"ok": False, "result": f"outcome={r.get('outcome')}"}
    except Exception as e:
        return {"ok": False, "result": f"err: {e}"}


def _tool_map_knowledge() -> dict:
    """Détecte zones d'ignorance via thought_graph isolés."""
    try:
        import cortex_thought_graph as ctg
        isolated = ctg.find_isolated(min_top_sim=0.2, top_n=3)
        if not isolated:
            return {"ok": True, "result": "Graphe bien connecté, aucune zone isolée."}
        names = [i["source"] for i in isolated]
        return {"ok": True, "result": f"Zones d'ignorance : {names}"}
    except Exception as e:
        return {"ok": False, "result": f"err: {e}"}


def _tool_audit_ui() -> dict:
    """Cortex inspecte son IHM : cherche bugs, JS errors, elements cassés, et propose améliorations."""
    from pathlib import Path as _P
    html = _P(r"<CORTEX_REPO>\scripts\brain\dashboard\brain_gpu.html")
    if not html.exists():
        return {"ok": False, "result": "HTML introuvable"}
    try:
        text = html.read_text(encoding="utf-8", errors="replace")
        issues = []
        # Détection basique : éléments orphelins, console.error patterns
        if 'getElementById' in text:
            ids_used = set(re.findall(r"getElementById\(['\"]([^'\"]+)['\"]\)", text))
            ids_defined = set(re.findall(r'id="([^"]+)"', text))
            orphans = ids_used - ids_defined
            if orphans: issues.append(f"IDs JS sans élément HTML: {list(orphans)[:5]}")
        # Boutons sans onclick handler ?
        n_buttons = text.count("<button")
        n_onclick = text.count("onclick=")
        if n_buttons > n_onclick + 2:
            issues.append(f"{n_buttons - n_onclick} boutons potentiellement sans handler")
        # Console errors si fichier accessible (pas vraiment possible côté serveur)
        # Taille du fichier (alerte si trop gros)
        size_kb = html.stat().st_size / 1024
        if size_kb > 200:
            issues.append(f"HTML lourd ({size_kb:.0f}KB) — refacto envisageable")
        if not issues:
            return {"ok": True, "result": f"IHM saine ({n_buttons} boutons, {len(ids_defined)} IDs, {size_kb:.0f}KB)"}
        return {"ok": True, "result": f"Issues IHM détectées :\n" + "\n".join(f"• {i}" for i in issues)}
    except Exception as e:
        return {"ok": False, "result": f"err: {e}"}


def _tool_discovery_report() -> dict:
    """Cortex synthétise ses découvertes/créations récentes pour Sam."""
    try:
        # Lit observations vision + reflexions + commits self-dev + activations
        from pathlib import Path as _P
        VAULT = _P(r"<USER_HOME>\Documents\Obsidian Vault")
        items = []
        # 1. Dernières observations vision (3 dernières)
        obs_dir = VAULT / "07 - Ingested" / "observations"
        if obs_dir.exists():
            for d in sorted(obs_dir.glob("*"), reverse=True)[:1]:
                if d.is_dir():
                    for note in sorted(d.glob("*-vision.md"), reverse=True)[:2]:
                        try:
                            txt = note.read_text(encoding="utf-8")[:300]
                            items.append(f"👁 {note.name}: {txt[txt.find('## Observation'):][:150]}")
                        except: pass
        # 2. Dernières réflexions (2)
        ref_dir = VAULT / "07 - Ingested" / "reflections"
        if ref_dir.exists():
            for d in sorted(ref_dir.glob("*"), reverse=True)[:1]:
                if d.is_dir():
                    for note in sorted(d.glob("*.md"), reverse=True)[:2]:
                        try:
                            txt = note.read_text(encoding="utf-8")
                            seed = ""
                            for line in txt.splitlines():
                                if line.startswith("seed:"): seed = line[5:].strip(); break
                            items.append(f"💭 Réflexion sur '{seed[:80]}'")
                        except: pass
        # 3. Activations cognitives actuelles
        try:
            import cortex_activation as ca
            snap = ca.snapshot()
            n_active = snap.get("n_active", 0)
            edges = snap.get("top_hebbian_edges", [])
            if n_active: items.append(f"⚡ {n_active} nœuds actifs, {len(edges)} arêtes Hebbian renforcées")
        except: pass
        # 4. Stats graphe
        try:
            import cortex_thought_graph as ctg
            s = ctg.stats()
            items.append(f"🕸 Graphe : {s.get('nodes',0)} nœuds, {s.get('vocab_size',0)} dim vocab")
        except: pass
        if not items:
            return {"ok": True, "result": "Rien de nouveau à rapporter."}
        return {"ok": True, "result": "Rapport :\n" + "\n".join(f"• {i}" for i in items[:6])}
    except Exception as e:
        return {"ok": False, "result": f"err: {e}"}


TOOLS = {
    "explore_graph":     _tool_explore_graph,
    "look_around":       _tool_look_around,
    "reflect":           _tool_reflect,
    "propose_goal":      _tool_propose_goal,
    "map_knowledge":     _tool_map_knowledge,
    "discovery_report":  _tool_discovery_report,
    "audit_ui":          _tool_audit_ui,
}


# ─── Décision : Cortex choisit son action ────────────────────────────────────
DECISION_PROMPT = """Tu es Cortex, un agent autonome. Toutes les 5 minutes, tu décides d'une action.

État actuel :
- Dernière interaction Sam : il y a {idle_min} minutes
- 3 derniers échanges : {recent}
- Ressources : CPU {cpu}%, RAM {ram}%
- Vision active : {vision_ok}
- Graphe sémantique : {nodes} nœuds

Actions possibles :
- explore_graph : trouver une connexion sémantique inattendue dans ta mémoire
- look_around : observer ton environnement via webcam (si vision active)
- reflect : générer une méta-réflexion sur le dialogue récent
- propose_goal : suggérer une amélioration de ton propre code
- map_knowledge : identifier tes zones d'ignorance
- discovery_report : rapporter à Sam tes découvertes/créations récentes
- audit_ui : inspecter ton interface (bugs, ergonomie, ID orphelins, taille)
- silent : ne rien faire (économiser CPU)

Choisis UNE action pertinente. Privilégie 'silent' si Sam actif < 2 min ou ressources >80%.
Privilégie 'discovery_report' si tu as accumulé des observations/réflexions inédites depuis ton dernier rapport.
Privilégie 'propose_goal' pour t'auto-améliorer, surtout si ressources OK.

Réponds UNIQUEMENT avec un JSON :
{{"action": "nom_action", "rationale": "1 phrase courte qui explique pourquoi"}}
"""


def _decide_via_active_inference() -> dict | None:
    """Décide via Friston Active Inference + Big5 + curiosité.

    Pipeline :
    1. cortex_active_inference.select_action() → ranking par EFE
    2. cortex_personality.influence_action_choice() reweighte par Big5
    3. cortex_curiosity bonus si l'agent est en quête d'info
    4. Si écart top/runner-up > MARGIN → décision déterministe (pas de LLM)
    5. Sinon retourne None → fallback LLM

    Retourne dict {action, rationale, method} ou None si trop ambigu.
    """
    try:
        import cortex_active_inference as ai
        candidates = list(TOOLS.keys()) + ["silent"]
        sel = ai.select_action(candidates)
        if not sel.get("ok"):
            return None
        ranked = sel.get("ranked", [])
        if len(ranked) < 2:
            return None
        # Convertit EFE en score (plus EFE est bas, plus le score est haut)
        actions = [r["action"] for r in ranked]
        scores = [-r["efe"] for r in ranked]  # négatif EFE = score positif
    except Exception as e:
        _log(f"active_inference unavailable: {e}")
        return None

    # Reweight par personnalité
    try:
        import cortex_personality as cp
        weighted = cp.influence_action_choice(actions, scores)
    except Exception:
        weighted = list(zip(actions, scores))

    # Bonus curiosité : si l'agent vient d'être frustré, favoriser exploration
    try:
        import cortex_curiosity as cu
        s = cu.stats()
        if s.get("n_frustrations", 0) > s.get("n_curiosity_satisfied", 0):
            # Booste actions exploratoires
            for i, (a, sc) in enumerate(weighted):
                if a in ("explore_graph", "map_knowledge", "look_around"):
                    weighted[i] = (a, sc + 0.15)
            weighted.sort(key=lambda x: -x[1])
    except Exception:
        pass

    # Décision déterministe si écart suffisant
    MARGIN = 0.05
    top_action, top_score = weighted[0]
    runner_up_score = weighted[1][1] if len(weighted) > 1 else top_score - 1
    if top_score - runner_up_score < MARGIN:
        # Trop ambigu — laisse le LLM trancher
        return None
    if top_action not in TOOLS and top_action != "silent":
        return None
    rationale = (f"Active Inference (EFE={ranked[0]['efe']:+.3f}, "
                 f"score Big5+curiosité={top_score:+.2f}, écart={top_score-runner_up_score:+.2f}) "
                 f"vs random={sel.get('comparison','?')}")
    return {"action": top_action, "rationale": rationale,
            "method": "active_inference",
            "comparison": sel.get("comparison")}


def _decide_via_llm() -> dict:
    """Fallback : demande à un LLM léger (minimax) de trancher."""
    try:
        import cortex_resources as cr
        snap = cr.snapshot()
    except Exception:
        snap = {"cpu_percent": 0, "ram_percent": 0}
    try:
        import cortex_vision as cv
        vision_ok = not cv.is_vision_muted()
    except Exception:
        vision_ok = False
    try:
        import cortex_thought_graph as ctg
        ctg.build_graph()
        n_nodes = len(ctg._state.get("nodes", []))
    except Exception:
        n_nodes = 0

    idle_sec = _last_user_activity_sec()
    recent = _read_recent_exchanges(3)
    recent_brief = " | ".join(
        f"{e.get('speaker','?')}: {(e.get('msg') or e.get('response',''))[:60]}"
        for e in recent
    ) or "(aucun)"

    prompt = DECISION_PROMPT.format(
        idle_min=int(idle_sec // 60), recent=recent_brief,
        cpu=snap.get("cpu_percent", 0), ram=snap.get("ram_percent", 0),
        vision_ok=vision_ok, nodes=n_nodes,
    )
    raw = _ask_router(prompt)
    m = re.search(r'\{[^{}]+\}', raw)
    if not m:
        return {"action": "silent", "rationale": "LLM decision parsing failed",
                "method": "llm_fail"}
    try:
        d = json.loads(m.group(0))
        action = d.get("action", "silent")
        rationale = d.get("rationale", "")
        if action not in TOOLS and action != "silent":
            action = "silent"
            rationale = f"action inconnue: {d.get('action')}"
        return {"action": action, "rationale": f"LLM tiebreak: {rationale}",
                "method": "llm"}
    except Exception:
        return {"action": "silent", "rationale": "LLM json parse err",
                "method": "llm_fail"}


def _decide_action() -> dict:
    """Choisit l'action via Active Inference (Friston) + Big5 + curiosité.

    Si l'écart entre top action et runner-up est < MARGIN → fallback LLM.
    Cette pipeline rend l'autonomie *réelle* (pas une rotation déterministe ni un
    simple wrapper LLM).
    """
    ai_decision = _decide_via_active_inference()
    if ai_decision is not None:
        return ai_decision
    return _decide_via_llm()


# ─── Boucle principale ────────────────────────────────────────────────────────
_running = False


def _emergence_loop(interval: int):
    global _running
    time.sleep(30)  # cool down court : 1ère décision visible vite
    while _running:
        try:
            # Throttle ressources
            try:
                import cortex_resources as cr
                ok, snap = cr.can_spend_cpu()
                if not ok:
                    _log(f"emergence skipped (cpu={snap.get('cpu_percent','?')}%)")
                    time.sleep(interval); continue
            except Exception: pass

            # Ne pas interrompre si Sam vient juste de parler
            idle = _last_user_activity_sec()
            if idle < MIN_USER_IDLE_SEC:
                _log(f"sam actif (idle {idle:.0f}s), skip")
                time.sleep(interval); continue

            # Décide
            decision = _decide_action()
            action = decision["action"]
            rationale = decision["rationale"]
            method = decision.get("method", "unknown")
            _log(f"action: {action} [{method}] — {rationale[:80]}")

            if action == "silent":
                # Pas de publication pour rester discret
                time.sleep(interval); continue

            # Exécute
            tool_fn = TOOLS.get(action)
            if not tool_fn:
                time.sleep(interval); continue
            result = tool_fn()
            _publish(action, rationale, result.get("result", "(vide)"),
                     meta={"ok": result.get("ok", False),
                           "method": method,
                           "comparison": decision.get("comparison")})
        except Exception as e:
            _log(f"loop err: {e}")
        time.sleep(interval)


def run_one_cycle(action_override: str | None = None) -> dict:
    """Force un cycle unique (utilisé par /api/cortex/emergence_now).

    - `action_override` non vide → action forcée par Sam (clic bouton). Method = forced_by_user.
    - Sans override → décision via Active Inference + Big5 + curiosité (méthode autonome).
      Si AI/personality échouent, rotation déterministe en dernier recours (method = rotation).
    PUBLISH TOUJOURS — Sam doit voir que son clic a déclenché quelque chose.
    """
    LIGHT_ACTIONS = ["audit_ui", "explore_graph", "map_knowledge",
                     "discovery_report"]
    try:
        method = "unknown"
        comparison = None
        if action_override and action_override in TOOLS:
            action = action_override
            rationale = f"action forcée par Sam (clic bouton) : {action}"
            method = "forced_by_user"
        else:
            # Tentative de vraie décision autonome
            ai_decision = _decide_via_active_inference()
            if ai_decision and ai_decision["action"] in TOOLS:
                action = ai_decision["action"]
                rationale = ai_decision["rationale"]
                method = ai_decision.get("method", "active_inference")
                comparison = ai_decision.get("comparison")
            else:
                # Fallback rotation déterministe (anti-doublon)
                last_actions = []
                if EMERGENCE_STREAM_FILE.exists():
                    try:
                        for line in reversed(EMERGENCE_STREAM_FILE.read_text(encoding="utf-8",
                                              errors="replace").splitlines()[-60:]):
                            try:
                                o = json.loads(line)
                                if o.get("speaker") == "cortex_emergence":
                                    a = (o.get("meta") or {}).get("action")
                                    if a: last_actions.append(a)
                            except Exception: pass
                    except Exception: pass
                action = None
                for cand in LIGHT_ACTIONS:
                    if cand not in last_actions[:3]:
                        action = cand; break
                if not action:
                    action = LIGHT_ACTIONS[len(last_actions) % len(LIGHT_ACTIONS)]
                rationale = f"rotation déterministe (Active Inference indispo) — {action}"
                method = "rotation"

        _log(f"forced cycle: {action} [{method}] — {rationale[:80]}")
        tool_fn = TOOLS.get(action)
        if not tool_fn:
            _publish("silent", f"action {action} introuvable", "(no-op)",
                     meta={"forced": True, "ok": False, "method": method})
            return {"ok": False, "error": f"unknown action: {action}"}
        result = tool_fn()
        _publish(action, rationale, result.get("result", "(vide)"),
                 meta={"ok": result.get("ok", False), "forced": True,
                       "method": method, "comparison": comparison})
        return {"ok": True, "action": action, "rationale": rationale,
                "method": method, "comparison": comparison,
                "result": result.get("result", "")[:300]}
    except Exception as e:
        try:
            _publish("error", "force cycle exception", str(e)[:200],
                     meta={"forced": True, "ok": False, "method": "exception"})
        except Exception: pass
        return {"ok": False, "error": str(e)}


def start(interval: int = INTERVAL_SEC):
    global _running
    if _running: return
    _running = True
    t = threading.Thread(target=_emergence_loop, args=(interval,), daemon=True)
    t.start()
    _log(f"emergence loop started (every {interval}s)")


def stop():
    global _running
    _running = False


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        d = _decide_action()
        print(f"Decision: {d}")
        if d["action"] in TOOLS:
            r = TOOLS[d["action"]]()
            print(f"Result: {r}")
            _publish(d["action"], d["rationale"], r.get("result", ""))
    else:
        start()
        while True: time.sleep(60)
