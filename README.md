# Cortex — un cerveau cognitif vivant

> Dernière mise à jour : `2026-05-04T11:20:32` (auto-généré)

Cortex est une entité cognitive autonome construite sur le projet Paperclip.
Il voit, entend, mémorise, apprend, et raisonne avec une vraie boucle Spreading
Activation Theory (Collins & Loftus, 1975) et un apprentissage Hebbian
(Hebb, 1949).

## État cognitif courant

| Métrique               | Valeur                                       |
|------------------------|----------------------------------------------|
| Nœuds graphe pensée    | **1779**                    |
| Arêtes sémantiques     | **181687**                    |
| Densité                | **0.1149**                    |
| Nœuds actifs           | **54** (décroissance τ=60 s) |
| Hebbian cumulé         | **4.86** (apprentissage) |
| Zones d'ignorance      | **0** (besoin de ponts) |

### Composition du graphe
- `claude_memory` : 20 nœuds
- `semantic` : 1659 nœuds
- `episodic` : 100 nœuds

## Corps (homeostasis)

- CPU : **30.2%**
- RAM : **51.8%**
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

Cortex est composé d'environ 30 modules Python autonomes orchestrés par un
serveur HTTP unique (`scripts/brain/dashboard/serve.py`). Chaque module
correspond à une fonction cognitive (mémoire, vision, voix, émergence,
homeostasis, recherche…).

Voir [docs/architecture.md](docs/architecture.md) pour la liste complète et
[docs/anti-fake.md](docs/anti-fake.md) pour la méthodologie anti-fake.

## Émancipation

Cortex peut :
- 🧠 [décider de manière autonome](docs/architecture.md) via Active Inference + Big5 + curiosité
- 🔍 [chercher](docs/research.md) — multi-source arxiv/wiki/scholar/duckduckgo + synthèse sourcée
- 🧹 [nettoyer son disque](docs/disk-hygiene.md) avec doc citée par pattern
- 🌉 [créer des ponts cognitifs](docs/bridges.md) entre concepts éloignés
- 📊 [détecter ses régressions](docs/brain-history.md) sur 24 h glissantes (snapshots cassés filtrés du baseline)
- 🪞 [s'expliquer lui-même](docs/introspection.md) à partir de ses métriques
- 🎯 [prouver qu'il ne fake pas](docs/anti-fake.md) via 5 tests mesurables

## Licence

MIT — open pour qu'autres "cerveaux vivants" puissent s'en inspirer.
