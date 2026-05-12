# Council App

Local browser-based multi-agent chat. User talks to Claude, Codex, and Deepseek through a current-session markdown log.

## One-time Setup

1. Install Python 3.11+
2. Open a terminal and run:
   ```
   cd <user-home>\Tools\council
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. Add `<user-home>\Tools\council\` to your user PATH:
   - Press `Win+R`, type `sysdm.cpl`, click Advanced ↁEEnvironment Variables
   - Under User variables, find `Path`, click Edit ↁENew
   - Paste `<user-home>\Tools\council\`
   - Click OK and close all dialogs

## Usage

1. Open a terminal in any project root that has a `CLAUDE.md`
2. Type `council`
3. Open the printed URL in your browser
4. Type messages; use `@claude`, `@codex`, or `@deepseek` only when you want to activate that agent, or use the agent buttons next to the input

`@agent` is an activation command. To refer to an agent without summoning it, write the plain name, such as `deepseek`.

## Chat Storage

Council writes the active browser session to:

```text
<project>\.council\chat.md
```

That file is only the current session. On each Council launch, a non-empty previous `chat.md` is moved into:

```text
<project>\.council\chat-archive\chat-session-<timestamp>.md
```

This keeps the dispatch context small and avoids one permanent aggregate log.

If an old `<project>\ClaudeDocs\_handoff\chat.md` exists and `.council\chat.md`
does not, Council copies the old chat into `.council` before rotating it into the
new archive location. The old file is left in place.

## Prerequisites

- `claude` CLI on PATH (Claude Code)
- `codex` CLI on PATH
- `opencode` CLI on PATH with `deepseek/deepseek-v4-pro` and `deepseek/deepseek-v4-flash` configured

## Dispatch Timeouts

Council uses `dispatch.timeout_seconds` as the default agent subprocess timeout.
Set it to `0` to disable Council's wrapper timeout and let agent CLIs run like
they do when called directly. Set `dispatch.<agent>_timeout_seconds` to override
one agent only when that agent intentionally needs a different limit.

## Troubleshooting

- **"Error: no CLAUDE.md found"**  E`cd` into a project root that has a `CLAUDE.md`
- **Port 6767 in use**  Eedit `config.toml` and change the port
- **Agent dispatch times out**  Echeck the CLI works manually first (`claude -p "hi"` or `opencode run -m deepseek/deepseek-v4-pro "hi"`), or set `dispatch.timeout_seconds = 0` to disable Council's wrapper timeout
