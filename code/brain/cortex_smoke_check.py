"""
cortex_smoke_check.py — CI locale, zéro dépendance externe, zéro quota.

GitHub Actions est en panne pour billing → on remplace par un smoke check
local qui tourne :
1. Manuellement : `python scripts/brain/cortex_smoke_check.py`
2. Automatiquement avant `cortex_publishing.update()` (hook bloquant)

Politique :
- **strict-core** : modules cognitifs cœur (activation, active_inference,
  anti_fake, action_effects, homeostasis). Doivent compiler + importer +
  self_test. Tout échec → exit 1, abort publish.
- **smoke-rest** : autres modules. Compile only, tolérant. Échec ne bloque pas.

Sortie : JSON + exit code 0 (ok) ou 1 (strict failed).
"""
from __future__ import annotations
import json
import py_compile
import sys
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parent

# Modules cœur — leurs erreurs cassent la publication
CORE_MODULES = [
    "cortex_activation",
    "cortex_active_inference",
    "cortex_anti_fake",
    "cortex_action_effects",
    "cortex_homeostasis",
]

# Modules pour lesquels self_test() existe et doit retourner ok=True
CORE_SELF_TEST = [
    "cortex_active_inference",
    "cortex_action_effects",
]


def _check_compile(name: str) -> dict:
    path = REPO / f"{name}.py"
    if not path.exists():
        return {"ok": False, "error": f"file missing: {path.name}"}
    try:
        py_compile.compile(str(path), doraise=True)
        return {"ok": True}
    except py_compile.PyCompileError as e:
        return {"ok": False, "error": f"py_compile: {e}"[:300]}
    except Exception as e:
        return {"ok": False, "error": f"unexpected: {e}"[:300]}


def _check_import(name: str) -> dict:
    try:
        if str(REPO) not in sys.path:
            sys.path.insert(0, str(REPO))
        # Force fresh import
        if name in sys.modules:
            del sys.modules[name]
        __import__(name)
        return {"ok": True}
    except Exception as e:
        return {"ok": False,
                "error": f"import: {type(e).__name__}: {e}"[:300],
                "traceback": traceback.format_exc()[-500:]}


def _check_self_test(name: str) -> dict:
    try:
        if str(REPO) not in sys.path:
            sys.path.insert(0, str(REPO))
        mod = __import__(name)
        st = getattr(mod, "self_test", None)
        if not callable(st):
            return {"ok": True, "skipped": "no self_test()"}
        r = st()
        if isinstance(r, dict) and r.get("ok") is True:
            return {"ok": True, "result_summary": str(r)[:200]}
        return {"ok": False, "error": "self_test returned non-ok",
                "result": str(r)[:300]}
    except Exception as e:
        return {"ok": False,
                "error": f"self_test exception: {type(e).__name__}: {e}"[:300],
                "traceback": traceback.format_exc()[-500:]}


def _scan_rest_compile() -> dict:
    """Compile-only sur tous les autres cortex_*.py — tolérant.

    Retourne le compte total / passed / failed mais n'affecte PAS le verdict
    global (strict-only).
    """
    total = passed = failed = 0
    failures = []
    for path in sorted(REPO.glob("cortex_*.py")):
        name = path.stem
        if name in CORE_MODULES: continue
        total += 1
        r = _check_compile(name)
        if r.get("ok"):
            passed += 1
        else:
            failed += 1
            failures.append({"module": name, "error": r.get("error")})
    return {"total": total, "passed": passed, "failed": failed,
            "failures": failures[:5]}  # cap


def run() -> dict:
    """Lance le smoke check complet. Returns dict + sets exit_code in caller."""
    report = {
        "strict_core": {},
        "smoke_rest": None,
        "verdict": "ok",
        "n_strict_passed": 0,
        "n_strict_failed": 0,
    }

    # 1. Strict core : compile + import + self_test
    for name in CORE_MODULES:
        m = {"compile": _check_compile(name)}
        if m["compile"]["ok"]:
            m["import"] = _check_import(name)
            if m["import"]["ok"] and name in CORE_SELF_TEST:
                m["self_test"] = _check_self_test(name)
        all_ok = all(stage.get("ok") for stage in m.values())
        m["all_ok"] = all_ok
        report["strict_core"][name] = m
        if all_ok:
            report["n_strict_passed"] += 1
        else:
            report["n_strict_failed"] += 1
            report["verdict"] = "strict_failed"

    # 2. Smoke rest : compile-only, tolérant
    report["smoke_rest"] = _scan_rest_compile()

    return report


def print_human_summary(report: dict) -> None:
    """Affichage lisible terminal."""
    print(f"=== cortex_smoke_check ===")
    print(f"verdict: {report['verdict']}")
    print()
    print("STRICT CORE (bloquant) :")
    for name, m in report["strict_core"].items():
        flag = "OK" if m.get("all_ok") else "FAIL"
        print(f"  [{flag}] {name}")
        if not m.get("all_ok"):
            for stage_name, stage in m.items():
                if stage_name == "all_ok": continue
                if isinstance(stage, dict) and not stage.get("ok"):
                    err = stage.get("error", "?")
                    print(f"        -> {stage_name}: {err}")
    sr = report.get("smoke_rest") or {}
    print(f"\nSMOKE REST (tolérant) : {sr.get('passed', 0)}/{sr.get('total', 0)} compile ok")
    if sr.get("failures"):
        print("  failures (non-fatales) :")
        for f in sr["failures"]:
            print(f"    - {f['module']}: {f['error'][:80]}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "human"
    rep = run()
    if cmd == "json":
        print(json.dumps(rep, indent=2, ensure_ascii=False))
    else:
        print_human_summary(rep)
        print(f"\nfull JSON: python {Path(__file__).name} json")
    sys.exit(0 if rep["verdict"] == "ok" else 1)
