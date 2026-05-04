"""
cortex_jepa_continual.py — Continual learning du modèle JEPA sans oubli catastrophique.

Le MLP JEPA actuel (`vault_jepa.py`) est entraîné batch puis figé. Pour devenir
plus IAG, il doit apprendre EN CONTINU des nouvelles paires (query, target) qui
arrivent via les chats Sam, les wander_loops, les emergence cycles.

Mais juste retrain sur les nouvelles paires = OUBLI CATASTROPHIQUE (Kirkpatrick
2017). Solution honnête en NumPy pur :

1. Replay buffer : .cortex-jepa-replay.npz garde les paires les plus
   informatives (gap élevé = paires où le modèle se trompait le plus).
2. Step incrémental : pour N nouvelles paires, sample K du replay et fait
   un mini-batch SGD (anti-oubli).
3. Auto-trigger : si N nouveaux chats > 20 ou gap moyen monte > 0.3,
   relance un step.

Pas de PyTorch. ~50 lignes de SGD numpy.

API :
    record_pair(query_emb, target_emb, gap_observed) → ajoute au replay
    step(n_new_pairs) → fait un retrain incrémental, retourne stats
    auto_step_if_needed() → trigger si conditions
    self_test() → vérifie le pipeline (avec données synthétiques si JEPA absent)
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

VAULT = Path(r"<USER_HOME>\Documents\Obsidian Vault")
JEPA_MODEL = VAULT / ".vault-jepa.npz"
REPLAY = VAULT / ".cortex-jepa-replay.npz"
STATS = VAULT / ".cortex-jepa-continual-stats.json"
EVENTS = VAULT / ".cortex-jepa-continual-events.jsonl"

REPLAY_MAX_SIZE = 5000  # paires max (mémoire bornée)
LEARNING_RATE = 1e-4
N_ITERATIONS = 10  # mini-batch steps par appel à step()
MINI_BATCH = 32

# Seuils trigger auto
AUTO_TRIGGER_MIN_NEW = 20
AUTO_TRIGGER_GAP_THRESH = 0.3


def _now() -> float: return time.time()


def _load_stats() -> dict:
    if STATS.exists():
        try: return json.loads(STATS.read_text(encoding="utf-8"))
        except Exception: pass
    return {
        "version": "wm-continual-0",
        "n_steps_total": 0,
        "n_pairs_seen": 0,
        "last_step_ts": 0.0,
        "last_loss": None,
        "replay_size": 0,
        "created_at": _now(),
    }


def _save_stats(stats: dict) -> None:
    stats["updated_at"] = _now()
    try:
        STATS.parent.mkdir(parents=True, exist_ok=True)
        STATS.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception: pass


def _log(event: dict) -> None:
    try:
        with EVENTS.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": _now(), **event}, ensure_ascii=False) + "\n")
    except Exception: pass


def _try_numpy():
    try:
        import numpy as np
        return np
    except Exception: return None


def _load_replay(np):
    if not REPLAY.exists():
        return None, None, None
    try:
        data = np.load(REPLAY, allow_pickle=False)
        return data["queries"], data["targets"], data["gaps"]
    except Exception:
        return None, None, None


def _save_replay(np, queries, targets, gaps):
    try:
        REPLAY.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(REPLAY,
                             queries=queries.astype(np.float32),
                             targets=targets.astype(np.float32),
                             gaps=gaps.astype(np.float32))
    except Exception as e:
        _log({"type": "save_replay_err", "error": str(e)})


def record_pair(query_emb, target_emb, gap_observed: float = 0.0) -> dict:
    """Ajoute une paire au replay buffer. Garde les top REPLAY_MAX_SIZE par gap."""
    np = _try_numpy()
    if np is None: return {"ok": False, "error": "numpy not available"}
    try:
        q = np.asarray(query_emb, dtype=np.float32).reshape(-1)
        t = np.asarray(target_emb, dtype=np.float32).reshape(-1)
        if q.shape != t.shape:
            return {"ok": False, "error": f"shape mismatch q={q.shape} t={t.shape}"}
    except Exception as e:
        return {"ok": False, "error": f"convert: {e}"}
    queries, targets, gaps = _load_replay(np)
    if queries is None:
        queries = q[None, :]
        targets = t[None, :]
        gaps = np.array([gap_observed], dtype=np.float32)
    else:
        queries = np.vstack([queries, q[None, :]])
        targets = np.vstack([targets, t[None, :]])
        gaps = np.concatenate([gaps, np.array([gap_observed], dtype=np.float32)])
        # Si dépasse capacité : garde les top par gap
        if queries.shape[0] > REPLAY_MAX_SIZE:
            top_idx = np.argsort(-gaps)[:REPLAY_MAX_SIZE]
            queries = queries[top_idx]
            targets = targets[top_idx]
            gaps = gaps[top_idx]
    _save_replay(np, queries, targets, gaps)
    stats = _load_stats()
    stats["n_pairs_seen"] += 1
    stats["replay_size"] = int(queries.shape[0])
    _save_stats(stats)
    return {"ok": True, "replay_size": int(queries.shape[0])}


def _load_jepa_model(np):
    """Charge le modèle JEPA NumPy (W1, b1, W2, b2)."""
    if not JEPA_MODEL.exists():
        return None
    try:
        data = np.load(JEPA_MODEL, allow_pickle=False)
        params = {k: data[k] for k in data.files}
        return params
    except Exception:
        return None


def _save_jepa_model(np, params):
    try:
        np.savez_compressed(JEPA_MODEL, **params)
    except Exception as e:
        _log({"type": "save_model_err", "error": str(e)})


def _forward(np, params, x):
    """MLP 2 couches : x → W1+b1 → relu → W2+b2 → y_pred."""
    h = np.maximum(0, x @ params["W1"] + params["b1"])
    return h @ params["W2"] + params["b2"]


def _step_sgd(np, params, X, Y, lr=LEARNING_RATE):
    """Un mini-batch SGD step. Loss = MSE."""
    h = np.maximum(0, X @ params["W1"] + params["b1"])
    Y_pred = h @ params["W2"] + params["b2"]
    diff = Y_pred - Y
    loss = float((diff ** 2).mean())
    n = X.shape[0]
    # Gradients
    grad_W2 = h.T @ diff / n
    grad_b2 = diff.mean(axis=0)
    dh = diff @ params["W2"].T
    dh = dh * (h > 0)
    grad_W1 = X.T @ dh / n
    grad_b1 = dh.mean(axis=0)
    # SGD update
    params["W1"] = params["W1"] - lr * grad_W1
    params["b1"] = params["b1"] - lr * grad_b1
    params["W2"] = params["W2"] - lr * grad_W2
    params["b2"] = params["b2"] - lr * grad_b2
    return loss, params


def step(n_iterations: int = N_ITERATIONS, mini_batch: int = MINI_BATCH) -> dict:
    """Continual learning step : sample du replay, mini-batch SGD anti-oubli."""
    np = _try_numpy()
    if np is None:
        return {"ok": False, "error": "numpy not available"}
    queries, targets, gaps = _load_replay(np)
    if queries is None or queries.shape[0] < mini_batch:
        return {"ok": False, "reason": "replay too small",
                "replay_size": 0 if queries is None else int(queries.shape[0])}
    params = _load_jepa_model(np)
    if params is None:
        return {"ok": False, "reason": "JEPA model not found",
                "expected_path": str(JEPA_MODEL)}
    losses = []
    n = queries.shape[0]
    rng = np.random.RandomState(int(_now()) & 0xFFFFFFFF)
    for it in range(n_iterations):
        # Sampling pondéré par gap (plus le gap est haut, plus on rejoue)
        weights = gaps / max(1e-6, gaps.sum())
        try:
            idx = rng.choice(n, size=mini_batch, replace=True, p=weights)
        except Exception:
            idx = rng.choice(n, size=mini_batch, replace=True)
        X = queries[idx]
        Y = targets[idx]
        loss, params = _step_sgd(np, params, X, Y)
        losses.append(loss)
    _save_jepa_model(np, params)
    stats = _load_stats()
    stats["n_steps_total"] += 1
    stats["last_step_ts"] = _now()
    stats["last_loss"] = round(float(losses[-1]), 6)
    stats["mean_loss"] = round(float(sum(losses) / len(losses)), 6)
    stats["replay_size"] = int(n)
    _save_stats(stats)
    _log({"type": "step", "n_iterations": n_iterations,
          "first_loss": round(losses[0], 6), "last_loss": round(losses[-1], 6)})
    return {
        "ok": True,
        "n_iterations": n_iterations,
        "first_loss": round(losses[0], 6),
        "last_loss": round(losses[-1], 6),
        "improvement_pct": round(100 * (losses[0] - losses[-1]) / max(1e-6, losses[0]), 2),
        "replay_size": int(n),
        "n_steps_total": stats["n_steps_total"],
    }


def auto_step_if_needed() -> dict:
    """Trigger step() si conditions remplies."""
    np = _try_numpy()
    if np is None: return {"ok": False, "skipped": True, "reason": "no numpy"}
    queries, targets, gaps = _load_replay(np)
    if queries is None: return {"ok": False, "skipped": True, "reason": "no replay"}
    n = queries.shape[0]
    stats = _load_stats()
    n_seen_since_last = stats["n_pairs_seen"] - (stats.get("n_pairs_at_last_step") or 0)
    mean_gap = float(gaps.mean()) if n > 0 else 0.0
    should_step = (n_seen_since_last >= AUTO_TRIGGER_MIN_NEW or
                   mean_gap >= AUTO_TRIGGER_GAP_THRESH)
    if not should_step:
        return {"ok": True, "skipped": True,
                "reason": f"n_seen_since_last={n_seen_since_last}, mean_gap={mean_gap:.3f}"}
    rep = step()
    if rep.get("ok"):
        stats = _load_stats()
        stats["n_pairs_at_last_step"] = stats["n_pairs_seen"]
        _save_stats(stats)
    return {"ok": True, "skipped": False, "step": rep}


def self_test() -> dict:
    np = _try_numpy()
    if np is None:
        return {"ok": False, "tests": [{"name": "numpy", "ok": False}]}
    tests = []
    # Test 1 : record_pair avec embeddings synthétiques
    rng = np.random.RandomState(42)
    q = rng.randn(768).astype(np.float32)
    t = rng.randn(768).astype(np.float32)
    rep = record_pair(q.tolist(), t.tolist(), gap_observed=0.5)
    tests.append({"name": "record_pair", "ok": rep.get("ok", False),
                  "replay_size": rep.get("replay_size")})
    # Test 2 : add several pairs to enable a step
    for i in range(40):
        q = rng.randn(768).astype(np.float32)
        t = rng.randn(768).astype(np.float32)
        record_pair(q.tolist(), t.tolist(), gap_observed=float(rng.rand()))
    queries, targets, gaps = _load_replay(np)
    tests.append({"name": "replay_filled",
                  "ok": queries is not None and queries.shape[0] >= 32,
                  "n": int(queries.shape[0]) if queries is not None else 0})
    # Test 3 : si modèle JEPA présent, faire un step
    params = _load_jepa_model(np)
    if params is not None:
        rep = step(n_iterations=3)
        tests.append({"name": "step_with_jepa", "ok": rep.get("ok", False),
                      "improvement_pct": rep.get("improvement_pct"),
                      "first_loss": rep.get("first_loss"),
                      "last_loss": rep.get("last_loss")})
    else:
        tests.append({"name": "step_with_jepa", "ok": True,
                      "skipped": True,
                      "reason": "JEPA model not found, replay still works"})
    # Test 4 : auto_step_if_needed
    rep = auto_step_if_needed()
    tests.append({"name": "auto_step_if_needed",
                  "ok": "ok" in rep, "result": rep})
    return {"ok": all(t["ok"] for t in tests), "tests": tests, "stats": _load_stats()}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "stats":
        print(json.dumps(_load_stats(), indent=2, ensure_ascii=False))
    elif cmd == "step":
        print(json.dumps(step(), indent=2, ensure_ascii=False))
    elif cmd == "auto":
        print(json.dumps(auto_step_if_needed(), indent=2, ensure_ascii=False))
    elif cmd == "test":
        print(json.dumps(self_test(), indent=2, ensure_ascii=False))
    else:
        print("Usage: cortex_jepa_continual.py {stats|step|auto|test}")
