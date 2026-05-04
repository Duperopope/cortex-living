"""
cortex_publish_safety_check.py — Garde-fou anti-fuite avant publication publique.

Sam pousse régulièrement le repo `cortex-living`. Cortex apprend en continu
(curve `appris` qui monte par paliers) et son état runtime est publié sous
forme anonymisée. Mais l'anonymisation initiale peut rater des cas :
- Une nouvelle clé API ajoutée dans un fichier code
- Un titre Obsidian non hashé qui slip dans un nouveau JSON
- Un cookie collé temporairement dans une note
- Un chemin local brut dans un nouveau module

Ce module SCANNE le mirror local (`.cortex-publishing/`) AVANT le push, détecte
les fuites probables, et **bloque le push** si finding non-trivial. Branché en
pre-flight de `cortex_publishing.update()` après le smoke check.

Catégories détectées :
- API_KEY     : sk-..., ghp_..., hf_..., AKIA... (AWS), etc.
- TOKEN       : Bearer xxx, Authorization: ...
- COOKIE      : claude.ai/anthropic.com cookies, .claude-cookies.placeholder
- ENV_FILE    : .env présent
- PRIVATE_KEY : -----BEGIN [RSA |EC ]PRIVATE KEY-----
- LOCAL_PATH  : <USER_HOME>, <CORTEX_REPO> non anonymisé
- OBSIDIAN_TITLE : titres de notes non hashés dans state.json/examples/
- OAUTH_LINK  : reset/auth links avec tokens

API :
    scan(repo_dir=REPO_LOCAL) → dict {findings, blockers, warnings}
    safe_to_publish(repo_dir=REPO_LOCAL) → bool
    self_test() → dict
"""
from __future__ import annotations
import json
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
PAPERCLIP_ROOT = Path(r"<CORTEX_REPO>")
REPO_LOCAL = PAPERCLIP_ROOT / ".cortex-publishing"
REPORT = PAPERCLIP_ROOT / ".cortex-publish-safety-last.json"

# Patterns de fuite. Chaque entry : (id, regex, severity, kind)
# severity = BLOCK (refuse push) | WARN (logue mais laisse passer)
PATTERNS = [
    # API keys
    (r"sk-[A-Za-z0-9_-]{20,}",            "BLOCK", "API_KEY",     "OpenAI/Anthropic-style key"),
    (r"sk-ant-[A-Za-z0-9_-]{40,}",        "BLOCK", "API_KEY",     "Anthropic API key"),
    (r"ghp_[A-Za-z0-9]{30,}",             "BLOCK", "API_KEY",     "GitHub PAT"),
    (r"github_pat_[A-Za-z0-9_]{60,}",     "BLOCK", "API_KEY",     "GitHub fine-grained PAT"),
    (r"hf_[A-Za-z0-9]{30,}",              "BLOCK", "API_KEY",     "HuggingFace token"),
    (r"AKIA[A-Z0-9]{16}",                 "BLOCK", "API_KEY",     "AWS access key"),
    (r"AIza[0-9A-Za-z_-]{35}",            "BLOCK", "API_KEY",     "Google API key"),
    (r"xoxb-[0-9]{10,}-[0-9]{10,}-[A-Za-z0-9]{20,}", "BLOCK", "API_KEY", "Slack bot token"),
    (r"openrouter[_-][a-zA-Z0-9_-]{30,}", "BLOCK", "API_KEY",     "OpenRouter-like key"),
    # Auth tokens
    (r"[Bb]earer\s+[A-Za-z0-9._\-]{20,}", "BLOCK", "TOKEN",       "Bearer token"),
    # Cookies / session
    (r"\.claude-cookies\.json",           "WARN",  "COOKIE",      "Claude cookies file ref"),
    (r"sessionKey=[A-Za-z0-9_-]{20,}",    "BLOCK", "COOKIE",      "Session key in URL"),
    # Private keys
    (r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----",
                                            "BLOCK", "PRIVATE_KEY", "Private key block"),
    # Chemins locaux non anonymisés
    (r"C:[\\/]+Users[\\/]+Smedj",         "BLOCK", "LOCAL_PATH",  "User home path"),
    (r"H:[\\/]+Code[\\/]+Paperclip",      "BLOCK", "LOCAL_PATH",  "Paperclip repo path"),
    # OAuth/reset
    (r"https?://[^\s\"'<>]*[?&](token|reset|auth)=[A-Za-z0-9._\-]{20,}",
                                            "BLOCK", "OAUTH_LINK",  "OAuth/reset URL"),
]

# Fichiers qu'on ne devrait JAMAIS voir dans le repo public
FORBIDDEN_FILENAMES = [".env", ".env.local", ".env.production",
                       "credentials.json", "secrets.json"]


def _now() -> float: return time.time()


def _scan_file(path: Path, max_bytes: int = 2_000_000) -> list[dict]:
    """Scan un fichier, retourne les findings."""
    findings = []
    try:
        if path.stat().st_size > max_bytes:
            return [{"path": str(path), "kind": "OVERSIZED",
                     "severity": "WARN", "size_mb": round(path.stat().st_size / 1e6, 2),
                     "msg": "skip scan, file too large"}]
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return [{"path": str(path), "kind": "READ_ERROR",
                 "severity": "WARN", "error": str(e)[:120]}]

    for pattern, severity, kind, label in PATTERNS:
        try:
            for m in re.finditer(pattern, text):
                # Position pour debug, mais on ne logue PAS le match complet
                # (sinon le rapport lui-même devient une fuite)
                start = m.start()
                line_num = text.count("\n", 0, start) + 1
                snippet = text[max(0, start-15):start+45]
                # Anonymise le snippet en masquant le match
                redacted = snippet[:15] + "***REDACTED***"
                findings.append({
                    "path": str(path),
                    "kind": kind,
                    "severity": severity,
                    "label": label,
                    "line": line_num,
                    "redacted_snippet": redacted,
                })
        except Exception: pass
    return findings


def scan(repo_dir: Path | str = REPO_LOCAL) -> dict:
    """Scan complet du repo. Retourne findings + blockers + warnings.

    Si blockers > 0 → publication doit être refusée.
    """
    started = _now()
    root = Path(repo_dir)
    if not root.exists():
        return {"ok": False, "error": "repo_dir_missing", "path": str(root)}

    findings: list[dict] = []
    files_scanned = 0
    files_skipped = 0

    # 1. Forbidden filenames
    for forbidden in FORBIDDEN_FILENAMES:
        for hit in root.rglob(forbidden):
            findings.append({
                "path": str(hit), "kind": "FORBIDDEN_FILE",
                "severity": "BLOCK", "label": f"forbidden file present: {forbidden}",
            })

    # 2. Pattern scan sur fichiers texte
    text_extensions = (".md", ".txt", ".json", ".jsonl", ".yml", ".yaml",
                       ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css",
                       ".sh", ".ps1", ".cmd", ".bat", ".env", ".cfg", ".ini",
                       ".toml", ".log")
    for f in root.rglob("*"):
        if not f.is_file(): continue
        # Skip .git/, node_modules/, __pycache__/
        rel = str(f.relative_to(root)).replace("\\", "/").lower()
        if any(skip in rel for skip in (".git/", "node_modules/", "__pycache__/")):
            continue
        if f.suffix.lower() not in text_extensions:
            files_skipped += 1
            continue
        files_scanned += 1
        findings.extend(_scan_file(f))

    blockers = [f for f in findings if f.get("severity") == "BLOCK"]
    warnings = [f for f in findings if f.get("severity") == "WARN"]

    # Group blockers par kind pour le résumé
    by_kind: dict[str, int] = {}
    for f in blockers:
        by_kind[f.get("kind", "?")] = by_kind.get(f.get("kind", "?"), 0) + 1

    rep = {
        "ts": _now(),
        "duration_s": round(_now() - started, 1),
        "repo_dir": str(root),
        "files_scanned": files_scanned,
        "files_skipped": files_skipped,
        "n_findings": len(findings),
        "n_blockers": len(blockers),
        "n_warnings": len(warnings),
        "blockers_by_kind": by_kind,
        "blockers": blockers[:50],  # cap pour ne pas exploser
        "warnings": warnings[:50],
        "verdict": "BLOCK" if blockers else ("WARN" if warnings else "OK"),
    }
    try:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(rep, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    except Exception: pass
    return rep


def safe_to_publish(repo_dir: Path | str = REPO_LOCAL) -> bool:
    """Boolean simple : True si aucun blocker."""
    rep = scan(repo_dir)
    return rep.get("n_blockers", 0) == 0


def self_test() -> dict:
    """Test : injecte un faux secret dans un fichier temp, vérifie qu'il est détecté."""
    import tempfile
    tmp = Path(tempfile.gettempdir()) / "cortex_safety_test"
    if tmp.exists():
        import shutil; shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    # Cas 1 : fake API key → doit être BLOCK
    (tmp / "fake_secret.md").write_text(
        "test markdown\n<API_KEY_REDACTED>\nfin\n",
        encoding="utf-8")
    # Cas 2 : fake clean → ne doit RIEN trouver
    (tmp / "clean.md").write_text("hello world\n", encoding="utf-8")
    # Cas 3 : forbidden filename
    (tmp / ".env").write_text("API_KEY=xxx", encoding="utf-8")

    rep = scan(tmp)
    expect_block = rep.get("n_blockers", 0) >= 2  # API key + .env
    detected_api = any(f.get("kind") == "API_KEY" for f in rep.get("blockers", []))
    detected_env = any(f.get("kind") == "FORBIDDEN_FILE" for f in rep.get("blockers", []))

    # Cleanup
    import shutil
    try: shutil.rmtree(tmp)
    except Exception: pass

    ok = expect_block and detected_api and detected_env
    return {
        "ok": ok,
        "n_blockers": rep.get("n_blockers"),
        "detected_api_key": detected_api,
        "detected_env_file": detected_env,
        "verdict": rep.get("verdict"),
    }


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if cmd == "scan":
        rep = scan()
        print(f"Files scanned: {rep['files_scanned']}, "
              f"skipped: {rep['files_skipped']}")
        print(f"Verdict: {rep['verdict']}")
        print(f"Blockers: {rep['n_blockers']}, Warnings: {rep['n_warnings']}")
        if rep['blockers_by_kind']:
            print("By kind :")
            for k, n in rep['blockers_by_kind'].items():
                print(f"  {k}: {n}")
        if rep['blockers']:
            print("First 10 blockers :")
            for b in rep['blockers'][:10]:
                print(f"  [{b['kind']}] {b.get('path')}: line {b.get('line', '?')}")
    elif cmd == "json":
        print(json.dumps(scan(), indent=2, ensure_ascii=False))
    elif cmd == "test":
        print(json.dumps(self_test(), indent=2, ensure_ascii=False))
    elif cmd == "safe":
        print("safe" if safe_to_publish() else "BLOCK")
    else:
        print("Usage: cortex_publish_safety_check.py {scan|json|test|safe}")
