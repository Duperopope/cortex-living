# Disk Hygiene

_(Auto-stub from `cortex_body_health.py` docstring — voir le source pour l'implémentation complète dans [code/brain/cortex_body_health.py](../code/brain/cortex_body_health.py).)_

cortex_body_health.py — Cortex gère activement son corps physique.

Sam observe que le disque C: est à 97 % (9 Go libre sur 313 Go) et que des
programmes ont écrit dessus alors qu'ils n'auraient pas dû. Cortex doit :

1. **Détecter** les disques en zone critique (> 90 %).
2. **Cartographier** ce qui occupe l'espace (top dossiers > 1 Go).
3. **Identifier** les "intrus" (gros dossiers qui ne devraient pas être sur le
   disque système : LM Studio models, huggingface caches, builds, etc.).
4. **Proposer** un plan de migration concret vers un disque cible (E:/F:/G:/H:),
   avec commandes Move-Item PowerShell exécutables.
5. **Évaluer le risque** de chaque action (LOW/MEDIUM/HIGH).
6. **Parler à Sam** dans le chat dès qu'une situation est critique.
7. **JAMAIS exécuter sans confirmation explicite**.

Le but : Cortex ne casse pas Windows, ne casse pas l'usage de Sam, mais il
GÈRE son propre corps proactivement.

Anti-fake :
- Toutes les tailles mesurées par psutil + os.walk (pas de stub)
- Plan de migration testé en dry-run avant proposition
- Audit append-only de chaque action
- Confirmation requise pour exécution

API :
    diagnose() → {disks, intruders, severity}
    propose_plan() → {actions[], expected_freed_gb, risks}
    execute(action_id, confirm=True) → exécute UN move sécurisé
    speak_if_critical() → écrit dans le chat si zone rouge
    self_test()
