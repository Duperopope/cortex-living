# Session current — preuves multi-rapports

> Snapshot live : `2026-05-05T03:51:21`

Ce dossier rassemble **tous les rapports croisés** produits par les modules
de Cortex à un instant T. Différent de `session-001/` (qui est une fenêtre
historique) — `session-current` reflète l'état d'aujourd'hui.

## Fichiers (audit reproductible)

| Fichier | Source | Ce qu'il prouve |
|---|---|---|
| `state.before.json` | `cortex_active_inference._load_state()` | Snapshot début fenêtre récente |
| `state.after.json` | idem fin de fenêtre | Win-rate vs 5 baselines naïves sur outcomes proxy |
| `decisions.jsonl` | `vfe_history[-N:]` | Une décision/cycle, action choisie + outcome |
| `action_effects_summary.json` | `cortex_action_effects.stats()` | empirical_ratio, prediction_error_avg, top_reliable / top_overoptimistic |
| `body_health_report.json` | `cortex_body_health.body_health_status()` | Sévérité disques + 6 junctions actives + dernier auto_exec |
| `anti_fake_report.json` | dernier rapport `cortex_anti_fake.run_all_tests()` | 5 tests dont `internal_state_dont_know` |
| `smoke_check_report.json` | `cortex_smoke_check.run()` LIVE | strict-core (compile + import + self_test) |
| `safety_check_report.json` | dernier scan `cortex_publish_safety_check.scan()` | n_blockers, n_warnings, by_kind |
| `iag_report.json` | `cortex_iag_test.run_iag_test()` | raw_score, calibrated_score, bottlenecks, maturity |
| `perception_context.json` | `cortex_dialogue.get_perception_context()` | vision_available, age_s, method (live/sticky) |

## Ce qui est PROUVÉ par ces fichiers

- Les **junctions NTFS** sont vérifiées via `Get-Item .LinkType` (locale-independent)
- Le **safety check** détecte les fuites (test self_test injecte un faux secret et vérifie qu'il est attrapé)
- Le **smoke check strict-core** a tourné et passé 7/7 modules
- L'**apprentissage empirique** est mesuré : nombre d'exemples par action, prediction_error
- Le **score IAG** est calibré par 3 facteurs visibles (action_effects, prediction_error, fake_confident_rate)

## Ce qui reste fake / partiel / métaphorique

- **3D viz** = notes Obsidian, pas modules Cortex (cf claims.md)
- **Active Inference** : EFE-like, pas le formalisme variationnel Friston
- **JEPA** : mini world-model NumPy entraîné, pas LeCun complet
- **Self-dev** : aspirationnel, pas testé end-to-end avec commits verts

## Comment reproduire

```bash
# Smoke check (compile + import + self_test sur 7 modules cœur)
python code/brain/cortex_smoke_check.py

# Safety check (anti-fuite avant publish)
python code/brain/cortex_publish_safety_check.py scan

# Action effects (stats apprentissage)
python code/brain/cortex_action_effects.py summary

# Body health status (disques + junctions)
python code/brain/cortex_body_health.py diagnose

# IAG calibré
python code/brain/cortex_iag_test.py
```

Si tes chiffres divergent de ce qui est dans les .json ici, c'est attendu —
ce snapshot est figé à `2026-05-05T03:51:21`. Lance les commandes pour voir l'état actuel.
