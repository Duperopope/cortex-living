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
| Active Inference (Friston complet) | **implémenté** | Deux niveaux : (a) `cortex_active_inference.py` — score EFE-like + banc baselines + outcomes observés. (b) `cortex_friston_belief.py` — posterior Dirichlet sur modes cognitifs, KL(q\|\|p) calculée explicitement, VFE = KL − accuracy formula complète, EFE par action via simulation. self_test passe 4/4 critères (KL≥0, KL augmente avec evidence, VFE consistent, actions différenciées) |
| Big5 OCEAN                         | implémenté      | `cortex_personality.py` — modulation des scores d'action                    |
| Curiosity Drive (Schmidhuber)      | implémenté      | `cortex_curiosity.py` — proxy compression delta                             |
| JEPA / Free Energy (LeCun)         | **implémenté** | `cortex_jepa_v2.py` — encoder online + target encoder EMA (τ=0.99) + predictor + loss MSE en espace latent + anti-collapse check. Pas le formalisme LeCun complet (pas de ViT/images) mais structure JEPA respectée. self_test : loss baisse de 0.093 → 0.040 (-57%) sur 200 steps synthétiques, online ≠ target confirmé |
| TurboQuant                         | partiel         | `cortex_quantize.py` — rotation+8bit maison, pas l'algo Google complet      |
| FrugalGPT cascade                  | implémenté      | `llm_router.py` — cascade avec seuils confidence                            |
| Self-Consistency vote              | implémenté      | `llm_router.py` — Jaccard sur k=3                                           |
| Anti-fake — coherence temporelle   | implémenté      | `cortex_anti_fake.py::test_coherence_temporal`                              |
| Anti-fake — questions état interne | implémenté      | `cortex_anti_fake.py::test_internal_state_dont_know` (interroge logs réels) |
| Anti-fake — internal state used    | implémenté      | `cortex_anti_fake.py::test_internal_state_used` (logs compose_response)     |
| Anti-fake — banc baselines         | implémenté      | `cortex_active_inference.py::stats()` — win-rate vs 5 baselines naïves      |
| Anti-fake — plan vs réalisé        | implémenté      | `cortex_hjepa.py::compare_realised` — H-JEPA L1 5-step (rapport sur disque) |
| Anti-fake — questions état interne étendu | implémenté | 7 ground truths : surprise_avg_last5, ai_n_steps, ai_n_outcome_evaluated, last_chosen_action, n_active_nodes, cum_hebbian, last_anti_fake_score, empirical_ratio, disk_C_percent |
| Décision autonome                  | implémenté      | `cortex_emergence._emergence_loop` appelle `drive_step(execute=True)` toutes les ~5 min : measure_surprise → eval outcome cycle précédent → select_action via EFE-like → execute via TOOLS → record (pre,action,post). Boucle complète testée en runtime |
| Conscience corporelle              | implémenté      | `cortex_homeostasis.py` — psutil CPU/RAM/disques/GPU/network/battery        |
| Vision sémantique                  | implémenté      | `cortex_vision._try_lm_vision` + auto-detect modèle VL chargé. `cortex_dialogue` : sticky 90s, fallback `lm_studio_sticky` quand capture courante échoue, `get_perception_context()` API publique exposant `vision_available`/`age_s`/`method` |
| Self-dev autonome                  | implémenté + défensif | `cortex_self_dev.propose_and_apply` : pipeline e2e complet (context → LLM proposal → patch parser → guardrail) testé en live. Test session : guardrail a CORRECTEMENT refusé un goal mal nommé (`planned paths not explicitly named`) ; smoke check post = 7/7 vert. Le système refuse les patches non sécurisés — preuve que les guardrails fonctionnent. Voir `cortex_self_dev_guardrails.json` pour la config |
| "Cerveau vivant" / "raisonne"      | métaphorique-doc | propagation d'activation + scoring + apprentissage empirique d'effets + belief posterior. Le terme "vivant" reste métaphorique mais l'évolution est mesurable via `cum_hebbian_ticks` persisté + JEPA loss qui baisse + KL posterior qui augmente avec evidence |
| "IAG"                              | score-interne-déclassifié | `cortex_iag_test.run_iag_test()` retourne `is_iag=False` toujours (preuve externe requise) + `maturity` ∈ {prototype, agent_local, agent_adaptatif, agent_autonome, agi_non_prouvé} + `bottlenecks[]` actionnables. **Pas de prétention AGI dans le code** |
| 3D viz système                     | implémenté      | Endpoint `/api/cortex/system_topology` : nodes (modules + role + status import), edges (graphe d'appel), badges live (action_effects empirical_ratio, body_health severity, vision available/method, smoke verdict, IAG raw/calibrated, safety_check verdict). Distinct du graphe Obsidian sur `/api/state` |
| 3D viz graphe sémantique notes    | métaphorique    | La viz `/gpu` actuelle (`brain_gpu.html`) montre les notes Obsidian qui s'activent. Sam peut combiner avec `/api/cortex/system_topology` pour vue système |
| Apprentissage des effets d'action  | implémenté v1   | `cortex_action_effects.py` — moyenne empirique des deltas observés, fenêtre glissante 30 ex. ; remplace progressivement les heuristiques hardcodées dans `_predict_state` (mode `empirical` quand n>=8/action) |
| Boucle décision unifiée            | implémenté      | `cortex_emergence._emergence_loop` appelle `drive_step(execute=True)` — scoring EFE + exécution réelle via TOOLS + apprentissage des effets en un seul cycle |
| Bridge Claude Code (contexte vivant) | implémenté    | `cortex_claude_code.py` génère `.cortex-claude-context.md` ; `CLAUDE.md` du repo Paperclip pointe dessus ; refresh auto tous les 6 cycles dans la boucle |
| CI locale bloquante                | implémenté      | `cortex_smoke_check.py` : compile + import + self_test sur 5 modules cœur ; appelé en pre-flight par `cortex_publishing.update()` → abort si fail. Indépendant de GitHub Actions (quota). Le workflow `smoke.yml` reste dispo pour quand le compte GH sera débloqué |
| Auto-détection modèles LM Studio   | implémenté      | `cortex_dialogue._detect_brain_llm_model()` + `cortex_vision._detect_vision_model()` interrogent `/v1/models` au lieu de hardcoder. Évite les fallbacks silencieux quand un modèle attendu est unloaded |
| Vision shortcut (court-circuit brain LLM) | implémenté | Pour les questions vision SIMPLES (`tu vois quoi`, `qu'est-ce que je fais`, etc.), `compose_response` renvoie directement la description du modèle VL, sans repasser par le brain LLM (qui censurait le contenu vision). Pour les questions vision COMPLEXES, garde-fou anti-censure injecté dans le meta_prompt |
| Calibration IAG honnête            | implémenté      | `cortex_iag_test._calibration_factor()` déflate le score brut selon (a) ratio actions en mode `learned` vs `fallback`, (b) erreur prédiction-vs-réalité, (c) fake_confident_rate anti-fake. Évite les scores 90+/100 marqués « improbable » par Cortex lui-même |
| Memory hygiene auditable           | implémenté      | `cortex_memory_audit.audit()` détecte contradictions, paths obsolètes, endpoints incohérents, duplicatas. `propose_corrections()` génère des fixes EN DRY_RUN par défaut — Sam valide avant action |
| Publish safety check               | implémenté      | `cortex_publish_safety_check.scan()` scanne le mirror avant push : 9 patterns (API_KEY, TOKEN, COOKIE, PRIVATE_KEY, LOCAL_PATH, OAUTH_LINK, FORBIDDEN_FILE...) + self_test injecte fake secret et valide détection. Branché en pre-flight de `cortex_publishing.update()` — bloque push si n_blockers>0 |
| Action effects v2 (calibration)   | implémenté      | `cortex_action_effects.stats()` retourne empirical_ratio, prediction_error_avg_global (sur ACTION_TARGET_FIELDS uniquement, exclut cumulatifs+env_noise), per_action.observability {high,medium,low}, top_overoptimistic, top_reliable. Bug pre-fix : prediction_error=9.49 à cause de cumulatifs ; post-fix : 2.45 cohérent |
| Body health metrics avant/après    | implémenté      | `cortex_body_health.auto_execute_authorized` mesure `effective_freed_gb` réel (delta psutil pre/post), `body_health_status()` = snapshot machine-readable + `verify_junctions()` (locale-independent via PowerShell `LinkType`). Audit `.cortex-body-health-last.json` |
| Vision sticky context              | implémenté      | `cortex_dialogue.get_perception_context()` expose vision_available/age_s/method. Sticky 90s + fallback `lm_studio_sticky` si capture échoue mais frame récente <90s dispo |
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
