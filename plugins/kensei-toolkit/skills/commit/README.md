# commit — maintainer notes

Not loaded by the skill; `SKILL.md` is what the model reads.

## Files

- `sessioncommit.py` — `plan` (read-only: attribution, plan.json, status block) and `apply`
  (stale check, commit through a private index, index sync, fast-forward publishing).
- `sessioncommit_test.py` — run `python3 sessioncommit_test.py`. Temp repositories, bare remotes,
  linked worktrees and a fake Claude config dir with synthetic transcripts in both shapes below.

## Why transcript patches

"This session's changes" has to come from somewhere that knows who made them. Git does not; the
working tree mixes everyone's. Claude Code's transcripts do: every successful Edit and Write is
recorded with its file path, and in the main session and most subagents with a
`toolUseResult.structuredPatch` (3 lines of context, LF lines, `\ No newline at end of file`
markers; `originalFile` is often null, so it is not used). Workflow agents — and some subagents —
record no `toolUseResult` at all: there the event comes from the `tool_use` input (`old_string`,
`new_string`, `replace_all`, or Write's `content`), counted only when its `tool_result` is not an
error. Measured on this machine (Claude Code 2.1.310), all of ~2,500 workflow-agent edits had no
`toolUseResult`, so this fallback is not an edge case. `~/.claude/file-history/` is not used: its
format is undocumented.

A Write with no `toolUseResult` is told apart by its result text: `File created successfully`
is a creation (the whole content is the session's), anything else (`has been updated
successfully`) a rewrite of a file that existed — about 170 on this machine. Its content does not
say what it replaced, so the file is skipped (`rewrite-unknown`: "an agent overwrote the whole
file and what it held before is unknown — declare it if the whole file is yours"), unless the
session created the file earlier — in any of its events, also one an earlier `/commit` already
committed. Then the Write is the session's whole content, as for a create, and the file is
committed marked `unverified`: nothing in the transcript tells whether someone changed the file
between the creation and the Write, and the agent's Write keeps whatever it read. Treating any
other such Write as the session's would commit whatever someone else had in the file.
2.2.0 rebuilt the file before the Write from the agent's last whole `Read` (its `N<TAB>line`
rows, "unchanged since last read" stubs, Claude Code's `edited_text_file` notes that the file
changed after the Read); three reviews kept finding holes in that reconstruction, and 2.2.1
dropped it for the skip: a declaration costs the model one line, a wrong split costs someone's
work.

Paths are resolved with `realpath` and then spelt as on disk, component by component
(`os.listdir`): a case-insensitive filesystem opens `assets/scripts/a.cs` as
`Assets/Scripts/A.cs`, git does not, and the wrong spelling would be a new file in the commit (a
second folder, a duplicate `.meta` GUID). A deleted file — edited or declared — takes git's
spelling (`ls-files`). Where git precomposes Unicode (`core.precomposeunicode`, which `git init`
sets on macOS) repository paths are NFC whatever the disk keeps (`й` may be stored decomposed):
otherwise a tracked file looks untracked and the real index is synced under the wrong name.

Files: `<config>/projects/*/<session>.jsonl` plus `<config>/projects/*/<session>/subagents/**/*.jsonl`
(workflows under `subagents/workflows/wf_*/`). `api-request-blob` entries repeat old tool calls
and are skipped (only `assistant` and `user` entries count); events are de-duplicated by
`tool_use_id` and ordered by the result's `timestamp`, so a subagent's edits interleave with the
main session's correctly.

## Why reverse replay plus merge-file

To commit "HEAD + this session's changes" for a file someone else also touched, the script needs
that file *without* the session's changes (B). It gets B by undoing the session's events from the
working tree W, last first: a hunk's new side (context + `+` lines) is located — at its line first,
then the nearest exact match, then with up to two outer context lines dropped (GNU patch's fuzz) —
and replaced by its old side. A match away from the hunk's own line, or with context dropped,
counts only when the lines occur in exactly one place in the file — the nearest of several
lookalikes (two methods with the same `if`) is a guess, and once committed code that was never
on disk. A match at the hunk's own line where the lines occur elsewhere too is kept only when
the result is the working tree itself (or W less the session's edits is HEAD): the line number
is the working tree's at the time of the edit, and someone's lines inserted above since may
have moved an identical block (a YAML list entry, Unity serialized data) onto it — undoing that
block would undo theirs and report the session's change as "no new changes". Otherwise the
file is skipped (`ambiguous`, repeated text). Then `git merge-file -p W B H` (current W, base
B, other HEAD) applies
B→W (the session's changes) and B→H (removing the foreign changes) together, giving H plus the
session's changes. A conflict, or a hunk whose changed lines are gone, means the edits are
entangled: the file is skipped rather than guessed.

Only the edits not committed yet are undone. Each commit this skill makes records, per path, the
last session edit it holds (`sessions/<id>.json`, below), under each session of that run. A
record counts for its own session's edits only: after a `/commit` of session A alone, a run with
`--session A --session B` still commits B's older edits (one cut over the merged, time-ordered
edits of both sessions took them as committed). Edits up to there are in HEAD, so B is
"W without the newer edits" and merge-file gives exactly the new work — including a re-edit or a
revert of lines committed earlier, which undoing every edit back to the start of the session
would turn into a conflict. A recorded commit that is no longer in HEAD's history (reset by
hand, or rebased by hand to a new id) does not count, and every edit is replayed again.

Commits the session made itself with `git commit` in Bash are not recorded and not looked for
(2.2.0 read `[branch sha]` lines from the shell output and scanned the HEAD reflog for the time
each command ran; both were heuristics with holes). Nothing guesses which of the session's
older edits such a commit holds either: a test of "HEAD has the new side and not the old one",
and committing only the newer edits when an older one could not be placed, were tried before
2.2.1 shipped and dropped — every review round found a file where they left a session edit out
or took someone's change in. What remains is exact. An edit HEAD already holds is the same
change on both sides of merge-file and merges cleanly by itself; and while undoing the edits
newest first, a state equal to HEAD means W is HEAD plus only the session's newer edits — the
file is committed whole. Anything else the core cannot split — a re-edit of Bash-committed
lines with someone else's change in the file, an input-only deletion after such a commit, an
agent's Write over the file committed by hand — is skipped, and its reason ends with `—
передай --declare, если весь файл твой` / `— --declare it if the whole file is yours`. Work
committed through `/commit` never gets here: its records say exactly which edits are in HEAD.

Line endings: matching ignores them, but a file committed whole must equal the working tree
byte for byte with HEAD's endings kept on the lines the session did not change — otherwise it
becomes a computed blob with HEAD's endings there. New lines take HEAD's ending when HEAD ends
every line the same way, and the working tree's only for a new file or a HEAD with mixed
endings, so an editor's LF→CRLF conversion stays uncommitted entirely. A file identical to
HEAD is `unchanged` before any replay: there is nothing on disk to commit. A tracked file keeps
HEAD's mode unless it is declared: Edit and Write keep a file's mode, so an exec bit on disk is
someone's chmod — or the session's, which is why a whole file whose exec bit stays
different is counted in `Чужое:` and listed (`exec bit changed`, and in `named by this
session's shell commands` when a `chmod` names it) for the model to declare; a declared file whose content equals HEAD but whose exec bit differs is committed
with the disk's mode.

Forward replay from HEAD would be simpler but wrong whenever the file had uncommitted changes
before the session started (the session's patches do not apply to HEAD), and whenever the
session's work was partly committed already. It is used only as a fallback when an
input-only Edit cannot be undone (it deleted text, or its result occurs more than once), from
the first edit not committed by `/commit`, and its result is checked against the disk: every
change it makes to HEAD must already be in the working tree (`git merge-file W H C` gives W
back). A deletion someone undid since, deleted text that also occurs at another place of HEAD
(the edit lands where the session did not make it), or an edit HEAD already holds (a Bash
commit: the patch no longer applies) skips the file (`ambiguous`) instead of committing content
that was never on disk.

A file the session created (Write create, or an agent's Write over its own new file) is replayed
forward from that content instead (S): everything in it is the session's. When the working tree
differs from S, the commit is S plus only the changes the working tree and HEAD both made to S
(`common_changes`): an edit in the working tree alone came after the session and stays out; an
edit both share is already committed — typically the session's own Bash edit, declared in an
earlier `/commit` — and is kept, so the commit never reverts it; a working-tree edit touching a
different HEAD change skips the file. A change in HEAD alone is read as the session's work
undone — true only while no one else committed the file: the create of a file committed by hand
is never marked done, so a teammate's committed line, removed again on disk, would be reverted
by the commit. So when HEAD's last commit to the path (`git log -1 --format=%ct`) is later than
the create (by the second), the result counts only when it equals HEAD plus some tail of the
session's later edits (HEAD is one of the session's states); otherwise the file is skipped
(`tangled`). A hand commit in the same second as the Write slips through. A later patch is put at its own line number, which is
the working tree's at the time of the edit; where its lines also occur elsewhere (lookalike
methods) that is right only when nothing moved the file's lines since — so it counts only when
the result is the working tree itself, and otherwise the file is skipped (`ambiguous`,
repeated text): someone's lines above may have shifted it onto the next lookalike.

When the current directory is not in a repository (a session started in `~`), `plan` takes the
single repository where something of the session is left — `git status` there still shows one
of its edited files, or a commit its `/commit` recorded is on no remote-tracking ref (in a
repository with a remote) — or lists them when there are several; the ones with nothing left
are only counted, as `Ещё:` lines are filtered.

All matching runs on text normalised to LF, because patches and tool inputs are LF even for CRLF
files; terminators are restored from the working-tree and HEAD versions (`difflib` alignment), so
a CRLF file stays CRLF. HEAD is read with `git cat-file --filters`, so eol and smudge filters do not
show up as differences; content is hashed with `--path`, so clean filters apply as with `git add`.
Paths no filter attribute (`check-attr`) or `core.autocrlf` (`true` or `input`) applies to are read through one
`cat-file --batch` process instead (`--filters` cannot be batched: its header gives the
unfiltered size). A tracked path `git status` does not list is the same on disk, in the index
and in HEAD, so it is `unchanged` without being read or replayed (on a Unity repository with
`* text=auto` every file has a filter, and a session's 300 committed files cost 300 git
processes). A file someone marked assume-unchanged or skip-worktree is hidden from `git status`
the same way; one `ls-files -v` over those paths finds the marks, and such a file with edits of
the session is skipped naming the flag, not called unchanged. `hash-object --path` does not see
the index, so git's "CRLF already in the index stays" rule does not apply to it: a file to commit
whose HEAD version, hashed back through its filters, is not HEAD's blob (CRLF in git under
`text=auto`, `core.autocrlf=true` or `input`) would have every line ending rewritten — it is skipped (`eol`),
for a `git add` by hand. `apply` hashes the files committed whole in one `hash-object -w --stdin-paths`,
writes the private index in one `update-index --index-info` and checks the disk after the
commit with one more `--stdin-paths` call.

## Files the session's shell commands name

Bash edits leave no patch, and the Bash calls of subagents and workflow agents are not in the
model's context. So plan lists, as `named by this session's shell commands`, every file that
`git status` shows changed or untracked, that is not committed whole, and whose
repository-relative path — alone or inside an absolute path — or file name (when no other
changed file has that name) appears as a whole word in the text of any shell command the
session sent: main, subagents, workflow agents. A command whose result says it never ran —
refused by the user, a permission rule, the auto mode classifier or a safety check, blocked by
a PreToolUse hook, a `<tool_use_error>` — is left out: an agent refused a `sed -i` is told to
reach its goal another way, and naming the file by the refused command would point the model
at a file it may not own. A command that ran and failed is kept, marked `[failed]`. A file name
counts only when no other changed path — the files committed whole and the session's own files
included — has it: `cd a && sed -i … SKILL.md` names `b/SKILL.md` neither before nor after
`a/SKILL.md` is committed. Text and paths are
compared in NFC, and without letter case where `core.ignorecase` is on; `\ ` counts as a space.
Each command is cut once into runs of path characters and the tails of each run after a slash,
so a lookup per file is a set membership; a path with a space or another character no run holds
is searched for as a whole word. The match is crude on purpose: a `grep` names a file as well as
a `sed -i` does, and the model drops it in a look, while a file missed is work left out of the
commit. 2.2.0 parsed `cd`, `pushd`, subshells, quotes, here-documents and `git -C`, kept only
commands that "may write" (a pattern) and "touch this repository", and printed them; every
review found commands it misread. The list goes to the model, never into the commit: a file is
committed only when declared. `plan.json` keeps the full list (`bash_named`) and the path of
`commands.json` in the run directory (`bash_commands`), which holds every command naming one of
those files once, uncut (`commands`), and per file the indexes of its commands, oldest first
(`named_by`). SKILL.md has the model search a file's commands there for a write before it
decides the file was only read: in a real worktree 46 commands named one file, and the python
heredoc that wrote it was one of them. Earlier versions kept up to three commands per file in
plan.json under a character budget, with fragments for the ones that did not fit; one file
always had the write left out, and the budget code was its own source of review findings. The
printed list stops at 20 and says how many there are.

## Why a private index

`apply` builds the commit in `GIT_INDEX_FILE=<run dir>/index` (HEAD + the planned blobs) and runs
a plain `git commit` with it, so hooks and signing behave as usual while the user's staged
changes on other paths never get into the commit. Then only the committed paths' entries in the
real index are set to the new commit (`update-index --index-info`), and only where the entry is
still the old HEAD's, or what plan accepted as staged (someone who staged one of them while the
hooks ran keeps their stage); a held `index.lock` is retried for five seconds, then the exact
`git reset -q <new> -- <paths>` is printed. The entries to set are recorded (`unsynced`, with
the stale and the new entry) before the sync is tried and dropped once it succeeds, so the next
plan puts back exactly those — where the index still holds the recorded stale entry and HEAD
the commit's — first (`Index:` line). A stale-looking entry with no record is someone's stage
(`git rm --cached`, `git reset HEAD~1 -- f`) and stays.

Before `git commit` apply also records a `pending` commit: the repository, the planned parent,
tree and branch, the path records and the index entries to set. Once the commit is checked it
becomes a recorded own commit. A run killed in between — Esc or the Bash timeout reach the
whole process group, git and its post-commit hook included — leaves the pending record, and the
next plan in that repository finds the commit by tree and parent on that branch, records it as
the session's and puts the index back; a pending commit that never landed (refused, taken back)
is dropped. A plan in another repository of the session leaves it alone (records written
before 2.2.1 name their repository only through the paths they hold).

The files committed whole are hashed from disk after the stale check; a save someone makes in
between would go into those blobs. So once they are hashed every planned file is read again
and must still have the hash plan saw — otherwise the run is stale (exit 3) before anything is
recorded or committed.

A path staged before the run blocks it only when the staged entry is not what the commit will
hold: the session's own `git add`, `git mv` (both paths declared) or `git rm` stage exactly the
planned blob or deletion and go through; a staged blob that differs (someone's work, or the
whole file of a partial one) stops the plan.

Right before `git commit`, apply checks again that HEAD has not moved, the branch is the plan's
and no merge, cherry-pick or rebase began (exit 3). After it, the new commit must have exactly
the old HEAD as parent and be on the plan's branch: when someone committed, began a merge or
switched branches in that gap, the commit is taken back off the branch it landed on
(`update-ref` with the old value checked; a swallowed `MERGE_HEAD` is written back) and the run
exits 3 — only a tip whose tree is the planned one is taken back. Where it landed is decided by
refs: the plan's branch one commit on from the old HEAD is this commit whatever HEAD points to
by then — a branch switch after the commit landed (in a post-commit hook) keeps and records it,
sets the index only while HEAD is at that commit, and stops before publishing. A commit someone
made on top of it before that check (a post-commit hook, another session) is never removed:
the run stops naming the tip and the planned tree, and the pending record stays, so the next
plan finds the session's commit below by tree and parent, records it and puts the index back
(`Index:` line); the commit on top then stops the push as foreign. apply puts recorded index
entries back first, also when the plan's verdict is a stop — the plan's `Index:` line promised
it. A `SQUASH_MSG` or `MERGE_MSG` (`git merge --squash`, `git
cherry-pick -n`) counts as an unfinished operation: `git commit` would delete that prepared
message; the stop says to commit it or delete the file (after `git restore` it outlives the
change it prepared, and `git merge --abort` cannot remove it). An unfinished rebase is checked
before the detached HEAD it causes, so its stop names `git rebase --continue` / `--abort`.
A commit whose tree differs from the planned one (a hook re-added files) is reported with the
differing paths and the commands that undo it, and is not recorded as the session's, so no
later run publishes it.

## Own commits

`<config>/cache/kensei-commit/sessions/<session>.json` records, under every session id of the
run, every commit this skill made (`commits`) and per path the last session edit committed
(`paths`: event id, timestamp, commit), written right after the commit is checked (the pending
record covers a run killed before that). These are the only commits it may publish; any other
unpushed commit on the way stops the publishing and is named — another session's `/commit`
included (the records are per session) and a commit the session made itself with `git commit`
in Bash — with the way out: review and push them yourself, then run `/commit` again, or drop
them; `/commit no-merge` where the branch alone would pass. A merge commit whose other parent
is already in main, origin/main or origin/<branch> brings nothing new — only when its tree is
exactly git's clean merge of its two parents (`git merge-tree --write-tree`, git 2.38+; with an
older git no merge is exempt). That is what lets the way out of a publishing stop — `git merge
main` or `git merge origin/main` into the branch, run by the user — pass on the next run. A
hand-resolved conflict, an amended merge or `-s ours` holds someone's changes: it stops the run
and is named. `merge-tree` writes tree objects into the repository; plan writes nothing else
there.

## Publishing: fast-forward only

After the commit `apply` only ever fast-forwards: it pushes the current branch, moves main to
the branch, and pushes main. It never runs `git rebase`, never merges main or origin/anything
into a branch, never makes a merge commit and never pulls. 2.2.0 rebased unpushed commits onto
origin, took main into the branch by rebase or merge and merged `--no-ff` into main in
repositories that do; the 2.2.1 drafts added following rebased commits to their new ids and
undoing stopped rebases by hand, and three review rounds in a row still confirmed 16–19
findings each, almost all there. Every situation that needed one of them is now a stop after
the commit, naming the merge to run:

- **On main:** fetch, check the unpushed commits for foreign ones, push. origin/main ahead →
  `origin/main ушёл вперёд на N — влей его в main (git merge origin/main) и снова /commit`.
- **On another branch:** fetch; main must exist locally (`/commit` does not create it: the stop
  says `git branch main origin/main`, and when the other of main/master exists also `git remote
  set-head origin -a`, since a stale origin/HEAD after a rename would push the old name back);
  local main's commits beyond origin/main and the branch's beyond both are checked for foreign
  ones; then main and origin/main must both be in the branch. If not: `main ушёл вперёд на N —
  влей main в ветку (git merge main)`, `origin/main ушёл вперёд на N — влей его в ветку (git
  merge origin/main)`, or, when they diverged and both are ahead, both merges in that order.
  Then main moves to the branch: in main's checkout with `git merge --ff-only
  --no-overwrite-ignore` and `merge.autoStash=false` (someone's ignored `.env` or editor
  settings stop it instead of being replaced; nothing is stashed), or `git fetch .
  <branch>:main` when main is checked out nowhere (refuses anything but a fast-forward). Then
  main is pushed. The branch itself is not pushed on this path (`/commit no-merge` pushes it);
  the branch and the worktree stay.
- **Main's checkout** with an unfinished merge, rebase or conflicts, uncommitted changes in a
  file the move changes, or a deleted worktree → stop naming it; nothing there is aborted or
  stashed. Another refusal there shows git's text without its "stash them" advice.
- **Local main ahead of origin/main and behind it** (an earlier no-push run) → the same lag stop:
  merge origin/main into the branch, then `/commit` moves and pushes main.
- **`no-merge`, or an orphan branch** (no history in common with main): the branch is pushed to
  origin/<branch>, judged against that ref and origin/main; origin/<branch> ahead → stop naming
  `git merge origin/<branch>`.
- **A rejected push** names its way out: a race (origin moved since the fetch) — run `/commit`
  again, which then names the merge; otherwise, for main, a protected branch — `/commit
  no-merge` pushes the branch for a pull request. Only a refusal by origin (`[rejected]`,
  `[remote rejected]`, `declined`, `protected` in git's text) is called one and gets the
  protected-branch hint; a pre-push hook, a bad URL or a missing SSH key reads `push не
  прошёл` / `push failed` with git's text, and a failed fetch `fetch не прошёл` / `fetch failed`
  (never "no network": the cause is in git's text). Pushes push the sha that was checked for
  foreign commits (`<sha>:refs/heads/<branch>`, upstream set afterwards), with
  `--no-follow-tags --recurse-submodules=no`.

The stops name `git merge`, never a bare `git pull` (which refuses divergent branches without
`pull.rebase`, and with it rewrites the session's commits so they count as foreign). The merge
the user makes is theirs; `/commit` publishes it on the next run only when it is git's clean
merge (above), so a hand-resolved conflict is pushed by hand. A stop before the push step still
prints the Push line, and a repository whose remotes are not named origin reads `no — no remote
named origin (remotes: …); push it yourself`.

`plan` runs the same publishing code dry: on local refs, no fetch, no write, with the commit it
is about to make counted on top of HEAD. Its `В main:` / `Push:` lines are therefore the same
decisions apply makes on the refs plan saw, worded as a forecast; a stop that comes after the
commit reads `остановлюсь после коммита` / `will stop after the commit` and keeps `verdict:
go`.

The default branch is origin/HEAD's (a local note from clone time), ignored when its ref is
gone; on `main` or `master` tracking origin/<itself> that branch is the default whatever
origin/HEAD says.

## Tasks, task branches and PR repositories

Decided by the owner on 2026-10-30 (the session should know where to commit and the commit
should carry the task link):

- **Task.** The model finds the task (a tracker link in the conversation, a finished ticket
  run's `RUN.md`, a `task/<id>-…` branch this conversation made or worked on — not just the
  branch a shared checkout sits on) and passes `--task`. The model writes the message in
  the history's style; `apply` only checks that the link, or the id as a whole word (a bare
  number only as `#<n>`), is in it — before any write, exit 2 — so a forgotten link cannot slip
  through. `task_of` takes the id as the ticket skill does: first the issue a board opened
  (`?selectedIssue=` Jira, `?issue=` YouTrack, `?p=` Notion/ClickUp peek), then after `/t/`
  (ClickUp, last segment), `/browse/` (Jira), `/issue/` (Linear, YouTrack), `/issues/` (GitHub,
  GitLab), `/task/` (Asana), else a Notion page's trailing 32-hex id or an Asana numeric gid.
  Any other link (a board, a list) has no id: plan and apply exit 2 asking for `--task <id>` —
  never a guess from the last segment.
- **Branch.** On the default branch a task gets `task/<id>-<slug>` (`--branch` from the model,
  else `task/<id>`), and in a PR repository any work gets `session/<slug>` — the commit
  subject transliterated to kebab-case at apply, `-2` … `-9` when taken. `git switch -c` from
  HEAD changes no file and no index entry, so other people's uncommitted work stays where it is
  (on the new branch, still uncommitted). It runs right before the commit, after the stale
  check and the message check; a commit that does not land (refused, stale, taken back) takes
  the branch back — switch to the old branch, delete the new ref only while it still is the old
  HEAD (`update-ref -d <ref> <old>`), else a line names the cleanup. Both switches are judged
  by where HEAD ended (`symbolic-ref`), not by the exit code: a failing post-checkout hook fails
  `git switch` after the switch is made (the Branch line then quotes the hook). An existing
  branch of that name, or any `task/<id>` / `task/<id>-…`, stops the plan: switching onto
  someone's branch would carry this working tree there; «pass another name with --branch» is
  offered only without `--task` (with it, any other name stops the same); a branch checked out
  in another worktree (a ticket's `.claude/worktrees/ticket-<id>`) is named with that worktree
  — commit there — since `git switch` onto it fails. A `session/<slug>` taken on origin counts
  as taken. `--task` on a branch `task/…` that is not this task's stops the plan. In a PR
  repository, and on a `task/…` or `session/…` branch in a direct one, a non-default branch
  holding commits beyond origin/<default> and <default> that are not this session's
  (`own_commits`; a clean merge of origin/<default> or <default> does not count — the lag
  stop's own way out) stops the plan before the commit, whatever `--task` says: a shared
  checkout left on another session's (or a live /ticket run's) branch would otherwise get this
  work committed onto it — and pushed into its PR, or, for a ticket, its `head_sha` moved — and
  a task taken from the branch name proves nothing. In a direct repository other branches
  (a feature branch of the user's) are not checked before the commit; their foreign commits
  stop the merge or push after it, as before. The stop names the ways out: back to the default
  branch for a branch of its own — left out when `--task` names this branch (that ends at the
  "already exists" stop), and replaced by why `git switch` would be refused, with a worktree of
  the default branch instead, when uncommitted tracked changes in files the switch changes are
  in the way (`back_to`: `in_the_way` over `changed_paths(<default>, HEAD)`); `--session` of
  the ended session whose `/commit` made them, together with this one's — never another open
  tab, since `--session` takes all of its uncommitted edits too; `/resume` of the /ticket
  session for a gate commit (its commits are not in `/commit`'s records, so `--session` cannot
  let them through); or committing and pushing by hand. SKILL step 0 takes
  the task from the branch name only when this conversation made or worked on the branch. A
  taken branch that HEAD already holds (landed in main) stops with `git branch -d` instead of
  `git switch`. A stopped plan says `no — stopped, see Commit` on the Branch line and has no
  Task line; the Task line shows only with something to commit, and says the task is named
  (`named in the commit message`), as the check is for the link or the id.
- **PR repository.** `repo_mode` reads the last 20 first-parent subjects of origin/<default>
  (else the local one): when at least 4 are there and half or more are PR merges ("Merge pull
  request #n", squash "… (#n)" whose committer is not its author or a `noreply@` address —
  GitHub's squash and rebase merges; a direct commit naming an issue as "(#n)", which SKILL
  step 3 itself writes for a GitHub Issues task, is its author's and does not count —, GitLab's
  quoted "Merge branch 'x' into 'main'", Bitbucket's
  "Merged in … (pull request #n)"), the repository takes work through PRs. The user's words and
  project rules reach the script as `--pr` / `--main`, read by the model (SKILL step 0); the
  script reads no CLAUDE.md. There main is never moved: the branch is pushed with its upstream
  set (the same foreign-commit and behind checks as `no-merge`), and the PR line names where to
  open the PR — `https://github.com/<owner>/<repo>/compare/<default>...<branch>?expand=1` when
  `remote.origin.url` is GitHub's (the page shows an open PR instead of a new one), else `gh pr
  create --fill --base <default> --head <branch>` as a command for the user. `/commit` runs no
  `gh` and opens, edits or looks up no PR: 2.2.1 before release did (`gh pr list` / `gh pr
  create`), and each review round found another way it put a PR on someone's branch or a
  session's commit into someone's PR, so the owner's fallback removed it — whose PR it is and
  its text are the user's call. A branch with no commits beyond origin/<default> is not pushed
  (`nothing beyond origin/main`); with no new commit, neither is a branch with no commit of this
  session's `/commit` beyond origin/<default> (`the branch holds no commit made by this
  session's /commit`, `Publisher.mine`, also in `no-merge`): someone's pushed branch a shared
  checkout sits on, or a person's clean merge of main on it, which `foreign_commits` lets
  through, is theirs to push. On main with unpushed commits, someone's get the "review and
  push them yourself" stop before the "move them to a branch" one.
  Foreign commits on the branch that origin holds on other branches (a stacked branch) stop the
  push with their own line: push the branch by hand and open the PR against its base. On main of a PR repository with nothing new to commit
  and unpushed commits → stop: they belong on a branch, or `/commit main`.
- **Inside a /ticket run** the script refuses (plan and apply, exit 1, `verdict: stop`): the
  ticket's publish gate commits with its own checks (branch, `reviewed_tree`, whitelist,
  message) and its guard, which does not see git run from a script. The run is the ticket
  guard's marker for the current session (`<runs root>/.guard/<session id>`, `run: <run dir>` —
  the task id is the run dir's name), or, for a resumed session whose skill hooks are not back,
  a `/ticket` invocation in the main transcript (a typed `<command-name>` entry or a Skill call;
  the tags inside a tool result are not one). Only the current session counts: after `/clear`
  the new session is not in the run. An earlier session's work passed with `--session` is
  committed past the ticket gate, and a `Task:` line says so for each such session that ran
  `/ticket` (`/resume <sid>` to go through the gate). The guard in turn reads a typed `/kensei-toolkit:commit`
  as the commit and push commands, and the model's invocation of this skill after a typed
  «закоммить» as adding the push (guard.py docstring, "the commit skill").

## Run directories

`<config>/cache/kensei-commit/<repo>-<hash>/<stamp>/`, mode 0700, newest 20 kept: `plan.json`,
`commands.json` (every shell command naming a file in `bash_named`, uncut, with `named_by`), `blobs/` (computed
content of partial files), `index`, `message.txt`, `result.json`.

## Known gaps

- Files changed through Bash (`sed -i`, `rm`, `git mv`, generators, Unity-made files other than
  `.meta`) have no patch: they are committed only when declared (`--declare`), and then whole —
  a foreign change in a declared file goes into the commit. The files the session's shell
  commands name are listed for the model; the script never takes paths from them. A file a
  command made without naming it (a generator's output, a Unity batchmode import, a glob like
  `src/*.cs`, a folder `rm -r`) is not listed: the model has to remember it.
- `/clear` starts a new session id: pass `--session` for each earlier id to include its work.
  Its unpushed `/commit` commits stop the push until then, as "not made by this session's
  /commit".
- NotebookEdit and binary files are committed whole (no patch to split by). No NotebookEdit or
  MultiEdit call was found in this machine's transcripts; both are handled from their input.
- Edits that reached HEAD other than through `/commit` (a `git commit` in Bash, a hand commit
  of an agent's rewrite) are handled only where the replay is exact (see "Why reverse
  replay"); otherwise the file is skipped with a reason that points at `--declare`.
- An input-only Edit that deleted text or produced repeated text cannot be undone; the fallback
  replays forward from HEAD and is skipped when its result is not on disk as it is.
- In a file the session created, an edit in code that repeats (two lookalike methods) is
  skipped as soon as the file differs from what the session made — also when the other change
  is far away: its line number cannot be trusted then. In a file that existed, undoing such an
  edit at its own line is likewise skipped unless the working tree is HEAD plus only the
  session's edits.
- A foreign change adjacent to (or inside the inner context line of) the session's hunk skips
  the file: there is no safe way to tell the two apart.
- A workflow agent's Write over an existing file is skipped, read first or not: what it
  replaced is unknown. Declare it when the whole file is the session's. The exception — a
  file the session created — is committed whole and marked `unverified`: a change someone
  made between the creation and the agent's Write goes in with it.
- When all of the session's changes in a repository are skipped, plan says so (`Коммит: нет —
  правки сессии пропущены` / `Commit: no — this session's changes are skipped`) and ends with
  `verdict: skipped`, not `nothing`; the skipped files are not counted under `Чужое:` /
  `Foreign:`.
- Publishing is fast-forward only. Whenever origin/main, main or origin/<branch> has commits the
  branch lacks, `/commit` commits and stops, naming the merge; the user runs it (resolving any
  conflict) and `/commit` again. On main that means a merge commit of origin/main where a
  rebase used to keep the history linear.
- From a branch, only main is pushed; the branch's own origin/<branch> is left as it is
  (`/commit no-merge` pushes it).
- With no local main (or master/develop as the default), `/commit` does not create it: it stops
  with `git branch <main> origin/<main>`. It cannot tell a stale origin/HEAD from a real default
  branch without the network, so when the other of main/master exists the stop names `git
  remote set-head origin -a` as well.
- Inside a /ticket run the script refuses rather than commit past the ticket's guard; the
  marker lives as long as the guard's (14 days unused), so every later `/commit` of that
  session hands over to the gate too, also after the run reached its report — and also for
  work in another repository, where the stop's "its gate makes the commit" is not so: that
  gate commits only in the ticket's repository (commit such work by hand or from another
  session).
- The ticket guard grants the commit skill's push after a typed «закоммить» unless the words
  hold the push back in a form it knows (push/пуш, `local`/`locally`, «локально», «только
  закоммить»); other hold-back phrasings («в origin не отправляй», «не публикуй») rely on the
  skill's own rule 4.
- A PR repository is told from history only when 4+ first-parent commits exist; a young one, or
  one whose PRs are merged by rebase (no merge commit, no "(#n)"), reads as direct — `pr` /
  `--pr`, or the project rules the model reads, decide then. The PR line links GitHub's compare
  page only; on GitLab or Bitbucket it names the branches and the `gh` command, which does not
  apply there.
- A session's own commit made with `git commit` in Bash and pushed is not in `/commit`'s
  records: on a rerun with nothing new the branch reads as "holds no commit made by this
  session's /commit" (true of the records, `verdict: nothing`) and gets no PR link — push and
  open the PR by hand.
- New Unity assets are skipped (`no-meta`) only while their `.meta` or their new folder's
  `.meta` is missing under `Assets/` of a project with `ProjectSettings/` beside it; a project
  laid out otherwise (`Assets/` elsewhere) commits the asset without its `.meta`. When the
  asset is skipped but its new folder's `.meta` was already there, that folder `.meta` goes in
  alone.
- A follow-up session on a /ticket task branch stops (the gate's commit is not `/commit`'s);
  after `git switch <default>` the same `--task` stops at "branch already exists". Ways out:
  `/resume` the ticket session, or commit and push by hand.
- Commits of this session that someone rebased by hand get new ids and count as foreign.
- A post-commit hook that is killed with the run still ran its part: the commit is picked up
  next time, whatever the hook did not get to do.
- Repositories in temporary places (a `scratchpad` path component, `/tmp`, `/private/tmp`,
  `/var/folders`, the system temp dir) are not taken when plan picks the repository from a
  session started outside one, not listed in its exit-2 list and not given `Ещё:` lines — only
  counted — unless plan itself runs in a temporary place. Another repository gets an `Ещё:`
  line only while `git status` there still shows some of the session's files (the count is of
  those); one git cannot read keeps the line with every file. The exit-2 list is filtered the
  same way, also keeping a repository with an unpushed commit of this session's `/commit`.
- `plan` uses the local remote-tracking refs (no fetch); `apply` fetches and decides again, so its
  publishing lines can differ from the plan's when origin moved in between.
- Git-ignored files the session wrote are one `Пропуск:` / `Skip:` line with a count and their
  top folders; plan.json and the details keep them one by one.
- `named by this session's shell commands` is a name match: commands that only read a file
  (`grep`, `cat`, `git diff`) name it too, and a file name shared by two changed files counts
  only as a full path. A path built in a variable (`f=src/a.cs; sed -i … "$f"`) is found by its
  text; one put together from parts (`"$dir/$name"`) is not.
- `--declare <folder>` takes the folder's changed tracked files and the untracked files there
  that this session's Edit or Write calls created or edited (whole, like any declared file);
  other untracked files there get one `Skip` line and are listed to be declared one by one
  (minus the `.meta` companions that go in anyway), not counted as Foreign; the folder is "not
  found" only when git shows nothing in it, and `--declare .` is refused. Relative `--declare` paths are taken from the repository root,
  as plan prints paths, whatever the current directory; a declared file that is nowhere (disk,
  index, HEAD) is listed as not found. The detail lists stop at 20 names and say how many
  there are; plan.json has them all.
- Case-only renames need both names declared: the old spelling, still tracked in HEAD, is kept
  as a deletion although a case-insensitive disk opens it as the new file. Where
  `core.ignorecase` is on, a commit that would add a path equal to a kept one but for letter
  case (file or folder) stops. An edited file git tracks under another letter case than the
  disk shows (the file or a folder above it renamed only by case, in Finder or Unity, or by a
  pull of a teammate's rename; `git status` is clean) is skipped (`case-disk`) naming git's
  spelling: match the disk to git, or `git mv`, and run `/commit` again. Its git spelling is
  not counted foreign.
- A task branch this session pushed with `no-merge` that a teammate then pushed to (and the user
  pulled) holds a commit not made by this session's `/commit`, so the next run stops before
  committing with "if the branch is yours, commit and push it yourself". Nothing is lost; 2.2.0
  committed and pushed there.
