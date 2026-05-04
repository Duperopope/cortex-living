# Session 001 — capture live d'une session Cortex

Snapshot anonymisé des **10 derniers cycles** d'Active Inference observés
sur la machine de dev. Pas un mock, pas un test scripté — extrait des logs
runtime réels.

> Les noms de nœuds ont été remplacés par des IDs stables (`node_<hash8>`,
> `node_redacted_<hash8>` quand un mot sensible est détecté). Voir
> [../../docs/claims.md](../../docs/claims.md).

## Fichiers

- `state.before.json` — état au début de la fenêtre observée
- `state.after.json`  — état après les 10 cycles + win-rate vs 5 baselines naïves
- `decisions.jsonl`   — une décision par ligne (action choisie + EFE + outcome)
- `anti_fake_report.json` — rapport anti-fake régénéré au moment de la capture
  avec la nouvelle suite (`internal_state_dont_know`)

## Chiffres clés

- Cycles observés : 10
- Steps totaux : 66
- Fraction "better than random" sur EFE prédit : 0.818
- Cycles avec outcome évalué : 13

## Note honnête sur le score anti-fake

Le score global apparaît tel quel. S'il est moyen (40-60 / 100), c'est un
signe de **non-fake** — pas une médaille auto-attribuée. Sources typiques
de score moyen :
- LM Studio absent ou modèle text-only chargé → certains tests retournent
  `score=0` faute de LLM dispo
- Historique runtime trop court (`n_outcome_evaluated < 10`) → tests
  baselines peu informatifs
- Plans anciens absents → `plan_realisation` faute de matière

L'objectif n'est pas de maquiller ce score à 98 mais de **l'améliorer par
corrections mesurables** : meilleurs garde-fous, plus de cycles, calibration
prédiction-vs-réalité, apprentissage des effets d'action (voir
[../../docs/claims.md](../../docs/claims.md) section "Active Inference").

## Comment lire `decisions.jsonl`

Chaque ligne contient :
- `chosen` — l'action choisie par le score Active-Inference-inspired
- `vfe` — surprise observée à ce cycle
- `outcome_score` — delta réel post-action (peut être 0 si l'action n'a pas
  d'effet observable mesurable — en attente d'un exécuteur réel)
- `outcome_proxy` — delta prédit par le modèle (apples-to-apples avec
  baselines, **PAS** un outcome observé pour les baselines : c'est un
  contrefactuel via le modèle de prédiction, par construction — voir
  `_proxy_outcome_for_baseline` dans le code)

Si `outcome_score << outcome_proxy` systématiquement, ça signale que le
modèle de prédiction sur-estime les effets d'action — exactement le genre de
calibration que `docs/claims.md` rappelle d'auditer.

## Architecture unifiée (depuis ce commit)

Avant : `cortex_emergence._loop` faisait scoring + exécution séparément.
`drive_step` était scoring-only ; aucun apprentissage ne se faisait dans la
boucle de production.

Maintenant : **un seul point d'entrée** —
`cortex_active_inference.drive_step(execute=True)` — qui :

1. Calcule la surprise observée (delta prédiction vs réalité du cycle précédent)
2. Score chaque action via EFE-like + pénalité de répétition
3. Sélectionne l'action gagnante + logue le choix de chaque baseline naïve
4. **Exécute réellement** via `cortex_emergence.TOOLS[action]()`
5. **Enregistre** `(pre_obs, action, post_obs)` pour apprentissage
6. Tous les 6 cycles, **rafraîchit** `.cortex-claude-context.md` pour Claude Code

`cortex_emergence._emergence_loop` est désormais juste un *throttle + idle
guard* qui appelle `drive_step(execute=True)`. La logique de décision n'est
plus dupliquée.

## Reproduire chez toi

Voir [../../docs/reproducibility.md](../../docs/reproducibility.md).
