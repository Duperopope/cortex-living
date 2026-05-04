---
name: Cortex — pipeline complet, audit et doc opérationnelle
type: operational_doc
domain: cortex-self
status: active
last_audit: 2026-04-30
purpose: Référence unique pour Sam pour piloter Cortex sans assistance Claude.
---

# Cortex — Pipeline complet, audit live, documentation

Ce document est la **référence opérationnelle** pour piloter Cortex après que
mon assistance Claude soit terminée. Audit du 2026-04-30 fait juste avant cette
fin de session : tous les composants vérifiés.

## 1. Démarrer Cortex

**Le plus simple** : double-clic sur `Cortex.lnk` du bureau.

Le `.lnk` pointe vers `<CORTEX_REPO>\scripts\brain\dashboard\Cortex.bat` qui :
1. Tue les listeners port 8765 résiduels.
2. Lance `python scripts\brain\dashboard\serve.py`.
3. Si le serveur crashe → relance auto après 3 s (boucle infinie).

**Ouvrir le dashboard** : http://localhost:8765/gpu

## 2. Architecture des boucles actives

`serve.py` démarre au boot **8 threads/loops background** (ordre dans `main()`) :

| # | Loop | Module | Intervalle | Rôle |
|---|---|---|---|---|
| 1 | Memory consolidation | `cortex_memory` | 6 h | Compacte les mémoires épisodiques en sémantiques |
| 2 | Continuous reflection | `cortex_continuous` | 15 min | Pensée arrière-plan + capture vision périodique |
| 3 | Vision capture | `cortex_continuous` | 5 min | Webcam/screenshot si vision activée |
| 4 | Emergence loop | `cortex_emergence` | 5 min | Cortex décide une action autonome (`audit_ui`, `explore_graph`, etc.) |
| 5 | Homeostasis | `cortex_homeostasis` | 60 s | Lit CPU/RAM/disques, écrit `vital-signs.json` |
| 6 | Activation | `cortex_activation` | 45 s (adaptatif) | Pensée vagabonde : active un nœud + spread + Hebbian |
| 7 | Brain history | `cortex_brain_history` | 10 min | Snapshots du cerveau pour détection régressions |
| 8 | Publishing GitHub | `cortex_publishing` | 1 h | Push docs+state vers github.com/Duperopope/cortex-living |
| 9 | Pipeline manager | `cortex_pipeline_manager` | 2 min | Tue les zombies opencode/node si RAM > 92 % |

**État au dernier audit (uptime 50 min)** :
- 32 activations cumulées, 24 pulses, 24 ticks Hebbian, 11 edges
- Pipeline : 74 processus, 12 zombies (sous seuil 80)
- CPU 22 %, RAM 79 %
- LM Studio : 1 instance qwen35b chargée, contexte 16k, KV Q4_0

## 3. Endpoints API (tous testés OK le 2026-04-30)

Préfixe : `http://localhost:8765`

### État et observabilité
- `GET /gpu` — page principale dashboard (180 KB de HTML/JS)
- `GET /api/cortex/heartbeat` — uptime + activation cum + emergence + vitals + chat
- `GET /api/cortex/heartbeat/config` — paramètres édités (seuils, polls, intervals)
- `GET /api/cortex/pipeline` — processus/zombies/mémoire RAM par catégorie
- `GET /api/cortex/activations` — nœuds éveillés + top hebbian edges
- `GET /api/cortex/pulses?since=<ts>` — pulses récents (8 s TTL)
- `GET /api/cortex/think_status?req_id=<id>` — étapes pipeline /api/chat en cours
- `GET /api/cortex/emergence_log?limit=N` — historique décisions autonomes

### Configuration éditable
- `POST /api/cortex/heartbeat/config` — change seuils (`dead_threshold_s`, `wander_interval_s`, etc.). Cliquer sur la chip **Live** dans la topbar → modal éditable.

### Fonctionnalités cognitives
- `POST /api/cortex/explain_term {term, context}` — tooltip LLM dyn (LM Studio, fallback opencode)
- `POST /api/cortex/llm_role {backend, role}` — métadonnées sur le LLM actif
- `POST /api/cortex/explain_brain` — Cortex décrit sa topologie 3D
- `POST /api/cortex/emergence_now?action=audit_ui` — force une décision autonome
- `POST /api/cortex/path {from, to}` — A* path entre 2 pensées du graphe
- `GET /api/cortex/learned_skills?q=...` — compétences apprises par self_dev
- `GET /api/cortex/judges` — panel-of-judges + benchmark IAG
- `GET /api/cortex/kv_quantize?target_vram_gb=12` — recommandation quantization

### Chat
- `POST /api/chat {message, fast, req_id}` — pipeline complet : RAG vault + identité + LLM. Logue dans `.cortex-chat-stream.jsonl`. TTS auto si `.tts-disabled.flag` absent.

## 4. Optimisations LLM appliquées

**Modèle actif** : `Qwen3.6-35B-A3B-UD-Q2_K_XL.gguf` (poids déjà très quantifiés Q2)
**Carte** : AMD Radeon RX 7800 XT, 16 GB VRAM

### Config LM Studio (persistée)

Fichier : `~/.lmstudio/.internal/user-concrete-model-default-config/unsloth/Qwen3.6-35B-A3B-GGUF/Qwen3.6-35B-A3B-UD-Q2_K_XL.gguf.json`

```
offloadRatio        : 1          (full GPU)
contextLength       : 16384      (réduit de 65536, économise 75 % KV cache)
kCacheQuantization  : q4_0       (réduit de q8_0, économise 50 % K cache)
vCacheQuantization  : q4_0       (idem V)
flashAttention      : true
tryMmap             : true       (était false — l'OS peut paginer dynamiquement)
```

### Speedup mesuré

| Étape | Tokens/s | Latence p50 | VRAM |
|---|---|---|---|
| Baseline (Q8_0 + ctx 65k + mmap off) | 1.4 | 62.32 s | 14.6 GB (saturé) |
| KV Q4_0 | 2.8 | 29.65 s | ~13.5 GB |
| **KV Q4_0 + ctx 16k + mmap on** | **5.52** | ~28 s | **13.1 GB** |

**Speedup total : ×3.94. VRAM libérée : 1.5 GB.**

### Pourquoi pas plus loin

- **Speculative decoding** (utiliser un modèle 27B en draft) : nécessite 2 modèles en VRAM = ~26 GB. Ta carte fait 16 GB → impossible.
- **TurboQuant 3-bit du papier Google** : pas encore mergé dans llama.cpp upstream (ETA 2026-Q3). Quand dispo, le code Cortex est prêt à basculer (`q3_turboquant` dans `cortex_kv_quantize.DTYPE_BYTES`).
- **Quantization plus agressive des poids** (Q2_K_XL → IQ2_M) : déjà en Q2, descendre dégrade trop la qualité.

## 5. Maintenance et commandes utiles

### Vérifier que tout va bien
```bash
curl http://localhost:8765/api/cortex/heartbeat
python scripts/brain/cortex_pipeline_manager.py snapshot
"<USER_HOME>/.lmstudio/bin/lms.exe" ps
```

### Cleanup zombies manuellement
```bash
python scripts/brain/cortex_pipeline_manager.py cleanup --no-dry-run
```

### Re-appliquer optim LLM (si LM Studio se réinitialise)
```bash
python scripts/brain/cortex_apply_kv_q4.py status   # voit l'état
python scripts/brain/cortex_apply_kv_q4.py full 16384   # re-applique
python scripts/brain/cortex_apply_kv_q4.py restore   # rollback backup
```

### Relancer le serveur Cortex
- Double-clic sur `Cortex.lnk` du bureau
- OU : `python scripts/brain/dashboard/serve.py` dans `<CORTEX_REPO>`

### Voir les decisions autonomes
```bash
curl 'http://localhost:8765/api/cortex/emergence_log?limit=10' | python -m json.tool
```

### Forcer une décision (sans LLM, instantané)
```bash
curl -X POST 'http://localhost:8765/api/cortex/emergence_now?action=audit_ui'
curl -X POST 'http://localhost:8765/api/cortex/emergence_now?action=explore_graph'
curl -X POST 'http://localhost:8765/api/cortex/emergence_now?action=discovery_report'
```

Dans l'UI : cliquer un des 6 boutons sous "Dernière décision autonome" :
`audit · graphe · ignorance · rapport` (sans LLM, ~1-3 s) ou
`reflect (LLM) · goal (LLM)` (10-30 s).

## 6. Fichiers d'état persistés

### Dans le repo `<CORTEX_REPO>\`
| Fichier | Contenu |
|---|---|
| `.cortex-self-dev.log` | Log auto-développement |
| `.cortex-emergence.log` | Log textuel boucle émergence |
| `.cortex-publishing.log` | Log push GitHub |
| `.cortex-homeostasis.log` | Log régulation corps |
| `.cortex-pipeline-audit.jsonl` | Audit chaque kill de zombie |
| `.cortex-pipeline-state.json` | État dernière auto-régulation |
| `.cortex-heartbeat-config.json` | Paramètres heartbeat éditables |
| `.cortex-kv-q4-applied.json` | Rapport dernière application optim |
| `.cortex-full-optim-applied.json` | Rapport optim full (ctx + mmap) |
| `.cortex-disk-migration-proposals.json` | Propositions migration disque |
| `.serve-watchdog.log` | Log watchdog port 8765 (si actif) |

### Dans le vault `~/Documents/Obsidian Vault/`
| Fichier | Contenu |
|---|---|
| `.cortex-chat-stream.jsonl` | TOUS les échanges chat + emergence_publish |
| `.cortex-activations.json` | État activations + edges Hebbian (cross-process) |
| `.cortex-pulses.jsonl` | Pulses cross-process pour viz 3D |
| `.cortex-tooltip-cache.json` | Cache 7j explanations LLM |
| `.cortex-brain-history.jsonl` | **MANQUANT au dernier audit** — à vérifier |
| `.vault-graph.json` | Graphe sémantique TF-IDF |
| `.vault-llm-benchmark-iag.json` | Rounds panel-of-judges |

### Dans `.claude/projects/h--Code-Paperclip/memory/`
Mémoires persistantes Cortex/Claude (cortex_identity, MEMORY index, feedback, projets actifs).

## 7. Diagnostic en cas de problème

### Le serveur ne démarre pas
1. Vérifier qu'il y a pas d'autre listener : `netstat -ano | findstr :8765`
2. Tuer le PID : `taskkill /F /PID <pid>`
3. Relancer via Cortex.lnk.

### Le LLM est lent ou ne répond pas
1. `lms ps` → vérifier qu'une seule instance tourne (pas de `:2`)
2. Si plusieurs : `lms unload qwen3.6-35b-a3b:2`
3. Si config dégradée : `python scripts/brain/cortex_apply_kv_q4.py full 16384`

### RAM système saturée
1. `python scripts/brain/cortex_pipeline_manager.py zombies` → liste
2. `python scripts/brain/cortex_pipeline_manager.py cleanup --no-dry-run` → tue
3. Le pipeline_manager le fait auto toutes les 2 min, mais on peut forcer.

### Cortex bloque sur même action en boucle (`propose_goal → no_goal_generated`)
Le bouton GO sans menu fait une rotation déterministe sur les actions sans LLM
(`audit_ui`, `explore_graph`, `map_knowledge`, `discovery_report`).
Cliquer un bouton spécifique force cette action.

### Le tooltip "Pas d'explication LLM disponible"
Vérifier que LM Studio est UP : `curl http://localhost:1234/v1/models`.
Sinon démarrer LM Studio depuis le menu démarrer.

## 8. GitHub auto-publishing

**Repo** : https://github.com/Duperopope/cortex-living

`cortex_publishing.start(interval=3600)` push toutes les heures :
- README.md auto-généré depuis l'état Cortex
- /docs/architecture.md
- /docs/state.json (snapshot live)

Forcer un push : `curl -X POST 'http://localhost:8765/api/cortex/publishing?action=update'`

## 9. Liste des modules Python clés

| Module | Rôle |
|---|---|
| `cortex_thought_graph` | Graphe TF-IDF sémantique (561 nœuds × 2000 dims) |
| `cortex_activation` | Spreading Activation + Hebbian + pulses |
| `cortex_emergence` | Boucle décisions autonomes |
| `cortex_continuous` | Réflexion arrière-plan + vision |
| `cortex_homeostasis` | Surveillance corps + propositions disque |
| `cortex_memory` | Consolidation épisodique → sémantique |
| `cortex_self_dev` | Auto-développement avec garde-fous git |
| `cortex_learned_skills` | Mémorise les goals self_dev qui ont marché |
| `cortex_pipeline_manager` | Auto-régulation matérielle (zombies) |
| `cortex_brain_history` | Snapshots cerveau pour régressions |
| `cortex_publishing` | Auto-push GitHub |
| `cortex_quantize` | TurboQuant Python (PolarQuant 3-bit) |
| `cortex_kv_quantize` | Tooling KV cache LM Studio |
| `cortex_apply_kv_q4` | Patch config LM Studio + mesure |
| `cortex_optimize_all` | Pipeline complet d'optims |

## 10. Commits récents

```
4c673542  optim LLM full (KV Q4 + ctx 16k + mmap) — speedup ×3.94
2aa38bc3  KV cache LM Studio Q8_0 → Q4_0 appliqué (speedup ×2.1)
8903ba01  optim all + tooltips → LM Studio local (élimine zombies opencode)
3e7f4513  TurboQuant 3-bit Python — application au thought_graph
72c9445b  gestion autonome du pipeline matériel (146 zombies → 0)
bc0233ef  PolarQuant fidèle au papier + lanceur bureau auto-restart
```

Branche : `cortex/dev/20260430-175149-mets-a-jour-scriptsbrainself_d`
Push automatique vers `main` du repo public toutes les heures.

## 11. Limites connues

- **LM Studio peut crasher silencieusement** sous pression mémoire. Le watchdog
  bash relance `serve.py` mais pas LM Studio. Si Cortex chat échoue, ouvrir
  l'app LM Studio manuellement et vérifier que qwen35b est chargé.
- **Le serveur Python serve.py crash parfois** (~5-30 min selon la charge).
  Cause non identifiée (probablement segfault d'extension C sous pression
  RAM). Mitigation : `Cortex.bat` boucle infinie + `cortex_pipeline_manager`
  agressif sur les zombies.
- **Brain history fichier manquant** au dernier audit — à investiguer.
- **2 modèles LLM en VRAM = impossible** (16 GB). Pas de speculative decoding.
- **TurboQuant 3-bit "vrai" du papier** : pas dispo, ETA 2026-Q3 dans llama.cpp.

## 12. Continuer sans Claude

Tu peux tout piloter via :
1. **L'UI dashboard** http://localhost:8765/gpu : chat, slash-commands, boutons,
   tooltips dynamiques (qui appellent ton LM Studio local).
2. **Les scripts CLI** documentés ci-dessus.
3. **Le chat Cortex** lui-même : tape `/help` pour voir les commandes.
4. **Le push GitHub auto** : tout ce que Cortex fait apparaît dans
   github.com/Duperopope/cortex-living/docs/state.json.

Cortex est conçu pour fonctionner en boucle fermée sans assistance externe :
ses propres décisions (emergence loop), ses propres optimisations (pipeline
manager), sa propre publication (publishing loop).
