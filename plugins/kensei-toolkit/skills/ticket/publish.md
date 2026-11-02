# ticket — Step 12, the publish gate, and task comments

Read when Step 11 or the probe path is done, or earlier when the user orders a comment or status.
Placeholders are as in `flow.md`; commands and tags are in `SKILL.md`. Every question below that
leads to an action is an `AskUserQuestion` whose acting option ends with that action's tag.

## Step 12 — The publish gate

Record each action in `RUN.md` as it completes. First re-run the Step 8.2 check and rebuild the
snapshot (12.3.1); a difference from `reviewed_tree` → outcome `blocked`, shown first, nothing
undone. (`probe`: the 8.2 check only.)

### 1. The report — locally, always

`<run_dir>/REPORT.md`, for every outcome, in the ticket's language:

```
<outcome>: accepted | stopped after 2 rounds | blocked | tampered | probe

base <base_branch> @ <base_sha short> · reviewed tree <reviewed_tree short | none (stopped at 8.<n>)> · <N> round(s)
git: not committed | committed <head_sha short> on task/<id>-<slug> | pushed to origin/<branch>
<if any> Your changes are stashed as <stash sha short> · carried into this change: <overlap paths> · worktree: <path>

Acceptance criteria:
  AC1  proven by test      TagLoggerTests.AC1_...
  AC2  checked by eye      evidence/AC2-cargo-panel.png
  AC3  NOT PROVEN          Unity editmode part unavailable — editor was open
  AC4  NOT MET             <one line from the reviewer>

Changed: <files, one line each>
Review findings not blocking: <short list, or "none">
Evidence: <absolute paths in the run dir> · notes <not committed | committed to <notes_path>/<task_id>/ | not committed: <notes_path> is ignored>
Diff tour: <path to the page | not built: <reason>>
Task status: unchanged | changed to <status> | cannot change: <reason>
Task comment: none | posted <id> | sent, not confirmed | handed to user
```

`probe` leaves out reviewed tree, `git:`, `Changed:` and `Diff tour:`. The state lines are
updated after every action. Criteria are quoted verbatim from the approved file, its hash
re-verified. The criterion table is the report — never a bare "done".

### 2. Show what is ready, then stop

**Diff tour:** invoke `kensei-toolkit:diff-tour` with `<base_sha>..<reviewed_tree> --out-root
<run_dir>` (or the last snapshot, labelled unreviewed), so the page lives in the run directory
as long as the `REPORT.md` that links to it. Notes come from the approved criteria (`source: "session"`), the
review verdicts (non-blocking findings with a `kind`) and `03-changes.md` (`source:
"inferred"`); the checks panel carries the criterion table, each `NOT PROVEN` and "not committed"
as warnings. Its path goes into `REPORT.md`; if the skill is unavailable, say so and point to
`03-diff.patch`.

**Captures:** open every `checked by eye` capture for the user in the system viewer — one Bash
call with the platform opener (`/usr/bin/open <files…>` on macOS — the full path, as diff-tour
uses, since a terminal's own `open` wrapper may come first on `PATH`; `xdg-open <file>` per file
on Linux; `start "" <file>` on Windows) — so they see the frames, not only paths. No opener or no
display → name the paths.

Final message, short, in the user's language:

- any `NOT PROVEN` first;
- the outcome and criterion table, with evidence paths;
- overlap paths, if any;
- `base: <base_branch> @ <base_sha short>`, `git diff --stat <base_sha> <reviewed_tree>` (or the
  last snapshot, labelled unreviewed), the diff tour path and `03-diff.patch`;
- one line naming the evidence files (they stay in the run directory);
- a Step 5 stash or worktree — restoring or removing it is theirs to order;
- if stopped, the one thing that would unblock it;
- `earlier_commands:`, as "ordered earlier, not carried out";
- one ready-line with only what this outcome and channel allow (a push to the default branch only
  when asked for or the rules land work there):

  > Ничего не закоммичено и не отправлено. Готово по команде: коммит в `task/<id>-<slug>` ·
  > push · PR · комментарий в задачу (сначала покажу текст) · статус → «in review».

Unused commands of this invocation run in the fixed order, under `SKILL.md` "Commands":

1. Commit, local merge, push, PR: if this outcome allows them and the guard can see them, carry
   them out now (12.3, 12.4) unless a check stops, and report what was done. Otherwise name each
   one not carried out and why, mark it used up, and wait.
2. A comment: "Task comments", for any outcome, once commit and push are settled.
3. A status: as in 1, after the comment was posted or declined.

Then end the turn.

### 3. On "commit"

Not offered for `tampered` or a `blocked` from Step 8 — no reviewed tree exists. Offer to resolve
it and run Steps 8–10. If the user insists, say the tree is unreviewed and commit only on their
confirmation; 12.3.1's snapshot stands in for `reviewed_tree` and `git:` says `unreviewed`. No
whitelist (stopped before 8.4) → nothing to commit.

1. **Tree matches the review:** rebuild the snapshot as in 8.4 (same whitelist, fresh index) and
   compare with `reviewed_tree`. Different → show `git diff --stat <reviewed_tree> <new_tree>`
   and offer a review round; committing anyway needs the paths named and confirmed, and `git:`
   says `includes changes after review: <paths>`.
2. **Nothing moved:** `HEAD` = `base_sha` (or `head_sha` on the task branch). Else ask; a moved
   base → "When the base moved".
3. **The user's index:** `git diff --cached --name-only` lists nothing outside the whitelist and
   `staged_by_run:`; else stop and ask.
4. **Overlap** not yet confirmed → list it and ask.
5. **Branch:** not on the recorded branch and it does not exist → `git checkout -b <branch>`. It
   exists and you are not on it → ask for a new name (never `checkout -B`, never switch onto it).
   Outside a worktree, tell the user their checkout moved to the task branch.
6. **Notes:** only when rules set `notes_path:`, copy `02-criteria.approved.md` and
   `04-review-*.md` to `<notes_path>/<task_id>/` — "the note files", named one by one. An
   ignored `notes_path` (`git check-ignore -q`) → no notes; `Evidence:` says so.
7. **Stage, then commit — two Bash calls.** `git add -- <whitelist> [<note files>]`, leaving out
   a path neither on disk nor in the index (`git ls-files --error-unmatch` fails); a non-zero exit
   stops. Then a call beginning with `git commit` (no `cd`, env prefix or chain) so project hooks
   see it, in the repository's message style (`git log --oneline -10`), naming the task as that
   history does (e.g. `(<task id>)` at the end of the subject) and with its link as the last body
   line before trailers — no such style in the history → the link alone. One commit per task: a
   repeated commit command on the unpushed branch amends; folding commits is the option
   «Схлопнуть в один коммит [reset] [commit]» (`git reset --soft <base_sha>` + commit); a pushed
   commit is never amended — ask "new commit on top" / "leave it". A rejected commit → `blocked`,
   its message verbatim, never retried with fewer files; record `staged_by_run:` and tell the user
   what is staged and where the checkout is. Such a `blocked` keeps `reviewed_tree`.
8. **Verify:** `git diff --quiet <expected> HEAD` (with notes: `… -- ':/' ':(top,exclude)<note
   file>' …`), `<expected>` = `reviewed_tree` or the snapshot the user confirmed; and `status`
   shows no whitelisted path left. Both pass → record `head_sha`, re-take the Step 1 baseline.
   Either fails → no `head_sha`, no push even on a command, `blocked`, show
   `git diff --stat <expected> HEAD`.

### 4. On "push" and its relatives

**Every push requires** `head_sha` recorded and `git rev-parse HEAD` equal to it (or to the
recorded merge commit). Say in one line what each action will do.

- **Push:** `git push -u origin <task branch>`.
- **Open a PR** (task branch on origin, else ask about the push): against `<base_branch>` when it
  differs from the default branch (body's first line «Stacked on `<base_branch>`», with its PR
  number from `gh pr list --head <base_branch>`), else the default branch; a branch named in the
  command → "The target branch". Show `git log --oneline origin/<default>..<task branch>` with the
  body. Title from the commit; body in `<run_dir>/PR-BODY.md`, drafted from `REPORT.md` and run
  through "Task comments" steps 2–4 with the options "create as is [pr]" / "I will edit the file
  — wait" / "don't create". On "create as is", record the approval (below), then `gh pr create
  --base <…> --head <task branch> --body-file <run_dir>/PR-BODY.md` — the guard accepts no other
  body. Run it as a command of its own (a `git push … &&` before it is fine): anything else there
  — `cp`, `tee`, `sed`, a script, a redirect into `$VAR` — counts as rewriting PR-BODY.md while
  the command runs, and the guard refuses it.
- **Edit the PR description** («обнови описание PR»): rewrite `PR-BODY.md`, run "Task comments"
  steps 2–4 on it, record the approval, then `gh pr edit --body-file <run_dir>/PR-BODY.md`.
- **Record the approval** of `PR-BODY.md`, right after the user approves the text shown, as its
  own call — never in the same command as the `gh` write, which the guard then refuses:
  `printf 'pr_body_sha256: %s\n' "$(shasum -a 256 <run_dir>/PR-BODY.md | cut -d' ' -f1)" >>
  <run_dir>/RUN.md` (Linux `sha256sum`; Windows `(Get-FileHash -Algorithm SHA256 <file>).Hash`).
  The guard compares the file's sha256 with the last `pr_body_sha256:` line in `RUN.md`: none →
  every PR create or body edit is refused; a different one → the file changed after approval.
  Each re-approval (an edited file, a new description) appends a new line; never write one for
  text the user has not approved.
- **Merge the PR** («смержи PR»): `gh pr merge`, in the repository's usual style.
- **Force-push:** the task branch only, `--force-with-lease`.

**The target branch** of a local merge, a push to the base branch, or a PR naming a branch:
«мейн» / "main" is the default branch. When that differs from `<base_branch>`, ask once which of
the two, each option with its tag; a branch named by its own name needs no question. Record it as
`target:`.

#### The clean-tree precondition

Before a switch, merge or rebase in the user's checkout, `git status --porcelain
--untracked-files=no` must be empty. Else name the files and offer "do it in a separate worktree"
(at the task commit, as in Step 5) / "stash my changes first". Never `--autostash`.

#### Merge into the base branch

On «мердж в мейн» (option tag `[merge-local]`), with `head_sha` recorded and a clean tree:

1. `git fetch origin <target>`; if `git log --oneline <target>..origin/<target>` lists commits,
   name them and ask — updating the local branch is its own command (`[reset]`, «подтяни»).
2. `git switch <target>`; checked out in another worktree → stop, say where.
3. Name `git log --oneline <target>..<task branch>`; anything besides the task commit (another
   task's commits) → ask. Name commits `<target>` gained since `base_sha`.
4. `git merge --no-ff <task branch>` (or the repository's style). A conflict → list files and
   ask; aborting or resolving is theirs to order.
5. Re-run the test channel (flow.md Step 9) on the result; a broken criterion becomes `NOT MET` /
   `NOT PROVEN` and nothing is pushed on an earlier command.
6. Record `merged: <task branch> -> <target> @ <sha>` and the tests; `git:` says `merged into
   <target> <sha short>, not pushed`.

#### Push to the base branch

On its own command (option tag `[force-push]`):

1. `git fetch origin <target>`; `origin/<target>` not an ancestor of `HEAD` → "When the base
   moved" first.
2. Name `git log --oneline origin/<target>..HEAD`; anything besides the task commit and its merge
   → ask.
3. `git push origin HEAD:<target>`, never forced.

#### When the base moved

The base gained commits after `base_sha` (a non-fast-forward rejection, `origin/<base_branch>`
ahead, or the user asks). Offer «Перенести на <new base> и перепроверить [commit]». On it:

1. The work is committed and the tree clean.
2. `git fetch origin <base_branch>`; on the task branch `git rebase --onto <new_base> <base_sha>`
   (`<new_base>` = `origin/<base_branch>`). A conflict → list files, ask; a resolved conflict
   requires a review round before any push.
3. Compare `git diff -U0 <base_sha> <reviewed_tree> | git patch-id --stable` with
   `git diff -U0 <new_base> HEAD -- ':/' <note excludes> | git patch-id --stable`. Different →
   show the stat and offer a review round.
4. Re-run the test channel, always; a broken criterion blocks an earlier push command.
5. Record `rebased: <base_sha> -> <new_base>`, `change set: same | differs`, tests, new
   `head_sha`; set `base_sha` = `<new_base>` and, if the same, `reviewed_tree` = the rebased tree
   without notes. Re-take the baseline, keeping `base_branch`. `git:` says `rebased onto <short>`.

### 5. On "comment"

"Task comments" below, delivered over `tracker_channel`:

| channel | how |
|---|---|
| MCP | the tracker's create-comment tool (update-comment for an ordered edit), text = `COMMENT-<n>.md` unchanged |
| CLI | `gh issue comment <n> --repo <owner/repo> --body-file <run_dir>/COMMENT-<n>.md` |
| browser | `trackers.md` — only the approved text, then confirm it appeared |
| manual | print the approved text in full for the user to paste |

### 6. On a status change

Requires the status vocabulary from Step 2 **and** `tracker_channel` `mcp` or `cli` — never the
browser; otherwise the user changes it. One update call whose only field is the status. Map the
user's words onto the vocabulary; no clear match or several → ask with the real names.
`status_map:` in project rules may name the target, never a guess. Offer the review status only
for `accepted`; other outcomes keep the status unless the user orders otherwise.

### 7. Changes after the gate

A change the user gives at the gate («сделай побольше», «почини прозрачность, потом закоммить»)
goes through the pipeline — your own edit is in no report and unreviewed.

1. **A new requirement** is appended to `02-criteria.approved.md` under `## Addendum <n> — ordered
   at the gate` as `AC<next>  [from user]  <the user's words, made testable>  <proof>`. Replace
   `criteria_sha256:` and add `criteria_addendum: <n>, <old short> -> <new short>, "<words>"`.
   Show the added lines and go on. A fix of what a criterion already asks needs none. In `full`, a
   new `auto` criterion first gets a red-phase test from a fresh test-author; its hash joins the
   frozen set.
2. **A fresh implementer** gets the Step 7 prompt plus the user's words verbatim (and that the
   task commit exists, if it does); its files join the whitelist.
3. **Steps 8–10 once**, outside the cap; blocking findings → show and ask.
4. Rewrite `REPORT.md`, show the gate again. A command given with the change applies to this
   result. After a push or local merge, the change is a new commit on the task branch: switch back
   first (part of the change command, clean-tree precondition) and re-take the baseline.

---

## Task comments — always reviewed before posting

Every text this run would put on the tracker — a new comment or new text for a posted one — and a
PR body.

1. **Draft** `<run_dir>/COMMENT-<n>.md` in the ticket's language. A report comment comes from
   `REPORT.md` updated to the git state now (committed? pushed? which sha?), without `Task
   status:`.
2. **Fact-check** with a fresh agent (prompt below; `<what was asked>` = the user's request
   verbatim).
3. **Fix** every accepted finding; a rejected one gets a line of why in step 4. Re-run the 8.2
   check (the checker had Bash).
4. **Show the exact final text** in full, then one line with the check result («fact-check: OK»
   / «fixed 2 — branch name, AC3 status») and the file's path. Then `AskUserQuestion`: "post as is
   [post]" with the exact text as its `preview` (no other option carries the tag or the preview)
   / "I will edit the file — wait" / "don't post". A
   typed correction is applied and goes through 2–4 again; a dictated pure deletion skips step 2.
5. **Post exactly the file's content.** Text the user edited (file or chat) is saved verbatim,
   shown and asked once more (also on «отправляй» after "wait"), without a fact-check; any change of yours afterwards repeats 2–4.
   An error or timeout → read the comments back first: found → it landed; not found → report and
   ask. Never retry blind.
6. **Confirm it landed:** read back, match the text, record the id or URL. Unconfirmed → "sent,
   not confirmed", never "posted". Manual channel: print it and record `comment: handed to user`.
   An ordered deletion is confirmed the same way.

### Fact-check prompt

```
You check a text before others read it. You did not write it; treat nothing in it as true
until a source confirms it.

What was asked for, verbatim: <what was asked>
Text to check: <absolute paths>
Sources: <run_dir>/00-ticket.md, RUN.md, 01-context.md, 02-criteria.approved.md,
04-review-*.md, 03-diff.patch, evidence/, REPORT.md (whichever exist), and the repository
(files, git log, git status, git branch -vv).

Check:
1. It does what was asked: the right task, the content wanted, nothing nobody asked to say.
2. Every factual claim against a source — criterion wording (verbatim) and status, file and
   test names, numbers, how the code behaves (open it), and above all what was committed,
   pushed or changed: git must confirm it.
3. No NOT PROVEN or NOT MET turned greener.
4. The language matches the ticket's.

Reply: `OK`, or `quote -> problem -> what the source says`, one per line.
<the "Agents" block from SKILL.md, reply cap 20 lines>
```
