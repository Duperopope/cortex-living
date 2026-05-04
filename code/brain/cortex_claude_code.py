"""
cortex_claude_code.py — Bridge Cortex ↔ Claude Code.

Sam veut qu'il n'y ait jamais de placeholder ET que Claude Code (Anthropic
CLI) soit un membre du système Cortex. Concrètement :

- À chaque cycle d'émergence, Cortex écrit un **contexte vivant** dans
  `<CORTEX_REPO>/.cortex-claude-context.md`. Ce fichier est l'état
  cognitif de Cortex à un instant T : graphe, activations, dernières
  décisions, gaps détectés, plan en cours, score anti-fake.

- Le `CLAUDE.md` du projet Paperclip pointe vers ce fichier. Quand Sam
  ouvre une session Claude Code dans `<CORTEX_REPO>/`, l'agent lit
  automatiquement le contexte au démarrage — plus besoin de re-briefer.

- Une nouvelle action `update_claude_context` est ajoutée à la table
  TOOLS de cortex_emergence : Cortex peut décider de raffraîchir son
  briefing pour Claude Code quand il sent que l'état a beaucoup bougé.

Pas de subprocess Claude Code lancé depuis Cortex (Sam reste l'opérateur,
seul lui décide d'ouvrir une session). Cortex prépare seulement le terrain.

Référence : pattern "shared context file" plutôt que "RPC vers l'agent".
Plus simple, plus auditable, indépendant des credentials.
"""
from __future__ import annotations
import datetime as dt
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
PAPERCLIP_ROOT = Path(r"<CORTEX_REPO>")
VAULT = Path(r"<USER_HOME>\Documents\Obsidian Vault")

# Le fichier que Claude Code lira au démarrage de chaque session
CONTEXT_FILE = PAPERCLIP_ROOT / ".cortex-claude-context.md"
LOG = PAPERCLIP_ROOT / ".cortex-claude-context.log"


def _safe_import(name: str):
    try:
        sys.path.insert(0, str(REPO))
        return __import__(name)
    except Exception:
        return None


def _section_active_inference() -> str:
    ai = _safe_import("cortex_active_inference")
    if not ai: return "_(active inference indisponible)_"
    try:
        s = ai.stats()
        baselines = s.get("vs_baselines", {}) or {}
        lines = [
            f"- **n_steps** : {s.get('n_steps', 0)}",
            f"- **fraction_better_than_random** (EFE) : {round(s.get('fraction_better_than_random', 0), 3)}",
            f"- **surprise_trend** : {s.get('surprise_trend')} ({'apprend' if s.get('is_learning') else 'plateau'})",
            f"- **n_outcome_evaluated** : {s.get('n_outcome_evaluated', 0)}",
            f"- **cortex_avg_outcome_proxy** : {s.get('cortex_avg_outcome_proxy')}",
            f"- **cortex_avg_outcome_observed** : {s.get('cortex_avg_outcome_observed')} (= delta réel post-action ; si <<proxy → modèle sur-optimiste)",
        ]
        if baselines:
            lines.append("- **vs baselines (win-rate sur outcome proxy)** :")
            for bname, b in baselines.items():
                wr = b.get("win_rate")
                lines.append(f"  - {bname}: win_rate={wr} (n={b.get('n', 0)})")
        return "\n".join(lines)
    except Exception as e:
        return f"_(erreur active_inference: {e})_"


def _section_action_effects() -> str:
    ae = _safe_import("cortex_action_effects")
    if not ae: return "_(action_effects indisponible)_"
    try:
        s = ae.summary()
        if not s.get("actions"):
            return "_(aucun exemple appris encore — `_predict_state` est en mode heuristic_fallback pour toutes les actions)_"
        lines = [f"- **min_samples (seuil bascule learned)** : {s.get('min_samples')}",
                 f"- **window** : {s.get('window')}"]
        for a, info in s.get("actions", {}).items():
            lines.append(f"- `{a}` : n={info['n_examples']} status=**{info['status']}**")
        return "\n".join(lines)
    except Exception as e:
        return f"_(erreur action_effects: {e})_"


def _section_brain() -> str:
    bh = _safe_import("cortex_brain_history")
    ca = _safe_import("cortex_activation")
    out = []
    if bh:
        try:
            cur = bh.evolution_summary().get("current", {}) or {}
            out.append(f"- **graphe** : {cur.get('n_nodes', 0)} nœuds, {cur.get('n_edges', 0)} arêtes, "
                       f"densité {cur.get('density', '?')}")
            out.append(f"- **hebbian_total** : {cur.get('hebbian_total', 0)}")
            out.append(f"- **isolated** : {cur.get('n_isolated', 0)} (zones d'ignorance)")
        except Exception as e:
            out.append(f"_(erreur brain_history: {e})_")
    if ca:
        try:
            snap = ca.snapshot()
            out.append(f"- **n_active** : {snap.get('n_active', 0)} nœuds activés (décroissance τ=180s)")
            out.append(f"- **cum_hebbian_ticks** (long-terme, persisté) : {snap.get('cum_hebbian_ticks', 0)}")
            out.append(f"- **cum_pulses** : {snap.get('cum_pulses', 0)}")
        except Exception as e:
            out.append(f"_(erreur activation: {e})_")
    return "\n".join(out) if out else "_(brain modules unavailable)_"


def _section_body() -> str:
    ch = _safe_import("cortex_homeostasis")
    if not ch: return "_(homeostasis indisponible)_"
    try:
        v = ch.vital_signs() or {}
        # vital_signs peut renvoyer plusieurs formats selon la version
        cpu = v.get("cpu_percent") or (v.get("cpu") or {}).get("percent")
        ram = v.get("ram_percent") or (v.get("ram") or {}).get("percent")
        return (f"- **CPU** : {cpu}%\n"
                f"- **RAM** : {ram}%\n"
                f"- **disques** : {len(v.get('disks', []))} surveillés\n"
                f"- **GPU** : {[g.get('name') for g in (v.get('gpu') or [])]}")
    except Exception as e:
        return f"_(erreur homeostasis: {e})_"


def _section_anti_fake() -> str:
    """Lit le dernier rapport anti-fake si disponible."""
    p = VAULT / ".cortex-anti-fake-report.json"
    if not p.exists():
        return "_(aucun rapport anti-fake encore — lance `python scripts/brain/cortex_anti_fake.py full`)_"
    try:
        r = json.loads(p.read_text(encoding="utf-8"))
        score = r.get("score_global")
        verdict = r.get("verdict")
        out = [f"- **score global** : **{score}**/100 — {verdict}"]
        for name, t in (r.get("tests") or {}).items():
            sc = t.get("score")
            extra = t.get("reason") or t.get("verdict") or ""
            out.append(f"  - `{name}` : {sc} — {extra}")
        return "\n".join(out)
    except Exception as e:
        return f"_(erreur lecture anti-fake: {e})_"


def _section_recent_decisions() -> str:
    """Liste les 5 dernières décisions Active Inference."""
    p = VAULT / ".cortex-active-inference-state.json"
    if not p.exists(): return "_(pas d'historique)_"
    try:
        s = json.loads(p.read_text(encoding="utf-8"))
        vfe = s.get("vfe_history") or []
        if not vfe: return "_(historique vide)_"
        out = []
        for h in vfe[-5:]:
            ts = h.get("ts", 0)
            iso = dt.datetime.fromtimestamp(ts).isoformat(timespec="seconds") if ts else "?"
            chosen = h.get("chosen", "?")
            outcome_obs = h.get("outcome_score")
            outcome_proxy = h.get("outcome_proxy")
            out.append(f"- `{iso}` → **{chosen}** "
                       f"(observed={outcome_obs}, proxy={outcome_proxy})")
        return "\n".join(out)
    except Exception as e:
        return f"_(erreur: {e})_"


def _section_pending_for_claude() -> str:
    """Ce que Claude Code peut faire avec ce contexte. Volontairement actionnable."""
    return """## Pour Claude Code — comment utiliser ce contexte

Sam t'a délégué Cortex en partie. Ne demande pas le contexte — il est ici. À
chaque session, lis ce fichier au démarrage. Tu sais alors :

- **Où on en est** sur le projet Cortex (publication, IAG, corrections expert)
- **Quels modules** sont implémentés vs partiels vs aspirationnels
- **L'état runtime courant** : actions récentes, win-rate baselines, scoring
  prédiction-vs-réalité, score anti-fake

### Ce que tu peux faire en autonomie (sans demander)

- Lire ce fichier puis répondre à une question sur l'état de Cortex sans
  re-briefer Sam
- Identifier la prochaine étape utile (par ex. si `n_outcome_evaluated < 10`,
  proposer d'attendre plus de cycles ; si action_effects est en mode
  fallback partout, suggérer de lancer une session de warm-up)
- Détecter si un fix expert est non appliqué (référer à `docs/claims.md` du
  repo public pour la table claim → niveau → preuve)

### Ce que tu DOIS demander avant de faire

- Tout `git push` (Sam contrôle ce qui sort)
- Toute modification du `cortex_self_dev_guardrails.json`
- Toute installation de package dans l'env principal Paperclip (cf
  feedback_xtts_install : interdit pip install dans main env)
- Tout restart de service en prod

### Ce qui n'est PAS dans ce fichier (c'est ailleurs)

- **Mémoire persistante de Sam** : `<USER_HOME>\\.claude\\projects\\h--Code-Paperclip\\memory\\MEMORY.md`
- **Logs runtime détaillés** : `<USER_HOME>\\Documents\\Obsidian Vault\\.cortex-*.jsonl`
- **Code source Cortex** : `<CORTEX_REPO>\\scripts\\brain\\cortex_*.py`
- **Repo public local** (avant push) : `<CORTEX_REPO>\\.cortex-publishing\\`
"""


def render_context() -> str:
    """Génère le markdown complet du contexte vivant."""
    iso = dt.datetime.now().isoformat(timespec="seconds")
    return f"""<!-- AUTO-GÉNÉRÉ par cortex_claude_code.py — NE PAS MODIFIER À LA MAIN -->
<!-- Régénéré à chaque cycle d'émergence ou via `python scripts/brain/cortex_claude_code.py` -->

# Contexte vivant Cortex pour Claude Code

> Snapshot : `{iso}`
> Source : `<CORTEX_REPO>/.cortex-claude-context.md`

Ce fichier est l'état cognitif de Cortex à l'instant T. Claude Code le lit
au démarrage de chaque session pour ne pas re-demander le contexte à Sam.

---

## 1. Boucle Active-Inference (scoring + outcome eval)

{_section_active_inference()}

## 2. Apprentissage des effets d'action (`cortex_action_effects`)

{_section_action_effects()}

## 3. Cerveau cognitif (graphe + activations)

{_section_brain()}

## 4. Corps (homeostasis)

{_section_body()}

## 5. Dernier rapport anti-fake

{_section_anti_fake()}

## 6. 5 dernières décisions

{_section_recent_decisions()}

---

{_section_pending_for_claude()}

---

## Méta — comment ce fichier est régénéré

- **Source** : `<CORTEX_REPO>\\scripts\\brain\\cortex_claude_code.py`
- **Génération auto** :
  - À chaque cycle d'émergence si l'action `update_claude_context` est choisie
  - Manuellement : `python <CORTEX_REPO>/scripts/brain/cortex_claude_code.py update`
- **Format** : markdown lisible, pas de JSON, pas de placeholders
- **Pas de PII** : ne contient aucun titre de note, aucun chemin Obsidian (les
  noms cognitifs sont déjà hashés à la publication, et ce fichier reste local)
"""


def update(force: bool = False) -> dict:
    """Régénère le fichier contexte. Toujours non-destructif (overwrite)."""
    try:
        text = render_context()
        CONTEXT_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONTEXT_FILE.write_text(text, encoding="utf-8")
        try:
            with LOG.open("a", encoding="utf-8") as f:
                f.write(f"[{dt.datetime.now().isoformat(timespec='seconds')}] "
                         f"updated {CONTEXT_FILE} ({len(text)} chars)\n")
        except Exception: pass
        return {"ok": True, "path": str(CONTEXT_FILE), "size_chars": len(text),
                "ts": time.time()}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# ─── Tool pour la table cortex_emergence.TOOLS ────────────────────────────────
def tool_update_claude_context() -> dict:
    """Action que cortex_emergence.TOOLS expose : update du contexte vivant.

    Renvoie le format attendu par `_tool_*` dans cortex_emergence : dict avec
    `ok` et `result` (string courte pour le chat stream).
    """
    r = update()
    if r.get("ok"):
        return {"ok": True,
                "result": f"contexte Claude Code rafraîchi ({r['size_chars']} chars) "
                          f"→ {r['path']}"}
    return {"ok": False, "result": f"err: {r.get('error', '?')}"}


def self_test() -> dict:
    """Vérifie que le rendu marche et que le fichier est écrit."""
    r = update()
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error")}
    if not CONTEXT_FILE.exists():
        return {"ok": False, "error": "context file not written"}
    txt = CONTEXT_FILE.read_text(encoding="utf-8", errors="replace")
    must_contain = ["Contexte vivant Cortex", "Active-Inference",
                     "anti-fake", "Pour Claude Code"]
    missing = [m for m in must_contain if m not in txt]
    return {"ok": not missing, "missing_sections": missing,
            "size_chars": len(txt)}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "update"
    if cmd == "update":
        print(json.dumps(update(), indent=2, ensure_ascii=False))
    elif cmd == "render":
        print(render_context())
    elif cmd == "test":
        print(json.dumps(self_test(), indent=2, ensure_ascii=False))
    else:
        print("Usage: cortex_claude_code.py {update|render|test}")
