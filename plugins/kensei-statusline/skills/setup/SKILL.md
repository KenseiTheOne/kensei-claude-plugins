---
name: setup
description: Configure the kensei-statusline as the Claude Code statusline — writes a small wrapper script and sets statusLine in the user settings.json. Use when the user wants to set up, reconfigure, repair or reinstall the statusline plugin, or agrees to the setup offered at session start.
---

# Statusline Setup

Make the kensei-statusline the active Claude Code statusline. `setup.py` next to this file does the
work, so the result does not depend on retyping anything by hand. It:

- copies the wrapper `scripts/kensei-statusline-wrapper.py` to `<config dir>/scripts/kensei-statusline.py`.
  On every render the wrapper runs the installed plugin version, read from
  `plugins/installed_plugins.json`, and skips orphaned cache copies;
- sets `statusLine.type` and `statusLine.command` in `<config dir>/settings.json`, keeping every other
  field, including `refreshInterval`, `padding` and the rest of the user's `statusLine`. When there
  is no `refreshInterval`, it sets one of 5 seconds: Claude Code does not re-run the statusline
  when ultracode is toggled, so only the periodic refresh shows that;
- saves a backup `settings.json.bak-<timestamp>` before changing `settings.json`;
- removes the "don't offer setup" marker `<config dir>/.statusline-no-setup`.

`<config dir>` is `$CLAUDE_CONFIG_DIR`, or `~/.claude` when it is unset. Talk to the user in their
language.

## Step 1 — Preview and tell the user what will change

```bash
python3 "${CLAUDE_SKILL_DIR}/setup.py" --dry-run
```

The output is JSON. If it has `error` (for example settings.json is not valid JSON), show it and
stop: fixing the user's settings file is their call. Otherwise tell the user in two or three short
lines:

- which settings file and wrapper path will be written. Say so when `wrapper_replaced` is true: an
  existing wrapper is overwritten, because older wrappers picked the plugin version by string order
  and could run a stale or orphaned copy;
- what `statusLine` changes from (`old_status_line`) and to (`new_status_line`), and that other
  fields stay as they are and a backup is made first;
- that the statusline reads Claude Code's own OAuth token (macOS keychain or `.credentials.json`)
  to fetch subscription usage meters from `api.anthropic.com` every few minutes at most, and that
  `KENSEI_STATUSLINE_NO_USAGE_FETCH=1` turns this off.

If `local_override_status_line` is not null, `settings.local.json` defines its own `statusLine`,
which takes precedence over `settings.json`. Setup leaves that file alone, so tell the user the old
statusline keeps showing until they remove `statusLine` from `local_settings`.

If `old_status_line` runs a different statusline (its command does not mention
`kensei-statusline`), ask with AskUserQuestion whether to replace it, since this removes the user's
current statusline. Otherwise go on.

## Step 2 — Apply

```bash
python3 "${CLAUDE_SKILL_DIR}/setup.py"
```

## Step 3 — Confirm

Tell the user the statusline is configured, name the backup file if one was made, and say that it
appears after a restart of Claude Code (`/exit` and start a new session).
