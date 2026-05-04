"""
cortex_game_autonomy_lab.py — Game IAG benchmark central pour Cortex.

Module-cadre qui orchestre les benchmarks d'autonomie sur jeux moddables.
X4 Foundations est le benchmark prioritaire (déjà branché via
cortex_x4_faction_lab.py). Les autres jeux (Factorio, RimWorld, Starfield)
sont déclarés mais en statut "planned" tant qu'aucun lab dédié n'est créé.

Échelle 7 niveaux (Game IAG) :
    0 — Jeu ignoré (aucun mod, aucune télémétrie)
    1 — Mod statique généré (XML/scripts) sans installation
    2 — Mod installé mais jamais lancé
    3 — Jeu lancé, télémétrie partielle visible
    4 — Faction/unité contrôlée + actions observées
    5 — Effets mesurables sur l'économie/score interne du jeu
    6 — Adaptation : Cortex ajuste sa stratégie selon les preuves
    7 — Dominance mesurée : Cortex surperforme un baseline humain/random

API publique :
    list_supported_games() → list[dict]
    create_game_profile(game) → dict
    create_game_iag_benchmark() → dict
    register_test_run(game, report) → dict
    compare_runs(game) → dict
    evaluate_dominance(game) → dict
    extract_transfer_lessons() → dict
    propose_next_game_task() → dict
    status() → dict
    self_test() → dict (sans toucher aux jeux réels)

Aucun "ça marche" gratuit — tous les statuts viennent de preuves dans
`examples/session-current/game_iag/`.
"""
from __future__ import annotations
import json
import time
from pathlib import Path

PAPERCLIP_ROOT = Path(r"<CORTEX_REPO>")
DOCS_ROOT = PAPERCLIP_ROOT / "docs" / "game-iag"
SESSION_ROOT = PAPERCLIP_ROOT / "examples" / "session-current" / "game_iag"
BENCHMARK_FILE = SESSION_ROOT / "game_iag_benchmark.json"
PROFILES_FILE = SESSION_ROOT / "game_profiles.json"
RUNS_FILE = SESSION_ROOT / "runs.jsonl"
LESSONS_FILE = SESSION_ROOT / "transfer_lessons.json"

# ---------- catalogue des jeux supportés ----------

SUPPORTED_GAMES = [
    {
        "game": "X4 Foundations",
        "priority": 1,
        "modding_type": ["xml", "mission_director", "extensions"],
        "automation_level": "local_assisted_to_autonomous",
        "allowed_modes": ["fair_play", "sandbox_modded", "cheat_debug"],
        "dominance_metrics": [
            "faction_economy_growth",
            "ships_built_per_hour",
            "stations_owned",
            "credits_balance_delta",
        ],
        "evidence_sources": ["logs", "save_files", "telemetry", "screenshots_optional"],
        "lab_module": "cortex_x4_faction_lab",
        "status": "active",
    },
    {
        "game": "Factorio",
        "priority": 2,
        "modding_type": ["lua", "mods", "data_extend"],
        "automation_level": "scripted_assisted",
        "allowed_modes": ["fair_play", "sandbox_modded", "creative"],
        "dominance_metrics": [
            "items_produced_per_minute",
            "research_completed",
            "biters_killed",
            "logistics_throughput",
        ],
        "evidence_sources": ["logs", "save_files", "stats_export"],
        "lab_module": None,
        "status": "planned",
    },
    {
        "game": "RimWorld",
        "priority": 3,
        "modding_type": ["xml", "csharp_assemblies", "harmony_patches"],
        "automation_level": "scripted_assisted",
        "allowed_modes": ["fair_play", "sandbox_modded", "dev_mode"],
        "dominance_metrics": [
            "colony_wealth",
            "colonists_alive",
            "raids_survived",
            "research_completed",
        ],
        "evidence_sources": ["logs", "save_files", "screenshots_optional"],
        "lab_module": None,
        "status": "planned",
    },
    {
        "game": "Starfield",
        "priority": 4,
        "modding_type": ["esm_esp", "papyrus_scripts", "creation_kit"],
        "automation_level": "scripted_assisted",
        "allowed_modes": ["fair_play", "sandbox_modded", "console_commands"],
        "dominance_metrics": [
            "credits_earned",
            "outposts_built",
            "ships_owned",
            "factions_completed",
        ],
        "evidence_sources": ["logs", "save_files", "screenshots_optional"],
        "lab_module": None,
        "status": "planned",
    },
]

# ---------- échelle 7 niveaux ----------

GAME_IAG_LEVELS = [
    {"level": 0, "name": "ignored",          "criterion": "Aucun mod, aucune télémétrie"},
    {"level": 1, "name": "mod_generated",     "criterion": "Mod statique XML/scripts généré (non installé)"},
    {"level": 2, "name": "mod_installed",     "criterion": "Mod installé dans le dossier extensions/mods du jeu"},
    {"level": 3, "name": "telemetry_seen",    "criterion": "Jeu lancé + marqueurs télémétrie visibles dans logs"},
    {"level": 4, "name": "unit_controlled",   "criterion": "Faction/unité spawnée et action observée"},
    {"level": 5, "name": "effect_measured",   "criterion": "Effet mesurable sur économie/score interne du jeu"},
    {"level": 6, "name": "strategy_adapted",  "criterion": "Cortex ajuste sa stratégie selon les preuves"},
    {"level": 7, "name": "dominance",         "criterion": "Surperformance vs baseline humain/random"},
]


def _ensure_dirs() -> None:
    SESSION_ROOT.mkdir(parents=True, exist_ok=True)
    DOCS_ROOT.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload) -> None:
    _ensure_dirs()
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def list_supported_games() -> list[dict]:
    return list(SUPPORTED_GAMES)


def create_game_profile(game: str) -> dict:
    """Crée/renvoie un profil pour un jeu donné. Persiste dans game_profiles.json."""
    target = next((g for g in SUPPORTED_GAMES if g["game"].lower() == game.lower()), None)
    if not target:
        return {"ok": False, "error": f"unsupported game: {game}",
                "supported": [g["game"] for g in SUPPORTED_GAMES]}
    profiles = _read_json(PROFILES_FILE, {"profiles": {}})
    profile = dict(target)
    profile["created_ts"] = time.time()
    profile["current_level"] = profile.get("current_level", 0)
    profiles["profiles"][target["game"]] = profile
    _write_json(PROFILES_FILE, profiles)
    return {"ok": True, "profile": profile}


def create_game_iag_benchmark() -> dict:
    """Initialise le fichier benchmark (idempotent)."""
    _ensure_dirs()
    payload = {
        "version": "0.1.0",
        "created_ts": time.time(),
        "levels": GAME_IAG_LEVELS,
        "games": [{"game": g["game"], "priority": g["priority"],
                   "current_level": 0, "evidence_count": 0,
                   "lab_module": g.get("lab_module"), "status": g["status"]}
                  for g in SUPPORTED_GAMES],
    }
    _write_json(BENCHMARK_FILE, payload)
    return {"ok": True, "path": str(BENCHMARK_FILE), "n_games": len(payload["games"])}


def register_test_run(game: str, report: dict) -> dict:
    """Append un rapport de cycle (X4, Factorio, ...) dans runs.jsonl + met à jour le niveau atteint."""
    _ensure_dirs()
    target = next((g for g in SUPPORTED_GAMES if g["game"].lower() == game.lower()), None)
    if not target:
        return {"ok": False, "error": f"unsupported game: {game}"}
    record = {
        "ts": time.time(),
        "game": target["game"],
        "report": report,
        "level_reached": _infer_level_from_report(report),
    }
    with RUNS_FILE.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(record, ensure_ascii=False) + "\n")
    _refresh_benchmark_levels()
    return {"ok": True, "record_ts": record["ts"], "level_reached": record["level_reached"]}


def _infer_level_from_report(report: dict) -> int:
    """Mappe un rapport (X4 ou autre) vers un niveau Game IAG honnête."""
    if not isinstance(report, dict):
        return 0
    flags = {k: bool(report.get(k)) for k in (
        "mod_generated", "static_validated", "installed", "launched",
        "telemetry_seen", "unit_controlled", "effect_measured",
        "strategy_adapted", "dominance_verified")}
    # mapping X4-style → niveaux
    if flags.get("dominance_verified"):
        return 7
    if flags.get("strategy_adapted"):
        return 6
    if flags.get("effect_measured"):
        return 5
    if flags.get("unit_controlled"):
        return 4
    if flags.get("telemetry_seen"):
        return 3
    if flags.get("installed"):
        return 2
    if flags.get("mod_generated") or flags.get("static_validated"):
        return 1
    return 0


def _read_runs() -> list[dict]:
    if not RUNS_FILE.exists():
        return []
    out = []
    with RUNS_FILE.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def _refresh_benchmark_levels() -> None:
    bench = _read_json(BENCHMARK_FILE, None)
    if not bench:
        create_game_iag_benchmark()
        bench = _read_json(BENCHMARK_FILE, None)
        if not bench:
            return
    runs = _read_runs()
    by_game: dict[str, list[dict]] = {}
    for r in runs:
        by_game.setdefault(r["game"], []).append(r)
    for entry in bench["games"]:
        records = by_game.get(entry["game"], [])
        entry["evidence_count"] = len(records)
        entry["current_level"] = max((r.get("level_reached", 0) for r in records), default=0)
    _write_json(BENCHMARK_FILE, bench)


def compare_runs(game: str) -> dict:
    runs = [r for r in _read_runs() if r["game"].lower() == game.lower()]
    if not runs:
        return {"ok": False, "error": f"no runs for {game}"}
    levels = [r.get("level_reached", 0) for r in runs]
    return {
        "ok": True, "game": game, "n_runs": len(runs),
        "level_min": min(levels), "level_max": max(levels),
        "level_last": levels[-1],
        "improving": levels[-1] >= levels[0],
        "first_ts": runs[0]["ts"], "last_ts": runs[-1]["ts"],
    }


def evaluate_dominance(game: str) -> dict:
    """Évalue la dominance honnêtement : exige niveau >= 5 ET >= 3 runs."""
    runs = [r for r in _read_runs() if r["game"].lower() == game.lower()]
    if not runs:
        return {"ok": False, "dominance": "unknown",
                "reason": "no_runs", "n_runs": 0}
    last = runs[-1]
    level = last.get("level_reached", 0)
    if level < 5 or len(runs) < 3:
        return {
            "ok": True, "dominance": "not_yet_proven",
            "reason": f"level={level}, n_runs={len(runs)} (besoin level>=5 et n>=3)",
            "n_runs": len(runs), "level_last": level,
        }
    metrics = last.get("report", {}).get("dominance_metrics", {})
    return {
        "ok": True, "dominance": "preliminary" if level < 7 else "verified",
        "level_last": level, "n_runs": len(runs),
        "metrics": metrics,
    }


def extract_transfer_lessons() -> dict:
    """Extrait des leçons cross-jeu (heuristiques) — honnête : si trop peu de runs, dit-le."""
    runs = _read_runs()
    if len(runs) < 2:
        payload = {"ok": True, "lessons": [], "n_runs": len(runs),
                   "note": "trop peu de runs pour transférer (besoin >= 2)"}
        _write_json(LESSONS_FILE, payload)
        return payload
    lessons = []
    by_game: dict[str, list[int]] = {}
    for r in runs:
        by_game.setdefault(r["game"], []).append(r.get("level_reached", 0))
    for g, levels in by_game.items():
        if max(levels) >= 2:
            lessons.append({
                "lesson": "static_validation_avant_install_paye",
                "evidence_game": g,
                "max_level": max(levels),
                "applies_to": [x["game"] for x in SUPPORTED_GAMES if x["game"] != g],
            })
        if max(levels) >= 3:
            lessons.append({
                "lesson": "telemetry_via_logs_natifs_du_jeu_est_fiable",
                "evidence_game": g,
                "applies_to": [x["game"] for x in SUPPORTED_GAMES if x["game"] != g],
            })
    payload = {"ok": True, "lessons": lessons, "n_runs": len(runs)}
    _write_json(LESSONS_FILE, payload)
    return payload


def propose_next_game_task() -> dict:
    """Choisit la prochaine action utile sur le benchmark."""
    bench = _read_json(BENCHMARK_FILE, None)
    if not bench:
        return {"ok": True, "action": "create_game_iag_benchmark",
                "reason": "benchmark file absent"}
    # priorité aux jeux active à plus bas niveau atteignable
    active = [g for g in bench["games"] if g.get("lab_module")]
    if not active:
        return {"ok": True, "action": "create_lab_module",
                "reason": "aucun jeu n'a de lab_module branché"}
    # X4 prioritaire si pas encore niveau 4
    x4 = next((g for g in active if g["game"] == "X4 Foundations"), None)
    if x4 and x4["current_level"] < 4:
        return {
            "ok": True, "action": "run_x4_autonomous_cycle",
            "reason": f"X4 niveau {x4['current_level']} < 4 (unit_controlled)",
            "module": "cortex_x4_faction_lab",
            "priority": 1,
        }
    if x4 and x4["current_level"] < 6:
        return {
            "ok": True, "action": "improve_x4_strategy",
            "reason": f"X4 niveau {x4['current_level']} — pousser vers strategy_adapted (6)",
            "module": "cortex_x4_faction_lab",
            "priority": 1,
        }
    return {"ok": True, "action": "create_factorio_lab",
            "reason": "X4 mature, brancher Factorio comme 2e jeu",
            "priority": 2}


def status() -> dict:
    bench = _read_json(BENCHMARK_FILE, None)
    runs = _read_runs()
    return {
        "ts": time.time(),
        "benchmark_exists": bench is not None,
        "n_supported_games": len(SUPPORTED_GAMES),
        "n_active_games": sum(1 for g in SUPPORTED_GAMES if g["status"] == "active"),
        "n_runs_total": len(runs),
        "games": [
            {"game": g["game"], "priority": g["priority"], "status": g["status"],
             "lab_module": g.get("lab_module")}
            for g in SUPPORTED_GAMES
        ],
    }


def self_test() -> dict:
    """Test sans toucher aux jeux réels."""
    out = {"ok": True, "tests": []}
    try:
        games = list_supported_games()
        out["tests"].append({"name": "list_supported_games", "ok": len(games) >= 1,
                             "n": len(games)})
        bench = create_game_iag_benchmark()
        out["tests"].append({"name": "create_benchmark", "ok": bench["ok"]})
        prof = create_game_profile("X4 Foundations")
        out["tests"].append({"name": "create_profile_x4", "ok": prof["ok"]})
        # run synthétique
        fake_report = {"mod_generated": True, "static_validated": True,
                       "installed": True, "launched": False}
        reg = register_test_run("X4 Foundations", fake_report)
        out["tests"].append({"name": "register_synthetic_run", "ok": reg["ok"],
                             "level_reached": reg.get("level_reached")})
        cmp = compare_runs("X4 Foundations")
        out["tests"].append({"name": "compare_runs", "ok": cmp["ok"]})
        dom = evaluate_dominance("X4 Foundations")
        out["tests"].append({"name": "evaluate_dominance", "ok": dom["ok"],
                             "dominance": dom.get("dominance")})
        nx = propose_next_game_task()
        out["tests"].append({"name": "propose_next_task", "ok": nx["ok"],
                             "action": nx.get("action")})
    except Exception as e:
        out["ok"] = False
        out["error"] = repr(e)
    out["ok"] = out["ok"] and all(t.get("ok") for t in out["tests"])
    return out


# ---------- CLI ----------

def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "status":
        print(json.dumps(status(), indent=2, ensure_ascii=False))
    elif cmd == "test":
        print(json.dumps(self_test(), indent=2, ensure_ascii=False))
    elif cmd == "init":
        print(json.dumps(create_game_iag_benchmark(), indent=2, ensure_ascii=False))
    elif cmd == "list":
        print(json.dumps(list_supported_games(), indent=2, ensure_ascii=False))
    elif cmd == "next":
        print(json.dumps(propose_next_game_task(), indent=2, ensure_ascii=False))
    elif cmd == "lessons":
        print(json.dumps(extract_transfer_lessons(), indent=2, ensure_ascii=False))
    else:
        print(f"unknown cmd: {cmd}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv))
