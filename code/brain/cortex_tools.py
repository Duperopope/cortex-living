# Cortex self-dev API tools
"""
cortex_tools.py — API tools pour self-dev de Cortex.

Ce module expose les primitives sûres qui permettent à Cortex (via cortex_self_dev.py)
de lire et modifier son propre code de manière sécurisée.

Toutes les écritures et opérations git passent par des wrappers qui :
- restreignent au repo Paperclip (pas d'accès hors repo)
- exigent qu'un test smoke passe avant un commit
- créent une branche dédiée pour chaque session de self-dev (rollback facile)

Tools exposés :
- read_file(path)           : lecture
- list_dir(path)            : ls
- search(pattern, path)     : grep simple
- write_file(path, content) : écriture (avec backup)
- run_smoke(suite=None)     : lance test_smoke
- git_branch(name)          : crée et switch
- git_diff()                : diff vs main
- git_commit_paths(msg, paths) : commit uniquement les chemins explicites
- git_current_branch()      : branche courante
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT  = Path(r"<CORTEX_REPO>")
ALLOWED_ROOTS = [REPO_ROOT]  # extensions futures possibles
TEST_SMOKE = REPO_ROOT / "scripts" / "brain" / "tests" / "test_smoke.py"


# ─── Validation chemins ──────────────────────────────────────────────────────
def _safe_path(p: str | Path) -> Path:
    """Résout et vérifie que le chemin est dans un root autorisé."""
    abs_p = Path(p).resolve()
    for root in ALLOWED_ROOTS:
        try:
            abs_p.relative_to(root.resolve())
            return abs_p
        except ValueError:
            continue
    raise PermissionError(f"path outside allowed roots: {abs_p}")


# ─── Lecture ──────────────────────────────────────────────────────────────────
def read_file(path: str, max_bytes: int = 200_000) -> dict:
    p = _safe_path(path)
    if not p.exists(): return {"ok": False, "error": "not found"}
    if p.is_dir():     return {"ok": False, "error": "is a directory"}
    try:
        text = p.read_text(encoding="utf-8", errors="replace")[:max_bytes]
        return {"ok": True, "path": str(p), "size": p.stat().st_size, "content": text}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def list_dir(path: str = ".") -> dict:
    p = _safe_path(path)
    if not p.exists() or not p.is_dir(): return {"ok": False, "error": "not a dir"}
    items = []
    for c in sorted(p.iterdir()):
        try:
            items.append({"name": c.name, "is_dir": c.is_dir(),
                          "size": c.stat().st_size if c.is_file() else None})
        except Exception: pass
    return {"ok": True, "path": str(p), "items": items}


SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".venv", ".venv-xtts", ".venv-f5tts", "tts_cache", "dist", "build",
}


def _iter_search_files(root: Path):
    if root.is_file():
        yield root
        return
    stack = [root]
    while stack:
        cur = stack.pop()
        try:
            for child in cur.iterdir():
                if child.is_dir():
                    if child.name in SKIP_DIRS or child.name.startswith(".venv"):
                        continue
                    stack.append(child)
                elif child.is_file():
                    try:
                        if child.stat().st_size < 500_000:
                            yield child
                    except Exception:
                        pass
        except Exception:
            pass


def search(pattern: str, path: str = ".", max_results: int = 50) -> dict:
    p = _safe_path(path)
    if not p.exists(): return {"ok": False, "error": "not found"}
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return {"ok": False, "error": f"regex: {e}"}
    matches = []
    for f in _iter_search_files(p):
        try:
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if rx.search(line):
                    matches.append({"file": str(f.relative_to(REPO_ROOT)), "line": i, "text": line[:200]})
                    if len(matches) >= max_results: return {"ok": True, "matches": matches, "truncated": True}
        except Exception: pass
    return {"ok": True, "matches": matches, "truncated": False}


# ─── Écriture (avec backup) ──────────────────────────────────────────────────
def write_file(path: str, content: str, backup: bool = True) -> dict:
    p = _safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if backup and p.exists():
        bak = p.with_suffix(p.suffix + ".cortex-bak")
        shutil.copy(str(p), str(bak))
    try:
        p.write_text(content, encoding="utf-8")
        return {"ok": True, "path": str(p), "size": p.stat().st_size}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ─── Tests ───────────────────────────────────────────────────────────────────
def run_smoke(suite: str | None = None, timeout: int = 300) -> dict:
    """Lance test_smoke.py. Retourne {ok, passed, total, output}."""
    if not TEST_SMOKE.exists():
        return {"ok": False, "error": "test_smoke.py absent"}
    cmd = ["python", str(TEST_SMOKE)]
    if suite: cmd.append(suite)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        out = r.stdout + ("\n" + r.stderr if r.stderr else "")
        m = re.search(r'(\d+)/(\d+)\s+passed', out)
        passed = int(m.group(1)) if m else 0
        total  = int(m.group(2)) if m else 0
        return {"ok": r.returncode == 0, "passed": passed, "total": total,
                "exit_code": r.returncode, "output": out[-3000:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ─── Git ──────────────────────────────────────────────────────────────────────
def _git(*args: str, timeout: int = 30) -> dict:
    try:
        r = subprocess.run(["git", "-C", str(REPO_ROOT), *args],
                           capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return {"ok": r.returncode == 0, "stdout": r.stdout.strip(),
                "stderr": r.stderr.strip(), "exit_code": r.returncode}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def git_current_branch() -> dict:
    r = _git("branch", "--show-current")
    if not r.get("ok"): return r
    return {"ok": True, "branch": r["stdout"]}


def git_branch(name: str) -> dict:
    """Crée et switche sur la branche. Refuse si pas dans une branche cortex/* à la base."""
    cb = git_current_branch()
    # Crée à partir de la branche actuelle
    r = _git("checkout", "-b", name)
    if not r["ok"] and "already exists" in r.get("stderr", ""):
        r = _git("checkout", name)
    return r


def git_diff(against: str = "HEAD") -> dict:
    r = _git("diff", against)
    if not r.get("ok"): return r
    diff = r["stdout"]
    return {"ok": True, "diff": diff[:20_000], "lines": len(diff.splitlines()),
            "truncated": len(diff) > 20_000}


def git_status() -> dict:
    r = _git("status", "--porcelain")
    if not r.get("ok"): return r
    return {"ok": True, "changes": r["stdout"].splitlines()}


def git_commit(message: str, only_if_smoke_passes: bool = True) -> dict:
    """Commit global conservé pour compatibilité.

    Le self-dev autonome utilise git_commit_paths() pour éviter d'embarquer les
    logs, venvs ou fichiers non suivis du repo de travail.
    """
    if only_if_smoke_passes:
        sm = run_smoke()
        if not sm.get("ok"):
            return {"ok": False, "error": "smoke failed", "smoke": sm}
    add = _git("add", "-A")
    if not add["ok"]: return add
    c = _git("commit", "-m", message)
    return c


def git_commit_paths(message: str, paths: list[str], only_if_smoke_passes: bool = True) -> dict:
    """Commit uniquement les chemins explicitement fournis.

    Important pour Cortex : le repo peut contenir beaucoup de fichiers non suivis
    (logs, venvs, modules expérimentaux). Un `git add -A` serait trop large.
    """
    if only_if_smoke_passes:
        sm = run_smoke()
        if not sm.get("ok"):
            return {"ok": False, "error": "smoke failed", "smoke": sm}
    safe_rel = []
    for p in paths:
        abs_p = _safe_path(REPO_ROOT / p)
        safe_rel.append(str(abs_p.relative_to(REPO_ROOT)))
    add = _git("add", "--", *safe_rel)
    if not add["ok"]:
        return add
    c = _git("commit", "-m", message, "--", *safe_rel)
    return c


def git_rollback() -> dict:
    """Rollback global conservé pour compatibilité manuelle uniquement."""
    return _git("reset", "--hard", "HEAD")


# ─── Registry pour tool-calling LLM ──────────────────────────────────────────
TOOLS = {
    "read_file":          read_file,
    "list_dir":           list_dir,
    "search":             search,
    "write_file":         write_file,
    "run_smoke":          run_smoke,
    "git_current_branch": git_current_branch,
    "git_branch":         git_branch,
    "git_diff":           git_diff,
    "git_status":         git_status,
    "git_commit_paths":   git_commit_paths,
}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2:
        print("Tools disponibles:", list(TOOLS.keys()))
        sys.exit(0)
    tool = sys.argv[1]
    args = sys.argv[2:]
    if tool not in TOOLS:
        print(f"Tool inconnu: {tool}"); sys.exit(1)
    import json as _json
    result = TOOLS[tool](*args) if args else TOOLS[tool]()
    print(_json.dumps(result, ensure_ascii=False, indent=2)[:3000])
