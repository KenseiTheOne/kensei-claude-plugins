# Kensei Statusline

Multi-line Claude Code statusline with model info, tokens, cost, usage limits, subagents, and git status.

## Display

```
Opus 5.5 high ⚡ultracode │ ▓▓▓▓░░░░░░ 42% │ ↑380.0K ↓62.0K │ $10.3 │ 3 agents (Explorex2, general-purpose)
5h 24% ↻ 18:00 · 7d 41% ↻ 16.06 · Fable 11% ↻ 28.09
main │ ●2 +3 ?1 │ +310 -45 │ 12 files 1.2K loc
```

**Line 1:**
- **Model** — current model name
- **Effort** — the session's live effort level, as Claude Code sends it (only for models that support effort): `low` and `medium` dim, `high` cyan, `xhigh` yellow, `max` red. Claude Code re-runs the statusline when it changes
- **⚡ultracode** — shown while ultracode is on. Claude Code does not pass it to statuslines, so it is worked out the way Claude Code keeps it: until the first reply of a Claude Code process from the `ultracode` settings key (user, project, local, managed), which a process reads once at start; after each reply from the last ultracode reminder in the transcript, which Claude Code re-checks against the live state at every prompt; in between from the output of an `/effort` command that toggled it. Claude Code does not re-run the statusline when only ultracode changes, so that shows on the next periodic refresh: set `statusLine.refreshInterval` (setup sets 5 seconds when there is none). An `/effort` typed while Claude is working, and before the first reply ultracode set by `--settings`, `--effort ultracode` or Remote Control, show from the next reply on. The transcript reading is cached by the transcripts' size and mtime in `<config dir>/cache/kensei-statusline/sessions.json`, so an idle refresh reads no transcript
- **Context bar** — green (<50%), yellow (50-80%), red (>80%)
- **↑ / ↓** — current context size (input tokens of the last request) / output tokens of the whole session, subagents and workflow subagents included
- **$X.XX** — session cost as Claude Code reports it; `~$X.XX` when it is estimated from the transcripts (see API Pricing)
- **N agents** — running Agent-tool subagents, grouped by `subagent_type`

**Line 2 (usage limits):**
- **5h / 7d** — subscription usage windows with reset time (`↻ HH:MM` for 5-hour, `↻ DD.MM` for weekly), same color thresholds as the context bar. Shown only on Claude Pro/Max — Claude Code sends `rate_limits` after the first API response of the session
- **Per-model and other meters** (e.g. a separate weekly **Fable** limit) — every other row of the claude.ai usage endpoint that `/usage` shows, labelled and ordered as the server sends them and coloured up by its severity. Claude Code does not pass these to statuslines, so the plugin fetches them itself: a detached background process (`scripts/usage.py`) calls `GET https://api.anthropic.com/api/oauth/usage` with Claude Code's own OAuth access token (macOS keychain item `Claude Code-credentials`, per profile under `CLAUDE_CONFIG_DIR`, or `.credentials.json` in the config dir) at most every 3 minutes, with backoff after errors, and caches only the rows in `<config dir>/cache/kensei-statusline/usage.json`. Rendering never waits on the network. The token is never refreshed, logged or stored; an expired one is skipped until Claude Code renews it. API-key sessions make no request. Set `KENSEI_STATUSLINE_NO_USAGE_FETCH=1` to turn the fetch off

**Line 3:**
- **Branch** — current git branch (bold blue)
- **●/+/?** — staged (green) / modified (yellow) / untracked (dim)
- **⇡/⇣** — commits ahead/behind remote
- **+N -N** — lines added/removed in session
- **N files N loc** — project size at HEAD, counted once per commit and cached in `<config dir>/cache/kensei-statusline/`

## Requirements

- Python 3.8+

## Installation

```bash
/plugin marketplace add KenseiTheOne/kensei-claude-plugins
/plugin install kensei-statusline@kensei-claude-plugins
```

At the start of a session where no statusline is configured yet, a `SessionStart` hook asks Claude to offer the setup; nothing changes until you agree. Setup shows what it will change, then copies a small wrapper to `~/.claude/scripts/kensei-statusline.py` and sets `statusLine` in `~/.claude/settings.json` (both under `CLAUDE_CONFIG_DIR` when that is set). Other settings, including an existing `statusLine.refreshInterval`, are kept (when there is none, setup sets 5 seconds, see ⚡ultracode), and `settings.json` is backed up first as `settings.json.bak-<timestamp>`. Restart Claude Code (`/exit`) for the statusline to take effect. Declining creates `~/.claude/.statusline-no-setup`, and the offer is not repeated.

To run setup yourself (e.g. after editing `settings.json`, or to reinstall the wrapper):

```bash
/kensei-statusline:setup
```

### Why a wrapper?

The plugin cache path includes a version (`~/.claude/plugins/cache/kensei-claude-plugins/kensei-statusline/<version>/...`), so a hardcoded path would break on every update. The wrapper looks up the installed version in `~/.claude/plugins/installed_plugins.json` on each render (a project install for the current project first), skips copies Claude Code marked as orphaned, and falls back to the highest non-orphaned cached version.

## Tests

```bash
python3 plugins/kensei-statusline/scripts/statusline_test.py
```

## API Pricing

Claude Code reports the session cost (`cost.total_cost_usd`) itself, and the statusline shows that value as is. Only when it is missing does the script estimate the cost from the transcripts (main session, subagents, workflow subagents) at Anthropic first-party API rates, per 1M tokens, as of September 2026:

| Model | Input | Output | Cache read |
|-------|-------|--------|------------|
| Fable 5.1 | $10.00 | $50.00 | $0.25 |
| Fable 5 | $10.00 | $50.00 | $1.00 |
| Opus 5.5 | $4.00 | $20.00 | $0.20 |
| Opus 5, 4.8, 4.7, 4.6 | $5.00 | $25.00 | $0.50 |
| Sonnet 5.5, 5 | $2.00 | $10.00 | $0.20 |
| Sonnet 4.6 | $3.00 | $15.00 | $0.30 |
| Haiku 4.5 | $1.00 | $5.00 | $0.10 |

Cache writes cost 1.25× input for the 5-minute TTL and 2× for the 1-hour TTL. Tokens of a model not in this table are not guessed: the estimate shows `n/a` for them (`~$4.20 + n/a`, or just `$n/a`). The table holds standard-speed rates; fast-mode requests (`usage.speed: "fast"`) are billed higher and count as `n/a` too.
