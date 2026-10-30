---
name: commit
description: Commit only what this session did — the files this conversation, its subagents and its workflow agents edited, split by hunks where someone else changed the same file — then, by default, merge the branch into main and push, announcing each step in a status line (commit? merge into main? push?). Everyone else's changes, staged files and untracked files stay as they are; it stops instead of stashing, forcing or publishing someone else's commits. Use when the user asks to commit or land this session's work — "commit", "commit my work", "commit this session", "land it", «закоммить», «коммит», «залей», «влей в мейн», «закоммить и запушь». Not for committing everything in the working tree, and not inside a /ticket run — there «закоммить» is the ticket's own commit command (commit only, no push).
argument-hint: "[plan] [local|no-merge|no-push] [message hint]"
allowed-tools: Bash(python3 *sessioncommit.py*), Bash(git log *), Bash(git show *), Bash(git status *)
---

# commit — land this session's work, leave everyone else's alone

Several sessions, a person and tools often change one working tree at once. This skill commits
only what *this* session changed: it reads the session's transcripts (the main one, every
subagent and every workflow agent), takes the patches of each Edit and Write from them, and puts
into the commit HEAD plus those changes — nothing else, even inside a file someone else also
edited. Then it merges into main and pushes, unless told otherwise. `/commit` is the command: no
confirmation is asked; the plan is shown as status lines and carried out at once.

The script does all git writes. Maintainer notes (how files are split, known gaps):
`${CLAUDE_SKILL_DIR}/README.md`.

## Arguments

`$ARGUMENTS` may hold, in any order:

- `plan` — show what would happen and stop after step 2.
- `local` — commit only: `--no-merge --no-push`.
- `no-merge` — commit and push the current branch, do not touch main.
- `no-push` — commit and merge into main locally, push nothing.
- anything else — a hint for the commit message.

Map the user's words the same way («только закоммить» = `local`, «без пуша» = `no-push`, «не
вливай в мейн» = `no-merge`). Pass the same flags to `plan` and `apply`.

## Rules

1. **All git writes go through the script.** Never `git add -A` / `-u` / `.`, never `git stash`,
   never `--autostash`, never a force push, never reset or check out other people's files, never
   delete a branch or a worktree, never amend a pushed commit.
2. **A stop is an answer, not a problem to work around.** `verdict: stop` in the plan, or exit
   1 from apply → report the stop with the phrase that unblocks it and end. Do not fix it on
   your own (no rebase, merge, unstage or commit by hand) unless the user then asks. A plan line
   that says `остановлюсь после коммита` / `will stop after the commit` with `verdict: go` is a
   forecast, not a stop: run apply — it commits, then stops at that step.
3. **Status lines are the user-facing record.** Print the lines the script prints verbatim, in
   order, in a code block — they say what was committed, skipped, left alone, merged and pushed.
4. **Not inside a /ticket run.** When a ticket run is active in this conversation (its `RUN.md`
   or publish gate), its commit and push commands follow the ticket's command table — commit
   only, push on its own command. Do not use this skill for them: it pushes to main by default,
   and the ticket's guard does not see git run from this script.

## Steps

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
   python3 "${CLAUDE_SKILL_DIR}/sessioncommit.py" plan --lang <ru|en> [--declare <path>]... [--no-merge] [--no-push]
   ```

   `--lang` is the user's language (`ru` for Russian, otherwise `en`). The session id comes
   from the environment; after `/clear` or when the user names earlier work, add
   `--session <id>` for each earlier session (repeatable, the current one included).

   Run it in the directory the session works in. When that is not inside a repository (a
   session started in `~`), plan takes the one repository this session edited files in and
   says so in a first line; when it edited several, plan exits 2 listing them — then run
   steps 2–4 for each listed one, with `--repo <path>`, one after another. Repositories in
   temporary places (a scratchpad or system temp clone) are never listed or taken: they are
   throwaway experiments, counted in an `Ещё:` / `Elsewhere:` line as `временные клоны` /
   `temporary clones` — do not run `/commit` there unless the user asks for that clone.

   It prints the status block, then `---` and details for you: `verdict:`, one line per file
   (`+` whole, `~` partial, `!` skipped), `named by this session's shell commands` — files left
   out of the commit that a shell command of the session names — `bash_hints` (the newest
   shell commands that may have changed files here: run inside this repository or naming a
   path in it; commands run elsewhere are only counted; the newest 40 in full in `plan.json`),
   and `plan: <run dir>/plan.json`.

   - The Bash calls of subagents and workflow agents are not in your context: for them these
     two lists are the only source. A file named there that this session changed (also a
     partial file whose "other" part is the session's own Bash edit) and that is not declared
     yet → run plan again with it declared. Once only; do not guess paths. A list cut at 20
     says so (`20 of N shown`); the whole list is in `plan.json` under the key it names — read
     it there for that one re-plan.
   - `declared but not found` — a declared path that is nowhere (not on disk, in the index or
     in HEAD): fix the path in that re-plan.
   - Print the status block (rule 3).
   - Exit 1, `verdict: stop` → report the stop and end.
   - `verdict: nothing` → say there is nothing of this session's to commit or publish, and end.
   - The `plan` argument → end here.
   - Exit 2 that lists several repositories → the per-repository run above. Any other exit 2
     → show the error and end.

3. **Write the commit message** — one commit for the session's work in this repository:
   - the repository's style and language: `git log --oneline -15`;
   - what changed and why, from this conversation; the message hint, if given;
   - when the system gives an attribution trailer for commits (`Co-Authored-By: …`), end with
     it.

   Skip this step when the plan commits no files (`Коммит: нет` / `Commit: no`) but has
   something to publish — then `apply` runs without a message.

4. **Apply**, with the same flags and the message on stdin (the script keeps a copy as
   `<run dir>/message.txt`):

   ```
   python3 "${CLAUDE_SKILL_DIR}/sessioncommit.py" apply --plan "<run dir>/plan.json" [--no-merge] [--no-push] --message-file - <<'MSG'
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
     or commit it by hand after looking at it);
   - other repositories this session edited (`Ещё:` / `Elsewhere:`) — `/commit` there;
   - what stays uncommitted (the `Чужое:` / `Foreign:` counts), as counts unless the list is
     short.

## How a file is split

- A file the session created (Write) or rewrote whole: the session's last full content plus
  its later edits. Changes someone made after that stay in the working tree; changes already
  in HEAD are never reverted. A workflow agent's Write over an existing file is split by what
  the agent last read of it; with no such Read — or when Claude Code told the agent the file
  changed after that Read — the file is skipped.
- Edits this session already committed (by `/commit` or its own `git commit`, quiet ones
  included) are in HEAD; only the edits made since are committed, so a re-edit or a revert of
  committed lines works. A file that is the same as in HEAD has nothing to commit, whatever
  the edits were.
- A file that existed: the session's edits are undone from the working tree, last first,
  giving the file without them; `git merge-file` then puts the session's changes onto HEAD's
  version. Someone else's edits stay in the working tree, unstaged.
- Skipped, with the reason in its status line: the session's lines were rewritten later, or its
  edit and someone else's touch the same or adjacent lines (`сцеплены` / `entangled`), or it
  matches lookalike code in more than one place; the file is git-ignored; it was deleted after
  the session's edits, or was untracked before them, without `--declare`.
- A path written in another letter case (macOS) is committed under its name on disk, in the
  Unicode form git uses there. Someone else's line-ending change stays out of the commit: when
  HEAD ends every line the same way, the session's lines end that way too. A mode change on
  disk stays out unless the file is declared — a `chmod` of the session's shows up in
  `bash_hints` and the `exec bit changed` detail line: declare that file (a declared file whose
  only change is the exec bit is committed with it). Where git ignores letter case, a commit
  that would hold two paths differing only in case (the new name of a rename declared without
  the old one) stops.
- Declared files, notebooks and binary files are committed whole, marked `без проверки` /
  `unverified`. A created or deleted Unity asset brings its `.meta` along, and a new folder its
  folder `.meta`.
- Someone staged changes on a path this session changed → stop before anything is committed.
  What is staged there exactly as the commit will hold it (the session's own `git add`,
  `git mv`, `git rm`) is not a stop. Staged changes on other paths stay staged and stay out of
  the commit. Index entries an earlier `/commit` could not update (a locked index, a run
  killed right after `git commit`) are put back by the next run (`Индекс:` / `Index:` line) —
  only the ones that run recorded; a stage someone made since is theirs and stays.

## Merge and push

- **Detached HEAD**, an unfinished merge or rebase, a message prepared by `git merge --squash`
  or `git cherry-pick -n` (`SQUASH_MSG` / `MERGE_MSG`), or unresolved conflicts → stop before
  the commit; the stop names the way out (`git rebase --continue` / `--abort`; for a leftover
  prepared message: commit it, or delete that file if it was abandoned).
- **On main:** fetch, then push. When origin moved ahead: rebase this session's unpushed commits
  onto it if no tracked file has uncommitted changes, otherwise stop after the commit.
- **On another branch or in a worktree:** fetch, bring local main up to origin (fast-forward;
  when local main is ahead only by this session's commits, origin/main is merged into the
  branch instead), take main's new commits into the branch (rebase when no commit of it was
  ever pushed, merge when one was; never with uncommitted tracked changes), move main to the
  branch — fast-forward, or a `--no-ff` merge (`Merge branch '<branch>'`) in main's checkout
  when the repository's history is mostly merges; created at the branch when there is no local
  main — and push main, then the branch if its upstream is origin/<branch> (also when main is
  already there and only the branch lags). The branch and the worktree stay. An
  unfinished merge or rebase in main's checkout stops the run; it is never aborted.
- **A new branch with no history** (orphan) is pushed to origin/<branch>, not merged.
- **Never published:** commits that are not this session's. The session's own are the ones this
  skill made (remembered per session, at once — a run killed right after `git commit` is picked
  up by the next one) and the ones its own Bash `git commit` made — named in its output, or for
  `git commit -q` found in the HEAD reflog of the checkout it ran in while it ran, when that
  commit holds the session's edits or the subject the command gave. A command the user
  rejected, or a read-only one (`git merge-base`, `git log`), makes nothing its own. Anything
  else unpushed on the way → stop and name those commits, with the way out (`/commit no-merge`
  when they are already on origin/<branch>).
- **HEAD moved to another branch while `git commit` ran** (a post-commit hook): the commit stays
  on the planned branch and is recorded; nothing is merged or pushed — switch back and run
  `/commit` again.
- A conflict in a rebase or merge is aborted at once, leaving the state after the commit. A
  rebase or merge that would overwrite a git-ignored file (origin started tracking someone's
  `.env` or editor settings — also spelt in another letter case, or a file where origin adds a
  folder) stops instead.
