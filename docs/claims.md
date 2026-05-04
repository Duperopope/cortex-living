# Claims — niveaux d'implémentation

Ce document liste **chaque claim** du README avec un niveau honnête et le
fichier de preuve. Trois niveaux :

- **implémenté** : le code tourne et la fonction principale fait ce qu'annonce
- **inspiré** : l'idée vient d'un papier mais l'implémentation est une
  heuristique simplifiée — pas le formalisme complet
- **partiel / aspirationnel** : prototype incomplet, à fiabiliser

| Claim                              | Niveau          | Preuve                                                                      |
|------------------------------------|-----------------|-----------------------------------------------------------------------------|
| Spreading Activation Theory        | implémenté      | `code/brain/cortex_activation.py` — `activate()` + persistance disque + tests |
| Hebbian Learning                   | implémenté      | `cortex_activation.py` — edges renforcées au co-activate, top-edges retournés |
| Homeostasis                        | implémenté      | `cortex_homeostasis.py` — vitals + actions graduelles                       |
| Active Inference (Friston complet) | **inspiré**     | `cortex_active_inference.py` — score EFE-like simplifié, effets hard-codés |
| Big5 OCEAN                         | implémenté      | `cortex_personality.py` — modulation des scores d'action                    |
| Curiosity Drive (Schmidhuber)      | implémenté      | `cortex_curiosity.py` — proxy compression delta                             |
| JEPA / Free Energy (LeCun)         | partiel         | `cortex_world_model.py` — mini world-model NumPy entraîné                   |
| TurboQuant                         | partiel         | `cortex_quantize.py` — rotation+8bit maison, pas l'algo Google complet      |
| FrugalGPT cascade                  | implémenté      | `llm_router.py` — cascade avec seuils confidence                            |
| Self-Consistency vote              | implémenté      | `llm_router.py` — Jaccard sur k=3                                           |
| Anti-fake — coherence temporelle   | implémenté      | `cortex_anti_fake.py::test_coherence_temporal`                              |
| Anti-fake — questions état interne | implémenté      | `cortex_anti_fake.py::test_internal_state_dont_know` (interroge logs réels) |
| Anti-fake — internal state used    | implémenté      | `cortex_anti_fake.py::test_internal_state_used` (logs compose_response)     |
| Anti-fake — banc baselines         | implémenté      | `cortex_active_inference.py::stats()` — win-rate vs 5 baselines naïves      |
| Anti-fake — plan vs réalisé        | partiel         | `cortex_hjepa.py::compare_realised` — H-JEPA L1 5-step                      |
| Décision autonome                  | partiel         | boucle `cortex_emergence.py`, scoring heuristique, pas un agent RL appris   |
| Conscience corporelle              | implémenté      | `cortex_homeostasis.py` — psutil CPU/RAM/disques/GPU/network/battery        |
| Vision sémantique                  | aspirationnel   | nécessite chargement d'un modèle vision dans LM Studio (qwen2-vl, llava…)  |
| Self-dev autonome                  | aspirationnel   | `cortex_self_dev.py` existe, pas testé end-to-end avec commit + tests verts |
| "Cerveau vivant" / "raisonne"      | métaphorique    | propagation d'activation + scoring d'actions, pas un raisonnement déductif  |
| "IAG"                              | aspirationnel   | score interne 0–100, pas une mesure externe — voir limites du score IAG     |
| Apprentissage des effets d'action  | implémenté v1   | `cortex_action_effects.py` — moyenne empirique des deltas observés, fenêtre glissante 30 ex. ; remplace progressivement les heuristiques hardcodées dans `_predict_state` (mode `empirical` quand n>=8/action) |
| Boucle décision unifiée            | implémenté      | `cortex_emergence._emergence_loop` appelle `drive_step(execute=True)` — scoring EFE + exécution réelle via TOOLS + apprentissage des effets en un seul cycle |
| Bridge Claude Code (contexte vivant) | implémenté    | `cortex_claude_code.py` génère `.cortex-claude-context.md` ; `CLAUDE.md` du repo Paperclip pointe dessus ; refresh auto tous les 6 cycles dans la boucle |
| CI locale bloquante                | implémenté      | `cortex_smoke_check.py` : compile + import + self_test sur 5 modules cœur ; appelé en pre-flight par `cortex_publishing.update()` → abort si fail. Indépendant de GitHub Actions (quota). Le workflow `smoke.yml` reste dispo pour quand le compte GH sera débloqué |
| Auto-détection modèles LM Studio   | implémenté      | `cortex_dialogue._detect_brain_llm_model()` + `cortex_vision._detect_vision_model()` interrogent `/v1/models` au lieu de hardcoder. Évite les fallbacks silencieux quand un modèle attendu est unloaded |
| Vision shortcut (court-circuit brain LLM) | implémenté | Pour les questions vision SIMPLES (`tu vois quoi`, `qu'est-ce que je fais`, etc.), `compose_response` renvoie directement la description du modèle VL, sans repasser par le brain LLM (qui censurait le contenu vision). Pour les questions vision COMPLEXES, garde-fou anti-censure injecté dans le meta_prompt |
| Calibration IAG honnête            | implémenté      | `cortex_iag_test._calibration_factor()` déflate le score brut selon (a) ratio actions en mode `learned` vs `fallback`, (b) erreur prédiction-vs-réalité, (c) fake_confident_rate anti-fake. Évite les scores 90+/100 marqués « improbable » par Cortex lui-même |
| Memory hygiene auditable           | implémenté      | `cortex_memory_audit.audit()` détecte contradictions, paths obsolètes, endpoints incohérents, duplicatas. `propose_corrections()` génère des fixes EN DRY_RUN par défaut — Sam valide avant action |
| Modules périodiques branchés       | implémenté      | `cortex_emergence._emergence_loop` appelle désormais `body_health` (12 cycles, ~1h), `memory_audit` (144 cycles, 12h), `anti_fake` (288 cycles, 24h) automatiquement. Avant : `cortex_body_health` existait mais n'était jamais déclenché → C: à 97% sans alerte |
| 3D viz : graphe sémantique notes   | métaphorique    | La visualisation 3D (`brain_gpu.html`) montre **les notes Obsidian** du vault qui s'activent (Spreading Activation), PAS les modules Cortex eux-mêmes ni leurs états. C'est une vue "graphe de connaissance" pas "topologie système". Voir docstring de `cortex_publishing.py` pour la liste des modules réels |

## Méthodologie anti-fake recommandée pour auditer

1. **Cloner le repo, lancer la CI** : `pytest` ou `python -m py_compile code/brain/*.py`
2. **Vérifier `examples/session-001/`** : capture d'une session live anonymisée
   avec `state.before.json`, `state.after.json`, `decisions.jsonl`,
   `anti_fake_report.json`
3. **Lire `docs/anti-fake.md`** : 5 tests mesurables, pondération transparente
4. **Comparer les métriques `docs/state.json` au code de `cortex_*.py`** : si un
   chiffre n'apparaît dans aucun fichier d'état → suspect

## Ce qu'on **ne** prétend **pas**

- Pas une AGI au sens DeepMind / OpenAI / Anthropic
- Pas un système qui s'auto-modifie (le `cortex_self_dev.py` est expérimental,
  garde-fous + sandbox, jamais commit auto sans tests verts manuels)
- Pas un agent RL entraîné — c'est du scoring heuristique
- Pas une preuve de conscience — c'est un système avec un modèle d'auto-état
  qui répond à des questions sur cet auto-état
