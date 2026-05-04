"""
cortex_memory.py — Mémoire active pour Cortex.

Trois fonctions exposées :
1. log_episodic(query, response, meta) : sauve l'échange en note épisodique
2. retrieve_context(query, k=5) : récupère les K mémoires les plus pertinentes
3. start_consolidation_loop() : thread daemon qui consolide tous les N min

Architecture :
- Épisodique : 07 - Ingested/conversations/YYYY-MM-DD/HH-MM-SS-slug.md
- Sémantique : 08 - Semantic/<topic>/<fact-id>.md (via vault_consolidate.py)
- Recherche : vault_brain.py BM25 + récence pondérée
"""
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

VAULT_PATH       = Path(r"<USER_HOME>\Documents\Obsidian Vault")
INGESTED_DIR     = VAULT_PATH / "07 - Ingested" / "conversations"
SEMANTIC_DIR     = VAULT_PATH / "08 - Semantic"
CLAUDE_MEMORY    = Path.home() / ".claude" / "projects" / "h--Code-Paperclip" / "memory"
INGESTED_DIR.mkdir(parents=True, exist_ok=True)
SEMANTIC_DIR.mkdir(parents=True, exist_ok=True)


def _slugify(text: str, max_len: int = 40) -> str:
    s = re.sub(r'[^\w\s-]', '', text.lower())
    s = re.sub(r'[-\s]+', '-', s).strip('-')
    return s[:max_len] or "anon"


def log_episodic(query: str, response: str, meta: dict | None = None) -> Path | None:
    """Écrit une note épisodique pour chaque échange utilisateur ↔ Cortex."""
    if not query or not query.strip():
        return None
    now = dt.datetime.now()
    day_dir = INGESTED_DIR / now.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    slug = _slugify(query[:60])
    fname = f"{now.strftime('%H-%M-%S')}-{slug}.md"
    path = day_dir / fname

    meta = meta or {}
    fm = {
        "captured_at": now.isoformat(timespec='seconds'),
        "type": "episodic",
        "source": meta.get("source", "chat"),
        "backend": meta.get("backend", "?"),
        "v2_path": meta.get("v2_path", "?"),
        "role": meta.get("role", "general"),
    }
    fm_lines = "\n".join(f"{k}: {v}" for k, v in fm.items())
    body = (
        f"---\n{fm_lines}\n---\n\n"
        f"## Question\n\n{query.strip()}\n\n"
        f"## Réponse\n\n{(response or '').strip()}\n"
    )
    try:
        path.write_text(body, encoding="utf-8")
        return path
    except Exception as e:
        print(f"[memory] log err: {e}", flush=True)
        return None


def _read_text(p: Path, max_chars: int = 800) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")[:max_chars]
    except Exception:
        return ""


def retrieve_context(query: str, k: int = 5, recent_hours: int = 168) -> list[dict]:
    """Récupère les K notes les plus pertinentes via 3 sources :
    1. Mémoire Claude (~/.claude/.../memory/) — durable, toujours pertinente
    2. Sémantique du vault (08 - Semantic) — concepts distillés
    3. Épisodiques récents — conversations des derniers jours
    Filtre les matches < seuil pour éviter le bruit (Starfield etc.)."""
    if not query or not query.strip():
        return []
    keywords = [w.lower() for w in re.findall(r'\w+', query) if len(w) > 3]
    if not keywords:
        return []
    MIN_KW_MATCHES = 1   # au moins 1 mot-clé spécifique doit matcher
    results = []

    # 1. Mémoire Claude — scan direct (fichiers courts, durables)
    if CLAUDE_MEMORY.exists():
        for md in CLAUDE_MEMORY.glob("*.md"):
            try:
                txt = _read_text(md, max_chars=2000)
                txt_lower = txt.lower()
                matches = sum(1 for kw in keywords if kw in txt_lower)
                if matches >= MIN_KW_MATCHES:
                    results.append({
                        "source": f".claude/memory/{md.name}",
                        "text": txt[:800],
                        "score": matches * 3.0,  # priorité haute
                        "kind": "claude_memory",
                    })
            except Exception: pass

    # 2. Sémantique du vault — scan direct
    if SEMANTIC_DIR.exists():
        for md in SEMANTIC_DIR.rglob("*.md"):
            try:
                txt = _read_text(md, max_chars=1500)
                txt_lower = txt.lower()
                matches = sum(1 for kw in keywords if kw in txt_lower)
                if matches >= MIN_KW_MATCHES:
                    results.append({
                        "source": str(md.relative_to(VAULT_PATH)),
                        "text": txt[:600],
                        "score": matches * 2.0,
                        "kind": "semantic",
                    })
            except Exception: pass

    # 3. Épisodiques récents (conversations)
    if INGESTED_DIR.exists():
        cutoff = time.time() - recent_hours * 3600
        for day_dir in sorted(INGESTED_DIR.glob("*"), reverse=True)[:14]:
            if not day_dir.is_dir(): continue
            for note in day_dir.glob("*.md"):
                try:
                    if note.stat().st_mtime < cutoff: continue
                    txt = _read_text(note)
                    matches = sum(1 for kw in keywords if kw in txt.lower())
                    if matches >= MIN_KW_MATCHES:
                        age_h = (time.time() - note.stat().st_mtime) / 3600
                        recency = 0.5 ** (age_h / 24.0)
                        results.append({
                            "source": str(note.relative_to(VAULT_PATH)),
                            "text": txt[:600],
                            "score": matches * recency * 1.5,
                            "kind": "episodic",
                            "age_h": round(age_h, 1),
                        })
                except Exception: pass

    # Dédup + tri
    seen, deduped = set(), []
    results.sort(key=lambda x: -x["score"])
    for r in results:
        if r["source"] in seen: continue
        seen.add(r["source"])
        deduped.append(r)
        if len(deduped) >= k: break
    # Activate retrieved nodes (Spreading Activation, Collins & Loftus 1975)
    try:
        import cortex_activation as _ca
        _ca.co_activate([r["source"] for r in deduped])
    except Exception: pass
    return deduped


def format_context_for_prompt(memories: list[dict]) -> str:
    """Formate les mémoires en bloc texte injectable dans un prompt."""
    if not memories:
        return ""
    lines = ["## Mémoire pertinente\n"]
    for m in memories:
        kind_tag = {
            "claude_memory": "🧠 MÉMOIRE",
            "semantic":      "💎 SÉMANTIQUE",
            "episodic":      "💬 ÉPISODIQUE",
            "vault":         "📁 VAULT",
        }.get(m["kind"], "?")
        lines.append(f"### {kind_tag} — {m['source']}")
        if m.get("age_h") is not None:
            lines.append(f"_(il y a {m['age_h']}h)_")
        lines.append(m["text"])
        lines.append("")
    return "\n".join(lines)


# ─── Consolidation en arrière-plan ────────────────────────────────────────────
_CONSOLIDATE_INTERVAL = 6 * 3600  # 6h entre consolidations
_consolidate_thread = None

def _run_consolidation():
    """Lance vault_consolidate.py --since 12h --apply en subprocess."""
    try:
        script = Path(r"<CORTEX_REPO>\scripts\brain\vault_consolidate.py")
        if not script.exists():
            print(f"[memory] consolidate script absent", flush=True)
            return
        print(f"[memory] consolidation start", flush=True)
        r = subprocess.run(
            ["python", str(script), "--since", "12h", "--apply"],
            capture_output=True, text=True, timeout=600,
            encoding="utf-8", errors="replace"
        )
        # Compter les nouvelles notes sémantiques créées
        out = r.stdout + r.stderr
        m = re.search(r'(\d+)\s+(?:facts?|notes?)', out)
        n = m.group(1) if m else "?"
        print(f"[memory] consolidation done — {n} new semantic facts", flush=True)
    except Exception as e:
        print(f"[memory] consolidation err: {e}", flush=True)


def _consolidation_loop():
    # Attendre 30 min après le démarrage avant 1ère consolidation
    time.sleep(1800)
    while True:
        _run_consolidation()
        time.sleep(_CONSOLIDATE_INTERVAL)


def start_consolidation_loop():
    """Lance le thread daemon de consolidation. À appeler une seule fois."""
    global _consolidate_thread
    if _consolidate_thread is not None and _consolidate_thread.is_alive():
        return
    _consolidate_thread = threading.Thread(target=_consolidation_loop, daemon=True)
    _consolidate_thread.start()
    print("[memory] consolidation loop started (6h interval)", flush=True)


# ─── CLI test ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "search":
        q = " ".join(sys.argv[2:])
        for m in retrieve_context(q, k=5):
            print(f"[{m['kind']}] {m['source']} (score={m['score']:.2f})")
            print(m["text"][:200]); print()
    elif len(sys.argv) > 1 and sys.argv[1] == "log":
        log_episodic("test query", "test response", {"backend": "test"})
        print("logged")
    else:
        print("Usage: cortex_memory.py [search <query> | log]")
