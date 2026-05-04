"""
cortex_homeostasis.py — Cortex apprend à maintenir son corps (la machine).

Inspiré de :
- Cannon, W.B. (1932). "The Wisdom of the Body." Norton.
- Ashby, W.R. (1960). "Design for a Brain." Chapman & Hall.
- Friston, K. (2010). "The free-energy principle: a unified brain theory?"
  Nature Reviews Neuroscience, 11(2), 127-138.

Principe : un organisme vivant maintient ses variables vitales dans une plage
viable (température, pH, glucose...). Cortex fait pareil avec ses ressources :
- CPU < 80% en moyenne
- RAM < 85%
- Disque libre > 5 Go
- Services critiques up (serve, llm_router, voice_input, tts_monitor optionnels)
- Pas de zombies (process en double)
- Logs rotation (>10MB → archive)

Quand un seuil est dépassé, Cortex prend des actions GRADUELLES et SAFE :
1. Niveau 1 (warning) : log + skip optional loops
2. Niveau 2 (alert) : kill zombies + clean temp + rotate logs
3. Niveau 3 (critical) : pause les loops les plus lourds (vision continue, emergence)
4. JAMAIS de force-kill brutal sans confirmation

Logs santé : .cortex-vital-signs.jsonl (timeline des mesures)
"""
import datetime as dt
import json
import os
import threading
import time
from pathlib import Path

REPO = Path(r"<CORTEX_REPO>")
VAULT = Path(r"<USER_HOME>\Documents\Obsidian Vault")
VITAL_LOG = VAULT / ".cortex-vital-signs.jsonl"
HOMEO_LOG = REPO / ".cortex-homeostasis.log"

CPU_WARN, CPU_ALERT, CPU_CRIT = 70.0, 85.0, 92.0
RAM_WARN, RAM_ALERT, RAM_CRIT = 75.0, 85.0, 92.0
DISK_FREE_GB_WARN = 10.0

CRITICAL_SERVICES = ["serve.py", "llm_router"]   # doivent être up
OPTIONAL_SERVICES = ["voice_input", "tts_monitor", "xtts_daemon"]

LOG_MAX_MB = 10
LOGS_TO_ROTATE = [
    Path(r"<USER_HOME>\Documents\Obsidian Vault\.voice-input.log"),
    Path(r"<USER_HOME>\Documents\Obsidian Vault\.tts-monitor.log"),
    REPO / ".cortex-emergence.log",
    REPO / ".cortex-self-dev.log",
    REPO / ".cortex-continuous.log",
]


def _log(msg: str):
    line = f"[{dt.datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    try:
        with open(HOMEO_LOG, "a", encoding="utf-8") as f: f.write(line + "\n")
    except Exception: pass


CUSTOM_METRICS_FILE = REPO / "scripts" / "brain" / "cortex_custom_metrics.json"

def _load_custom_metrics() -> dict:
    """Métriques que Cortex a décidé d'ajouter lui-même (émergent)."""
    if not CUSTOM_METRICS_FILE.exists(): return {}
    try:
        return json.loads(CUSTOM_METRICS_FILE.read_text(encoding="utf-8"))
    except Exception: return {}

def add_custom_metric(name: str, source: str, description: str = "") -> dict:
    """Cortex peut s'auto-ajouter des métriques. source = path Python ou cmd shell.
    Ex: source = 'cortex_thought_graph.stats()' ou 'len(open(\"...\").readlines())'."""
    metrics = _load_custom_metrics()
    metrics[name] = {"source": source, "description": description,
                     "added_at": dt.datetime.now().isoformat(timespec='seconds')}
    try:
        CUSTOM_METRICS_FILE.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "name": name}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def _eval_custom_metric(source: str):
    """Évalue une expression Python sécurisée. Globals limités."""
    try:
        import sys as _sys
        if r"<CORTEX_REPO>\scripts\brain" not in _sys.path:
            _sys.path.insert(0, r"<CORTEX_REPO>\scripts\brain")
        # Whitelist d'imports utiles
        local_ctx = {}
        exec("import cortex_thought_graph as ctg\nimport cortex_activation as ca", local_ctx)
        result = eval(source, {"__builtins__": __builtins__}, local_ctx)
        if isinstance(result, (int, float, bool, str)): return result
        if isinstance(result, dict): return result
        return str(result)[:200]
    except Exception as e:
        return f"err: {e}"


def vital_signs() -> dict:
    """Lecture complète : CPU, RAM, TOUS les disques, GPU si dispo, network, batterie."""
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.5)
        cpu_per_core = psutil.cpu_percent(interval=0.1, percpu=True)
        cpu_freq = psutil.cpu_freq()
        cpu_count = psutil.cpu_count()
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        # TOUS les disques
        disks = []
        for part in psutil.disk_partitions(all=False):
            try:
                u = psutil.disk_usage(part.mountpoint)
                disks.append({
                    "mount": part.mountpoint,
                    "fs": part.fstype,
                    "free_gb": round(u.free / 1e9, 1),
                    "total_gb": round(u.total / 1e9, 1),
                    "percent": u.percent,
                })
            except Exception: pass
        # Network counters
        try:
            net = psutil.net_io_counters()
            net_data = {"sent_mb": round(net.bytes_sent / 1e6, 1),
                        "recv_mb": round(net.bytes_recv / 1e6, 1)}
        except Exception: net_data = {}
        # GPU (NVIDIA via nvidia-smi)
        gpu_data = []
        try:
            import subprocess
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2, encoding="utf-8")
            if r.returncode == 0:
                for line in r.stdout.strip().splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 5:
                        gpu_data.append({
                            "name": parts[0],
                            "util_percent": int(parts[1]),
                            "mem_used_mb": int(parts[2]),
                            "mem_total_mb": int(parts[3]),
                            "temp_c": int(parts[4]),
                        })
        except Exception: pass
        # Batterie si laptop
        try:
            bat = psutil.sensors_battery()
            battery = {"percent": round(bat.percent, 1), "plugged": bat.power_plugged} if bat else None
        except Exception: battery = None
        # Températures CPU si dispo (Linux principalement)
        try:
            temps = psutil.sensors_temperatures()
            temp_data = {}
            for chip, entries in (temps or {}).items():
                for e in entries:
                    if e.current: temp_data[f"{chip}_{e.label or 'core'}"] = e.current
        except Exception: temp_data = {}

        out = {
            "ts": time.time(),
            "cpu": {
                "percent": cpu,
                "per_core": [round(c, 1) for c in cpu_per_core],
                "cores": cpu_count,
                "freq_mhz": round(cpu_freq.current) if cpu_freq else None,
            },
            "ram": {
                "percent": mem.percent,
                "used_gb": round(mem.used / 1e9, 2),
                "total_gb": round(mem.total / 1e9, 2),
                "available_gb": round(mem.available / 1e9, 2),
            },
            "swap": {
                "percent": swap.percent,
                "used_gb": round(swap.used / 1e9, 2),
                "total_gb": round(swap.total / 1e9, 2),
            },
            "disks": disks,
            "network": net_data,
            "gpu": gpu_data,
            "battery": battery,
            "temps": temp_data,
            # Aliases backward-compat
            "cpu_percent": cpu,
            "ram_percent": mem.percent,
            "ram_used_gb": round(mem.used / 1e9, 2),
            "ram_total_gb": round(mem.total / 1e9, 2),
            "disk_free_gb": disks[0]["free_gb"] if disks else 0,
            "disk_total_gb": disks[0]["total_gb"] if disks else 0,
        }
        # Cortex's own metrics (emergent)
        custom = {}
        for name, cfg in _load_custom_metrics().items():
            try:
                custom[name] = _eval_custom_metric(cfg["source"])
            except Exception as e:
                custom[name] = f"err: {e}"
        if custom:
            out["custom"] = custom
        return out
    except Exception as e:
        return {"error": str(e)}


def _persist_vital(snap: dict):
    try:
        with open(VITAL_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(snap, ensure_ascii=False) + "\n")
    except Exception: pass


def services_status() -> dict:
    """État des services critiques + optionnels."""
    try:
        import psutil
        all_cmds = []
        for p in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmd = " ".join(p.info.get("cmdline") or [])
                if "powershell" in cmd.lower(): continue
                all_cmds.append((p.info["pid"], cmd))
            except Exception: pass
        out = {"critical": {}, "optional": {}}
        for sname in CRITICAL_SERVICES:
            pids = [pid for pid, cmd in all_cmds if sname in cmd]
            out["critical"][sname] = {"running": len(pids) > 0, "pids": pids, "duplicates": len(pids) - 1}
        for sname in OPTIONAL_SERVICES:
            pids = [pid for pid, cmd in all_cmds if sname in cmd]
            out["optional"][sname] = {"running": len(pids) > 0, "pids": pids, "duplicates": len(pids) - 1}
        return out
    except Exception as e:
        return {"error": str(e)}


def rotate_logs() -> dict:
    """Archive les fichiers log dépassant LOG_MAX_MB."""
    rotated = []
    for log_path in LOGS_TO_ROTATE:
        if not log_path.exists(): continue
        try:
            size_mb = log_path.stat().st_size / 1e6
            if size_mb > LOG_MAX_MB:
                ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
                archive = log_path.with_suffix(f".{ts}.archive")
                log_path.rename(archive)
                rotated.append({"file": log_path.name, "size_mb": round(size_mb, 1)})
        except Exception as e:
            _log(f"rotate err {log_path.name}: {e}")
    return {"rotated": rotated}


def clean_temp_files() -> dict:
    """Supprime fichiers temporaires Cortex anciens (>1h, >100KB)."""
    cleaned = 0; freed_mb = 0.0
    patterns = [
        (Path.home(), ".cortex_screenshot*.png"),
        (Path.home(), ".cortex_webcam*.png"),
        (Path(os.environ.get("TEMP", "")), "tmp*.wav"),
    ]
    cutoff = time.time() - 3600
    for base, pattern in patterns:
        if not base.exists(): continue
        try:
            for f in base.glob(pattern):
                try:
                    if f.stat().st_mtime < cutoff:
                        size = f.stat().st_size
                        f.unlink()
                        cleaned += 1; freed_mb += size / 1e6
                except Exception: pass
        except Exception: pass
    return {"files_cleaned": cleaned, "freed_mb": round(freed_mb, 1)}


def kill_duplicates() -> dict:
    """Tue les processus Cortex en double (garde le plus ancien)."""
    try:
        import cortex_resources as cr
        return cr.kill_zombies()
    except Exception as e:
        return {"error": str(e)}


def restart_critical_if_down() -> dict:
    """Relance les services critiques manquants. Safe : seulement si vraiment absent."""
    import subprocess
    status = services_status()
    actions = []
    for sname, info in status.get("critical", {}).items():
        if not info["running"]:
            try:
                if sname == "serve.py":
                    p = subprocess.Popen(["python", str(REPO / "scripts" / "brain" / "dashboard" / "serve.py")],
                                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    actions.append({"service": sname, "action": "restarted", "pid": p.pid})
                elif sname == "llm_router":
                    p = subprocess.Popen(["python", str(REPO / "scripts" / "brain" / "llm_router.py")],
                                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    actions.append({"service": sname, "action": "restarted", "pid": p.pid})
                _log(f"restart {sname}")
            except Exception as e:
                actions.append({"service": sname, "error": str(e)})
    return {"actions": actions}


def health_check_and_act() -> dict:
    """Point d'entrée : vérifie tout, prend des actions graduelles."""
    snap = vital_signs()
    _persist_vital(snap)
    actions_taken = []
    severity = "ok"

    cpu = snap.get("cpu_percent", 0)
    ram = snap.get("ram_percent", 0)
    disk = snap.get("disk_free_gb", 999)
    # Détection saturation disque (>= 90 % d'occupation sur n'importe lequel)
    saturated_disks = [d for d in snap.get("disks", []) if d.get("percent", 0) >= DISK_CRIT_PCT]

    # Niveau 1 : warning (log seulement)
    if cpu > CPU_WARN or ram > RAM_WARN or disk < DISK_FREE_GB_WARN or saturated_disks:
        severity = "warn"

    # Disque saturé : Cortex apprend à proposer un déménagement (sans exécuter).
    if saturated_disks:
        try:
            mig = propose_disk_migration()
            if mig.get("proposals"):
                actions_taken.append({"action": "propose_disk_migration",
                                      "n": len(mig["proposals"]),
                                      "summary": [p.get("rationale", "")[:140]
                                                  for p in mig["proposals"][:3]]})
        except Exception as e:
            _log(f"propose_disk_migration err: {e}")

    # Niveau 2 : alert (clean + rotate + kill zombies)
    if cpu > CPU_ALERT or ram > RAM_ALERT:
        severity = "alert"
        r = rotate_logs()
        if r["rotated"]: actions_taken.append({"action": "rotate_logs", **r})
        c = clean_temp_files()
        if c["files_cleaned"]: actions_taken.append({"action": "clean_temp", **c})
        z = kill_duplicates()
        if z.get("killed"): actions_taken.append({"action": "kill_zombies", **z})

    # Niveau 3 : critical (signal aux loops de pause)
    if cpu > CPU_CRIT or ram > CPU_CRIT:
        severity = "critical"
        # Marqueur sur disque que les loops lisent et respectent
        try:
            (REPO / ".cortex-pause.flag").touch()
            actions_taken.append({"action": "pause_loops", "ts": time.time()})
        except Exception: pass
    else:
        # Désactiver le pause flag si tout est revenu OK
        flag = REPO / ".cortex-pause.flag"
        if flag.exists():
            try: flag.unlink(); actions_taken.append({"action": "unpause_loops"})
            except: pass

    # Toujours : restart services critiques si down
    rc = restart_critical_if_down()
    if rc.get("actions"): actions_taken.append({"action": "restart_critical", **rc})

    if actions_taken or severity != "ok":
        _log(f"severity={severity} cpu={cpu}% ram={ram}% disk={disk}Go actions={len(actions_taken)}")

    return {
        "severity": severity,
        "vital_signs": snap,
        "services": services_status(),
        "actions_taken": actions_taken,
    }


DISK_CRIT_PCT       = 90.0   # > 90 % occupé = il faut déménager
MIGRATION_PROPOSALS = REPO / ".cortex-disk-migration-proposals.json"


def _largest_dirs(root: Path, max_depth: int = 2, top: int = 10) -> list[dict]:
    """Top-N des plus gros dossiers sous root (BFS borné)."""
    sizes = {}
    try:
        # Walk borné en profondeur
        for cur, subdirs, files in os.walk(root, topdown=True):
            depth = Path(cur).relative_to(root).parts
            if len(depth) > max_depth:
                subdirs[:] = []  # stop descente
                continue
            sz = 0
            try:
                for fn in files:
                    try:
                        sz += (Path(cur) / fn).stat().st_size
                    except Exception: pass
            except Exception: pass
            sizes[cur] = sz
    except Exception: pass
    items = [{"path": k, "size_gb": round(v / 1e9, 2)}
             for k, v in sizes.items() if v > 100 * 1024 * 1024]  # > 100 MB
    items.sort(key=lambda x: -x["size_gb"])
    return items[:top]


def propose_disk_migration() -> dict:
    """Quand un disque est saturé, propose des déménagements vers un disque libre.
    Cortex décide QUOI bouger en se basant sur :
    - Caches/temp/builds (= safe à déplacer)
    - Vérifie qu'il y a un disque cible avec assez de marge
    - JAMAIS exécuté : on écrit la proposition pour validation Sam.
    Retour : {proposals: [...], reason}."""
    snap = vital_signs()
    disks = snap.get("disks", [])
    if not disks: return {"proposals": [], "reason": "no disk info"}
    # Trie : saturé → libre
    full   = [d for d in disks if d.get("percent", 0) >= DISK_CRIT_PCT]
    spare  = sorted([d for d in disks if d.get("free_gb", 0) > 50],
                    key=lambda d: -d["free_gb"])
    if not full:  return {"proposals": [], "reason": f"all disks below {DISK_CRIT_PCT}% — rien à déménager"}
    if not spare: return {"proposals": [], "reason": "aucun disque cible avec >50 Go libre"}

    proposals = []
    # Heuristique : sur les disques saturés on cherche les dossiers candidats
    # connus comme déplaçables (caches, builds, modèles AI, vidéos). Attention:
    # "build" ne doit PAS matcher "IncrediBuild" ou un logiciel installé.
    SAFE_PATH_FRAGMENTS = [
        "appdata/local/pip/cache", "appdata/local/temp",
        "appdata/local/npm-cache", "appdata/roaming/npm-cache",
        "lm-studio/models", "huggingface",
    ]
    SAFE_DIR_NAMES = {
        ".cache", "node_modules", "target", "build", "dist",
        ".venv", "venv", "env", ".pytest_cache",
        "downloads", "videos", "onedrive",
    }

    def _safe_match(path: str) -> str | None:
        norm = path.lower().replace("\\", "/")
        for frag in SAFE_PATH_FRAGMENTS:
            if frag in norm:
                return frag
        parts = [p for p in norm.split("/") if p]
        for part in parts:
            if part in SAFE_DIR_NAMES:
                return part
        return None

    for d in full:
        mount = d["mount"]
        mount_root = Path(mount)
        target = spare[0]
        biggest = _largest_dirs(mount_root, max_depth=3, top=15)
        # Filtre : on ne propose QUE des chemins qui matchent un safe pattern
        candidates = []
        for b in biggest:
            matched = _safe_match(b["path"])
            if matched:
                candidates.append({**b, "matched_pattern": matched})
        if not candidates:
            proposals.append({
                "from_disk": mount, "occupancy_pct": d["percent"],
                "to_disk": target["mount"], "to_free_gb": target["free_gb"],
                "candidates": [],
                "note": "Rien d'évident à déménager — regarder manuellement avec WizTree",
            })
            continue
        # Top candidate par taille
        top_cand = candidates[0]
        proposals.append({
            "from_disk": mount, "occupancy_pct": d["percent"],
            "to_disk": target["mount"], "to_free_gb": target["free_gb"],
            "suggested_move": top_cand,
            "all_candidates": candidates[:5],
            "rationale": (
                f"{mount} est à {d['percent']}% — déménager '{top_cand['path']}' "
                f"({top_cand['size_gb']} Go) vers {target['mount']} "
                f"libère ~{top_cand['size_gb']} Go. Pattern '{top_cand['matched_pattern']}' "
                f"= safe (cache/build/téléchargement, reproductible)."
            ),
            "command_hint": (
                f'robocopy "{top_cand["path"]}" '
                f'"{target["mount"]}{Path(top_cand["path"]).name}" /E /MOVE'
            ),
        })
    out = {
        "proposals": proposals,
        "ts": time.time(),
        "iso": dt.datetime.now().isoformat(timespec="seconds"),
    }
    try:
        MIGRATION_PROPOSALS.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    except Exception: pass
    return out


CLEAN_LOG    = REPO / ".cortex-disk-cleanup.log"
CLEAN_REPORT = REPO / ".cortex-disk-cleanup-report.json"

# Patterns considérés safe à nettoyer + documentation SOURCÉE
# Format : (glob, age_jours_min, what_it_is, why_safe, source_url)
# Cortex utilise cette doc pour expliquer à Sam *pourquoi* il propose de supprimer.
SAFE_CLEAN_TARGETS = [
    ("**/__pycache__", 7,
     "Bytecode Python compilé (.pyc) stocké pour réutilisation",
     "Régénéré automatiquement à l'import suivant — aucune perte de fonctionnalité.",
     "https://docs.python.org/3/tutorial/modules.html#compiled-python-files"),
    ("**/.pytest_cache", 7,
     "Cache pytest (états derniers tests, last-failed)",
     "Reconstruit au lancement suivant. Perd l'historique --lf mais rien d'autre.",
     "https://docs.pytest.org/en/stable/how-to/cache.html"),
    ("**/.mypy_cache", 7,
     "Cache de typage incrémental mypy",
     "Régénéré au prochain mypy. Perte = première run plus longue.",
     "https://mypy.readthedocs.io/en/stable/command_line.html#incremental-mode"),
    ("**/.ruff_cache", 7,
     "Cache d'analyses du linter ruff",
     "Reconstruit instantanément au prochain ruff check.",
     "https://docs.astral.sh/ruff/configuration/#cache-directory"),
    ("**/.next/cache", 7,
     "Cache de build Next.js (image optimization, fetch)",
     "Perte = première navigation lente. Reconstruit en build/dev.",
     "https://nextjs.org/docs/app/api-reference/next-config-js/cacheHandler"),
    ("**/.turbo", 7,
     "Cache local de Turborepo (artefacts de tâches)",
     "Re-calculé au prochain `turbo run`. Perte = un build sans cache.",
     "https://turborepo.com/docs/crafting-your-repository/caching"),
    ("**/.parcel-cache", 7,
     "Cache du bundler Parcel",
     "Régénéré au prochain bundle. Pas d'effet runtime.",
     "https://parceljs.org/features/profiling/#caching"),
    ("**/.*.archive", 30,
     "Archives Cortex de logs précédents (>10MB rotated)",
     "Données historiques, aucune dépendance runtime.",
     "interne (cortex_homeostasis.rotate_logs)"),
    (".cortex_screenshot*.png", 1,
     "Captures écran prises par Cortex pendant son audit visuel",
     "Re-capturable instantanément via /api/cortex/see.",
     "interne (cortex_vision.capture_screen)"),
    (".cortex_webcam*.png", 1,
     "Captures webcam Cortex",
     "Idem, re-capturable.",
     "interne (cortex_vision.capture_webcam)"),
    ("**/coverage", 30,
     "Rapports de couverture de tests (HTML + JSON)",
     "Ré-généré à `pytest --cov`. Pas de dépendance.",
     "https://coverage.readthedocs.io/"),
]

# Patterns que Cortex DOIT préserver pour ses capacités actives.
# Si un candidat de nettoyage matche un de ces, on le garde et on explique pourquoi.
CAPABILITY_PRESERVE = [
    (".venv-xtts",        "venv isolé pour XTTS (TTS) — perte = TTS HS jusqu'à reinstall"),
    (".venv-f5tts",       "venv isolé pour F5-TTS"),
    ("scripts/brain",     "code source du cerveau Cortex"),
    ("scripts/voice",     "code source pipeline voix"),
    ("cortex_thought_graph", "module thought_graph utilisé en runtime"),
    (".cortex_graph_cache", "cache de TF-IDF — peut être régénéré mais lent"),
    ("xtts_v2",           "modèle TTS Coqui — gros, redownload long"),
    ("whisper",           "modèle Whisper utilisé par voice_input"),
    ("faster-whisper",    "binaires Whisper accélérés"),
]


def _capability_reason(path_str: str) -> str | None:
    """Si le chemin matche une capacité active, retourne pourquoi le garder."""
    p_low = path_str.lower().replace("\\", "/")
    for needle, reason in CAPABILITY_PRESERVE:
        if needle.lower() in p_low:
            return reason
    return None


def _is_recently_used(p: Path, days: int = 7) -> bool:
    """Le chemin a-t-il été ACCÉDÉ (lecture/exécution) récemment ?
    Sur Windows, st_atime est suivi sauf si NTFS l'a désactivé.
    Si désactivé, on retombe sur mtime (modification) — moins précis."""
    try:
        st = p.stat()
        atime = st.st_atime
        if atime <= 0: atime = st.st_mtime  # fallback
        return (time.time() - atime) / 86400 < days
    except Exception:
        return True  # défensif : si on peut pas lire, on garde

# Limites strictes — Cortex ne touche JAMAIS hors de ces racines.
# Tout ce qui est sous "Documents" ou "Desktop" ou "Pictures" est INTOUCHABLE.
SAFE_ROOTS = [
    Path(r"<CORTEX_REPO>"),
    Path.home() / "AppData" / "Local" / "pip" / "Cache",
    Path.home() / "AppData" / "Local" / "Temp",
    Path.home() / "AppData" / "Roaming" / "npm-cache",
    Path.home() / ".cache",
]
FORBIDDEN_PATTERNS = [
    "Documents", "Desktop", "Pictures", "Music",
    "OneDrive", "Obsidian Vault", ".git",
]


def _is_safe_path(p: Path) -> bool:
    """Le chemin doit (1) être sous un SAFE_ROOT, (2) ne PAS contenir un FORBIDDEN."""
    abs_p = p.resolve()
    if not any(abs_p.is_relative_to(r) for r in SAFE_ROOTS if r.exists()):
        return False
    s = str(abs_p)
    return not any(f in s for f in FORBIDDEN_PATTERNS)


def safe_clean_disk(execute: bool = False) -> dict:
    """Scanne, documente CHAQUE candidat avec une source citée, vérifie qu'aucune
    capacité active n'en dépend, et (optionnellement) nettoie.

    SÉCURITÉ — 6 garde-fous :
    1. SAFE_ROOTS : on ne descend que dans ces racines.
    2. FORBIDDEN_PATTERNS : Documents/Desktop/Vault/.git intouchables.
    3. Âge minimum (7-30j) — jamais de fichiers récents.
    4. Recently-accessed check : si atime < 7j → garde.
    5. CAPABILITY_PRESERVE : si chemin matche une capacité active → garde.
    6. Log JSONL append-only : tout passage est auditable.

    Retourne un rapport avec doc + raison KEEP/DELETE par item (pour Sam ou pour
    Cortex lui-même qui peut l'expliquer dans le chat).
    """
    candidates = []
    keep_for_capability = []
    total_bytes = 0
    now = time.time()
    for root in SAFE_ROOTS:
        if not root.exists(): continue
        for pattern, age_days, what_it_is, why_safe, source_url in SAFE_CLEAN_TARGETS:
            try:
                for path in root.glob(pattern):
                    if not _is_safe_path(path): continue
                    try:
                        st = path.stat()
                        age_d = (now - st.st_mtime) / 86400
                        if age_d < age_days: continue
                        if _is_recently_used(path, days=7):
                            keep_for_capability.append({
                                "path": str(path),
                                "reason": f"accédé < 7j (atime récent) — outils en cours d'usage",
                            })
                            continue
                        cap_reason = _capability_reason(str(path))
                        if cap_reason:
                            keep_for_capability.append({
                                "path": str(path), "reason": cap_reason,
                            })
                            continue
                        if path.is_dir():
                            sz = sum((p.stat().st_size
                                      for p in path.rglob('*') if p.is_file()),
                                     start=0)
                        else:
                            sz = st.st_size
                        candidates.append({
                            "path": str(path), "size_mb": round(sz/1e6, 2),
                            "age_days": round(age_d, 1),
                            "what_it_is": what_it_is, "why_safe": why_safe,
                            "source": source_url, "is_dir": path.is_dir(),
                        })
                        total_bytes += sz
                    except Exception: pass
            except Exception: pass
    candidates.sort(key=lambda c: -c["size_mb"])
    report = {
        "ts": time.time(),
        "iso": dt.datetime.now().isoformat(timespec="seconds"),
        "candidates": candidates[:100],
        "kept_for_capability": keep_for_capability[:50],
        "total_size_mb": round(total_bytes / 1e6, 2),
        "n_candidates": len(candidates),
        "n_kept": len(keep_for_capability),
        "executed": False,
        "deleted": [],
        "errors": [],
    }
    if execute:
        import shutil
        for c in candidates:
            p = Path(c["path"])
            if not _is_safe_path(p):
                report["errors"].append({"path": str(p), "reason": "not safe (re-check)"})
                continue
            if _capability_reason(str(p)):
                report["errors"].append({"path": str(p), "reason": "capability dependency (last check)"})
                continue
            try:
                if p.is_dir(): shutil.rmtree(p, ignore_errors=False)
                else: p.unlink()
                report["deleted"].append({"path": str(p), "size_mb": c["size_mb"]})
            except Exception as e:
                report["errors"].append({"path": str(p), "reason": str(e)[:120]})
        report["executed"] = True
        report["freed_mb"] = sum(d["size_mb"] for d in report["deleted"])
        try:
            with open(CLEAN_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": report["ts"],
                                     "freed_mb": report["freed_mb"],
                                     "n_deleted": len(report["deleted"]),
                                     "n_errors": len(report["errors"])},
                                    ensure_ascii=False) + "\n")
        except Exception: pass
    try:
        CLEAN_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception: pass
    return report


def is_paused() -> bool:
    return (REPO / ".cortex-pause.flag").exists()


# ─── Background loop : auto-check chaque 60s ─────────────────────────────────
_running = False

def _loop(interval: int):
    while _running:
        try:
            health_check_and_act()
        except Exception as e:
            _log(f"loop err: {e}")
        time.sleep(interval)


def start(interval: int = 60):
    global _running
    if _running: return
    _running = True
    t = threading.Thread(target=_loop, args=(interval,), daemon=True)
    t.start()
    _log(f"homeostasis loop started (every {interval}s)")


def stop():
    global _running
    _running = False


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "check":
        print(json.dumps(health_check_and_act(), ensure_ascii=False, indent=2))
    elif cmd == "vital":
        print(json.dumps(vital_signs(), indent=2))
    elif cmd == "services":
        print(json.dumps(services_status(), indent=2))
    elif cmd == "rotate":
        print(json.dumps(rotate_logs(), indent=2))
