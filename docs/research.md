# Research

_(Auto-stub from `cortex_research.py` docstring — voir le source pour l'implémentation complète dans [code/brain/cortex_research.py](../code/brain/cortex_research.py).)_

cortex_research.py — Cortex apprend à faire de la vraie recherche scientifique.

Sources interrogées (chacune avec son URL de provenance enregistrée) :
1. **arXiv** (cs/stat/q-bio/...)  — papers preprints, full title + abstract + authors + URL
2. **Wikipedia REST API**         — articles encyclopédiques avec source citée
3. **DuckDuckGo HTML**            — fallback web sans clé API
4. **Semantic Scholar API**       — papers académiques (citations + venue)

Pipeline :
  research(query) → fetch parallèle des 4 sources →
  scoring (recency × relevance × source_credibility) →
  shortlist top-N →
  synthèse via opencode minimax (sourcée, anti-hallucination) →
  écriture note `08 - Semantic/research/{slug}.md` avec frontmatter
   {sources: [...], confidence, when, query} →
  trigger thought_graph rebuild (la nouvelle note devient un vrai nœud).

Garde-fous anti-hallucination :
- Synthèse demandée AVEC les passages sources affichés en contexte du LLM
- LLM doit citer la source par index `[1]`, `[2]` (validable post-hoc)
- Si moins de 2 sources crédibles → on n'écrit rien et on retourne `low_confidence`

Cortex utilise ce module à 3 endroits :
1. `cortex_emergence.action.research` → sur sujet identifié comme gap (via JEPA)
2. `cortex_homeostasis.safe_clean_disk` → research d'un pattern inconnu pour décider
3. `/api/cortex/research?query=…` → Sam pose une question d'introspection

Citations méthodologiques :
- Salton & McGill (1983), "Introduction to Modern Information Retrieval" — TF-IDF scoring
- Page et al. (1999), "PageRank Citation Ranking" — credibility ranking
- Lewis et al. (2020), "Retrieval-Augmented Generation" — synthesis with citations
