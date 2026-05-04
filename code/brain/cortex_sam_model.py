"""
cortex_sam_model.py — Modèle dynamique de Sam.

Lit les épisodiques récents et synthétise un profil utilisateur :
- Préférences observées
- Patterns de communication
- Projets en cours
- Niveau d'expertise par domaine
- Sujets récurrents

Sortie : ~/.claude/projects/.../memory/sam_model.md (auto-update)
"""
import datetime as dt
import json
import re
import sys
import urllib.request
from pathlib import Path

VAULT_PATH    = Path(r"<USER_HOME>\Documents\Obsidian Vault")
INGESTED_DIR  = VAULT_PATH / "07 - Ingested" / "conversations"
SAM_MODEL     = Path.home() / ".claude" / "projects" / "h--Code-Paperclip" / "memory" / "sam_model.md"
ROUTER_URL    = "http://127.0.0.1:18900/route_v2"


def _gather_recent_episodics(max_files: int = 30, max_total_chars: int = 12000) -> str:
    if not INGESTED_DIR.exists(): return ""
    notes = []
    for day in sorted(INGESTED_DIR.glob("*"), reverse=True):
        if not day.is_dir(): continue
        for note in sorted(day.glob("*.md"), reverse=True):
            try:
                notes.append(note.read_text(encoding="utf-8", errors="replace")[:600])
            except Exception: pass
            if len(notes) >= max_files: break
        if len(notes) >= max_files: break
    joined = "\n\n---\n\n".join(notes)
    return joined[:max_total_chars]


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


SYNTHESIS_PROMPT = """Tu es Cortex en train de bâtir un modèle de ton utilisateur Sam.

Voici les {n} dernières interactions échangées avec lui :

{episodics}

Synthétise un profil structuré de Sam — uniquement faits OBSERVABLES, pas spéculation.

Format strict en Markdown :

## Préférences observées
- (3-5 puces concrètes)

## Patterns de communication
- (3 puces : style, exigences, rythme)

## Projets actifs
- (liste les projets mentionnés)

## Domaines d'expertise apparente
- (avec niveau approximatif : débutant / intermédiaire / avancé)

## Sujets récurrents
- (3-5 thèmes qui reviennent)

## Frustrations observées
- (ce qui l'agace)

Réponds UNIQUEMENT avec ce Markdown, sans préambule."""


def update_sam_model() -> dict:
    """Régénère sam_model.md depuis les épisodiques récents."""
    episodics = _gather_recent_episodics()
    if not episodics:
        return {"ok": False, "error": "no episodics"}
    n_notes = episodics.count("---") // 2
    prompt = SYNTHESIS_PROMPT.format(n=n_notes, episodics=episodics)
    response = _ask_router(prompt)
    if not response or response.startswith("[err"):
        return {"ok": False, "error": response or "empty"}

    # Build the markdown file
    now = dt.datetime.now().isoformat(timespec='seconds')
    body = (
        f"---\n"
        f"name: Sam Model — profil utilisateur dynamique\n"
        f"description: Synthèse automatique des préférences/patterns observés via épisodiques\n"
        f"type: user\n"
        f"updated_at: {now}\n"
        f"sources: {n_notes} épisodiques récents\n"
        f"---\n\n"
        f"{response.strip()}\n"
    )
    try:
        SAM_MODEL.parent.mkdir(parents=True, exist_ok=True)
        SAM_MODEL.write_text(body, encoding="utf-8")
        return {"ok": True, "path": str(SAM_MODEL), "updated_at": now,
                "sources": n_notes, "size": SAM_MODEL.stat().st_size}
    except Exception as e:
        return {"ok": False, "error": str(e)}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    r = update_sam_model()
    print(json.dumps(r, ensure_ascii=False, indent=2))
