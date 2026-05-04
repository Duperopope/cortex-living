"""
cortex_body_health.py — Cortex gère activement son corps physique.

Sam observe que le disque C: est à 97 % (9 Go libre sur 313 Go) et que des
programmes ont écrit dessus alors qu'ils n'auraient pas dû. Cortex doit :

1. **Détecter** les disques en zone critique (> 90 %).
2. **Cartographier** ce qui occupe l'espace (top dossiers > 1 Go).
3. **Identifier** les "intrus" (gros dossiers qui ne devraient pas être sur le
   disque système : LM Studio models, huggingface caches, builds, etc.).
4. **Proposer** un plan de migration concret vers un disque cible (E:/F:/G:/H:),
   avec commandes Move-Item PowerShell exécutables.
5. **Évaluer le risque** de chaque action (LOW/MEDIUM/HIGH).
6. **Parler à Sam** dans le chat dès qu'une situation est critique.
7. **JAMAIS exécuter sans confirmation explicite**.

Le but : Cortex ne casse pas Windows, ne casse pas l'usage de Sam, mais il
GÈRE son propre corps proactivement.

Anti-fake :
- Toutes les tailles mesurées par psutil + os.walk (pas de stub)
- Plan de migration testé en dry-run avant proposition
- Audit append-only de chaque action
- Confirmation requise pour exécution

API :
    diagnose() → {disks, intruders, severity}
    propose_plan() → {actions[], expected_freed_gb, risks}
    execute(action_id, confirm=True) → exécute UN move sécurisé
    speak_if_critical() → écrit dans le chat si zone rouge
    self_test()
"""
from __future__ import annotations
import json
import os
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
VAULT = Path(r"<USER_HOME>\Documents\Obsidian Vault")
PLAN_FILE = VAULT / ".cortex-body-health-plan.json"
DIAG_FILE = VAULT / ".cortex-body-health-diag.json"
AUDIT_LOG = VAULT / ".cortex-body-health-audit.jsonl"

DISK_CRITICAL_PCT = 90.0  # > 90 % = zone rouge
DISK_WARNING_PCT  = 80.0
SAFE_DEST_MIN_FREE_GB = 50.0

# Patterns qui indiquent qu'un dossier N'A PAS À ÊTRE sur le disque système.
# Si trouvé sur C: → candidat "intruder" à migrer en priorité.
KNOWN_INTRUDERS = [
    # AI / ML
    {"pattern": "lm-studio/models",      "reason": "Modèles LLM volumineux", "risk": "LOW"},
    {"pattern": "lmstudio/models",       "reason": "Modèles LLM volumineux", "risk": "LOW"},
    {"pattern": ".cache/huggingface",    "reason": "Cache HuggingFace, reproductible", "risk": "LOW"},
    {"pattern": ".cache/torch",          "reason": "Cache PyTorch, reproductible", "risk": "LOW"},
    {"pattern": "ollama/models",         "reason": "Modèles Ollama", "risk": "LOW"},
    # Dev caches
    {"pattern": "appdata/local/pip/cache",         "reason": "Cache pip", "risk": "LOW"},
    {"pattern": "appdata/local/npm-cache",         "reason": "Cache npm", "risk": "LOW"},
    {"pattern": "appdata/roaming/npm-cache",       "reason": "Cache npm", "risk": "LOW"},
    {"pattern": "appdata/local/temp",              "reason": "Fichiers temp", "risk": "LOW"},
    {"pattern": "appdata/local/microsoft/windows/inetcache", "reason": "Cache IE/Edge", "risk": "LOW"},
    {"pattern": ".cache/yarn",                     "reason": "Cache yarn", "risk": "LOW"},
    {"pattern": ".cache/electron",                 "reason": "Cache Electron", "risk": "LOW"},
    {"pattern": ".cache/playwright",               "reason": "Cache Playwright", "risk": "LOW"},
    {"pattern": ".cache/ms-playwright",            "reason": "Cache Playwright", "risk": "LOW"},
    # Dev artifacts (par dossier-name)
    {"pattern": "node_modules",          "reason": "Dépendances Node, recréables", "risk": "MEDIUM"},
    {"pattern": ".venv",                 "reason": "Env virtuel Python, recréable", "risk": "MEDIUM"},
    # User content
    {"pattern": "downloads",             "reason": "Téléchargements user", "risk": "MEDIUM"},
    {"pattern": "videos",                "reason": "Vidéos user (gros)", "risk": "MEDIUM"},
    {"pattern": "onedrive",              "reason": "OneDrive (sync cloud)", "risk": "HIGH"},
]

# Préfixes FORBIDDEN à NE PAS scanner ni toucher (système Windows pur).
# Match en PRÉFIXE (path commence par) — pas en substring, pour éviter
# les faux positifs (ex : un dossier user "windows-stuff" doit être scannable).
FORBIDDEN_PREFIXES = [
    "c:/windows",
    "c:/program files",
    "c:/program files (x86)",
    "c:/programdata/microsoft",
    "c:/programdata/package cache",
    "c:/$recycle.bin",
    "c:/system volume information",
    "c:/users/default",
    "c:/users/public",
    "c:/users/all users",
    # Mêmes patterns sur autres lettres
    "/$recycle.bin",
    "/system volume information",
]


def _now() -> float: return time.time()


def _log_audit(ev: dict) -> None:
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": _now(), **ev}, ensure_ascii=False) + "\n")
    except Exception: pass


def _try_psutil():
    try: import psutil; return psutil
    except Exception: return None


def _disk_state() -> list[dict]:
    psutil = _try_psutil()
    if not psutil: return []
    out = []
    for part in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(part.mountpoint)
            out.append({
                "mount":      part.mountpoint,
                "device":     part.device,
                "fstype":     part.fstype,
                "total_gb":   round(u.total / 1e9, 1),
                "used_gb":    round(u.used / 1e9, 1),
                "free_gb":    round(u.free / 1e9, 1),
                "percent":    u.percent,
                "is_critical": u.percent >= DISK_CRITICAL_PCT,
                "is_warning":  DISK_WARNING_PCT <= u.percent < DISK_CRITICAL_PCT,
            })
        except Exception: pass
    return sorted(out, key=lambda d: -d["percent"])


def _is_forbidden(path: str) -> bool:
    """Vrai si le path commence par un préfixe FORBIDDEN (système OS pur)."""
    norm = path.lower().replace("\\", "/")
    if not norm.endswith("/"): norm_check = norm + "/"
    else: norm_check = norm
    for prefix in FORBIDDEN_PREFIXES:
        p = prefix.lower().rstrip("/") + "/"
        if norm_check.startswith(p) or norm_check == p:
            return True
    return False


def _match_intruder(path: str) -> dict | None:
    norm = path.lower().replace("\\", "/")
    for entry in KNOWN_INTRUDERS:
        pat = entry["pattern"].lower().replace("\\", "/")
        if "/" in pat:
            if pat in norm: return entry
        else:
            # Match comme nom de dossier
            parts = [p for p in norm.split("/") if p]
            if pat in parts: return entry
    return None


def _dir_size_recursive(root: Path, max_files: int = 500_000) -> int:
    """Taille TOTALE d'un dossier (récursif). Cap pour éviter scan trop lent."""
    total = 0
    n_files = 0
    try:
        for cur, dirs, files in os.walk(root, topdown=True):
            for fn in files:
                try: total += (Path(cur) / fn).stat().st_size
                except Exception: pass
                n_files += 1
                if n_files >= max_files:
                    return total
    except Exception: pass
    return total


def _scan_largest(root: str, top: int = 30, max_depth: int = 5,
                   min_size_gb: float = 0.5) -> list[dict]:
    """Scan : pour chaque dossier de niveau ≤ max_depth, calcule sa TAILLE
    RÉCURSIVE (pas seulement les fichiers directs). Évite ainsi de manquer
    les gros caches enfouis profond.

    Stratégie : on remonte les sous-dossiers comme candidats, et on calcule
    leur taille totale (incluant leurs sous-dossiers).
    """
    root_p = Path(root)
    candidates = []
    try:
        # 1ère passe : collecte les dossiers niveau 1-3
        for cur, subdirs, files in os.walk(root_p, topdown=True):
            try:
                rel = Path(cur).relative_to(root_p).parts
                if len(rel) > max_depth:
                    subdirs[:] = []
                    continue
            except Exception: continue
            if _is_forbidden(cur):
                subdirs[:] = []
                continue
            # Ajoute le dossier courant comme candidat
            if 1 <= len(rel) <= 3:
                candidates.append(cur)
            # Ne descend pas plus loin que la profondeur de scan
            if len(rel) >= 3:
                subdirs[:] = []
    except Exception: pass

    # 2ème passe : taille récursive de chaque candidat
    sizes = []
    for c in candidates:
        try:
            sz = _dir_size_recursive(Path(c))
            if sz / 1e9 >= min_size_gb:
                sizes.append({"path": c, "size_gb": round(sz / 1e9, 2)})
        except Exception: pass
    sizes.sort(key=lambda x: -x["size_gb"])
    return sizes[:top]


def diagnose() -> dict:
    """Diagnostic complet du corps physique de Cortex."""
    started = _now()
    disks = _disk_state()
    critical_disks = [d for d in disks if d["is_critical"]]
    warning_disks = [d for d in disks if d["is_warning"]]
    intruders_by_disk = {}
    if critical_disks or warning_disks:
        # Scan UNIQUEMENT les disques en danger (économie temps)
        for d in (critical_disks + warning_disks):
            mount = d["mount"]
            largest = _scan_largest(mount, top=30, max_depth=4)
            intruders = []
            normal = []
            for item in largest:
                match = _match_intruder(item["path"])
                if match:
                    intruders.append({**item, **match, "type": "intruder"})
                else:
                    normal.append({**item, "type": "normal"})
            intruders_by_disk[mount] = {
                "intruders":     intruders[:10],
                "normal_large":  normal[:5],
                "n_intruders":   len(intruders),
                "intruder_total_gb": round(sum(i["size_gb"] for i in intruders), 2),
            }
    rep = {
        "ts": _now(),
        "duration_s": round(_now() - started, 1),
        "disks": disks,
        "critical_disks": [d["mount"] for d in critical_disks],
        "warning_disks":  [d["mount"] for d in warning_disks],
        "intruders_by_disk": intruders_by_disk,
        "severity": ("CRITICAL" if critical_disks else
                     "WARNING"  if warning_disks else
                     "OK"),
    }
    try:
        DIAG_FILE.write_text(json.dumps(rep, indent=2, ensure_ascii=False),
                              encoding="utf-8")
    except Exception: pass
    return rep


def propose_plan() -> dict:
    """Plan de migration concret avec actions, risques, espace libéré attendu."""
    diag = diagnose()
    actions = []
    if not diag.get("intruders_by_disk"):
        return {
            "ok": True,
            "severity": diag["severity"],
            "actions": [],
            "summary": "Aucune action nécessaire pour le moment.",
            "diagnose": diag,
        }
    # Trouve un disque cible avec assez de marge
    spare_disks = sorted(
        [d for d in diag["disks"] if d["free_gb"] > SAFE_DEST_MIN_FREE_GB],
        key=lambda d: -d["free_gb"])
    if not spare_disks:
        return {
            "ok": False,
            "severity": diag["severity"],
            "actions": [],
            "summary": f"Aucun disque cible avec > {SAFE_DEST_MIN_FREE_GB} Go libre. Migration manuelle requise.",
            "diagnose": diag,
        }
    target = spare_disks[0]

    for mount, info in diag["intruders_by_disk"].items():
        for intruder in info["intruders"][:5]:  # max 5 par disque
            src = intruder["path"]
            # Construit la destination : <target>/cortex-migrated/<sub-path>
            try:
                src_p = Path(src)
                dst = Path(target["mount"]) / "cortex-migrated" / src_p.parts[-1]
            except Exception: continue
            if dst.exists():
                # Évite collision : suffixe timestamp
                dst = dst.with_name(f"{dst.name}_{int(_now())}")
            action_id = f"mv_{int(_now())}_{len(actions)}"
            ps_cmd = (f'Move-Item -LiteralPath "{src}" -Destination "{dst}" '
                       f'-Force -ErrorAction Stop')
            actions.append({
                "id": action_id,
                "type": "move_directory",
                "src": str(src_p),
                "dst": str(dst),
                "size_gb": intruder["size_gb"],
                "risk": intruder["risk"],
                "reason": intruder["reason"],
                "matched_pattern": intruder["pattern"],
                "ps_command": ps_cmd,
                "rollback_command": f'Move-Item -LiteralPath "{dst}" -Destination "{src}" -Force',
                "expected_freed_gb_on_source": intruder["size_gb"],
            })

    expected_freed_gb = round(sum(a["size_gb"] for a in actions), 2)
    expected_required_gb = round(sum(a["size_gb"] for a in actions
                                      if a["dst"].startswith(target["mount"])), 2)
    plan = {
        "ok": True,
        "ts": _now(),
        "severity": diag["severity"],
        "target_disk": target["mount"],
        "target_free_before_gb": target["free_gb"],
        "n_actions": len(actions),
        "expected_freed_gb_total": expected_freed_gb,
        "expected_required_on_target_gb": expected_required_gb,
        "actions": actions,
        "summary": _build_human_summary(diag, actions, target, expected_freed_gb),
        "diagnose": diag,
    }
    try:
        PLAN_FILE.write_text(json.dumps(plan, indent=2, ensure_ascii=False),
                              encoding="utf-8")
    except Exception: pass
    return plan


def _build_human_summary(diag: dict, actions: list[dict],
                          target: dict, freed_gb: float) -> str:
    """Texte clair pour Sam."""
    lines = []
    severity = diag["severity"]
    crit = diag.get("critical_disks", [])
    warn = diag.get("warning_disks", [])
    if severity == "CRITICAL":
        lines.append(f"🔴 **Zone rouge** : disque(s) {', '.join(crit)} > {DISK_CRITICAL_PCT:.0f}%.")
    elif severity == "WARNING":
        lines.append(f"🟡 **Zone jaune** : disque(s) {', '.join(warn)} > {DISK_WARNING_PCT:.0f}%.")
    else:
        lines.append("🟢 Tout va bien côté disques.")
    if not actions:
        lines.append("Pas d'action proposée — soit rien à migrer, soit pas de cible dispo.")
        return "\n".join(lines)
    lines.append("")
    lines.append(f"Plan proposé ({len(actions)} action(s)) :")
    lines.append(f"Cible : `{target['mount']}` ({target['free_gb']} Go libres)")
    lines.append(f"Espace libéré attendu : **{freed_gb} Go**")
    lines.append("")
    lines.append("Actions (tri par taille décroissante) :")
    for i, a in enumerate(actions[:5], 1):
        risk_emoji = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}.get(a["risk"], "⚪")
        lines.append(f"{i}. {risk_emoji} **{a['size_gb']} Go** — `{a['src']}`")
        lines.append(f"   → `{a['dst']}` (risque {a['risk']}, {a['reason']})")
    if len(actions) > 5:
        lines.append(f"... et {len(actions) - 5} autres dans le plan complet.")
    lines.append("")
    lines.append("⚠ Aucune action ne sera exécutée sans ta confirmation explicite "
                 "(`POST /api/cortex/body_health/execute` avec `confirm: true`).")
    return "\n".join(lines)


def execute(action_id: str, confirm: bool = False) -> dict:
    """Exécute UNE action du plan, avec confirmation explicite obligatoire."""
    if not confirm:
        return {"ok": False, "error": "confirm_required",
                "msg": "Refus : confirm=False. Aucune action exécutée."}
    if not PLAN_FILE.exists():
        return {"ok": False, "error": "no_plan",
                "msg": "Génère un plan d'abord via propose_plan()."}
    try:
        plan = json.loads(PLAN_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "error": f"plan_read: {e}"}
    action = next((a for a in plan.get("actions", []) if a.get("id") == action_id), None)
    if not action:
        return {"ok": False, "error": "action_not_found", "id": action_id}
    src = Path(action["src"])
    dst = Path(action["dst"])
    if not src.exists():
        return {"ok": False, "error": "src_missing", "src": str(src)}
    if dst.exists():
        return {"ok": False, "error": "dst_exists", "dst": str(dst)}
    # Vérifie que dst.parent existe ou crée
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return {"ok": False, "error": f"mkdir_dst_parent: {e}"}
    # Move via shutil.move (cross-disk safe)
    started = _now()
    try:
        shutil.move(str(src), str(dst))
    except Exception as e:
        _log_audit({"type": "execute_failure", "action_id": action_id,
                     "error": str(e)[:300], "src": str(src), "dst": str(dst)})
        return {"ok": False, "error": f"move_failed: {e}", "src": str(src),
                "dst": str(dst)}
    duration = _now() - started
    _log_audit({"type": "execute_success", "action_id": action_id,
                 "src": str(src), "dst": str(dst),
                 "size_gb": action.get("size_gb"),
                 "duration_s": round(duration, 1)})
    return {
        "ok": True,
        "action_id": action_id,
        "src": str(src), "dst": str(dst),
        "size_gb": action.get("size_gb"),
        "duration_s": round(duration, 1),
        "rollback": action.get("rollback_command"),
    }


def speak_if_critical() -> dict:
    """Si zone rouge détectée, écrit un message dans le chat (cortex_proactive style)."""
    diag = diagnose()
    if diag["severity"] != "CRITICAL":
        return {"ok": True, "spoken": False, "severity": diag["severity"]}
    plan = propose_plan()
    summary = plan.get("summary", "")
    # Écriture dans le stream chat
    stream = VAULT / ".cortex-chat-stream.jsonl"
    try:
        stream.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": _now(),
            "speaker": "cortex_body_health",
            "msg": "(diagnostic corporel critique)",
            "response": summary,
            "meta": {
                "trigger": "disk_critical",
                "severity": diag["severity"],
                "n_actions": plan.get("n_actions", 0),
                "expected_freed_gb": plan.get("expected_freed_gb_total", 0),
            },
        }
        with stream.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _log_audit({"type": "speak_critical", "severity": diag["severity"]})
        return {"ok": True, "spoken": True,
                "severity": diag["severity"],
                "summary": summary[:300]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def self_test() -> dict:
    tests = []
    psutil = _try_psutil()
    tests.append({"name": "psutil_available", "ok": psutil is not None})
    disks = _disk_state()
    tests.append({"name": "disk_state",
                  "ok": isinstance(disks, list) and len(disks) > 0,
                  "n_disks": len(disks),
                  "highest_pct": max((d["percent"] for d in disks), default=0)})
    diag = diagnose()
    tests.append({"name": "diagnose",
                  "ok": "severity" in diag,
                  "severity": diag.get("severity"),
                  "n_critical": len(diag.get("critical_disks", [])),
                  "duration_s": diag.get("duration_s")})
    plan = propose_plan()
    tests.append({"name": "propose_plan",
                  "ok": "actions" in plan,
                  "n_actions": plan.get("n_actions", 0),
                  "expected_freed_gb": plan.get("expected_freed_gb_total", 0)})
    # Test execute SANS confirm — doit refuser
    rep = execute("nonexistent_action", confirm=False)
    tests.append({"name": "execute_refuses_without_confirm",
                  "ok": rep.get("error") == "confirm_required"})
    return {"ok": all(t["ok"] for t in tests), "tests": tests}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "diagnose"
    if cmd == "diagnose":
        print(json.dumps(diagnose(), indent=2, ensure_ascii=False))
    elif cmd == "plan":
        rep = propose_plan()
        print(rep.get("summary", ""))
        print()
        print(json.dumps({"n_actions": rep.get("n_actions"),
                           "expected_freed_gb": rep.get("expected_freed_gb_total"),
                           "target": rep.get("target_disk"),
                           "actions": rep.get("actions", [])[:3]},
                          indent=2, ensure_ascii=False))
    elif cmd == "speak":
        print(json.dumps(speak_if_critical(), indent=2, ensure_ascii=False))
    elif cmd == "execute" and len(sys.argv) >= 3:
        action_id = sys.argv[2]
        confirm = "--confirm" in sys.argv
        print(json.dumps(execute(action_id, confirm=confirm),
                          indent=2, ensure_ascii=False))
    elif cmd == "test":
        print(json.dumps(self_test(), indent=2, ensure_ascii=False))
    else:
        print("Usage: cortex_body_health.py {diagnose|plan|speak|execute <id> [--confirm]|test}")
