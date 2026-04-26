<p align="center">
  <img src="marketing_material/ive-banner.png" alt="IVE" width="800">
</p>

<p align="center">
  <strong>Integrated Vibecoding Environment</strong><br>
  Control multiple Claude Code &amp; Gemini CLI sessions from one browser UI.
</p>

<p align="center">
  <a href="https://github.com/vibe2vibe/ive/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-blue.svg" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/node-18%2B-green.svg" alt="Node 18+">
</p>

---

## What is IVE?

IVE is a local web app that turns your browser into a command center for AI coding agents. Run dozens of Claude Code and Gemini CLI sessions from a single UI — with orchestration, a Kanban board, deep research, and graph-based multi-agent pipelines built in.

Every session is a real pseudo-terminal (`os.fork()` + `pty.openpty()`). Shift+Tab, plan mode, slash commands, interactive prompts — everything works exactly like the native CLI. IVE just gives you superpowers on top.

## Highlights

- **Real PTY sessions** for both Claude Code (Haiku/Sonnet/Opus) and Gemini CLI, multiplexed over a single WebSocket.
- **Pipelines** — visual node-graph editor for multi-agent workflows. Drag stages, draw transitions, set conditions, ship custom agent loops (RALPH, TDD, Review, Research presets included).
- **Commander / Tester / Documentor** orchestrator sessions that spawn and manage worker sessions via MCP.
- **Feature Board** — built-in Kanban (backlog → todo → planning → in_progress → review → done) with full agent history per ticket and Excalidraw scratchpads.
- **Deep Research** — self-hosted, quota-free research engine with multi-source search (DuckDuckGo, arXiv, Semantic Scholar, GitHub) + extraction and a CLI plugin.
- **Hub-and-spoke memory sync, Plugin Marketplace, Output Styles, @-token expansion, Live Preview, voice annotation** — and 30+ configurable keyboard shortcuts.

## Architecture

- **Backend**: Python aiohttp on `:5111` — 140+ REST routes, WebSocket, real PTYs.
- **Frontend**: React 19 + Vite 8 + xterm.js on `:5173` — Zustand state, Tailwind v4 dark theme.
- **Data**: SQLite at `~/.ive/data.db`.
- **No external services** — everything runs locally.

A more detailed architecture writeup, including the three-layer CLI abstraction, central event bus, and hook-based state detection, lives in [`CLAUDE.md`](CLAUDE.md). Visual diagrams live under [`marketing_material/`](marketing_material/).

## Quick Start

```bash
git clone https://github.com/vibe2vibe/ive.git
cd ive
./start.sh
```

The script auto-updates the CLIs, installs Python and Node dependencies, and launches the backend (`:5111`) and frontend (`:5173`). Open [http://localhost:5173](http://localhost:5173).

For full prerequisites (Python version, Node version, supported OSes, screenshot deps, etc.), see [`INSTALL.md`](INSTALL.md).

### One-line internet-shareable instance

Spin up a public tunneled instance without cloning:

```bash
npx ive --tunnel
```

This launches IVE locally and exposes it through a Cloudflare tunnel so you can hop on it from any device — useful for handing off a live session or reviewing from your phone.

### Manual run

```bash
# Backend only
cd backend && python3 server.py

# Frontend only
cd frontend && npm run dev

# Install deps
pip3 install -r backend/requirements.txt
cd frontend && npm install
```

## Telemetry &amp; Privacy

IVE ships with anonymous PostHog telemetry **enabled by default** so the maintainer can see how many installs are active during the beta. Each ping carries a hashed machine id, version string, platform tag, session count, and uptime. **No PII, no prompts, no code.** See [`backend/telemetry.py`](backend/telemetry.py) for the exact payload.

**To opt out**, set the env var before starting:

```bash
IVE_TELEMETRY=off ./start.sh
```

## Contributing

Issues and PRs welcome. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for dev setup, code conventions, and the PR process. The canonical project layout, conventions, and architecture notes live in [`CLAUDE.md`](CLAUDE.md).

## License

MIT — see [`LICENSE`](LICENSE).

The bundled subprojects (`ext-repo/myelin/`, `anti-vibe-code-pwner/`, `deep_research/`, first-party plugins) carry their own license files where applicable.
