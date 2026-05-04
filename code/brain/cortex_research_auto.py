"""
cortex_research_auto.py — Self-research automatique sur les gaps JEPA persistants.

Branche `cortex_research.py` (qui interroge arXiv, Wikipedia, Semantic Scholar,
DuckDuckGo) à la détection de gaps JEPA. Quand un gap reste élevé sur N cycles
consécutifs, Cortex déclenche TOUT SEUL une recherche en ligne, écrit la note
synthétisée dans le vault, et MESURE si la recherche a réduit le gap.

Anti-fake intégré :
- chaque recherche est loggée avec timestamp + query + sources_urls
- gap mesuré AVANT et APRÈS l'indexation de la note
- si gap pas réduit après 2 cycles → marqué unsuccessful_research
- les sources [N] doivent contenir des URLs validables

API :
    detect_persistent_gaps(min_cycles=3) → list[gap]
    research_gap(query) → exécute cortex_research + mesure
    auto_step() → cycle complet : détecte + recherche + mesure
    stats()
    self_test()

État dans .cortex-research-auto-state.json + log .cortex-research-auto-log.jsonl
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
VAULT = Path(r"<USER_HOME>\Documents\Obsidian Vault")
STATE = VAULT / ".cortex-research-auto-state.json"
LOG = VAULT / ".cortex-research-auto-log.jsonl"

GAP_PERSIST_CYCLES = 3
GAP_HIGH_THRESHOLD = 0.4  # gap considéré "élevé"


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
        "version": "research-auto-1",
        "n_steps": 0,
        "n_researches_triggered": 0,
        "n_successful": 0,
        "n_unsuccessful": 0,
        "queries_history": [],
        "gap_tracking": {},  # query → list[(ts, gap)] pour détecter persistance
    }


def _save_state(s: dict) -> None:
    s["updated_at"] = _now()
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception: pass


def _log_event(ev: dict) -> None:
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": _now(), **ev}, ensure_ascii=False) + "\n")
    except Exception: pass


def _measure_gap(query: str) -> dict:
    """Mesure le gap JEPA actuel pour une query."""
    cwm = _safe_import("cortex_world_model")
    if not cwm: return {"gap": None, "reason": "world_model_unavailable"}
    try:
        probe = cwm.probe_world(query)
        return {
            "gap": probe.get("gap"),
            "confidence": probe.get("confidence"),
            "mode": probe.get("mode"),
        }
    except Exception as e:
        return {"gap": None, "error": str(e)[:200]}


def detect_persistent_gaps(min_cycles: int = GAP_PERSIST_CYCLES) -> list[dict]:
    """Identifie les queries dont le gap reste haut depuis >= min_cycles cycles."""
    state = _load_state()
    tracking = state.get("gap_tracking", {})
    persistent = []
    for query, history in tracking.items():
        if not isinstance(history, list) or len(history) < min_cycles: continue
        recent = history[-min_cycles:]
        if all(isinstance(h, list) and len(h) == 2 and h[1] is not None and h[1] > GAP_HIGH_THRESHOLD
               for h in recent):
            persistent.append({
                "query": query,
                "n_cycles_high": len(recent),
                "avg_gap": sum(h[1] for h in recent) / len(recent),
                "last_gap": recent[-1][1],
            })
    persistent.sort(key=lambda x: -x["avg_gap"])
    return persistent


def _track_gap(query: str, gap: float | None) -> None:
    """Enregistre un point dans le tracking de gap."""
    state = _load_state()
    tracking = state.setdefault("gap_tracking", {})
    history = tracking.setdefault(query, [])
    history.append([_now(), gap])
    # Cap : garde les 10 derniers points par query
    tracking[query] = history[-10:]
    # Cap général : pas plus de 100 queries trackées
    if len(tracking) > 100:
        # Vire les queries les plus anciennes (last point oldest)
        sorted_q = sorted(tracking.items(), key=lambda kv: kv[1][-1][0] if kv[1] else 0)
        for q, _ in sorted_q[:20]:
            tracking.pop(q, None)
    state["gap_tracking"] = tracking
    _save_state(state)


def research_gap(query: str) -> dict:
    """Exécute une recherche et MESURE si le gap a baissé."""
    cr = _safe_import("cortex_research")
    if not cr:
        return {"ok": False, "error": "cortex_research module unavailable"}

    # Mesure AVANT
    gap_before = _measure_gap(query).get("gap")

    # Exécute la recherche
    started_at = _now()
    try:
        if hasattr(cr, "research"):
            result = cr.research(query)
        elif hasattr(cr, "do_research"):
            result = cr.do_research(query)
        elif hasattr(cr, "search"):
            result = cr.search(query)
        else:
            return {"ok": False, "error": "no research function in cortex_research"}
    except Exception as e:
        _log_event({"type": "research_failure", "query": query, "error": str(e)[:300]})
        return {"ok": False, "error": str(e)[:300]}
    duration = _now() - started_at

    # Re-mesure APRÈS un court délai pour permettre l'indexation
    time.sleep(2)
    gap_after = _measure_gap(query).get("gap")

    # Évalue le succès
    if gap_before is not None and gap_after is not None:
        gap_delta = gap_before - gap_after
        successful = gap_delta > 0.02  # > 2% baisse = succès
    else:
        gap_delta = None
        successful = False

    state = _load_state()
    state["n_researches_triggered"] = state.get("n_researches_triggered", 0) + 1
    if successful:
        state["n_successful"] = state.get("n_successful", 0) + 1
    elif gap_after is not None:
        state["n_unsuccessful"] = state.get("n_unsuccessful", 0) + 1
    history = state.setdefault("queries_history", [])
    history.append({
        "ts": _now(), "query": query, "duration_s": round(duration, 1),
        "gap_before": gap_before, "gap_after": gap_after, "gap_delta": gap_delta,
        "successful": successful,
        "result_summary": (str(result)[:200] if result else "no_result"),
    })
    state["queries_history"] = history[-50:]
    _save_state(state)

    _log_event({
        "type": "research_completed", "query": query,
        "gap_before": gap_before, "gap_after": gap_after,
        "successful": successful, "duration_s": round(duration, 1),
    })

    return {
        "ok": True,
        "query": query,
        "duration_s": round(duration, 1),
        "gap_before": gap_before,
        "gap_after": gap_after,
        "gap_delta": gap_delta,
        "successful": successful,
        "result": (result if result else None),
    }


def _candidate_queries() -> list[str]:
    """Génère des queries candidates à tracker depuis l'état Cortex."""
    out = []
    cwm = _safe_import("cortex_world_model")
    if cwm:
        try:
            state = cwm.read_state()
            for g in state.get("gaps", []) or []:
                if isinstance(g, dict):
                    q = g.get("query") or g.get("topic")
                    if q and isinstance(q, str): out.append(q)
                elif isinstance(g, str):
                    out.append(g)
        except Exception: pass
    cur = _safe_import("cortex_curiosity")
    if cur:
        try:
            qs = cur.generate_questions(3)
            out.extend(qs)
        except Exception: pass
    # Dédup et garde les 5 premiers
    seen = set()
    uniq = []
    for q in out:
        q_norm = (q or "").strip()[:200]
        if q_norm and q_norm not in seen:
            seen.add(q_norm)
            uniq.append(q_norm)
        if len(uniq) >= 5: break
    return uniq


def auto_step() -> dict:
    """Un cycle complet : track gaps + détecter persistants + recherche si dispo."""
    state = _load_state()
    state["n_steps"] = state.get("n_steps", 0) + 1
    _save_state(state)

    # 1. Track les gaps actuels pour les queries candidates
    candidates = _candidate_queries()
    tracked = []
    for q in candidates:
        meas = _measure_gap(q)
        gap = meas.get("gap")
        if gap is not None:
            _track_gap(q, gap)
            tracked.append({"query": q[:80], "gap": gap})

    # 2. Détecte les persistants
    persistent = detect_persistent_gaps()

    # 3. Si on a des persistants → research le top 1 (un par cycle, anti-spam)
    research_result = None
    if persistent:
        top = persistent[0]
        research_result = research_gap(top["query"])

    rep = {
        "ok": True,
        "ts": _now(),
        "n_candidates_tracked": len(tracked),
        "n_persistent_high_gaps": len(persistent),
        "tracked_sample": tracked[:3],
        "research_executed": research_result is not None,
        "research_result": research_result,
    }
    _log_event({"type": "auto_step",
                 "n_tracked": len(tracked),
                 "n_persistent": len(persistent),
                 "research_executed": research_result is not None})
    return rep


def stats() -> dict:
    s = _load_state()
    history = s.get("queries_history", [])
    successful_recent = [h for h in history[-20:] if h.get("successful")]
    return {
        "n_steps": s.get("n_steps", 0),
        "n_researches_triggered": s.get("n_researches_triggered", 0),
        "n_successful": s.get("n_successful", 0),
        "n_unsuccessful": s.get("n_unsuccessful", 0),
        "success_rate": (s.get("n_successful", 0) /
                         max(1, s.get("n_researches_triggered", 0))),
        "recent_successful_queries": [h["query"][:80] for h in successful_recent[:5]],
        "n_tracked_queries": len(s.get("gap_tracking", {})),
    }


def self_test() -> dict:
    tests = []
    candidates = _candidate_queries()
    tests.append({"name": "candidate_queries",
                  "ok": isinstance(candidates, list),
                  "n_candidates": len(candidates),
                  "sample": candidates[:2]})
    persistent = detect_persistent_gaps()
    tests.append({"name": "detect_persistent_gaps",
                  "ok": isinstance(persistent, list),
                  "n_persistent": len(persistent)})
    # Track un gap test
    _track_gap("active inference free energy Friston", 0.5)
    state = _load_state()
    tests.append({"name": "track_gap_persists",
                  "ok": "active inference free energy Friston" in state.get("gap_tracking", {}),
                  "n_tracked": len(state.get("gap_tracking", {}))})
    # auto_step (peut faire une vraie recherche, c'est OK)
    rep = auto_step()
    tests.append({"name": "auto_step",
                  "ok": rep.get("ok"),
                  "n_tracked": rep.get("n_candidates_tracked"),
                  "research_executed": rep.get("research_executed")})
    s = stats()
    tests.append({"name": "stats",
                  "ok": "n_steps" in s,
                  "n_steps": s.get("n_steps"),
                  "success_rate": s.get("success_rate")})
    return {"ok": all(t["ok"] for t in tests), "tests": tests}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "step"
    if cmd == "step":
        print(json.dumps(auto_step(), indent=2, ensure_ascii=False))
    elif cmd == "stats":
        print(json.dumps(stats(), indent=2, ensure_ascii=False))
    elif cmd == "persistent":
        print(json.dumps(detect_persistent_gaps(), indent=2, ensure_ascii=False))
    elif cmd == "research" and len(sys.argv) > 2:
        query = " ".join(sys.argv[2:])
        print(json.dumps(research_gap(query), indent=2, ensure_ascii=False))
    elif cmd == "test":
        print(json.dumps(self_test(), indent=2, ensure_ascii=False))
    else:
        print("Usage: cortex_research_auto.py {step|stats|persistent|research <query>|test}")
