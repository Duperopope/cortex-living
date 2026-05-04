"""
cortex_jepa_v2.py — Mini-JEPA implémenté complètement (pas un stub).

Architecture LeCun JEPA simplifiée mais fonctionnelle :

    obs_t ──[online encoder f_θ]──→ z_t
    obs_{t+1} ──[target encoder f_ξ EMA(θ)]──→ z_target  (stop-gradient)
    z_t ⊕ action_one_hot ──[predictor g_φ]──→ ẑ_{t+1}
    Loss = MSE(ẑ_{t+1}, sg(z_target))

Les 3 composantes essentielles d'un JEPA réel :
1. **Online encoder** entraîné par gradient sur la loss prédictive
2. **Target encoder** mis à jour par EMA (pas de gradient direct, anti-collapse)
3. **Predictor** qui prédit dans l'espace latent — pas dans l'espace pixel

Pas le formalisme LeCun papier exact (qui utilise des images + ViT), mais le
contrat structurel est respecté. Pas de `torch` (incompatible avec env
Paperclip qui a `numpy<2.0` pour sklearn) → NumPy from scratch.

API :
    JEPA(obs_dim, n_actions, latent_dim) → instance
    .encode(obs)         → z (online)
    .encode_target(obs)  → z_target (EMA, stop-gradient)
    .predict(z, action)  → ẑ_next
    .train_step(obs_pre, action, obs_post) → {loss, ...}
    .save(path) / .load(path)
    .self_test()
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None

REPO = Path(__file__).resolve().parent
VAULT = Path(r"<USER_HOME>\Documents\Obsidian Vault")
MODEL_FILE = VAULT / ".cortex-jepa-v2-model.npz"
TRAIN_LOG  = VAULT / ".cortex-jepa-v2-train.jsonl"


def _now() -> float: return time.time()


def _xavier(rng, fan_in: int, fan_out: int):
    """Initialisation Xavier/Glorot uniform."""
    limit = (6.0 / (fan_in + fan_out)) ** 0.5
    return rng.uniform(-limit, limit, (fan_in, fan_out)).astype(np.float32)


def _relu(x): return np.maximum(0.0, x)
def _drelu(x): return (x > 0).astype(np.float32)


class JEPA:
    """Mini-JEPA NumPy : encoder + target_encoder (EMA) + predictor.

    Tailles par défaut : obs 2D (n_active, compression_error), 9 actions
    (les 9 actions Cortex), latent 8D. Tout petit mais réel.

    EMA τ=0.99 : target = 0.99*target + 0.01*online à chaque train_step.
    """

    def __init__(self,
                  obs_dim: int = 2,
                  n_actions: int = 9,
                  latent_dim: int = 8,
                  hidden_dim: int = 16,
                  ema_tau: float = 0.99,
                  lr: float = 0.01,
                  seed: int = 42):
        if np is None:
            raise ImportError("numpy required for JEPA")
        self.obs_dim = obs_dim
        self.n_actions = n_actions
        self.latent_dim = latent_dim
        self.hidden_dim = hidden_dim
        self.ema_tau = ema_tau
        self.lr = lr

        rng = np.random.default_rng(seed)
        # Online encoder : obs_dim → hidden → latent
        self.W1 = _xavier(rng, obs_dim, hidden_dim)
        self.b1 = np.zeros(hidden_dim, dtype=np.float32)
        self.W2 = _xavier(rng, hidden_dim, latent_dim)
        self.b2 = np.zeros(latent_dim, dtype=np.float32)
        # Target encoder : copie initiale de l'online (pas séparée au départ)
        self.W1_t = self.W1.copy()
        self.b1_t = self.b1.copy()
        self.W2_t = self.W2.copy()
        self.b2_t = self.b2.copy()
        # Predictor : (latent + n_actions) → latent
        self.Wp1 = _xavier(rng, latent_dim + n_actions, hidden_dim)
        self.bp1 = np.zeros(hidden_dim, dtype=np.float32)
        self.Wp2 = _xavier(rng, hidden_dim, latent_dim)
        self.bp2 = np.zeros(latent_dim, dtype=np.float32)

        self.n_train_steps = 0
        self.last_loss = None
        self.loss_history: list[float] = []

    # ─── Encoders ────────────────────────────────────────────────────────────
    def _enc(self, x, W1, b1, W2, b2):
        h = _relu(x @ W1 + b1)
        z = h @ W2 + b2
        return z, h

    def encode(self, obs) -> "np.ndarray":
        x = np.asarray(obs, dtype=np.float32).reshape(-1, self.obs_dim)
        z, _ = self._enc(x, self.W1, self.b1, self.W2, self.b2)
        return z

    def encode_target(self, obs) -> "np.ndarray":
        """Target encoder, stop-gradient (utilisé pour la loss seulement)."""
        x = np.asarray(obs, dtype=np.float32).reshape(-1, self.obs_dim)
        z, _ = self._enc(x, self.W1_t, self.b1_t, self.W2_t, self.b2_t)
        return z

    # ─── Predictor ───────────────────────────────────────────────────────────
    def _onehot_action(self, action_id: int) -> "np.ndarray":
        v = np.zeros((1, self.n_actions), dtype=np.float32)
        if 0 <= action_id < self.n_actions:
            v[0, action_id] = 1.0
        return v

    def predict(self, z, action_id: int) -> "np.ndarray":
        z = np.asarray(z, dtype=np.float32).reshape(-1, self.latent_dim)
        a = self._onehot_action(action_id)
        x = np.concatenate([z, a], axis=1)
        h = _relu(x @ self.Wp1 + self.bp1)
        z_pred = h @ self.Wp2 + self.bp2
        return z_pred

    # ─── Train step ──────────────────────────────────────────────────────────
    def train_step(self, obs_pre, action_id: int, obs_post) -> dict:
        """Un pas de gradient sur la loss prédictive latente.

        - Forward : z_t = enc(obs_pre), z_target = enc_target(obs_post)
        - Predict : ẑ = predictor(z_t, action)
        - Loss : MSE(ẑ, z_target.detach())
        - Backward : grad sur online encoder (W1, b1, W2, b2) + predictor
        - EMA update : target ← τ*target + (1-τ)*online
        """
        x_pre = np.asarray(obs_pre, dtype=np.float32).reshape(-1, self.obs_dim)
        x_post = np.asarray(obs_post, dtype=np.float32).reshape(-1, self.obs_dim)
        a_oh = self._onehot_action(action_id)

        # Forward online encoder (avec activations cachées pour backprop)
        h1 = _relu(x_pre @ self.W1 + self.b1)
        z_pre = h1 @ self.W2 + self.b2  # (1, latent)
        # Target encoder (stop-gradient)
        h1_t = _relu(x_post @ self.W1_t + self.b1_t)
        z_target = h1_t @ self.W2_t + self.b2_t  # stop-grad de fait (pas de backprop dessus)
        # Predictor forward
        x_p = np.concatenate([z_pre, a_oh], axis=1)
        hp = _relu(x_p @ self.Wp1 + self.bp1)
        z_hat = hp @ self.Wp2 + self.bp2

        # Loss MSE
        diff = z_hat - z_target
        loss = float((diff ** 2).mean())

        # Backward predictor
        N = x_pre.shape[0]
        d_zhat = 2.0 * diff / (self.latent_dim * N)  # gradient de la MSE
        d_Wp2 = hp.T @ d_zhat
        d_bp2 = d_zhat.sum(axis=0)
        d_hp = d_zhat @ self.Wp2.T
        d_xp = d_hp * _drelu(hp) @ self.Wp1.T
        d_Wp1 = ((d_hp * _drelu(hp)).T @ x_p).T
        d_bp1 = (d_hp * _drelu(hp)).sum(axis=0)

        # Backward online encoder (via le gradient passé par predictor)
        d_zpre = d_xp[:, :self.latent_dim]  # le reste = grad de a_oh, ignoré
        d_W2 = h1.T @ d_zpre
        d_b2 = d_zpre.sum(axis=0)
        d_h1 = d_zpre @ self.W2.T
        d_W1 = ((d_h1 * _drelu(h1)).T @ x_pre).T
        d_b1 = (d_h1 * _drelu(h1)).sum(axis=0)

        # SGD step
        self.W1 -= self.lr * d_W1
        self.b1 -= self.lr * d_b1
        self.W2 -= self.lr * d_W2
        self.b2 -= self.lr * d_b2
        self.Wp1 -= self.lr * d_Wp1
        self.bp1 -= self.lr * d_bp1
        self.Wp2 -= self.lr * d_Wp2
        self.bp2 -= self.lr * d_bp2

        # EMA update du target encoder (τ=0.99 par défaut)
        tau = self.ema_tau
        self.W1_t = tau * self.W1_t + (1 - tau) * self.W1
        self.b1_t = tau * self.b1_t + (1 - tau) * self.b1
        self.W2_t = tau * self.W2_t + (1 - tau) * self.W2
        self.b2_t = tau * self.b2_t + (1 - tau) * self.b2

        self.n_train_steps += 1
        self.last_loss = loss
        self.loss_history.append(loss)
        if len(self.loss_history) > 200:
            self.loss_history = self.loss_history[-200:]

        return {"ok": True, "loss": loss, "n_train_steps": self.n_train_steps,
                "ema_tau": tau, "lr": self.lr}

    # ─── Persistance ─────────────────────────────────────────────────────────
    def save(self, path: Path = MODEL_FILE) -> dict:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            np.savez(str(path),
                     W1=self.W1, b1=self.b1, W2=self.W2, b2=self.b2,
                     W1_t=self.W1_t, b1_t=self.b1_t,
                     W2_t=self.W2_t, b2_t=self.b2_t,
                     Wp1=self.Wp1, bp1=self.bp1,
                     Wp2=self.Wp2, bp2=self.bp2,
                     meta=np.array([self.obs_dim, self.n_actions,
                                     self.latent_dim, self.hidden_dim,
                                     self.n_train_steps], dtype=np.int32))
            return {"ok": True, "path": str(path), "n_train_steps": self.n_train_steps}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    @classmethod
    def load(cls, path: Path = MODEL_FILE) -> "JEPA | None":
        if not path.exists() or np is None: return None
        try:
            data = np.load(str(path))
            meta = data["meta"]
            inst = cls(obs_dim=int(meta[0]), n_actions=int(meta[1]),
                        latent_dim=int(meta[2]), hidden_dim=int(meta[3]))
            inst.W1 = data["W1"]; inst.b1 = data["b1"]
            inst.W2 = data["W2"]; inst.b2 = data["b2"]
            inst.W1_t = data["W1_t"]; inst.b1_t = data["b1_t"]
            inst.W2_t = data["W2_t"]; inst.b2_t = data["b2_t"]
            inst.Wp1 = data["Wp1"]; inst.bp1 = data["bp1"]
            inst.Wp2 = data["Wp2"]; inst.bp2 = data["bp2"]
            inst.n_train_steps = int(meta[4])
            return inst
        except Exception:
            return None

    # ─── Vérification anti-collapse ──────────────────────────────────────────
    def diagnostic(self) -> dict:
        """Anti-collapse : vérifie que online ≠ target (sinon EMA dégénère)."""
        diff_W1 = float(np.abs(self.W1 - self.W1_t).mean())
        diff_W2 = float(np.abs(self.W2 - self.W2_t).mean())
        return {
            "n_train_steps": self.n_train_steps,
            "last_loss": self.last_loss,
            "loss_avg_recent": (sum(self.loss_history[-20:]) / max(1, len(self.loss_history[-20:]))
                                if self.loss_history else None),
            "online_target_W1_diff_mean": round(diff_W1, 6),
            "online_target_W2_diff_mean": round(diff_W2, 6),
            "ema_tau": self.ema_tau,
            "anti_collapse_ok": diff_W1 > 1e-6 or self.n_train_steps == 0,
        }


def self_test() -> dict:
    """Test : entraîne 100 steps sur des transitions synthétiques + vérifie
    que la loss baisse + que online ≠ target (anti-collapse)."""
    if np is None:
        return {"ok": False, "error": "numpy not available"}
    rng = np.random.default_rng(7)
    j = JEPA(obs_dim=2, n_actions=4, latent_dim=4, hidden_dim=8, lr=0.05)
    # Données synthétiques : pour chaque action, un effet linéaire stable
    effects = rng.standard_normal((4, 2)) * 0.5
    losses = []
    for step in range(200):
        action = int(rng.integers(0, 4))
        obs_pre = rng.standard_normal(2).astype(np.float32)
        obs_post = obs_pre + effects[action] + rng.standard_normal(2).astype(np.float32) * 0.05
        rep = j.train_step(obs_pre, action, obs_post)
        losses.append(rep["loss"])
    avg_first_20 = sum(losses[:20]) / 20
    avg_last_20 = sum(losses[-20:]) / 20
    learned = avg_last_20 < avg_first_20 * 0.7  # baisse > 30%
    diag = j.diagnostic()
    # Test save/load roundtrip
    import tempfile
    tmp = Path(tempfile.gettempdir()) / "_jepa_test.npz"
    save_rep = j.save(tmp)
    j2 = JEPA.load(tmp)
    roundtrip_ok = j2 is not None and j2.n_train_steps == j.n_train_steps
    try: tmp.unlink()
    except Exception: pass
    return {
        "ok": learned and diag["anti_collapse_ok"] and roundtrip_ok,
        "learned": learned,
        "loss_first20_avg": round(avg_first_20, 4),
        "loss_last20_avg": round(avg_last_20, 4),
        "loss_drop_ratio": round(avg_last_20 / max(avg_first_20, 1e-9), 3),
        "diagnostic": diag,
        "save_load_ok": roundtrip_ok,
    }


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "test"
    if cmd == "test":
        print(json.dumps(self_test(), indent=2, ensure_ascii=False))
    elif cmd == "diagnostic":
        j = JEPA.load()
        if not j:
            print(json.dumps({"error": "no model on disk"}, indent=2))
        else:
            print(json.dumps(j.diagnostic(), indent=2, ensure_ascii=False))
    else:
        print("Usage: cortex_jepa_v2.py {test|diagnostic}")
