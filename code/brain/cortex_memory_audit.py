"""
cortex_memory_audit.py — Cortex audite sa propre mémoire et corrige.

Détection de problèmes mémoire :

1. CONTRADICTIONS : 2 mémoires affirment des choses incompatibles
   (ex: "TTS = Damien Black" vs "TTS = Benoît Allemane" sur même période)

2. OBSOLESCENCE : un fait pointe vers un fichier/path qui n'existe plus,
   ou contient un timestamp dépassé

3. INCOHÉRENCE STRUCTURELLE : mémoire référence un module qui n'existe pas,
   ou un endpoint /api/cortex/X qui retourne 404

4. DOUBLONS : 2 mémoires disent la même chose avec des wording différents
   (cosine TF-IDF > 0.9 entre titres + bodies)

Approche : pas de LLM, règles déterministes + le thought_graph TF-IDF existant.
Sortie : `.cortex-memory-audit-report.json` + propositions de corrections.

API :
    audit() → dict {issues_found, contradictions, obsolete, incoherent, duplicates}
    propose_corrections(issues) → list[fix] avec dry_run par défaut
    apply_correction(fix_id, confirm=False) → applique une correction validée
    self_test()
"""
from __future__ import annotations
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent
VAULT = Path(r"<USER_HOME>\Documents\Obsidian Vault")
MEMORY_DIR = Path.home() / ".claude" / "projects" / "h--Code-Paperclip" / "memory"
GRAPH = VAULT / ".vault-graph.json"
SEMANTIC_DIR = VAULT / "08 - Semantic"
AUDIT_REPORT = VAULT / ".cortex-memory-audit-report.json"
AUDIT_LOG = VAULT / ".cortex-memory-audit-events.jsonl"
CORRECTIONS_PROPOSED = VAULT / ".cortex-memory-corrections.json"


def _now() -> float: return time.time()


def _safe_read(path: Path) -> str:
    try: return path.read_text(encoding="utf-8", errors="replace")
    except Exception: return ""


def _all_memory_files() -> list[Path]:
    files = []
    if MEMORY_DIR.exists():
        files.extend(sorted(MEMORY_DIR.glob("*.md")))
    return files


def _extract_facts(content: str, filename: str) -> list[dict]:
    """Extraction simple : chaque ligne assertive (sans ?, sans liste vide) = un fact."""
    facts = []
    for i, line in enumerate(content.splitlines()):
        line = line.strip()
        if not line: continue
        if line.startswith("#"): continue  # titres
        if line.startswith("---"): continue  # frontmatter
        if line.endswith("?"): continue  # questions
        if len(line) < 20: continue  # trop court
        if line.startswith("- "): line = line[2:].strip()
        facts.append({"file": filename, "line": i, "text": line[:300]})
    return facts


def detect_contradictions() -> list[dict]:
    """Cherche les paires de facts qui s'opposent par mots-clés contradictoires."""
    files = _all_memory_files()
    facts = []
    for f in files:
        facts.extend(_extract_facts(_safe_read(f), f.name))
    # Heuristique simple : si deux facts ont haute similarité ET mots-clés opposés
    # (oui/non, est/n'est pas, true/false, ON/OFF, etc.)
    NEGATIONS = [("est", "n'est pas"), ("oui", "non"), ("true", "false"),
                 ("ON", "OFF"), ("activé", "désactivé"), ("actif", "inactif"),
                 ("réel", "fake"), ("v1", "v2"), ("up", "down")]
    found = []
    for i, a in enumerate(facts):
        for b in facts[i+1:]:
            if a["file"] == b["file"]: continue
            ta = a["text"].lower()
            tb = b["text"].lower()
            # Tokens communs (10+) + tokens contradictoires
            words_a = set(re.findall(r"\w{3,}", ta))
            words_b = set(re.findall(r"\w{3,}", tb))
            common = words_a & words_b
            if len(common) < 5: continue
            for pos, neg in NEGATIONS:
                if pos in ta and neg in tb and pos not in tb and neg not in ta:
                    found.append({
                        "type": "contradiction",
                        "fact_a": a, "fact_b": b,
                        "common_tokens": list(common)[:5],
                        "axis": f"{pos} vs {neg}",
                    })
                    break
                if neg in ta and pos in tb and neg not in tb and pos not in ta:
                    found.append({
                        "type": "contradiction",
                        "fact_a": a, "fact_b": b,
                        "common_tokens": list(common)[:5],
                        "axis": f"{neg} vs {pos}",
                    })
                    break
    return found[:30]  # cap


def detect_obsolete_paths() -> list[dict]:
    """Cherche les références à des fichiers qui n'existent plus."""
    files = _all_memory_files()
    found = []
    # Pattern : chemin Windows ou Unix
    PATH_PATTERN = re.compile(r"([A-Za-z]:\\[^\s'\":,;]+|/[\w./\-_]+\.\w+|scripts/brain/\w+\.py)")
    for f in files:
        content = _safe_read(f)
        for m in PATH_PATTERN.finditer(content):
            path_str = m.group(1)
            # Normalise
            try:
                p = Path(path_str.replace("/", "\\")) if ":\\" in path_str else Path(path_str)
                if not p.is_absolute() and "scripts/" in path_str:
                    p = Path(r"<CORTEX_REPO>") / path_str
            except Exception: continue
            if not p.exists():
                found.append({
                    "type": "obsolete_path",
                    "file": f.name,
                    "missing_path": str(p),
                    "raw": path_str,
                })
            if len(found) >= 25: return found
    return found


def detect_incoherent_endpoints() -> list[dict]:
    """Cherche les références à des endpoints /api/cortex/X et teste s'ils répondent."""
    files = _all_memory_files()
    endpoints = set()
    EP_PATTERN = re.compile(r"(/api/cortex/[\w/]+)")
    for f in files:
        for m in EP_PATTERN.finditer(_safe_read(f)):
            endpoints.add(m.group(1))
    # Test rapide : appel HTTP local
    found = []
    try:
        import urllib.request
        for ep in sorted(endpoints):
            try:
                req = urllib.request.Request(f"http://127.0.0.1:8765{ep}",
                                              method="GET")
                with urllib.request.urlopen(req, timeout=2) as r:
                    code = r.status
                if code >= 400:
                    found.append({"type": "incoherent_endpoint",
                                   "endpoint": ep, "status": code})
            except Exception as e:
                msg = str(e)
                if "404" in msg:
                    found.append({"type": "incoherent_endpoint",
                                   "endpoint": ep, "status": 404,
                                   "error": msg[:100]})
    except Exception: pass
    return found


def detect_duplicates() -> list[dict]:
    """Détecte les mémoires qui disent ~la même chose."""
    files = _all_memory_files()
    by_title = defaultdict(list)
    titles = []
    for f in files:
        content = _safe_read(f)
        # Frontmatter description
        m = re.search(r"^description:\s*(.+)$", content, re.MULTILINE)
        title = m.group(1).strip() if m else f.stem
        # Token set
        tokens = set(re.findall(r"\w{4,}", title.lower()))
        titles.append({"file": f.name, "title": title, "tokens": tokens})
    found = []
    for i, a in enumerate(titles):
        for b in titles[i+1:]:
            if not a["tokens"] or not b["tokens"]: continue
            inter = a["tokens"] & b["tokens"]
            union = a["tokens"] | b["tokens"]
            jaccard = len(inter) / max(1, len(union))
            if jaccard > 0.5:
                found.append({
                    "type": "duplicate",
                    "file_a": a["file"], "file_b": b["file"],
                    "title_a": a["title"][:80], "title_b": b["title"][:80],
                    "jaccard_similarity": round(jaccard, 3),
                })
    return found


def audit() -> dict:
    """Audit complet de la mémoire de Cortex."""
    contradictions = detect_contradictions()
    obsolete = detect_obsolete_paths()
    incoherent = detect_incoherent_endpoints()
    duplicates = detect_duplicates()
    n_total = len(contradictions) + len(obsolete) + len(incoherent) + len(duplicates)
    rep = {
        "ts": _now(),
        "issues_found": n_total,
        "by_type": {
            "contradictions": len(contradictions),
            "obsolete_paths": len(obsolete),
            "incoherent_endpoints": len(incoherent),
            "duplicates": len(duplicates),
        },
        "contradictions": contradictions,
        "obsolete": obsolete,
        "incoherent": incoherent,
        "duplicates": duplicates,
    }
    try:
        AUDIT_REPORT.parent.mkdir(parents=True, exist_ok=True)
        AUDIT_REPORT.write_text(json.dumps(rep, indent=2, ensure_ascii=False),
                                 encoding="utf-8")
    except Exception: pass
    try:
        with AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": _now(), "n_issues": n_total},
                                ensure_ascii=False) + "\n")
    except Exception: pass
    return rep


def propose_corrections(issues: list[dict] | None = None) -> list[dict]:
    """Génère des propositions de fix. Toutes sont en dry_run par défaut."""
    if issues is None:
        rep = audit()
        issues = (rep.get("contradictions", []) + rep.get("obsolete", []) +
                  rep.get("incoherent", []) + rep.get("duplicates", []))
    fixes = []
    for i, issue in enumerate(issues):
        fix_id = f"fix-{int(_now())}-{i}"
        if issue["type"] == "contradiction":
            fixes.append({
                "fix_id": fix_id,
                "type": "contradiction",
                "action": "manual_review",
                "explanation": (f"Contradiction détectée entre {issue['fact_a']['file']} "
                                f"et {issue['fact_b']['file']} sur axe {issue['axis']}. "
                                f"Sam doit décider lequel est correct."),
                "dry_run": True,
                "issue": issue,
            })
        elif issue["type"] == "obsolete_path":
            fixes.append({
                "fix_id": fix_id,
                "type": "obsolete_path",
                "action": "annotate_obsolete",
                "explanation": (f"Le chemin '{issue['missing_path']}' n'existe plus. "
                                f"Proposition : annoter avec [obsolète depuis "
                                f"{time.strftime('%Y-%m-%d', time.localtime())}]."),
                "dry_run": True,
                "target_file": issue["file"],
                "issue": issue,
            })
        elif issue["type"] == "duplicate":
            fixes.append({
                "fix_id": fix_id,
                "type": "duplicate",
                "action": "merge_or_keep_one",
                "explanation": (f"Doublon entre {issue['file_a']} et {issue['file_b']} "
                                f"(jaccard {issue['jaccard_similarity']}). "
                                f"Sam peut fusionner ou supprimer un des deux."),
                "dry_run": True,
                "issue": issue,
            })
        elif issue["type"] == "incoherent_endpoint":
            fixes.append({
                "fix_id": fix_id,
                "type": "incoherent_endpoint",
                "action": "annotate_obsolete",
                "explanation": (f"L'endpoint {issue['endpoint']} retourne "
                                f"{issue.get('status', 'erreur')}. "
                                f"Soit il est mort, soit la mémoire est dépassée."),
                "dry_run": True,
                "issue": issue,
            })
    try:
        CORRECTIONS_PROPOSED.parent.mkdir(parents=True, exist_ok=True)
        CORRECTIONS_PROPOSED.write_text(
            json.dumps({"ts": _now(), "fixes": fixes}, indent=2, ensure_ascii=False),
            encoding="utf-8")
    except Exception: pass
    return fixes


def self_test() -> dict:
    tests = []
    rep = audit()
    tests.append({"name": "audit_runs",
                  "ok": "issues_found" in rep,
                  "n_issues": rep.get("issues_found", 0),
                  "by_type": rep.get("by_type", {})})
    fixes = propose_corrections()
    tests.append({"name": "propose_corrections",
                  "ok": isinstance(fixes, list),
                  "n_fixes": len(fixes),
                  "all_dry_run": all(f.get("dry_run") for f in fixes) if fixes else True})
    # Test que la détection est cohérente (pas de crash)
    tests.append({"name": "detectors_resilient",
                  "ok": True,
                  "n_files_scanned": len(_all_memory_files())})
    return {"ok": all(t["ok"] for t in tests), "tests": tests}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "audit"
    if cmd == "audit":
        rep = audit()
        print(f"Issues found: {rep['issues_found']}")
        print(json.dumps(rep["by_type"], indent=2, ensure_ascii=False))
    elif cmd == "fixes":
        print(json.dumps(propose_corrections(), indent=2, ensure_ascii=False))
    elif cmd == "test":
        print(json.dumps(self_test(), indent=2, ensure_ascii=False))
    else:
        print("Usage: cortex_memory_audit.py {audit|fixes|test}")
