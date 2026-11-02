---
name: commit
description: Commit only what this session did — the files this conversation, its subagents and its workflow agents edited, split by hunks where someone else changed the same file — then publish the way the repository takes work, announcing each step in a status line (branch? task? commit? into main? push? PR?). Work for a task carries the task link in its message and gets a task branch; a repository that takes work through pull requests gets the branch pushed and a link to open its PR (no PR is opened or edited by it); any other gets main fast-forwarded to the branch and pushed. Everyone else's changes, staged files and untracked files stay as they are; it never rebases, merges, stashes or forces, and stops instead of publishing someone else's commits or anything that is not a fast-forward. Use when the user asks to commit or land this session's work — "commit", "commit my work", "commit this session", "land it", «закоммить», «коммит», «залей», «влей в мейн», «закоммить и запушь», «через PR». Not for committing everything in the working tree. Inside a /ticket run it hands over to the ticket's publish gate (the ticket's commit and push, with its checks).
argument-hint: "[plan] [local|no-merge|no-push] [pr|main] [message hint]"
allowed-tools: Bash(python3 *sessioncommit.py*), Bash(git log *), Bash(git show *), Bash(git status *)
---

# commit — land this session's work, leave everyone else's alone

Several sessions, a person and tools often change one working tree at once. This skill commits
only what *this* session changed: it reads the session's transcripts (the main one, every
subagent and every workflow agent), takes the patches of each Edit and Write from them, and puts
into the commit HEAD plus those changes — nothing else, even inside a file someone else also
edited. Then it publishes by fast-forward only — main moved to the branch, then pushed — unless
told otherwise. `/commit` is the command: no confirmation is asked; the plan is shown as status
lines and carried out at once.

The script does all git writes. Maintainer notes (how files are split, known gaps):
`${CLAUDE_SKILL_DIR}/README.md`.

## Arguments

`$ARGUMENTS` may hold, in any order:

- `plan` — show what would happen and stop after step 2.
- `local` — commit only: `--no-merge --no-push`.
- `no-merge` — commit and push the current branch, do not touch main.
- `no-push` — commit and move main to the branch locally, push nothing.
- `pr` — this repository takes work through pull requests: `--pr` (push the branch; the PR
  line names the page to open the PR on — /commit opens none).
- `main` — straight into main, even where history says PRs: `--main`.
- anything else — a hint for the commit message.

Map the user's words the same way («только закоммить» = `local`, «без пуша» = `no-push`, «не
вливай в мейн» = `no-merge`, «через PR» / «открой PR» = `pr`, «прямо в мейн» = `main`). Pass the
same flags to `plan` and `apply`.

## Rules

1. **All git writes go through the script.** Never `git add -A` / `-u` / `.`, never `git stash`,
   never `--autostash`, never a force push, never reset or check out other people's files, never
   delete a branch or a worktree, never amend a pushed commit.
2. **A stop is an answer, not a problem to work around.** `verdict: stop` in the plan, or exit
   1 from apply → report the stop with the phrase that unblocks it and end. Do not fix it on
   your own (no rebase, merge, unstage or commit by hand) unless the user then asks — also not
   the `git merge main` / `git merge origin/main` a publishing stop names: that merge is the
   user's call (it may conflict). When the user asks you to run it, run exactly that merge,
   leave a conflict to the user, then `/commit` again. A plan line that says `остановлюсь после
   коммита` / `will stop after the commit` with `verdict: go` is a forecast, not a stop: run
   apply — it commits, then stops at that step.
3. **Status lines are the user-facing record.** Print the lines the script prints verbatim, in
   order, in a code block — they say what was committed, skipped, left alone, merged and pushed.
4. **Inside a /ticket run it hands over to the ticket's gate.** When this conversation ran
   `/kensei-toolkit:ticket` (its `RUN.md`, its publish gate), `/commit` means the gate's own
   «закоммить» + «запушь» — plus «PR» or «влей в мейн» only when the user said so (in a PR
   repository, offer the PR next): follow the ticket's `publish.md` 12.3 (task branch,
   check against `reviewed_tree`, whitelist, its message with the task link), then the push of
   12.4 (and its PR, body approved first). Do not run the script: it refuses there (`идёт прогон
   /ticket <id> — коммит делает шлюз тикета` / `a /ticket run <id> is on — its gate makes the
   commit`), since the ticket's guard does not see git run from a script. The guard counts a
   typed `/kensei-toolkit:commit`, or this skill invoked on a typed «закоммить», as the commit
   and push commands — the commit alone when the user's words hold the push back («не пушь»,
   "don't push", «пуш потом», «только закоммить», «локально»): then do not push.

## Steps

0. **Task and repository.** Outside a /ticket run, find the task this work is for: a tracker link
   or id in this conversation, the `RUN.md` of a finished ticket run of an earlier session of
   this work (`~/.claude/task-runs/<repo>/<task id>/`), or the current branch named
   `task/<id>-<slug>` when this conversation made or worked on that branch (a shared checkout
   that sits on some task branch is not this session's task). Found → `--task <link or id>` on plan and apply (the link when there is
   one; a link with no id in it — a board or a list — exits 2: pass the id). On the default
   branch with a task, also `--branch task/<id>-<slug>` — the slug short
   kebab-case from the task title. How the repository takes work: the user's words («через PR»,
   «прямо в мейн»), then project rules (`CLAUDE.md`, `.claude/task-flow-rules.md`) → `--pr` /
   `--main`; neither → leave it to the script, which reads main's recent history.

1. **Recall Bash changes.** The transcript has patches only for Edit and Write. List the files
   this session changed through Bash or other tools — `sed -i`, `rm`, `git mv`, generators,
   formatters, files Unity or a build created — that belong in the commit. Each becomes a
   `--declare <path>`: absolute, or relative to the repository root as plan prints paths (not
   to the current directory). A folder declares its changed tracked files and the untracked
   files in it that this session's own Edit or Write calls created or edited; other new files
   in it are listed as not taken — declare each of those that is the session's by its path. The
   repository root is refused (that would be `git add -A`). A declared file is committed whole,
   as it is on disk — declare only files no one else is changing. A case-only rename (`git mv
   Foo.cs foo.cs`): declare both names — the old one is committed as a deletion.

2. **Plan.** From the repository the work is in:

   ```
   python3 "${CLAUDE_SKILL_DIR}/sessioncommit.py" plan --lang <ru|en> [--declare <path>]... [--task <link|id>] [--branch <name>] [--pr|--main] [--no-merge] [--no-push]
   ```

   `--lang` is the user's language (`ru` for Russian, otherwise `en`). The session id comes
   from the environment; after `/clear` or when the user names earlier work, add
   `--session <id>` for each earlier session (repeatable, the current one included). Only a
   session that has ended: `--session` takes all of that session's uncommitted edits too, so
   never pass another open tab's id.

   Run it in the directory the session works in. When that is not inside a repository (a
   session started in `~`), plan takes the one repository where something of this session is
   left (a file `git status` still shows, or a `/commit` commit not pushed yet) and says so in
   a first line; when there are several, plan exits 2 listing them — then run steps 2–4 for
   each listed one, with `--repo <path>`, one after another. Repositories in
   temporary places (a scratchpad or system temp clone) are never listed or taken: they are
   throwaway experiments, counted in an `Ещё:` / `Elsewhere:` line as `временные клоны` /
   `temporary clones` — do not run `/commit` there unless the user asks for that clone.

   It prints the status block, then `---` and details for you: `verdict:`, one line per file
   (`+` whole, `~` partial, `!` skipped), `named by this session's shell commands` and `plan:
   <run dir>/plan.json`. That list holds the files changed or untracked in this repository,
   not committed whole, whose path (or file name, when no other changed file — this session's
   own included — has it) appears in the text of any shell command of the session — main,
   subagents and workflow agents. A command that was refused or blocked never ran and is left
   out; one that failed is kept, marked `[failed]` — it may have written before it failed, but
   it does not by itself show that it did. It is a name match, not proof: a `grep` names a file
   as well as a `sed -i` does. Every naming command, uncut, is in the `commands.json` the
   details line names (`named_by`: file → indexes into `commands`, oldest first).

   - The Bash calls of subagents and workflow agents are not in your context: for them this
     list is the only source. A file named there that this session changed (also a partial
     file whose "other" part is the session's own Bash edit) and that is not declared yet → run
     plan again with it declared. Most of a file's commands may be reads while one is the
     write: before you decide a file was only read, search its commands in `commands.json` for
     a write to it (`>`/`>>` to it, `sed -i`, `perl -pi`, `tee`, `cp`/`mv` onto it, `open(…,
     'w')`, `write_text`) — always for a file marked `~` partial or `!` skipped. Once only; do
     not guess paths. A list cut at 20 says so (`20 of N shown`); the
     whole list is in `plan.json` under the key it names — read it there for that one re-plan.
   - A `Пропуск:` / `Skip:` line that asks for `--declare` on a file this session wrote (an
     agent overwrote it whole, an edit cannot be found, placed or split from other changes,
     untracked before, deleted), or on new files of a declared folder (`не взяты новые файлы в
     объявленных папках` / `new files in declared folders not taken`) → when the whole file is
     the session's, add it by its path to that same one re-plan; otherwise leave it.
   - `declared but not found` — a declared path that is nowhere (not on disk, in the index or
     in HEAD): fix the path in that re-plan.
   - Print the status block (rule 3).
   - Exit 1, `verdict: stop` → report the stop and end.
   - `verdict: nothing` → say there is nothing of this session's to commit or publish, and end.
   - `verdict: skipped` → this session's changes here are all skipped: report the skipped files,
     their reasons and what unblocks each (step 5), and end — never say there is nothing of
     this session's.
   - The `plan` argument → end here.
   - Exit 2 that lists several repositories → the per-repository run above. Any other exit 2
     → show the error and end.

3. **Write the commit message** — one commit for the session's work in this repository:
   - the repository's style and language: `git log --oneline -15`;
   - what changed and why, from this conversation; the message hint, if given;
   - with a task, the task in the history's style — e.g. its id in parentheses at the end of the
     subject (`Эффект попадания лазера в щит (869fejukt)`) and its link as the last body line
     before trailers; no style in the history → the link as the last body line. apply refuses a
     message that names neither the link nor the id (exit 2: fix the message, apply again);
   - when the system gives an attribution trailer for commits (`Co-Authored-By: …`), end with
     it.

   Skip this step when the plan commits no files (`Коммит: нет` / `Commit: no`) but has
   something to publish — then `apply` runs without a message.

4. **Apply**, with the same flags and the message on stdin (the script keeps a copy as
   `<run dir>/message.txt`):

   ```
   python3 "${CLAUDE_SKILL_DIR}/sessioncommit.py" apply --plan "<run dir>/plan.json" [--task <link|id>] [--pr|--main] [--no-merge] [--no-push] --message-file - <<'MSG'
   <message>
   MSG
   ```

   Run it with the Bash tool's longest timeout (600000 ms): a push or a hook can be slow, and a
   run cut off half-way leaves its last steps unreported. It prints one status line per step
   as it goes. Exit 0 — all done; 1 — stopped at a step, earlier steps stay done (rule 2); 3 —
   the repository moved since the plan: run step 2 again (once) and apply the new plan, showing
   its status block; 2 — error.

5. **Report**, in the user's language:
   - the plan's status block and apply's lines, verbatim;
   - `git show --stat --oneline HEAD` cut to the subject and the stat summary line (no diff —
     never print a diff in the terminal);
   - skipped and partial files with their reasons, and what unblocks a skipped one (declare it,
     or commit it by hand after looking at it) — also when the run stopped or had nothing else
     to do;
   - other repositories where this session's edits are still uncommitted (`Ещё:` /
     `Elsewhere:`) — `/commit` there;
   - what stays uncommitted (the `Чужое:` / `Foreign:` counts), as counts unless the list is
     short.

## How a file is split

- A file the session created (Write) or rewrote whole: the session's last full content plus
  its later edits. Changes someone made after that stay in the working tree; changes already
  in HEAD are never reverted: when someone committed the file after the session wrote it (a
  hand commit), the commit may take out of HEAD only the session's own later edits — anything
  else (someone's committed line removed on disk again) skips the file as tangled
  (`сцеплены с другими` / `entangled`), to declare when the whole file is the session's. A workflow agent's Write over an existing file (its transcript
  has no patch, so what the file held before is unknown) is skipped — `агент перезаписал файл
  целиком` / `an agent overwrote the whole file` — unless the session created that file
  earlier (also when that creation is committed already): then it is committed marked `без
  проверки` / `unverified`, since someone may have changed the file in between. Declare a
  skipped one when the whole file is the session's.
- Edits `/commit` already committed are in HEAD (it records them per session, and a record
  covers only the sessions of the run that made it: work of an earlier session added later
  with `--session` is committed then); only the edits made since are committed, so a re-edit
  or a revert of committed lines works. Edits that reached HEAD another way (a `git commit` in
  Bash, a hand commit) are not guessed at: the same change merges cleanly by itself, and a
  file that is HEAD plus only the session's newer edits is committed whole; any other such
  file is skipped with a reason that ends in `передай --declare, если весь файл твой` /
  `--declare it if the whole file is yours`. So commit through `/commit`, not `git commit`. A
  file that is the same as in HEAD has nothing to commit, whatever the edits were.
- A file that existed: the session's edits are undone from the working tree, last first,
  giving the file without them; `git merge-file` then puts the session's changes onto HEAD's
  version. Someone else's edits stay in the working tree, unstaged.
- Skipped, with the reason in its status line: the session's lines were rewritten later, or its
  edit and someone else's (or a commit made outside `/commit`) touch the same or adjacent lines
  (`сцеплены` / `entangled`), or it matches lookalike code in more than one place (also at its
  own line, once someone else's lines changed the file: they may have moved a lookalike block
  there); an edit that can only be replayed onto HEAD (a workflow agent's deletion) and whose
  result the disk does not show; the file is git-ignored; it was deleted after the session's
  edits (a notebook or a binary file too), or was untracked before them, without `--declare`;
  it is marked assume-unchanged or skip-worktree in git; it is CRLF in git and
  `.gitattributes` / `core.autocrlf` (`true` or `input`) normalise it, so a commit from here
  would rewrite every line ending (commit it by hand: `git add` keeps them); git tracks it under
  another letter case than the disk has (`git хранит его как …` / `git tracks it as …`: a
  case-only rename of the file or its folder that git does not see — match the disk to git or
  `git mv`, then `/commit` again); a new Unity asset (under `Assets/` of a project with
  `ProjectSettings/`) whose `.meta`, or whose new folder's `.meta`, Unity has not written yet
  (`новый ассет без .meta` / `a new asset with no .meta yet`: focus the editor or refresh
  assets, then `/commit` again).
- A path written in another letter case (macOS) is committed under its name on disk, in the
  Unicode form git uses there. Someone else's line-ending change stays out of the commit: when
  HEAD ends every line the same way, the session's lines end that way too. A mode change on
  disk stays out unless the file is declared — a `chmod` of the session's names the file in
  `named by this session's shell commands`, and the `exec bit changed` detail line lists it:
  declare that file (a declared file whose only change is the exec bit is committed with it).
  Where git ignores letter case, a commit that would hold two paths differing only in case (the
  new name of a rename declared without the old one) stops.
- Declared files, notebooks and binary files are committed whole, marked `без проверки` /
  `unverified`. A created or deleted Unity asset brings its `.meta` along, and a new folder its
  folder `.meta`.
- Someone staged changes on a path this session changed → stop before anything is committed.
  What is staged there exactly as the commit will hold it (the session's own `git add`,
  `git mv`, `git rm`) is not a stop. Staged changes on other paths stay staged and stay out of
  the commit. Index entries an earlier `/commit` could not update (a locked index, a run
  killed right after `git commit`) are put back by the next run (`Индекс:` / `Index:` line) —
  only the ones that run recorded; a stage someone made since is theirs and stays.

## Branch, task and PR

- **Branch** (`Ветка:` / `Branch:`): on the default branch, work for a task gets
  `task/<id>-<slug>` (`--branch`, else `task/<id>`), and any work in a PR repository gets
  `session/<slug of the commit subject>` (or `--branch`). apply makes it from HEAD with `git
  switch -c` right before the commit — the working tree and index stay as they are, everyone
  else's changes stay uncommitted on it — and says `создана от main` / `created from main`. On
  any other branch the commit goes there, unless one of the stops below applies. A branch of that name, or another `task/<id>-…` of the
  same task, already there → stop naming it; nothing switches onto it. `--task` on another
  task's `task/<id>-…` branch → stop (switch to the default branch first); a taken branch
  checked out in another worktree → stop naming it (commit there); a taken branch main already
  holds → stop with `git branch -d` to delete it. In a PR repository, or on a `task/…` or
  `session/…` branch in any repository, a non-default branch with commits not made by this
  session's `/commit` → stop before the commit, whatever `--task` says: it may be another
  session's (or a /ticket run's) branch and PR in a shared checkout. The ways out it names: go
  back to the default branch for a branch of its own (not offered when `--task` names that
  branch; when uncommitted changes in files the switch changes would make git refuse `git
  switch`, the stop says so and names a worktree of the default branch instead); `--session`
  of the ended session whose `/commit` made them, together with this one's; `/resume` the
  /ticket session for a gate commit; or commit and push by hand. A `session/<slug>`
  that origin already has gets `-2`, `-3`, …. A commit that does not land takes the new branch
  back (`убрана — коммита нет` / `removed — no commit`).
- **Task** (`Задача:` / `Task:`): with `--task`, apply checks the message names it before
  anything is written; a stopped plan has no Task line.
- **PR repository** — main's recent first-parent history came mostly through pull requests
  ("Merge pull request #…", squash "… (#12)" that GitHub committed — a direct commit naming an
  issue as "(#12)" does not count —, GitLab's "Merge branch 'x' into 'main'"), or
  `pr`: main is never moved (`В main: нет — в этом репо через PR`). The branch is pushed with its
  upstream set; `/commit` never opens, edits or looks up a PR (no `gh`): the PR line names the
  page to open it on — GitHub's compare page for a GitHub origin (`PR: не открываю сам — открой
  (или посмотри открытый): https://github.com/…/compare/main...<branch>?expand=1`), else `gh pr
  create --fill …` as a command for the user. Show that line: the PR is the user's to open
  (also after «открой PR»); this skill opens none. A branch with nothing beyond
  origin/<default> is neither pushed nor given a PR link, and neither is a branch with no commit of this session's `/commit` on it when nothing
  new is committed (`на ветке нет коммитов /commit этой сессии` / `the branch holds no commit
  made by this session's /commit`: someone's branch, or a person's merge on it — theirs to
  push). On main of a PR repository with unpushed commits and nothing new to commit → stop
  (someone's: review and push them yourself; this session's: move them to a branch, or
  `/commit main`).
- **Direct repository** — everything else, or `main`: the fast-forward flow below; with a task
  branch, main is fast-forwarded to it and pushed.

## Into main and push — fast-forward only

After the commit the script only fast-forwards: push the branch, move main to the branch, push
main. It never rebases, never merges anything into a branch, never makes a merge commit, never
pulls, stashes or forces. Whatever is not a fast-forward is a stop after the commit, naming the
merge that unblocks it — the user runs that merge (it may conflict) and `/commit` again.

- **Detached HEAD**, an unfinished merge or rebase, a message prepared by `git merge --squash`
  or `git cherry-pick -n` (`SQUASH_MSG` / `MERGE_MSG`), or unresolved conflicts → stop before
  the commit; the stop names the way out (`git rebase --continue` / `--abort`; for a leftover
  prepared message: commit it, or delete that file if it was abandoned).
- **On main:** fetch, then push. origin/main ahead → `origin/main ушёл вперёд на N — влей его в
  main (git merge origin/main) и снова /commit` / `origin/main is N ahead — merge it into main
  (git merge origin/main) and run /commit again`.
- **On another branch or in a worktree:** fetch; main and origin/main must already be in the
  branch, else stop: `main ушёл вперёд на N — влей main в ветку (git merge main) и снова
  /commit`, `origin/main ушёл вперёд на N — влей его в ветку (git merge origin/main) …`, or both
  merges when they diverged. Then main is fast-forwarded to the branch (`git merge --ff-only` in
  main's checkout, or a ref update when main is checked out nowhere) and pushed. The branch
  itself is not pushed then (`/commit no-merge` does that); the branch and the worktree stay.
- **No local main** → stop: create it (`git branch main origin/main`), or, when the other of
  main/master exists, maybe origin/HEAD is stale after a rename (`git remote set-head origin
  -a`); or `/commit no-merge`. `/commit` does not create main.
- **Main checked out elsewhere** with uncommitted changes in files the move changes, an
  unfinished merge or rebase, or conflicts → stop naming them (plan foresees it); nothing there
  is aborted or stashed. Way out: sort it out there, or `/commit no-merge`.
- **`no-merge`, or a branch with no history in common with main** (orphan): the branch is pushed
  to origin/<branch>; origin/<branch> ahead → stop naming `git merge origin/<branch>`. With
  nothing new committed, a branch holding no commit of this session's `/commit` is not pushed.
- **A push origin rejects** names the way out: a race — run `/commit` again (it then names the
  merge); main protected — `/commit no-merge` pushes the branch for a pull request (local main
  stays ahead by this session's commits). No remote named origin → `Push: no — no remote named
  origin (remotes: …); push it yourself`. A stop before the push still prints the Push line.
- **Never published:** commits this session's `/commit` did not make. Its own are only the
  ones it recorded (per session, at once — a run killed right after `git commit` is picked up
  by the next plan in that repository). Everything else unpushed on the way — another
  session's `/commit`, or a commit this session made itself with `git commit` in Bash — stops
  the run, naming those commits, with the way out: review and push them yourself, or drop
  them; `/commit no-merge` when the branch alone would pass. A merge commit that only brings in
  main, origin/main or origin/<branch> and holds nothing of its own (exactly git's clean merge
  of its parents — the merge a stop above names, when it had no conflict) does not stop it; a
  merge with a hand-resolved conflict or other changes in it is someone's work and stops it:
  push that one yourself. The foreign commits on the branch are checked before main is moved.
- **HEAD moved to another branch while `git commit` ran** (a post-commit hook): the commit stays
  on the planned branch and is recorded; nothing is published — switch back and run `/commit`
  again. **Someone committed on top of it while `git commit` ran**: nothing is taken back and
  the run stops; the next plan finds the session's commit below theirs, records it and puts
  the index back (`Индекс:` / `Index:`), and their commit stops the push as foreign.
