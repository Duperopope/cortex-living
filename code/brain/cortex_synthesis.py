"""
cortex_synthesis.py — Synthèse cross-mémoire hebdomadaire.

Lit 100+ épisodiques + sémantiques et identifie :
- Thèmes émergents (clusters TF-IDF)
- Contradictions (dires opposés sur même sujet)
- Questions ouvertes (interrogations non résolues)

Sortie : 08 - Semantic/synthesis/<date>-weekly.md
"""
import datetime as dt
import json
import re
import sys
from pathlib import Path
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import KMeans
import numpy as np
import urllib.request

VAULT_PATH    = Path(r"<USER_HOME>\Documents\Obsidian Vault")
INGESTED_DIR  = VAULT_PATH / "07 - Ingested" / "conversations"
SEMANTIC_DIR  = VAULT_PATH / "08 - Semantic" / "synthesis"
ROUTER_URL    = "http://127.0.0.1:18900/route_v2"

SEMANTIC_DIR.mkdir(parents=True, exist_ok=True)


def _collect_recent(days: int = 7, limit: int = 200) -> list[str]:
    """Collecte les notes des N derniers jours."""
    cutoff = dt.datetime.now() - dt.timedelta(days=days)
    notes = []
    if INGESTED_DIR.exists():
        for day in sorted(INGESTED_DIR.glob("*"), reverse=True):
            if not day.is_dir(): continue
            try:
                day_dt = dt.datetime.strptime(day.name, "%Y-%m-%d")
                if day_dt < cutoff: break
            except ValueError: continue
            for note in sorted(day.glob("*.md"), reverse=True):
                try:
                    notes.append(note.read_text(encoding="utf-8", errors="replace")[:1500])
                except Exception: pass
                if len(notes) >= limit: return notes
    return notes


def _cluster_themes(notes: list[str], k: int = 5) -> list[dict]:
    """Identifie K thèmes par K-Means sur TF-IDF."""
    if len(notes) < k: return []
    vec = TfidfVectorizer(max_features=500, ngram_range=(1, 2))
    X = vec.fit_transform(notes)
    km = KMeans(n_clusters=min(k, len(notes)), random_state=0, n_init=10)
    km.fit(X)
    feature_names = vec.get_feature_names_out()
    themes = []
    for i in range(km.n_clusters):
        center = km.cluster_centers_[i]
        top_idx = np.argsort(-center)[:8]
        keywords = [feature_names[j] for j in top_idx]
        cluster_size = int(np.sum(km.labels_ == i))
        themes.append({"theme_id": i, "size": cluster_size, "keywords": keywords})
    themes.sort(key=lambda x: -x["size"])
    return themes


def _ask_router(prompt: str, timeout: int = 180) -> str:
    try:
        payload = json.dumps({"text": prompt, "role": "general"}).encode("utf-8")
        req = urllib.request.Request(ROUTER_URL, data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode())
            return d.get("response") or d.get("text") or ""
    except Exception as e:
        return f"[err: {e}]"


def weekly_synthesis(days: int = 7) -> dict:
    """Lance la synthèse hebdomadaire complète."""
    notes = _collect_recent(days=days)
    if len(notes) < 5:
        return {"ok": False, "error": f"only {len(notes)} notes, need 5+"}

    # 1. Thèmes par clustering
    themes = _cluster_themes(notes, k=min(5, len(notes) // 4))

    # 2. Demander au LLM les contradictions et questions ouvertes
    sample = "\n\n---\n\n".join(notes[:30])[:8000]
    prompt = (
        f"Tu es Cortex. Voici un échantillon de mes {len(notes)} dernières interactions sur {days} jours.\n\n"
        f"Thèmes identifiés par clustering :\n" +
        "\n".join(f"- Thème {t['theme_id']} ({t['size']} notes): {', '.join(t['keywords'][:5])}" for t in themes) +
        f"\n\nÉchantillon :\n{sample}\n\n"
        f"Identifie en 3 sections concises :\n"
        f"1. **CONTRADICTIONS** : 2-3 dires opposés sur un même sujet (sois précis avec citations courtes)\n"
        f"2. **QUESTIONS OUVERTES** : 3-5 problèmes non résolus qui reviennent\n"
        f"3. **INSIGHTS** : 2-3 patterns émergents non triviaux\n"
        f"Format Markdown strict, pas de préambule."
    )
    insights = _ask_router(prompt)

    # 3. Assembler la note
    now = dt.datetime.now()
    fname = f"{now.strftime('%Y-%m-%d')}-weekly.md"
    path = SEMANTIC_DIR / fname
    body = (
        f"---\n"
        f"captured_at: {now.isoformat(timespec='seconds')}\n"
        f"type: synthesis\n"
        f"period_days: {days}\n"
        f"notes_analyzed: {len(notes)}\n"
        f"---\n\n"
        f"# Synthèse hebdomadaire — {now.strftime('%Y-%m-%d')}\n\n"
        f"## Thèmes émergents\n\n"
    )
    for t in themes:
        body += f"- **Thème {t['theme_id']}** ({t['size']} notes) : {', '.join(t['keywords'][:6])}\n"
    body += f"\n{insights}\n"

    try:
        path.write_text(body, encoding="utf-8")
        return {"ok": True, "path": str(path), "themes": len(themes),
                "notes_analyzed": len(notes)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    print(json.dumps(weekly_synthesis(days=days), ensure_ascii=False, indent=2))
