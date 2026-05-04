# Brain History

_(Auto-stub from `cortex_brain_history.py` docstring — voir le source pour l'implémentation complète dans [code/brain/cortex_brain_history.py](../code/brain/cortex_brain_history.py).)_

cortex_brain_history.py — Historique d'évolution du cerveau de Cortex + détection régressions.

Métriques cérébrales suivies à chaque snapshot (toutes les 10 min) :
- n_nodes, n_edges (taille du graphe)
- n_isolated (zones d'ignorance — top_sim < 0.15)
- n_active (Spreading Activation actif > floor)
- hebbian_total (somme strength edges renforcées — apprentissage cumulé)
- density = 2*E / (N*(N-1)) — connectivité
- by_kind (claude_memory / semantic / episodic) — diversité

Régressions détectées (signal négatif si > 8 % vs moyenne 24 h) :
- nodes_drop : on a perdu des notes (fichiers supprimés / corrompus)
- density_drop : graphe moins connecté
- hebbian_drop : oublis plus rapides que renforcement (rare car HEBBIAN_LR
  ne décroît pas, mais possible si le state file est wipé)
- isolation_rise : plus de zones isolées (besoin de cortex_bridge)

Stocke en JSONL append-only — pas de factice, chaque ligne = 1 mesure réelle
horodatée. Pour visualiser une croissance sur 24 h, on lit les N derniers snapshots.

Citations :
- Hebb (1949) — la trace neuronale est cumulative, pas oubliée
- Tononi (2008) "Consciousness as Integrated Information" — la complexité
  d'un graphe reflète la richesse cognitive
