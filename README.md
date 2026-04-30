# Cortex Living

Cortex is a local cognitive layer built around Paperclip, Obsidian, voice,
vision, and interchangeable LLM backends. It is not a standalone chatbot. It is
a persistent operator interface and runtime that makes an AI system observable:
what it can hear, see, remember, activate, route, and maintain.

Live dashboard, when running locally:

```text
http://127.0.0.1:8765/gpu
```

## What It Does

- Shows a real-time 3D thought graph built from Obsidian notes, Claude memory,
  semantic notes, and recent conversation episodes.
- Tracks spreading activations and Hebbian edge reinforcement as Cortex retrieves
  and reuses memory.
- Routes chat through local and remote model backends using a cost-aware router.
- Connects voice input, TTS output, webcam/screen vision, and machine vitals.
- Runs background loops for memory consolidation, emergence, homeostasis,
  publishing, and self-development experiments.
- Keeps Paperclip available as the control plane for agents, companies, tasks,
  budgets, and heartbeats.

## Current Runtime

| Layer | Implementation |
| --- | --- |
| UI | Single-page HTML/JS, Three.js, WebGL, SSE |
| Local server | Python `ThreadingHTTPServer`, `scripts/brain/dashboard/serve.py` |
| Memory | Obsidian Vault, Claude auto-memory, JSON/JSONL state files |
| Graph | TF-IDF cosine graph + force-directed 3D layout |
| Activation | Spreading activation with disk-backed snapshots |
| Learning | Hebbian reinforcement on co-activated edges |
| Voice | VAD, speech input, XTTS/Piper/Edge-TTS utilities, watchdog |
| Vision | Webcam/screen feed endpoints plus model/OCR fallback hooks |
| Routing | `llm_router.py`, opencode models, LM Studio, Codex/Claude fallback paths |
| Body | CPU/RAM/disk/GPU/services/battery homeostasis |
| Control plane | Paperclip local instance |

## Documentation

- [Architecture](docs/architecture.md)
- [Runtime Map](docs/runtime.md)
- [Dashboard UI](docs/ui.md)
- [Publishing](docs/publishing.md)
- [Safety Boundaries](docs/safety.md)
- [Live State Snapshot](docs/state.json)

## Repository Scope

This repository intentionally publishes documentation and sanitized state only.
It does not publish the private Obsidian vault, secrets, local cookies, raw
conversation logs, virtual environments, model weights, or machine-specific
runtime data.

## Status

Experimental, local-first, and actively changing. The important design rule is
that every visible signal should either be real or clearly marked as a fallback.
