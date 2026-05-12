# Council App

Local browser-based multi-agent chat. User talks to Claude, Codex, and Deepseek through Council-owned sessions. Each session has its own project root, chat transcript, event log, compaction records, and model/effort/role overrides.

## One-time Setup

1. Install Python 3.11+
2. Open a terminal and run:
   ```text
   cd <user-home>\Tools\council
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. Add `<user-home>\Tools\council\` to your user PATH.

## Usage

1. Open a terminal in any project root, or pass a project root as the first argument.
2. Type `council`
3. Open the printed URL in your browser.
4. Use the left rail to create, switch, or delete sessions.
5. Type messages; use `@claude`, `@codex`, or `@deepseek` only when you want to activate that agent.

`@agent` is an activation command. To refer to an agent without summoning it, write the plain name, such as `deepseek`.

## Session Storage

Council owns the session registry under:

```text
<user-home>\Tools\council\sessions\
```

The registry lives in `sessions\sessions.json`. Each session directory contains `meta.json`, `state.json`, `events.jsonl`, `chat.md`, `compactions.jsonl`, and `chat-archive\`.

`events.jsonl` is the durable structured log. `chat.md` is the human-readable rendered transcript used by the UI and dispatch prompt tail. Compaction snapshots and records stay inside the session directory.

## Configuration

`config.toml` stores global defaults. The Config panel edits the active session by default. Toggle `global defaults` when you want to patch `config.toml` instead.

Missing or empty per-session values in `sessions\{id}\state.json` fall back to `config.toml`.

## Prerequisites

- `claude` CLI on PATH
- `codex` CLI on PATH
- `opencode` CLI on PATH with `deepseek/deepseek-v4-pro` and `deepseek/deepseek-v4-flash` configured
- Target projects should have `AGENTS.md` or `CLAUDE.md` for dispatch prompt rules

## Dispatch Timeouts

Council uses `dispatch.timeout_seconds` as the default agent subprocess timeout. Set it to `0` to disable Council's wrapper timeout and let agent CLIs run like they do when called directly. Set `dispatch.<agent>_timeout_seconds` to override one agent only when that agent intentionally needs a different limit.

## Troubleshooting

- **Port 6767 in use** - edit `config.toml` and change the port.
- **Agent dispatch fails with missing project rules** - set that session's project root to a folder containing `AGENTS.md` or `CLAUDE.md`.
- **Agent dispatch times out** - check the CLI works manually first (`claude -p "hi"` or `opencode run -m deepseek/deepseek-v4-pro "hi"`), or set `dispatch.timeout_seconds = 0`.
