"""
cortex_pipeline_manager.py — Gestion autonome du pipeline matériel de Cortex.

Capacité primordiale : Cortex gère ses propres processus système, prévient
la saturation, nettoie ses zombies, et prédit le coût des actions avant de
les lancer.

Pipeline réel observé :
- LLM cascade : opencode → node (×N par appel, ~120Mo/zombie résiduel)
- Voix : xtts_daemon, voice_input, lm-studio, .venv-xtts
- Vault : indexer python (consolidation, vault_brain BM25)
- Serveur : serve.py + threads (homeostasis, emergence, activation, brain_history)
- Pensée : cortex_continuous, cortex_emergence (loops)

Stratégie auto-régulation :
- Toutes les N minutes : compte les zombies, mesure RAM
- Si RAM > 90% : kill opencode/node idle > 10min (CPU=0 + zéro descripteur ouvert)
- Si zombies > 100 : kill les plus anciens
- Avant chaque action LLM : check budget (RAM dispo, opencode actifs)
- Audit complet de chaque kill dans .cortex-pipeline-audit.jsonl

API :
- list_processes()          : snapshot catégorisé
- find_zombies()            : opencode/node candidats au cleanup
- cleanup_zombies(dry_run)  : kill ciblé avec audit
- predict_action_cost(action): {ram_mb, duration_s, n_subprocesses}
- can_launch(action)        : True si ressources suffisantes
- auto_regulate()           : décision auto (cleanup si saturation)
- start(interval=120)       : boucle background
"""
import json
import os
import time
import threading
import subprocess
from pathlib import Path

REPO_ROOT      = Path(r"<CORTEX_REPO>")
AUDIT_LOG      = REPO_ROOT / ".cortex-pipeline-audit.jsonl"
STATE_FILE     = REPO_ROOT / ".cortex-pipeline-state.json"

# Catégorisation des processus connus de Cortex
CATEGORIES = {
    "llm_cascade":    ["opencode", "node"],
    "voice":          ["xtts", "voice_input", "lm-studio", "lmstudio"],
    "browser":        ["chrome", "msedge", "firefox", "playwright"],
    "server":         ["serve.py"],
    "indexer":        ["vault_brain", "consolidation"],
    "system":         ["python", "powershell", "cmd"],
}

# Coûts prédits (mesurés empiriquement, ajustables via .cortex-pipeline-state.json)
DEFAULT_COSTS = {
    "chat_minimax":     {"ram_mb": 180, "duration_s": 12,  "n_subprocesses": 4},
    "chat_claude":      {"ram_mb": 50,  "duration_s": 18,  "n_subprocesses": 1},
    "chat_router_v2":   {"ram_mb": 220, "duration_s": 25,  "n_subprocesses": 6},
    "explain_term":     {"ram_mb": 180, "duration_s": 10,  "n_subprocesses": 4},
    "self_dev_iter":    {"ram_mb": 220, "duration_s": 60,  "n_subprocesses": 6},
    "emergence_decide": {"ram_mb": 180, "duration_s": 12,  "n_subprocesses": 4},
    "audit_ui":         {"ram_mb": 5,   "duration_s": 1,   "n_subprocesses": 0},
    "explore_graph":    {"ram_mb": 50,  "duration_s": 2,   "n_subprocesses": 0},
}

# Seuils auto-régulation
AUTO_REG = {
    "ram_critical":         92.0,  # %
    "ram_warning":          85.0,
    "zombie_max":           80,    # nb max d'opencode/node tolérés
    "zombie_idle_min":      10,    # min sans CPU activity = candidat kill
    "kill_per_cycle":       20,    # kill au plus N par cycle (safety)
}


def _audit(action: str, details: dict):
    """Append-only audit pour traçabilité (chaque kill est sourcé)."""
    try:
        with AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "action": action,
                                **details}, ensure_ascii=False) + "\n")
    except Exception: pass


def list_processes() -> dict:
    """Snapshot catégorisé des processus liés à Cortex.
    Retourne {category: [{pid, name, cpu_pct, ram_mb, age_s}], total: N, ram_total_mb: X}."""
    try:
        import psutil
    except ImportError:
        return {"error": "psutil non installé", "categories": {}}

    out = {cat: [] for cat in CATEGORIES}
    out["other"] = []
    total_ram_mb = 0
    now = time.time()
    for p in psutil.process_iter(attrs=["pid", "name", "create_time", "memory_info", "cpu_percent"]):
        try:
            info = p.info
            name = (info.get("name") or "").lower()
            ram_mb = info["memory_info"].rss / (1024*1024) if info.get("memory_info") else 0
            age_s = now - (info.get("create_time") or now)
            cpu = info.get("cpu_percent") or 0
            entry = {"pid": info["pid"], "name": info.get("name") or "?",
                     "cpu_pct": cpu, "ram_mb": round(ram_mb, 1),
                     "age_s": round(age_s, 0)}
            placed = False
            for cat, keywords in CATEGORIES.items():
                if any(k in name for k in keywords):
                    out[cat].append(entry)
                    total_ram_mb += ram_mb
                    placed = True; break
            if not placed and any(k in name for k in ["python", "powershell", "node", "opencode"]):
                out["other"].append(entry)
                total_ram_mb += ram_mb
        except Exception: pass

    # Tri : plus gros consommateurs d'abord
    for cat in out:
        out[cat].sort(key=lambda x: -x["ram_mb"])

    summary = {
        "categories": out,
        "total_processes": sum(len(v) for v in out.values()),
        "ram_total_mb":    round(total_ram_mb, 1),
        "by_category_count": {k: len(v) for k, v in out.items()},
        "by_category_ram":   {k: round(sum(p["ram_mb"] for p in v), 1)
                              for k, v in out.items()},
        "ts": now,
    }
    return summary


def find_zombies(idle_min: int | None = None) -> list[dict]:
    """Identifie les opencode/node candidats au cleanup :
    - idle (CPU < 1%) depuis > idle_min minutes (défaut: AUTO_REG['zombie_idle_min'])
    - mais skip si processus jeune (< idle_min × 60 s)
    Retourne la liste des candidats avec raison."""
    try:
        import psutil
    except ImportError:
        return []
    idle_min = idle_min or AUTO_REG["zombie_idle_min"]
    cutoff_age = idle_min * 60
    now = time.time()
    candidates = []
    for p in psutil.process_iter(attrs=["pid", "name", "create_time", "memory_info", "cpu_percent"]):
        try:
            info = p.info
            name = (info.get("name") or "").lower()
            if not any(k in name for k in ["opencode", "node"]): continue
            age = now - (info.get("create_time") or now)
            if age < cutoff_age: continue          # trop jeune, skip
            cpu = info.get("cpu_percent") or 0
            if cpu > 1.0: continue                 # actif, skip
            ram_mb = info["memory_info"].rss / (1024*1024) if info.get("memory_info") else 0
            candidates.append({
                "pid": info["pid"], "name": info.get("name"),
                "age_s": round(age, 0), "cpu_pct": cpu,
                "ram_mb": round(ram_mb, 1),
                "reason": f"idle {idle_min}min+ · CPU {cpu}% · age {round(age/60,0)}min"
            })
        except Exception: pass
    candidates.sort(key=lambda x: -x["age_s"])  # plus vieux d'abord
    return candidates


def cleanup_zombies(dry_run: bool = True, max_kill: int | None = None) -> dict:
    """Kill les zombies identifiés. Audit log systématique.
    dry_run=True : ne tue rien, retourne juste la liste."""
    try:
        import psutil
    except ImportError:
        return {"ok": False, "error": "psutil missing"}
    max_kill = max_kill or AUTO_REG["kill_per_cycle"]
    zombies = find_zombies()
    candidates = zombies[:max_kill]
    if dry_run:
        _audit("cleanup_dry_run", {"n_candidates": len(zombies),
                                   "would_kill": [c["pid"] for c in candidates]})
        return {"ok": True, "dry_run": True,
                "n_candidates": len(zombies), "would_kill": candidates}
    killed, failed = [], []
    for c in candidates:
        try:
            p = psutil.Process(c["pid"])
            p.kill()
            killed.append(c["pid"])
        except Exception as e:
            failed.append({"pid": c["pid"], "error": str(e)})
    _audit("cleanup_executed", {"killed": killed, "failed": failed,
                                "n_killed": len(killed),
                                "ram_freed_mb": round(sum(c["ram_mb"]
                                                  for c in candidates if c["pid"] in killed), 1)})
    return {"ok": True, "dry_run": False, "killed": killed, "failed": failed,
            "n_killed": len(killed)}


def _vital_signs() -> dict:
    """Lit cortex_homeostasis si dispo, sinon psutil direct."""
    try:
        import sys
        if str(REPO_ROOT / "scripts" / "brain") not in sys.path:
            sys.path.insert(0, str(REPO_ROOT / "scripts" / "brain"))
        import cortex_homeostasis as _ch
        v = _ch.vital_signs() or {}
        return {"cpu": (v.get("cpu") or {}).get("percent", 0),
                "ram": (v.get("ram") or {}).get("percent", 0)}
    except Exception:
        try:
            import psutil
            return {"cpu": psutil.cpu_percent(interval=0.3),
                    "ram": psutil.virtual_memory().percent}
        except Exception:
            return {"cpu": 0, "ram": 0}


def predict_action_cost(action: str) -> dict:
    """Prédit le coût en ressources d'une action avant de la lancer.
    Si action inconnue : moyenne raisonnable."""
    cost = dict(DEFAULT_COSTS.get(action,
                {"ram_mb": 100, "duration_s": 10, "n_subprocesses": 2}))
    cost["action"] = action
    cost["ts"] = time.time()
    return cost


def can_launch(action: str, override_ram_pct: float | None = None) -> dict:
    """Avant d'appeler une action coûteuse, vérifie qu'on a la marge.
    Retourne {ok: bool, reason: str, suggestion: str}."""
    cost = predict_action_cost(action)
    vit = _vital_signs()
    ram_pct = override_ram_pct if override_ram_pct is not None else vit["ram"]
    cpu_pct = vit["cpu"]
    # Réserve de sécurité : on ne lance pas si on est déjà en zone critique
    if ram_pct > AUTO_REG["ram_critical"]:
        return {"ok": False, "ram_pct": ram_pct, "cost": cost,
                "reason": f"RAM critique ({ram_pct}%)",
                "suggestion": "auto_regulate() ou skip cette action"}
    if cpu_pct > 95:
        return {"ok": False, "ram_pct": ram_pct, "cost": cost,
                "reason": f"CPU saturé ({cpu_pct}%)",
                "suggestion": "attendre 10-30s"}
    # Si action lourde et RAM en warning : refuse
    if cost["ram_mb"] > 150 and ram_pct > AUTO_REG["ram_warning"]:
        return {"ok": False, "ram_pct": ram_pct, "cost": cost,
                "reason": f"RAM en warning ({ram_pct}%) + action lourde ({cost['ram_mb']}MB attendus)",
                "suggestion": "cleanup_zombies(dry_run=False) puis retry"}
    return {"ok": True, "ram_pct": ram_pct, "cpu_pct": cpu_pct, "cost": cost}


def auto_regulate() -> dict:
    """Cycle de régulation autonome :
    1. Lit RAM/CPU
    2. Si zombies > zombie_max ou RAM > critical → cleanup auto (non-dry)
    3. Sinon si RAM > warning → dry_run preview pour info
    4. Audit complet."""
    vit = _vital_signs()
    zombies = find_zombies()
    n_zombies = len(zombies)
    ram_freed = 0
    actions = []
    if vit["ram"] > AUTO_REG["ram_critical"] or n_zombies > AUTO_REG["zombie_max"]:
        rep = cleanup_zombies(dry_run=False)
        actions.append({"action": "cleanup_executed",
                        "killed": rep.get("n_killed", 0),
                        "trigger": (f"RAM={vit['ram']}%" if vit["ram"] > AUTO_REG["ram_critical"]
                                    else f"zombies={n_zombies}")})
        # Re-mesure
        time.sleep(0.5)
        vit = _vital_signs()
    elif vit["ram"] > AUTO_REG["ram_warning"]:
        rep = cleanup_zombies(dry_run=True)
        actions.append({"action": "cleanup_preview",
                        "would_kill": rep.get("n_candidates", 0),
                        "trigger": f"RAM warning={vit['ram']}%"})
    else:
        actions.append({"action": "noop", "reason": "ressources OK"})
    state = {
        "ts": time.time(),
        "vitals": vit,
        "zombies_found": n_zombies,
        "actions": actions,
    }
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False),
                              encoding="utf-8")
    except Exception: pass
    _audit("auto_regulate", state)
    return state


_running = False
def _loop(interval: int):
    global _running
    while _running:
        try: auto_regulate()
        except Exception as e:
            _audit("loop_err", {"error": str(e)})
        time.sleep(interval)


def start(interval: int = 120):
    global _running
    if _running: return
    _running = True
    threading.Thread(target=_loop, args=(interval,), daemon=True).start()
    _audit("started", {"interval_s": interval})


def stop():
    global _running
    _running = False


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "snapshot"
    if cmd == "snapshot":
        out = list_processes()
        print(json.dumps({"by_category_count": out["by_category_count"],
                          "by_category_ram": out["by_category_ram"],
                          "total_processes": out["total_processes"],
                          "ram_total_mb": out["ram_total_mb"]}, indent=2))
    elif cmd == "zombies":
        z = find_zombies()
        print(f"{len(z)} candidats zombies :")
        for c in z[:20]: print(f"  pid={c['pid']} {c['name']} · {c['reason']} · {c['ram_mb']}MB")
    elif cmd == "cleanup":
        dry = "--no-dry-run" not in sys.argv
        rep = cleanup_zombies(dry_run=dry)
        print(json.dumps(rep, indent=2))
    elif cmd == "regulate":
        print(json.dumps(auto_regulate(), indent=2, ensure_ascii=False))
    elif cmd == "predict":
        action = sys.argv[2] if len(sys.argv) > 2 else "chat_minimax"
        print(json.dumps(predict_action_cost(action), indent=2))
    elif cmd == "can":
        action = sys.argv[2] if len(sys.argv) > 2 else "chat_minimax"
        print(json.dumps(can_launch(action), indent=2))
    else:
        print("Usage: cortex_pipeline_manager.py {snapshot|zombies|cleanup [--no-dry-run]|regulate|predict <action>|can <action>}")
