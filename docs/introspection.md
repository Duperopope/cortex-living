# Introspection

_(Auto-stub from `cortex_introspection.py` docstring — voir le source pour l'implémentation complète dans [code/brain/cortex_introspection.py](../code/brain/cortex_introspection.py).)_

cortex_introspection.py — Méta-cognition : Cortex sait ce qu'il sait, ne sait pas, et apprend.

Pour une IAG, savoir où on en est est aussi important que de savoir.
Trois questions auxquelles ce module répond :

1. **Que SAIS-JE** ?
   - Concepts solidement représentés dans le thought_graph (degré élevé)
   - Skills apprises et persistées (cortex_learned_skills)
   - Pairs causales fortes (relations cause→effet établies)
   - Modules dont le self-test passe systématiquement

2. **Que NE SAIS-JE PAS** ?
   - Concepts isolés (orphans, degré ≤ 1)
   - Gaps JEPA non comblés (probe.confidence < 0.2)
   - Issues mémoire non résolues
   - Dimensions IAG faibles

3. **Qu'apprends-je en ce moment** ?
   - Continual learning JEPA en cours (loss qui baisse)
   - Nouvelles activations qui renforcent des edges Hebbian
   - Causalités émergentes (paires qui passent au-dessus du seuil)

Le module retourne un rapport structuré + une représentation textuelle pour humain.

API :
    introspect() → dict structuré avec 3 sections
    confidence_on(topic) → estime la confiance de Cortex sur un sujet
    say_what_i_dont_know() → sélectionne les 3 lacunes prioritaires en français
    self_test()
