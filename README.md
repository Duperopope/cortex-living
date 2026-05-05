# Cortex — prototype expérimental de boucle cognitive locale

> Dernière mise à jour : `2026-05-05T03:30:38` (auto-généré)

Cortex est un **prototype expérimental** de boucle cognitive locale
construite sur le projet Paperclip. Il combine capture webcam, audio, mémoire
épisodique/sémantique, propagation d'activation (Collins & Loftus, 1975), et un
score d'action **inspiré** d'Active Inference (Friston, 2010, version simplifiée
— pas le formalisme complet).

> Statut : prototype auditable. Voir [docs/claims.md](docs/claims.md) pour la
> liste exacte de ce qui est implémenté vs inspiré vs aspirationnel.

## État cognitif courant

| Métrique               | Valeur                                       |
|------------------------|----------------------------------------------|
| Nœuds graphe pensée    | **1782**                    |
| Arêtes sémantiques     | **182753**                    |
| Densité                | **0.1152**                    |
| Nœuds actifs           | **42** (décroissance τ=60 s) |
| Hebbian cumulé         | **87.18** (apprentissage) |
| Zones d'ignorance      | **0** (besoin de ponts) |

### Composition du graphe
- `claude_memory` : 23 nœuds
- `semantic` : 1659 nœuds
- `episodic` : 100 nœuds

## Corps (homeostasis)

- CPU : **32.6%**
- RAM : **88.3%**
- Disques surveillés : **5**
- GPU : —

Cortex maintient ses signes vitaux dans une plage viable (Cannon 1932,
Ashby 1960). Au-dessus de 90 % d'occupation disque il propose un déménagement
vers un disque plus libre.

## Boucle de décision (Active-Inference-inspired) — unifiée

Toutes les ~5 minutes, **un seul appel** `cortex_active_inference.drive_step(execute=True)`
réalise le cycle complet : score EFE des actions, sélection, exécution réelle
via `cortex_emergence.TOOLS`, enregistrement des deltas observés
(`cortex_action_effects.record_observation`) pour apprentissage. Ce
**n'est pas** une rotation déterministe ni un wrapper LLM nu, mais ce
**n'est pas non plus** le formalisme Active Inference complet — c'est une
heuristique inspirée qui apprend ses effets au fil des cycles :

1. **Score Expected-Free-Energy-like** — combine valeur épistémique
   (réduction prédite de `compression_error`) et valeur pragmatique (utilité
   par rapport au plan courant). Effets d'action initialement hardcodés,
   **désormais remplacés par les deltas empiriques** quand l'agent a observé
   ≥ 8 exemples de l'action (`cortex_action_effects.predict_effect`)
2. **Modulation Big5** — openness booste les actions exploratoires,
   conscientiousness booste les actions d'audit
3. **Bonus curiosité** (Schmidhuber, 1991) — si `compression_error` en hausse,
   bonus pour les actions exploratoires
4. **Banc de baselines naïves** — chaque cycle, on logue le choix de
   `random`, `always-reflect`, `always-explore`, `round-robin`, `last-best`,
   et la fraction où le score Cortex bat chacune sur les *outcomes observés*
   post-action (pas juste les prédictions). Voir [docs/claims.md](docs/claims.md)
5. **LLM en fallback uniquement** — si l'écart top/runner-up < 0.05, un LLM
   léger tranche

L'UI distingue :
- **AUTO** = sortie de la boucle de scoring (`method=active_inference`)
- **Forcer (override)** = clic humain sur une action (`method=forced_by_user`)

## Sciences inspirantes (niveaux honnêtes — détail dans [docs/claims.md](docs/claims.md))

- **Active Inference / Free Energy Principle** (Friston, 2010) — *inspiré*, score EFE-like simplifié
- **Big5 OCEAN** (McCrae & Costa, 1987) — *implémenté*, modulation des scores
- **Curiosity Drive** (Schmidhuber, 1991) — *implémenté*, proxy compression delta
- **Spreading Activation** (Collins & Loftus, 1975) — *implémenté*, persisté disque
- **Hebbian Learning** (Hebb, 1949) — *implémenté*, edges renforcées au co-activate
- **Homeostasis** (Cannon, 1932 ; Ashby, 1960) — *implémenté*, vitals + actions graduelles
- **JEPA** (LeCun, 2022) — *partiel*, mini world-model NumPy entraîné sur paires
- **Force-Directed Layout** (Fruchterman & Reingold, 1991) — *implémenté*
- **Conceptual Blending** (Fauconnier & Turner, 2002) — *inspiré*
- **TF-IDF cosine** (Salton & McGill, 1983) — *implémenté* via sklearn
- **FrugalGPT cascade** (Chen et al., 2023) — *implémenté* dans router v2
- **TurboQuant-inspired** (Google, 2026) — *partiel*, version simplifiée maison

## Architecture

Cortex est composé d'**environ 43 modules Python** autonomes orchestrés par un
serveur HTTP unique. Chaque module correspond à une fonction cognitive
(mémoire, vision, voix, émergence, homeostasis, recherche…).

- [docs/architecture.md](docs/architecture.md) — liste complète des modules
- [docs/architecture-internal.md](docs/architecture-internal.md) — diagramme 4 couches + endpoints + fichiers d'état
- [docs/anti-fake.md](docs/anti-fake.md) — méthodologie anti-fake (5 tests mesurables)
- [docs/iag-progress.md](docs/iag-progress.md) — score IAG sur 7 dimensions, historique

## Code source publié

Le **code Python complet** qui implémente Cortex est dans [code/](code/) :

- [code/brain/](code/brain/) — 43 modules cognitifs (cortex_*.py + llm_router.py + lmstudio_policy.py)
- [code/dashboard/](code/dashboard/) — serveur HTTP (serve.py) + visualisation 3D (brain_gpu.html)

Les chemins user-spécifiques ont été anonymisés (`<USER_HOME>`, `<CORTEX_REPO>`).
Voir [code/README.md](code/README.md) pour les instructions de relance locale.

## Capacités

Cortex peut :
- scorer ses actions via une heuristique Active-Inference-inspired + Big5 + curiosité — voir [code/brain/cortex_active_inference.py](code/brain/cortex_active_inference.py) + [code/brain/cortex_emergence.py](code/brain/cortex_emergence.py)
- [chercher](docs/research.md) — multi-source arxiv/wiki/scholar/duckduckgo + synthèse sourcée
- [proposer du nettoyage disque](docs/disk-hygiene.md) avec règles documentées
- [proposer des ponts cognitifs](docs/bridges.md) entre concepts éloignés
- [détecter ses régressions](docs/brain-history.md) sur 24 h glissantes
- [s'expliquer à partir de ses métriques](docs/introspection.md)
- [se faire auditer par 5 tests anti-fake mesurables](docs/anti-fake.md), dont des questions sur son propre état interne

## Limites honnêtes

- **Active Inference simplifié** : EFE est une heuristique. Les effets d'action
  étaient hard-codés (`pred["n_active"] += 2` pour `explore_graph`) ; ils sont
  maintenant **appris empiriquement** par `cortex_action_effects.py` à partir
  des deltas observés post-action (mode `empirical` quand n≥8 exemples par
  action, fallback heuristique sinon). Ce **n'est pas** le formalisme
  variationnel complet de Friston, mais ce n'est plus une table fixe.
- **Score IAG calibré** : le score brut est multiplié par un facteur ≤1
  basé sur la maturité runtime réelle (ratio learned/fallback,
  prediction_error, fake_confident_rate). Sans ça le scoring binaire
  donnait 90+/100 sur un système clairement immature.
- **Vision en deux étages** : modèle VL pour la perception (qwen2.5-vl, llava)
  + brain LLM pour la synthèse. Pour les questions vision simples, on
  court-circuite le brain LLM (la description VL = la réponse). Pour les
  questions vision complexes, garde-fou anti-censure injecté dans le prompt
  brain LLM (qui sinon répondait « Je ne vois rien » alors que [Vue webcam]
  avait du contenu).
- **Active Inference vs banc de baselines** : la fraction "better than random"
  est calculée sur des **prédictions** EFE. Une mesure plus solide compare les
  *outcomes observés* post-action contre plusieurs baselines naïves (random,
  always-reflect, always-explore, round-robin, last-best). Les deux sont logués.
- **Anti-fake — questions sur l'état interne** : les questions OOD interrogent
  maintenant l'état non-disponible à un LLM nu (logs Cortex, historiques
  Hebbian/surprise). Une réponse confidente + factuellement fausse = fake.
- **CI minimale** publiée (`.github/workflows/smoke.yml`) : `py_compile` +
  `self_test` sur quelques modules, sans dépendances lourdes.
- Plusieurs paths Windows-spécifiques anonymisés mais pas portés Linux/macOS.
- Métriques `state.json` auto-déclarées : confronter au code réel dans `code/`
  et à [examples/session-001/](examples/session-001/) (capture de session).
- Repo synchronisé via `cortex_publishing.update()` (pas un fork manuel).

## Licence

[MIT](LICENSE) — open pour qu'autres "cerveaux vivants" puissent s'en inspirer.
