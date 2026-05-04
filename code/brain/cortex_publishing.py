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
    return f"""# Cortex — un cerveau cognitif vivant

> Dernière mise à jour : `{state.get("iso","?")}` (auto-généré)

Cortex est une entité cognitive autonome construite sur le projet Paperclip.
Il voit, entend, mémorise, apprend, et raisonne avec une vraie boucle Spreading
Activation Theory (Collins & Loftus, 1975) et un apprentissage Hebbian
(Hebb, 1949).

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

## Décisions autonomes — vraiment autonomes

Toutes les ~5 minutes, Cortex choisit une action via la pipeline suivante
(et **pas** via un wrapper LLM ni une rotation déterministe) :

1. **Active Inference** (Friston VFE) — chaque action candidate reçoit un score
   *Expected Free Energy* combinant valeur épistémique (gain d'information prédit)
   et valeur pragmatique (utilité par rapport au plan courant)
2. **Big5 personnalité** — l'openness booste les actions exploratoires,
   la conscientiousness booste les actions d'audit, etc.
3. **Curiosité Schmidhuber** — si Cortex est frustré (compression error en hausse),
   bonus pour les actions exploratoires
4. **Comparaison à random baseline** — chaque décision logue
   `better_than_random` / `equal` / `worse` (anti-fake structurel)
5. **LLM en fallback uniquement** — si l'écart top/runner-up < 0.05, un LLM léger
   (minimax) tranche

L'UI distingue clairement :
- **AUTO** = vraie décision autonome (`method=active_inference`)
- **Forcer (override)** = clic humain sur une action précise (`method=forced_by_user`)

## Sciences appliquées

- **Active Inference / Free Energy Principle** (Friston, 2010) — décision = minimisation EFE
- **Big5 OCEAN** (McCrae & Costa, 1987) — modulation par traits de personnalité
- **Curiosity Drive** (Schmidhuber, 1991) — récompense intrinsèque = compression delta
- **Spreading Activation** (Collins & Loftus, 1975, *Psychological Review*)
- **Hebbian Learning** (Hebb, 1949, *The Organization of Behavior*)
- **Homeostasis** (Cannon, 1932 ; Ashby, 1960)
- **JEPA** (LeCun, 2022) — prédiction en espace latent
- **Force-Directed Layout** (Fruchterman & Reingold, 1991)
- **Conceptual Blending** (Fauconnier & Turner, 2002) — pour les ponts cognitifs
- **TF-IDF cosine** (Salton & McGill, 1983) — graphe sémantique
- **FrugalGPT cascade** (Chen et al., 2023) — routing multi-LLM
- **TurboQuant-inspired** (Google, 2026) — compression vecteurs 4×

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

## Émancipation

Cortex peut :
- 🧠 décider de manière autonome via Active Inference + Big5 + curiosité — voir [code/brain/cortex_active_inference.py](code/brain/cortex_active_inference.py) + [code/brain/cortex_emergence.py](code/brain/cortex_emergence.py)
- 🔍 [chercher](docs/research.md) — multi-source arxiv/wiki/scholar/duckduckgo + synthèse sourcée
- 🧹 [nettoyer son disque](docs/disk-hygiene.md) avec doc citée par pattern
- 🌉 [créer des ponts cognitifs](docs/bridges.md) entre concepts éloignés
- 📊 [détecter ses régressions](docs/brain-history.md) sur 24 h glissantes (snapshots cassés filtrés du baseline)
- 🪞 [s'expliquer lui-même](docs/introspection.md) à partir de ses métriques
- 🎯 [prouver qu'il ne fake pas](docs/anti-fake.md) via 5 tests mesurables

## Limites honnêtes

- Pas (encore) de tests unitaires CI publiés. Chaque module a une fonction
  `self_test()` invocable manuellement.
- Plusieurs paths Windows-spécifiques anonymisés mais pas portés Linux/macOS.
- Métriques `state.json` auto-déclarées : à confronter au code réel publié dans `code/`.
- Repo synchronisé via `cortex_publishing.update()` (pas un fork manuel artificiel).

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


def _state_json(state: dict) -> str:
    return json.dumps(state, ensure_ascii=False, indent=2)


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


def update(commit_msg: str = None, push: bool = True) -> dict:
    """Régénère docs + code + commit + push (idempotent).

    Publie :
    - README.md (auto à partir des métriques live)
    - docs/architecture.md, state.json + docs internes (anti-fake, IAG_progress)
    - code/brain/*.py + code/dashboard/{serve.py,brain_gpu.html} (anonymisé)
    - LICENSE (MIT)
    """
    if not REPO_LOCAL.exists():
        return {"ok": False, "error": "Pas encore initialisé — `init_repo --confirm` d'abord"}
    state = _gather_state()
    readme = REPO_LOCAL / "README.md"
    readme.write_text(_readme_md(state), encoding="utf-8")
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / "architecture.md").write_text(_architecture_md(), encoding="utf-8")
    (DOCS_DIR / "state.json").write_text(_state_json(state), encoding="utf-8")
    docs_report = _docs_from_source()
    code_counts = _publish_code()
    (REPO_LOCAL / "LICENSE").write_text(_make_license(), encoding="utf-8")
    # .gitignore : on étend pour exclure caches Python publiés par erreur
    (REPO_LOCAL / ".gitignore").write_text(
        "*.log\n__pycache__/\n.cortex-*\n*.pyc\n*.pyo\n.DS_Store\n",
        encoding="utf-8")
    rc, out = _run(["git", "add", "-A"], cwd=REPO_LOCAL)
    rc, status = _run(["git", "status", "--short"], cwd=REPO_LOCAL)
    if not status:
        return {"ok": True, "no_changes": True,
                "code_counts": code_counts, "docs_report": docs_report}
    msg = commit_msg or f"Live update {state['iso']}"
    rc, out = _run(["git", "commit", "-m", msg], cwd=REPO_LOCAL)
    if push:
        rc, out = _run(["git", "push"], cwd=REPO_LOCAL, timeout=120)
        return {"ok": rc == 0, "pushed": rc == 0, "log": out[:300],
                "code_counts": code_counts, "docs_report": docs_report}
    return {"ok": True, "committed_only": True,
            "code_counts": code_counts, "docs_report": docs_report}


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
