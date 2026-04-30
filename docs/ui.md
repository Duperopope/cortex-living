# Dashboard UI

The GPU dashboard is served at `/gpu`. It is a cockpit-style interface with four
main regions.

## 1. Topbar

The topbar shows only the most important live signals:

- cognitive mode
- active model/backend
- quota pressure
- CPU/RAM state
- vault counts
- voice service state

The topbar exists to prevent the rest of the UI from becoming a wall of
debugging panels.

## 2. 3D Thought Graph

The central canvas renders the memory graph. Nodes come from vault notes,
semantic memory, Claude memory, and recent conversation episodes. Edges are
semantic similarity links. Active nodes pulse when `cortex_activation` marks
them as currently relevant.

The graph uses:

- Three.js/WebGL
- instanced node meshes
- line segments for edges
- bloom for active nodes
- force-directed movement with folder and time anchors

The UI now forces a dark CSS and WebGL background so a WebGL hiccup cannot leave
the operator staring at a white page.

## 3. Left Instrumentation Panel

The left panel is for machine and memory instrumentation:

- voice status
- Claude quota bars
- current LLM route
- vault counts
- CPU/RAM/GPU/disk/service health
- brain evolution sparkline
- privacy controls

The panel is intentionally secondary to the graph.

## 4. Chat Console

The chat console is a floating work surface, not a full-screen overlay. It has:

- a compact model selector
- optional live vision rail
- readable Sam/Cortex message bubbles
- Markdown rendering for assistant output
- copy buttons
- duplicate suppression between immediate send results and SSE replay

## 5. Right Cognition Panel

The right panel explains what Cortex is currently doing:

- current cognitive mode
- top spreading activations
- strongest Hebbian edges
- recent pulses
- attention composition
- latest autonomous decision
- event log

This panel is the main anti-black-box feature of the interface.

The panel separates measured state from UI interpretation:

- spreading activations come from `/api/cortex/activations`
- Hebbian edges come from the persisted activation state
- recent pulses come from `.cortex-pulses.jsonl`
- cognitive mode and attention focus are inferred labels, not direct sensors

When there are no active nodes or pulses, the UI should show that quiet state
rather than fabricate activity.
