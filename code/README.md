# Cortex — code source

Code Python + dashboard HTML qui implémente Cortex. Anonymisé : les chemins
machine de Sam ont été remplacés par des placeholders (`<USER_HOME>`,
`<CORTEX_REPO>`).

## Layout

```
code/
├── brain/                          # 43 modules cognitifs
│   ├── cortex_active_inference.py  # Friston VFE + EFE
│   ├── cortex_personality.py       # Big5 OCEAN traits
│   ├── cortex_curiosity.py         # Schmidhuber drive
│   ├── cortex_emergence.py         # boucle décisionnelle autonome
│   ├── cortex_dialogue.py          # chat ancré sur l'état interne
│   ├── cortex_thought_graph.py     # graphe sémantique TF-IDF
│   ├── cortex_activation.py        # Spreading Activation + Hebbian
│   ├── cortex_world_model.py       # JEPA latent
│   ├── cortex_brain_history.py     # snapshots + détection régressions
│   ├── cortex_anti_fake.py         # 5 tests anti-fake mesurables
│   ├── cortex_homeostasis.py       # Cannon/Ashby vitals
│   ├── cortex_vision.py            # webcam + screen capture
│   └── ... (+30 autres)
├── dashboard/
│   ├── serve.py                    # serveur HTTP unique (port 8765)
│   └── brain_gpu.html              # visualisation 3D + chat + cerveau
```

## Pour relancer chez toi

1. Remplace les placeholders par tes propres chemins :
   - `<USER_HOME>` → ex. `C:\Users\<toi>` ou `/home/<toi>`
   - `<CORTEX_REPO>` → racine de ce code
2. Installe les dépendances :
   ```
   pip install numpy<2.0 scikit-learn opencv-python psutil pillow requests
   ```
3. Optionnel : LM Studio + qwen3.6-35b-a3b sur localhost:1234 (pour LLM local)
   ou `OPENROUTER_API_KEY` env var (fallback).
4. Lance le serveur :
   ```
   python brain/dashboard/serve.py
   ```
5. Ouvre `http://127.0.0.1:8765/gpu`

## Statut

- **Le code publié = ce qui tourne réellement chez Sam**, anonymisé.
- Pas un fork artificiel. Synchronisé via `cortex_publishing.update()`.
- Les chemins originaux Windows sont préservés sous forme placeholder pour
  que le code reste lisible (libre à toi d'adapter Linux/macOS).

## Limites honnêtes

- Pas de tests unitaires automatisés publiés (la plupart des modules ont une
  fonction `self_test()` qu'on peut invoquer manuellement).
- Plusieurs paths `os` Windows-spécifiques que tu devras patcher pour Linux.
- Dépend implicitement d'Obsidian Vault (chemin `<USER_HOME>/Documents/Obsidian Vault`).
