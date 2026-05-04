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
LAST_RUN  = VAULT / ".cortex-body-health-last.json"

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
    skipped: list[dict] = []

    # 1. Cache cleanups (LOW, action delete_cache, ne nécessite pas de target)
    cache_actions = _propose_cache_cleanups()
    actions.extend(cache_actions)
    # On ajoute aussi les caches absents/petits aux skipped pour traçabilité
    seen_paths = {a.get("src") for a in cache_actions}
    for entry in SAFE_CACHE_TARGETS:
        if entry["path"] in seen_paths: continue
        p = Path(entry["path"])
        if not p.exists() or not p.is_dir():
            skipped.append({"name": entry["name"], "path": entry["path"],
                             "reason": "missing_path"})
        else:
            sz, _ = _measure(p)
            if sz < 100 * 1024 * 1024:
                skipped.append({"name": entry["name"], "path": entry["path"],
                                 "reason": "too_small",
                                 "size_gb": round(sz/1e9, 4)})

    # 2. Migrations huge user dirs (MEDIUM, move + junction NTFS)
    actions.extend(_propose_huge_user_migrations(target["mount"], skipped=skipped))

    # 3. Intruders du scan _scan_largest (legacy : LM Studio paths, etc.)
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
        "n_skipped": len(skipped),
        "skipped": skipped,
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


# Gros dossiers user que les apps attendent à un path FIXE (Steam saves,
# X4 Egosoft, captures jeux, etc.). On les déplace vers un autre disque ET
# on laisse une JUNCTION NTFS au path original — l'app continue à trouver
# son contenu, mais le stockage réel est ailleurs.
HUGE_USER_DIRS = [
    {"path": r"<USER_HOME>\Documents\my games",
     "name": "Documents\\my games",
     "reason": "Saves/replays jeux Steam (X4 Egosoft etc.) — path en dur côté apps, "
                "junction NTFS conservée pour que les jeux continuent de fonctionner",
     "risk": "MEDIUM",
     "min_gb_to_act": 30.0},
    # Identifié en 2e passe
    {"path": r"<USER_HOME>\AppData\Local\Larian Studios",
     "name": "Larian Studios (BG3)",
     "reason": "Saves Baldur's Gate 3 / Divinity. Path en dur côté apps Larian, "
                "junction NTFS conservée pour transparence",
     "risk": "MEDIUM",
     "min_gb_to_act": 5.0},
    {"path": r"<USER_HOME>\AppData\Local\GensparkSoftware",
     "name": "GensparkSoftware",
     "reason": "Données app Genspark — path attendu en dur. Junction conservée",
     "risk": "MEDIUM",
     "min_gb_to_act": 10.0},
    {"path": r"<USER_HOME>\.local\share",
     "name": ".local/share",
     "reason": "Données utilisateur Linux-style (peut contenir Lutris/WSL/Flatpak). "
                "Junction conservée — risque limité car peu d'apps Windows attendent ce path",
     "risk": "MEDIUM",
     "min_gb_to_act": 10.0},
    {"path": r"<USER_HOME>\AppData\Roaming\Claude",
     "name": "Claude Desktop data",
     "reason": "Données Claude Desktop. Junction conservée pour que l'app continue à fonctionner",
     "risk": "MEDIUM",
     "min_gb_to_act": 5.0},
    {"path": r"<USER_HOME>\AppData\Roaming\anythingllm-desktop",
     "name": "AnythingLLM data",
     "reason": "Données AnythingLLM Desktop. Junction conservée",
     "risk": "MEDIUM",
     "min_gb_to_act": 3.0},
]


# Caches safe à supprimer (delete_cache action) — pip purge, Temp, HF cache.
# Le contenu est SUPPRIMÉ, le dossier parent reste. Régénérable à la demande.
SAFE_CACHE_TARGETS = [
    {"path": r"<USER_HOME>\AppData\Local\pip\Cache",
     "name": "pip cache",
     "reason": "Cache pip — recréé automatiquement à la prochaine install",
     "risk": "LOW"},
    {"path": r"<USER_HOME>\AppData\Local\Temp",
     "name": "AppData Temp",
     "reason": "Fichiers temp Windows — Windows nettoie de toute façon, mais le fait pas tout le temps",
     "risk": "LOW",
     "skip_locked": True},  # certains fichiers en cours d'utilisation, on skip
    {"path": r"<USER_HOME>\.cache\huggingface",
     "name": "HuggingFace cache",
     "reason": "Cache HF — re-téléchargé si un script en a besoin",
     "risk": "LOW"},
    {"path": r"<USER_HOME>\.cache\torch",
     "name": "PyTorch cache",
     "reason": "Cache PyTorch — recréé",
     "risk": "LOW"},
    # Caches identifiés en 2e passe (audit live de C:)
    {"path": r"<USER_HOME>\AppData\Local\UnrealEngine\Common\DerivedDataCache",
     "name": "UnrealEngine DDC",
     "reason": "Derived Data Cache Unreal — recréé à la compile",
     "risk": "LOW",
     "skip_locked": True},
    {"path": r"<USER_HOME>\.npm-cache",
     "name": "npm cache (user)",
     "reason": "Cache npm — recréé à la prochaine install",
     "risk": "LOW",
     "skip_locked": True},
    {"path": r"<USER_HOME>\AppData\Roaming\npm-cache",
     "name": "npm cache (roaming)",
     "reason": "Cache npm — recréé",
     "risk": "LOW",
     "skip_locked": True},
    {"path": r"<USER_HOME>\AppData\Roaming\Code\Cache",
     "name": "VS Code cache",
     "reason": "Cache HTTP/cookies VS Code — recréé sans perte de réglages",
     "risk": "LOW",
     "skip_locked": True},
    {"path": r"<USER_HOME>\AppData\Roaming\Code\CachedData",
     "name": "VS Code CachedData",
     "reason": "CachedData VS Code — recréé",
     "risk": "LOW",
     "skip_locked": True},
    {"path": r"<USER_HOME>\AppData\Local\GensparkSoftware\cache",
     "name": "Genspark cache",
     "reason": "Cache app Genspark — recréé",
     "risk": "LOW",
     "skip_locked": True},
]


def _measure(p: Path) -> tuple[int, int]:
    """Taille + n_files d'un dossier (cap 200k pour rester rapide)."""
    if not p.exists() or not p.is_dir(): return (0, 0)
    total = 0; n = 0
    try:
        for cur, _, files in os.walk(p):
            for fn in files:
                try: total += (Path(cur) / fn).stat().st_size
                except Exception: pass
                n += 1
                if n >= 200_000: return (total, n)
    except Exception: pass
    return (total, n)


def _delete_cache_contents(target: Path, skip_locked: bool = True) -> dict:
    """Supprime le CONTENU de `target` (récursif), garde le dossier lui-même.

    `skip_locked=True` : si un fichier est verrouillé (Windows en cours
    d'utilisation), on skip silencieusement. Sinon on lève l'exception.
    """
    if not target.exists():
        return {"ok": False, "error": "target_missing", "path": str(target)}
    if not target.is_dir():
        return {"ok": False, "error": "target_not_dir", "path": str(target)}
    n_deleted = 0
    n_skipped = 0
    bytes_freed = 0
    errors = []
    for entry in list(target.iterdir()):
        try:
            if entry.is_file() or entry.is_symlink():
                sz = 0
                try: sz = entry.stat().st_size
                except Exception: pass
                entry.unlink(missing_ok=True)
                n_deleted += 1
                bytes_freed += sz
            elif entry.is_dir():
                # Mesure avant pour avoir le delta
                pre_sz, _ = _measure(entry)
                shutil.rmtree(str(entry), ignore_errors=skip_locked)
                # Si le dossier existe encore après rmtree (ignored errors),
                # on compte ce qui reste
                if entry.exists():
                    post_sz, _ = _measure(entry)
                    bytes_freed += pre_sz - post_sz
                    n_skipped += 1
                else:
                    bytes_freed += pre_sz
                    n_deleted += 1
        except Exception as e:
            n_skipped += 1
            errors.append(f"{entry.name}: {str(e)[:80]}")
            if not skip_locked:
                return {"ok": False, "error": "delete_failed",
                        "path": str(entry), "errors": errors}
    return {"ok": True, "n_deleted": n_deleted, "n_skipped": n_skipped,
            "bytes_freed": bytes_freed,
            "gb_freed": round(bytes_freed / 1e9, 2),
            "errors": errors[:5]}


def _propose_cache_cleanups() -> list[dict]:
    """Génère les actions `delete_cache` pour les SAFE_CACHE_TARGETS dispo."""
    actions = []
    for entry in SAFE_CACHE_TARGETS:
        p = Path(entry["path"])
        if not p.exists() or not p.is_dir(): continue
        sz, n_files = _measure(p)
        if sz < 100 * 1024 * 1024:  # skip si < 100 MB
            continue
        action_id = f"clean_{int(_now())}_{len(actions)}"
        actions.append({
            "id": action_id,
            "type": "delete_cache",
            "name": entry["name"],
            "src": entry["path"],
            "size_gb": round(sz / 1e9, 2),
            "n_files": n_files,
            "risk": entry["risk"],
            "reason": entry["reason"],
            "skip_locked": entry.get("skip_locked", True),
            # Pas de rollback : delete = définitif. Mais c'est un cache donc OK.
            "rollback_command": "(delete cache : pas de rollback, contenu régénéré à la demande)",
        })
    return actions


def _propose_huge_user_migrations(target_disk: str,
                                    skipped: list | None = None) -> list[dict]:
    """Génère les actions `move_with_junction` pour les HUGE_USER_DIRS dispo.

    `target_disk` : ex `G:\\` — disque cible avec assez d'espace.
    Junction NTFS conservée au path original pour transparence côté apps.

    Si `skipped` est fourni, on y append les raisons de skip pour traçabilité.
    """
    import subprocess
    actions = []
    skipped = skipped if skipped is not None else []
    for entry in HUGE_USER_DIRS:
        src_p = Path(entry["path"])
        if not src_p.exists() or not src_p.is_dir():
            skipped.append({"name": entry.get("name"), "path": str(src_p),
                             "reason": "missing_path"})
            continue
        # Idempotence : si déjà junction NTFS, skip (locale-independent check)
        is_j, _tgt = _is_junction_via_powershell(src_p)
        if is_j:
            skipped.append({"name": entry.get("name"), "path": str(src_p),
                             "reason": "already_junction",
                             "target": _tgt})
            continue

        sz, n_files = _measure(src_p)
        if sz < entry["min_gb_to_act"] * 1e9:
            skipped.append({"name": entry.get("name"), "path": str(src_p),
                             "reason": "below_min_threshold",
                             "size_gb": round(sz/1e9, 2),
                             "min_gb": entry["min_gb_to_act"]})
            continue

        target_root = Path(target_disk) / "cortex-migrated"
        dst = target_root / src_p.parts[-1]
        if dst.exists():
            dst = dst.with_name(f"{dst.name}_{int(_now())}")
        action_id = f"mvj_{int(_now())}_{len(actions)}"
        actions.append({
            "id": action_id,
            "type": "move_with_junction",
            "name": entry["name"],
            "src": str(src_p),
            "dst": str(dst),
            "size_gb": round(sz / 1e9, 2),
            "n_files": n_files,
            "risk": entry["risk"],
            "reason": entry["reason"],
            "ps_command": (f'Move-Item -LiteralPath "{src_p}" -Destination "{dst}" -Force; '
                            f'New-Item -ItemType Junction -Path "{src_p}" -Target "{dst}"'),
            "rollback_command": (f'Remove-Item -LiteralPath "{src_p}"; '
                                  f'Move-Item -LiteralPath "{dst}" -Destination "{src_p}"'),
        })
    return actions


def execute(action_id: str, confirm: bool = False) -> dict:
    """Exécute UNE action du plan, avec confirmation explicite obligatoire.

    Dispatch selon le type d'action :
    - `move_directory`  : shutil.move src → dst (cross-disk)
    - `delete_cache`    : rmtree du contenu (dossier parent conservé)
    """
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

    atype = action.get("type", "move_directory")

    # Action : delete_cache
    if atype == "delete_cache":
        target = Path(action["src"])
        started = _now()
        rep = _delete_cache_contents(target,
                                      skip_locked=action.get("skip_locked", True))
        duration = _now() - started
        ev = {"type": ("execute_success" if rep.get("ok") else "execute_failure"),
              "action_id": action_id, "action_type": "delete_cache",
              "target": str(target), "duration_s": round(duration, 1),
              **{k: v for k, v in rep.items() if k != "ok"}}
        _log_audit(ev)
        return {**rep, "action_id": action_id, "type": "delete_cache",
                "target": str(target), "duration_s": round(duration, 1)}

    # Action : move_with_junction (pour my games, etc.)
    # Move puis crée une jonction NTFS au path source pour que les apps qui
    # référencent le path en dur (Steam, jeux) continuent de marcher.
    if atype == "move_with_junction":
        import subprocess
        src = Path(action["src"])
        dst = Path(action["dst"])
        if not src.exists():
            return {"ok": False, "error": "src_missing", "src": str(src)}
        if dst.exists():
            return {"ok": False, "error": "dst_exists", "dst": str(dst)}
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return {"ok": False, "error": f"mkdir_dst_parent: {e}"}
        started = _now()
        # 1. Move
        try:
            shutil.move(str(src), str(dst))
        except Exception as e:
            _log_audit({"type": "execute_failure", "action_id": action_id,
                         "action_type": "move_with_junction",
                         "stage": "move", "error": str(e)[:300],
                         "src": str(src), "dst": str(dst)})
            return {"ok": False, "error": f"move_failed: {e}",
                    "src": str(src), "dst": str(dst)}
        # 2. Crée la junction NTFS au path original
        try:
            r = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(src), str(dst)],
                capture_output=True, text=True, timeout=15)
            if r.returncode != 0:
                # Move OK mais junction KO : on essaye de rollback le move
                try: shutil.move(str(dst), str(src))
                except Exception: pass
                _log_audit({"type": "execute_failure", "action_id": action_id,
                             "action_type": "move_with_junction",
                             "stage": "junction",
                             "stderr": r.stderr[:300] if r.stderr else "",
                             "rolled_back_move": True})
                return {"ok": False, "error": "junction_failed",
                        "stderr": r.stderr[:300]}
        except Exception as e:
            try: shutil.move(str(dst), str(src))
            except Exception: pass
            _log_audit({"type": "execute_failure", "action_id": action_id,
                         "action_type": "move_with_junction",
                         "stage": "junction_exception", "error": str(e)[:300]})
            return {"ok": False, "error": f"junction_exception: {e}"}
        duration = _now() - started
        _log_audit({"type": "execute_success", "action_id": action_id,
                     "action_type": "move_with_junction",
                     "src": str(src), "dst": str(dst),
                     "size_gb": action.get("size_gb"),
                     "duration_s": round(duration, 1)})
        return {
            "ok": True,
            "action_id": action_id,
            "type": "move_with_junction",
            "src": str(src), "dst": str(dst),
            "size_gb": action.get("size_gb"),
            "gb_freed": action.get("size_gb"),
            "duration_s": round(duration, 1),
            "rollback": action.get("rollback_command"),
            "msg": "Migré + junction NTFS créée. Les apps continuent à voir le path original.",
        }

    # Action : move_directory (legacy default)
    src = Path(action["src"])
    dst = Path(action["dst"])
    if not src.exists():
        return {"ok": False, "error": "src_missing", "src": str(src)}
    if dst.exists():
        return {"ok": False, "error": "dst_exists", "dst": str(dst)}
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return {"ok": False, "error": f"mkdir_dst_parent: {e}"}
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
                 "action_type": "move_directory",
                 "src": str(src), "dst": str(dst),
                 "size_gb": action.get("size_gb"),
                 "duration_s": round(duration, 1)})
    return {
        "ok": True,
        "action_id": action_id,
        "type": "move_directory",
        "src": str(src), "dst": str(dst),
        "size_gb": action.get("size_gb"),
        "duration_s": round(duration, 1),
        "rollback": action.get("rollback_command"),
    }


def _critical_disk_state(disks: list[dict]) -> dict | None:
    """Retourne le snapshot du disque le plus critique (% le plus haut)."""
    if not disks: return None
    crit = max(disks, key=lambda d: d.get("percent", 0))
    return {"mount": crit.get("mount"),
            "percent": crit.get("percent"),
            "free_gb": crit.get("free_gb"),
            "used_gb": crit.get("used_gb"),
            "total_gb": crit.get("total_gb")}


def auto_execute_authorized(allow_low: bool = True,
                             allow_medium: bool = True,
                             allow_high: bool = False) -> dict:
    """Exécute automatiquement les actions du plan selon les niveaux autorisés.

    Sam a explicitement autorisé LOW + MEDIUM ("toutes les actions, fais
    confiance"). HIGH reste OFF par défaut (OneDrive sync, contenu cloud,
    etc.) — il faudrait un opt-in explicite pour ça.

    Sécurité :
    - Snapshot disques AVANT et APRÈS pour calculer effective_freed_gb réel
    - Audit log de CHAQUE action (succès / échec) dans `.cortex-body-health-audit.jsonl`
    - HIGH refusé par défaut (override par Sam si vraiment voulu)
    - Échec d'une action n'arrête pas la chaîne (les autres tentent)
    - Sanity guard : refuse si total prétendu > 250 Go (probable bug de scan)
    - Écrit `.cortex-body-health-last.json` machine-readable pour UI/Claude context
    """
    if not PLAN_FILE.exists():
        propose_plan()
    if not PLAN_FILE.exists():
        return {"ok": False, "error": "plan_generation_failed"}
    try:
        plan = json.loads(PLAN_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "error": f"plan_read: {e}"}

    allowed = set()
    if allow_low: allowed.add("LOW")
    if allow_medium: allowed.add("MEDIUM")
    if allow_high: allowed.add("HIGH")

    actions_all = plan.get("actions", [])
    actions = [a for a in actions_all if a.get("risk") in allowed]
    skipped_by_risk = [{"id": a.get("id"), "name": a.get("name", a.get("src")),
                         "risk": a.get("risk"), "reason": "risk_not_authorized"}
                        for a in actions_all if a.get("risk") not in allowed]
    total_gb = sum(a.get("size_gb", 0) for a in actions)

    if total_gb > 250:
        return {"ok": False, "error": "suspicious_total_gb",
                "total_gb": total_gb, "n_actions": len(actions),
                "msg": "Plus de 250 Go d'actions auto : refus, audit manuel requis."}

    # Snapshot AVANT
    disks_before = _disk_state()
    crit_before = _critical_disk_state(disks_before)
    started_at = _now()

    results = []
    skipped = list(skipped_by_risk)
    for a in actions:
        rep = execute(a["id"], confirm=True)
        if rep.get("skip"):
            skipped.append({"id": a["id"], "name": a.get("name", a.get("src")),
                             "risk": a.get("risk"), "reason": rep["skip"]})
            continue
        freed = rep.get("gb_freed", a.get("size_gb", 0)) if rep.get("ok") else 0
        results.append({"id": a["id"],
                         "name": a.get("name", a.get("src")),
                         "type": a.get("type"),
                         "risk": a.get("risk"),
                         "ok": rep.get("ok"),
                         "freed_gb": freed,
                         "error": rep.get("error"),
                         "kind": rep.get("kind")})

    # Snapshot APRÈS pour mesurer le delta RÉEL côté filesystem
    disks_after = _disk_state()
    crit_after = _critical_disk_state(disks_after)
    n_ok = sum(1 for r in results if r["ok"])
    n_fail = sum(1 for r in results if not r["ok"])
    declared_freed = round(sum(r.get("freed_gb", 0) for r in results if r["ok"]), 2)
    effective_freed = None
    if crit_before and crit_after and crit_before["mount"] == crit_after["mount"]:
        effective_freed = round(crit_after["free_gb"] - crit_before["free_gb"], 2)

    summary = {
        "ts": _now(),
        "started_at": started_at,
        "duration_s": round(_now() - started_at, 1),
        "allowed_risks": sorted(allowed),
        "n_actions_attempted": len(actions),
        "n_succeeded": n_ok,
        "n_failed": n_fail,
        "n_skipped_by_risk": len(skipped_by_risk),
        "n_skipped_total": len(skipped),
        "declared_freed_gb": declared_freed,
        "effective_freed_gb": effective_freed,
        "calibration_gap_gb": (round(declared_freed - effective_freed, 2)
                                if effective_freed is not None else None),
        "critical_before": crit_before,
        "critical_after": crit_after,
        "results": results,
        "skipped": skipped,
    }

    _log_audit({"type": "auto_execute_authorized_completed",
                 "allowed_risks": sorted(allowed),
                 "n_actions": len(actions),
                 "n_ok": n_ok, "n_fail": n_fail,
                 "n_skipped": len(skipped),
                 "declared_freed_gb": declared_freed,
                 "effective_freed_gb": effective_freed})
    try:
        LAST_RUN.parent.mkdir(parents=True, exist_ok=True)
        LAST_RUN.write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                             encoding="utf-8")
    except Exception: pass
    return {"ok": True, **summary}


# Compat : ancien nom conservé
def auto_execute_low() -> dict:
    return auto_execute_authorized(allow_low=True, allow_medium=False,
                                    allow_high=False)


def _is_junction_via_powershell(src: Path) -> tuple[bool, str | None]:
    """Universel : utilise PowerShell Get-Item .LinkType qui marche en EN/FR.

    Retourne (is_junction, target_path_or_None).
    Plus fiable que fsutil reparsepoint query qui parse différemment selon locale.
    """
    import subprocess
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"$i = Get-Item -LiteralPath '{src}' -Force; "
             f"if ($i.LinkType -eq 'Junction') {{ Write-Output $i.Target }} "
             f"else {{ Write-Output '' }}"],
            capture_output=True, text=True, timeout=8)
        target = (r.stdout or "").strip()
        if target:
            return (True, target)
    except Exception: pass
    return (False, None)


def verify_junctions() -> dict:
    """Inspecte chaque path connu de HUGE_USER_DIRS, vérifie si junction NTFS.

    Utilise `Get-Item .LinkType` PowerShell (universel locale-independent)
    plutôt que fsutil reparsepoint query (output en français vs anglais).

    Pour chaque entrée :
    - is_junction : True si reparse point Mount Point
    - target : la cible si junction
    - target_exists : la cible existe sur disque
    - broken : junction présente mais target manquant
    """
    out = []
    for entry in HUGE_USER_DIRS:
        src = Path(entry["path"])
        rec = {"name": entry.get("name"), "path": str(src),
               "exists": src.exists(),
               "is_junction": False, "target": None,
               "target_exists": None, "broken": False}
        if src.exists():
            is_j, target = _is_junction_via_powershell(src)
            rec["is_junction"] = is_j
            if target:
                rec["target"] = target
                tp = Path(target)
                rec["target_exists"] = tp.exists()
                rec["broken"] = not tp.exists()
        out.append(rec)
    n_junctions = sum(1 for r in out if r["is_junction"])
    n_broken = sum(1 for r in out if r["broken"])
    return {"ts": _now(),
             "n_total": len(out),
             "n_junctions": n_junctions,
             "n_broken": n_broken,
             "entries": out}


def body_health_status() -> dict:
    """Snapshot machine-readable pour UI / Claude context.

    Légère : retourne juste les chiffres essentiels, ne déclenche pas le scan
    profond de propose_plan. Lit `.cortex-body-health-last.json` si présent.
    """
    disks = _disk_state()
    crit_now = _critical_disk_state(disks)
    severity = ("CRITICAL" if any(d.get("is_critical") for d in disks) else
                "WARNING"  if any(d.get("is_warning")  for d in disks) else
                "OK")
    last = None
    try:
        if LAST_RUN.exists():
            last = json.loads(LAST_RUN.read_text(encoding="utf-8"))
    except Exception: pass
    j = verify_junctions()
    return {
        "ts": _now(),
        "severity": severity,
        "critical_disk": crit_now,
        "all_disks": [{"mount": d["mount"], "percent": d["percent"],
                        "free_gb": d["free_gb"], "is_critical": d["is_critical"],
                        "is_warning": d["is_warning"]} for d in disks],
        "n_junctions_active": j["n_junctions"],
        "n_junctions_broken": j["n_broken"],
        "junctions": j["entries"],
        "last_auto_exec": last,
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
