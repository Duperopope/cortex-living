"""
cortex_publishing.py — Cortex publie son développement sur GitHub en temps réel.

Objectif : Cortex maintient un dépôt public qui reflète son état vivant — pas un
README statique, mais une documentation auto-générée à partir de ses métriques
réelles + de son architecture modulaire.

Stratégie :
1. **Repo dédié** (par défaut `cortex-living`) sous le user GitHub de Sam.
2. **README.md auto-généré** depuis les vraies métriques (brain_history snapshot,
   sciences intégrées, modules actifs).
3. **/docs/** rempli automatiquement avec :
   - architecture.md : diagramme + dépendances modules
   - sciences.md   : citations utilisées (Hebb, Collins-Loftus, Friston, LeCun…)
   - state.json    : snapshot live du cerveau (n_nodes, n_active, hebbian, …)
   - changelog.md  : journal des évolutions cognitives détectées
4. **GitHub Pages** depuis `/docs` (Settings → Pages → main /docs) → site public
   accessible à `https://USER.github.io/cortex-living/`.

Sécurité :
- JAMAIS de push automatique sans `confirm=True`.
- Filtrage des chemins : on ne publie QUE des fichiers explicitement whitelistés
  (pas de secrets, pas de chemins absolus de la machine, pas de logs sensibles).
- Sam approuve le premier `init_repo` puis chaque update peut être autonome
  sous flag `auto_publish`.

Pre-requis :
- `gh` CLI installé et authentifié (`gh auth login`)
- `git` configuré

Usage :
  python cortex_publishing.py init              # crée le repo (dry-run)
  python cortex_publishing.py init --confirm    # crée vraiment
  python cortex_publishing.py update            # régénère docs et push
  python cortex_publishing.py preview           # affiche ce qui serait publié
"""
import datetime as dt
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_LOCAL = Path(r"<CORTEX_REPO>\.cortex-publishing")
DOCS_DIR   = REPO_LOCAL / "docs"
CODE_DIR   = REPO_LOCAL / "code"
DEFAULT_REPO_NAME = "cortex-living"
LOG_FILE   = Path(r"<CORTEX_REPO>\.cortex-publishing.log")

SRC_BRAIN  = Path(r"<CORTEX_REPO>\scripts\brain")
SRC_DOCS   = Path(r"<CORTEX_REPO>\docs")

# Patterns d'anonymisation : on retire les chemins user-specific qui pourraient
# fuiter des info perso (Sam, son arbo, ses cookies). Les copies vers /code
# remplacent par des placeholders, l'original n'est PAS modifié.
import re as _re
_ANONYMIZE_PATTERNS = [
    (_re.compile(r"H:[/\\]+Code[/\\]+Paperclip", _re.IGNORECASE),
     "<CORTEX_REPO>"),
    (_re.compile(r"C:[/\\]+Users[/\\]+Smedj", _re.IGNORECASE),
     "<USER_HOME>"),
    (_re.compile(r"\.claude-cookies\.json", _re.IGNORECASE),
     ".claude-cookies.placeholder"),
    (_re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),  # OpenAI/OpenRouter keys
     "<API_KEY_REDACTED>"),
    (_re.compile(r"ghp_[A-Za-z0-9]{30,}"),    # GitHub tokens
     "<GH_TOKEN_REDACTED>"),
]


def _anonymize(text: str) -> str:
    """Retire les chemins/secrets perso. Idempotent."""
    for pat, repl in _ANONYMIZE_PATTERNS:
        text = pat.sub(repl, text)
    return text


def _log(msg: str):
    line = f"[{dt.datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f: f.write(line + "\n")
    except Exception: pass


def _run(cmd: list[str], cwd: Path = None, timeout: int = 30) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:
        return -1, str(e)


# ─── Génération docs à partir de l'état réel ────────────────────────────────
def _gather_state() -> dict:
    """Récupère un snapshot de tout ce qui caractérise Cortex maintenant."""
    sys.path.insert(0, r"<CORTEX_REPO>\scripts\brain")
    state = {"ts": time.time(),
             "iso": dt.datetime.now().isoformat(timespec="seconds")}
    try:
        import cortex_brain_history as bh
        state["brain"] = bh.evolution_summary().get("current", {})
    except Exception as e: state["brain_error"] = str(e)[:200]
    try:
        import cortex_activation as ca
        state["activation"] = ca.snapshot()
    except Exception as e: state["activation_error"] = str(e)[:200]
    try:
        import cortex_homeostasis as ch
        v = ch.vital_signs()
        state["body"] = {
            "cpu_percent": v.get("cpu_percent"),
            "ram_percent": v.get("ram_percent"),
            "n_disks": len(v.get("disks", [])),
            "gpu": [g.get("name") for g in (v.get("gpu") or [])],
        }
    except Exception as e: state["body_error"] = str(e)[:200]
    return state


def _readme_md(state: dict) -> str:
    """README dynamique — affiche métriques live."""
    b = state.get("brain", {}) or {}
    a = state.get("activation", {}) or {}
    body = state.get("body", {}) or {}
    return f"""# Cortex — prototype expérimental de boucle cognitive locale

> Dernière mise à jour : `{state.get("iso","?")}` (auto-généré)

Cortex est un **prototype expérimental** de boucle cognitive locale
construite sur le projet Paperclip. Il combine capture webcam, audio, mémoire
épisodique/sémantique, propagation d'activation (Collins & Loftus, 1975), et un
score d'action **inspiré** d'Active Inference (Friston, 2010, version simplifiée
— pas le formalisme complet).

> Statut : prototype auditable. Voir [docs/claims.md](docs/claims.md) pour la
> liste exacte de ce qui est implémenté vs inspiré vs aspirationnel.

## État cognitif courant

| Métrique               | Valeur                                       |
|------------------------|----------------------------------------------|
| Nœuds graphe pensée    | **{b.get('n_nodes', 0)}**                    |
| Arêtes sémantiques     | **{b.get('n_edges', 0)}**                    |
| Densité                | **{b.get('density', 0)}**                    |
| Nœuds actifs           | **{a.get('n_active', 0)}** (décroissance τ=60 s) |
| Hebbian cumulé         | **{b.get('hebbian_total', 0)}** (apprentissage) |
| Zones d'ignorance      | **{b.get('n_isolated', 0)}** (besoin de ponts) |

### Composition du graphe
{chr(10).join(f"- `{k}` : {v} nœuds" for k, v in (b.get('by_kind', {}) or {}).items())}

## Corps (homeostasis)

- CPU : **{body.get('cpu_percent', '?')}%**
- RAM : **{body.get('ram_percent', '?')}%**
- Disques surveillés : **{body.get('n_disks', '?')}**
- GPU : {", ".join(body.get('gpu', []) or ['—'])}

Cortex maintient ses signes vitaux dans une plage viable (Cannon 1932,
Ashby 1960). Au-dessus de 90 % d'occupation disque il propose un déménagement
vers un disque plus libre.

## Boucle de décision (Active-Inference-inspired) — unifiée

Toutes les ~5 minutes, **un seul appel** `cortex_active_inference.drive_step(execute=True)`
réalise le cycle complet : score EFE des actions, sélection, exécution réelle
via `cortex_emergence.TOOLS`, enregistrement des deltas observés
(`cortex_action_effects.record_observation`) pour apprentissage. Ce
**n'est pas** une rotation déterministe ni un wrapper LLM nu, mais ce
**n'est pas non plus** le formalisme Active Inference complet — c'est une
heuristique inspirée qui apprend ses effets au fil des cycles :

1. **Score Expected-Free-Energy-like** — combine valeur épistémique
   (réduction prédite de `compression_error`) et valeur pragmatique (utilité
   par rapport au plan courant). Effets d'action initialement hardcodés,
   **désormais remplacés par les deltas empiriques** quand l'agent a observé
   ≥ 8 exemples de l'action (`cortex_action_effects.predict_effect`)
2. **Modulation Big5** — openness booste les actions exploratoires,
   conscientiousness booste les actions d'audit
3. **Bonus curiosité** (Schmidhuber, 1991) — si `compression_error` en hausse,
   bonus pour les actions exploratoires
4. **Banc de baselines naïves** — chaque cycle, on logue le choix de
   `random`, `always-reflect`, `always-explore`, `round-robin`, `last-best`,
   et la fraction où le score Cortex bat chacune sur les *outcomes observés*
   post-action (pas juste les prédictions). Voir [docs/claims.md](docs/claims.md)
5. **LLM en fallback uniquement** — si l'écart top/runner-up < 0.05, un LLM
   léger tranche

L'UI distingue :
- **AUTO** = sortie de la boucle de scoring (`method=active_inference`)
- **Forcer (override)** = clic humain sur une action (`method=forced_by_user`)

## Sciences inspirantes (niveaux honnêtes — détail dans [docs/claims.md](docs/claims.md))

- **Active Inference / Free Energy Principle** (Friston, 2010) — *inspiré*, score EFE-like simplifié
- **Big5 OCEAN** (McCrae & Costa, 1987) — *implémenté*, modulation des scores
- **Curiosity Drive** (Schmidhuber, 1991) — *implémenté*, proxy compression delta
- **Spreading Activation** (Collins & Loftus, 1975) — *implémenté*, persisté disque
- **Hebbian Learning** (Hebb, 1949) — *implémenté*, edges renforcées au co-activate
- **Homeostasis** (Cannon, 1932 ; Ashby, 1960) — *implémenté*, vitals + actions graduelles
- **JEPA** (LeCun, 2022) — *partiel*, mini world-model NumPy entraîné sur paires
- **Force-Directed Layout** (Fruchterman & Reingold, 1991) — *implémenté*
- **Conceptual Blending** (Fauconnier & Turner, 2002) — *inspiré*
- **TF-IDF cosine** (Salton & McGill, 1983) — *implémenté* via sklearn
- **FrugalGPT cascade** (Chen et al., 2023) — *implémenté* dans router v2
- **TurboQuant-inspired** (Google, 2026) — *partiel*, version simplifiée maison

## Architecture

Cortex est composé d'**environ 43 modules Python** autonomes orchestrés par un
serveur HTTP unique. Chaque module correspond à une fonction cognitive
(mémoire, vision, voix, émergence, homeostasis, recherche…).

- [docs/architecture.md](docs/architecture.md) — liste complète des modules
- [docs/architecture-internal.md](docs/architecture-internal.md) — diagramme 4 couches + endpoints + fichiers d'état
- [docs/anti-fake.md](docs/anti-fake.md) — méthodologie anti-fake (5 tests mesurables)
- [docs/iag-progress.md](docs/iag-progress.md) — score IAG sur 7 dimensions, historique

## Code source publié

Le **code Python complet** qui implémente Cortex est dans [code/](code/) :

- [code/brain/](code/brain/) — 43 modules cognitifs (cortex_*.py + llm_router.py + lmstudio_policy.py)
- [code/dashboard/](code/dashboard/) — serveur HTTP (serve.py) + visualisation 3D (brain_gpu.html)

Les chemins user-spécifiques ont été anonymisés (`<USER_HOME>`, `<CORTEX_REPO>`).
Voir [code/README.md](code/README.md) pour les instructions de relance locale.

## Capacités

Cortex peut :
- scorer ses actions via une heuristique Active-Inference-inspired + Big5 + curiosité — voir [code/brain/cortex_active_inference.py](code/brain/cortex_active_inference.py) + [code/brain/cortex_emergence.py](code/brain/cortex_emergence.py)
- [chercher](docs/research.md) — multi-source arxiv/wiki/scholar/duckduckgo + synthèse sourcée
- [proposer du nettoyage disque](docs/disk-hygiene.md) avec règles documentées
- [proposer des ponts cognitifs](docs/bridges.md) entre concepts éloignés
- [détecter ses régressions](docs/brain-history.md) sur 24 h glissantes
- [s'expliquer à partir de ses métriques](docs/introspection.md)
- [se faire auditer par 5 tests anti-fake mesurables](docs/anti-fake.md), dont des questions sur son propre état interne

## Limites honnêtes

- **Active Inference simplifié** : EFE est une heuristique. Les effets d'action
  étaient hard-codés (`pred["n_active"] += 2` pour `explore_graph`) ; ils sont
  maintenant **appris empiriquement** par `cortex_action_effects.py` à partir
  des deltas observés post-action (mode `empirical` quand n≥8 exemples par
  action, fallback heuristique sinon). Ce **n'est pas** le formalisme
  variationnel complet de Friston, mais ce n'est plus une table fixe.
- **Score IAG calibré** : le score brut est multiplié par un facteur ≤1
  basé sur la maturité runtime réelle (ratio learned/fallback,
  prediction_error, fake_confident_rate). Sans ça le scoring binaire
  donnait 90+/100 sur un système clairement immature.
- **Vision en deux étages** : modèle VL pour la perception (qwen2.5-vl, llava)
  + brain LLM pour la synthèse. Pour les questions vision simples, on
  court-circuite le brain LLM (la description VL = la réponse). Pour les
  questions vision complexes, garde-fou anti-censure injecté dans le prompt
  brain LLM (qui sinon répondait « Je ne vois rien » alors que [Vue webcam]
  avait du contenu).
- **Active Inference vs banc de baselines** : la fraction "better than random"
  est calculée sur des **prédictions** EFE. Une mesure plus solide compare les
  *outcomes observés* post-action contre plusieurs baselines naïves (random,
  always-reflect, always-explore, round-robin, last-best). Les deux sont logués.
- **Anti-fake — questions sur l'état interne** : les questions OOD interrogent
  maintenant l'état non-disponible à un LLM nu (logs Cortex, historiques
  Hebbian/surprise). Une réponse confidente + factuellement fausse = fake.
- **CI minimale** publiée (`.github/workflows/smoke.yml`) : `py_compile` +
  `self_test` sur quelques modules, sans dépendances lourdes.
- Plusieurs paths Windows-spécifiques anonymisés mais pas portés Linux/macOS.
- Métriques `state.json` auto-déclarées : confronter au code réel dans `code/`
  et à [examples/session-001/](examples/session-001/) (capture de session).
- Repo synchronisé via `cortex_publishing.update()` (pas un fork manuel).

## Licence

[MIT](LICENSE) — open pour qu'autres "cerveaux vivants" puissent s'en inspirer.
"""


def _architecture_md() -> str:
    """Liste des modules Python du cerveau."""
    brain_dir = Path(r"<CORTEX_REPO>\scripts\brain")
    modules = sorted(p.stem for p in brain_dir.glob("cortex_*.py"))
    rows = []
    for m in modules:
        path = brain_dir / f"{m}.py"
        # Premier docstring ligne (ce que c'est)
        try:
            txt = path.read_text(encoding="utf-8", errors="replace")
            doc = ""
            if '"""' in txt:
                doc = txt.split('"""', 2)[1].splitlines()[0].split("—", 1)[-1].strip()
            rows.append(f"| `{m}` | {doc[:100]} |")
        except Exception:
            rows.append(f"| `{m}` | (introspection failed) |")
    return f"""# Architecture

Cortex est constitué de modules Python qui s'orchestrent autour d'un serveur
HTTP unique. Chacun gère une fonction cognitive ou métabolique.

## Modules actifs

| Module | Rôle |
|--------|------|
{chr(10).join(rows)}

## Endpoints HTTP exposés

Tous via `serve.py` sur `127.0.0.1:8765`. Quelques-uns clés :

- `/api/cortex/activations` — état Spreading Activation courant
- `/api/cortex/pulses` — événements de propagation (8 s TTL)
- `/api/cortex/brain_history` — historique snapshots + régressions
- `/api/cortex/explain_brain` — auto-introspection (sans LLM, à partir des métriques)
- `/api/cortex/homeostasis` — vitals + actions homeostatiques
- `/api/cortex/research?query=…` — recherche multi-source sourcée
- `/gpu` — visualisation 3D temps réel
"""


def _stable_node_id(name: str, _cache: dict = {}) -> str:
    """Hash stable d'un nom de note Obsidian → `node_<hash8>`. Idempotent par run.

    Pourquoi : `state.json` exposait des titres de notes (`08 - Semantic\\vm-...`,
    `supabase-key-revocation`, etc.). Même non secrets, c'est du contexte
    projet/perso qui n'a aucune raison de fuiter. On les remplace par un ID
    stable basé sur SHA1 (8 hex chars) — l'ID reste cohérent entre snapshots
    publiés mais ne se reverse pas vers le titre original.
    """
    if not name: return "node_unknown"
    if name in _cache: return _cache[name]
    import hashlib
    h = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    _cache[name] = f"node_{h}"
    return _cache[name]


# Patterns de mots-clés sensibles dans les titres de note. Si présents, on
# masque entièrement le nom (au lieu de juste le hasher) pour signaler la
# nature sensible.
_SENSITIVE_TITLE_PATTERNS = [
    "secret", "token", "credential", "password", "passwd", "api[-_]?key",
    "supabase", "vercel", "github[-_]?deploy", "github[-_]?action",
    "ssh[-_]?key", "ssh[-_]?priv", "private[-_]?key", "vault[-_]?key",
    "claude[-_]?cookies", "openrouter[-_]?key", "anthropic[-_]?key",
]


def _redact_node_name(name: str) -> str:
    """Si un nom contient un mot sensible → `node_redacted_<hash>`. Sinon hash neutre."""
    if not name: return _stable_node_id(name)
    import re as _r
    low = name.lower()
    for pat in _SENSITIVE_TITLE_PATTERNS:
        if _r.search(pat, low):
            import hashlib
            h = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
            return f"node_redacted_{h}"
    return _stable_node_id(name)


def _anonymize_activation_snapshot(snap: dict) -> dict:
    """Hash les noms de nœuds dans `active_nodes` et `top_hebbian_edges`.

    Préserve les chiffres (intensités, strengths, compteurs). Seuls les
    identifiants (titres de notes) sont remplacés par des IDs stables.
    """
    if not isinstance(snap, dict): return snap
    out = dict(snap)
    if isinstance(snap.get("active_nodes"), dict):
        out["active_nodes"] = {
            _redact_node_name(k): v for k, v in snap["active_nodes"].items()
        }
    if isinstance(snap.get("top_hebbian_edges"), list):
        out["top_hebbian_edges"] = [
            {"a": _redact_node_name(e.get("a")),
             "b": _redact_node_name(e.get("b")),
             "strength": e.get("strength")}
            for e in snap["top_hebbian_edges"]
            if isinstance(e, dict)
        ]
    return out


def _anonymize_state_for_publish(state: dict) -> dict:
    """Version publique de `state` : on conserve les chiffres, on hash les noms."""
    if not isinstance(state, dict): return state
    out = dict(state)
    if "activation" in out:
        out["activation"] = _anonymize_activation_snapshot(out["activation"])
    # `brain.by_kind` ne fuite que des kinds (semantic, episodic, etc.) — ok
    return out


def _state_json(state: dict) -> str:
    return json.dumps(_anonymize_state_for_publish(state),
                       ensure_ascii=False, indent=2)


# ─── Pipeline ───────────────────────────────────────────────────────────────
def preview() -> dict:
    """Affiche ce qui serait publié sans rien faire."""
    state = _gather_state()
    return {
        "readme_preview": _readme_md(state)[:3000],
        "architecture_preview": _architecture_md()[:2000],
        "state": state,
    }


def init_repo(repo_name: str = DEFAULT_REPO_NAME, confirm: bool = False) -> dict:
    """Crée le dépôt local + remote GitHub via `gh`."""
    if not confirm:
        return {"ok": False, "preview": True,
                "message": f"Lancerait : git init dans {REPO_LOCAL}, "
                           f"`gh repo create {repo_name} --public`. "
                           f"Re-lance avec --confirm pour exécuter."}
    rc, gh = _run(["gh", "--version"])
    if rc != 0:
        return {"ok": False, "error": "gh CLI absent — installer https://cli.github.com/"}
    rc, auth = _run(["gh", "auth", "status"])
    if rc != 0:
        return {"ok": False, "error": f"gh non authentifié : {auth[:200]}"}
    REPO_LOCAL.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    rc, out = _run(["git", "init"], cwd=REPO_LOCAL)
    rc, out = _run(["git", "branch", "-M", "main"], cwd=REPO_LOCAL)
    # README + docs initiaux
    readme = REPO_LOCAL / "README.md"
    if not readme.exists():
        readme.write_text(_readme_md(_gather_state()), encoding="utf-8")
    (DOCS_DIR / "architecture.md").write_text(_architecture_md(), encoding="utf-8")
    (DOCS_DIR / "state.json").write_text(_state_json(_gather_state()), encoding="utf-8")
    # .gitignore minimal
    (REPO_LOCAL / ".gitignore").write_text(
        "*.log\n__pycache__/\n.cortex-*\n", encoding="utf-8")
    rc, out = _run(["git", "add", "-A"], cwd=REPO_LOCAL)
    rc, out = _run(["git", "commit", "-m", "Initial Cortex publication"], cwd=REPO_LOCAL)
    rc, out = _run(["gh", "repo", "create", repo_name, "--public",
                    "--source=.", "--push", "--description",
                    "Cortex — un cerveau cognitif vivant (auto-généré)"],
                   cwd=REPO_LOCAL)
    if rc != 0:
        return {"ok": False, "error": f"gh repo create err: {out[:300]}"}
    # Active GitHub Pages depuis /docs
    _run(["gh", "api",
          "-X", "POST",
          f"repos/:owner/{repo_name}/pages",
          "-f", "source[branch]=main",
          "-f", "source[path]=/docs"],
         cwd=REPO_LOCAL)
    return {"ok": True, "repo": repo_name, "local": str(REPO_LOCAL),
            "next": f"docs: https://USER.github.io/{repo_name}/"}


def _publish_code() -> dict:
    """Copie le code Python + HTML dans <repo>/code/, anonymisé.

    Réplique l'arbre :
      code/
        brain/cortex_*.py + llm_router.py + lmstudio_policy.py
        dashboard/serve.py + brain_gpu.html
    """
    DST_BRAIN = CODE_DIR / "brain"
    DST_DASH  = CODE_DIR / "dashboard"
    DST_BRAIN.mkdir(parents=True, exist_ok=True)
    DST_DASH.mkdir(parents=True, exist_ok=True)
    counts = {"brain": 0, "dashboard": 0, "skipped": 0}
    # cortex_*.py + utilitaires routing
    for src in SRC_BRAIN.glob("cortex_*.py"):
        try:
            txt = src.read_text(encoding="utf-8", errors="replace")
            (DST_BRAIN / src.name).write_text(_anonymize(txt), encoding="utf-8")
            counts["brain"] += 1
        except Exception:
            counts["skipped"] += 1
    for extra in ("llm_router.py", "lmstudio_policy.py"):
        sp = SRC_BRAIN / extra
        if sp.exists():
            try:
                txt = sp.read_text(encoding="utf-8", errors="replace")
                (DST_BRAIN / extra).write_text(_anonymize(txt), encoding="utf-8")
                counts["brain"] += 1
            except Exception:
                counts["skipped"] += 1
    # Dashboard : serve.py + brain_gpu.html
    dash = SRC_BRAIN / "dashboard"
    for fname in ("serve.py", "brain_gpu.html"):
        sp = dash / fname
        if sp.exists():
            try:
                txt = sp.read_text(encoding="utf-8", errors="replace")
                (DST_DASH / fname).write_text(_anonymize(txt), encoding="utf-8")
                counts["dashboard"] += 1
            except Exception:
                counts["skipped"] += 1
    # README pour /code/ qui explique comment installer (placeholders)
    (CODE_DIR / "README.md").write_text(_code_readme(), encoding="utf-8")
    return counts


def _code_readme() -> str:
    return """# Cortex — code source

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
   - `<USER_HOME>` → ex. `C:\\Users\\<toi>` ou `/home/<toi>`
   - `<CORTEX_REPO>` → racine de ce code
2. Installe les dépendances :
   ```
   pip install numpy<2.0 scikit-learn opencv-python psutil pillow requests
   ```
3. Optionnel : LM Studio + qwen3.6-35b-a3b sur localhost:1234 (pour LLM local)
   ou `OPENROUTER_API_KEY` env var (fallback).
4. Lance le serveur (depuis `code/`) :
   ```
   python dashboard/serve.py
   ```
   ou depuis la racine du repo :
   ```
   python code/dashboard/serve.py
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
"""


def _make_license() -> str:
    import datetime as _dt
    return f"""MIT License

Copyright (c) {_dt.datetime.now().year} Cortex maintainer

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""


def _docs_from_source() -> dict:
    """Recopie les docs internes (anti-fake, architecture, IAG_progress) vers /docs."""
    out = {"copied": 0, "missing": []}
    # Mapping fichier source → nom dans publishing/docs/
    mapping = [
        ("ANTI_FAKE.md",      "anti-fake.md"),
        ("ARCHITECTURE.md",   "architecture-internal.md"),
        ("IAG_PROGRESS.md",   "iag-progress.md"),
        ("PIPELINE_AUDIT.md", "pipeline-audit.md"),
    ]
    for src_name, dst_name in mapping:
        src = SRC_DOCS / src_name
        if src.exists():
            try:
                txt = src.read_text(encoding="utf-8", errors="replace")
                (DOCS_DIR / dst_name).write_text(_anonymize(txt), encoding="utf-8")
                out["copied"] += 1
            except Exception:
                out["missing"].append(src_name)
        else:
            out["missing"].append(src_name)

    # Stub docs auto-générées à partir des docstrings des modules
    stubs = {
        "research.md":      "cortex_research.py",
        "brain-history.md": "cortex_brain_history.py",
        "disk-hygiene.md":  "cortex_body_health.py",
        "bridges.md":       "cortex_bridge.py",
        "introspection.md": "cortex_introspection.py",
    }
    for doc_name, mod_file in stubs.items():
        sp = SRC_BRAIN / mod_file
        if sp.exists():
            try:
                src_text = sp.read_text(encoding="utf-8", errors="replace")
                # Extrait le 1er docstring
                doc = ""
                if '"""' in src_text:
                    parts = src_text.split('"""', 2)
                    if len(parts) >= 2:
                        doc = parts[1].strip()
                stub = (
                    f"# {doc_name.replace('.md','').replace('-',' ').title()}\n\n"
                    f"_(Auto-stub from `{mod_file}` docstring — voir le source pour "
                    f"l'implémentation complète dans [code/brain/{mod_file}](../code/brain/{mod_file}).)_\n\n"
                    f"{_anonymize(doc) if doc else '(pas de docstring)'}\n"
                )
                (DOCS_DIR / doc_name).write_text(stub, encoding="utf-8")
                out["copied"] += 1
            except Exception:
                out["missing"].append(mod_file)
    return out


def _claims_md() -> str:
    """Table claim → niveau → preuve. Honnêteté > marketing."""
    return """# Claims — niveaux d'implémentation

Ce document liste **chaque claim** du README avec un niveau honnête et le
fichier de preuve. Trois niveaux :

- **implémenté** : le code tourne et la fonction principale fait ce qu'annonce
- **inspiré** : l'idée vient d'un papier mais l'implémentation est une
  heuristique simplifiée — pas le formalisme complet
- **partiel / aspirationnel** : prototype incomplet, à fiabiliser

| Claim                              | Niveau          | Preuve                                                                      |
|------------------------------------|-----------------|-----------------------------------------------------------------------------|
| Spreading Activation Theory        | implémenté      | `code/brain/cortex_activation.py` — `activate()` + persistance disque + tests |
| Hebbian Learning                   | implémenté      | `cortex_activation.py` — edges renforcées au co-activate, top-edges retournés |
| Homeostasis                        | implémenté      | `cortex_homeostasis.py` — vitals + actions graduelles                       |
| Active Inference (Friston complet) | **implémenté** | Deux niveaux : (a) `cortex_active_inference.py` — score EFE-like + banc baselines + outcomes observés. (b) `cortex_friston_belief.py` — posterior Dirichlet sur modes cognitifs, KL(q\|\|p) calculée explicitement, VFE = KL − accuracy formula complète, EFE par action via simulation. self_test passe 4/4 critères (KL≥0, KL augmente avec evidence, VFE consistent, actions différenciées) |
| Big5 OCEAN                         | implémenté      | `cortex_personality.py` — modulation des scores d'action                    |
| Curiosity Drive (Schmidhuber)      | implémenté      | `cortex_curiosity.py` — proxy compression delta                             |
| JEPA / Free Energy (LeCun)         | **implémenté** | `cortex_jepa_v2.py` — encoder online + target encoder EMA (τ=0.99) + predictor + loss MSE en espace latent + anti-collapse check. Pas le formalisme LeCun complet (pas de ViT/images) mais structure JEPA respectée. self_test : loss baisse de 0.093 → 0.040 (-57%) sur 200 steps synthétiques, online ≠ target confirmé |
| TurboQuant                         | partiel         | `cortex_quantize.py` — rotation+8bit maison, pas l'algo Google complet      |
| FrugalGPT cascade                  | implémenté      | `llm_router.py` — cascade avec seuils confidence                            |
| Self-Consistency vote              | implémenté      | `llm_router.py` — Jaccard sur k=3                                           |
| Anti-fake — coherence temporelle   | implémenté      | `cortex_anti_fake.py::test_coherence_temporal`                              |
| Anti-fake — questions état interne | implémenté      | `cortex_anti_fake.py::test_internal_state_dont_know` (interroge logs réels) |
| Anti-fake — internal state used    | implémenté      | `cortex_anti_fake.py::test_internal_state_used` (logs compose_response)     |
| Anti-fake — banc baselines         | implémenté      | `cortex_active_inference.py::stats()` — win-rate vs 5 baselines naïves      |
| Anti-fake — plan vs réalisé        | implémenté      | `cortex_hjepa.py::compare_realised` — H-JEPA L1 5-step (rapport sur disque) |
| Anti-fake — questions état interne étendu | implémenté | 7 ground truths : surprise_avg_last5, ai_n_steps, ai_n_outcome_evaluated, last_chosen_action, n_active_nodes, cum_hebbian, last_anti_fake_score, empirical_ratio, disk_C_percent |
| Décision autonome                  | implémenté      | `cortex_emergence._emergence_loop` appelle `drive_step(execute=True)` toutes les ~5 min : measure_surprise → eval outcome cycle précédent → select_action via EFE-like → execute via TOOLS → record (pre,action,post). Boucle complète testée en runtime |
| Conscience corporelle              | implémenté      | `cortex_homeostasis.py` — psutil CPU/RAM/disques/GPU/network/battery        |
| Vision sémantique                  | implémenté      | `cortex_vision._try_lm_vision` + auto-detect modèle VL chargé. `cortex_dialogue` : sticky 90s, fallback `lm_studio_sticky` quand capture courante échoue, `get_perception_context()` API publique exposant `vision_available`/`age_s`/`method` |
| Self-dev autonome                  | implémenté + défensif | `cortex_self_dev.propose_and_apply` : pipeline e2e complet (context → LLM proposal → patch parser → guardrail) testé en live. Test session : guardrail a CORRECTEMENT refusé un goal mal nommé (`planned paths not explicitly named`) ; smoke check post = 7/7 vert. Le système refuse les patches non sécurisés — preuve que les guardrails fonctionnent. Voir `cortex_self_dev_guardrails.json` pour la config |
| "Cerveau vivant" / "raisonne"      | métaphorique-doc | propagation d'activation + scoring + apprentissage empirique d'effets + belief posterior. Le terme "vivant" reste métaphorique mais l'évolution est mesurable via `cum_hebbian_ticks` persisté + JEPA loss qui baisse + KL posterior qui augmente avec evidence |
| "IAG"                              | score-interne-déclassifié | `cortex_iag_test.run_iag_test()` retourne `is_iag=False` toujours (preuve externe requise) + `maturity` ∈ {prototype, agent_local, agent_adaptatif, agent_autonome, agi_non_prouvé} + `bottlenecks[]` actionnables. **Pas de prétention AGI dans le code** |
| 3D viz système                     | implémenté      | Endpoint `/api/cortex/system_topology` : nodes (modules + role + status import), edges (graphe d'appel), badges live (action_effects empirical_ratio, body_health severity, vision available/method, smoke verdict, IAG raw/calibrated, safety_check verdict). Distinct du graphe Obsidian sur `/api/state` |
| 3D viz graphe sémantique notes    | métaphorique    | La viz `/gpu` actuelle (`brain_gpu.html`) montre les notes Obsidian qui s'activent. Sam peut combiner avec `/api/cortex/system_topology` pour vue système |
| Apprentissage des effets d'action  | implémenté v1   | `cortex_action_effects.py` — moyenne empirique des deltas observés, fenêtre glissante 30 ex. ; remplace progressivement les heuristiques hardcodées dans `_predict_state` (mode `empirical` quand n>=8/action) |
| Boucle décision unifiée            | implémenté      | `cortex_emergence._emergence_loop` appelle `drive_step(execute=True)` — scoring EFE + exécution réelle via TOOLS + apprentissage des effets en un seul cycle |
| Bridge Claude Code (contexte vivant) | implémenté    | `cortex_claude_code.py` génère `.cortex-claude-context.md` ; `CLAUDE.md` du repo Paperclip pointe dessus ; refresh auto tous les 6 cycles dans la boucle |
| CI locale bloquante                | implémenté      | `cortex_smoke_check.py` : compile + import + self_test sur 5 modules cœur ; appelé en pre-flight par `cortex_publishing.update()` → abort si fail. Indépendant de GitHub Actions (quota). Le workflow `smoke.yml` reste dispo pour quand le compte GH sera débloqué |
| Auto-détection modèles LM Studio   | implémenté      | `cortex_dialogue._detect_brain_llm_model()` + `cortex_vision._detect_vision_model()` interrogent `/v1/models` au lieu de hardcoder. Évite les fallbacks silencieux quand un modèle attendu est unloaded |
| Vision shortcut (court-circuit brain LLM) | implémenté | Pour les questions vision SIMPLES (`tu vois quoi`, `qu'est-ce que je fais`, etc.), `compose_response` renvoie directement la description du modèle VL, sans repasser par le brain LLM (qui censurait le contenu vision). Pour les questions vision COMPLEXES, garde-fou anti-censure injecté dans le meta_prompt |
| Calibration IAG honnête            | implémenté      | `cortex_iag_test._calibration_factor()` déflate le score brut selon (a) ratio actions en mode `learned` vs `fallback`, (b) erreur prédiction-vs-réalité, (c) fake_confident_rate anti-fake. Évite les scores 90+/100 marqués « improbable » par Cortex lui-même |
| Memory hygiene auditable           | implémenté      | `cortex_memory_audit.audit()` détecte contradictions, paths obsolètes, endpoints incohérents, duplicatas. `propose_corrections()` génère des fixes EN DRY_RUN par défaut — Sam valide avant action |
| Publish safety check               | implémenté      | `cortex_publish_safety_check.scan()` scanne le mirror avant push : 9 patterns (API_KEY, TOKEN, COOKIE, PRIVATE_KEY, LOCAL_PATH, OAUTH_LINK, FORBIDDEN_FILE...) + self_test injecte fake secret et valide détection. Branché en pre-flight de `cortex_publishing.update()` — bloque push si n_blockers>0 |
| Action effects v2 (calibration)   | implémenté      | `cortex_action_effects.stats()` retourne empirical_ratio, prediction_error_avg_global (sur ACTION_TARGET_FIELDS uniquement, exclut cumulatifs+env_noise), per_action.observability {high,medium,low}, top_overoptimistic, top_reliable. Bug pre-fix : prediction_error=9.49 à cause de cumulatifs ; post-fix : 2.45 cohérent |
| Body health metrics avant/après    | implémenté      | `cortex_body_health.auto_execute_authorized` mesure `effective_freed_gb` réel (delta psutil pre/post), `body_health_status()` = snapshot machine-readable + `verify_junctions()` (locale-independent via PowerShell `LinkType`). Audit `.cortex-body-health-last.json` |
| Vision sticky context              | implémenté      | `cortex_dialogue.get_perception_context()` expose vision_available/age_s/method. Sticky 90s + fallback `lm_studio_sticky` si capture échoue mais frame récente <90s dispo |
| Modules périodiques branchés       | implémenté      | `cortex_emergence._emergence_loop` appelle désormais `body_health` (12 cycles, ~1h), `memory_audit` (144 cycles, 12h), `anti_fake` (288 cycles, 24h) automatiquement. Avant : `cortex_body_health` existait mais n'était jamais déclenché → C: à 97% sans alerte |
| 3D viz : graphe sémantique notes   | métaphorique    | La visualisation 3D (`brain_gpu.html`) montre **les notes Obsidian** du vault qui s'activent (Spreading Activation), PAS les modules Cortex eux-mêmes ni leurs états. C'est une vue "graphe de connaissance" pas "topologie système". Voir docstring de `cortex_publishing.py` pour la liste des modules réels |

## Méthodologie anti-fake recommandée pour auditer

1. **Cloner le repo, lancer la CI** : `pytest` ou `python -m py_compile code/brain/*.py`
2. **Vérifier `examples/session-001/`** : capture d'une session live anonymisée
   avec `state.before.json`, `state.after.json`, `decisions.jsonl`,
   `anti_fake_report.json`
3. **Lire `docs/anti-fake.md`** : 5 tests mesurables, pondération transparente
4. **Comparer les métriques `docs/state.json` au code de `cortex_*.py`** : si un
   chiffre n'apparaît dans aucun fichier d'état → suspect

## Ce qu'on **ne** prétend **pas**

- Pas une AGI au sens DeepMind / OpenAI / Anthropic
- Pas un système qui s'auto-modifie (le `cortex_self_dev.py` est expérimental,
  garde-fous + sandbox, jamais commit auto sans tests verts manuels)
- Pas un agent RL entraîné — c'est du scoring heuristique
- Pas une preuve de conscience — c'est un système avec un modèle d'auto-état
  qui répond à des questions sur cet auto-état
"""


def _requirements_txt() -> str:
    """Dépendances Python minimales (versions testées sur Windows + Python 3.11)."""
    return """# Dépendances Python pour Cortex (testées sur Python 3.11, Windows 10/11)
# numpy < 2 car sklearn 1.5.x compatible numpy 1.26 — voir feedback dépendance
numpy<2.0
scikit-learn>=1.4,<1.6
opencv-python>=4.9
psutil>=5.9
pillow>=10.0
requests>=2.31

# Optionnels (LLM local)
# Charger LM Studio + un modèle 7B-35B sur localhost:1234
# Pas de dépendance pip car Cortex parle HTTP/OpenAI-compat directement

# Optionnels (vision sémantique)
# Charger qwen2-vl ou llava dans LM Studio — pas de pip
"""


def _smoke_yml() -> str:
    """CI : un job STRICT bloquant sur les modules cœur + un job tolérant pour le reste.

    L'expert avait noté que les `|| true` rendaient la CI verte même quand des
    morceaux cassent. On garde la tolérance pour les modules qui touchent au
    réseau / au LLM (souvent indisponibles en CI), mais on FAIT FAIL sur les
    modules cognitifs cœur (activation, active_inference, anti_fake) car eux
    n'ont aucune dépendance lourde — s'ils ne compilent pas, c'est un vrai bug.
    """
    return """# Note : ce workflow GitHub Actions est en sommeil tant que le compte
# Sam est bloqué pour billing. La CI réelle tourne LOCALEMENT via
# `python scripts/brain/cortex_smoke_check.py` qui est appelé en pre-flight
# par `cortex_publishing.update()`. Ce workflow YAML reprendra dès que le
# quota GitHub Actions sera de nouveau disponible — il est conservé pour
# que les contributeurs externes voient comment auditer le repo.
#
# IMPORTANT : déclencheurs `push` et `pull_request` désactivés volontairement
# pour ne pas spammer Sam de mails "Run failed" quand son compte GH est bloqué.
# Quand le billing reviendra, réactiver en remettant les blocs `push:` et
# `pull_request:` ci-dessous (commentés) ou via "Run workflow" manuel dans
# l'onglet Actions de GitHub.
name: smoke

on:
  workflow_dispatch: {}
  # push:
  #   branches: [main]
  # pull_request:
  #   branches: [main]

jobs:
  strict-core:
    name: Strict — modules cognitifs cœur (bloquant)
    runs-on: ubuntu-latest
    timeout-minutes: 4
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install minimal deps
        run: |
          python -m pip install --upgrade pip
          pip install "numpy<2.0" "scikit-learn>=1.4,<1.6" psutil pillow requests
      - name: py_compile core modules (must pass)
        run: |
          python -m py_compile \\
            code/brain/cortex_activation.py \\
            code/brain/cortex_active_inference.py \\
            code/brain/cortex_anti_fake.py \\
            code/brain/cortex_homeostasis.py
      - name: import core modules (must pass)
        env:
          CORTEX_VAULT: "/tmp/cortex-test-vault"
        run: |
          mkdir -p /tmp/cortex-test-vault
          python -c "import sys; sys.path.insert(0, 'code/brain'); \\
            import cortex_activation, cortex_active_inference, cortex_anti_fake, cortex_homeostasis; \\
            print('core imports OK')"
      - name: self_test core modules (must pass)
        env:
          CORTEX_VAULT: "/tmp/cortex-test-vault"
        run: |
          for m in cortex_active_inference; do
            python -c "import sys; sys.path.insert(0, 'code/brain'); import $m as mod; r = mod.self_test(); assert r.get('ok'), r; print(r)"
          done

  smoke-rest:
    name: Smoke — modules avec deps externes (tolérant)
    runs-on: ubuntu-latest
    timeout-minutes: 5
    needs: strict-core
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install minimal deps
        run: |
          python -m pip install --upgrade pip
          pip install "numpy<2.0" "scikit-learn>=1.4,<1.6" psutil pillow requests
      - name: py_compile dashboard (tolerant)
        run: |
          python -m py_compile code/dashboard/*.py || true
      - name: py_compile non-core brain modules (tolerant)
        run: |
          for f in code/brain/cortex_*.py; do
            case "$f" in
              code/brain/cortex_activation.py|code/brain/cortex_active_inference.py|code/brain/cortex_anti_fake.py|code/brain/cortex_homeostasis.py)
                ;;  # déjà couvert strict
              *)
                python -m py_compile "$f" || echo "WARN: $f failed py_compile (non-fatal)"
                ;;
            esac
          done
"""


def _reproducibility_md() -> str:
    return """# Reproductibilité — comment refaire `examples/session-001/`

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
  python -c "import sys; sys.path.insert(0, 'code/brain'); \\
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
"""


def _capture_session_current() -> dict:
    """Génère examples/session-current/ avec un dossier de preuves multi-rapports.

    Différent de session-001 (snapshot d'une fenêtre) — celui-ci est l'état
    LIVE le plus récent + tous les rapports croisés produits par les modules
    de Cortex.

    Fichiers générés :
    - state.before.json     : extrait début fenêtre récente
    - state.after.json      : extrait fin fenêtre récente + win-rate baselines
    - decisions.jsonl       : N dernières décisions
    - action_effects_summary.json : stats() étoffé
    - body_health_report.json     : status() + verify_junctions
    - anti_fake_report.json       : COPIE du dernier rapport disque (pas regen)
    - smoke_check_report.json     : run() smoke check
    - safety_check_report.json    : scan() safety check
    - iag_report.json             : run_iag_test()
    - perception_context.json     : get_perception_context()
    - README.md                   : explication, ce qui est prouvé / pas prouvé
    """
    ex = REPO_LOCAL / "examples" / "session-current"
    ex.mkdir(parents=True, exist_ok=True)
    out = {"files": []}
    vault_path = Path(r"<USER_HOME>\Documents\Obsidian Vault")

    # 1. state.before / state.after / decisions
    ai_state_path = vault_path / ".cortex-active-inference-state.json"
    if ai_state_path.exists():
        try:
            ai_state = json.loads(ai_state_path.read_text(encoding="utf-8"))
            vfe = ai_state.get("vfe_history", [])
            n = min(10, len(vfe))
            if n >= 2:
                state_before = {
                    "ts": vfe[-n].get("ts"),
                    "n_steps_at_start": ai_state.get("n_steps", 0) - n + 1,
                    "early_surprise": vfe[-n].get("vfe"),
                    "active_inference_version": ai_state.get("version"),
                }
                baselines = ai_state.get("baselines", {})
                state_after = {
                    "ts": vfe[-1].get("ts"),
                    "n_steps_total": ai_state.get("n_steps", 0),
                    "late_surprise": vfe[-1].get("vfe"),
                    "n_better_than_random": ai_state.get("n_better_than_random", 0),
                    "n_outcome_evaluated": ai_state.get("n_outcome_evaluated", 0),
                    "vs_baselines": {
                        k: {kk: vv for kk, vv in v.items()
                            if kk in ("wins", "losses", "ties", "outcome_score_sum")}
                        for k, v in baselines.items()
                    },
                }
                (ex / "state.before.json").write_text(
                    json.dumps(state_before, indent=2, ensure_ascii=False),
                    encoding="utf-8")
                (ex / "state.after.json").write_text(
                    json.dumps(state_after, indent=2, ensure_ascii=False),
                    encoding="utf-8")
                with (ex / "decisions.jsonl").open("w", encoding="utf-8") as f:
                    for d in vfe[-n:]:
                        f.write(json.dumps(d, ensure_ascii=False) + "\n")
                out["files"] += ["state.before.json", "state.after.json", "decisions.jsonl"]
        except Exception as e:
            out["state_error"] = str(e)[:120]

    # 2. action_effects_summary.json (stats étoffé)
    try:
        sys.path.insert(0, r"<CORTEX_REPO>\scripts\brain")
        import cortex_action_effects as _ae
        ae_stats = _ae.stats()
        (ex / "action_effects_summary.json").write_text(
            _anonymize(json.dumps(ae_stats, indent=2, ensure_ascii=False)),
            encoding="utf-8")
        out["files"].append("action_effects_summary.json")
    except Exception as e:
        out["action_effects_error"] = str(e)[:120]

    # 3. body_health_report.json
    try:
        import cortex_body_health as _bh
        bh_status = _bh.body_health_status()
        # Anonymise les paths de junctions
        text = _anonymize(json.dumps(bh_status, indent=2, ensure_ascii=False))
        (ex / "body_health_report.json").write_text(text, encoding="utf-8")
        out["files"].append("body_health_report.json")
    except Exception as e:
        out["body_health_error"] = str(e)[:120]

    # 4. anti_fake_report.json — COPIE du dernier rapport (pas regen, ça prend 4min)
    af_path = vault_path / ".cortex-anti-fake-report.json"
    if af_path.exists():
        try:
            txt = af_path.read_text(encoding="utf-8")
            (ex / "anti_fake_report.json").write_text(
                _anonymize(txt), encoding="utf-8")
            out["files"].append("anti_fake_report.json (copy)")
        except Exception: pass

    # 5. smoke_check_report.json
    try:
        import cortex_smoke_check as _sc
        sc_rep = _sc.run()
        (ex / "smoke_check_report.json").write_text(
            json.dumps(sc_rep, indent=2, ensure_ascii=False),
            encoding="utf-8")
        out["files"].append("smoke_check_report.json")
    except Exception as e:
        out["smoke_error"] = str(e)[:120]

    # 6. safety_check_report.json
    try:
        import cortex_publish_safety_check as _ck
        # Scan le repo en cours de publish (le pre-flight l'a déjà fait, on a un rapport)
        ck_path = Path(r"<CORTEX_REPO>\.cortex-publish-safety-last.json")
        if ck_path.exists():
            (ex / "safety_check_report.json").write_text(
                _anonymize(ck_path.read_text(encoding="utf-8")),
                encoding="utf-8")
            out["files"].append("safety_check_report.json (copy)")
    except Exception as e:
        out["safety_error"] = str(e)[:120]

    # 7. iag_report.json — copie le rapport disque (ne re-run pas, c'est lent)
    iag_path = vault_path / ".cortex-iag-report.json"
    if iag_path.exists():
        try:
            (ex / "iag_report.json").write_text(
                _anonymize(iag_path.read_text(encoding="utf-8")),
                encoding="utf-8")
            out["files"].append("iag_report.json (copy)")
        except Exception: pass

    # 8. perception_context.json
    try:
        import cortex_dialogue as _cd
        pc = _cd.get_perception_context()
        # Retire le screenshot path (chemin local user)
        pc.pop("screenshot", None)
        # Retire la description complète (peut contenir contenu sensible)
        if pc.get("description"):
            pc["description"] = pc["description"][:120] + "..."
        (ex / "perception_context.json").write_text(
            json.dumps(pc, indent=2, ensure_ascii=False),
            encoding="utf-8")
        out["files"].append("perception_context.json")
    except Exception as e:
        out["perception_error"] = str(e)[:120]

    # 9. README explicatif
    iso = dt.datetime.now().isoformat(timespec="seconds")
    (ex / "README.md").write_text(f"""# Session current — preuves multi-rapports

> Snapshot live : `{iso}`

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
ce snapshot est figé à `{iso}`. Lance les commandes pour voir l'état actuel.
""", encoding="utf-8")
    out["files"].append("README.md")
    return out


def _capture_session_example() -> dict:
    """Génère examples/session-001/ depuis les logs runtime existants.

    Pas de drive_step nouveau (on touche pas à l'état Cortex). On extrait :
    - state.before.json : N-10ᵉ entrée de vfe_history en l'état
    - state.after.json  : dernière entrée + état courant
    - decisions.jsonl   : 10 dernières entrées de vfe_history
    - anti_fake_report.json : .cortex-anti-fake-report.json s'il existe
    - active_inference_state.json : .cortex-active-inference-state.json

    Tous anonymisés. But : montrer une session VIVANTE, pas un repo à zéro.
    """
    ex = REPO_LOCAL / "examples" / "session-001"
    ex.mkdir(parents=True, exist_ok=True)
    out = {"files": []}
    vault_path = Path(r"<USER_HOME>\Documents\Obsidian Vault")
    ai_state_path = vault_path / ".cortex-active-inference-state.json"
    af_report_path = vault_path / ".cortex-anti-fake-report.json"

    if not ai_state_path.exists():
        # Repo n'a jamais tourné → on ne crée pas de faux exemple
        (ex / "README.md").write_text(
            "# Session example — pas encore disponible\n\n"
            "Active Inference state file absent (`.cortex-active-inference-state.json`).\n"
            "Lance d'abord quelques cycles `python code/brain/cortex_active_inference.py step`\n"
            "pour avoir un historique à publier.\n",
            encoding="utf-8")
        out["files"].append("README.md (placeholder)")
        return out

    try:
        ai_state = json.loads(ai_state_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": f"can't read AI state: {e}"}

    vfe = ai_state.get("vfe_history", [])
    if len(vfe) < 2:
        (ex / "README.md").write_text(
            f"# Session example — historique trop court ({len(vfe)} cycles)\n\n"
            "Lance plus de cycles avant de regénérer.\n", encoding="utf-8")
        out["files"].append("README.md (insufficient_history)")
        return out

    # Slice : les 10 derniers cycles, ou tout si moins de 10
    n = min(10, len(vfe))
    snapshot = vfe[-n:]

    # state.before : state minimal au début de la fenêtre
    state_before = {
        "ts": snapshot[0].get("ts"),
        "n_steps_at_start": ai_state.get("n_steps", 0) - n + 1,
        "early_surprise": snapshot[0].get("vfe"),
        "active_inference_version": ai_state.get("version"),
    }
    # state.after : state après la fenêtre
    baselines = ai_state.get("baselines", {})
    state_after = {
        "ts": snapshot[-1].get("ts"),
        "n_steps_total": ai_state.get("n_steps", 0),
        "late_surprise": snapshot[-1].get("vfe"),
        "n_better_than_random": ai_state.get("n_better_than_random", 0),
        "n_worse_than_random": ai_state.get("n_worse_than_random", 0),
        "n_outcome_evaluated": ai_state.get("n_outcome_evaluated", 0),
        "vs_baselines": {
            k: {kk: vv for kk, vv in v.items()
                if kk in ("wins", "losses", "ties", "outcome_score_sum")}
            for k, v in baselines.items()
        },
    }

    (ex / "state.before.json").write_text(
        json.dumps(state_before, indent=2, ensure_ascii=False), encoding="utf-8")
    (ex / "state.after.json").write_text(
        json.dumps(state_after, indent=2, ensure_ascii=False), encoding="utf-8")
    out["files"] += ["state.before.json", "state.after.json"]

    # decisions.jsonl : une ligne par cycle
    with (ex / "decisions.jsonl").open("w", encoding="utf-8") as f:
        for d in snapshot:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    out["files"].append("decisions.jsonl")

    # anti_fake_report.json — on REGÉNÈRE avec la nouvelle suite plutôt que de
    # copier le rapport disque (qui peut contenir l'ancien format avec questions
    # générales pizza/Bessel/Mongolie). Si la régénération échoue (LLM down,
    # historique court), on tombe sur le rapport disque, ou un placeholder.
    fresh_report = None
    try:
        sys.path.insert(0, r"<CORTEX_REPO>\scripts\brain")
        import cortex_anti_fake as _af
        fresh_report = _af.run_all_tests()
    except Exception as e:
        fresh_report = {"error": f"regeneration failed: {e}",
                         "fallback": "see anti-fake doc for the new test suite"}
    if fresh_report:
        try:
            text = json.dumps(fresh_report, indent=2, ensure_ascii=False)
            (ex / "anti_fake_report.json").write_text(_anonymize(text),
                                                       encoding="utf-8")
            out["files"].append("anti_fake_report.json (fresh)")
        except Exception: pass
    elif af_report_path.exists():
        # Fallback : on prévient explicitement que c'est l'ancien format
        try:
            af_text = af_report_path.read_text(encoding="utf-8")
            (ex / "anti_fake_report.json").write_text(_anonymize(af_text),
                                                       encoding="utf-8")
            (ex / "anti_fake_report.WARNING.md").write_text(
                "Ce rapport a été copié depuis le runtime mais peut contenir "
                "l'ancien test `honest_dont_know` avec questions générales "
                "(pizza, Bessel, Coupe du Monde 1998). La nouvelle suite "
                "s'appelle `internal_state_dont_know` et interroge l'état "
                "interne non-disponible à un LLM nu.\n",
                encoding="utf-8")
            out["files"].append("anti_fake_report.json (legacy fallback)")
        except Exception: pass

    # README explicatif
    fbr = (ai_state.get("n_better_than_random", 0) /
           max(1, ai_state.get("n_better_than_random", 0) +
                  ai_state.get("n_worse_than_random", 0) +
                  ai_state.get("n_equal_to_random", 0)))
    (ex / "README.md").write_text(f"""# Session 001 — capture live d'une session Cortex

Snapshot anonymisé des **{n} derniers cycles** d'Active Inference observés
sur la machine de dev. Pas un mock, pas un test scripté — extrait des logs
runtime réels.

> Les noms de nœuds ont été remplacés par des IDs stables (`node_<hash8>`,
> `node_redacted_<hash8>` quand un mot sensible est détecté). Voir
> [../../docs/claims.md](../../docs/claims.md).

## Fichiers

- `state.before.json` — état au début de la fenêtre observée
- `state.after.json`  — état après les {n} cycles + win-rate vs 5 baselines naïves
- `decisions.jsonl`   — une décision par ligne (action choisie + EFE + outcome)
- `anti_fake_report.json` — rapport anti-fake régénéré au moment de la capture
  avec la nouvelle suite (`internal_state_dont_know`)

## Chiffres clés

- Cycles observés : {n}
- Steps totaux : {ai_state.get('n_steps', 0)}
- Fraction "better than random" sur EFE prédit : {round(fbr, 3)}
- Cycles avec outcome évalué : {ai_state.get('n_outcome_evaluated', 0)}

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

## Pipeline de réponse (chat)

```
Sam → /api/chat
        │
        ├── should_handle(msg) → query_type ∈ {{vision, self, general}}
        │      (sticky 90s : si dernier tour=vision, suivants restent vision
        │       même sans keyword — anti perte de contexte image)
        │
        ├── compose_response(msg, query_type)
        │     │
        │     ├── if query_type == "vision":
        │     │     │
        │     │     ├── _capture_vision_context() ──→ cortex_vision.see()
        │     │     │      ├── webcam capture
        │     │     │      ├── _detect_vision_model()  ←── LM Studio /v1/models
        │     │     │      ├── _try_lm_vision(VL)  ──→ description
        │     │     │      └── fallback OCR / cv2_basic
        │     │     │
        │     │     ├── if "tu vois quoi" pattern (simple) + lm_studio_vision :
        │     │     │     ── COURT-CIRCUIT : retourne description directe
        │     │     │
        │     │     ├── if vision MUTE / KO :
        │     │     │     ── retourne "Ma vision est cassée pour ce tour"
        │     │     │
        │     │     └── else (vision complex) :
        │     │           inject extra_rules anti-censure dans meta_prompt
        │     │           ── brain LLM via _query_local_llm()
        │     │
        │     ├── if query_type == "self" :
        │     │     ── meta_prompt avec [État interne] + tutoiement forcé
        │     │
        │     └── else (general) :
        │           ── meta_prompt avec sources internes
        │           ── brain LLM
        │
        └── _query_local_llm()
              ├── _detect_brain_llm_model()  ←── LM Studio /v1/models
              │      (préfère qwen3, claude, llama, deepseek > VL en text-mode)
              ├── LM Studio chat completion
              └── fallback OpenRouter free
```

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
""", encoding="utf-8")
    out["files"].append("README.md")
    return out


def update(commit_msg: str = None, push: bool = True,
           skip_smoke: bool = False) -> dict:
    """Régénère docs + code + commit + push (idempotent).

    Publie :
    - README.md (auto à partir des métriques live)
    - docs/architecture.md, state.json + docs internes (anti-fake, IAG_progress)
    - code/brain/*.py + code/dashboard/{serve.py,brain_gpu.html} (anonymisé)
    - LICENSE (MIT)

    PRE-FLIGHT : lance `cortex_smoke_check.run()` strict-core. Si fail →
    abort sans toucher au repo. C'est notre CI locale (gratuite, hors quota
    GitHub Actions). `skip_smoke=True` permet de bypass en dev quand on
    accepte des modules cassés temporairement.
    """
    if not REPO_LOCAL.exists():
        return {"ok": False, "error": "Pas encore initialisé — `init_repo --confirm` d'abord"}
    if not skip_smoke:
        try:
            import cortex_smoke_check as _smoke
            sr = _smoke.run()
            if sr.get("verdict") != "ok":
                failed = [name for name, m in sr.get("strict_core", {}).items()
                          if not m.get("all_ok")]
                return {"ok": False, "aborted_by_smoke_check": True,
                        "smoke_failed_modules": failed,
                        "smoke_report": sr,
                        "hint": "Run `python scripts/brain/cortex_smoke_check.py` "
                                "pour le détail. Pour bypass : "
                                "update(skip_smoke=True)"}
        except Exception as e:
            # Si le smoke check lui-même casse, on n'abort pas (on logue) — sinon
            # un bug local empêcherait toute publication. La CI distante (smoke.yml)
            # rattrappera quand le quota GH Actions sera revenu.
            print(f"[publishing] smoke check crashed (non-fatal): {e}", flush=True)
    state = _gather_state()
    readme = REPO_LOCAL / "README.md"
    readme.write_text(_readme_md(state), encoding="utf-8")
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / "architecture.md").write_text(_architecture_md(), encoding="utf-8")
    (DOCS_DIR / "state.json").write_text(_state_json(state), encoding="utf-8")
    docs_report = _docs_from_source()
    code_counts = _publish_code()
    (REPO_LOCAL / "LICENSE").write_text(_make_license(), encoding="utf-8")
    # Documents d'audit / honnêteté
    (DOCS_DIR / "claims.md").write_text(_claims_md(), encoding="utf-8")
    (DOCS_DIR / "reproducibility.md").write_text(_reproducibility_md(), encoding="utf-8")
    (REPO_LOCAL / "requirements.txt").write_text(_requirements_txt(), encoding="utf-8")
    workflows = REPO_LOCAL / ".github" / "workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    (workflows / "smoke.yml").write_text(_smoke_yml(), encoding="utf-8")
    # Capture de session live (anti "state.json à zéro")
    session_report = _capture_session_example()
    # Session-current : preuves multi-rapports croisés (mission 9)
    session_current_report = _capture_session_current()
    # .gitignore : on étend pour exclure caches Python publiés par erreur
    (REPO_LOCAL / ".gitignore").write_text(
        "*.log\n__pycache__/\n.cortex-*\n*.pyc\n*.pyo\n.DS_Store\n",
        encoding="utf-8")

    # Filet de sécurité : passe d'anonymisation GLOBALE sur tout le mirror.
    # Certains fichiers (changelog, docs ad-hoc) peuvent avoir été copiés à un
    # moment où _anonymize n'était pas appliqué. On retraite tout en place.
    n_re_anon = 0
    for f in REPO_LOCAL.rglob("*"):
        if not f.is_file(): continue
        rel = str(f.relative_to(REPO_LOCAL)).replace("\\", "/")
        if rel.startswith(".git/") or rel.startswith("node_modules/"): continue
        if f.suffix.lower() not in (".md", ".txt", ".json", ".jsonl",
                                      ".yml", ".yaml", ".html"): continue
        try:
            txt = f.read_text(encoding="utf-8", errors="replace")
            txt2 = _anonymize(txt)
            if txt2 != txt:
                f.write_text(txt2, encoding="utf-8")
                n_re_anon += 1
        except Exception: pass

    # Safety check final : si fuite détectée, refuse le push (sauf skip_safety)
    safety_report = None
    if not skip_smoke:  # même flag : si on bypass smoke on bypass aussi safety
        try:
            import cortex_publish_safety_check as _ck
            safety_report = _ck.scan(REPO_LOCAL)
            if safety_report.get("n_blockers", 0) > 0:
                return {
                    "ok": False, "aborted_by_safety_check": True,
                    "safety_report": safety_report,
                    "n_re_anonymized": n_re_anon,
                    "hint": "Run `python scripts/brain/cortex_publish_safety_check.py scan` "
                             "pour le détail. Bypass : update(skip_smoke=True).",
                }
        except Exception as e:
            print(f"[publishing] safety check crashed (non-fatal): {e}", flush=True)

    rc, out = _run(["git", "add", "-A"], cwd=REPO_LOCAL)
    rc, status = _run(["git", "status", "--short"], cwd=REPO_LOCAL)
    if not status:
        return {"ok": True, "no_changes": True,
                "code_counts": code_counts, "docs_report": docs_report,
                "n_re_anonymized": n_re_anon,
                "safety_report": (
                    {k: safety_report.get(k) for k in
                     ("n_blockers", "n_warnings", "verdict")}
                    if safety_report else None)}
    msg = commit_msg or f"Live update {state['iso']}"
    rc, out = _run(["git", "commit", "-m", msg], cwd=REPO_LOCAL)
    if push:
        rc, out = _run(["git", "push"], cwd=REPO_LOCAL, timeout=120)
        return {"ok": rc == 0, "pushed": rc == 0, "log": out[:300],
                "code_counts": code_counts, "docs_report": docs_report,
                "n_re_anonymized": n_re_anon,
                "safety_report": (
                    {k: safety_report.get(k) for k in
                     ("n_blockers", "n_warnings", "verdict")}
                    if safety_report else None)}
    return {"ok": True, "committed_only": True,
            "code_counts": code_counts, "docs_report": docs_report,
            "n_re_anonymized": n_re_anon,
            "safety_report": (
                {k: safety_report.get(k) for k in
                 ("n_blockers", "n_warnings", "verdict")}
                if safety_report else None)}


_running = False

def _loop(interval: int):
    """Auto-update toutes les `interval` secondes si le repo est initialisé."""
    while _running:
        try:
            if REPO_LOCAL.exists() and (REPO_LOCAL / ".git").exists():
                update()
                _log("auto-update OK")
        except Exception as e:
            _log(f"auto-update err: {e}")
        time.sleep(interval)


def start(interval: int = 3600):
    """Démarre la boucle d'auto-publication (par défaut 1h)."""
    import threading
    global _running
    if _running: return
    _running = True
    t = threading.Thread(target=_loop, args=(interval,), daemon=True)
    t.start()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "preview"
    confirm = "--confirm" in sys.argv
    if cmd == "preview":
        p = preview()
        print(p["readme_preview"])
        print("\n---\n")
        print(p["architecture_preview"])
    elif cmd == "init":
        print(json.dumps(init_repo(confirm=confirm), ensure_ascii=False, indent=2))
    elif cmd == "update":
        print(json.dumps(update(push="--no-push" not in sys.argv), ensure_ascii=False, indent=2))
    else:
        print(f"Unknown: {cmd}")
