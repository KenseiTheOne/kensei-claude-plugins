---
name: diff-tour
description: Annotated diff for self-review before a commit — renders the changes (uncommitted work with untracked files, or a ref or range) as a local HTML page in the system browser that opens on a queue of what the user should look at (suspected failures and risky changes raised by two fresh reviewers and the script's risk rules, everything else folded), with the whole diff a click away — file by file, side by side or unified, with notes on what each change does and why it was made in this conversation, and every change no note explains marked "Unexplained". Use when the user asks to see the changes — "show the diff", "what changed", "review before commit", "diff tour", «покажи дифф», «что поменялось», «дифф-тур», «ревью перед коммитом» — or when another skill or the user's CLAUDE.md says to show changes this way. Read-only — it never stages, commits or edits the repository. Run it when showing the changes is asked for, not on your own after every edit.
argument-hint: "[all] [ref | a..b]"
---

# diff-tour — what changed, and why, before you commit

The user runs this before committing, to see that every change in the diff was made on purpose.
The built-in `/diff` already shows *what* changed. This page adds *why*, and its one signal is
**Unexplained**: changes that no note from this conversation accounts for — leftover debug code,
a stray edit, work from an earlier session.

The page opens on the **queue** — what is worth the user's attention, so the rest can stay out
of sight: *suspected failures* that a fresh reviewer can state concretely, then units *worth a
look* — raised by a reviewer with a question to check, by the script's risk rules (enum
members and MemoryPack fields inserted or removed, other serialized format, destructive
commands, secrets in code, build settings, weakened tests, the project's own
`.claude/review-risk`), or by the author's own flags. Under a rule's hit a reviewer may add
why it is safe; that does not take it out. Everything else is folded.
**Only the script folds; nobody can take a unit out of the queue** — the model and the
reviewers can only add to it. Notes do not move units either way: a unit without a note is
counted under the queue and marked Unexplained in the whole diff, but not raised for that
alone (on a big diff most units have none), and a note — `session` or `inferred` — takes
nothing out.

A switch shows the whole diff: a headline and a plain-language paragraph, summary panels (the
script's own self-check first), a sticky bar with the file list and a side-by-side / unified
switch, then one card per file with notes placed under the lines they explain. It is a local
file; nothing is uploaded.

Design rationale (the owner's notes, not shipped with the plugin):
`~/docs/brainstorms/2026-09-24-diff-tour-skill.md`.

## Rules

1. **Code reaches the page only through the script.** notes.json refers to units by id and
   quotes at most a piece of one line to anchor a note. Do not write or edit `index.html`,
   `patch.diff`, `hunks.json` or `index.txt` by hand.
2. **Unexplained is a result, not a failure.** A unit is explained when at least one note is
   about it. Write a note only when the change is part of something done on purpose. Never
   write a vague note to reach full coverage — a unit you cannot account for stays
   unexplained, and that is the most useful thing the page can show. The script does not
   reward coverage: units without notes are not an error.
3. **`source: "session"` only for a reason you can point to in this conversation** — the user
   asked for it, a bug was found, a decision was agreed. Everything else, and any doubt, is
   `"inferred"`. After a compaction, the summary counts only for what it states explicitly.
   A file that a skill's procedure produced as a by-product (run notes, reports, copied
   evidence) has no reason of that kind, even though a step told you to make it: mark it
   `inferred` and say what produced it, so the user can decide whether it belongs in the commit.
4. **Look at every unit before writing about it.** A hunk you remember writing can still carry a
   line you do not remember. When part of a hunk is unexplained, anchor a note with
   `kind: "stray"` under that line.
5. **Panels state facts from this session only.** A check pill such as `6/6 tests` needs the
   run that printed it in this conversation. What was not done (commit, device check) goes in
   as a pill with `warn: true` — never leave it out to look finished.
6. **Read-only.** Nothing is staged, committed, stashed, reverted or edited in the repository.
   Only the run directory is written. What to do about unexplained units is the user's call.
7. **The queue is the reviewers' and the script's, not yours.** Never write or edit a
   `triage-*.json` or `hunks.json`, and never tell a reviewer what to find or leave out. You
   can still raise a unit yourself through a note `kind`; nothing you write can fold one.

## Steps

1. **Collect.** Run from the repository:

   ```
   python3 "${CLAUDE_SKILL_DIR}/difftour.py" collect $ARGUMENTS
   ```

   A leading `all` is dropped by collect itself (unless it is the only argument and a ref named
   `all` exists): it only skips the triage step.

   - No argument: HEAD against the working tree, untracked included. When nothing is
     uncommitted (the work was just committed), collect compares HEAD with its upstream
     instead — the commits not pushed yet — or, when the upstream has them all or there is
     none, with the default branch (`origin/HEAD`, `main`, `master`): the branch's own work.
     It then prints `Nothing uncommitted — showing the commits HEAD has beyond <ref> instead.`
   - A ref: the working tree against the point where HEAD's history meets that ref (the
     merge-base) — `main` gives the branch's own work plus uncommitted changes, not main's
     newer commits reversed.
   - `a..b`: that range only, no working tree. Either side may be a commit or a tree id (a
     `git write-tree` result). `a...b` starts from the merge-base of two commits. A range's
     reasons are mostly `inferred` unless the work was done in this conversation.

   It prints the run directory, the base, the totals, the length of patch.diff, then one line
   per unit:
   `id  status  path  @@ header [facts] [risk …]  +added -removed  L<line in patch.diff>` — the same
   table is saved as `<run dir>/index.txt` for the reviewers.
   `hN` is one hunk, or a whole file that has no hunks (binary, pure rename, mode change).
   `nN` is a whole file matching a noise pattern: lock files, minified, source maps, and the
   repository's optional `.claude/diff-tour-noise` (syntax in `${CLAUDE_SKILL_DIR}/README.md`). Noise
   units need a note too: a lock file that changed for no reason is worth seeing.
   - `No changes.` → nothing uncommitted and no commits beyond the upstream or default branch
     (or the given ref/range is empty). Tell the user and stop. When it adds `HEAD shares no
     history with <ref>` (an orphan branch with its own commits), say so and ask which ref or
     range to compare with.
   - Exit 2 → show the error and stop.
   - A `[risk …]` tag on a unit — the script's risk rules raised it; it will be in the queue
     whatever anyone writes.
   - A size `WARNING` → mention it to the user and carry on with the tour. Collect again with a
     narrower ref only if the user asks for one.

2. **Triage** — skip it when the argument starts with `all`. Launch two
   fresh reviewers at once, in the background, with the Agent tool: `general-purpose`, effort
   `xhigh`, the model not overridden (this is the safety net — do not save on it). Their
   prompt names only the brief, the run directory, their name (`1`, `2`) and the user's
   language:

   > Follow `${CLAUDE_SKILL_DIR}/triage.md`. Run directory: `<run dir>`. Your name: `1`.
   > Write `text`, `check` and `why` in Russian.

   Nothing about the change, its purpose or your own view — an independent read is the point.
   While they work, go on with steps 3–4; build refuses to run on fewer than two triage files.

3. **Read the units.** Read `<run dir>/patch.diff` in one pass when collect reports up to
   2000 lines; otherwise read it in pieces by the `L` offsets, so that every unit is read once.
   Recall from this conversation why each change was made.

4. **Write `<run dir>/notes.json`.** An example for a Russian-speaking user:

   ```json
   {
     "lang": "ru",
     "title": "Моб больше не агрится на корабль в безопасной зоне станции",
     "lede": "Что было не так и что делает фикс — 2–4 простых предложения, можно `code`.",
     "link": { "url": "https://app.clickup.com/t/…", "label": "ClickUp 869f6uw9t" },
     "panels": [
       { "title": "Что изменилось", "text": "1 файл игрового кода, 2 файла тестов: …" },
       { "title": "Проверки", "checks": [
         { "pill": "6/6", "text": "новые тесты зелёные; до фикса 4 были красными" },
         { "pill": "не сделано", "text": "коммит, пуш", "warn": true } ] }
     ],
     "files": [
       { "path": "src/Npc/ServerNpcAiSystem.cs", "role": "игровой код", "kind": "prod" },
       { "path": "tests/NpcSafeZoneAggroTests.cs", "role": "новые тесты", "kind": "test" }
     ],
     "notes": [
       { "unit": "h3", "after": "if (!sp.InSafeZone && d < minDist)", "source": "session",
         "text": "**Сам фикс.** Корабль в зоне больше не может стать ближайшим …" },
       { "unit": "h2", "source": "session",
         "text": "**Блок перенесён, а не удалён** — теперь он идёт после станций." },
       { "unit": "h5", "after": "Debug.Log", "side": "new", "kind": "stray", "source": "session",
         "text": "Остался после отладки цикла агро — убрать до коммита." }
     ],
     "unexplained": { "h7": "добавляет Debug.Log в Update" }
   }
   ```

   - `lang` — the user's language; it picks the page labels (`ru`, otherwise English).
     Write every text field in that language.
   - `title` — the change in terms of behaviour, one sentence. `lede` — the problem and the fix
     for someone who has not read the code.
   - `link` — optional, http(s) only: the task, if there is one.
   - `panels` — optional, up to three; each has `text` or `checks` (`pill`, `text`, `warn`).
     The script adds its own Self-check panel (unexplained, flags, inferred notes, staleness) —
     do not repeat those counts.
   - `files` — optional: the order of the file cards (most important first; files you leave
     out follow in diff order), a short `role` badge, and `kind` — `prod` (highlighted badge),
     `test`, `docs`, `config`, `meta`; `kind` without `role` shows a default badge.
   - `notes` — the heart of the page. Each note is about one unit and says what the change
     does and why. Lead with a **bold phrase**. With `after`, the note sits under the line
     whose text contains that string; it must match exactly one line of the unit — quote more
     of the line, or narrow it with `"side": "old"` (removed lines only) or `"new"` (added lines
     only); context lines are matched only without `side`. That is how a duplicated line next
     to its original is anchored. Without `after`, it sits at the top of the unit. A unit can have
     several notes; say what a reviewer would otherwise have to work out, not what the line
     already says.
   - `kind` on a note — only when there is something to check: `untested` (not run or not
     covered by a test), `decision` (a debatable choice with a real alternative), `temporary`
     (temporary or debug code), `stray` (part of the hunk nothing explains).
   - `unexplained` — optional; *what* changed in a unit that has no notes, never *why*.
   - Markup — in `title`, `lede`, panel `text`, check `text`, note `text` and `unexplained`
     only: `backticks` for code, `**bold**` (may contain code), line breaks. Nothing else; HTML
     is shown as text. Panel titles, pills, roles and the link label are plain text.

5. **Build**, once both reviewers have finished (`--all` instead of triage when the argument
   starts with `all`):

   ```
   python3 "${CLAUDE_SKILL_DIR}/difftour.py" build "<run dir>" --open
   ```

   Exit 1 lists every problem in notes.json and the triage files — fix the notes yourself; for
   a triage file, send the problem back to the reviewer that wrote it (SendMessage) rather
   than editing it. Start a fresh reviewer with the same name in its place only when that one cannot be reached,
   and only over a problem build reported — never because of what it found or left out. Without triage files and without `--all`, build stops and says so. Never
   rerun collect to get past a problem: it takes a new snapshot with new ids.
   `--open` shows the page in the system browser. Never open it with a bare `open`: terminals
   such as cmux shadow `open` on PATH and put the page in a side pane instead.

6. **Report** in the user's language: the page path, the base when collect fell back to one,
   the queue line and every suspected failure as build printed them, a reviewer `WARNING` if
   one read only part of the diff, then the counts line and the Unexplained list. If build printed the drift
   `WARNING`, say it first — the page no longer matches what a commit would contain. Then
   stop. No follow-up action unless the user asks for one.

## When another skill calls it

A skill that has its own snapshot to show (for example ticket's publish gate) passes the exact
range and its own run directory: `<base_sha>..<reviewed_tree> --out-root <its run dir>`. The
steps are the same, with these differences:

- A tree id is accepted on either side of the range, so the page shows the reviewed snapshot
  even if the working tree moved on.
- `--out-root` puts the tour under `<its run dir>/<repo>/`, so the page lives as long as the
  caller's record that links to it. The default cache keeps only the newest 20 runs per
  repository. That root must be outside the work tree or git-ignored, or the tour shows up as
  untracked files in the next `collect`.
- The calling skill's agreed criteria, test runs and review findings in this conversation
  are reasons you can point to, so notes built on them are `session`; the rules above still
  apply unchanged.
- The triage step runs as usual unless the caller asks for `all`.
- Hand back the `Page:` path, the queue line, every `!` line (suspected failures), any
  `WARNING` and the counts line from build's output instead of the full report; the caller
  decides what to show.

Run directories are private to the user (mode 0700).
