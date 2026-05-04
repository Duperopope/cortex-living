"""
cortex_brain_history.py — Historique d'évolution du cerveau de Cortex + détection régressions.

Métriques cérébrales suivies à chaque snapshot (toutes les 10 min) :
- n_nodes, n_edges (taille du graphe)
- n_isolated (zones d'ignorance — top_sim < 0.15)
- n_active (Spreading Activation actif > floor)
- hebbian_total (somme strength edges renforcées — apprentissage cumulé)
- density = 2*E / (N*(N-1)) — connectivité
- by_kind (claude_memory / semantic / episodic) — diversité

Régressions détectées (signal négatif si > 8 % vs moyenne 24 h) :
- nodes_drop : on a perdu des notes (fichiers supprimés / corrompus)
- density_drop : graphe moins connecté
- hebbian_drop : oublis plus rapides que renforcement (rare car HEBBIAN_LR
  ne décroît pas, mais possible si le state file est wipé)
- isolation_rise : plus de zones isolées (besoin de cortex_bridge)

Stocke en JSONL append-only — pas de factice, chaque ligne = 1 mesure réelle
horodatée. Pour visualiser une croissance sur 24 h, on lit les N derniers snapshots.

Citations :
- Hebb (1949) — la trace neuronale est cumulative, pas oubliée
- Tononi (2008) "Consciousness as Integrated Information" — la complexité
  d'un graphe reflète la richesse cognitive
"""
import datetime as dt
import json
import time
from pathlib import Path

HISTORY_FILE = Path(r"<CORTEX_REPO>\scripts\brain\.cortex-brain-history.jsonl")
HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)

REGRESSION_THRESHOLD = 0.08  # 8 % en dessous moyenne 24 h => alert
LOOKBACK_HOURS       = 24


def take_snapshot() -> dict:
    """Calcule les métriques cérébrales courantes."""
    snap = {"ts": time.time(), "iso": dt.datetime.now().isoformat(timespec="seconds")}
    # 1. Graphe
    try:
        import sys
        sys.path.insert(0, r"<CORTEX_REPO>\scripts\brain")
        import cortex_thought_graph as ctg
        ctg.build_graph()
        nodes = ctg._state.get("nodes", []) or []
        snap["n_nodes"] = len(nodes)
        # by_kind
        by_kind = {}
        for n in nodes:
            by_kind[n.get("kind", "?")] = by_kind.get(n.get("kind", "?"), 0) + 1
        snap["by_kind"] = by_kind
        # density via cosine sims (>0.15 = edge)
        if len(nodes) >= 2:
            from sklearn.metrics.pairwise import cosine_similarity
            import numpy as np
            sims = cosine_similarity(ctg._state["vectors"])
            np.fill_diagonal(sims, 0)
            n_edges = int((sims > 0.15).sum() // 2)
            n_iso   = int(((sims > 0.15).sum(axis=1) == 0).sum())
            snap["n_edges"]    = n_edges
            snap["n_isolated"] = n_iso
            n = len(nodes)
            snap["density"] = round(2 * n_edges / (n * (n - 1)), 4) if n > 1 else 0
        else:
            snap["n_edges"] = 0; snap["n_isolated"] = 0; snap["density"] = 0
    except Exception as e:
        snap["graph_error"] = str(e)[:120]
    # 2. Activation state
    try:
        import cortex_activation as ca
        s = ca.snapshot()
        snap["n_active"]      = s.get("n_active", 0)
        snap["hebbian_total"] = round(sum(e.get("strength", 0)
                                          for e in s.get("top_hebbian_edges", [])), 4)
    except Exception as e:
        snap["activation_error"] = str(e)[:120]
    return snap


def append_snapshot(snap: dict | None = None) -> dict:
    """Écrit un snapshot en append-only."""
    if snap is None: snap = take_snapshot()
    try:
        with HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(snap, ensure_ascii=False) + "\n")
    except Exception: pass
    return snap


def load_history(hours: float = LOOKBACK_HOURS, limit: int = 500) -> list[dict]:
    """Lit les N derniers snapshots dans la fenêtre demandée."""
    if not HISTORY_FILE.exists(): return []
    cutoff = time.time() - hours * 3600
    out = []
    try:
        for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                d = json.loads(line)
                if d.get("ts", 0) >= cutoff:
                    out.append(d)
            except Exception: pass
    except Exception: pass
    return out


_PROCESS_START_TS = time.time()


def detect_regressions(snap: dict | None = None) -> list[dict]:
    """Compare snap courant à moyenne 24 h. Renvoie liste de régressions.

    Filtre les snapshots cassés (graph_error / activation_error / 0 sentinel) du
    baseline pour ne pas alerter à tort après un restart. La métrique
    `hebbian_total` est volatile en RAM, donc on n'alerte pas si l'uptime du
    process actuel est < 1 h (le store n'a pas eu le temps de se ré-accumuler).
    """
    if snap is None: snap = take_snapshot()
    history_raw = load_history(hours=LOOKBACK_HOURS)
    # Filtre les snapshots cassés du baseline
    healthy = [h for h in history_raw
               if not h.get("graph_error") and not h.get("activation_error")]
    if len(healthy) < 4: return []  # pas assez de data SAINE pour comparer

    def avg(field, only_positive=False):
        vals = [h.get(field) for h in healthy
                if isinstance(h.get(field), (int, float))
                and (h.get(field) > 0 if only_positive else True)]
        return sum(vals) / len(vals) if vals else 0

    uptime_min = (time.time() - _PROCESS_START_TS) / 60.0
    regressions = []
    for field, label in [
        ("n_nodes",       "nodes_drop"),
        ("n_edges",       "edges_drop"),
        ("density",       "density_drop"),
        ("hebbian_total", "hebbian_drop"),
    ]:
        # hebbian_total est volatile RAM : on n'alerte que si le process tourne
        # depuis assez longtemps pour avoir reconstruit l'état d'activation
        if field == "hebbian_total" and uptime_min < 60:
            continue
        # Pour hebbian_total, on exclut aussi les zéros du baseline (sentinel
        # de snapshots où l'activation n'avait pas encore démarré)
        ref  = avg(field, only_positive=(field == "hebbian_total"))
        cur  = snap.get(field, 0)
        if ref > 0 and cur < ref * (1 - REGRESSION_THRESHOLD):
            regressions.append({
                "type": label, "current": cur, "avg_24h": round(ref, 3),
                "delta_pct": round(100 * (cur - ref) / ref, 1),
            })
    # rise = mauvais (plus d'isolés = plus d'ignorance)
    iso_ref = avg("n_isolated")
    iso_cur = snap.get("n_isolated", 0)
    if iso_ref > 0 and iso_cur > iso_ref * (1 + REGRESSION_THRESHOLD):
        regressions.append({
            "type": "isolation_rise", "current": iso_cur,
            "avg_24h": round(iso_ref, 1),
            "delta_pct": round(100 * (iso_cur - iso_ref) / iso_ref, 1),
        })
    return regressions


def evolution_summary() -> dict:
    """Résumé pour l'UI : trend + dernière mesure + régressions actives."""
    history = load_history()
    snap = append_snapshot()  # trace cette mesure
    regs = detect_regressions(snap)
    uptime_s = time.time() - _PROCESS_START_TS
    return {
        "current": snap,
        "history_n": len(history),
        "history": history[-60:],  # 60 derniers points pour sparkline
        "regressions": regs,
        "uptime_s": round(uptime_s, 1),
        "uptime_min": round(uptime_s / 60.0, 1),
        "ok": len(regs) == 0,
    }


# ─── Background loop : snapshot toutes les 10 min ────────────────────────────
import threading

_running = False

def _loop():
    while _running:
        try: append_snapshot()
        except Exception: pass
        time.sleep(600)  # 10 min

def start():
    global _running
    if _running: return
    _running = True
    t = threading.Thread(target=_loop, daemon=True)
    t.start()


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "now"
    if cmd == "now":
        print(json.dumps(append_snapshot(), ensure_ascii=False, indent=2))
    elif cmd == "history":
        print(json.dumps(load_history(), ensure_ascii=False, indent=2)[:5000])
    elif cmd == "summary":
        print(json.dumps(evolution_summary(), ensure_ascii=False, indent=2)[:5000])
    elif cmd == "regressions":
        print(json.dumps(detect_regressions(), ensure_ascii=False, indent=2))
