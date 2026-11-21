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
`nN` unit, folded on the page unless a risk rule, a reviewer or an author's flag raises it —
the risk rules still read all of its lines, so a noise pattern cannot hide a risky edit. The
file is read from the base, like the risk rules below.
The patterns are gitignore-like globs:

- without `/` — matches a file name at any depth (`*.g.cs`);
- with `/`, or a leading `/` — matches from the repository root (`/Generated/Proto.cs`);
- a leading `**/` — lets a pattern with `/` start in any directory (`**/obj/cache.bin`);
- a trailing `/` — a directory and everything under it (`generated/`).

The built-in list covers lock files, minified files and source maps (`DEFAULT_NOISE`).

## Risk rules

What the script raises into the queue by itself, whatever the reviewers think. The built-in
list (`DEFAULT_RISK`) covers networked attributes (RPC, Ghost, Meta methods), version and
schema fields, a `"unity"` or `"unityRelease"` key in any file (Unity's version in
`package.json`), tool permissions, destructive
commands (also git arguments passed as a list, `rm(`, `shutil.rmtree`, `os.remove`), secrets
written into code (API keys, tokens, private keys; a placeholder like `"${VAR}"`, `"<your-token>"`
or `"access_token"` is not one), assembly definitions, project settings and
package manifests at any depth, and the files that steer the tour itself
(`.claude/diff-tour-noise`, `.claude/review-risk`, `.gitattributes`). An existing test file is
raised when it checks less: fewer tests or asserts than before, a skip added, or the file
moved out of the tests. A rewritten assert with the same count is left to the reviewers: the
script cannot tell a tighter one from a looser.

Some checks need the file, not just the hunk, so the script reads it at the base (one
`git cat-file --batch`) and rebuilds the new side by applying the hunks from patch.diff —
`build` gets the same answer as `collect` without trusting the working tree:

- **enum** — a member removed, changed, reordered or inserted anywhere but the end, or the
  declaration changed (name, underlying type); C#, Java, Kotlin, TS, C/C++ (anonymous
  `enum {` too), Rust, Protobuf, FlatBuffers, Thrift, Swift `case`s, Python
  `Enum`/`IntEnum`/`StrEnum`/`Flag`/`IntFlag` classes. Appending is not raised — after the last
  member, or before Java's `;` that ends them — nor is a Swift enum with no `case` (a
  namespace), nor a key added to `CodingKeys`. A member put under `#if` or into a block comment
  counts as removed. Not seen: Rust struct variants and Java constants with a body spread over
  several lines.
- **memorypack** — in a type that is `[MemoryPackable]` at the base: a field or its order
  removed, rewritten or reordered; a `MemoryPackOrder` not above the type's highest, a
  `MemoryPackUnion` tag not above the highest that type already has; an implicit-order field
  inserted before the type's other fields; a MemoryPack attribute added, `[MemoryPackable]`
  itself changed; a field put under `#if` or into a block comment. A new type, fields added at
  the end, private fields without `[MemoryPackInclude]`, comments and methods are not raised.
  Not seen: positional records (`record Save(int A, int B)`), whose members have no block.
- **only reformatted** — a JSON file (`.json`, `.asmdef`, `.asmref`) whose parsed value, key
  order and number and literal types included, did not change gets that fact and loses its rule
  hits.

Range ends are pinned to commit ids at collect (`spec.base`, `spec.new`), so these checks and
the rules read the same base in `build` after the branch has moved on; when the hunks do not
fit the base all the same, the file checks are skipped rather than run on the wrong text.
- **checks switched off** — a C# file with `Il2CppSetOption(Option.*Checks, false)`, an
  `unsafe` class or struct, or `[SkipLocalsInit]` gets that fact on each of its units, so a
  reviewer looks at index writes there twice.

A repository adds its own in `.claude/review-risk`, one rule per line, `#` for comments:

- `path <pattern>` — a file, matched like a noise pattern under its old or new path;
- `line <regex>` — any added or removed line of a hunk (Python `re`);
- `section <regex>` — the code around the change: the hunk's `@@` context or any of its
  unchanged lines, e.g. the class or method whose body it edits. Enums and MemoryPack
  types need no rule: the script checks them itself (above).

The file is read from the base, never from the change itself: a change cannot switch off the
rules that would raise it, and rules it adds apply from the next change on. An invalid rule
is skipped with a warning. For the same reason the diff is taken with the base's
`.gitattributes` (`git --attr-source`, git 2.40+), so a new `-diff` cannot turn a file into
"Binary files differ". `build` reads the noise and risk files from the base again rather than
trusting the copies in hunks.json, and stops if the base is gone.

## Triage files

Each fresh reviewer writes `<run dir>/triage-<name>.json` following `triage.md`:
`{"units_seen": N, "suspects": [{"unit", "text"}], "risks": [{"unit", "check"}],
"cleared": [{"unit", "why"}]}`. `cleared` is a line on why a rule's hit is safe, allowed only
on units with a `[risk …]` tag (a rule or a check raised them); it shows under the hit and never folds the unit. `build` checks the
files (two at least, ids from the snapshot, non-empty text, `units_seen` within the snapshot) and
merges them into the queue, the same risk or `cleared` text from several reviewers shown once
with their names; a reviewer that read fewer units than the snapshot has is flagged on the
page. Whether a unit has an author note does not move it into or out of the queue: the units
without one are a count under the verdict and a list in the Self-check. `build --all` renders
the whole diff alone — no triage files, no queue — and that page is byte-for-byte what the
goldens pin.

## Run directories

`collect` writes to `~/.cache/kensei-diff/<repo>/<timestamp>/` (mode 0700) and keeps the newest
20 runs per repository there. A caller that links to the page from its own record passes
`--out-root <its run dir>`: rotation then counts only the runs under that root. That root must be
outside the work tree or git-ignored, or the tour shows up as untracked files in the next `collect`.
