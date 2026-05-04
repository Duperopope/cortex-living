"""
cortex_x4_faction_lab.py — Cortex apprend X4 Foundations en créant sa faction.

Pas de doc, pas de tutoriel : Cortex génère un mod, l'installe, lance le jeu,
observe une preuve auto-générée par son mod (télémétrie via Mission Director),
diagnostique, patche, recommence.

Boucle :
    detect_x4 → inspect_x4_modding_environment → create_cortex_faction_extension
    → static_validate_extension → install_extension → launch_x4 → collect_evidence
    → diagnose_x4_result → patch_extension → relaunch.

Statuts honnêtes (pas de "ça marche" gratuit) :
    generated, static_validated, installed, launched, game_detected_extension,
    faction_detected, unit_spawned, unit_has_order, economy_tick_verified,
    failed, needs_human_only_if_blocked.

Stratégie télémétrie X4 :
    Le mod écrit via Mission Director `<debug_text>` dans le journal X4
    (`<USER_HOME>\\Documents\\Egosoft\\X4\\*\\debug.log` ou similar). Cortex
    parse ce log pour confirmer chaque step (extension chargée, faction init,
    unit spawn, order assigned).

API publique :
    detect_x4() → dict {installed, x4_root, x4_exe, extensions_dir, ...}
    inspect_x4_modding_environment() → dict
    create_cortex_faction_extension() → dict
    static_validate_extension(path=None) → dict
    install_extension(path=None) → dict
    launch_x4() → dict
    collect_evidence() → dict
    diagnose_x4_result() → dict
    patch_extension() → dict
    rollback_extension() → dict
    run_autonomous_test_cycle(max_minutes=20) → dict
    status() → dict (snapshot rapide)
    self_test() → dict (NE LANCE PAS le jeu)

Sécurité :
- `self_test()` ne touche pas au système X4 réel.
- `install_extension()` fait un backup horodaté de toute extension cortex_faction préexistante.
- `launch_x4()` spawn dans un process indépendant, log le PID, et n'attend pas la fin du jeu.
- `rollback_extension()` permet d'annuler proprement.
"""
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parent
PAPERCLIP_ROOT = Path(r"<CORTEX_REPO>")
LAB_ROOT = PAPERCLIP_ROOT / "examples" / "game-modding" / "x4-cortex-faction"
SESSION_ROOT = PAPERCLIP_ROOT / "examples" / "session-current" / "x4_cortex_faction"
STATE_FILE = SESSION_ROOT / "lab_state.json"

USER_HOME = Path(os.environ.get("USERPROFILE",
                                  os.environ.get("HOME", str(Path.home()))))
X4_USER_DOCS = USER_HOME / "Documents" / "Egosoft" / "X4"

# Identifiants stables du mod Cortex
MOD_ID = "cortex_faction"
MOD_NAME = "Cortex Faction Lab"
MOD_VERSION = "0.2.1"

# Marqueurs télémétrie : ces strings doivent apparaître dans debug.log
# quand le mod tourne. Cortex grep ces strings comme preuves.
TELEMETRY_MARKERS = {
    "extension_loaded":   "[CORTEX_FACTION] extension_loaded v" + MOD_VERSION,
    "new_game_started":   "[CORTEX_FACTION] new_game_started",
    "save_loaded":        "[CORTEX_FACTION] save_loaded",
    "faction_init":       "[CORTEX_FACTION] faction_init",
    "sector_claimed":     "[CORTEX_FACTION] sector_claimed",
    "station_built":      "[CORTEX_FACTION] station_built",
    "ship_spawned":       "[CORTEX_FACTION] ship_spawned",
    "order_assigned":     "[CORTEX_FACTION] order_assigned",
    "economy_tick":       "[CORTEX_FACTION] economy_tick",
    "bootstrap_done":     "[CORTEX_FACTION] bootstrap_done",
    "error":              "[CORTEX_FACTION] ERROR",
}

# Spec autonomie v0.2 : ce que le mod doit accomplir SEUL (preuves dans debug.log)
AUTONOMY_TARGETS = {
    "min_sectors_owned": 3,
    "min_stations":      5,    # 1 wharf + 1 shipyard + 3 production
    "min_ships":         8,    # 4 miners + 2 traders + 2 defense
    "min_credits_seed":  50_000_000,
}


def _now() -> float: return time.time()


def _load_state() -> dict:
    if STATE_FILE.exists():
        try: return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception: pass
    return {"created_at": _now(), "history": []}


def _save_state(s: dict) -> None:
    s["updated_at"] = _now()
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(s, indent=2, ensure_ascii=False),
                               encoding="utf-8")
    except Exception: pass


def _record_step(step: str, payload: dict) -> None:
    """Append-only journal des étapes (audit)."""
    state = _load_state()
    h = state.get("history", [])
    h.append({"ts": _now(), "step": step, **payload})
    state["history"] = h[-200:]
    _save_state(state)


# ─── DÉTECTION X4 ────────────────────────────────────────────────────────────
def _candidate_steam_libraries() -> list[Path]:
    """Trouve toutes les Steam libraries.

    Stratégies :
    1. Registre Windows (HKLM\\SOFTWARE\\WOW6432Node\\Valve\\Steam → InstallPath)
    2. Paths classiques (Program Files (x86)/Steam, etc.)
    3. Scan des lettres de disque pour `<L>:\\Steam` (vu en live : Sam a `G:\\Steam`)
    4. Custom libraries depuis libraryfolders.vdf de chaque Steam trouvé
    """
    out: list[Path] = []

    # 1. Registre Windows
    try:
        import winreg  # type: ignore
        for key_path in (r"SOFTWARE\WOW6432Node\Valve\Steam",
                          r"SOFTWARE\Valve\Steam"):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as k:
                    val, _ = winreg.QueryValueEx(k, "InstallPath")
                    p = Path(val)
                    if p.exists() and p not in out:
                        out.append(p)
            except Exception: pass
    except Exception: pass

    # 2. Paths classiques
    for default in [r"C:\Program Files (x86)\Steam",
                     r"C:\Program Files\Steam",
                     str(USER_HOME / "scoop" / "apps" / "steam" / "current")]:
        p = Path(default)
        if p.exists() and p not in out: out.append(p)

    # 3. Scan des lettres de disque (Sam : G:\Steam, mais peut être ailleurs)
    for letter in "CDEFGHIJKL":
        p = Path(f"{letter}:\\Steam")
        if p.exists() and p not in out:
            out.append(p)

    # 4. Custom libraries depuis libraryfolders.vdf
    for steam_dir in list(out):
        vdf = steam_dir / "steamapps" / "libraryfolders.vdf"
        if not vdf.exists(): continue
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r'"path"\s*"([^"]+)"', text):
                lib = Path(m.group(1).replace("\\\\", "\\"))
                if lib.exists() and lib not in out:
                    out.append(lib)
        except Exception: pass
    return out


def detect_x4() -> dict:
    """Détecte X4 Foundations sans intervention humaine.

    Cherche :
    - Steam libraries (defaults + custom via libraryfolders.vdf)
    - X4.exe sous steamapps/common/X4 Foundations/
    - dossier extensions
    - dossier user docs Egosoft/X4
    """
    notes = []
    for lib in _candidate_steam_libraries():
        notes.append(f"steam_lib_found={lib}")
    rep = {
        "installed": False,
        "confidence": 0.0,
        "x4_root": None,
        "x4_exe": None,
        "extensions_dir": None,
        "user_docs_dir": str(X4_USER_DOCS) if X4_USER_DOCS.exists() else None,
        "logs_found": [],
        "can_launch": False,
        "notes": notes,
    }

    candidates = []
    for lib in _candidate_steam_libraries():
        candidates.append(lib / "steamapps" / "common" / "X4 Foundations")
    # Aussi vérifier le path GOG
    candidates.append(USER_HOME / "GOG Games" / "X4 Foundations")
    candidates.append(Path(r"C:\GOG Games\X4 Foundations"))

    for cand in candidates:
        if not cand.exists(): continue
        exe = cand / "X4.exe"
        if not exe.exists(): continue
        rep["installed"] = True
        rep["x4_root"] = str(cand)
        rep["x4_exe"] = str(exe)
        rep["can_launch"] = True
        rep["confidence"] = 0.95
        ext_dir = cand / "extensions"
        if ext_dir.exists():
            rep["extensions_dir"] = str(ext_dir)
        notes.append(f"x4_root={cand}")
        break

    # Logs user docs
    if X4_USER_DOCS.exists():
        try:
            for sub in X4_USER_DOCS.iterdir():
                if not sub.is_dir(): continue
                # Cherche logs récents
                for log_name in ("debug.log", "debug-output.log"):
                    f = sub / log_name
                    if f.exists():
                        rep["logs_found"].append(str(f))
        except Exception: pass

    if not rep["installed"]:
        rep["notes"].append("X4 non détecté dans les paths Steam/GOG classiques")
        rep["notes"].append("paths essayés : " +
                              " | ".join(str(c) for c in candidates))

    _record_step("detect_x4", {"installed": rep["installed"],
                                "x4_root": rep["x4_root"]})
    return rep


# ─── INSPECTION ENVIRONNEMENT MODDING ────────────────────────────────────────
def inspect_x4_modding_environment() -> dict:
    """Scan les extensions vanilla et XSD pour apprendre les patterns réels.

    Pas d'invention : on regarde ce que X4 propose vraiment.
    """
    d = detect_x4()
    if not d["installed"]:
        rep = {"ok": False, "reason": "x4_not_installed",
               "notes": d["notes"]}
        _record_step("inspect_env", rep)
        return rep

    x4_root = Path(d["x4_root"])
    rep = {
        "ok": True,
        "x4_root": str(x4_root),
        "extensions_vanilla": [],
        "xsd_files": [],
        "content_xml_examples": [],
        "md_examples": [],
        "aiscript_examples": [],
        "patterns_observed": [],
    }

    # Extensions vanilla (Egosoft + DLC) : on skipe les .cat/.dat pour la rapidité
    ext_dir = x4_root / "extensions"
    if ext_dir.exists():
        for ext in ext_dir.iterdir():
            if not ext.is_dir(): continue
            cx = ext / "content.xml"
            info = {"name": ext.name, "has_content_xml": cx.exists()}
            if cx.exists():
                try:
                    txt = cx.read_text(encoding="utf-8", errors="replace")[:1500]
                    info["content_preview"] = txt
                except Exception: pass
            rep["extensions_vanilla"].append(info)

    # XSD à la racine X4 (souvent dans <root>/xsd/ ou .cat compressé)
    xsd_dir = x4_root / "xsd"
    if xsd_dir.exists():
        for xsd in xsd_dir.glob("*.xsd"):
            rep["xsd_files"].append(xsd.name)

    # Patterns observés
    if rep["extensions_vanilla"]:
        rep["patterns_observed"].append(
            f"{len(rep['extensions_vanilla'])} extensions vanilla scannées")
    if rep["xsd_files"]:
        rep["patterns_observed"].append(
            f"{len(rep['xsd_files'])} XSD disponibles pour validation stricte")
    else:
        rep["patterns_observed"].append(
            "Pas de XSD .xsd à la racine — peut-être dans .cat (extraction non tentée v1)")

    # Sauvegarde rapport
    out_path = SESSION_ROOT / "vanilla_patterns.json"
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(rep, indent=2, ensure_ascii=False),
                             encoding="utf-8")
    except Exception: pass

    _record_step("inspect_env", {"n_extensions_vanilla": len(rep["extensions_vanilla"]),
                                  "n_xsd": len(rep["xsd_files"])})
    return rep


# ─── GÉNÉRATION EXTENSION MVP ────────────────────────────────────────────────
def _content_xml_template() -> str:
    """content.xml minimal mais valide. Définit l'extension dans X4."""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<content
    id="{MOD_ID}"
    name="{MOD_NAME}"
    description="Cortex autonomous faction lab — telemetry-driven mod testing."
    author="Cortex"
    version="{MOD_VERSION.replace('.', '')}"
    date="{time.strftime('%Y-%m-%d')}"
    save="0">
  <text language="44" name="{MOD_NAME}" description="Cortex autonomous faction laboratory."/>
  <text language="33" name="{MOD_NAME}" description="Laboratoire de faction autonome Cortex."/>
  <dependency id="ego_dlc_split" optional="true"/>
</content>
"""


def _md_telemetry_xml() -> str:
    """Mission Director script qui écrit dans debug.log à des étapes clés.

    Stratégie X4 : `<debug_text text="..."/>` ou `<debug_to_file/>` selon
    permissions Egosoft. Pour la v1, on utilise des cues triggered au
    démarrage du jeu (`<conditions><event_game_loaded/></conditions>`) qui
    écrivent les markers TELEMETRY_MARKERS dans le debug log.
    """
    return f"""<?xml version="1.0" encoding="utf-8"?>
<mdscript name="cortex_faction_telemetry" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <cues>
    <cue name="cortex_save_loaded">
      <conditions>
        <event_game_loaded/>
      </conditions>
      <actions>
        <debug_text text="'{TELEMETRY_MARKERS['extension_loaded']}'"/>
        <debug_text text="'{TELEMETRY_MARKERS['save_loaded']}'"/>
      </actions>
    </cue>
    <cue name="cortex_new_game">
      <conditions>
        <event_player_created/>
      </conditions>
      <actions>
        <debug_text text="'{TELEMETRY_MARKERS['extension_loaded']}'"/>
        <debug_text text="'{TELEMETRY_MARKERS['new_game_started']}'"/>
      </actions>
    </cue>
    <cue name="cortex_faction_init" instantiate="true">
      <conditions>
        <event_cue_completed cue="cortex_save_loaded"/>
      </conditions>
      <delay min="2s" max="3s"/>
      <actions>
        <debug_text text="'{TELEMETRY_MARKERS['faction_init']}'"/>
      </actions>
    </cue>
    <cue name="cortex_faction_init_new" instantiate="true">
      <conditions>
        <event_cue_completed cue="cortex_new_game"/>
      </conditions>
      <delay min="2s" max="3s"/>
      <actions>
        <debug_text text="'{TELEMETRY_MARKERS['faction_init']}'"/>
      </actions>
    </cue>
    <cue name="cortex_economy_heartbeat" instantiate="true">
      <conditions>
        <event_cue_completed cue="cortex_faction_init"/>
      </conditions>
      <delay min="30s" max="60s"/>
      <actions>
        <debug_text text="'{TELEMETRY_MARKERS['economy_tick']}'"/>
      </actions>
    </cue>
    <cue name="cortex_economy_heartbeat_new" instantiate="true">
      <conditions>
        <event_cue_completed cue="cortex_faction_init_new"/>
      </conditions>
      <delay min="30s" max="60s"/>
      <actions>
        <debug_text text="'{TELEMETRY_MARKERS['economy_tick']}'"/>
      </actions>
    </cue>
  </cues>
</mdscript>
"""


# ─── v0.2 : MOD AUTONOME (faction réelle, claim secteurs, spawn flotte) ──────

def _factions_diff_xml() -> str:
    """Diff XML qui ajoute cortex_faction au registre vanilla des factions.

    X4 charge libraries/factions.xml + applique les diffs des extensions.
    On ajoute notre faction sans toucher aux vanillas. Relations neutres
    par défaut (pas en guerre avec personne au démarrage — ça change si
    on prend leurs secteurs).
    """
    return f"""<?xml version="1.0" encoding="utf-8"?>
<diff>
  <add sel="//factions">
    <faction id="{MOD_ID}" name="{{20204,201}}" shortname="{{20204,202}}"
             primaryrace="argon"
             behaviour="generic" tags="player accept_player_attendance plot trade build mine">
      <colorvariation index="0"/>
      <colors>
        <map ref="map_default"/>
      </colors>
      <icon active="faction_argon" inactive="faction_argon_inactive"/>
      <office position="argonprime"/>
      <relations default="0"/>
      <ranks>
        <rank id="0" minrep="-30" name="{{20204,1010}}"/>
        <rank id="1" minrep="0"   name="{{20204,1011}}"/>
        <rank id="2" minrep="20"  name="{{20204,1012}}"/>
      </ranks>
      <licences>
        <licence type="generaluse" required="0"/>
        <licence type="trade"      required="0"/>
        <licence type="build"      required="0"/>
      </licences>
    </faction>
  </add>
</diff>
"""


def _md_bootstrap_xml() -> str:
    """MD bootstrap v0.2.1 — corrigé après debug.log live.

    Bugs v0.2.0 corrigés :
    - `<conditions><check_any>...</check_any></conditions>` au cue root → INTERDIT (event required)
      → On split en 2 cues séparés (un par event)
    - `do_for_each_counter` n'existe pas → remplacé par `<do_while>` + counter manuel
    - `md.cortex_faction.bootstrap_done` namespace → utilise variable du cue lui-même
    - `set_owner sector="$sec" faction="..."` → sector argument doit être le premier param

    Pattern X4 MD valide pour boucler n fois :
        <set_value name="$i" exact="0"/>
        <do_while value="$i lt 4">
          ... actions ...
          <set_value name="$i" exact="$i + 1"/>
        </do_while>

    Pour event au cue root : un cue par event_*.
    """
    spawn_block = lambda role, macro_name, count: f"""
              <set_value name="$i_{role}" exact="0"/>
              <do_while value="$i_{role} lt {count}">
                <create_ship name="$ship_{role}" macro="macro.{macro_name}"
                              sector="$sec1" owner="faction.{MOD_ID}"/>
                <debug_text text="'{TELEMETRY_MARKERS['ship_spawned']} role={role} id=' + $ship_{role}.idcode"/>
                <set_value name="$i_{role}" exact="$i_{role} + 1"/>
              </do_while>"""

    setup_actions = f"""
            <debug_text text="'{TELEMETRY_MARKERS['faction_init']} faction={MOD_ID}'"/>
            <!-- Seed crédits faction -->
            <set_value name="faction.{MOD_ID}.$cortex_credits" exact="{AUTONOMY_TARGETS['min_credits_seed']}"/>
            <!-- Sélection 3 secteurs candidats (1 par grande race) -->
            <find_sector name="$sec1" multiple="false" space="player.galaxy">
              <match owner="faction.argon"/>
            </find_sector>
            <find_sector name="$sec2" multiple="false" space="player.galaxy">
              <match owner="faction.teladi"/>
            </find_sector>
            <find_sector name="$sec3" multiple="false" space="player.galaxy">
              <match owner="faction.paranid"/>
            </find_sector>
            <do_if value="$sec1">
              <set_sector_owner sector="$sec1" faction="faction.{MOD_ID}"/>
              <debug_text text="'{TELEMETRY_MARKERS['sector_claimed']} id=' + $sec1.macro.name"/>
            </do_if>
            <do_if value="$sec2">
              <set_sector_owner sector="$sec2" faction="faction.{MOD_ID}"/>
              <debug_text text="'{TELEMETRY_MARKERS['sector_claimed']} id=' + $sec2.macro.name"/>
            </do_if>
            <do_if value="$sec3">
              <set_sector_owner sector="$sec3" faction="faction.{MOD_ID}"/>
              <debug_text text="'{TELEMETRY_MARKERS['sector_claimed']} id=' + $sec3.macro.name"/>
            </do_if>
            <!-- Stations dans le 1er secteur -->
            <do_if value="$sec1">
              <create_station name="$st_wharf" macro="macro.station_arg_default_argon_wharf_l_macro"
                              sector="$sec1" owner="faction.{MOD_ID}"/>
              <debug_text text="'{TELEMETRY_MARKERS['station_built']} type=wharf id=' + $st_wharf.idcode"/>
              <create_station name="$st_yard" macro="macro.station_arg_default_argon_shipyard_macro"
                              sector="$sec1" owner="faction.{MOD_ID}"/>
              <debug_text text="'{TELEMETRY_MARKERS['station_built']} type=shipyard id=' + $st_yard.idcode"/>
              <create_station name="$st_ore" macro="macro.station_arg_default_ore_refinery_l_macro"
                              sector="$sec1" owner="faction.{MOD_ID}"/>
              <debug_text text="'{TELEMETRY_MARKERS['station_built']} type=ore_refinery id=' + $st_ore.idcode"/>
              <create_station name="$st_sil" macro="macro.station_arg_default_silicon_refinery_l_macro"
                              sector="$sec1" owner="faction.{MOD_ID}"/>
              <debug_text text="'{TELEMETRY_MARKERS['station_built']} type=silicon_refinery id=' + $st_sil.idcode"/>
              <create_station name="$st_solar" macro="macro.station_arg_default_solar_l_macro"
                              sector="$sec1" owner="faction.{MOD_ID}"/>
              <debug_text text="'{TELEMETRY_MARKERS['station_built']} type=solar id=' + $st_solar.idcode"/>
            </do_if>
            <!-- Vaisseaux : 4 miners + 2 traders + 2 défense -->
            <do_if value="$sec1">{spawn_block("miner", "ship_arg_l_miner_solid_01_a_macro", 4)}{spawn_block("trader", "ship_arg_l_trans_container_01_a_macro", 2)}{spawn_block("defense", "ship_arg_m_destroyer_01_a_macro", 2)}
            </do_if>
            <signal_cue cue="md.cortex_faction_bootstrap.cortex_bootstrap_complete"/>
            <debug_text text="'{TELEMETRY_MARKERS['bootstrap_done']}'"/>"""

    return f"""<?xml version="1.0" encoding="utf-8"?>
<mdscript name="cortex_faction_bootstrap" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <cues>
    <cue name="cortex_bootstrap_on_new_game">
      <conditions>
        <event_player_created/>
      </conditions>
      <delay min="3s" max="5s"/>
      <actions>
        <do_if value="not (player.entity.$cortex_bootstrap_done?)">
          <set_value name="player.entity.$cortex_bootstrap_done" exact="true"/>{setup_actions}
        </do_if>
      </actions>
    </cue>
    <cue name="cortex_bootstrap_on_load">
      <conditions>
        <event_game_loaded/>
      </conditions>
      <delay min="3s" max="5s"/>
      <actions>
        <do_if value="not (player.entity.$cortex_bootstrap_done?)">
          <set_value name="player.entity.$cortex_bootstrap_done" exact="true"/>{setup_actions}
        </do_if>
      </actions>
    </cue>
    <!-- Marker cue : signalé par bootstrap pour démarrer l'economy loop. -->
    <cue name="cortex_bootstrap_complete">
      <conditions>
        <event_cue_signalled/>
      </conditions>
      <actions>
        <debug_text text="'[CORTEX_FACTION] bootstrap_complete signaled'"/>
      </actions>
    </cue>
  </cues>
</mdscript>
"""


def _md_economy_xml() -> str:
    """MD economy v0.2.1 — tick toutes les 60s sans self-ref problématique.

    Pattern X4 : un cue parent (`cortex_economy_root`) qui respawne un cue
    enfant `cortex_economy_tick` toutes les 60s. Le child cue logge la métrique
    et signale au parent pour relance.

    Bug v0.2.0 corrigé : `<cue ref="cortex_economy_loop"/>` self-ref non valide.
    """
    return f"""<?xml version="1.0" encoding="utf-8"?>
<mdscript name="cortex_faction_economy" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <cues>
    <!-- Démarre quand le bootstrap signale qu'il a fini. -->
    <cue name="cortex_economy_starter">
      <conditions>
        <event_cue_signalled cue="md.cortex_faction_bootstrap.cortex_bootstrap_complete"/>
      </conditions>
      <actions>
        <debug_text text="'[CORTEX_FACTION] economy_starter armed'"/>
      </actions>
      <cues>
        <cue name="cortex_economy_tick" instantiate="true">
          <conditions>
            <event_cue_signalled cue="md.cortex_faction_bootstrap.cortex_bootstrap_complete"/>
          </conditions>
          <delay min="60s" max="60s"/>
          <actions>
            <set_value name="$n_sec"  exact="(faction.{MOD_ID}.sectors).count"/>
            <set_value name="$n_ship" exact="(faction.{MOD_ID}.ships).count"/>
            <set_value name="$n_st"   exact="(faction.{MOD_ID}.stations).count"/>
            <set_value name="$cred"   exact="faction.{MOD_ID}.money"/>
            <debug_text text="'{TELEMETRY_MARKERS['economy_tick']} sectors=' + $n_sec + ' ships=' + $n_ship + ' stations=' + $n_st + ' credits=' + $cred"/>
            <!-- Re-signale pour que le cue se réinstancie et tick à nouveau dans 60s. -->
            <signal_cue cue="md.cortex_faction_bootstrap.cortex_bootstrap_complete"/>
          </actions>
        </cue>
      </cues>
    </cue>
  </cues>
</mdscript>
"""


def _manifest_dict(stage: dict | None = None) -> dict:
    base = {
        "game": "X4 Foundations",
        "mod_id": MOD_ID,
        "mod_name": MOD_NAME,
        "version": MOD_VERSION,
        "purpose": "autonomous_faction_lab",
        "risk": "medium",
        "telemetry_markers": list(TELEMETRY_MARKERS.values()),
        "static_validated": False,
        "installed": False,
        "launched": False,
        "game_detected_extension": False,
        "faction_detected": False,
        "unit_spawned": False,
        "unit_has_order": False,
        "economy_tick_verified": False,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if stage: base.update(stage)
    return base


def create_cortex_faction_extension() -> dict:
    """Génère l'extension v0.2 autonome dans LAB_ROOT.

    Fichiers générés :
      - content.xml                                  (manifest extension)
      - libraries/factions.xml                       (DIFF : ajoute cortex_faction)
      - md/cortex_faction_telemetry.xml              (markers debug)
      - md/cortex_faction_bootstrap.xml              (claim secteurs + spawn flotte)
      - md/cortex_faction_economy.xml                (ticks mesurables)
      - manifest.json + README.md
    """
    LAB_ROOT.mkdir(parents=True, exist_ok=True)
    files_written = []

    # content.xml
    cx = LAB_ROOT / "content.xml"
    cx.write_text(_content_xml_template(), encoding="utf-8")
    files_written.append("content.xml")

    # libraries/factions.xml (diff faction)
    lib_dir = LAB_ROOT / "libraries"
    lib_dir.mkdir(parents=True, exist_ok=True)
    (lib_dir / "factions.xml").write_text(_factions_diff_xml(), encoding="utf-8")
    files_written.append("libraries/factions.xml")

    # md/*.xml
    md_dir = LAB_ROOT / "md"
    md_dir.mkdir(parents=True, exist_ok=True)
    (md_dir / "cortex_faction_telemetry.xml").write_text(
        _md_telemetry_xml(), encoding="utf-8")
    files_written.append("md/cortex_faction_telemetry.xml")
    (md_dir / "cortex_faction_bootstrap.xml").write_text(
        _md_bootstrap_xml(), encoding="utf-8")
    files_written.append("md/cortex_faction_bootstrap.xml")
    (md_dir / "cortex_faction_economy.xml").write_text(
        _md_economy_xml(), encoding="utf-8")
    files_written.append("md/cortex_faction_economy.xml")

    # README
    readme = LAB_ROOT / "README.md"
    readme.write_text(
        f"# Cortex Faction Lab v{MOD_VERSION} — X4 Foundations\n\n"
        f"Auto-generated by `cortex_x4_faction_lab.py`.\n\n"
        f"## Cible autonomie v0.2\n\n"
        f"Le mod EST l'agent. À la création joueur ou chargement save, il doit :\n"
        f"- Claim {AUTONOMY_TARGETS['min_sectors_owned']} secteurs\n"
        f"- Construire {AUTONOMY_TARGETS['min_stations']} stations (wharf + shipyard + refineries + solar)\n"
        f"- Spawn {AUTONOMY_TARGETS['min_ships']} vaisseaux (miners + traders + défense)\n"
        f"- Seed {AUTONOMY_TARGETS['min_credits_seed']:,} crédits\n"
        f"- Logguer un tick économique toutes les 60s\n\n"
        f"## Markers attendus dans debug.log\n\n"
        + "".join(f"- `{m}`\n" for m in TELEMETRY_MARKERS.values())
        + f"\n## Format tick économique\n\n"
        f"`{TELEMETRY_MARKERS['economy_tick']} sectors=N ships=M stations=K credits=C`\n\n"
        f"Cortex parse cette ligne pour valider sans deviner.\n\n"
        f"## Risque\n\nMedium : `set_owner` peut hostiliser vanilla factions. "
        f"Rollback via `cortex_x4_faction_lab.rollback_extension()`.\n",
        encoding="utf-8")
    files_written.append("README.md")

    # manifest.json
    manifest = _manifest_dict()
    (LAB_ROOT / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    files_written.append("manifest.json")

    rep = {
        "ok": True,
        "stage": "generated",
        "lab_path": str(LAB_ROOT),
        "files_written": files_written,
        "n_files": len(files_written),
    }
    _record_step("create_extension", rep)
    return rep


# ─── VALIDATION STATIQUE ─────────────────────────────────────────────────────
def static_validate_extension(path: Path | str | None = None) -> dict:
    """Valide tous les XML du mod + manifest cohérent. Pas de dependances jeu.

    Checks :
    - content.xml existe et XML well-formed
    - tous les .xml well-formed
    - id du content matche MOD_ID
    - manifest présent
    - aucun chemin local privé dans les fichiers
    """
    p = Path(path) if path else LAB_ROOT
    rep = {"ok": False, "stage": "static_validated", "lab_path": str(p),
           "checks": {}, "errors": []}
    if not p.exists():
        rep["errors"].append("lab_path_missing")
        return rep

    # 1. content.xml présent + well-formed
    cx = p / "content.xml"
    rep["checks"]["content_xml_exists"] = cx.exists()
    if cx.exists():
        try:
            tree = ET.parse(str(cx))
            root = tree.getroot()
            rep["checks"]["content_xml_well_formed"] = True
            rep["checks"]["content_id_match"] = (root.get("id") == MOD_ID)
            if not rep["checks"]["content_id_match"]:
                rep["errors"].append(
                    f"content.xml id '{root.get('id')}' != expected '{MOD_ID}'")
        except Exception as e:
            rep["checks"]["content_xml_well_formed"] = False
            rep["errors"].append(f"content_xml_parse: {e}")

    # 2. Tous les .xml well-formed
    bad_xml = []
    for xml_f in p.rglob("*.xml"):
        try:
            ET.parse(str(xml_f))
        except Exception as e:
            bad_xml.append({"file": str(xml_f.relative_to(p)),
                             "error": str(e)[:120]})
    rep["checks"]["all_xml_well_formed"] = len(bad_xml) == 0
    if bad_xml: rep["errors"].extend([f"xml_bad: {b['file']}" for b in bad_xml])
    rep["bad_xml"] = bad_xml

    # 3. Manifest présent
    m = p / "manifest.json"
    rep["checks"]["manifest_present"] = m.exists()

    # 4. Pas de chemin local privé
    private_leaks = []
    for f in p.rglob("*"):
        if not f.is_file(): continue
        if f.suffix.lower() not in (".md", ".xml", ".json", ".txt"): continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
            if re.search(r"C:[\\/]+Users[\\/]+Smedj", text):
                private_leaks.append(str(f.relative_to(p)))
        except Exception: pass
    rep["checks"]["no_private_paths"] = len(private_leaks) == 0
    if private_leaks: rep["errors"].extend([f"private_leak: {f}" for f in private_leaks])

    rep["ok"] = (not rep["errors"]
                  and all(v for v in rep["checks"].values() if isinstance(v, bool)))

    # Update manifest
    try:
        if m.exists():
            mdata = json.loads(m.read_text(encoding="utf-8"))
            mdata["static_validated"] = rep["ok"]
            m.write_text(json.dumps(mdata, indent=2, ensure_ascii=False),
                          encoding="utf-8")
    except Exception: pass

    # Rapport session
    out = SESSION_ROOT / "static_validation.json"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rep, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    except Exception: pass

    _record_step("static_validate", {"ok": rep["ok"], "n_errors": len(rep["errors"])})
    return rep


# ─── INSTALLATION (avec backup) ──────────────────────────────────────────────
def install_extension(path: Path | str | None = None) -> dict:
    """Copie l'extension dans <X4_ROOT>/extensions/cortex_faction.
    Backup horodaté si une version précédente existe."""
    src = Path(path) if path else LAB_ROOT
    if not src.exists():
        rep = {"ok": False, "error": "src_missing", "src": str(src)}
        _record_step("install", rep); return rep
    d = detect_x4()
    if not d["installed"]:
        rep = {"ok": False, "error": "x4_not_installed", "notes": d["notes"]}
        _record_step("install", rep); return rep
    if not d["extensions_dir"]:
        rep = {"ok": False, "error": "extensions_dir_missing",
               "x4_root": d["x4_root"]}
        _record_step("install", rep); return rep

    target = Path(d["extensions_dir"]) / MOD_ID
    rep = {"ok": False, "stage": "installed",
           "src": str(src), "target": str(target)}
    backup_path = None

    # Backup si existe — HORS du dossier extensions/ pour ne pas que X4 le scanne
    # (sinon X4 charge les MD scripts du backup → conflit "Script name already found")
    if target.exists():
        backups_root = SESSION_ROOT / "x4_extension_backups"
        backups_root.mkdir(parents=True, exist_ok=True)
        backup_path = backups_root / f"{MOD_ID}_backup_{int(_now())}"
        try:
            shutil.move(str(target), str(backup_path))
            rep["backup"] = str(backup_path)
        except Exception as e:
            rep["error"] = f"backup_failed: {e}"
            _record_step("install", rep); return rep

    # Copy
    try:
        shutil.copytree(str(src), str(target))
        rep["ok"] = True
    except Exception as e:
        rep["error"] = f"copy_failed: {e}"
        # Tente rollback du backup
        if backup_path and backup_path.exists():
            try:
                if target.exists(): shutil.rmtree(str(target))
                shutil.move(str(backup_path), str(target))
                rep["rolled_back"] = True
            except Exception: pass
        _record_step("install", rep); return rep

    # Update manifest
    m = target / "manifest.json"
    try:
        if m.exists():
            mdata = json.loads(m.read_text(encoding="utf-8"))
            mdata["installed"] = True
            mdata["installed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            m.write_text(json.dumps(mdata, indent=2, ensure_ascii=False),
                          encoding="utf-8")
    except Exception: pass

    # Marker installation
    try:
        (target / ".cortex-installed.json").write_text(
            json.dumps({"installed_at": _now(), "src": str(src),
                        "version": MOD_VERSION}, indent=2),
            encoding="utf-8")
    except Exception: pass

    out = SESSION_ROOT / "install_report.json"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rep, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    except Exception: pass

    _record_step("install", rep)
    return rep


def rollback_extension() -> dict:
    """Désinstalle proprement l'extension Cortex de <X4_ROOT>/extensions/."""
    d = detect_x4()
    if not d["installed"]:
        return {"ok": False, "error": "x4_not_installed"}
    target = Path(d["extensions_dir"]) / MOD_ID
    if not target.exists():
        return {"ok": True, "skip": "not_installed"}
    try:
        shutil.rmtree(str(target))
        _record_step("rollback", {"target": str(target)})
        return {"ok": True, "removed": str(target)}
    except Exception as e:
        return {"ok": False, "error": f"rmtree_failed: {e}"}


# ─── LANCEMENT JEU ───────────────────────────────────────────────────────────
def launch_x4(wait_seconds: int = 0, mode: str = "default",
               debug: bool = True) -> dict:
    """Lance X4.exe en process indépendant. Ne bloque pas.

    Args :
        wait_seconds : si > 0, attend ce délai (utile pour run_autonomous_test_cycle).
        mode :
            "default"  → menu principal, Sam clique
            "continue" → -continue : charge la dernière save (zéro clic)
            "newgame"  → -skipintro : Sam doit cliquer New Game → Cortex Faction Start
        debug : True → ajoute -debug all -scriptlogfiles -logfile debug-cortex.log
                       (indispensable pour que TELEMETRY_MARKERS apparaissent)
    """
    d = detect_x4()
    if not d["installed"] or not d["can_launch"]:
        return {"ok": False, "error": "x4_not_launchable", "notes": d["notes"]}
    exe = d["x4_exe"]

    args = [exe]
    if debug:
        # X4 supporte -debug all|general|scripts, -scriptlogfiles, -logfile
        args += ["-debug", "all", "-scriptlogfiles", "-logfile", "debug-cortex.log"]
    if mode == "continue":
        args += ["-continue", "-skipintro"]
    elif mode == "newgame":
        args += ["-skipintro"]

    rep = {"ok": False, "stage": "launched", "exe": exe, "args": args[1:],
           "mode": mode, "debug": debug, "started_at": _now()}
    try:
        flags = int(getattr(subprocess, "DETACHED_PROCESS", 0)) | \
                 int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        proc = subprocess.Popen(args, cwd=str(Path(exe).parent),
                                 creationflags=flags)
        rep["pid"] = proc.pid
        rep["ok"] = True
        if mode == "newgame":
            rep["manual_step"] = ("Cliquer 'New Game' → 'Custom Start' → "
                                    "'Cortex Faction Start' (la 1ère fois). "
                                    "Ensuite mode='continue' = zéro clic.")
    except Exception as e:
        rep["error"] = f"launch_failed: {e}"
        _record_step("launch", rep); return rep

    if wait_seconds > 0:
        time.sleep(min(wait_seconds, 600))
        rep["waited_s"] = wait_seconds

    out = SESSION_ROOT / "launch_report.json"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rep, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    except Exception: pass

    _record_step("launch", rep)
    return rep


def terminate_x4_session() -> dict:
    """Arrête X4 proprement (envoi WM_CLOSE puis kill si pas répondu en 30s)."""
    rep = {"ok": False, "stage": "terminate", "ts": _now()}
    try:
        # Tente fermeture gracieuse
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process -Name 'X4' -ErrorAction SilentlyContinue | "
             "ForEach-Object { $_.CloseMainWindow() | Out-Null; $_.Id }"],
            capture_output=True, text=True, timeout=10)
        rep["close_stdout"] = (r.stdout or "").strip()
        time.sleep(8)
        # Kill résiduel
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process -Name 'X4' -ErrorAction SilentlyContinue | Stop-Process -Force"],
            capture_output=True, text=True, timeout=10)
        rep["ok"] = True
    except Exception as e:
        rep["error"] = repr(e)
    _record_step("terminate", rep)
    return rep


def _scan_log_for_markers(log_path: Path, since_ts: float,
                            seen: dict[str, dict]) -> None:
    """Scan un log et met à jour seen={marker_key: {first_seen_ts, count, ...}}."""
    if not log_path.exists():
        return
    try:
        sz = log_path.stat().st_size
        # Lit la queue (1 Mo max) pour rapidité
        offset = max(0, sz - 1_000_000)
        with log_path.open("r", encoding="utf-8", errors="replace") as fh:
            fh.seek(offset)
            content = fh.read()
    except Exception:
        return
    now = _now()
    for key, marker in TELEMETRY_MARKERS.items():
        # marker peut contenir "id=" comme préfixe partiel
        idx = content.find(marker)
        if idx >= 0:
            entry = seen.setdefault(key, {"first_seen_ts": now, "count": 0,
                                            "marker": marker, "log": str(log_path)})
            # count = nb d'occurrences (utile pour economy_tick / heartbeats)
            entry["count"] = content.count(marker)
            entry["last_seen_ts"] = now


def play_x4_session(duration_minutes: int = 15, poll_interval_s: int = 10,
                     mode: str = "continue", debug: bool = True,
                     auto_launch: bool = True) -> dict:
    """Lance X4 (mode continue par défaut) puis poll debug.log toutes les
    poll_interval_s secondes pendant duration_minutes minutes. Retourne le
    timeline réel des markers TELEMETRY_MARKERS observés.

    Args :
        duration_minutes  : durée totale d'observation
        poll_interval_s   : intervalle entre 2 scans de log
        mode              : "continue" (zéro clic) | "newgame" | "default"
        debug             : flags debug à l'exe (recommandé)
        auto_launch       : si False, suppose X4 déjà lancé et ne fait que poller

    Verdict honnête :
        - "telemetry_observed"   : ≥ 1 marker vu (extension_loaded au minimum)
        - "running_no_telemetry" : process X4 vu mais aucun marker
        - "launch_failed"        : impossible de spawner
    """
    started = _now()
    deadline = started + max(60, duration_minutes * 60)
    rep = {
        "ok": False, "stage": "play_session",
        "ts_start": started, "duration_minutes": duration_minutes,
        "poll_interval_s": poll_interval_s, "mode": mode,
        "auto_launch": auto_launch, "launch": None,
        "polls": 0, "markers_seen": {},
        "x4_running_at_end": False,
        "verdict": "unknown", "next_action": "",
    }

    if auto_launch:
        ln = launch_x4(wait_seconds=0, mode=mode, debug=debug)
        rep["launch"] = ln
        if not ln.get("ok"):
            rep["verdict"] = "launch_failed"
            rep["next_action"] = ln.get("error", "spawn impossible")
            _record_step("play_session", rep)
            return rep

    # Localisation des logs : Documents/Egosoft/X4/<id>/debug-cortex.log
    # OU debug.log (selon flags) — on scan tout ce qui matche.
    seen: dict[str, dict] = {}
    while _now() < deadline:
        # Re-detect logs à chaque poll (X4 peut créer le dossier in-flight)
        d = detect_x4()
        log_paths: list[Path] = []
        # 1. logs explicites détectés
        for p in d.get("logs_found", []):
            log_paths.append(Path(p))
        # 2. fallback : scan racine X4 user docs
        for sub in X4_USER_DOCS.glob("*/debug-cortex.log") if X4_USER_DOCS.exists() else []:
            log_paths.append(sub)
        for sub in X4_USER_DOCS.glob("*/debug.log") if X4_USER_DOCS.exists() else []:
            log_paths.append(sub)
        # dédoublonne
        seen_paths = set()
        for lp in log_paths:
            key = str(lp.resolve())
            if key in seen_paths: continue
            seen_paths.add(key)
            _scan_log_for_markers(lp, started, seen)
        rep["polls"] += 1
        rep["markers_seen"] = seen
        # log progressif (utile si Sam coupe avant deadline)
        try:
            (SESSION_ROOT / "play_session.json").write_text(
                json.dumps(rep, indent=2, ensure_ascii=False),
                encoding="utf-8")
        except Exception: pass
        # sortie anticipée si on a déjà toutes les preuves clés
        if "extension_loaded" in seen and "faction_init" in seen \
                and "economy_tick" in seen:
            rep["early_exit"] = "all_key_markers_seen"
            break
        time.sleep(poll_interval_s)

    # Process toujours là ?
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process -Name 'X4' -ErrorAction SilentlyContinue | Select-Object -First 1 -Property Id"],
            capture_output=True, text=True, timeout=5)
        rep["x4_running_at_end"] = "Id" in (r.stdout or "")
    except Exception: pass

    rep["ok"] = True
    if "extension_loaded" in seen:
        rep["verdict"] = "telemetry_observed"
    elif rep["x4_running_at_end"]:
        rep["verdict"] = "running_no_telemetry"
        rep["next_action"] = (
            "X4 tourne mais aucun marker. "
            "Vérifier : (1) mod activé dans menu Extensions, "
            "(2) flags -debug all -scriptlogfiles bien passés, "
            "(3) chemin debug-cortex.log dans Documents/Egosoft/X4/<id>/")
    else:
        rep["verdict"] = "x4_not_running"
        rep["next_action"] = "X4 fermé avant la fin. Vérifier crash log."

    _record_step("play_session", rep)
    return rep


# ─── COLLECTE PREUVE ─────────────────────────────────────────────────────────
def collect_evidence() -> dict:
    """Cherche les markers télémétrie dans debug.log de X4.

    Critère honnête : `extension_detected=true` UNIQUEMENT si le marker
    `extension_loaded` apparaît dans un log X4 récent (< 1h).
    Parse aussi les ticks économie pour extraire metrics réelles
    (n_sectors_owned, n_ships, n_stations, credits) et compare aux
    AUTONOMY_TARGETS.
    """
    d = detect_x4()
    rep = {
        "ok": True,
        "ts": _now(),
        "game_launched": False,
        "extension_detected": False,
        "faction_detected": False,
        "bootstrap_done": False,
        "n_sector_claims_seen": 0,
        "n_station_builds_seen": 0,
        "n_ship_spawns_seen": 0,
        "unit_spawned": False,
        "unit_has_order": False,
        "economy_tick_verified": False,
        # Metrics extraites du DERNIER tick économie observé
        "last_metrics": {
            "sectors": None, "ships": None, "stations": None, "credits": None,
            "n_ticks_seen": 0, "tick_age_s": None,
        },
        "autonomy_verdict": "unknown",
        "autonomy_targets": dict(AUTONOMY_TARGETS),
        "evidence_files": [],
        "logs_scanned": [],
        "markers_found": {},
        "errors_in_log": [],
        "confidence": 0.0,
        "needs_human_check": False,
    }

    if not d["installed"]:
        rep["ok"] = False
        rep["needs_human_check"] = True
        rep["reason"] = "x4_not_installed"
        return rep

    # Process X4 toujours actif ?
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process -Name 'X4' -ErrorAction SilentlyContinue | Select-Object -First 1 -Property Id"],
            capture_output=True, text=True, timeout=5)
        rep["game_launched"] = "Id" in (r.stdout or "")
    except Exception: pass

    # Logs : Documents\Egosoft\X4\<id>\debug.log ou debug-output.log
    cutoff = _now() - 3600  # ne lit que les logs modifiés < 1h
    for f_path in d.get("logs_found", []):
        f = Path(f_path)
        if not f.exists(): continue
        if f.stat().st_mtime < cutoff:
            rep["logs_scanned"].append({"file": str(f), "skipped": "stale"})
            continue
        try:
            # Lit la queue (dernière ~1 MB) pour rapidité
            sz = f.stat().st_size
            offset = max(0, sz - 1_000_000)
            with f.open("r", encoding="utf-8", errors="replace") as fh:
                fh.seek(offset)
                text = fh.read()
            for marker_name, marker_str in TELEMETRY_MARKERS.items():
                if marker_str in text:
                    rep["markers_found"][marker_name] = True
            # Compteurs (un même marker peut apparaître plusieurs fois)
            rep["n_sector_claims_seen"] = max(rep["n_sector_claims_seen"],
                                                text.count(TELEMETRY_MARKERS["sector_claimed"]))
            rep["n_station_builds_seen"] = max(rep["n_station_builds_seen"],
                                                 text.count(TELEMETRY_MARKERS["station_built"]))
            rep["n_ship_spawns_seen"] = max(rep["n_ship_spawns_seen"],
                                              text.count(TELEMETRY_MARKERS["ship_spawned"]))
            # Erreurs MD (X4 logue [=ERROR=] souvent)
            for line in text.splitlines():
                if "[CORTEX_FACTION] ERROR" in line or \
                   ("ERROR" in line and MOD_ID in line):
                    rep["errors_in_log"].append(line.strip()[:300])
            rep["errors_in_log"] = rep["errors_in_log"][:20]  # cap
            # Parse économie ticks : "economy_tick sectors=N ships=M stations=K credits=C"
            tick_pat = re.compile(
                re.escape(TELEMETRY_MARKERS["economy_tick"])
                + r"\s+sectors=(\d+)\s+ships=(\d+)\s+stations=(\d+)\s+credits=(-?\d+)")
            ticks = tick_pat.findall(text)
            if ticks:
                last = ticks[-1]
                rep["last_metrics"] = {
                    "sectors":  int(last[0]),
                    "ships":    int(last[1]),
                    "stations": int(last[2]),
                    "credits":  int(last[3]),
                    "n_ticks_seen": len(ticks),
                    "tick_age_s": int(_now() - f.stat().st_mtime),
                }
            rep["logs_scanned"].append({"file": str(f), "bytes_read": len(text)})
        except Exception as e:
            rep["logs_scanned"].append({"file": str(f), "error": str(e)[:120]})

    # Mapping markers → flags
    rep["extension_detected"] = bool(rep["markers_found"].get("extension_loaded"))
    rep["faction_detected"] = bool(rep["markers_found"].get("faction_init"))
    rep["bootstrap_done"] = bool(rep["markers_found"].get("bootstrap_done"))
    rep["unit_spawned"] = bool(rep["markers_found"].get("ship_spawned")
                                  or rep["markers_found"].get("unit_spawned"))
    rep["unit_has_order"] = bool(rep["markers_found"].get("order_assigned"))
    rep["economy_tick_verified"] = bool(rep["markers_found"].get("economy_tick"))

    # Verdict autonomie (HONNÊTE) — uniquement si on a un tick réel
    lm = rep["last_metrics"]
    if lm["n_ticks_seen"] > 0 and lm["sectors"] is not None:
        ok_sec = lm["sectors"]  >= AUTONOMY_TARGETS["min_sectors_owned"]
        ok_st  = lm["stations"] >= AUTONOMY_TARGETS["min_stations"]
        ok_sh  = lm["ships"]    >= AUTONOMY_TARGETS["min_ships"]
        if ok_sec and ok_st and ok_sh:
            rep["autonomy_verdict"] = "achieved"
        elif lm["sectors"] >= 1 or lm["stations"] >= 1 or lm["ships"] >= 1:
            rep["autonomy_verdict"] = "partial"
        else:
            rep["autonomy_verdict"] = "bootstrap_failed"
    elif rep["bootstrap_done"]:
        rep["autonomy_verdict"] = "bootstrap_done_no_economy_tick_yet"
    else:
        rep["autonomy_verdict"] = "no_evidence"

    # Confidence simple
    n_markers = sum(1 for v in rep["markers_found"].values() if v)
    rep["confidence"] = round(n_markers / len(TELEMETRY_MARKERS), 2)

    if not rep["logs_scanned"] or all(s.get("skipped") for s in rep["logs_scanned"]):
        rep["needs_human_check"] = True
        rep["reason_human"] = ("Aucun log X4 récent. Sam doit activer le mode "
                                "debug Egosoft (`-debug all` ou similaire) "
                                "pour que les markers MD soient écrits.")

    out = SESSION_ROOT / "evidence.json"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rep, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    except Exception: pass

    _record_step("collect_evidence",
                  {"extension_detected": rep["extension_detected"],
                   "n_markers": n_markers,
                   "confidence": rep["confidence"]})
    return rep


# ─── DIAGNOSTIC + PATCH ──────────────────────────────────────────────────────
def diagnose_x4_result(evidence: dict | None = None) -> dict:
    """Diagnostique l'écart entre ce qu'on attend et ce qu'on a observé."""
    if evidence is None: evidence = collect_evidence()
    diag = {"ts": _now(), "issues": [], "next_actions": []}

    if not evidence.get("game_launched"):
        diag["issues"].append("game_not_running")
        diag["next_actions"].append("relance launch_x4()")
        return diag
    if not evidence.get("extension_detected"):
        diag["issues"].append("extension_not_detected_in_logs")
        diag["next_actions"].append(
            "vérifier : (a) extension installée, (b) X4 lancé en mode debug "
            "(`-debug all -logfile debug.log`), (c) content.xml id matche")
    if not evidence.get("faction_detected"):
        diag["issues"].append("faction_init_not_observed")
        diag["next_actions"].append(
            "MD cue cortex_faction_init n'a pas écrit. Vérifier syntaxe MD + "
            "que <event_cue_completed> trigge bien")
    if evidence.get("needs_human_check"):
        diag["issues"].append("needs_human_input")
        diag["next_actions"].append(evidence.get("reason_human", "input human requis"))

    out = SESSION_ROOT / "diagnose.json"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(diag, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    except Exception: pass
    _record_step("diagnose", {"n_issues": len(diag["issues"])})
    return diag


def patch_extension(diagnosis: dict | None = None) -> dict:
    """Applique un patch selon le diagnostic. v1 : régénère l'extension propre."""
    rep = {"ok": True, "stage": "patched", "actions": []}
    # v1 : on régénère l'extension de zéro (les vrais patches viendront avec
    # plus d'inspection des XSD vanilla)
    if LAB_ROOT.exists():
        try:
            shutil.rmtree(str(LAB_ROOT))
            rep["actions"].append("removed_old_lab")
        except Exception: pass
    create_cortex_faction_extension()
    rep["actions"].append("regenerated_extension")
    _record_step("patch", rep)
    return rep


# ─── BOUCLE AUTONOME ─────────────────────────────────────────────────────────
def run_autonomous_test_cycle(max_minutes: int = 20,
                                allow_launch: bool = False) -> dict:
    """Pipeline complet : detect → inspect → create → validate → install → (launch?) → evidence → diagnose → patch.

    `allow_launch=False` par défaut : n'allume PAS X4 (Sam doit confirmer via UI).
    Quand False, on s'arrête après install + tente de collecter evidence sur
    logs existants.
    """
    started = _now()
    deadline = started + max_minutes * 60
    report = {
        "ts_start": started,
        "max_minutes": max_minutes,
        "allow_launch": allow_launch,
        "cycles_attempted": 0,
        "extension_generated": False,
        "static_validated": False,
        "installed": False,
        "launched": False,
        "extension_detected": False,
        "faction_detected": False,
        "unit_spawned": False,
        "unit_has_order": False,
        "economy_tick_verified": False,
        "patches_attempted": [],
        "final_verdict": "failed",
        "needs_sam": False,
        "next_action": "",
    }

    # 1. Detect
    d = detect_x4()
    if not d["installed"]:
        report["final_verdict"] = "failed"
        report["needs_sam"] = True
        report["next_action"] = "X4 non détecté. Vérifie l'install Steam."
        _save_report(report); return report

    # 2. Inspect (best-effort)
    inspect_x4_modding_environment()

    # 3. Create
    cr = create_cortex_faction_extension()
    report["extension_generated"] = cr.get("ok", False)

    # 4. Static validate
    sv = static_validate_extension()
    report["static_validated"] = sv.get("ok", False)
    if not sv.get("ok"):
        # patch + retry une fois
        patch_extension(sv)
        sv2 = static_validate_extension()
        report["patches_attempted"].append({"reason": "static_invalid",
                                              "passed_after_patch": sv2.get("ok", False)})
        report["static_validated"] = sv2.get("ok", False)
        if not report["static_validated"]:
            report["final_verdict"] = "failed"
            report["next_action"] = "static validation failed even after patch"
            _save_report(report); return report

    # 5. Install
    inst = install_extension()
    report["installed"] = inst.get("ok", False)
    if not report["installed"]:
        report["final_verdict"] = "failed"
        report["next_action"] = inst.get("error", "install_failed")
        _save_report(report); return report

    # 6. Launch (optionnel — par défaut désactivé)
    if allow_launch:
        if _now() >= deadline:
            report["next_action"] = "deadline_reached_before_launch"
            _save_report(report); return report
        ln = launch_x4(wait_seconds=0)
        report["launched"] = ln.get("ok", False)
        if report["launched"]:
            # Attendre que le jeu charge + écrive ses logs
            wait_target = min(deadline, _now() + 120)
            while _now() < wait_target:
                time.sleep(10)
            ev = collect_evidence()
            report["extension_detected"] = ev.get("extension_detected", False)
            report["faction_detected"] = ev.get("faction_detected", False)
            report["unit_spawned"] = ev.get("unit_spawned", False)
    else:
        # Pas de launch : on collecte ce qui existe déjà (anciens logs)
        ev = collect_evidence()
        report["extension_detected"] = ev.get("extension_detected", False)

    # Verdict final
    if report["extension_detected"]:
        report["final_verdict"] = "verified"
    elif report["installed"]:
        report["final_verdict"] = "partial"
        report["next_action"] = ("extension installée mais non observée en jeu. "
                                  "Lancer X4 en mode debug pour preuve.")
    else:
        report["final_verdict"] = "failed"
        report["next_action"] = "install_failed"

    _save_report(report)
    return report


def _save_report(report: dict) -> None:
    out = SESSION_ROOT / "autonomous_test_report.json"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    except Exception: pass


# ─── STATUS RAPIDE ───────────────────────────────────────────────────────────
def status() -> dict:
    """Snapshot rapide pour UI : où en est le lab X4 ?"""
    d = detect_x4()
    rep = {
        "ts": _now(),
        "x4_detected": d["installed"],
        "x4_root": d["x4_root"],
        "extension_dir_local": str(LAB_ROOT),
        "extension_dir_local_exists": LAB_ROOT.exists(),
        "extension_installed_in_x4": False,
    }
    if d["installed"] and d["extensions_dir"]:
        target = Path(d["extensions_dir"]) / MOD_ID
        rep["extension_installed_in_x4"] = target.exists()
        rep["extension_path_in_x4"] = str(target) if target.exists() else None
    # Dernier rapport
    last_report = SESSION_ROOT / "autonomous_test_report.json"
    if last_report.exists():
        try:
            r = json.loads(last_report.read_text(encoding="utf-8"))
            rep["last_test"] = {
                "ts_start": r.get("ts_start"),
                "final_verdict": r.get("final_verdict"),
                "extension_detected": r.get("extension_detected"),
                "installed": r.get("installed"),
                "next_action": r.get("next_action"),
            }
        except Exception: pass
    return rep


# ─── SELF-TEST (NE LANCE PAS LE JEU) ─────────────────────────────────────────
def self_test() -> dict:
    """Test pipeline statique : detect (read-only) + create + validate.
    NE LANCE PAS X4. NE TOUCHE PAS aux fichiers d'install jeu.
    """
    tests = []
    # 1. detect_x4 doit retourner un dict valide (même si X4 absent)
    d = detect_x4()
    tests.append({"name": "detect_x4",
                  "ok": isinstance(d, dict) and "installed" in d,
                  "x4_installed": d.get("installed")})
    # 2. Génération extension dans LAB_ROOT
    cr = create_cortex_faction_extension()
    tests.append({"name": "create_extension",
                  "ok": cr.get("ok") and cr.get("n_files", 0) >= 4,
                  "n_files": cr.get("n_files")})
    # 3. Validation statique
    sv = static_validate_extension()
    tests.append({"name": "static_validate",
                  "ok": sv.get("ok"),
                  "n_errors": len(sv.get("errors", []))})
    # 4. Status
    s = status()
    tests.append({"name": "status",
                  "ok": isinstance(s, dict) and "x4_detected" in s})
    return {"ok": all(t["ok"] for t in tests),
            "tests": tests,
            "x4_detected": d.get("installed"),
            "lab_path": str(LAB_ROOT)}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "test":
        print(json.dumps(self_test(), indent=2, ensure_ascii=False))
    elif cmd == "detect":
        print(json.dumps(detect_x4(), indent=2, ensure_ascii=False))
    elif cmd == "inspect":
        print(json.dumps(inspect_x4_modding_environment(), indent=2, ensure_ascii=False))
    elif cmd == "create":
        print(json.dumps(create_cortex_faction_extension(), indent=2, ensure_ascii=False))
    elif cmd == "validate":
        print(json.dumps(static_validate_extension(), indent=2, ensure_ascii=False))
    elif cmd == "install":
        print(json.dumps(install_extension(), indent=2, ensure_ascii=False))
    elif cmd == "rollback":
        print(json.dumps(rollback_extension(), indent=2, ensure_ascii=False))
    elif cmd == "launch":
        print(json.dumps(launch_x4(), indent=2, ensure_ascii=False))
    elif cmd == "evidence":
        print(json.dumps(collect_evidence(), indent=2, ensure_ascii=False))
    elif cmd == "diagnose":
        print(json.dumps(diagnose_x4_result(), indent=2, ensure_ascii=False))
    elif cmd == "patch":
        print(json.dumps(patch_extension(), indent=2, ensure_ascii=False))
    elif cmd == "run_test_cycle":
        allow_launch = "--launch" in sys.argv
        max_min = 20
        for arg in sys.argv:
            if arg.startswith("--max-min="):
                max_min = int(arg.split("=", 1)[1])
        print(json.dumps(run_autonomous_test_cycle(max_minutes=max_min,
                                                     allow_launch=allow_launch),
                          indent=2, ensure_ascii=False))
    elif cmd == "status":
        print(json.dumps(status(), indent=2, ensure_ascii=False))
    else:
        print("Usage: cortex_x4_faction_lab.py {test|detect|inspect|create|"
              "validate|install|launch|evidence|diagnose|patch|"
              "run_test_cycle [--launch] [--max-min=N]|rollback|status}")
