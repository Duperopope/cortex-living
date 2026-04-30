# Runtime Map

## Main Processes

| Process | Role | Default Port |
| --- | --- | --- |
| `dashboard/serve.py` | Cortex HTTP server and dashboard | `8765` |
| `llm_router.py` | Model routing and fallback API | `18900` |
| `tts_daemon.py` / `xtts_daemon.py` | Speech synthesis service | `18768` when active |
| `voice_input.py` | Voice activity and speech input loop | `18767` health signal |
| `tts_monitor.py` | Tracks and plays assistant speech | `18766` health signal |
| Paperclip server | AI company control plane | usually `3100` or fork-local override |
| LM Studio | Local OpenAI-compatible model server | `1234` |

## Self-Development Loop

`scripts/brain/cortex_self_dev.py` is the controlled self-development runner.
It does not let the model write directly to disk. The runner asks the model for
structured JSON, validates the planned paths against guardrails, creates a
dedicated `cortex/dev/...` branch, writes only approved files, runs smoke tests,
then commits only the explicit files that were applied.

If the router returns an `inject=true` answer meant for Claude rather than a
model response, the runner falls back to the local LM Studio OpenAI-compatible
API so that self-development still receives a structured proposal.

The guardrail file is:

`H:\Code\Paperclip\scripts\brain\cortex_self_dev_guardrails.json`

## Important Local Paths

| Path | Purpose |
| --- | --- |
| `H:\Code\Paperclip` | Main Paperclip fork and Cortex working tree |
| `H:\Code\Paperclip\scripts\brain` | Cortex modules |
| `H:\Code\Paperclip\scripts\voice` | Voice pipeline |
| `C:\Users\Smedj\Documents\Obsidian Vault` | Memory, state files, graph artifacts |
| `C:\Users\Smedj\.claude\projects\h--Code-Paperclip\memory` | Claude auto-memory |
| `H:\Code\Paperclip\.cortex-publishing` | Sanitized GitHub publication repo |

## State Files

Cortex stores live state in JSON/JSONL files instead of hiding it in a database.
Examples:

- `.cortex-activations.json`
- `.cortex-chat-stream.jsonl`
- `.cortex-pulses.jsonl`
- `.cortex-vital-signs.jsonl`
- `.vault-graph.json`
- `.vault-graph-layout.json`
- `.vault-jepa-status.json`

This makes the system easy to inspect and resilient across process restarts, but
also requires careful filtering before publishing anything publicly.
