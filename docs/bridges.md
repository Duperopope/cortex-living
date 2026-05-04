# Bridges

_(Auto-stub from `cortex_bridge.py` docstring — voir le source pour l'implémentation complète dans [code/brain/cortex_bridge.py](../code/brain/cortex_bridge.py).)_

cortex_bridge.py — Cortex tisse des ponts cognitifs entre domaines isolés.

Inspiré de :
- Fauconnier & Turner (2002), "The Way We Think: Conceptual Blending"
- Hofstadter (1979), "Gödel, Escher, Bach" — analogie comme noyau de la cognition
- Mednick (1962), "The associative basis of the creative process" — Remote Associates Test

Méthode :
1. Identifier nœuds isolés (top_sim < 0.2 avec leurs voisins) via thought_graph
2. Pour chaque pair isolé, demander au LLM :
   "Quel concept scientifique relie {A} et {B} ?
    Cherche en : neurologie, physique quantique, informatique, mathématiques,
    physique, biologie, chimie, philosophie. Réponds par UN concept-pont."
3. Écrire le concept-pont comme nouvelle note sémantique dans
   08 - Semantic/bridges/{topic}.md
4. Re-build le thought_graph → les ponts apparaissent comme nouveaux nœuds
   qui rapprochent A et B via une chaîne A → bridge → B.

Effet visuel : avec force-directed, les zones isolées s'unifient organiquement
quand de nouveaux ponts apparaissent.
