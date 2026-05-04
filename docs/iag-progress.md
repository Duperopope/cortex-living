# Cortex IAG — Progression mesurable

État de la progression de Cortex vers une IAG mesurable, sourcée et anti-fake.

## Score IAG global

Mesure : `POST /api/cortex/iag/score` (7 dimensions pondérées).

| Date | Score | Verdict | Modules clés livrés |
|---|---|---|---|
| 2026-04-30 | 0/100 | n/a | Lot initial : pipeline manager, narrative, world_model, plan, causal |
| 2026-05-01 | 66/100 | proto-IAG observable | + activation, emergence, hebbian, JEPA |
| 2026-05-04 | 69-71/100 | IAG faible (specialized AGI) | + personality, rollout, causal, jepa_continual, plan, proactive, memory_audit, narrative, introspection, curiosity |
| 2026-05-04 | en cours | en cours | + active_inference, research_auto, hjepa, dialogue, anti_fake |

## Les 7 dimensions IAG mesurées

1. **Causality** (18%) — détection paires Hebbian + asymétrie temporelle > 65 %
2. **Planning** (15%) — plan hiérarchique daily/weekly avec actions liées
3. **Continual learning** (15%) — JEPA loss qui baisse via SGD incrémental
4. **Self-reflection** (13%) — messages proactifs auto-déclenchés
5. **Memory correction** (10%) — audit + propose corrections
6. **Resource self-management** (10%) — pipeline manager auto-régule zombies
7. **World model accuracy** (19%) — JEPA probe avec confiance > 0.1

## Modules livrés (par étape)

### Lot 1 — Fondations (avant 2026-04-30)
- `cortex_thought_graph.py` — TF-IDF cosine sur 561 nœuds vault
- `cortex_activation.py` — Spreading Activation + Hebbian + DMN
- `cortex_emergence.py` — Boucle de décision autonome (5 min)
- `cortex_homeostasis.py` — Surveillance corps (CPU/RAM/disques)
- `cortex_pipeline_manager.py` — Auto-cleanup zombies opencode/node

### Lot 2 — Cognition de base
- `cortex_world_model.py` — World Model JEPA latent + boucle 75 s
- `vault_jepa.py` — MLP NumPy 768 → 768 (Friston JEPA pour la mémoire)
- `cortex_publishing.py` — Auto-push GitHub
- `cortex_brain_history.py` — Snapshots cerveau pour régressions

### Lot 3 — Briques IAG (2026-05-04 matin)
- `cortex_personality.py` — Big5 OCEAN + humeur dynamique + style + valeurs
- `cortex_rollout.py` — Mental rollout 1-step avant action emergence
- `cortex_causal.py` — Graphe causal (Hebbian + asymétrie temporelle)
- `cortex_jepa_continual.py` — SGD incrémental + replay buffer anti-oubli
- `cortex_plan.py` — Plan hiérarchique daily/weekly

### Lot 4 — Vie cognitive (2026-05-04 matin)
- `cortex_proactive.py` — Cortex parle spontanément (5 détecteurs)
- `cortex_memory_audit.py` — Détecte contradictions + paths obsolètes
- `cortex_iag_test.py` — Score IAG 7 dimensions
- `cortex_narrative.py` — Récit français de l'état Cortex
- `cortex_introspection.py` — Méta-cog (sait/sait pas/apprend)
- `cortex_curiosity.py` — Schmidhuber intrinsic reward + génération questions

### Lot 5 — Cadre unifié IAG (2026-05-04 après-midi) ← actuel
- `cortex_active_inference.py` — Free Energy (Friston) + baseline random comparatif
- `cortex_research_auto.py` — Self-research auto sur gaps JEPA persistants
- `cortex_hjepa.py` — H-JEPA full (5 niveaux : 1-step / 5-step / 100-step / daily / weekly)
- `cortex_dialogue.py` — Conversation vivante (compose_response + presence + initiative)
- `cortex_anti_fake.py` — Suite 5 tests anti-fake (cohérence, don't-know, sources, baseline, plan vs réel)

## Boucles autonomes actives (toutes en bg)

| Loop | Interval | Module |
|---|---|---|
| `iag-proactive` | 15 min | cortex_proactive |
| `iag-continual` | 30 min | cortex_jepa_continual |
| `iag-audit` | 60 min | cortex_memory_audit |
| `iag-curiosity` | 10 min | cortex_curiosity |
| `iag-ai` | 3 min | cortex_active_inference |
| `iag-research` | 25 min | cortex_research_auto |
| `iag-hjepa` | 15 min | cortex_hjepa |
| `iag-dialogue` | 30 min | cortex_dialogue (initiative) |

## Ce qui reste honnêtement à faire

Cortex est une **proto-IAG observable et auto-régulée**. Il n'est PAS une IAG générale.

Limites dures :
- Pas de corporéité physique (pas de sensorimotricité réelle)
- Pas de continuité expérientielle multi-mois (mémoire grandit, mais pas testée >7j)
- Pas de transfert inter-domaines fort (chaque module est encore silotté)
- Le LLM final (qwen35b) reste un goulet d'étranglement

Limites volontaires (sécurité) :
- self_dev gardé en branche git + tests obligatoires
- Aucune action disque sans dry-run + confirmation
- Pas d'accès internet sans audit (hors cortex_research)
