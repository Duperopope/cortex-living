"""
cortex_learned_skills.py — Mémoire modulaire des compétences acquises par Cortex.

Quand une action de cortex_self_dev (ou un /run, /code, /test) réussit
(tests OK, outcome=applied), on en extrait :
  - le diff minimal qui a marché
  - le contexte (goal en langage naturel)
  - les tests passés
  - les fichiers touchés
On écrit une note atomique dans `08 - Semantic/learned-skills/<slug>.md` avec
frontmatter type=learned_skill — la note est immédiatement indexée par
cortex_thought_graph (TF-IDF cosine), donc Cortex peut RAPPELER cette
solution la prochaine fois qu'il rencontre un problème similaire.

Aussi un append-only `.cortex-learned-skills.jsonl` (audit + replay).

API :
- remember(name, goal, outcome, applied_files, tests, diff="") -> dict
- list_learned(limit=20) -> list[dict]
- search_learned(query, k=5) -> list[dict]
"""
import json
import re
import time
from pathlib import Path

VAULT       = Path(r"<USER_HOME>\Documents\Obsidian Vault")
SKILLS_DIR  = VAULT / "08 - Semantic" / "learned-skills"
SKILLS_LOG  = VAULT / ".cortex-learned-skills.jsonl"
REPO_ROOT   = Path(r"<CORTEX_REPO>")


def _slugify(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"[\s-]+", "-", s)
    return s[:60].strip("-") or "skill"


def _short_diff(applied_files, max_chars: int = 1800) -> str:
    """Lit les fichiers appliqués et garde un extrait représentatif (head + tail)."""
    out = []
    budget = max_chars
    for f in applied_files[:3]:
        try:
            p = (REPO_ROOT / f) if not Path(f).is_absolute() else Path(f)
            if not p.exists() or not p.is_file(): continue
            txt = p.read_text(encoding="utf-8", errors="replace")
            n = len(txt)
            if n <= 600:
                snippet = f"### {f}\n```\n{txt}\n```"
            else:
                head = txt[:300]
                tail = txt[-300:]
                snippet = (f"### {f}\n```\n{head}\n"
                           f"# ... ({n - 600} chars élidés) ...\n{tail}\n```")
            out.append(snippet)
            budget -= len(snippet)
            if budget <= 0: break
        except Exception:
            pass
    return "\n\n".join(out)


def remember(name: str, goal: str, outcome: str,
             applied_files=None, tests=None, diff: str = "",
             tags=None) -> dict:
    """Mémorise une compétence acquise. Appelé après une action self_dev
    qui a réussi (outcome='applied' + tests passés).

    Returns dict avec {ok, path, slug, ts}.
    """
    if outcome != "applied":
        return {"ok": False, "skipped": True,
                "reason": f"outcome={outcome}, only 'applied' is remembered"}
    applied_files = list(applied_files or [])
    tests = dict(tests or {})
    tags = list(tags or [])

    bad = [s for s, info in tests.items() if not info.get("ok")]
    if bad:
        return {"ok": False, "skipped": True, "reason": f"tests failed: {bad}"}

    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.time()
    slug = _slugify(name)
    path = SKILLS_DIR / f"{slug}.md"
    n = 1
    while path.exists():
        n += 1
        path = SKILLS_DIR / f"{slug}-{n}.md"

    tests_brief = []
    for s, info in tests.items():
        tests_brief.append(f"- **{s}** : {info.get('passed', '?')}/{info.get('total', '?')} OK")

    if not diff and applied_files:
        diff = _short_diff(applied_files)

    body = (
        f"---\n"
        f"name: {name}\n"
        f"type: learned_skill\n"
        f"outcome: {outcome}\n"
        f"learned_at: {time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(ts))}\n"
        f"tags: [{', '.join(tags or ['skill', 'self_dev'])}]\n"
        f"---\n\n"
        f"# Compétence acquise : {name}\n\n"
        f"## Contexte\n\n"
        f"Objectif Cortex (langage naturel) :\n\n> {goal}\n\n"
        f"## Résultat\n\n"
        f"- Outcome : `{outcome}`\n"
        f"- Fichiers touchés : {', '.join(f'`{f}`' for f in applied_files) or '(aucun)'}\n\n"
        f"## Tests qui ont validé la compétence\n\n"
        f"{chr(10).join(tests_brief) if tests_brief else '_aucun test rapporté_'}\n\n"
        f"## Code qui a marché (extrait modulaire)\n\n"
        f"{diff or '_pas de diff capturé_'}\n\n"
        f"## Quand réutiliser\n\n"
        f"Si Cortex reçoit un goal sémantiquement proche (cosine TF-IDF > 0.4), "
        f"cette skill apparaît dans les voisins du graphe et peut être rappelée "
        f"par retrieve_context avant de re-générer un patch.\n"
    )
    try:
        path.write_text(body, encoding="utf-8")
    except Exception as e:
        return {"ok": False, "error": f"write failed: {e}"}

    entry = {"ts": ts, "name": name, "slug": slug, "goal": goal[:300],
             "outcome": outcome, "files": applied_files, "tests": tests,
             "path": str(path)}
    try:
        with SKILLS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception: pass

    # Re-indexer le graphe sémantique pour que la skill soit immédiatement disponible
    try:
        import sys as _sys
        if str(REPO_ROOT / "scripts" / "brain") not in _sys.path:
            _sys.path.insert(0, str(REPO_ROOT / "scripts" / "brain"))
        import cortex_thought_graph as _ctg
        _ctg.build_graph(force=True)
    except Exception as e:
        print(f"[learned_skills] reindex graph failed: {e}", flush=True)

    return {"ok": True, "path": str(path), "slug": slug, "ts": ts,
            "files": applied_files}


def list_learned(limit: int = 20) -> list:
    """Liste les compétences mémorisées (les plus récentes d'abord)."""
    out = []
    if not SKILLS_LOG.exists(): return out
    try:
        lines = SKILLS_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        for ln in lines[-limit*3:]:
            try: out.append(json.loads(ln))
            except Exception: pass
    except Exception: pass
    out.sort(key=lambda x: -x.get("ts", 0))
    return out[:limit]


def search_learned(query: str, k: int = 5) -> list:
    """Cherche les skills sémantiquement proches du query (cosine TF-IDF)."""
    try:
        import sys as _sys
        if str(REPO_ROOT / "scripts" / "brain") not in _sys.path:
            _sys.path.insert(0, str(REPO_ROOT / "scripts" / "brain"))
        import cortex_thought_graph as _ctg
        _ctg.build_graph()
        idx = _ctg._find_node(query)
        if idx is None: return []
        from sklearn.metrics.pairwise import cosine_similarity
        sims = cosine_similarity(_ctg._state["vectors"][idx],
                                  _ctg._state["vectors"])[0]
        top = sims.argsort()[::-1]
        out = []
        for j in top:
            n = _ctg._state["nodes"][j]
            src = (n.get("source") or "").replace("\\", "/")
            if "learned-skills" in src:
                out.append({"source": src, "sim": float(sims[j]),
                            "text": n.get("text", "")[:300]})
                if len(out) >= k: break
        return out
    except Exception as e:
        return [{"error": str(e)}]


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) > 1 and sys.argv[1] == "list":
        for s in list_learned(20):
            print(f"- {s.get('name')} ({s.get('slug')}) — {s.get('files')}")
    elif len(sys.argv) > 1 and sys.argv[1] == "search":
        q = " ".join(sys.argv[2:]) or "skill"
        for s in search_learned(q, k=8):
            print(f"  sim={s.get('sim',0):.3f} {s.get('source')}")
    else:
        print(json.dumps({"skills_dir": str(SKILLS_DIR), "log": str(SKILLS_LOG),
                          "count": len(list_learned(999))}, indent=2))
