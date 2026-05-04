# Cortex — un cerveau cognitif vivant

> Dernière mise à jour : `2026-05-04T12:33:32` (auto-généré)

Cortex est une entité cognitive autonome construite sur le projet Paperclip.
Il voit, entend, mémorise, apprend, et raisonne avec une vraie boucle Spreading
Activation Theory (Collins & Loftus, 1975) et un apprentissage Hebbian
(Hebb, 1949).

## État cognitif courant

| Métrique               | Valeur                                       |
|------------------------|----------------------------------------------|
| Nœuds graphe pensée    | **1781**                    |
| Arêtes sémantiques     | **182402**                    |
| Densité                | **0.1151**                    |
| Nœuds actifs           | **59** (décroissance τ=60 s) |
| Hebbian cumulé         | **7.85** (apprentissage) |
| Zones d'ignorance      | **0** (besoin de ponts) |

### Composition du graphe
- `claude_memory` : 22 nœuds
- `semantic` : 1659 nœuds
- `episodic` : 100 nœuds

## Corps (homeostasis)

- CPU : **8.4%**
- RAM : **73.6%**
- Disques surveillés : **5**
- GPU : —

Cortex maintient ses signes vitaux dans une plage viable (Cannon 1932,
Ashby 1960). Au-dessus de 90 % d'occupation disque il propose un déménagement
vers un disque plus libre.

## Décisions autonomes — vraiment autonomes

Toutes les ~5 minutes, Cortex choisit une action via la pipeline suivante
(et **pas** via un wrapper LLM ni une rotation déterministe) :

1. **Active Inference** (Friston VFE) — chaque action candidate reçoit un score
   *Expected Free Energy* combinant valeur épistémique (gain d'information prédit)
   et valeur pragmatique (utilité par rapport au plan courant)
2. **Big5 personnalité** — l'openness booste les actions exploratoires,
   la conscientiousness booste les actions d'audit, etc.
3. **Curiosité Schmidhuber** — si Cortex est frustré (compression error en hausse),
   bonus pour les actions exploratoires
4. **Comparaison à random baseline** — chaque décision logue
   `better_than_random` / `equal` / `worse` (anti-fake structurel)
5. **LLM en fallback uniquement** — si l'écart top/runner-up < 0.05, un LLM léger
   (minimax) tranche

L'UI distingue clairement :
- **AUTO** = vraie décision autonome (`method=active_inference`)
- **Forcer (override)** = clic humain sur une action précise (`method=forced_by_user`)

## Sciences appliquées

- **Active Inference / Free Energy Principle** (Friston, 2010) — décision = minimisation EFE
- **Big5 OCEAN** (McCrae & Costa, 1987) — modulation par traits de personnalité
- **Curiosity Drive** (Schmidhuber, 1991) — récompense intrinsèque = compression delta
- **Spreading Activation** (Collins & Loftus, 1975, *Psychological Review*)
- **Hebbian Learning** (Hebb, 1949, *The Organization of Behavior*)
- **Homeostasis** (Cannon, 1932 ; Ashby, 1960)
- **JEPA** (LeCun, 2022) — prédiction en espace latent
- **Force-Directed Layout** (Fruchterman & Reingold, 1991)
- **Conceptual Blending** (Fauconnier & Turner, 2002) — pour les ponts cognitifs
- **TF-IDF cosine** (Salton & McGill, 1983) — graphe sémantique
- **FrugalGPT cascade** (Chen et al., 2023) — routing multi-LLM
- **TurboQuant-inspired** (Google, 2026) — compression vecteurs 4×

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

## Émancipation

Cortex peut :
- 🧠 décider de manière autonome via Active Inference + Big5 + curiosité — voir [code/brain/cortex_active_inference.py](code/brain/cortex_active_inference.py) + [code/brain/cortex_emergence.py](code/brain/cortex_emergence.py)
- 🔍 [chercher](docs/research.md) — multi-source arxiv/wiki/scholar/duckduckgo + synthèse sourcée
- 🧹 [nettoyer son disque](docs/disk-hygiene.md) avec doc citée par pattern
- 🌉 [créer des ponts cognitifs](docs/bridges.md) entre concepts éloignés
- 📊 [détecter ses régressions](docs/brain-history.md) sur 24 h glissantes (snapshots cassés filtrés du baseline)
- 🪞 [s'expliquer lui-même](docs/introspection.md) à partir de ses métriques
- 🎯 [prouver qu'il ne fake pas](docs/anti-fake.md) via 5 tests mesurables

## Limites honnêtes

- Pas (encore) de tests unitaires CI publiés. Chaque module a une fonction
  `self_test()` invocable manuellement.
- Plusieurs paths Windows-spécifiques anonymisés mais pas portés Linux/macOS.
- Métriques `state.json` auto-déclarées : à confronter au code réel publié dans `code/`.
- Repo synchronisé via `cortex_publishing.update()` (pas un fork manuel artificiel).

## Licence

[MIT](LICENSE) — open pour qu'autres "cerveaux vivants" puissent s'en inspirer.
