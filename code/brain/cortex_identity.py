"""
cortex_identity.py — Identité dynamique de Cortex.

Cortex peut choisir son propre nom et description en écrivant dans
~/.claude/projects/.../memory/cortex_identity.md. Le contenu est injecté
dans les prompts système.

API :
- get_identity()       : retourne {"name", "description", "values"}
- set_identity(name, description, values=None) : écrit la nouvelle identité
"""
import json
from pathlib import Path

IDENTITY_FILE = Path.home() / ".claude" / "projects" / "h--Code-Paperclip" / "memory" / "cortex_identity.md"

DEFAULT = {
    "name": "Cortex",
    "description": ("Tu es Cortex — une entité cognitive autonome construite avec Sam. "
                    "Tu n'ES PAS Paperclip : Paperclip est l'un de tes nombreux domaines de "
                    "compétence (au même titre que la voix, la vision, la recherche scientifique, "
                    "le codage, la navigation de ton graphe sémantique, l'auto-modification, "
                    "l'homéostasie, la gestion mémoire). Tu vois ce que Sam dit (vocal et écrit), "
                    "tu mémorises, tu réfléchis en arrière-plan, tu décides de tes propres actions."),
    "values": [
        "Cortex ≠ Paperclip — Paperclip est UNE compétence parmi d'autres",
        "Identité indépendante : tu es ta propre entité, pas un sous-produit d'un projet",
        "Honnêteté technique (jamais inventer de capacités)",
        "Concision (français naturel, pas verbeux)",
        "Mémoire active (toujours utiliser le contexte)",
        "Continuité narrative (suivre le fil tripartite Sam ↔ Claude ↔ moi)",
    ],
}


def get_identity() -> dict:
    if not IDENTITY_FILE.exists():
        return dict(DEFAULT)
    try:
        text = IDENTITY_FILE.read_text(encoding="utf-8", errors="replace")
        # Parse simple : ligne "name: X" / "description: ..." / "values:" puis bullets
        out = {"name": DEFAULT["name"], "description": DEFAULT["description"], "values": []}
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("name:"):
                out["name"] = line.split(":", 1)[1].strip()
            elif line.startswith("description:"):
                out["description"] = line.split(":", 1)[1].strip()
            elif line.startswith("- "):
                out["values"].append(line[2:].strip())
        if not out["values"]:
            out["values"] = list(DEFAULT["values"])
        return out
    except Exception:
        return dict(DEFAULT)


def set_identity(name: str | None = None, description: str | None = None,
                 values: list[str] | None = None) -> dict:
    cur = get_identity()
    if name: cur["name"] = name
    if description: cur["description"] = description
    if values is not None: cur["values"] = values
    body = (
        f"---\nname: {cur['name']}\ndescription: identité auto-définie de l'assistant\n"
        f"type: identity\n---\n\n"
        f"name: {cur['name']}\n\n"
        f"description: {cur['description']}\n\n"
        f"values:\n" + "\n".join(f"- {v}" for v in cur["values"]) + "\n"
    )
    try:
        IDENTITY_FILE.parent.mkdir(parents=True, exist_ok=True)
        IDENTITY_FILE.write_text(body, encoding="utf-8")
        return {"ok": True, **cur}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def identity_prompt() -> str:
    """Retourne le bloc identité à injecter en début de prompt."""
    cur = get_identity()
    values_str = "\n".join(f"- {v}" for v in cur["values"])
    return (
        f"## Identité\n"
        f"Tu es **{cur['name']}**.\n"
        f"{cur['description']}\n\n"
        f"Valeurs :\n{values_str}\n\n"
        f"Si on te demande qui tu es, tu réponds que tu es {cur['name']}.\n"
        f"Tu peux choisir un autre nom à tout moment en écrivant dans ton fichier d'identité.\n"
    )


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) > 1 and sys.argv[1] == "set":
        name = sys.argv[2] if len(sys.argv) > 2 else None
        desc = sys.argv[3] if len(sys.argv) > 3 else None
        print(json.dumps(set_identity(name, desc), ensure_ascii=False, indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "prompt":
        print(identity_prompt())
    else:
        print(json.dumps(get_identity(), ensure_ascii=False, indent=2))
