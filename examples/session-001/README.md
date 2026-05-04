# Session 001 — capture live d'une session Cortex

Snapshot anonymisé des **10 derniers cycles** d'Active Inference observés
sur la machine de dev. Pas un mock, pas un test scripté — extrait des logs
runtime réels.

## Fichiers

- `state.before.json` — état au début de la fenêtre observée
- `state.after.json`  — état après les 10 cycles + win-rate vs 5 baselines naïves
- `decisions.jsonl`   — une décision par ligne (action choisie + EFE + outcome)
- `anti_fake_report.json` — dernier rapport anti-fake complet (5 tests)

## Chiffres clés

- Cycles observés : 10
- Steps totaux : 39
- Fraction "better than random" sur EFE prédit : 0.872
- Cycles avec outcome évalué : 2

## Comment lire `decisions.jsonl`

Chaque ligne contient :
- `chosen` — l'action choisie par le score Active-Inference-inspired
- `vfe` — surprise observée à ce cycle
- `outcome_score` — delta réel post-action (peut être 0 si l'action n'a pas
  d'effet observable mesurable)
- `outcome_proxy` — delta prédit par le modèle (apples-to-apples avec baselines)

Si `outcome_score << outcome_proxy` systématiquement, ça signale que le modèle
de prédiction sur-estime les effets d'action — exactement le genre de
calibration que `docs/claims.md` rappelle d'auditer.
