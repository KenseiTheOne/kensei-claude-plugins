# Kensei Claude Plugins

Claude Code plugin marketplace: a statusline and a skill toolkit for tracker tasks, diff review,
design brainstorms, Unity review and end-of-session capture. Release notes are in [CHANGELOG.md](CHANGELOG.md).

## Installation

```bash
claude plugin marketplace add KenseiTheOne/kensei-claude-plugins
claude plugin install kensei-toolkit@kensei-claude-plugins
claude plugin install kensei-statusline@kensei-claude-plugins
```

Inside a session the same works as `/plugin marketplace add …` and `/plugin install …`.

## Requirements

- **Claude Code 2.1.286 or newer** — the version the plugins are validated with; CI pins the same
  version. Older versions are untested.
- **`python3` 3.9 or newer** on `PATH`. The ticket guard, diff-tour and the statusline are Python
  scripts; without `python3` the guard cannot run and blocks nothing.
- **Codex CLI 0.158 or newer**, signed in with ChatGPT (a paid plan), for codex-img only.
- **A PNG renderer** for svg-diagram only, to look at the picture before it is handed over: Google
  Chrome or Microsoft Edge (found in the usual places, or set `CHROME=<path>`), or `rsvg-convert`
  (librsvg).
- **`git` 2.25 or newer** for diff-tour, the ticket flow and the statusline's git line (2.28 to run
  the tests).

## Plugins

| Plugin | Description |
|--------|-------------|
| [kensei-toolkit](#kensei-toolkit) | Tracker task runner with a publish guard, annotated diff tour, design brainstorm, image generation through Codex, SVG diagrams for READMEs, Unity code review, session learnings and loose ends |
| [kensei-statusline](plugins/kensei-statusline/README.md) | Multi-line statusline: model, effort and ultracode, context, tokens, cost, usage limits (incl. per-model ones like Fable), subagents, git, project stats |

Versions live in each plugin's `.claude-plugin/plugin.json`; the CHANGELOG lists what each one changed.

**Invocation** says who can start a skill: *manual* — only you, by typing the command; *model* —
Claude also picks it on its own when your request matches the skill's description. Every skill
answers in the language you write in.

### kensei-toolkit

| Skill | Command | Invocation | Example | What it does |
|-------|---------|------------|---------|--------------|
| [ticket](plugins/kensei-toolkit/skills/ticket/) | `/kensei-toolkit:ticket <url\|id> [probe\|fix\|full]` | manual, model | «возьми тикет CU-86c1234, режим fix» | Runs one tracker task end to end: context agent, acceptance criteria you approve, implementation, tests, fresh-agent review, then a publish gate. Commit, merge into main, push, PR (only with the body you approved), branch deletes, task comment, status, task creation, releases, repository settings and mail, chat or calendar sends happen only on your command (typed, or an answer tagged such as `[merge-local]`, `[delete-branch]`, `[create-task]`, `[publish]`, `[repo-admin]`, `[send]`), enforced by a guard hook that also covers subagents; a comment is fact-checked and shown in full before posting. Any tracker via MCP, CLI or browser |
| [diff-tour](plugins/kensei-toolkit/skills/diff-tour/) | `/kensei-toolkit:diff-tour [ref\|a..b]` | manual, model | «покажи дифф» | Self-review before a commit: the changes (untracked files included) as a local HTML page, side by side or unified, with notes on what and why placed under the lines they explain; every change no note explains is marked "Unexplained". Read-only: the git index and the repository are never touched |
| [brainstorm](plugins/kensei-toolkit/skills/brainstorm/) | `/kensei-toolkit:brainstorm [topic]` | manual, model (on an explicit ask) | «давай побрейнштормим, как устроить офлайн-кэш» | Design dialogue before implementation: focused questions, honest challenge, approaches, incremental validation. Ends with a design doc and a standalone plan, saved outside the project (`~/docs/brainstorms` unless the project names a place); implementation starts only when you say so |
| [codex-img](plugins/kensei-toolkit/skills/codex-img/) | `/kensei-toolkit:codex-img [what to draw] [--out path]` | manual, model | «нарисуй иконку огненного камня с прозрачным фоном в icons/fire.png» | Generates or edits images (icons, sprites, illustrations) through the Codex CLI's built-in image tool on your ChatGPT subscription — no API key. Saves each image under the name you choose, keeps a series in one style from reference images, checks that a transparent PNG really has alpha, runs a series from a manifest in the background after you confirm the cost, and stops on the image limit. The model, size and masks are not selectable: Codex decides |
| [svg-diagram](plugins/kensei-toolkit/skills/svg-diagram/) | `/kensei-toolkit:svg-diagram [what to draw] [--out docs/diagrams/name.svg]` | manual, model | «замени ASCII-схему в README на картинку» | Draws an architecture, data-flow or pipeline diagram as a hand-built SVG in a dark gradient style (cards, labelled arrows, a highlighted special path, takeaway badges), one per README language, and embeds it. A small generator script with a stdlib kit copied next to it keeps the picture editable; `--strict` reports text overflowing its box, off the canvas or overlapping, and every language is rendered to PNG and looked at before it is handed over. Not for raster art (codex-img) or data charts |
| [unity-review](plugins/kensei-toolkit/skills/unity-review/) | `/kensei-toolkit:unity-review [quick\|perf\|arch\|full] [paths\|ref\|a..b] [focus]` | manual, model | «проверь на перф» | Unity-specialist review of a change through four lenses (performance, memory and lifecycle, Unity architecture, platform), checked against the project's real platforms and packages. Read-only reviewers; a fresh agent tries to refute every finding; the report lists confirmed findings by severity with `path:line` and a failure scenario. Generic bugs and style are left to `/code-review`. In a project with its own review skill, a general review goes to that skill; a lens you name that it lacks (such as performance) runs here |
| [learn](plugins/kensei-toolkit/skills/learn/) | `/kensei-toolkit:learn [path/to/CLAUDE.md\|focus]` | manual | — | End of session: proposes durable takeaways, deduplicated against the project and global `CLAUDE.md` and the auto-memory, each routed to one of them; writes only what you tick |
| [todo](plugins/kensei-toolkit/skills/todo/) | `/kensei-toolkit:todo [path/to/notes.md\|focus]` | manual | — | End of session: collects loose ends with evidence and, for the ones you tick, adds personal items to the Todoist Inbox (or prints a paste block) and project work to the ClickUp list you pick (the workspace comes from your project rules or `CLAUDE.md`, otherwise it asks); with a `.md` path, appends to that file instead |

The toolkit also ships a `PreToolUse` hook ([hooks/hooks.json](plugins/kensei-toolkit/hooks/hooks.json))
that applies the ticket guard to subagents while `/kensei-toolkit:ticket` is active. The guard
over the main session comes from the skill's own hooks, which register when the skill is invoked:
after `claude --resume`, invoke `/kensei-toolkit:ticket <same id>` again to turn it back on (the
run continues from the step it reached).

### kensei-statusline

| Skill | Command | Invocation | Example | What it does |
|-------|---------|------------|---------|--------------|
| [setup](plugins/kensei-statusline/skills/setup/) | `/kensei-statusline:setup` | manual, model | «настрой статуслайн» | Installs the statusline wrapper and sets `statusLine` in `settings.json`, keeping your other settings and backing the file up first |

A `SessionStart` hook ([hooks/hooks.json](plugins/kensei-statusline/hooks/hooks.json)) asks Claude
to *offer* setup when no statusline is configured yet; nothing changes until you agree. See the
[statusline README](plugins/kensei-statusline/README.md) for the display and the usage-limit fetch.

## Structure

```
.claude-plugin/marketplace.json            — marketplace manifest (plugin list, no versions)
.github/workflows/ci.yml                   — runs scripts/check.sh on Linux and macOS
scripts/check.sh                           — release check: script tests, validate, strict YAML
CHANGELOG.md                               — release notes for both plugins
plugins/kensei-toolkit/
  .claude-plugin/plugin.json               — manifest, version
  hooks/hooks.json                         — subagent guard while a ticket run is active
  skills/ticket/                           — SKILL.md, flow.md, publish.md, trackers.md,
                                             guard.py + guard_test.py, testdata/ (test fixtures)
  skills/diff-tour/                        — SKILL.md, README.md, difftour.py + difftour_test.py,
                                             assets/ (page template, CSS, JS),
                                             testdata/ (golden pages)
  skills/brainstorm/                       — SKILL.md
  skills/codex-img/                        — SKILL.md, README.md (why a skill, not an MCP),
                                             codex_img.py + codex_img_test.py
  skills/svg-diagram/                      — SKILL.md, diagram_kit.py + diagram_kit_test.py,
                                             example.py (reference diagram)
  skills/unity-review/                     — SKILL.md, lenses.md (read by the reviewer agents)
  skills/learn/, skills/todo/              — SKILL.md
  evals/                                   — behavioural cases for claude plugin eval
plugins/kensei-statusline/
  .claude-plugin/plugin.json               — manifest, version
  hooks/hooks.json                         — SessionStart: offer setup once
  scripts/                                 — statusline.py, usage.py (usage rows), wrapper,
                                             setup-check.py, statusline_test.py
  skills/setup/                            — SKILL.md, setup.py
```

## Development

This clone (`~/Projects/IT/kensei-claude-plugins`) is the source of truth. The copies under
`~/.claude/plugins/` belong to Claude Code: edits there are overwritten on the next update and leave
the installed version number lying about its contents, so they are never edited by hand.

**Edit and try.** Change files here and load the plugin straight from the clone:

```bash
claude --plugin-dir ~/Projects/IT/kensei-claude-plugins/plugins/kensei-toolkit
```

A `/kensei-toolkit:ticket` session started this way treats the clone's `skills/ticket/` and
`hooks/` as its own guard and refuses to edit them. To change the guard through `/ticket`, start
that session on the installed plugin instead.

**Release.**

1. The release check passes — the same script CI runs:
   ```bash
   scripts/check.sh            # or one part: tests, validate, yaml
   ```
   It runs the three script tests, `claude plugin validate --strict` on the marketplace and each
   plugin (skills included), and parses every skill frontmatter, eval frontmatter and `case.yaml`
   as strict YAML, which `validate` does not. It needs `claude`, `git` and PyYAML.
2. After a skill change, run the evals on Opus (they cost real model calls, see
   [evals/README.md](plugins/kensei-toolkit/evals/README.md)).
3. Bump `version` in the plugin's `plugin.json` (semver; removing a skill is a major bump) and add a
   `CHANGELOG.md` entry.
4. Commit, push, then tag: `claude plugin tag plugins/<plugin> --push` creates
   `<plugin>--v<version>` and checks that the manifests agree.

**Install the release.** Only through Claude Code:

```bash
claude plugin marketplace update kensei-claude-plugins
claude plugin update kensei-toolkit@kensei-claude-plugins
claude plugin update kensei-statusline@kensei-claude-plugins
```

Then restart long-lived sessions: a running session keeps the plugin copy it started with. When a
skill behaves oddly, first check which copy that session actually loaded: look for the `.in_use`
marker under `~/.claude/plugins/cache/kensei-claude-plugins/<plugin>/<version>/` (look, never edit),
or the plugin path the session itself reports. `claude plugin list` shows only the installed
version, not what a running session holds.

CI ([ci.yml](.github/workflows/ci.yml)) runs `scripts/check.sh` on pushes to `main`, on tags and
on pull requests: the tests on Linux and macOS (and on Python 3.9 on Linux), validation and YAML
with the pinned Claude Code version; on a tag it also checks that the tag matches `plugin.json`.
Evals are not run in CI.
