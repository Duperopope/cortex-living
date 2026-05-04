"""
cortex_friston_belief.py — Belief posterior + KL divergence calculées.

Le formalisme Friston "complet" tient sur 3 quantités :

1. **q(s|o)** : posterior approché — distribution sur les états cachés `s`
   conditionnés à l'observation `o`. Cortex le maintient comme un Dirichlet
   discret sur les "modes cognitifs" (silent, exploring, reflecting,
   planning, perceiving).

2. **p(s)** : prior — distribution a priori sur les modes (déduite de la
   personnalité Big5 + curiosité courante).

3. **VFE (Variational Free Energy)** = E_q[log q(s) - log p(o,s)]
   = -accuracy + complexity
   = surprise observée + KL(q(s|o) || p(s))

C'est le SCORE qu'un agent Friston minimise. Cortex ne calcule pas le KL
exact via gradient (pas torch), mais via une closed form Dirichlet sur les
modes discrets : tractable, auditable.

API :
    BeliefState(modes, prior_alpha) → instance avec posterior Dirichlet
    .observe(observation_signal: dict) → met à jour le posterior via Bayes
    .kl_to_prior() → KL(q || p), valeur scalaire ≥ 0
    .vfe(observation_log_lik: float) → VFE scalaire (à minimiser)
    .self_test()

Réf : Friston (2010) "Free-energy principle: a unified brain theory?" — la
formule VFE = -accuracy + complexity est universelle ; ici on l'instancie
sur un espace de modes discrets pour que ce soit calculable sans GPU.
"""
from __future__ import annotations
import json
import math
import sys
import time
from pathlib import Path

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None

REPO = Path(__file__).resolve().parent
VAULT = Path(r"<USER_HOME>\Documents\Obsidian Vault")
STATE_FILE = VAULT / ".cortex-belief-state.json"

# Modes cognitifs discrets — l'espace `s` que Cortex modélise.
DEFAULT_MODES = ("silent", "exploring", "reflecting", "planning", "perceiving")


def _now() -> float: return time.time()


class BeliefState:
    """Posterior Dirichlet sur modes cognitifs.

    α = paramètres de concentration. q(s) ∝ α_s.
    """

    def __init__(self, modes: tuple = DEFAULT_MODES,
                  prior_alpha: list[float] | None = None):
        self.modes = list(modes)
        self.n = len(modes)
        if prior_alpha is None:
            prior_alpha = [1.0] * self.n  # uniforme initial
        if len(prior_alpha) != self.n:
            raise ValueError("prior_alpha length must match modes")
        # Prior fixe (peut être recalculé via personality)
        self.prior_alpha: list[float] = list(prior_alpha)
        # Posterior courant — initialisé au prior
        self.alpha: list[float] = list(prior_alpha)
        self.n_observations = 0
        self.last_kl = None
        self.last_vfe = None

    def q_dist(self) -> list[float]:
        """Distribution posterior normalisée q(s)."""
        s = sum(self.alpha)
        if s <= 0: return [1.0 / self.n] * self.n
        return [a / s for a in self.alpha]

    def p_dist(self) -> list[float]:
        """Distribution prior normalisée p(s)."""
        s = sum(self.prior_alpha)
        if s <= 0: return [1.0 / self.n] * self.n
        return [a / s for a in self.prior_alpha]

    def observe(self, evidence: dict) -> dict:
        """Update bayésien : evidence est un dict mode → likelihood relative.

        Ex : {"exploring": 0.6, "reflecting": 0.3, "silent": 0.1}
        On multiplie alpha[mode] par (1 + evidence[mode]) — boost
        proportionnel à la vraisemblance observée. Approximation locale du
        Bayes update sur Dirichlet.
        """
        if not isinstance(evidence, dict):
            return {"ok": False, "error": "evidence must be dict"}
        for i, mode in enumerate(self.modes):
            ev = evidence.get(mode, 0.0)
            if isinstance(ev, (int, float)) and ev > 0:
                self.alpha[i] += float(ev)
        self.n_observations += 1
        # Recalcule KL et VFE
        self.last_kl = self.kl_to_prior()
        return {"ok": True,
                 "alpha": self.alpha[:],
                 "q": self.q_dist(),
                 "kl_to_prior": self.last_kl,
                 "n_observations": self.n_observations}

    def kl_to_prior(self) -> float:
        """KL(q || p) — divergence du posterior au prior.

        Pour distributions discrètes :
            KL(q||p) = Σ q(s) * log(q(s) / p(s))
        Si q ≈ p → KL ≈ 0 (peu d'information apportée par les obs).
        Si q s'écarte → KL > 0 (le posterior est très différent du prior).
        """
        q = self.q_dist()
        p = self.p_dist()
        kl = 0.0
        for qi, pi in zip(q, p):
            if qi > 1e-12 and pi > 1e-12:
                kl += qi * math.log(qi / pi)
        return max(0.0, kl)  # numériquement KL ≥ 0

    def vfe(self, observation_log_lik: float = 0.0) -> float:
        """Variational Free Energy = -accuracy + complexity.

        - accuracy = E_q[log p(o|s)] ≈ observation_log_lik (passé par caller)
        - complexity = KL(q(s|o) || p(s))
        - VFE = complexity - accuracy

        Plus VFE est BAS, mieux le posterior explique l'observation tout en
        restant proche du prior (parcimonie cognitive).
        """
        kl = self.kl_to_prior()
        vfe = kl - observation_log_lik
        self.last_vfe = vfe
        return vfe

    def expected_free_energy_for(self, predicted_evidence: dict,
                                  predicted_log_lik: float = 0.0) -> float:
        """EFE pour une action candidate.

        On simule l'effet de prendre cette action (predicted_evidence),
        on calcule q hypothétique, et on retourne le KL résultant + le
        log-lik attendu.

        Plus EFE est bas, plus l'action est attractive (gain d'info OU
        meilleure adéquation aux préférences).
        """
        # Simulation : alpha hypothétique après l'action
        hyp_alpha = list(self.alpha)
        for i, mode in enumerate(self.modes):
            ev = predicted_evidence.get(mode, 0.0)
            if isinstance(ev, (int, float)) and ev > 0:
                hyp_alpha[i] += float(ev)
        # KL du posterior hypothétique vs prior
        s = sum(hyp_alpha)
        q_hyp = [a / s for a in hyp_alpha] if s > 0 else [1.0/self.n]*self.n
        p = self.p_dist()
        kl = 0.0
        for qi, pi in zip(q_hyp, p):
            if qi > 1e-12 and pi > 1e-12:
                kl += qi * math.log(qi / pi)
        return max(0.0, kl) - predicted_log_lik

    def to_dict(self) -> dict:
        return {
            "ts": _now(),
            "modes": self.modes,
            "prior_alpha": self.prior_alpha,
            "alpha": self.alpha,
            "q": self.q_dist(),
            "p": self.p_dist(),
            "kl_to_prior": self.kl_to_prior(),
            "last_vfe": self.last_vfe,
            "n_observations": self.n_observations,
        }

    def save(self, path: Path = STATE_FILE) -> dict:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self.to_dict(), indent=2,
                                        ensure_ascii=False), encoding="utf-8")
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    @classmethod
    def load(cls, path: Path = STATE_FILE) -> "BeliefState | None":
        if not path.exists(): return None
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            inst = cls(tuple(d["modes"]), d["prior_alpha"])
            inst.alpha = list(d["alpha"])
            inst.n_observations = d.get("n_observations", 0)
            return inst
        except Exception:
            return None


def self_test() -> dict:
    """Vérifie : KL≥0 ; KL augmente quand on observe des évidences asymétriques ;
    EFE distingue actions ; save/load roundtrip."""
    bs = BeliefState()
    initial_kl = bs.kl_to_prior()
    # Observation : evidence forte pour "exploring"
    bs.observe({"exploring": 5.0, "reflecting": 0.5})
    after_obs_kl = bs.kl_to_prior()
    # KL doit avoir augmenté
    kl_increased = after_obs_kl > initial_kl
    # VFE négatif si accuracy positive
    vfe1 = bs.vfe(observation_log_lik=2.0)
    vfe2 = bs.vfe(observation_log_lik=0.0)
    vfe_consistent = vfe1 < vfe2
    # EFE pour 2 actions hypothétiques différentes
    efe_explore = bs.expected_free_energy_for({"exploring": 3.0}, predicted_log_lik=1.0)
    efe_silent = bs.expected_free_energy_for({"silent": 3.0}, predicted_log_lik=0.0)
    actions_differentiated = abs(efe_explore - efe_silent) > 0.01
    # Save/load
    import tempfile
    tmp = Path(tempfile.gettempdir()) / "_belief_test.json"
    save_ok = bs.save(tmp).get("ok")
    bs2 = BeliefState.load(tmp)
    rt = bs2 is not None and abs(bs2.kl_to_prior() - bs.kl_to_prior()) < 1e-9
    try: tmp.unlink()
    except Exception: pass
    return {
        "ok": all([kl_increased, vfe_consistent, actions_differentiated, rt]),
        "initial_kl": round(initial_kl, 6),
        "after_obs_kl": round(after_obs_kl, 6),
        "kl_increased": kl_increased,
        "vfe_with_accuracy": round(vfe1, 4),
        "vfe_without_accuracy": round(vfe2, 4),
        "vfe_consistent": vfe_consistent,
        "efe_exploring_action": round(efe_explore, 4),
        "efe_silent_action": round(efe_silent, 4),
        "actions_differentiated": actions_differentiated,
        "save_load_ok": rt,
    }


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "test"
    if cmd == "test":
        print(json.dumps(self_test(), indent=2, ensure_ascii=False))
    elif cmd == "state":
        bs = BeliefState.load() or BeliefState()
        print(json.dumps(bs.to_dict(), indent=2, ensure_ascii=False))
    else:
        print("Usage: cortex_friston_belief.py {test|state}")
