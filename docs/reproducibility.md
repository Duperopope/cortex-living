# Reproductibilité — comment refaire `examples/session-001/`

Tu peux régénérer l'exemple toi-même depuis ton clone du repo. Le but est que
les chiffres publiés ne soient pas un mock mais reproductibles à partir d'un
runtime local.

## Ce dont tu as besoin

- Python 3.11+
- `pip install -r requirements.txt`
- (optionnel) LM Studio sur `localhost:1234` avec un modèle text. Sans LM
  Studio, certains tests anti-fake passent en mode dégradé mais le pipeline
  tourne quand même
- (optionnel) Un Obsidian Vault dont le path est passé via la variable
  d'env `CORTEX_VAULT`. Sans vault, le système fonctionne mais sans graphe
  sémantique enrichi

## Étapes

```bash
# 1. Cloner et installer
git clone https://github.com/<USER>/<REPO>.git cortex-living
cd cortex-living
pip install -r requirements.txt

# 2. Lancer 30+ cycles d'Active Inference pour avoir un historique
for i in $(seq 1 30); do
  python -c "import sys; sys.path.insert(0, 'code/brain'); \
    import cortex_active_inference as ai; print(ai.drive_step()['chosen_action'])"
done

# 3. Lancer la suite anti-fake
python code/brain/cortex_anti_fake.py full > my_anti_fake.json

# 4. Comparer avec examples/session-001/
diff -u examples/session-001/anti_fake_report.json my_anti_fake.json | head -50
```

## Ce que tu dois retrouver (à l'ordre de grandeur près)

- `n_steps_total` ≥ 30
- `n_outcome_evaluated` ≥ 29 (un cycle de retard, normal)
- `vs_baselines` : Cortex devrait gagner contre `random` mais peut perdre
  contre `always_explore` ou `round_robin` selon la phase d'exploration
  (c'est documenté honnêtement)
- `score_global` anti-fake : variable selon la disponibilité de LM Studio,
  l'ancien format à 43.5/100 est un signal honnête de système non-faké, pas
  un médaille à 98/100

## Si tes chiffres divergent fortement

1. Vérifier que `numpy < 2.0` (sinon sklearn casse)
2. Vérifier `compression_error` initial — il dépend du graphe vault, donc sans
   vault il sera figé à 0.5
3. Pour comparer politique-vs-politique, attendre au moins 50 cycles —
   `n_outcome_evaluated=2` ne suffit pas à conclure
4. **Mode exécution réelle** : tu DOIS appeler `drive_step(execute=True)` (pas
   le défaut `execute=False` qui est scoring-only). Sinon les outcomes
   observés resteront à 0 et l'apprentissage des effets sera vide.

## CI locale (gratuite, pas de quota)

Pour vérifier la santé du code sans dépendre de GitHub Actions, lance
directement :

```bash
python code/brain/cortex_smoke_check.py
# ou en JSON :
python code/brain/cortex_smoke_check.py json
```

Couvre :
- **strict-core** : `cortex_activation`, `cortex_active_inference`,
  `cortex_anti_fake`, `cortex_action_effects`, `cortex_homeostasis` —
  py_compile + import + self_test. Exit code 1 si fail.
- **smoke-rest** : tous les autres `cortex_*.py` — py_compile only,
  tolérant. Échec ne casse pas l'exit code.

`cortex_publishing.update()` appelle ce smoke check en pre-flight : si
strict-core échoue → la publication est refusée. Donc tant que tu publies
via `update()`, le code publié a forcément passé un compile + import +
self_test des modules cœur.

Le workflow GitHub Actions `smoke.yml` reste dispo (tu peux le
re-déclencher manuellement via "Actions → smoke → Run workflow"). Mais ce
n'est plus le seul rempart : la CI locale est désormais le rempart
principal.

## Memory hygiene (auto-audit du vault)

```bash
python code/brain/cortex_memory_audit.py audit       # rapport complet
python code/brain/cortex_memory_audit.py propose     # propose des fix DRY_RUN
```

Détecte 4 types d'issues :
- **Contradictions** entre notes mémoire (axes opposés, proposition manuelle)
- **Paths obsolètes** : refs vers fichiers qui n'existent plus (annotation
  proposée, pas suppression)
- **Endpoints incohérents** : `/api/cortex/X` cités dans la mémoire mais
  qui répondent 404
- **Duplicatas** : notes avec ≥0.7 jaccard sur leur description

`propose_corrections()` retourne des `fix_id` en `dry_run=true` par défaut.
Sam doit explicitement appliquer chacun. Pas de cleanup auto qui pourrait
détruire de l'historique utile.

## Stack LM Studio recommandée (local-first)

Pour que tout fonctionne sans dégradation silencieuse :

| Rôle | Modèle suggéré | Taille | Notes |
|---|---|---|---|
| **Vision-Language** | `unsloth/qwen2.5-vl-7b-instruct` (Q4-Q5) | ~5-7 GB | Webcam description, capture screen analysis |
| **Embedding** | `text-embedding-nomic-embed-text-v1.5` | ~80 MB | Vectorisation queries pour graphe sémantique |
| **Brain text** (optionnel) | `qwen3-4b` ou `qwen3.6-35b-a3b` | 2-14 GB | Synthèse meta_prompt pour questions complexes |

Tous chargés en parallèle dans LM Studio (multi-load). Le code Cortex
auto-détecte les modèles disponibles via `/v1/models` :
- `cortex_vision._detect_vision_model()` cherche `vl`/`vision`/`llava`
- `cortex_dialogue._detect_brain_llm_model()` cherche un brain text-only
  d'abord (qwen3, claude, llama, deepseek), retombe sur le VL en mode text
  si nécessaire

Pas de hardcoded model name → pas de fallback silencieux quand un modèle
est unloaded.

## Comment Claude Code se branche au système

Si tu utilises Claude Code (Anthropic CLI), un `CLAUDE.md` dans la racine
demande à l'agent de lire `.cortex-claude-context.md` au démarrage. Ce
fichier est régénéré tous les 6 cycles par la boucle d'émergence (constante
`CONTEXT_REFRESH_EVERY` dans `cortex_emergence.py`). Tu peux aussi le
forcer à la main :

```bash
python code/brain/cortex_claude_code.py update
```

Le contenu : état Active Inference, statut apprentissage par action
(empirical / fallback), graphe, body, dernier rapport anti-fake, 5 dernières
décisions. Pas de PII (les noms de nœuds étaient déjà hashés à la
publication ; ce contexte reste local de toute façon).
