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
