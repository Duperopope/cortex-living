"""
cortex_bridge.py — Cortex tisse des ponts cognitifs entre domaines isolés.

Inspiré de :
- Fauconnier & Turner (2002), "The Way We Think: Conceptual Blending"
- Hofstadter (1979), "Gödel, Escher, Bach" — analogie comme noyau de la cognition
- Mednick (1962), "The associative basis of the creative process" — Remote Associates Test

Méthode :
1. Identifier nœuds isolés (top_sim < 0.2 avec leurs voisins) via thought_graph
2. Pour chaque pair isolé, demander au LLM :
   "Quel concept scientifique relie {A} et {B} ?
    Cherche en : neurologie, physique quantique, informatique, mathématiques,
    physique, biologie, chimie, philosophie. Réponds par UN concept-pont."
3. Écrire le concept-pont comme nouvelle note sémantique dans
   08 - Semantic/bridges/{topic}.md
4. Re-build le thought_graph → les ponts apparaissent comme nouveaux nœuds
   qui rapprochent A et B via une chaîne A → bridge → B.

Effet visuel : avec force-directed, les zones isolées s'unifient organiquement
quand de nouveaux ponts apparaissent.
"""
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

VAULT      = Path(r"<USER_HOME>\Documents\Obsidian Vault")
BRIDGE_DIR = VAULT / "08 - Semantic" / "bridges"
OPENCODE   = r"<USER_HOME>\AppData\Roaming\npm\opencode.cmd"

BRIDGE_DIR.mkdir(parents=True, exist_ok=True)


def _ask(prompt: str, timeout: int = 45) -> str:
    try:
        r = subprocess.run([OPENCODE, "run", "--model", "opencode/minimax-m2.5-free", "-"],
                           input=prompt, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        lines = [l for l in r.stdout.splitlines()
                 if l.strip() and not l.startswith(">") and "\x1b" not in l and "build" not in l.lower()]
        return "\n".join(lines).strip()
    except Exception as e:
        return f"err: {e}"


def find_isolated_pairs(top_n: int = 5) -> list[tuple[str, str]]:
    """Retourne les paires de nœuds les plus distants sémantiquement (à pontifier)."""
    sys.path.insert(0, r"<CORTEX_REPO>\scripts\brain")
    import cortex_thought_graph as ctg
    ctg.build_graph()
    nodes = ctg._state.get("nodes", [])
    if len(nodes) < 4: return []
    from sklearn.metrics.pairwise import cosine_similarity
    import numpy as np
    sims = cosine_similarity(ctg._state["vectors"])
    np.fill_diagonal(sims, 1.0)
    # Pour chaque nœud, sa distance min (= max sim) avec n'importe quel autre
    # On veut les pairs où max_sim entre eux est faible mais ils sont actifs
    pairs = []
    for i in range(len(nodes)):
        # Trouve l'index avec sim minimale (le plus éloigné)
        sims_i = sims[i].copy(); sims_i[i] = 1
        j = int(np.argmin(sims_i))
        if i < j and sims_i[j] < 0.15:  # vraiment éloignés
            pairs.append((nodes[i]["source"], nodes[j]["source"], float(sims_i[j])))
    pairs.sort(key=lambda p: p[2])
    # Dédup
    seen = set()
    uniq = []
    for a, b, s in pairs:
        key = tuple(sorted([a.split("/")[-1], b.split("/")[-1]]))
        if key in seen: continue
        seen.add(key)
        uniq.append((a, b, s))
        if len(uniq) >= top_n: break
    return uniq


BRIDGE_PROMPT = """Tu es Cortex. Tu cherches un pont cognitif entre deux concepts éloignés
de ta mémoire. Trouve-le en explorant les sciences : neurologie, physique quantique,
informatique, mathématiques, physique classique, biologie, chimie, philosophie.

Concept A : {a}
Concept B : {b}

Réponds UNIQUEMENT en JSON, format strict :
{{"bridge_concept": "nom du concept-pont (3-7 mots)",
  "domain": "domaine (ex: physique quantique, neurologie...)",
  "explanation": "1-2 phrases qui expliquent comment ce concept relie A et B",
  "depth": "surface|moyen|profond"}}"""


def _slugify(s: str) -> str:
    s = re.sub(r'[^\w\s-]', '', s.lower())
    return re.sub(r'[\s-]+', '-', s).strip('-')[:40] or "bridge"


def create_bridge(node_a: str, node_b: str) -> dict:
    """Demande au LLM un concept-pont et l'écrit dans 08 - Semantic/bridges/."""
    raw = _ask(BRIDGE_PROMPT.format(a=node_a, b=node_b))
    m = re.search(r'\{[\s\S]*?\}', raw)
    if not m: return {"ok": False, "raw": raw[:300]}
    try:
        d = json.loads(m.group(0))
    except Exception:
        return {"ok": False, "raw": raw[:300]}
    bridge = d.get("bridge_concept", "")
    if not bridge: return {"ok": False, "no_bridge": True}
    slug = _slugify(bridge)
    fname = f"{slug}.md"
    body = (
        f"---\ncaptured_at: {dt.datetime.now().isoformat(timespec='seconds')}\n"
        f"type: cognitive_bridge\n"
        f"domain: {d.get('domain','?')}\n"
        f"depth: {d.get('depth','?')}\n"
        f"connects: [{node_a}, {node_b}]\n---\n\n"
        f"# {bridge}\n\n"
        f"**Domaine** : {d.get('domain','')}\n\n"
        f"## Explication\n{d.get('explanation','')}\n\n"
        f"## Pont entre\n- {node_a}\n- {node_b}\n"
    )
    path = BRIDGE_DIR / fname
    try:
        path.write_text(body, encoding="utf-8")
        return {"ok": True, "bridge": bridge, "path": str(path), **d}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def bridge_session(max_pairs: int = 3) -> dict:
    """Identifie les paires isolées et crée des ponts pour les top-N."""
    pairs = find_isolated_pairs(top_n=max_pairs)
    if not pairs: return {"ok": True, "result": "Aucune paire isolée — graphe déjà bien connecté."}
    bridges = []
    for a, b, s in pairs:
        r = create_bridge(a, b)
        bridges.append({"a": a, "b": b, "sim": round(s, 3), **r})
    # Force re-build du graphe pour intégrer les nouveaux ponts
    try:
        sys.path.insert(0, r"<CORTEX_REPO>\scripts\brain")
        import cortex_thought_graph as ctg
        ctg.build_graph(force=True)
    except Exception: pass
    return {"ok": True, "bridges": bridges,
            "result": f"{len([b for b in bridges if b.get('ok')])}/{len(bridges)} ponts créés"}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "session"
    if cmd == "isolated":
        print(json.dumps(find_isolated_pairs(), ensure_ascii=False, indent=2))
    elif cmd == "session":
        print(json.dumps(bridge_session(), ensure_ascii=False, indent=2))
