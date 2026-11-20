# diff-tour — maintainer notes

Not loaded by the skill; `SKILL.md` is what the model reads.

## Files

- `difftour.py` — `collect` (snapshot and unit index) and `build` (checks notes.json, renders
  the page).
- `assets/` — the page itself: `page.html` (document shell with `$lang`, `$title`, `$style`,
  `$body`, `$highlight`, `$script`), `page.css` (light theme; `%DARK%` marks where the tokens of
  `dark.css` go), `page.js` (view switch, unit links, per-line highlighting); `queue.css` and
  `queue.js` (the queue and the queue / whole diff switch) are added only when there is a queue.
- `triage.md` — the brief the fresh reviewers follow.
- `difftour_test.py` — run `python3 difftour_test.py`. `testdata/golden-*.html` pins the page
  for a fixed input byte for byte; after an intended change to the page, regenerate them with
  `DIFFTOUR_REGEN_GOLDEN=1 python3 difftour_test.py` and review the change in the goldens.

## Noise patterns

A repository can list its own generated files in `.claude/diff-tour-noise`, one pattern per
line; blank lines and lines starting with `#` are skipped. Each file that matches becomes one
`nN` unit, folded on the page — but the risk rules still read all of its lines, so a noise
pattern cannot hide a risky edit. The file is read from the base, like the risk rules below.
The patterns are gitignore-like globs:

- without `/` — matches a file name at any depth (`*.g.cs`);
- with `/`, or a leading `/` — matches from the repository root (`/Generated/Proto.cs`);
- a leading `**/` — lets a pattern with `/` start in any directory (`**/obj/cache.bin`);
- a trailing `/` — a directory and everything under it (`generated/`).

The built-in list covers lock files, minified files and source maps (`DEFAULT_NOISE`).

## Risk rules

What the script raises into the queue by itself, whatever the reviewers think. The built-in
list (`DEFAULT_RISK`) covers serialized and networked attributes, enums, version and schema
fields, tool permissions, destructive commands, assembly definitions and project settings,
and the files that steer the tour itself (`.claude/diff-tour-noise`, `.claude/review-risk`,
`.gitattributes`). An existing test file is raised when it checks less: fewer tests or
asserts than before, a skip added, or the file moved out of the tests. A rewritten assert with
the same count is left to the reviewers: the script cannot tell a tighter one from a looser.

A repository adds its own in `.claude/review-risk`, one rule per line, `#` for comments:

- `path <pattern>` — a file, matched like a noise pattern under its old or new path;
- `line <regex>` — any added or removed line of a hunk (Python `re`);
- `section <regex>` — the code around the change: the hunk's `@@` context or any of its
  unchanged lines, e.g. the enum whose members it edits.

The file is read from the base, never from the change itself: a change cannot switch off the
rules that would raise it, and rules it adds apply from the next change on. An invalid rule
is skipped with a warning. For the same reason the diff is taken with the base's
`.gitattributes` (`git --attr-source`, git 2.40+), so a new `-diff` cannot turn a file into
"Binary files differ". `build` reads the noise and risk files from the base again rather than
trusting the copies in hunks.json, and stops if the base is gone.

## Triage files

Each fresh reviewer writes `<run dir>/triage-<name>.json` following `triage.md`:
`{"units_seen": N, "suspects": [{"unit", "text"}], "risks": [{"unit", "check"}]}`. `build`
checks them (two files at least, ids from hunks.json, non-empty text, `units_seen` within the
snapshot) and merges them into the queue; a reviewer that read fewer units than the snapshot
has is flagged on the page. Whether a unit has an author note does not move it into or out of
the queue: the units without one are a count under the verdict and a list in the Self-check. `build --all` renders the
whole diff alone — no triage files, no queue — and that page is byte-for-byte what the
goldens pin.

## Run directories

`collect` writes to `~/.cache/kensei-diff/<repo>/<timestamp>/` (mode 0700) and keeps the newest
20 runs per repository there. A caller that links to the page from its own record passes
`--out-root <its run dir>`: rotation then counts only the runs under that root. That root must be
outside the work tree or git-ignored, or the tour shows up as untracked files in the next `collect`.
