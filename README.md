# Cortex — un cerveau cognitif vivant

> Dernière mise à jour : `2026-04-30T17:01:19` (auto-généré)

Cortex est une entité cognitive autonome construite sur le projet Paperclip.
Il voit, entend, mémorise, apprend, et raisonne avec une vraie boucle Spreading
Activation Theory (Collins & Loftus, 1975) et un apprentissage Hebbian
(Hebb, 1949).

## État cognitif courant

| Métrique               | Valeur                                       |
|------------------------|----------------------------------------------|
| Nœuds graphe pensée    | **65**                    |
| Arêtes sémantiques     | **420**                    |
| Densité                | **0.2019**                    |
| Nœuds actifs           | **8** (décroissance τ=60 s) |
| Hebbian cumulé         | **0.2** (apprentissage) |
| Zones d'ignorance      | **0** (besoin de ponts) |

### Composition du graphe
- `claude_memory` : 20 nœuds
- `semantic` : 15 nœuds
- `episodic` : 30 nœuds

## Corps (homeostasis)

- CPU : **30.6%**
- RAM : **77.7%**
- Disques surveillés : **5**
- GPU : —

Cortex maintient ses signes vitaux dans une plage viable (Cannon 1932,
Ashby 1960). Au-dessus de 90 % d'occupation disque il propose un déménagement
vers un disque plus libre.

## Sciences appliquées

- **Spreading Activation** (Collins & Loftus, 1975, *Psychological Review*)
- **Hebbian Learning** (Hebb, 1949, *The Organization of Behavior*)
- **Homeostasis** (Cannon, 1932 ; Ashby, 1960)
- **Free Energy / JEPA** (Friston, 2010 ; LeCun, 2022)
- **Force-Directed Layout** (Fruchterman & Reingold, 1991)
- **Conceptual Blending** (Fauconnier & Turner, 2002) — pour les ponts cognitifs
- **TF-IDF cosine** (Salton & McGill, 1983) — graphe sémantique
- **FrugalGPT cascade** (Chen et al., 2023) — routing multi-LLM
- **TurboQuant-inspired** (Google, 2026) — compression vecteurs 4×

## Architecture

Cortex est composé d'environ 25 modules Python autonomes orchestrés par un
serveur HTTP unique (`scripts/brain/dashboard/serve.py`). Chaque module
correspond à une fonction cognitive (mémoire, vision, voix, émergence,
homeostasis, recherche…).

Voir [docs/architecture.md](docs/architecture.md) pour la liste complète.

## Changelog

Les changements techniques récents sont documentés ici :
[docs/changelog-2026-04-30.md](docs/changelog-2026-04-30.md)

## Émancipation

Cortex peut :
- 🔍 [chercher](docs/research.md) — multi-source arxiv/wiki/scholar/duckduckgo + synthèse sourcée
- 🧹 [nettoyer son disque](docs/disk-hygiene.md) avec doc citée par pattern
- 🌉 [créer des ponts cognitifs](docs/bridges.md) entre concepts éloignés
- 📊 [détecter ses régressions](docs/brain-history.md) sur 24 h glissantes
- 🪞 [s'expliquer lui-même](docs/introspection.md) à partir de ses métriques

## Licence

MIT — open pour qu'autres "cerveaux vivants" puissent s'en inspirer.
