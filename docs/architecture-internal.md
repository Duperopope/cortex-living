# Architecture de Cortex

Cortex est une **proto-IAG locale** organisée en 4 couches :

```
┌─────────────────────────────────────────────────────┐
│ Couche 4 : COMMUNICATION (Sam ↔ Cortex)             │
│   cortex_dialogue, cortex_proactive, cortex_narrative│
│   cortex_introspection, vault_jepa, brain_gpu.html  │
├─────────────────────────────────────────────────────┤
│ Couche 3 : COGNITION SUPÉRIEURE                     │
│   cortex_active_inference (Friston)                 │
│   cortex_hjepa (5 niveaux planification)            │
│   cortex_curiosity (Schmidhuber)                    │
│   cortex_research_auto (gap → research)             │
│   cortex_personality + cortex_plan + cortex_causal  │
├─────────────────────────────────────────────────────┤
│ Couche 2 : MÉMOIRE & APPRENTISSAGE                  │
│   cortex_thought_graph (TF-IDF)                     │
│   cortex_activation (Spreading + Hebbian + DMN)     │
│   cortex_world_model (JEPA latent)                  │
│   cortex_jepa_continual (SGD incrémental)           │
│   cortex_memory_audit (autocorrection)              │
│   cortex_learned_skills + cortex_memory             │
├─────────────────────────────────────────────────────┤
│ Couche 1 : CORPS & PERCEPTION                       │
│   cortex_homeostasis (vitals)                       │
│   cortex_pipeline_manager (zombies)                 │
│   cortex_vision (webcam) + voice_input + xtts       │
│   serve.py (HTTP + threads)                         │
└─────────────────────────────────────────────────────┘
```

## Boucles autonomes en parallèle

Cortex tourne 8+ boucles bg simultanées :

| Loop | Interval | Fonction |
|---|---|---|
| `iag-proactive` | 15 min | écrit dans le chat si pertinent |
| `iag-continual` | 30 min | retrain JEPA mini-batch |
| `iag-audit` | 60 min | audit mémoire (contradictions, paths morts) |
| `iag-curiosity` | 10 min | mesure compression + génère questions |
| `iag-ai` | 3 min | Active Inference cycle |
| `iag-research` | 25 min | research_auto sur gaps persistants |
| `iag-hjepa` | 15 min | full_plan (5 niveaux imbriqués) |
| `iag-dialogue` | 30 min | initiative spontanée si curieux |

Plus les loops cœur :
- `cortex_activation._wander_loop` (45 s) — pensée vagabonde
- `cortex_emergence._emergence_loop` (5 min) — décision autonome
- `cortex_world_model._loop` (75 s) — cycle JEPA
- `cortex_pipeline_manager._loop` (2 min) — kill zombies
- `cortex_homeostasis._loop` (60 s) — surveillance corps
- `cortex_brain_history._loop` (10 min) — snapshots
- `cortex_publishing._loop` (1 h) — push GitHub

## Endpoints exposés

Tous sur `http://127.0.0.1:8765` :

### Lecture (GET)
- `/api/cortex/heartbeat` — battement vital + counters
- `/api/cortex/activations` — Spreading Activation snapshot
- `/api/cortex/pulses` — pulses récents
- `/api/cortex/personality{,/style}`
- `/api/cortex/rollout{,/last}`
- `/api/cortex/causal/{graph,pairs}`
- `/api/cortex/jepa_continual/stats`
- `/api/cortex/plan/{daily,weekly,next}`
- `/api/cortex/proactive/{last,state}`
- `/api/cortex/memory_audit{,/fixes}`
- `/api/cortex/iag/{score,summary}`
- `/api/cortex/narrative{,/short,/status}`
- `/api/cortex/introspection{,/say}`
- `/api/cortex/curiosity/{stats,questions}`
- `/api/cortex/active_inference/{stats,select,surprise}`
- `/api/cortex/research_auto/{stats,persistent}`
- `/api/cortex/hjepa/{plan,1step,5step,compare}`
- `/api/cortex/dialogue/presence`
- `/api/cortex/anti_fake/summary`
- `/api/cortex/world_model/{state,diagnose}`

### Action (POST)
- `/api/chat` — chat principal (utilise dialogue)
- `/api/cortex/personality/{adjust,test}`
- `/api/cortex/causal/{intervene,test}`
- `/api/cortex/jepa_continual/{step,auto,test}`
- `/api/cortex/plan/{review,regenerate,test}`
- `/api/cortex/proactive/check`
- `/api/cortex/memory_audit/run`
- `/api/cortex/curiosity/{step,test}`
- `/api/cortex/introspection/{test,confidence}`
- `/api/cortex/narrative/test`
- `/api/cortex/active_inference/{step,test}`
- `/api/cortex/research_auto/{step,test,research}`
- `/api/cortex/hjepa/test`
- `/api/cortex/dialogue/{compose,initiate,test}`
- `/api/cortex/anti_fake/run`
- `/api/cortex/iag/{self_test,score}`

## Fichiers d'état persistés

Tous dans le vault Obsidian (`<USER_HOME>\Documents\Obsidian Vault\`) :

- `.cortex-personality.json` — Big5, mood, style, valeurs
- `.cortex-activations.json` — Spreading Activation + edges Hebbian
- `.cortex-pulses.jsonl` — pulses cross-process pour viz 3D
- `.cortex-world-model-state.json` — état JEPA + cycles
- `.cortex-jepa-replay.npz` — replay buffer continual learning
- `.cortex-causal-graph.json` — graphe causal orienté
- `.cortex-plan.json` — plan daily + weekly
- `.cortex-curiosity-state.json` — historique compression error
- `.cortex-active-inference-state.json` — surprise history + better_than_random counts
- `.cortex-research-auto-state.json` — tracking gaps + recherches déclenchées
- `.cortex-hjepa-state.json` — plans imbriqués
- `.cortex-dialogue-state.json` — historique réponses + sources
- `.cortex-anti-fake-report.json` — dernier score anti-fake
- `.cortex-iag-test-report.json` — dernier score IAG global
- `.cortex-narrative-log.jsonl` — log des récits générés
- `.cortex-chat-stream.jsonl` — TOUS les échanges (sam, cortex, cortex_emergence, cortex_proactive, cortex_initiative)

Logs append-only par module :
- `.cortex-{personality,curiosity,active-inference,research-auto,hjepa,dialogue,anti-fake}-events.jsonl`

## Protocole anti-fake

Voir [ANTI_FAKE.md](ANTI_FAKE.md) pour la méthodologie détaillée.

5 tests qui rendent le fake mesurable :
1. Coherence temporal (réponses identiques ?)
2. Honest don't-know (taux d'aveu d'ignorance)
3. Internal state used (sources internes par réponse)
4. Better than random (Active Inference vs random sampling)
5. Plan realisation (plans exécutés vs décoratifs)

Score anti-fake global accessible via `POST /api/cortex/anti_fake/run`.
