# Architecture

Cortex est constitué de modules Python qui s'orchestrent autour d'un serveur
HTTP unique. Chacun gère une fonction cognitive ou métabolique.

## Modules actifs

| Module | Rôle |
|--------|------|
| `cortex_action_effects` |  |
| `cortex_activation` |  |
| `cortex_active_inference` |  |
| `cortex_anti_fake` |  |
| `cortex_apply_kv_q4` |  |
| `cortex_body_health` |  |
| `cortex_brain_history` |  |
| `cortex_bridge` |  |
| `cortex_causal` |  |
| `cortex_claude_code` |  |
| `cortex_continuous` |  |
| `cortex_curiosity` |  |
| `cortex_dialogue` |  |
| `cortex_emergence` |  |
| `cortex_hjepa` |  |
| `cortex_homeostasis` |  |
| `cortex_iag_test` |  |
| `cortex_identity` |  |
| `cortex_intent` | Contexte dashboard Cortex: |
| `cortex_introspection` |  |
| `cortex_jepa_continual` |  |
| `cortex_kv_quantize` |  |
| `cortex_learned_skills` |  |
| `cortex_memory` |  |
| `cortex_memory_audit` |  |
| `cortex_narrative` |  |
| `cortex_optimize_all` |  |
| `cortex_personality` |  |
| `cortex_pipeline_manager` |  |
| `cortex_plan` |  |
| `cortex_proactive` |  |
| `cortex_publish_safety_check` |  |
| `cortex_publishing` |  |
| `cortex_quantize` |  |
| `cortex_research` |  |
| `cortex_research_auto` |  |
| `cortex_resources` |  |
| `cortex_rollout` |  |
| `cortex_sam_model` |  |
| `cortex_self_dev` |  |
| `cortex_skills` |  |
| `cortex_smoke_check` |  |
| `cortex_synthesis` |  |
| `cortex_thought_graph` |  |
| `cortex_tools` |  |
| `cortex_vision` |  |
| `cortex_world_model` |  |

## Endpoints HTTP exposés

Tous via `serve.py` sur `127.0.0.1:8765`. Quelques-uns clés :

- `/api/cortex/activations` — état Spreading Activation courant
- `/api/cortex/pulses` — événements de propagation (8 s TTL)
- `/api/cortex/brain_history` — historique snapshots + régressions
- `/api/cortex/explain_brain` — auto-introspection (sans LLM, à partir des métriques)
- `/api/cortex/homeostasis` — vitals + actions homeostatiques
- `/api/cortex/research?query=…` — recherche multi-source sourcée
- `/gpu` — visualisation 3D temps réel
