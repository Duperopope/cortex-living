"""
cortex_activation.py — Activation neurale spreading avec décroissance.

Implémente la Spreading Activation Theory (Collins & Loftus, 1975, Psychological Review)
combinée avec apprentissage Hebbian (Hebb, 1949).

Citations :
- Collins, A.M. & Loftus, E.F. (1975). "A spreading-activation theory of semantic
  processing." Psychological Review, 82(6), 407-428.
  https://doi.org/10.1037/0033-295X.82.6.407
- Hebb, D.O. (1949). "The Organization of Behavior." Wiley & Sons.
- Anderson, J.R. (1983). "A spreading activation theory of memory." Journal of
  Verbal Learning and Verbal Behavior, 22(3), 261-295.

Modèle :
- Chaque nœud (note mémoire) a un niveau d'activation a(t) ∈ [0, 1]
- Quand le système accède un nœud, son activation = max(a, 1.0)
- Décroissance exponentielle : a(t+dt) = a(t) * exp(-dt/τ), τ = 60s par défaut
- Spreading : un nœud activé propage 30% de son activation à ses voisins
  (pondéré par la similarité d'arête)
- Hebbian : si deux nœuds sont activés en même temps,
  l'arête entre eux est renforcée (poids += learning_rate * a_i * a_j)

Usage par Cortex :
- cortex_memory.retrieve_context() appelle activate(node_id) sur chaque retour
- cortex_thought_graph.astar_path() appelle activate sur tous les nœuds du chemin
- L'UI 3D lit les activations courantes via /api/cortex/activations pour
  colorer les nœuds (vert = activé récemment) — cognition rendue visible.
"""
import json
import math
import threading
import time
from pathlib import Path

VAULT       = Path(r"<USER_HOME>\Documents\Obsidian Vault")
STATE_FILE  = VAULT / ".cortex-activations.json"

DECAY_TAU_SEC      = 180.0  # 3 min : un humain garde une pensée vive ~minutes
SPREAD_RATIO       = 0.30   # 30 % de l'activation se propage aux voisins
HEBBIAN_LR         = 0.01   # learning rate des arêtes co-activées
ACTIVATION_FLOOR   = 0.01   # en dessous, on considère 0
WANDER_INTERVAL    = 45.0   # toutes les 45 s, pensée vagabonde si idle (DMN)
WANDER_IDLE_SEC    = 30.0   # ne diverge que si pas d'activité récente
# Paramètres lisibles/modifiables via cortex_activation.config.json
import os
_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "cortex_activation.config.json")
try:
    if os.path.exists(_CONFIG_FILE):
        _cfg = json.load(open(_CONFIG_FILE, "r", encoding="utf-8"))
        DECAY_TAU_SEC   = float(_cfg.get("decay_tau_sec", DECAY_TAU_SEC))
        SPREAD_RATIO    = float(_cfg.get("spread_ratio", SPREAD_RATIO))
        HEBBIAN_LR      = float(_cfg.get("hebbian_lr", HEBBIAN_LR))
        WANDER_INTERVAL = float(_cfg.get("wander_interval_sec", WANDER_INTERVAL))
        WANDER_IDLE_SEC = float(_cfg.get("wander_idle_sec", WANDER_IDLE_SEC))
except Exception: pass


PULSES_FILE = VAULT / ".cortex-pulses.jsonl"
PULSES_RING_MAX = 200  # garder seulement les N derniers événements
PULSES_TTL_SEC  = 15.0  # horizon visuel plus tolérant: l'UI peut rater un polling


class ActivationState:
    """État global d'activation. Thread-safe."""
    def __init__(self):
        self.activations: dict[str, tuple[float, float]] = {}
        # node_id -> (activation_level, last_touch_ts)
        self.edge_strengths: dict[tuple[str, str], float] = {}
        # (a, b) sorted -> strength incrément par Hebbian
        self.pulses: list[dict] = []  # ring buffer {from, to, strength, ts}
        self.lock = threading.Lock()
        # Compteurs cumulés + derniers timestamps réels (pour heartbeat UI)
        self.cum_activations    = 0
        self.cum_pulses         = 0
        self.cum_hebbian_ticks  = 0
        self.last_activation_ts = 0.0
        self.last_pulse_ts      = 0.0
        self.last_hebbian_ts    = 0.0  # dernier renforcement d'arête
        self.last_wander_ts     = 0.0
        self.created_at         = time.time()

    def _decay_one(self, node_id: str) -> float:
        """Calcule l'activation actuelle après décroissance exponentielle."""
        if node_id not in self.activations: return 0.0
        a0, t0 = self.activations[node_id]
        dt = time.time() - t0
        a_now = a0 * math.exp(-dt / DECAY_TAU_SEC)
        return a_now if a_now > ACTIVATION_FLOOR else 0.0

    def activate(self, node_id: str, level: float = 1.0):
        """Active un nœud. Si déjà actif, prend le max. Persiste sur disque (cross-process)."""
        if not node_id: return
        now = time.time()
        with self.lock:
            current = self._decay_one(node_id)
            new_a = max(current, level)
            self.activations[node_id] = (new_a, now)
            self.cum_activations += 1
            self.last_activation_ts = now
        # Persistance immédiate cross-process (disk-shared)
        try: self.persist_inline()
        except Exception: pass

    def persist_inline(self):
        """Persiste l'état immédiatement (sans le snapshot/decay)."""
        try:
            data = {
                "activations": {k: list(v) for k, v in self.activations.items()},
                "edge_strengths": {f"{a}|||{b}": s for (a,b), s in self.edge_strengths.items()},
                "ts": time.time(),
            }
            STATE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception: pass

    def load_from_disk(self):
        """Charge l'état persisté (pour partage cross-process)."""
        if not STATE_FILE.exists(): return
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            with self.lock:
                # Merge : prend le max entre disk et memory
                disk_acts = data.get("activations", {})
                for k, v in disk_acts.items():
                    if isinstance(v, list) and len(v) == 2:
                        existing = self.activations.get(k)
                        if not existing or v[1] > existing[1]:  # plus récent
                            self.activations[k] = tuple(v)
                disk_edges = data.get("edge_strengths", {})
                for ek, s in disk_edges.items():
                    if "|||" in ek:
                        a, b = ek.split("|||", 1)
                        key = (a, b)
                        if key not in self.edge_strengths or self.edge_strengths[key] < s:
                            self.edge_strengths[key] = s
        except Exception: pass

    def co_activate(self, node_ids: list[str]):
        """Active une liste de nœuds simultanément + Hebbian sur paires + pulses chaînés."""
        if not node_ids: return
        for n in node_ids:
            self.activate(n)
        # Hebbian : strengthen edges between co-activated
        with self.lock:
            now = time.time()
            for i, a in enumerate(node_ids):
                for b in node_ids[i+1:]:
                    key = tuple(sorted([a, b]))
                    self.edge_strengths[key] = self.edge_strengths.get(key, 0) + HEBBIAN_LR
                    self.cum_hebbian_ticks += 1
                    self.last_hebbian_ts = now
        # Pulses visibles : chaîne A→B→C...→N (parcours cognitif)
        self.co_pulse(node_ids)

    def _emit_pulse(self, src: str, dst: str, strength: float):
        """Enregistre un événement de propagation pour la viz (ring buffer + disque)."""
        now = time.time()
        evt = {"from": src, "to": dst, "strength": round(strength, 3), "ts": now}
        with self.lock:
            self.pulses.append(evt)
            if len(self.pulses) > PULSES_RING_MAX:
                self.pulses = self.pulses[-PULSES_RING_MAX:]
            self.cum_pulses += 1
            self.last_pulse_ts = now
        # Persist append-only for cross-process (un autre script qui active doit pouvoir
        # animer la viz servie par serve.py)
        try:
            with PULSES_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(evt, ensure_ascii=False) + "\n")
        except Exception: pass

    def spread(self, source_id: str, neighbors_with_sim: list[tuple[str, float]]):
        """Propage l'activation d'un nœud source à ses voisins, pondérée par similarité.
        Émet un événement pulse pour chaque propagation (visible côté UI 3D)."""
        if not source_id or not neighbors_with_sim: return
        src_act = self._decay_one(source_id)
        if src_act < ACTIVATION_FLOOR: return
        for nb_id, sim in neighbors_with_sim:
            spread_amount = SPREAD_RATIO * src_act * max(0, min(1, sim))
            if spread_amount > ACTIVATION_FLOOR:
                self.activate(nb_id, level=spread_amount)
                self._emit_pulse(source_id, nb_id, spread_amount)

    def co_pulse(self, node_ids: list[str]):
        """Émet des pulses entre paires de nœuds co-activés (visualise la cognition
        synchrone : retrieve_context, A* path, etc.)."""
        if not node_ids or len(node_ids) < 2: return
        # Chaîne séquentielle : n0 → n1 → n2 ... — illustre le parcours cognitif
        for i in range(len(node_ids) - 1):
            self._emit_pulse(node_ids[i], node_ids[i+1], 0.7)

    def recent_pulses(self, since_ts: float = 0.0) -> list[dict]:
        """Retourne les pulses récents (> since_ts ET < TTL)."""
        cutoff = time.time() - PULSES_TTL_SEC
        with self.lock:
            return [p for p in self.pulses if p["ts"] > max(since_ts, cutoff)]

    def snapshot(self) -> dict:
        """État courant des activations (après décroissance). Charge le disk pour cross-process."""
        self.load_from_disk()  # toujours rafraîchir avec ce que les autres process ont écrit
        out = {}
        with self.lock:
            for node_id in list(self.activations.keys()):
                a = self._decay_one(node_id)
                if a > ACTIVATION_FLOOR:
                    out[node_id] = round(a, 3)
                else:
                    # cleanup
                    del self.activations[node_id]
            # top 20 edges renforcées
            top_edges = sorted(self.edge_strengths.items(), key=lambda x: -x[1])[:20]
        return {
            "active_nodes": out,
            "n_active": len(out),
            "top_hebbian_edges": [{"a": e[0][0], "b": e[0][1], "strength": round(e[1], 3)}
                                  for e in top_edges],
            "ts": time.time(),
            # Heartbeat : compteurs + derniers événements pour timers UI
            "cum_activations":    self.cum_activations,
            "cum_pulses":         self.cum_pulses,
            "cum_hebbian_ticks":  self.cum_hebbian_ticks,
            "last_activation_ts": self.last_activation_ts,
            "last_pulse_ts":      self.last_pulse_ts,
            "last_hebbian_ts":    self.last_hebbian_ts,
            "last_wander_ts":     self.last_wander_ts,
            "n_edges_total":      len(self.edge_strengths),
            "wander_interval":    WANDER_INTERVAL,
        }

    def persist(self):
        """Sauve sur disque pour survivre redémarrage. Utilise le format
        persist_inline (compatible load_from_disk) — IMPORTANT car snapshot()
        produit un format différent (active_nodes / top_hebbian_edges) qui n'est
        PAS chargeable. Bug historique : persist() écrasait l'état Hebbian."""
        self.persist_inline()


_state = ActivationState()


def activate(node_id: str, level: float = 1.0):
    _state.activate(node_id, level)


def co_activate(node_ids: list[str]):
    _state.co_activate(node_ids)


def spread(source_id: str, neighbors: list[tuple[str, float]]):
    _state.spread(source_id, neighbors)


def snapshot() -> dict:
    return _state.snapshot()


def recent_pulses(since_ts: float = 0.0) -> list[dict]:
    return _state.recent_pulses(since_ts)


def persist():
    _state.persist()


# ─── Background : persiste périodiquement ─────────────────────────────────────
_running = False

def _persist_loop():
    while _running:
        try: persist()
        except Exception: pass
        time.sleep(30)


# ─── Default Mode Network : pensée vagabonde quand idle ──────────────────────
# Réf : Raichle (2001), "A default mode of brain function", PNAS 98(2), 676-682.
#       Quand un humain n'a pas de tâche, son cerveau s'auto-active sur des
#       souvenirs/projections — il ne se "repose" jamais vraiment. On reproduit.
def _adaptive_interval(base: float) -> float:
    """Auto-adaptation : sous charge, espace les boucles ; au repos, accélère.
    Lit cortex_homeostasis si disponible — fallback sur base si dépendance absente.
    Coût : 1 appel/cycle (pas de coût continu)."""
    try:
        import cortex_homeostasis as _ch
        v = _ch.vital_signs() or {}
        cpu = (v.get("cpu") or {}).get("percent", 50) or 50
        ram = (v.get("ram") or {}).get("percent", 50) or 50
        # CPU>85 → ×2, CPU>70 → ×1.4, CPU<30 → ×0.7
        f = 1.0
        if cpu > 85: f *= 2.0
        elif cpu > 70: f *= 1.4
        elif cpu < 30: f *= 0.7
        if ram > 90: f *= 1.5
        elif ram > 80: f *= 1.2
        return max(15.0, min(180.0, base * f))
    except Exception:
        return base


def _wander_loop():
    """Toutes les WANDER_INTERVAL s (modulé par charge), si Cortex n'a pas eu
    d'activation récente, relance une pensée à partir d'un nœud aléatoire."""
    while _running:
        eff = _adaptive_interval(WANDER_INTERVAL)
        time.sleep(eff)
        try:
            # Si encore en cooldown utilisateur, skip (ne pas interrompre Sam)
            try:
                pause_flag = Path(r"<CORTEX_REPO>\.cortex-pause.flag")
                if pause_flag.exists(): continue
            except Exception: pass
            snap = _state.snapshot()
            n_act = snap.get("n_active", 0)
            last_age = time.time() - max(
                snap.get("last_activation_ts") or 0,
                snap.get("last_pulse_ts") or 0,
                snap.get("last_wander_ts") or 0,
            )
            # Bug évité : des activations anciennes restent > floor pendant
            # plusieurs minutes. Elles ne doivent pas bloquer indéfiniment la
            # pensée vagabonde. On skip seulement si l'activité est récente.
            if n_act >= 3 and last_age < WANDER_IDLE_SEC:
                continue
            wander_once(reason="idle_stale" if n_act >= 3 else "idle_empty")
        except Exception: pass


import sys


def wander_once(reason: str = "manual") -> dict:
    """Déclenche une pensée réelle maintenant.

    Utilisé par la boucle DMN et par /api/cortex/wake_brain. Contrairement au
    pulse_test, cette fonction choisit un vrai nœud du thought graph, propage
    vers ses voisins TF-IDF, renforce Hebbian et écrit les pulses disque.
    """
    import random
    snap = _state.snapshot()
    seeds = []
    for e in snap.get("top_hebbian_edges", [])[:5]:
        if e.get("a"): seeds.append(e["a"])
    for k in list(snap.get("active_nodes", {}).keys())[:3]:
        seeds.append(k)
    sys.path.insert(0, r"<CORTEX_REPO>\scripts\brain")
    import cortex_thought_graph as _ctg
    _ctg.build_graph()
    nodes = _ctg._state.get("nodes", []) or []
    graph_sources = {n.get("source") for n in nodes if n.get("source")}
    seeds = [s for s in seeds if s in graph_sources]
    if not seeds and nodes:
        seeds = [random.choice(nodes)["source"]]
    if not seeds:
        return {"ok": False, "error": "no_seed", "reason": reason}
    seed = random.choice(seeds)
    _state.last_wander_ts = time.time()
    _state.activate(seed, level=1.0)
    pairs = []
    try:
        idx = _ctg._find_node(seed)
        if idx is not None:
            from sklearn.metrics.pairwise import cosine_similarity
            sims = cosine_similarity(_ctg._state["vectors"][idx],
                                      _ctg._state["vectors"])[0]
            nbrs = sorted(enumerate(sims), key=lambda x: -x[1])[1:4]
            for j, sim in nbrs:
                nb = _ctg._state["nodes"][j].get("source")
                if nb: pairs.append((nb, float(sim)))
    except Exception as e:
        return {"ok": False, "error": str(e), "seed": seed, "reason": reason}
    if pairs:
        _state.spread(seed, pairs)
        _state.co_activate([seed] + [p[0] for p in pairs])
    return {
        "ok": True,
        "reason": reason,
        "seed": seed,
        "neighbors": [{"node": p, "similarity": round(sim, 3)} for p, sim in pairs],
        "n_pulses_expected": max(0, len(pairs) * 2),
        "ts": time.time(),
    }

def start():
    global _running
    if _running: return
    _running = True
    threading.Thread(target=_persist_loop, daemon=True).start()
    threading.Thread(target=_wander_loop, daemon=True).start()


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        # Test rapide : active 3 nœuds, spread, snapshot
        activate("test_a", 1.0)
        activate("test_b", 0.8)
        co_activate(["test_a", "test_c", "test_d"])
        spread("test_a", [("test_b", 0.7), ("test_c", 0.4)])
        print(json.dumps(snapshot(), indent=2, ensure_ascii=False))
    else:
        print(json.dumps(snapshot(), indent=2, ensure_ascii=False))
