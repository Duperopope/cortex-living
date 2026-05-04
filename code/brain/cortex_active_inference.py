"""
cortex_active_inference.py — Active Inference / Free Energy (Friston 2010).

Cadre mathématique unifié : tout (perception, action, apprentissage) minimise
une seule quantité, la Variational Free Energy (VFE).

VFE = -log p(observations|model) + KL(q(states)||p(states))
    = expected_surprise + complexity_cost

Pour Cortex :
- observations  = état réel (active_nodes, vitals, gaps JEPA observés)
- predictions   = état prédit par le world model (JEPA + plan + personality)
- surprise      = écart entre prédit et observé
- action choice = celle qui minimise EXPECTED future free energy

Anti-fake intégré :
- baseline_random : score du même choix par random sampling (référence)
- divergence_from_random : si AI choice ≈ random → l'agent ne fait rien d'utile
- log append-only de chaque calcul (.cortex-active-inference-log.jsonl)
- surprise tracking : doit DIMINUER dans le temps si l'agent apprend

API :
    measure_surprise() → float : écart prédit vs observé courant
    expected_free_energy(action) → float : EFE pour une action candidate
    select_action(actions) → dict : action choisie + comparaison à random
    self_test()
    drive_step() : un cycle complet (mesure + log)

Référence :
    Friston, K. (2010). "The free-energy principle: a unified brain theory?"
    Nature Reviews Neuroscience 11, 127-138.
    Friston et al. (2017). "Active inference, curiosity and insight."
    Neural Computation 29, 2633-2683.
"""
from __future__ import annotations
import json
import math
import random
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
VAULT = Path(r"<USER_HOME>\Documents\Obsidian Vault")
STATE = VAULT / ".cortex-active-inference-state.json"
LOG = VAULT / ".cortex-active-inference-log.jsonl"

# Liste fixe des actions (ordre stable → action_id pour JEPA one-hot).
# Cette liste DOIT correspondre à l'ordre dans select_action(actions).
ACTIONS_ORDER = ["audit_ui", "explore_graph", "map_knowledge",
                 "discovery_report", "reflect", "propose_goal",
                 "look_around", "update_claude_context", "silent"]


def _action_id(name: str) -> int:
    """Retourne l'index stable d'une action pour le one-hot encoding JEPA."""
    try: return ACTIONS_ORDER.index(name)
    except ValueError: return len(ACTIONS_ORDER) - 1  # silent par défaut


# Lazy-loaded singletons pour JEPA + BeliefState (évite charge au moindre import)
_JEPA_SINGLETON = None
_BELIEF_SINGLETON = None


def _get_jepa():
    """Charge ou crée le JEPA singleton (obs_dim=2 sur n_active+compression_error)."""
    global _JEPA_SINGLETON
    if _JEPA_SINGLETON is not None: return _JEPA_SINGLETON
    try:
        sys.path.insert(0, str(REPO))
        import cortex_jepa_v2 as _j
        if _j.np is None: return None
        # Tente de charger depuis disque
        loaded = _j.JEPA.load()
        if loaded is not None:
            _JEPA_SINGLETON = loaded
        else:
            _JEPA_SINGLETON = _j.JEPA(obs_dim=2,
                                       n_actions=len(ACTIONS_ORDER),
                                       latent_dim=8, hidden_dim=16,
                                       lr=0.005)  # lr modéré pour ne pas instabiliser
        return _JEPA_SINGLETON
    except Exception:
        return None


def _get_belief():
    """Charge ou crée le BeliefState singleton."""
    global _BELIEF_SINGLETON
    if _BELIEF_SINGLETON is not None: return _BELIEF_SINGLETON
    try:
        sys.path.insert(0, str(REPO))
        import cortex_friston_belief as _fb
        loaded = _fb.BeliefState.load()
        if loaded is not None:
            _BELIEF_SINGLETON = loaded
        else:
            _BELIEF_SINGLETON = _fb.BeliefState()
        return _BELIEF_SINGLETON
    except Exception:
        return None


# Mapping action → mode cognitif (pour BeliefState)
ACTION_TO_MODE = {
    "audit_ui":              "reflecting",
    "explore_graph":         "exploring",
    "map_knowledge":         "exploring",
    "discovery_report":      "perceiving",
    "reflect":               "reflecting",
    "propose_goal":          "planning",
    "look_around":           "perceiving",
    "update_claude_context": "perceiving",
    "silent":                "silent",
}


def _obs_to_vector(obs: dict) -> list:
    """Convertit l'observation dict en vecteur 2D NORMALISÉ pour JEPA.

    Sans normalisation, les gradients explosent (n_active peut atteindre 100,
    compression_error 0-1 → mismatch d'échelle → loss → NaN). Vu en live :
    cycle 1 loss=368, cycle 3 loss=1.2e26, cycle 4 = inf.

    Normalisation conservatrice :
    - n_active / 50 (typique 0-2 après /50, max ~2 si pic à 100)
    - compression_error tel quel (déjà ∈ [0,1])
    """
    n_active = (obs.get("n_active", 0) or 0) / 50.0
    ce = obs.get("compression_error", 0.5) or 0.5
    return [float(n_active), float(ce)]


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
        "version": "active-inference-2",
        "n_steps": 0,
        "surprise_history": [],
        "vfe_history": [],
        "n_better_than_random": 0,
        "n_worse_than_random": 0,
        "n_equal_to_random": 0,
        "last_observed_state": None,
        "last_predicted_state": None,
        # NOUVEAU : tracking outcome réel + win-rate par baseline naïve
        # baselines : random, always_reflect, always_explore, round_robin, last_best
        "baselines": {
            "random":         {"wins": 0, "losses": 0, "ties": 0,
                                "outcome_score_sum": 0.0},
            "always_reflect": {"wins": 0, "losses": 0, "ties": 0,
                                "outcome_score_sum": 0.0},
            "always_explore": {"wins": 0, "losses": 0, "ties": 0,
                                "outcome_score_sum": 0.0},
            "round_robin":    {"wins": 0, "losses": 0, "ties": 0,
                                "outcome_score_sum": 0.0},
            "last_best":      {"wins": 0, "losses": 0, "ties": 0,
                                "outcome_score_sum": 0.0},
        },
        "cortex_outcome_score_sum": 0.0,
        "n_outcome_evaluated": 0,
        # Pour évaluer l'outcome au cycle suivant : on garde l'obs pré-action
        "pending_eval": None,  # {pre_obs, chosen_action, baseline_choices}
        "history_actions": [],  # liste des actions choisies par Cortex
    }


def _save_state(s: dict) -> None:
    s["updated_at"] = _now()
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception: pass


def _log_event(ev: dict) -> None:
    """Append-only audit pour traçabilité totale (anti-fake)."""
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": _now(), **ev}, ensure_ascii=False) + "\n")
    except Exception: pass


def _observe_state() -> dict:
    """État observable réel de Cortex maintenant."""
    obs = {"ts": _now()}
    ca = _safe_import("cortex_activation")
    if ca:
        try:
            snap = ca.snapshot()
            obs["n_active"] = snap.get("n_active", 0)
            obs["n_pulses_cum"] = snap.get("cum_pulses", 0)
            obs["n_hebbian_cum"] = snap.get("cum_hebbian_ticks", 0)
        except Exception: pass
    pm = _safe_import("cortex_pipeline_manager")
    if pm:
        try:
            v = pm._vital_signs()
            obs["cpu"] = v.get("cpu", 0)
            obs["ram"] = v.get("ram", 0)
            obs["n_zombies"] = len(pm.find_zombies())
        except Exception: pass
    # Compression error proxy LIGHT (sans LM Studio) : juste isolated_ratio + jepa_loss
    # On évite cortex_curiosity.measure_compression_error() qui peut être lent
    # car il appelle probe_world. La version light suffit pour Active Inference.
    try:
        from pathlib import Path as _P
        g_path = VAULT / ".vault-graph.json"
        if g_path.exists():
            g = json.loads(g_path.read_text(encoding="utf-8"))
            nodes = g.get("nodes", [])
            edges = g.get("edges", [])
            n = len(nodes)
            if n > 0:
                deg = [0] * n
                for e in edges:
                    if isinstance(e, list) and len(e) >= 2:
                        a, b = e[0], e[1]
                        if isinstance(a, int) and a < n: deg[a] += 1
                        if isinstance(b, int) and b < n: deg[b] += 1
                isolated = sum(1 for d in deg if d <= 1)
                obs["compression_error"] = round(0.6 * (isolated / n), 4)
    except Exception:
        obs["compression_error"] = 0.5
    return obs


def _heuristic_predict(action: str | None, obs: dict) -> dict:
    """Effets hard-codés legacy. Utilisés en fallback quand pas assez de
    données apprises pour l'action. Préserve la sémantique d'origine pour ne
    pas casser les tests. À remplacer progressivement par l'apprentissage.
    """
    pred = dict(obs)
    if action == "audit_ui":
        pred["cpu"] = max(0, obs.get("cpu", 0) + 2)
        pred["compression_error"] = obs.get("compression_error", 0.5) - 0.005
    elif action == "explore_graph":
        pred["n_active"] = obs.get("n_active", 0) + 2
        pred["n_pulses_cum"] = obs.get("n_pulses_cum", 0) + 5
        pred["compression_error"] = obs.get("compression_error", 0.5) - 0.02
    elif action == "map_knowledge":
        pred["compression_error"] = obs.get("compression_error", 0.5) - 0.015
    elif action == "discovery_report":
        pred["n_active"] = obs.get("n_active", 0) + 1
    elif action == "reflect":
        pred["n_active"] = obs.get("n_active", 0) + 3
        pred["compression_error"] = obs.get("compression_error", 0.5) - 0.01
    elif action == "propose_goal":
        pred["compression_error"] = obs.get("compression_error", 0.5) - 0.008
    elif action == "look_around":
        pred["n_active"] = obs.get("n_active", 0) + 1
    elif action == "update_claude_context":
        # Petit effet : rafraîchir le contexte de Claude Code est un acte de
        # synthèse qui structure légèrement l'état. Pas un gros driver.
        pred["compression_error"] = obs.get("compression_error", 0.5) - 0.003
    elif action == "silent":
        pass  # rien ne change
    return pred


def _predict_state(action: str | None = None) -> dict:
    """Prédiction du prochain état si on prend `action`.

    Stratégie :
    1. Tente d'utiliser les effets EMPIRIQUEMENT appris via
       `cortex_action_effects.predict_effect(action)`. Si on a au moins
       MIN_SAMPLES exemples, on applique le delta moyen observé par champ.
    2. Sinon (cold start ou action peu pratiquée) → fallback sur les
       heuristiques hard-codées historiques (`_heuristic_predict`).

    Le résultat enregistre dans `prediction_source` quel mode a servi, ce qui
    permet de tracer la transition heuristique → empirique au fil du runtime.
    """
    obs = _observe_state()
    learned = None
    try:
        ae = _safe_import("cortex_action_effects")
        if ae:
            learned = ae.predict_effect(action) if action else None
    except Exception:
        learned = None
    if learned:
        # Applique le delta moyen appris pour chaque champ. Pour les champs
        # absents du modèle appris, on conserve la valeur observée (pas de
        # changement prédit).
        pred = dict(obs)
        for field, stats in learned.items():
            if isinstance(obs.get(field), (int, float)) and \
               isinstance(stats.get("mean"), (int, float)):
                pred[field] = obs[field] + stats["mean"]
        pred["action_taken"] = action
        pred["prediction_source"] = "empirical"
        # Pour audit : combien d'exemples ont servi (champ avec le plus d'exemples)
        n_max = max((s.get("n", 0) for s in learned.values()), default=0)
        pred["prediction_n_samples"] = n_max
        return pred
    pred = _heuristic_predict(action, obs)
    pred["action_taken"] = action
    pred["prediction_source"] = "heuristic_fallback"
    return pred


def measure_surprise() -> dict:
    """Surprise = écart entre dernière prédiction et observation actuelle."""
    state = _load_state()
    obs = _observe_state()
    last_pred = state.get("last_predicted_state")
    if not last_pred:
        # Premier cycle : pas de prédiction passée à comparer
        return {"ok": True, "surprise": 0.0, "reason": "first_cycle"}
    # Surprise = somme normalisée des écarts |obs - pred| pour les champs numériques
    diffs = {}
    surprise = 0.0
    n = 0
    for k, v in obs.items():
        if not isinstance(v, (int, float)): continue
        if k not in last_pred or not isinstance(last_pred[k], (int, float)): continue
        # Normalise par échelle attendue
        scale = {"cpu": 100, "ram": 100, "n_active": 10, "n_pulses_cum": 100,
                 "n_hebbian_cum": 100, "n_zombies": 100,
                 "compression_error": 1.0}.get(k, 10)
        delta = abs(v - last_pred[k]) / scale
        diffs[k] = round(delta, 4)
        surprise += delta
        n += 1
    if n > 0: surprise /= n
    return {"ok": True, "surprise": round(surprise, 4),
            "n_fields": n, "diffs": diffs,
            "observed": obs, "predicted": last_pred}


def expected_free_energy(action: str) -> float:
    """EFE pour une action candidate. Plus c'est BAS, mieux c'est.

    EFE = epistemic_value (info gain prédit) + pragmatic_value (utilité prédite).
    On veut MINIMISER l'opposé : maximiser le gain d'info + utilité.

    Formule simplifiée :
    EFE = - reduction_compression_error  (epistemic, on aime les actions qui apprennent)
        - utility_score                  (pragmatic, ça dépend du plan courant)
    """
    pred = _predict_state(action)
    obs = _observe_state()
    # Epistemic value = combien on s'attend à réduire la compression error
    epistemic = obs.get("compression_error", 0.5) - pred.get("compression_error", 0.5)
    # Pragmatic value = est-ce que cette action sert un goal du plan actuel ?
    pragmatic = 0.0
    pl = _safe_import("cortex_plan")
    if pl:
        try:
            d = pl.daily_plan()
            for g in d.get("goals", []):
                if action in g.get("actions", []):
                    pragmatic += 0.1
                    if not g.get("completed"): pragmatic += 0.05
        except Exception: pass
    # Personnalité : si openness élevée et action exploratoire → bonus
    pers = _safe_import("cortex_personality")
    if pers:
        try:
            big5 = pers.state().get("big5", {})
            if action in ("explore_graph", "map_knowledge", "look_around"):
                pragmatic += (big5.get("openness", 0.5) - 0.5) * 0.1
            if action in ("audit_ui", "propose_goal"):
                pragmatic += (big5.get("conscientiousness", 0.5) - 0.5) * 0.1
        except Exception: pass
    # Anti-répétition : si Cortex vient de prendre cette action plusieurs fois
    # d'affilée, malus pour diversifier. Sinon le scoring hardcodé piège la
    # politique sur l'action qui réduit le plus `compression_error` (toujours
    # `explore_graph` dans les effets actuels) — l'expert l'avait noté.
    repetition_penalty = 0.0
    try:
        s = _load_state()
        recent = (s.get("history_actions") or [])[-5:]
        n_recent_same = sum(1 for a in recent if a == action)
        if n_recent_same >= 3:
            # 3 occurrences/5 = malus 0.10 ; 4/5 = 0.20 ; 5/5 = 0.30
            repetition_penalty = 0.10 * (n_recent_same - 2)
    except Exception: pass

    # FRISTON EFE : KL(q_hyp||p) − predicted_log_lik calculée via le module
    # cortex_friston_belief. C'est la VRAIE EFE selon le formalisme Friston
    # (information gain pur en espace des modes cognitifs).
    # On l'AJOUTE à l'EFE-like comme bonus/malus modéré, pour ne pas
    # déstabiliser la politique apprise sur les outcomes.
    friston_efe = 0.0
    try:
        bs = _get_belief()
        if bs is not None:
            mode = ACTION_TO_MODE.get(action, "silent")
            # Evidence prédite : si l'action est cette mode, evidence=1.0
            pred_evidence = {mode: 1.0}
            # Predicted log-likelihood : utility liée à la pertinence courante
            # (si compression_error élevée, on gagne plus en explorant)
            pred_log_lik = 0.0
            if mode == "exploring":
                pred_log_lik = obs.get("compression_error", 0.5) * 0.5
            elif mode == "reflecting":
                pred_log_lik = 0.2
            friston_efe = bs.expected_free_energy_for(pred_evidence, pred_log_lik)
    except Exception: pass

    # EFE finale : combine l'EFE-like (heuristique outcomes) avec EFE Friston
    # (information gain belief). Poids modeste sur Friston pour ne pas dominer
    # alors que l'EFE-like est validée par les baselines.
    efe = (-epistemic - pragmatic + repetition_penalty
           + 0.15 * friston_efe)
    return round(efe, 5)


def _baseline_choice(name: str, actions: list[str], state: dict) -> str:
    """Politiques naïves pour benchmarker Cortex contre quelque chose de bête."""
    history = state.get("history_actions", [])
    if name == "random":
        return random.choice(actions)
    if name == "always_reflect":
        return "reflect" if "reflect" in actions else actions[0]
    if name == "always_explore":
        return "explore_graph" if "explore_graph" in actions else actions[0]
    if name == "round_robin":
        return actions[len(history) % len(actions)]
    if name == "last_best":
        # Action ayant donné le meilleur outcome_score historiquement
        scored: dict[str, list[float]] = {}
        for h in state.get("vfe_history", []):
            a = h.get("chosen")
            o = h.get("outcome_score")
            if a is not None and isinstance(o, (int, float)):
                scored.setdefault(a, []).append(o)
        if scored:
            ranked = sorted(scored.items(),
                            key=lambda kv: sum(kv[1]) / len(kv[1]),
                            reverse=True)
            for a, _ in ranked:
                if a in actions: return a
        return random.choice(actions)
    return random.choice(actions)


def select_action(actions: list[str] | None = None) -> dict:
    """Choisit l'action via le scoring EFE-like, et logue le choix de chaque baseline.

    NOTE — pourquoi un banc de baselines au lieu d'un seul random :
    comparer Cortex à un seul random est trompeur, parce que le score Cortex est
    défini sur la même fonction qu'on minimise — Cortex bat presque toujours
    random sur les *prédictions* EFE. Le vrai test, c'est :
    - chaque baseline naïve (random, always-X, round-robin, last-best) propose
      AUSSI son action
    - on note les actions de tout le monde
    - au cycle SUIVANT on évalue l'*outcome observé* (delta compression_error,
      delta n_active) du choix Cortex et on le compare à ce qu'aurait donné
      chaque baseline (en utilisant le modèle de prédiction comme proxy)
    """
    actions = actions or ["audit_ui", "explore_graph", "map_knowledge",
                          "discovery_report", "reflect", "propose_goal",
                          "look_around", "update_claude_context", "silent"]
    state = _load_state()
    # Score chaque action via EFE
    scored = [(a, expected_free_energy(a)) for a in actions]
    scored.sort(key=lambda x: x[1])  # plus bas = mieux
    chosen = scored[0][0]
    chosen_efe = scored[0][1]
    # Choix de chaque baseline naïve
    baseline_choices = {
        name: _baseline_choice(name, actions, state)
        for name in ("random", "always_reflect", "always_explore",
                     "round_robin", "last_best")
    }
    # Comparaison legacy à random sur l'EFE prédite (conservée pour compat)
    random_action = baseline_choices["random"]
    random_efe = expected_free_energy(random_action)
    if chosen_efe < random_efe - 0.001:
        comparison = "better_than_random"
    elif chosen_efe > random_efe + 0.001:
        comparison = "worse_than_random"
    else:
        comparison = "equal_to_random"
    return {
        "ok": True,
        "ts": _now(),
        "chosen_action": chosen,
        "chosen_efe": chosen_efe,
        "random_action": random_action,
        "random_efe": random_efe,
        "comparison": comparison,
        "baseline_choices": baseline_choices,
        "ranked": [{"action": a, "efe": e} for a, e in scored[:5]],
    }


def _outcome_score(pre_obs: dict, post_obs: dict) -> float:
    """Score de qualité d'outcome observé : réduction compression_error +
    activations gagnées (capées). Plus c'est haut, mieux c'est."""
    score = 0.0
    if isinstance(pre_obs.get("compression_error"), (int, float)) and \
       isinstance(post_obs.get("compression_error"), (int, float)):
        # réduction de compression_error → bon
        score += (pre_obs["compression_error"] - post_obs["compression_error"]) * 10
    if isinstance(pre_obs.get("n_active"), (int, float)) and \
       isinstance(post_obs.get("n_active"), (int, float)):
        delta = post_obs["n_active"] - pre_obs["n_active"]
        # Activations gagnées (capées à 1.0 pour éviter qu'une explosion domine)
        score += max(min(delta / 5.0, 1.0), -0.5)
    if isinstance(pre_obs.get("n_pulses_cum"), (int, float)) and \
       isinstance(post_obs.get("n_pulses_cum"), (int, float)):
        delta = post_obs["n_pulses_cum"] - pre_obs["n_pulses_cum"]
        # Pulses gagnés = un peu d'activité, bon signal mais marginal
        score += max(min(delta / 20.0, 0.5), 0.0)
    return round(score, 4)


def _proxy_outcome_for_baseline(baseline_action: str, pre_obs: dict) -> float:
    """Approxime l'outcome qu'aurait eu une baseline en utilisant la prédiction.

    Limite assumée : on ne peut pas exécuter contrefactuellement chaque baseline.
    On utilise donc le modèle de prédiction `_predict_state` comme proxy. Si le
    modèle est mauvais, ça pénalise Cortex de la même façon que les baselines,
    donc le ratio reste informatif pour comparer les politiques entre elles.
    """
    pred = _predict_state(baseline_action)
    return _outcome_score(pre_obs, pred)


def _execute_action(action: str) -> dict:
    """Exécute *réellement* l'action choisie via cortex_emergence.TOOLS.

    Sans ça, drive_step ne déclencherait aucun side-effect observable et
    l'apprentissage des effets dans cortex_action_effects convergerait vers 0.
    Branché ici plutôt que duppliqué : cortex_emergence définit déjà la table
    canonique des outils (`explore_graph`, `audit_ui`, etc.).
    """
    if not action or action == "silent":
        return {"ok": True, "result": "silent (no-op)", "executed": False}
    try:
        em = _safe_import("cortex_emergence")
        if not em or not hasattr(em, "TOOLS"):
            return {"ok": False, "executed": False,
                    "result": "cortex_emergence.TOOLS unavailable"}
        tool = em.TOOLS.get(action)
        if not tool:
            return {"ok": False, "executed": False,
                    "result": f"no tool for action {action}"}
        out = tool() or {}
        out["executed"] = True
        return out
    except Exception as e:
        return {"ok": False, "executed": False,
                "result": f"executor exception: {e}"[:200]}


def drive_step(execute: bool = False) -> dict:
    """UN cycle complet : observe + évalue l'outcome du cycle précédent +
    nouvelle décision + logue le choix de chaque baseline pour évaluation au
    cycle suivant.

    Args:
        execute: si True, déclenche réellement l'action choisie via
                 `cortex_emergence.TOOLS`. Sans ça, drive_step est en mode
                 "scoring only" — utile pour les tests, dangereux en prod
                 si on veut un système qui APPREND ses effets réels.
                 La boucle de production (cortex_emergence._loop ou un
                 watchdog) doit appeler `drive_step(execute=True)`.
    """
    state = _load_state()
    surprise = measure_surprise()
    current_obs = surprise.get("observed") or _observe_state()

    # 1) Si un cycle précédent a laissé une éval pendante, on calcule :
    #    - cortex_outcome_observed  : delta réel post-action (mesure honnêteté
    #                                 du modèle prédictif vs réalité)
    #    - cortex_outcome_proxy     : delta prédit par le modèle, apples-to-apples
    #                                 avec les baselines pour comparer les politiques
    #    Les win/losses contre baselines utilisent le proxy (apples-to-apples).
    #    Le observed vs proxy donne la dérive prédiction-vs-réalité (anti-fake bonus).
    eval_report = None
    pending = state.get("pending_eval")
    if pending and isinstance(pending, dict):
        pre_obs = pending.get("pre_obs") or {}
        cortex_action = pending.get("chosen_action")
        cortex_outcome_observed = _outcome_score(pre_obs, current_obs)
        cortex_outcome_proxy = _proxy_outcome_for_baseline(cortex_action, pre_obs) \
            if cortex_action else 0.0
        baselines_state = state.setdefault("baselines", {})
        per_baseline = {}
        for bname, baction in (pending.get("baseline_choices") or {}).items():
            b_proxy = _proxy_outcome_for_baseline(baction, pre_obs)
            per_baseline[bname] = {"action": baction,
                                   "outcome_proxy": b_proxy}
            entry = baselines_state.setdefault(
                bname, {"wins": 0, "losses": 0, "ties": 0,
                        "outcome_score_sum": 0.0})
            # Apples-to-apples : Cortex proxy vs baseline proxy
            if cortex_outcome_proxy > b_proxy + 0.01:
                entry["wins"] = entry.get("wins", 0) + 1
            elif cortex_outcome_proxy < b_proxy - 0.01:
                entry["losses"] = entry.get("losses", 0) + 1
            else:
                entry["ties"] = entry.get("ties", 0) + 1
            entry["outcome_score_sum"] = entry.get("outcome_score_sum", 0.0) + b_proxy
        state["cortex_outcome_score_sum"] = (
            state.get("cortex_outcome_score_sum", 0.0) + cortex_outcome_proxy)
        state["cortex_outcome_observed_sum"] = (
            state.get("cortex_outcome_observed_sum", 0.0) + cortex_outcome_observed)
        state["n_outcome_evaluated"] = state.get("n_outcome_evaluated", 0) + 1
        # Tag le dernier vfe_history avec le score réel (utile pour last_best)
        vh = state.get("vfe_history", [])
        if vh:
            vh[-1]["outcome_score"] = cortex_outcome_observed
            vh[-1]["outcome_proxy"] = cortex_outcome_proxy
        eval_report = {
            "cortex_action": cortex_action,
            "cortex_outcome_proxy": cortex_outcome_proxy,
            "cortex_outcome_observed": cortex_outcome_observed,
            "prediction_error": round(
                abs(cortex_outcome_proxy - cortex_outcome_observed), 4),
            "baselines": per_baseline,
        }
        # APPRENTISSAGE : enregistrer (pre, action, post) pour que cortex_action_effects
        # puisse construire un modèle empirique des effets. Au bout de
        # MIN_SAMPLES exemples par action, _predict_state utilisera ces effets
        # appris à la place des heuristiques hard-codées.
        try:
            ae = _safe_import("cortex_action_effects")
            if ae and cortex_action:
                ae.record_observation(cortex_action, pre_obs, current_obs)
        except Exception: pass

        # JEPA TRAIN STEP : apprend la transition latente (pre, action) → post
        # dans l'espace des embeddings. Pas le formalisme LeCun complet mais
        # contrat JEPA respecté : online encoder + target EMA + predictor.
        try:
            if cortex_action:
                jepa = _get_jepa()
                if jepa is not None:
                    aid = _action_id(cortex_action)
                    pre_vec = _obs_to_vector(pre_obs)
                    post_vec = _obs_to_vector(current_obs)
                    jepa_rep = jepa.train_step(pre_vec, aid, post_vec)
                    eval_report["jepa_loss"] = round(jepa_rep.get("loss", 0), 5)
                    eval_report["jepa_n_train_steps"] = jepa_rep.get("n_train_steps")
                    # Save tous les 10 steps pour ne pas thrash le disque
                    if jepa_rep.get("n_train_steps", 0) % 10 == 0:
                        try: jepa.save()
                        except Exception: pass
        except Exception as e:
            eval_report["jepa_err"] = str(e)[:100]

        # BELIEF UPDATE : observe l'evidence dérivée de l'action prise.
        # Si action=explore_graph → evidence forte pour mode "exploring".
        try:
            if cortex_action:
                bs = _get_belief()
                if bs is not None:
                    mode = ACTION_TO_MODE.get(cortex_action, "silent")
                    # Evidence pondérée par succès de l'exécution réelle
                    weight = 1.0
                    exec_rep = pending.get("execution") if isinstance(pending, dict) else None
                    if exec_rep and not exec_rep.get("ok"):
                        weight = 0.3  # evidence affaiblie si exec a échoué
                    bs.observe({mode: weight})
                    eval_report["belief_kl"] = round(bs.kl_to_prior(), 4)
                    eval_report["belief_n_obs"] = bs.n_observations
                    # Save tous les 5 obs
                    if bs.n_observations % 5 == 0:
                        try: bs.save()
                        except Exception: pass
        except Exception as e:
            eval_report["belief_err"] = str(e)[:100]

    # 2) Nouvelle sélection d'action
    selection = select_action()
    vfe = surprise.get("surprise", 0)
    new_prediction = _predict_state(selection["chosen_action"])
    state["last_observed_state"] = current_obs
    state["last_predicted_state"] = new_prediction
    state["n_steps"] = state.get("n_steps", 0) + 1
    hist = state.get("surprise_history", [])
    hist.append({"ts": _now(), "surprise": vfe})
    state["surprise_history"] = hist[-30:]
    vfe_history = state.get("vfe_history", [])
    vfe_history.append({"ts": _now(), "vfe": vfe,
                        "chosen": selection["chosen_action"],
                        "comparison": selection["comparison"]})
    state["vfe_history"] = vfe_history[-50:]
    if selection["comparison"] == "better_than_random":
        state["n_better_than_random"] = state.get("n_better_than_random", 0) + 1
    elif selection["comparison"] == "worse_than_random":
        state["n_worse_than_random"] = state.get("n_worse_than_random", 0) + 1
    else:
        state["n_equal_to_random"] = state.get("n_equal_to_random", 0) + 1
    # 3) Pose l'éval pendante pour le prochain cycle
    state["pending_eval"] = {
        "pre_obs": current_obs,
        "chosen_action": selection["chosen_action"],
        "baseline_choices": selection.get("baseline_choices", {}),
        "ts": _now(),
    }
    # 4) Historique des actions Cortex (pour round_robin baseline)
    actions_hist = state.get("history_actions", [])
    actions_hist.append(selection["chosen_action"])
    state["history_actions"] = actions_hist[-100:]

    # 5) EXÉCUTION RÉELLE de l'action choisie (si execute=True)
    # Avant ce branchement, drive_step ne déclenchait aucun side-effect → les
    # outcomes observés convergeaient vers 0 et l'apprentissage des effets
    # dans cortex_action_effects était vide. En branchant cortex_emergence.TOOLS
    # comme exécuteur, l'action a un effet réel sur l'état (n_active monte si
    # explore_graph appelle wander_once, etc.) et le cycle suivant pourra
    # mesurer le vrai delta.
    exec_report = None
    if execute:
        exec_report = _execute_action(selection["chosen_action"])
        # On logue dans pending_eval pour traçabilité
        state["pending_eval"]["execution"] = exec_report

    _save_state(state)
    rep = {
        "ok": True,
        "ts": _now(),
        "surprise": vfe,
        "chosen_action": selection["chosen_action"],
        "chosen_efe": selection["chosen_efe"],
        "comparison_to_random": selection["comparison"],
        "baseline_choices": selection.get("baseline_choices", {}),
        "outcome_eval_prev_cycle": eval_report,
        "execution": exec_report,
        "n_steps": state["n_steps"],
    }
    _log_event({"type": "drive_step", **{k: v for k, v in rep.items() if k != "ts"}})
    return rep


def stats() -> dict:
    s = _load_state()
    history = s.get("surprise_history", [])
    if len(history) >= 2:
        early = sum(h["surprise"] for h in history[:5]) / max(1, len(history[:5]))
        late = sum(h["surprise"] for h in history[-5:]) / max(1, len(history[-5:]))
        surprise_trend = late - early  # négatif = en baisse = bon
    else:
        early = late = surprise_trend = None
    n_total = (s.get("n_better_than_random", 0) +
               s.get("n_worse_than_random", 0) +
               s.get("n_equal_to_random", 0))
    # Win-rate Cortex contre chaque baseline naïve, sur les outcomes observés
    baselines = s.get("baselines", {}) or {}
    n_eval = s.get("n_outcome_evaluated", 0) or 0
    cortex_avg_outcome = (s.get("cortex_outcome_score_sum", 0.0) / n_eval
                          if n_eval > 0 else None)
    cortex_avg_observed = (s.get("cortex_outcome_observed_sum", 0.0) / n_eval
                           if n_eval > 0 else None)
    baseline_summary = {}
    for bname, b in baselines.items():
        nw = b.get("wins", 0); nl = b.get("losses", 0); nt = b.get("ties", 0)
        ntot = nw + nl + nt
        avg_out = (b.get("outcome_score_sum", 0.0) / ntot) if ntot > 0 else None
        baseline_summary[bname] = {
            "wins": nw, "losses": nl, "ties": nt, "n": ntot,
            "win_rate": round(nw / ntot, 3) if ntot > 0 else None,
            "avg_outcome_proxy": round(avg_out, 4) if avg_out is not None else None,
        }
    # JEPA + BeliefState diagnostics LIVE (intégration prod, mission AUTONOMIE 3)
    jepa_diag = None
    belief_diag = None
    try:
        jepa = _get_jepa()
        if jepa is not None:
            jepa_diag = jepa.diagnostic()
    except Exception: pass
    try:
        bs = _get_belief()
        if bs is not None:
            belief_diag = {
                "n_observations": bs.n_observations,
                "kl_to_prior": round(bs.kl_to_prior(), 4),
                "last_vfe": (round(bs.last_vfe, 4) if bs.last_vfe is not None else None),
                "q_dist": {m: round(v, 3) for m, v in zip(bs.modes, bs.q_dist())},
            }
    except Exception: pass

    return {
        "n_steps": s.get("n_steps", 0),
        "n_better_than_random": s.get("n_better_than_random", 0),
        "n_worse_than_random": s.get("n_worse_than_random", 0),
        "n_equal_to_random": s.get("n_equal_to_random", 0),
        "fraction_better_than_random": (s.get("n_better_than_random", 0) / max(1, n_total)),
        "early_avg_surprise": round(early, 4) if early is not None else None,
        "late_avg_surprise": round(late, 4) if late is not None else None,
        "surprise_trend": round(surprise_trend, 4) if surprise_trend is not None else None,
        "is_learning": (surprise_trend is not None and surprise_trend < 0),
        # Banc de baselines (apples-to-apples sur prédiction)
        "n_outcome_evaluated": n_eval,
        "cortex_avg_outcome_proxy": (round(cortex_avg_outcome, 4)
                                      if cortex_avg_outcome is not None else None),
        "cortex_avg_outcome_observed": (round(cortex_avg_observed, 4)
                                         if cortex_avg_observed is not None else None),
        "model_calibration_note": "outcome_observed << outcome_proxy → modèle "
                                   "de prédiction trop optimiste (effets d'action "
                                   "sur-évalués) ; outcome_observed ≈ proxy → bonne "
                                   "calibration",
        "vs_baselines": baseline_summary,
        # JEPA v2 (encoder + target EMA + predictor) — appris sur les transitions
        # (pre_obs, action, post_obs) capturées par drive_step
        "jepa": jepa_diag,
        # BeliefState (Friston posterior + KL) — alimenté par chaque action
        # via ACTION_TO_MODE mapping
        "belief": belief_diag,
    }


def self_test() -> dict:
    tests = []
    obs = _observe_state()
    tests.append({"name": "observe_state",
                  "ok": isinstance(obs, dict) and "ts" in obs,
                  "n_fields": len(obs)})
    pred = _predict_state("explore_graph")
    tests.append({"name": "predict_state",
                  "ok": "compression_error" in pred,
                  "predicted_action": pred.get("action_taken")})
    surprise = measure_surprise()
    tests.append({"name": "measure_surprise",
                  "ok": "surprise" in surprise,
                  "value": surprise.get("surprise")})
    sel = select_action()
    tests.append({"name": "select_action",
                  "ok": sel.get("chosen_action") is not None and "comparison" in sel,
                  "chosen": sel.get("chosen_action"),
                  "comparison": sel.get("comparison")})
    step = drive_step()
    tests.append({"name": "drive_step",
                  "ok": step.get("ok") and "comparison_to_random" in step,
                  "comparison": step.get("comparison_to_random")})
    s = stats()
    tests.append({"name": "stats",
                  "ok": "n_steps" in s,
                  "n_steps": s.get("n_steps"),
                  "is_learning": s.get("is_learning")})
    return {"ok": all(t["ok"] for t in tests), "tests": tests}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "step"
    if cmd == "step":
        print(json.dumps(drive_step(), indent=2, ensure_ascii=False))
    elif cmd == "stats":
        print(json.dumps(stats(), indent=2, ensure_ascii=False))
    elif cmd == "select":
        print(json.dumps(select_action(), indent=2, ensure_ascii=False))
    elif cmd == "surprise":
        print(json.dumps(measure_surprise(), indent=2, ensure_ascii=False))
    elif cmd == "test":
        print(json.dumps(self_test(), indent=2, ensure_ascii=False))
    else:
        print("Usage: cortex_active_inference.py {step|stats|select|surprise|test}")
