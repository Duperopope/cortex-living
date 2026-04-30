# Changelog 2026-04-30

This page tracks the concrete technical changes made on 2026-04-30.

## Scope

- dashboard UX/layout cleanup
- right cognition panel clarity (real vs inferred signals)
- self-development reliability and safety guardrails
- reproducible self-code proof flow from chat
- Playwright install and preview capture support
- public documentation hardening

## Dashboard (`/gpu`)

Files:

- `H:\Code\Paperclip\scripts\brain\dashboard\brain_gpu.html`

Changes:

- Reworked dashboard composition to reduce overload:
  - topbar for essential state
  - graph as primary scene
  - dockable side panels
  - floating compact chat console
- Added left drawer behavior to prevent panel overlap.
- Improved chat rendering (readability, dedupe between immediate reply and SSE replay, markdown/copy behavior).
- Added dark background and WebGL clear-color safeguards to prevent white-screen failures during rendering hiccups.
- Removed noisy decorative controls and reduced icon clutter.
- Right cognition panel now labels signal provenance:
  - `real` for activation API values
  - `real disk/jsonl` for persisted Hebbian/pulse state
  - `inferred` for derived cognitive mode and attention focus

## Self-Development (`cortex_self_dev.py`)

Files:

- `H:\Code\Paperclip\scripts\brain\cortex_self_dev.py`
- `H:\Code\Paperclip\scripts\brain\cortex_tools.py`
- `H:\Code\Paperclip\scripts\brain\cortex_self_dev_guardrails.json`

Changes:

- Added explicit guardrail file and defaults:
  - allowed path prefixes
  - blocked path fragments
  - max context/files/shrink ratio
  - required explicit target path in goal
  - test suite normalization and aliases
- Added fallback to LM Studio when router v2 returns an `inject=true` handoff instead of a structured model answer.
- Fixed autonomous goal parsing by separating goal JSON parsing from patch JSON parsing.
- Hardened context search by skipping heavy directories and caches.
- Replaced broad commit behavior with explicit-path commit (`git_commit_paths`) to avoid accidental staging.
- Removed destructive rollback behavior from autonomous flow:
  - failed runs now stay on branch for inspection
  - global rollback tool is not exposed in autonomous tool registry
- Fixed branch-step logging bug in self-dev report generation.

## Automatic Proof From Chat

Files:

- `H:\Code\Paperclip\scripts\brain\dashboard\serve.py`

Changes:

- Added a direct "self-code proof" path in `POST /api/chat`.
- When a user asks for proof (keywords like `autocoder`, `preuve`, `proof`), the server now:
  - runs a guarded dry-run self-dev proposal
  - executes a minimal bounded update on `scripts/brain/self_dev_probe.py`
  - returns a compact proof summary:
    - goal
    - dry-run outcome
    - apply outcome
    - target file/value
    - smoke test results
    - generated branch
- This bypasses generic LLM discussion and provides an actionable verification path.
- Added a deterministic verification path for follow-up prompts like
  `prouve moi ca`:
  - reads `scripts/brain/self_dev_probe.py` directly
  - reports current file content
  - reports last commit touching this file
  - reports current branch
- Updated wording so failed/self-dev-unavailable runs are reported as
  non-applied attempts instead of successful proof.

## Pulse Visibility Diagnostics

Files:

- `H:\Code\Paperclip\scripts\brain\dashboard\serve.py`
- `H:\Code\Paperclip\scripts\brain\dashboard\brain_gpu.html`

Changes:

- Added `POST /api/cortex/pulse_test` endpoint to inject visible pulse events.
- Added `PULSE` button in the right cognition panel that triggers this endpoint.
- This gives an immediate operator-side check that pulse propagation is working
  end-to-end (`cortex_activation` -> jsonl/api -> panel).

## Playwright Preview

Files:

- `H:\Code\Paperclip\package.json`
- `H:\Code\Paperclip\pnpm-lock.yaml`

Changes:

- Installed `playwright` as dev dependency with `pnpm`.
- Installed Chromium runtime (`pnpm exec playwright install chromium`).
- Verified headless preview capture on `/gpu`.
- Example artifact generated locally:
  - `H:\Code\Paperclip\.tmp_cortex_playwright_preview.png`

## Validation Performed

- Python compile checks:
  - `scripts/brain/dashboard/serve.py`
  - `scripts/brain/cortex_self_dev.py`
  - `scripts/brain/cortex_tools.py`
- Smoke tests:
  - `python scripts/brain/tests/test_smoke.py serve` passed
  - `python scripts/brain/tests/test_smoke.py cortex` passed
- Live endpoint checks:
  - `/gpu`
  - `/api/chat`
  - `/api/cortex/activations`
- Real self-dev proof executed and committed on a `cortex/dev/...` branch with bounded file scope.
