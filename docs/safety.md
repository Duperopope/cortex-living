# Safety Boundaries

Cortex is designed to be observable and local-first. The safety model is based
on explicit boundaries rather than hidden trust.

## Publication Boundary

Only sanitized documentation and summarized state are published here. The
publication process must not include:

- cookies or session tokens
- `.env` files
- Obsidian private notes
- raw conversation logs
- voice samples
- webcam images
- model weights
- virtual environments
- local absolute paths beyond documented topology

## Runtime Boundary

The dashboard is localhost-only by default. It should not be exposed directly to
the public internet because it controls microphone, TTS, vision, local model
backends, and self-development endpoints.

## Autonomy Boundary

Autonomous loops may propose actions and write sanitized reports. Riskier
actions should remain gated:

- package installation
- deletion or migration of files
- public publishing
- self-modifying code changes
- starting new long-running services

## Self-Development Guardrails

The local self-development loop is configured by:

`H:\Code\Paperclip\scripts\brain\cortex_self_dev_guardrails.json`

This file is intentionally human-editable. It controls whether self-development
is enabled, which path prefixes may be modified, blocked path fragments, context
budget, maximum files per change, valid smoke-test suites, and whether a target
path must be explicitly named in the requested goal.

Current important defaults:

- writes are limited to `scripts/brain/`
- goals must name the exact target file path
- commits are made with explicit paths only, never `git add -A`
- destructive rollback commands are not exposed to the autonomous tool registry
- failed tests leave the branch for inspection instead of erasing local state

On 2026-04-30, the loop was verified with an isolated probe: Cortex created
`scripts/brain/self_dev_probe.py`, ran the `cortex` smoke suite, and committed
only that file on a `cortex/dev/...` branch.

## Honesty Boundary

UI signals should be real. If a feature is a placeholder, fallback, or heuristic,
the interface and documentation should say so. The project explicitly prefers a
less impressive real signal over a decorative fake one.
