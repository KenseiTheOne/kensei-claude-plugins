# commit — maintainer notes

Not loaded by the skill; `SKILL.md` is what the model reads.

## Files

- `sessioncommit.py` — `plan` (read-only: attribution, plan.json, status block) and `apply`
  (stale check, commit through a private index, index sync, merge into main, push).
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
say what it replaced, so the file before it is rebuilt from the agent's last whole `Read` of the
path in the same transcript (`N<TAB>line` rows; a Read with offset/limit, 2,000 lines or a
2,000-character line may be cut and is not used) plus the agent's own edits since, and the
rewrite becomes an ordinary patch. With no such Read the file is skipped (`rewrite-unknown`):
treating it as the session's would commit whatever someone else had in the file. The same
when, after that Read, Claude Code told the agent the file changed (an `attachment` entry of
type `edited_text_file`): it then lets the agent write without reading again, so the Read no
longer shows what the Write replaced. A Read answered "unchanged since last read" (no file
rows) stands for the Read before it; a whole Read is used whatever its text says.

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
on disk. Then `git merge-file -p W B H` (current W, base B, other HEAD) applies
B→W (the session's changes) and B→H (removing the foreign changes) together, giving H plus the
session's changes. A conflict, or a hunk whose changed lines are gone, means the edits are
entangled: the file is skipped rather than guessed.

Only the edits not committed yet are undone. Each commit this skill makes records, per path, the
last session edit it holds (`sessions/<id>.json`, below); the session's own Bash `git commit`
counts up to the timestamp of its output, for the paths that commit touched — less the last
edits whose result that commit's blob does not hold (a parallel agent's edit made while
`git commit && npm test` still ran stays uncommitted work). Edits up to there
are in HEAD, so B is "W without the newer edits" and merge-file gives exactly the new work —
including a re-edit or a revert of lines committed earlier, which undoing every edit back to the
start of the session would turn into a conflict. A recorded commit that is no longer in HEAD's
history (reset by hand) does not count, and every edit is replayed again; one this skill
rebased is followed to its new id.

Line endings: matching ignores them, but a file committed whole must equal the working tree
byte for byte with HEAD's endings kept on the lines the session did not change — otherwise it
becomes a computed blob with HEAD's endings there. New lines take HEAD's ending when HEAD ends
every line the same way, and the working tree's only for a new file or a HEAD with mixed
endings, so an editor's LF→CRLF conversion stays uncommitted entirely. A file identical to
HEAD is `unchanged` before any replay: there is nothing on disk to commit. A tracked file keeps
HEAD's mode unless it is declared: Edit and Write keep a file's mode, so an exec bit on disk is
someone's chmod — or the session's, which is why `chmod` is a shell hint and a whole file whose
exec bit stays different is counted in `Чужое:` and listed (`exec bit changed`) for the model
to declare; a declared file whose content equals HEAD but whose exec bit differs is committed
with the disk's mode.

Forward replay from HEAD would be simpler but wrong whenever the file had uncommitted changes
before the session started (the session's patches do not apply to HEAD), and whenever the
session's work was partly committed already. It is used only as a fallback when an
input-only Edit cannot be undone (it deleted text, or its result occurs more than once).

A file the session created (Write create, or an agent's Write over its own new file) is replayed
forward from that content instead (S): everything in it is the session's. When the working tree
differs from S, the commit is S plus only the changes the working tree and HEAD both made to S
(`common_changes`): an edit in the working tree alone came after the session and stays out; an
edit both share is already committed — typically the session's own Bash edit, declared in an
earlier `/commit` — and is kept, so the commit never reverts it; a working-tree edit touching a
different HEAD change skips the file.

When the current directory is not in a repository (a session started in `~`), `plan` takes the
single repository the session's edits are in, or lists them when there are several.

All matching runs on text normalised to LF, because patches and tool inputs are LF even for CRLF
files; terminators are restored from the working-tree and HEAD versions (`difflib` alignment), so
a CRLF file stays CRLF. HEAD is read with `git cat-file --filters`, so eol and smudge filters do not
show up as differences; content is hashed with `--path`, so clean filters apply as with `git add`.
Paths no filter attribute (`check-attr`) or `core.autocrlf=true` applies to are read through one
`cat-file --batch` process instead (`--filters` cannot be batched: its header gives the
unfiltered size). Shell commands are matched against file names as whole words collected once,
not one regex per left-out file: a session with thousands of commands planned in 24 s, now 10.

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

Before `git commit` apply also records a `pending` commit: the planned parent, tree and branch,
the path records and the index entries to set. Once the commit is checked it becomes a recorded
own commit. A run killed in between — Esc or the Bash timeout reach the whole process group,
git and its post-commit hook included — leaves the pending record, and the next plan finds the
commit by tree and parent on that branch, records it as the session's and puts the index back;
a pending commit that never landed (refused, taken back) is dropped.

A path staged before the run blocks it only when the staged entry is not what the commit will
hold: the session's own `git add`, `git mv` (both paths declared) or `git rm` stage exactly the
planned blob or deletion and go through; a staged blob that differs (someone's work, or the
whole file of a partial one) stops the plan.

Right before `git commit`, apply checks again that HEAD has not moved, the branch is the plan's
and no merge, cherry-pick or rebase began (exit 3). After it, the new commit must have exactly
the old HEAD as parent and be on the plan's branch: when someone committed, began a merge or
switched branches in that gap, the commit is taken back off the branch it landed on
(`update-ref` with the old value checked; a swallowed `MERGE_HEAD` is written back) and the run
exits 3. Where it landed is decided by refs: the plan's branch one commit on from the old HEAD
is this commit whatever HEAD points to by then — a branch switch after the commit landed (in a
post-commit hook) keeps and records it, sets the index only while HEAD is at that commit, and
stops before merging or pushing. A `SQUASH_MSG` or `MERGE_MSG` (`git merge --squash`, `git
cherry-pick -n`) counts as an unfinished operation: `git commit` would delete that prepared
message; the stop says to commit it or delete the file (after `git restore` it outlives the
change it prepared, and `git merge --abort` cannot remove it). An unfinished rebase is checked
before the detached HEAD it causes, so its stop names `git rebase --continue` / `--abort`.
A commit whose tree differs from the planned one (a hook re-added files) is reported with the
differing paths and the commands that undo it, and is not recorded as the session's, so no
later run publishes it.

## Own commits

`<config>/cache/kensei-commit/sessions/<session>.json` records, under every session id of the
run, every commit, rebased commit and merge commit this skill made (`commits`), per path the last
session edit committed (`paths`: event id, timestamp, commit) and the ids a rebase gave its
commits (`rewritten`) — written as each is made, not at the end, so a run killed during a slow
push (Esc, the Bash timeout; SIGTERM also runs the final write) does not leave its rebased
commits looking foreign. Together with the session's own Bash commits, these are the commits it
may publish; any other unpushed commit on the way stops the push. A Bash commit is the
session's when its output has a `[branch abc1234]` line — also from a command that failed
later, like `git commit && git push` with the push rejected — or, for `git commit -q` (most of
them), when the HEAD reflog of the checkout the command ran it in (its cwd followed through
`cd` and `git -C`) got a new commit (`commit:`, `cherry-pick:`, `revert:`, a merge that made a
merge commit — not a fast-forward or reset) between the command's call and its result, and
that commit holds the session's last edit before then of a file it changes, or the subject the
command gave (`-m '…'`, a here-document message). Only a parsed `git commit|merge|pull|
cherry-pick|revert` counts (not `git merge-base`, `git cat-file commit`), and only a call that
ran: one the user rejected, or a hook or permission blocked, has no `Exit code` result and
makes nothing its own. Someone else committing in that checkout meanwhile — or in another
checkout, or in the second after the result — therefore stays foreign, and the push stops
naming it. A Bash commit the skill later rebased is followed through `rewritten`. A merge
commit whose other parents are already in main or origin/main (the session's
own `git merge` / `git pull`, which prints no `[branch sha]` line) brings nothing new and does
not stop it.

Integration never touches someone else's work in main's checkout: an unfinished merge, rebase or
conflict there stops the run (their merge is never aborted), every merge and rebase runs with
`merge.autoStash` / `rebase.autoStash` off, and pushes use `--no-follow-tags
--recurse-submodules=no`. Merges use `--no-overwrite-ignore`, and before a rebase the files
`onto` adds — and every folder of them — are checked against the ignored files on disk,
spelt as the disk spells them, so `Secrets.env` counts for `secrets.env` and an ignored file
`notes` for an added `notes/README.md` (`git rebase` has no such flag): git's
own refusal to overwrite changes does not cover ignored files, so origin starting to track a
path someone keeps ignored (`.env`, editor settings) would otherwise replace it silently. A
branch push that origin refuses because origin/<branch> has commits the branch lacks names
them; "diverged after a rebase" is said only when those are the branch's own rebased commits.
Foreign commits on the branch or on local main stop the merge with the way out: `/commit
no-merge` when they are already on origin/<branch> (or only local main holds them), else merge
or drop them by hand. A `--no-ff` merge into main gets `-m "Merge branch '<branch>'"`, as `git
merge <branch>` would write it (the full ref it merges would read `refs/heads/<branch>`). A
branch counts as published (merge, never rebase) when any of its
commits is on a remote, with or without an upstream; it is pushed only to origin/<branch>.

## Run directories

`<config>/cache/kensei-commit/<repo>-<hash>/<stamp>/`, mode 0700, newest 20 kept: `plan.json`,
`blobs/` (computed content of partial files), `index`, `message.txt`, `result.json`.

## Known gaps

- Files changed through Bash (`sed -i`, `rm`, `git mv`, generators, Unity-made files other than
  `.meta`) have no patch: they are committed only when declared (`--declare`), and then whole —
  a foreign change in a declared file goes into the commit. `bash_hints` lists the session's
  commands that may have changed files; the script never takes paths from them.
- `/clear` starts a new session id: pass `--session` for each earlier id to include its work.
- NotebookEdit and binary files are committed whole (no patch to split by). No NotebookEdit or
  MultiEdit call was found in this machine's transcripts; both are handled from their input.
- An input-only Edit that deleted text or produced repeated text cannot be undone; the fallback
  replays forward from HEAD, which fails (skip) if the file had other uncommitted changes in that
  region before the session.
- A foreign change adjacent to (or inside the inner context line of) the session's hunk skips
  the file: there is no safe way to tell the two apart.
- A workflow agent's Write over an existing file it did not read whole first is skipped: what
  it replaced is unknown. Declare it when the whole file is the session's.
- A session `git commit` through Bash that took only some hunks (`git add -p`) still counts all
  of the file's earlier edits as committed; the rest stays in the working tree as foreign.
- The ticket skill's guard does not see git run by `sessioncommit.py` (a script, not a Bash git
  command): inside a /ticket run the ticket's own commit command is the one to use — the skill
  says so in its description and rules.
- Commits of this session that someone rebased by hand get new ids and count as foreign.
- The reflog keeps whole seconds: a quiet commit made in the second its command started counts
  only when the command holds the commit's subject (`-m '<subject>'`); one from `-F file` in
  that second is not recognised (the push then stops and names it). A quiet commit found only
  in the reflog must hold one of the session's Edit/Write edits or the subject the command
  gave: a `sed -i … && git commit -q -F msg` of Bash-only changes, a quiet `git cherry-pick` or
  `git revert`, and a `git merge -q` merge commit count as foreign (the push stops and names
  them). Someone's `git commit -a` in the same checkout during the session's command that
  sweeps up the session's edits passes that check.
- A post-commit hook that is killed with the run still ran its part: the commit is picked up
  next time, whatever the hook did not get to do.
- Repositories in temporary places (a `scratchpad` path component, `/tmp`, `/private/tmp`,
  `/var/folders`, the system temp dir) are not taken when plan picks the repository from a
  session started outside one, not listed in its exit-2 list and not given `Ещё:` lines — only
  counted — unless plan itself runs in a temporary place.
- `plan` uses the local remote-tracking refs (no fetch); `apply` fetches and decides again, so its
  merge and push lines can differ from the plan's. A stop `plan` foresees after the commit reads
  "will stop after the commit" and keeps `verdict: go`.
- Shell commands are kept as hints and their commit ids read even when they exited non-zero:
  the part before the failure may have changed files. Unity batchmode runs (`Unity -batchmode
  -projectPath …`) are hints too; the files they generate still have to be declared.
- Git-ignored files the session wrote are one `Пропуск:` / `Skip:` line with a count and their
  top folders; plan.json and the details keep them one by one.
- `bash_hints` keeps only shell commands that may have touched this repository: a part of the
  command that may change files (the hint pattern) runs in a directory inside it — the tool
  call's recorded cwd, followed through the command's own `cd`/`pushd` and `NAME=value` — and
  names no file or a relative one, or it names an absolute path inside the repository (`/…`,
  `~/…`, `$HOME/…`; the source of a `cp`/`rsync` does not count; the body of a here-document
  that runs, like `python3 - <<EOF`, is searched, the body `cat > f <<EOF` writes is not).
  A relative path counts from the directory the part runs in, inside the repository or not
  (`sed -i … Projects/app/x.yml` from `~`); `git -C <dir>` runs in `<dir>`; a `cd` inside
  `( … )` or `$( … )` ends with it; a quoted absolute path with spaces is one path. A `>` inside
  quotes (`grep "List<Ship>"`) is no redirect, and `git merge-base` is not `git merge`.
  A `cd` into an unknown place (`cd "$TMP"`) and a path in an unknown variable count as
  elsewhere — such a command is missed. The newest 40 are kept in full in `plan.json`, the
  newest 12 printed; commands run elsewhere are only counted. The files they name that the
  commit leaves out are listed as `bash_named` — hints for the model, never taken as
  declarations.
- `--declare <folder>` takes the folder's changed tracked files and the untracked files there
  that this session's Edit or Write calls created or edited (whole, like any declared file);
  other untracked files there are listed to be declared one by one (minus the `.meta`
  companions that go in anyway), the folder is "not found" only when git shows nothing in it,
  and `--declare .` is refused. Relative `--declare` paths are taken from the repository root,
  as plan prints paths, whatever the current directory; a declared file that is nowhere (disk,
  index, HEAD) is listed as not found. The detail lists stop at 20 names and say how many
  there are; plan.json has them all.
- Case-only renames need both names declared: the old spelling, still tracked in HEAD, is kept
  as a deletion although a case-insensitive disk opens it as the new file. Where
  `core.ignorecase` is on, a commit that would add a path equal to a kept one but for letter
  case (file or folder) stops.
