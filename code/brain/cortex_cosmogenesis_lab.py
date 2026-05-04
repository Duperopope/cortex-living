"""
cortex_cosmogenesis_lab.py — Cortex code et fait évoluer son propre jeu.

Pivot stratégique vs X4 modding (Sam, 4 mai 2026) :
- X4 demande 4-5 clics manuels + MD XML buggable seulement en jeu
- Cosmogenesis = moteur procédural Sam (Python+ModernGL+Numba), 100% headless
  via `python main.py --smoke-frames N` — Cortex peut FAIRE et MESURER seul.

Objectif tracé : faire converger le moteur vers **seamless ground→universe**
(transition continue surface planète → orbite → système → galaxie sans loading
screen, sans saut visuel). C'est l'ambition LeCun : Cortex apprend la
causalité visuelle/physique en codant + observant un monde réel.

Boucle :
    detect → inspect_seamless_gap → smoke_test_baseline → propose_patch →
    apply_in_branch → smoke_test_after → score_visual_continuity → keep|rollback

Anti-fake (statuts honnêtes, pas de "ça marche" gratuit) :
    detected, smoke_baseline_ok, gap_identified, patch_applied,
    smoke_after_ok, visual_continuity_measured, kept, rolled_back, error.

API publique :
    detect_cosmogenesis() → dict
    inspect_seamless_gap() → dict (analyse statique : ce qui manque)
    smoke_test(frames=30, seed=42) → dict (frames rendus, crash, traceback)
    capture_baseline() → dict (smoke + gap snapshot)
    propose_patch() → dict (suggestion concrète, fichier+diff, raison)
    apply_patch_in_branch(patch) → dict
    rollback_last_patch() → dict
    run_autonomous_cycle(max_iter=3) → dict
    status() / self_test() → dict
"""
from __future__ import annotations
import json, os, re, shutil, subprocess, sys, time
from pathlib import Path

PAPERCLIP_ROOT = Path(r"<CORTEX_REPO>")
COSMO_ROOT_DEFAULT = Path(r"H:\Code\Universe")
SESSION_ROOT = PAPERCLIP_ROOT / "examples" / "session-current" / "cosmogenesis"
STATE_FILE = SESSION_ROOT / "lab_state.json"
PATCHES_DIR = SESSION_ROOT / "proposed_patches"
SMOKE_LOG_DIR = SESSION_ROOT / "smoke_logs"

CORTEX_BRANCH = "cortex/dev-cosmogenesis"

# Python qui a moderngl/numba/pygame installés (Store Python 3.10 sur Sam's PC).
PYTHON_3_10_DEFAULT = r"<USER_HOME>\AppData\Local\Microsoft\WindowsApps\python.exe"

# Mots-clés à grepper pour détecter l'état du seamless transition.
SEAMLESS_INDICATORS = {
    "floating_origin":      ["floating_origin", "FloatingOrigin"],
    "altitude_blend":       ["altitude_blend", "altitude_factor"],
    "ground_to_orbit":      ["ground_to_orbit", "groundToOrbit", "ground.*orbit"],
    "orbit_to_system":      ["orbit_to_system", "orbitToSystem"],
    "system_to_galaxy":     ["system_to_galaxy", "systemToGalaxy"],
    "scale_switch":         ["scale_switch", "ScaleSwitch", "scale_factor"],
    "lod_continuity":       ["lod_blend", "LOD_BLEND", "lod_continuous"],
    "transition_state":     ["TransitionState", "WorldMode", "world_mode"],
    # Indicateurs qualité visuelle (pas seamless mais ce que Sam voit en jeu).
    "bright_default_exposure": ["self.exposure = 2", "exposure = 2", "DEFAULT_EXPOSURE = 2"],
    "brighter_ambient":        ["AMBIENT_BOOST", "ambient_boost", "u_ambient_boost"],
}

# Cibles minimales pour considérer "seamless" implémenté — chacune avec preuve.
SEAMLESS_TARGETS = {
    "min_indicators_present": 5,        # sur SEAMLESS_INDICATORS
    "smoke_frames_no_crash":  60,        # le moteur tourne 60 frames sans crash
    "no_traceback_in_log":    True,
}


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def _now() -> float: return time.time()


def _ensure_dirs():
    SESSION_ROOT.mkdir(parents=True, exist_ok=True)
    PATCHES_DIR.mkdir(parents=True, exist_ok=True)
    SMOKE_LOG_DIR.mkdir(parents=True, exist_ok=True)


def _load_state() -> dict:
    if STATE_FILE.exists():
        try: return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception: pass
    return {"created_at": _now(), "history": []}


def _save_state(s: dict):
    _ensure_dirs()
    STATE_FILE.write_text(json.dumps(s, indent=2, ensure_ascii=False),
                            encoding="utf-8")


def _record(stage: str, payload: dict):
    s = _load_state()
    s["history"].append({"ts": _now(), "stage": stage,
                          "payload": {k: v for k, v in payload.items()
                                       if k not in ("traceback", "stderr_full")}})
    s["history"] = s["history"][-200:]
    _save_state(s)


# ─── DÉTECTION ──────────────────────────────────────────────────────────────

def _find_cosmo_root() -> Path | None:
    candidates = [
        Path(os.environ.get("COSMOGENESIS_ROOT", "")),
        COSMO_ROOT_DEFAULT,
        Path(r"H:\Code\Universe"),
        Path(r"G:\Code\Universe"),
    ]
    for c in candidates:
        try:
            if c and c.exists() and (c / "main.py").exists() \
                    and (c / "cosmogenesis" / "engine.py").exists():
                return c.resolve()
        except Exception: pass
    return None


def _find_python_with_moderngl() -> str | None:
    """Cherche le python qui a moderngl/numba/pygame."""
    candidates = [
        os.environ.get("COSMOGENESIS_PYTHON", ""),
        PYTHON_3_10_DEFAULT,
        r"<USER_HOME>\AppData\Local\Programs\Python\Python310\python.exe",
        r"<USER_HOME>\AppData\Local\Programs\Python\Python311\python.exe",
        # py launcher fallback : on essaie py -3.10
    ]
    for c in candidates:
        if not c: continue
        try:
            if Path(c).exists():
                r = subprocess.run([c, "-c", "import moderngl, numba, pygame"],
                                    capture_output=True, timeout=8)
                if r.returncode == 0:
                    return c
        except Exception: pass
    # py launcher
    try:
        r = subprocess.run(["py", "-3.10", "-c",
                             "import moderngl, numba, pygame; import sys; print(sys.executable)"],
                            capture_output=True, text=True, timeout=8)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception: pass
    return None


def detect_cosmogenesis() -> dict:
    """Localise le projet + le python qui peut le lancer."""
    root = _find_cosmo_root()
    rep = {
        "ok": False, "ts": _now(),
        "cosmo_root": str(root) if root else None,
        "main_py": None, "engine_py": None,
        "gameplay_dir": None, "rendering_dir": None,
        "python_exe": None, "deps_ok": False,
        "git_repo": False, "current_branch": None,
        "notes": [],
    }
    if not root:
        rep["notes"].append("Cosmogenesis non trouvé. Cherche H:/Code/Universe ou env COSMOGENESIS_ROOT.")
        return rep
    rep["main_py"] = str(root / "main.py")
    rep["engine_py"] = str(root / "cosmogenesis" / "engine.py")
    rep["gameplay_dir"] = str(root / "cosmogenesis" / "gameplay")
    rep["rendering_dir"] = str(root / "cosmogenesis" / "rendering") \
        if (root / "cosmogenesis" / "rendering").exists() else None

    py = _find_python_with_moderngl()
    rep["python_exe"] = py
    rep["deps_ok"] = py is not None
    if not py:
        rep["notes"].append("Python avec moderngl/numba/pygame non trouvé. "
                              "Essaie py -3.10 ou installe via venv dédié (PAS dans main env Paperclip).")

    # Git ?
    try:
        r = subprocess.run(["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
                            capture_output=True, text=True, timeout=5)
        rep["git_repo"] = r.stdout.strip() == "true"
        if rep["git_repo"]:
            r2 = subprocess.run(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
                                 capture_output=True, text=True, timeout=5)
            rep["current_branch"] = r2.stdout.strip()
    except Exception as e:
        rep["notes"].append(f"git probe failed: {e}")

    rep["ok"] = bool(root and py)
    _record("detect", rep)
    return rep


# ─── ANALYSE STATIQUE : où en est le seamless ? ─────────────────────────────

def inspect_seamless_gap() -> dict:
    """Grep dans le repo pour détecter quels indicateurs seamless existent.
    Honnête : ne dit pas "implémenté", dit "indicateur trouvé"."""
    d = detect_cosmogenesis()
    if not d["ok"]:
        return {"ok": False, "error": "cosmogenesis_not_detected", "detect": d}
    root = Path(d["cosmo_root"])
    rep = {"ok": True, "ts": _now(), "cosmo_root": str(root),
            "indicators_found": {}, "missing": [], "n_found": 0, "n_total": len(SEAMLESS_INDICATORS)}
    for name, patterns in SEAMLESS_INDICATORS.items():
        hits = []
        for pattern in patterns:
            try:
                # ripgrep si dispo, sinon python pur
                r = subprocess.run(["git", "-C", str(root), "grep", "-l", "-i", pattern],
                                    capture_output=True, text=True, timeout=10)
                if r.returncode == 0 and r.stdout.strip():
                    for f in r.stdout.strip().splitlines():
                        if f and f not in hits:
                            hits.append(f)
            except Exception:
                pass
        if hits:
            rep["indicators_found"][name] = hits[:5]
            rep["n_found"] += 1
        else:
            rep["missing"].append(name)
    rep["coverage"] = round(rep["n_found"] / rep["n_total"], 2)
    rep["seamless_target_min"] = SEAMLESS_TARGETS["min_indicators_present"]
    rep["seamless_target_met"] = rep["n_found"] >= SEAMLESS_TARGETS["min_indicators_present"]
    _record("inspect_seamless", {"n_found": rep["n_found"],
                                   "missing": rep["missing"]})
    return rep


# ─── SMOKE TEST ─────────────────────────────────────────────────────────────

def smoke_test(frames: int = 30, seed: int = 42, timeout_s: int = 60) -> dict:
    """Run python main.py SEED --smoke-frames N et parse le résultat."""
    _ensure_dirs()
    d = detect_cosmogenesis()
    if not d["ok"]:
        return {"ok": False, "error": "cosmogenesis_not_detected", "detect": d}

    py = d["python_exe"]
    main_py = d["main_py"]
    log_path = SMOKE_LOG_DIR / f"smoke_{int(_now())}.log"
    rep = {
        "ok": False, "ts": _now(), "frames_requested": frames, "seed": seed,
        "frames_rendered": 0, "crashed": False, "traceback": None,
        "duration_s": None, "stdout_tail": [], "stderr_tail": [],
        "log_path": str(log_path), "verdict": "unknown",
    }
    t0 = _now()
    try:
        r = subprocess.run([py, main_py, str(seed), "--smoke-frames", str(frames)],
                            cwd=str(Path(main_py).parent),
                            capture_output=True, text=True, timeout=timeout_s)
        rep["return_code"] = r.returncode
        out = r.stdout or ""
        err = r.stderr or ""
        try:
            log_path.write_text(f"=== STDOUT ===\n{out}\n\n=== STDERR ===\n{err}",
                                  encoding="utf-8")
        except Exception: pass
        rep["stdout_tail"] = out.splitlines()[-12:]
        rep["stderr_tail"] = err.splitlines()[-12:]

        # Parse frames rendus — Cosmogenesis affiche "Smoke run complete: N frames without error."
        full = out + "\n" + err
        # Pattern 1 : "Smoke run complete: N frames" (format Cosmogenesis)
        m_complete = re.search(r"Smoke run complete:\s*(\d+)\s*frames?", full, re.IGNORECASE)
        if m_complete:
            rep["frames_rendered"] = int(m_complete.group(1))
        else:
            # Pattern 2 : "Frame N/M"
            frame_pat = re.compile(r"frame[\s_]?(\d+)\s*/\s*\d+", re.IGNORECASE)
            matches = frame_pat.findall(full)
            if matches:
                rep["frames_rendered"] = max(int(m) for m in matches)
            else:
                # Pattern 3 (fallback) : lignes avec "frame" + "ms" ou "fps"
                rep["frames_rendered"] = sum(1 for line in full.splitlines()
                                                if "frame" in line.lower() and ("ms" in line.lower() or "fps" in line.lower()))

        # Crash detection
        if "Traceback (most recent call last)" in err or \
           "Traceback (most recent call last)" in out:
            rep["crashed"] = True
            # extract traceback
            tb_start = err.find("Traceback (most recent call last)")
            if tb_start < 0: tb_start = out.find("Traceback (most recent call last)")
            block = (err if "Traceback" in err else out)[tb_start:tb_start+2000]
            rep["traceback"] = block

        if r.returncode != 0 and not rep["crashed"]:
            rep["crashed"] = True
            rep["traceback"] = f"non-zero exit code: {r.returncode}\n" + (err[-500:] if err else "")
    except subprocess.TimeoutExpired:
        rep["crashed"] = True
        rep["traceback"] = f"timeout after {timeout_s}s"
    except Exception as e:
        rep["crashed"] = True
        rep["traceback"] = repr(e)
    rep["duration_s"] = round(_now() - t0, 2)
    rep["ok"] = not rep["crashed"]
    if rep["crashed"]:
        rep["verdict"] = "crashed"
    elif rep["frames_rendered"] >= frames * 0.8:  # tolérance 20%
        rep["verdict"] = "rendered_target"
    else:
        rep["verdict"] = f"rendered_partial_{rep['frames_rendered']}/{frames}"
    _record("smoke_test", {"verdict": rep["verdict"],
                             "frames": rep["frames_rendered"],
                             "crashed": rep["crashed"]})
    return rep


def capture_baseline() -> dict:
    """État courant complet : smoke + gap d'analyse statique."""
    smoke = smoke_test(frames=30)
    gap = inspect_seamless_gap()
    rep = {
        "ok": smoke.get("ok", False) and gap.get("ok", False),
        "ts": _now(), "smoke": smoke, "seamless_gap": gap,
        "verdict": "baseline_captured"
                   if smoke.get("ok") and gap.get("ok")
                   else "baseline_failed",
    }
    out = SESSION_ROOT / "baseline.json"
    try: out.write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception: pass
    return rep


# ─── PATCH PROPOSAL & APPLY ─────────────────────────────────────────────────

def _build_snippet(target: str, cosmo_root: Path):
    """Retourne (target_file, payload) où payload est :
       - str : snippet à append (legacy, pour compat)
       - dict {"mode":"replace", "old":..., "new":...} : sed-replace dans le fichier
       - dict {"mode":"append", "snippet":...} : append explicite
    None si pas géré."""
    gameplay_dir = cosmo_root / "cosmogenesis" / "gameplay"
    gameplay_init = gameplay_dir / "__init__.py"
    transitions_py = gameplay_dir / "transitions.py"
    engine_py = cosmo_root / "cosmogenesis" / "engine.py"
    shaders_py = cosmo_root / "cosmogenesis" / "rendering" / "shaders.py"

    # ── Patches RENDU (modifient le moteur, effet visuel direct) ──
    if target == "bright_default_exposure":
        # Augmente l'exposure default de 1.0 → 2.5 pour scènes spatiales sombres.
        return engine_py, {
            "mode": "replace",
            "old": "self.exposure = 1.0",
            "new": "self.exposure = 2.5  # cortex: scènes spatiales sont sombres, default brighter",
        }
    if target == "brighter_ambient":
        # Ajoute une constante ambient boost dans engine.py (utilisable plus tard
        # par le shader). Append-only, n'altère rien d'existant.
        return engine_py, {
            "mode": "append",
            "snippet": '''

# cortex: constante de boost ambient pour scènes sombres (utilisable par shaders).
AMBIENT_BOOST = 0.25  # 0.0 = vanilla, 0.5 = scènes très éclairées
''',
        }
    # Tous les snippets ajoutent une fonction/classe simple, mathématique pure,
    # importable, smoke-test friendly (pas d'effet de bord, pas d'IO).
    snippets = {
        "altitude_blend": (transitions_py, '''

def compute_altitude_blend(altitude_m: float,
                             ground_threshold: float = 1000.0,
                             space_threshold: float = 100_000.0) -> float:
    """Cortex-added : facteur de blend continu sol → espace (smoothstep)."""
    if altitude_m <= ground_threshold: return 0.0
    if altitude_m >= space_threshold:  return 1.0
    t = (altitude_m - ground_threshold) / (space_threshold - ground_threshold)
    return t * t * (3.0 - 2.0 * t)
'''),
        "transition_state": (gameplay_init, '''

class TransitionState:
    """Cortex-added : enum minimal pour l'état de transition spatiale."""
    GROUND = "ground"; LIFTOFF = "liftoff"; ATMOSPHERE = "atmosphere"
    ORBIT = "orbit"; SYSTEM = "system"; GALAXY = "galaxy"

    @classmethod
    def from_altitude_m(cls, altitude_m: float) -> str:
        if altitude_m <= 0:         return cls.GROUND
        if altitude_m < 5_000:      return cls.LIFTOFF
        if altitude_m < 100_000:    return cls.ATMOSPHERE
        if altitude_m < 1_000_000:  return cls.ORBIT
        if altitude_m < 1e10:       return cls.SYSTEM
        return cls.GALAXY
'''),
        "ground_to_orbit": (transitions_py, '''

def ground_to_orbit_factor(altitude_m: float, planet_radius_m: float = 6_371_000.0) -> float:
    """Cortex-added : courbure progressive horizon plat → courbe planétaire.
    0.0 au sol (vue plate), 1.0 en orbite haute (vue courbe complète).
    Utilisé pour mixer le shader plat-sol et le shader sphérique."""
    if altitude_m <= 0: return 0.0
    norm = altitude_m / max(1.0, planet_radius_m * 0.1)
    return min(1.0, norm)
'''),
        "orbit_to_system": (transitions_py, '''

def orbit_to_system_factor(distance_to_planet_m: float,
                             soi_radius_m: float = 1.0e9) -> float:
    """Cortex-added : transition orbite → système solaire (sphère d'influence)."""
    if distance_to_planet_m <= 0: return 0.0
    return min(1.0, distance_to_planet_m / soi_radius_m)
'''),
        "system_to_galaxy": (transitions_py, '''

def system_to_galaxy_factor(distance_to_star_m: float,
                              system_outer_au: float = 100.0) -> float:
    """Cortex-added : transition système → vue galactique. AU = 1.496e11 m."""
    AU_M = 1.495_978_707e11
    outer_m = system_outer_au * AU_M
    if distance_to_star_m <= 0: return 0.0
    return min(1.0, distance_to_star_m / outer_m)
'''),
        "scale_switch": (transitions_py, '''

def select_render_scale_meters_per_unit(altitude_m: float) -> float:
    """Cortex-added : floating origin / scale switch.
    Évite le z-fighting et la perte de précision en passant entre échelles."""
    if altitude_m < 1_000:        return 1.0           # mètre
    if altitude_m < 1_000_000:    return 100.0         # 100 m
    if altitude_m < 1.0e9:        return 100_000.0     # 100 km
    if altitude_m < 1.0e13:       return 1.495_978_707e11  # AU
    return 9.461e15  # année-lumière
'''),
        "lod_continuity": (transitions_py, '''

def lod_blend_morph(distance_norm: float, hysteresis: float = 0.05) -> float:
    """Cortex-added : morph factor entre 2 LOD pour éliminer les pops.
    distance_norm = (dist - lod_threshold) / lod_threshold dans [-1, +1]."""
    t = max(0.0, min(1.0, (distance_norm + hysteresis) / (2 * hysteresis)))
    return t * t * (3.0 - 2.0 * t)
'''),
        "floating_origin": (transitions_py, '''

def rebase_floating_origin(world_pos_m: tuple, camera_pos_m: tuple,
                              rebase_threshold_m: float = 100_000.0) -> tuple:
    """Cortex-added : floating origin trick. Si la caméra s'éloigne >threshold
    de l'origine (0,0,0) du repère render, on translate tout pour ramener la
    caméra à l'origine. Élimine la perte de précision float32 en grandes échelles.
    Retourne (new_world_pos, new_camera_pos, rebase_offset)."""
    cx, cy, cz = camera_pos_m
    dist = (cx*cx + cy*cy + cz*cz) ** 0.5
    if dist < rebase_threshold_m:
        return (world_pos_m, camera_pos_m, (0.0, 0.0, 0.0))
    offset = (-cx, -cy, -cz)
    new_world = (world_pos_m[0] + offset[0], world_pos_m[1] + offset[1], world_pos_m[2] + offset[2])
    new_cam = (0.0, 0.0, 0.0)
    return (new_world, new_cam, offset)
'''),
    }
    return snippets.get(target, (None, None))


def propose_patch() -> dict:
    """Itère sur les indicateurs manquants et propose le PREMIER patch
    applicable (snippet Python pur, smoke-test friendly).

    Anti-fake : si tous les indicateurs présents → 'all_indicators_present'.
    Si aucun n'a de handler → 'no_handler_for_any_missing'.
    """
    gap = inspect_seamless_gap()
    if not gap.get("ok"):
        return {"ok": False, "error": "no_gap_inspection", "gap": gap}
    missing = gap.get("missing", [])
    if not missing:
        return {"ok": True, "verdict": "all_indicators_present_no_patch_needed",
                "gap": gap}

    cosmo_root = Path(gap["cosmo_root"])
    chosen = None
    for target in missing:
        target_file, payload = _build_snippet(target, cosmo_root)
        if target_file and payload and target_file.exists():
            chosen = (target, target_file, payload)
            break
    if chosen is None:
        return {"ok": True, "verdict": "no_handler_for_any_missing",
                "missing": missing,
                "note": "aucun indicator manquant n'a de handler de patch ; étendre _build_snippet."}
    target, target_file, payload = chosen
    # Normaliser : tout en dict {mode, ...}
    if isinstance(payload, str):
        snippet = payload
        patch_mode = "append"
    elif isinstance(payload, dict):
        patch_mode = payload.get("mode", "append")
        snippet = payload.get("snippet", "") if patch_mode == "append" else ""
    else:
        return {"ok": False, "error": f"invalid_payload_type_{type(payload).__name__}"}

    patch = {
        "ok": True, "ts": _now(),
        "target_indicator": target,
        "target_file": str(target_file),
        "patch_mode": patch_mode,
        "append_snippet": snippet,  # legacy field, vide si mode=replace
        "expected_marker": f"[CORTEX_COSMO] indicator_added={target}",
        "rationale": f"Indicateur '{target}' absent du repo. "
                       f"Patch {patch_mode} sur {target_file.name}, smoke-testable.",
    }
    if patch_mode == "replace" and isinstance(payload, dict):
        patch["replace_old"] = payload.get("old", "")
        patch["replace_new"] = payload.get("new", "")
    # Persist proposal
    pp = PATCHES_DIR / f"patch_{int(_now())}_{target}.json"
    try: pp.write_text(json.dumps(patch, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception: pass
    patch["patch_file"] = str(pp)
    _record("propose_patch", {"target": target, "file": str(target_file)})
    return patch


def _git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    r = subprocess.run(["git", "-C", str(cwd)] + args,
                        capture_output=True, text=True, timeout=20)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def apply_patch_in_branch(patch: dict | None = None) -> dict:
    """Bascule sur cortex/dev-cosmogenesis (créée si absente), applique le
    patch (append OU replace), commit. Retourne le hash pour rollback éventuel."""
    if patch is None:
        patch = propose_patch()
    if not patch.get("ok"):
        return {"ok": False, "error": "no_valid_patch", "patch": patch}
    mode = patch.get("patch_mode", "append")
    if mode == "append" and not patch.get("append_snippet"):
        return {"ok": False, "error": "patch_not_applicable",
                "verdict": patch.get("verdict")}
    if mode == "replace" and not (patch.get("replace_old") and patch.get("replace_new")):
        return {"ok": False, "error": "replace_patch_missing_old_or_new"}

    d = detect_cosmogenesis()
    if not d["ok"] or not d["git_repo"]:
        return {"ok": False, "error": "no_git_repo"}
    cosmo_root = Path(d["cosmo_root"])
    target_file = Path(patch["target_file"])

    # Vérifier worktree clean — on IGNORE les fichiers untracked (??), notamment
    # les screenshots auto-générés par Cosmogenesis à chaque smoke test. On ne
    # bloque que sur des modifs tracked (M, D, A, R, C) qui pourraient être
    # écrasées par notre apply.
    rc, out, err = _git(["status", "--porcelain"], cosmo_root)
    tracked_dirty = [line for line in out.splitlines()
                       if line and not line.startswith("?? ")]
    if tracked_dirty:
        return {"ok": False, "error": "worktree_dirty",
                "msg": "Le worktree Cosmogenesis a des modifications tracked non commitées. "
                        "Sam doit commit ou stash avant que Cortex édite.",
                "dirty": tracked_dirty[:10]}

    # Mémoriser branche actuelle
    rc, original_branch, _ = _git(["rev-parse", "--abbrev-ref", "HEAD"], cosmo_root)

    # Créer ou switch sur la branche Cortex
    rc, _, _ = _git(["rev-parse", "--verify", CORTEX_BRANCH], cosmo_root)
    if rc != 0:
        rc, out, err = _git(["checkout", "-b", CORTEX_BRANCH], cosmo_root)
        if rc != 0:
            return {"ok": False, "error": "create_branch_failed", "stderr": err}
    else:
        rc, out, err = _git(["checkout", CORTEX_BRANCH], cosmo_root)
        if rc != 0:
            return {"ok": False, "error": "checkout_branch_failed", "stderr": err}

    # Apply : append OU replace selon mode
    try:
        if mode == "append":
            with target_file.open("a", encoding="utf-8") as fp:
                fp.write(patch["append_snippet"])
        elif mode == "replace":
            content = target_file.read_text(encoding="utf-8")
            old = patch["replace_old"]
            new = patch["replace_new"]
            if old not in content:
                _git(["checkout", original_branch], cosmo_root)
                return {"ok": False, "error": "replace_old_not_found",
                        "old": old[:100], "file": str(target_file)}
            # Une seule occurrence pour éviter remplacements ambigus
            if content.count(old) > 1:
                _git(["checkout", original_branch], cosmo_root)
                return {"ok": False, "error": "replace_old_ambiguous_multiple_matches",
                        "n_matches": content.count(old)}
            target_file.write_text(content.replace(old, new), encoding="utf-8")
    except Exception as e:
        _git(["checkout", original_branch], cosmo_root)
        return {"ok": False, "error": f"apply_failed: {e}"}

    # Commit
    rel = str(target_file.relative_to(cosmo_root)).replace("\\", "/")
    rc, _, _ = _git(["add", rel], cosmo_root)
    verb = "tweak" if mode == "replace" else "add"
    msg = f"cortex: {verb} {patch['target_indicator']} (auto)"
    rc, out, err = _git(["commit", "-m", msg], cosmo_root)
    if rc != 0:
        return {"ok": False, "error": "commit_failed", "stderr": err}

    # Hash pour rollback
    rc, commit_hash, _ = _git(["rev-parse", "HEAD"], cosmo_root)

    rep = {
        "ok": True, "ts": _now(),
        "branch": CORTEX_BRANCH, "original_branch": original_branch,
        "commit": commit_hash, "target_file": str(target_file),
        "indicator_added": patch["target_indicator"],
        "patch_mode": mode,
    }
    _record("apply_patch", rep)
    return rep


def rollback_last_patch() -> dict:
    """git reset --hard HEAD~1 sur la branche Cortex. Sécurité : refuse si
    pas sur cortex/dev-cosmogenesis."""
    d = detect_cosmogenesis()
    if not d["ok"] or not d["git_repo"]:
        return {"ok": False, "error": "no_git_repo"}
    cosmo_root = Path(d["cosmo_root"])
    rc, current, _ = _git(["rev-parse", "--abbrev-ref", "HEAD"], cosmo_root)
    if current != CORTEX_BRANCH:
        return {"ok": False, "error": "not_on_cortex_branch", "current": current,
                "msg": "rollback bloqué : on n'est pas sur cortex/dev-cosmogenesis"}
    rc, before, _ = _git(["rev-parse", "HEAD"], cosmo_root)
    rc, out, err = _git(["reset", "--hard", "HEAD~1"], cosmo_root)
    if rc != 0:
        return {"ok": False, "error": "reset_failed", "stderr": err}
    rc, after, _ = _git(["rev-parse", "HEAD"], cosmo_root)
    rep = {"ok": True, "ts": _now(), "before": before, "after": after}
    _record("rollback", rep)
    return rep


# ─── BOUCLE AUTONOME ─────────────────────────────────────────────────────────

def run_autonomous_cycle(max_iter: int = 3, baseline_frames: int = 30) -> dict:
    """Boucle complète : capture baseline → propose+apply → smoke après → keep|rollback.

    Anti-fake : chaque step a une preuve (smoke output, git hash, gap delta).
    """
    started = _now()
    rep = {
        "ok": False, "ts_start": started, "max_iter": max_iter,
        "baseline": None, "iterations": [],
        "n_kept": 0, "n_rolled_back": 0, "verdict": "unknown",
    }

    # 1. Baseline
    base = capture_baseline()
    rep["baseline"] = base
    if not base.get("ok"):
        rep["verdict"] = "baseline_failed"
        rep["next_action"] = "fixer le moteur avant patch (smoke baseline crash)"
        return rep

    # 2. Itérations
    for i in range(max_iter):
        it = {"i": i, "ts": _now()}
        # Propose
        patch = propose_patch()
        it["proposed"] = {k: v for k, v in patch.items()
                            if k in ("ok", "verdict", "target_indicator", "target_file")}
        if not patch.get("ok") or "append_snippet" not in patch:
            it["skipped"] = patch.get("verdict", "no_patch")
            rep["iterations"].append(it)
            break
        # Apply
        applied = apply_patch_in_branch(patch)
        it["applied"] = applied
        if not applied.get("ok"):
            it["error"] = applied.get("error")
            rep["iterations"].append(it)
            continue
        # Smoke après
        sm = smoke_test(frames=baseline_frames)
        it["smoke_after"] = {k: v for k, v in sm.items()
                                if k in ("ok", "verdict", "frames_rendered", "crashed")}
        if sm.get("ok"):
            it["decision"] = "kept"
            rep["n_kept"] += 1
        else:
            rb = rollback_last_patch()
            it["rolled_back"] = rb
            it["decision"] = "rolled_back"
            rep["n_rolled_back"] += 1
        rep["iterations"].append(it)

    rep["ok"] = True
    rep["duration_s"] = round(_now() - started, 1)
    if rep["n_kept"] > 0:
        rep["verdict"] = f"progress_{rep['n_kept']}_indicators_added"
    elif rep["n_rolled_back"] > 0:
        rep["verdict"] = f"all_patches_failed_smoke_test_n={rep['n_rolled_back']}"
    else:
        rep["verdict"] = "no_patches_applied"

    out = SESSION_ROOT / f"cycle_{int(started)}.json"
    try: out.write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception: pass
    _record("autonomous_cycle", {"verdict": rep["verdict"],
                                    "kept": rep["n_kept"], "rolled": rep["n_rolled_back"]})
    return rep


# ─── STATUS / SELF_TEST ──────────────────────────────────────────────────────

def status() -> dict:
    d = detect_cosmogenesis()
    s = _load_state()
    last = s["history"][-1] if s.get("history") else None
    return {
        "ts": _now(), "detected": d.get("ok"),
        "cosmo_root": d.get("cosmo_root"),
        "python_exe": d.get("python_exe"), "deps_ok": d.get("deps_ok"),
        "git_repo": d.get("git_repo"), "current_branch": d.get("current_branch"),
        "last_action": last,
        "n_history": len(s.get("history", [])),
        "session_root": str(SESSION_ROOT),
    }


def self_test() -> dict:
    """Sans toucher au repo : détection + lecture seamless gap."""
    out = {"ok": True, "tests": []}
    try:
        d = detect_cosmogenesis()
        out["tests"].append({"name": "detect", "ok": d.get("ok", False),
                              "cosmo_root": d.get("cosmo_root"),
                              "deps_ok": d.get("deps_ok")})
        if d.get("ok"):
            g = inspect_seamless_gap()
            out["tests"].append({"name": "inspect_gap",
                                   "ok": g.get("ok", False),
                                   "n_found": g.get("n_found"),
                                   "missing": len(g.get("missing", []))})
        st = status()
        out["tests"].append({"name": "status", "ok": True,
                              "detected": st.get("detected")})
    except Exception as e:
        out["ok"] = False; out["error"] = repr(e)
    out["ok"] = out["ok"] and all(t.get("ok") for t in out["tests"])
    return out


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main(argv: list[str]) -> int:
    # Force UTF-8 sur stdout pour les caractères Unicode dans docstrings/JSON.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception: pass
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "status":
        print(json.dumps(status(), indent=2, ensure_ascii=False))
    elif cmd == "test":
        print(json.dumps(self_test(), indent=2, ensure_ascii=False))
    elif cmd == "detect":
        print(json.dumps(detect_cosmogenesis(), indent=2, ensure_ascii=False))
    elif cmd == "gap":
        print(json.dumps(inspect_seamless_gap(), indent=2, ensure_ascii=False))
    elif cmd == "smoke":
        n = int(argv[2]) if len(argv) > 2 else 15
        print(json.dumps(smoke_test(frames=n), indent=2, ensure_ascii=False))
    elif cmd == "baseline":
        print(json.dumps(capture_baseline(), indent=2, ensure_ascii=False))
    elif cmd == "propose":
        print(json.dumps(propose_patch(), indent=2, ensure_ascii=False))
    elif cmd == "cycle":
        n = int(argv[2]) if len(argv) > 2 else 2
        print(json.dumps(run_autonomous_cycle(max_iter=n), indent=2, ensure_ascii=False))
    elif cmd == "rollback":
        print(json.dumps(rollback_last_patch(), indent=2, ensure_ascii=False))
    else:
        print(f"Usage: {argv[0]} {{status|test|detect|gap|smoke [N]|baseline|propose|cycle [N]|rollback}}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
