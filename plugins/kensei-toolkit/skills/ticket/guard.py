#!/usr/bin/env python3
"""ticket guard — PreToolUse hook registered by the /kensei-toolkit:ticket skill.

Blocks, in code, what the skill may only do on the user's explicit command:

  commit       git commit / merge / rebase / cherry-pick / revert / am (history-writing) other
               than the two below — including a commit on a base branch the session was already
               on (the user chose to work there)
  merge-local  a local git merge / rebase that integrates into a base branch: run while a base
               branch is checked out, after `git switch <base>` in the same command, or
               `git rebase <upstream> <base>`; and any history write (cherry-pick, am, commit)
               after `git switch <base>` in the same command; `git commit` concluding a merge
               on a base branch; `git fetch . <branch>:<base>`. Bringing a base branch up to date
               from its own remote branch (`git merge --ff-only origin/main` on main) is history;
               a rebase of the base branch's own commits (`git rebase -i HEAD~3`) is commit
  base-sync    a git merge / rebase / pull of a base branch into the task branch (`git merge
               main`, `git rebase --onto origin/main …`, `git pull origin main`) — allowed under
               commit or merge-local; it uses
               up the commit command only when no merge-local command is open
  push         git push to a non-base branch (a plain `git push` / `git push <remote>` is judged by
               where the repository's settings send it: remote.<r>.push, push.default with
               branch.<b>.merge — upstream / simple / tracking to the upstream, matching to
               every branch — so a base branch there is force), git send-email / send-pack, gh repo
               sync, gh pr update-branch (force with --force / --rebase), any gh call with --push,
               GitHub/GitLab/GitKraken MCP file/branch pushes
  publish      creating something public outside the task branch, one grant per object — a
               release (gh release create / upload / edit, gh api writes to …/releases), a gist
               (gh gist create / edit, gh api …/gists), a repository (gh repo create / fork,
               gh api POST …/repos or …/forks); and the same through a GitHub MCP. A grant for
               one object does not reach another: «создай репо» is no release
  repo-admin   changing an existing repository, its secrets or CI, one grant per kind:
               settings (gh repo edit incl. --visibility / rename / archive / unarchive /
               autolink, gh api PATCH repos/o/r, hooks, collaborators, pages, topics, graphql
               updateRepository), secrets (gh secret / variable / deploy-key / ssh-key / gpg-key,
               …/secrets, …/variables, …/environments, …/keys, user/keys), ci (gh workflow / run /
               cache, …/actions, …/dispatches, …/deployments), protection (gh ruleset, …/rulesets,
               …/branches/<b>/protection, tags/protection), delete (gh release delete /
               delete-asset, gh gist delete); org endpoints and full api URLs alike; the same
               through a GitHub MCP. «поставь секрет» is no visibility change. gh repo delete and
               MCP repository deletes are destructive
  pr           opening a PR / MR (gh pr create, create_pull_request, api POST …/pulls)
  pr-body      a PR body that is not this session's approved <run_dir>/PR-BODY.md — on gh pr
               create, gh pr edit --body / --body-file, api …/pulls with body=, GraphQL
               createPullRequest / updatePullRequest, a GitHub / GitLab MCP PR create or update.
               The body passes when it is that file (--body-file, also after a `cd` in the same
               command; `--body "$(cat <file>)"`; gh api `-F body=@<file>`, GraphQL `body: $body`
               with `-F body=@<file>`) or text equal to it byte for byte, trailing line breaks
               aside; --web leaves it to the user. The file is the approved one only while its
               sha256 equals the last `pr_body_sha256:` in RUN.md (recorded when the user approved
               it) and the same command cannot write PR-BODY.md or RUN.md: it holds nothing but
               plain reads, git and gh — no redirect into a name built at run time (`> $P`,
               `PR-BODY.{md,x}`), no cp / tee / script / function. A body in a nested field
               (`-F input[body]=…`, JSON --input variables) cannot be checked. No tag grants
               another text: it is approved first
  merge        merging a PR / MR (gh pr merge, merge_pull_request, api …/merge, GraphQL
               mergePullRequest / enablePullRequestAutoMerge)
  force        a push that rewrites, deletes or widens: --force*, -f in any bundle (-fu),
               --delete, --mirror, --all, --tags, +refspec; or one to a base branch — main,
               master, develop, production, …, the remote's default branch, the `base_branch:`
               of this session's RUN.md, or the current branch when none is named
  branch-delete  git branch -d / -D / --delete («удали ветку», "delete the branch", [delete-branch]
               or [reset]); it grants no reset, clean or worktree remove. A remote branch delete
               (push --delete, :ref) stays force
  history      git pull, reset --hard/--soft or to a ref, update-ref, branch -f/-M/-C, checkout -B,
               switch -C, filter-branch/-repo, stash pop/apply/drop; and throwing local changes
               away: clean -f, checkout/restore of paths, checkout/switch --force, worktree remove
               --force
  status       a change of the status field only: an MCP update, transition or close / reopen
               tool whose only changed key is the status (state, transition), gh issue close /
               reopen. A typed command that names tasks covers exactly those tasks, each once
               (the call's task id, issue number or key matched against the ids and link
               segments typed); naming none, or an option's [status], covers one change
  create-task  creating a task / issue (clickup_create_task, create_issue, gh issue create, an
               operator create on a task model); one command covers the whole batch
  tracker      any other tracker write (MCP tool, clickup_execute_operator write pairs, gh issue /
               pr / label / project edits, curl/wget/httpie writes to a tracker) — including an
               update or transition that changes the status together with other fields
  destructive  deleting a task / issue / page, merging tasks
  delete       deleting a tracker comment
  comment      posting or editing a tracker comment — only text the user approved word for word
  send         a message that leaves through mail, chat or a calendar: a mail / chat / calendar
               MCP server's send, reply, forward, post, reaction, invite or respond, a chat
               message or canvas edit, any calendar event write (Gmail send_message / reply /
               forward / send_draft, Slack post / reply / canvas, Calendar create / update /
               delete / respond_to_event); and on any other non-tracker server, a tool named for
               sending mail or a message (send / reply / forward with mail / email / message /
               sms: outlook_email_send, send-mail, send_gmail_message) or for writing an event
               (create / update / delete / respond with event / meeting / calendar). Drafts and
               reads pass (`get_post`); a Slack send_message_draft counts as a send. Server names
               that also mean something else (signal, sms, zoom, teams, exchange) count only as
               the whole server name. curl / wget / httpie POSTs to webhooks
               (hooks.slack.com, discord.com/api/webhooks, *.webhook.office.com, Telegram bots,
               …). One command, one message
  tamper       writing to this session's transcript, the guard's markers, the guard's own files
               or plugin install, or hook settings (a Bash write to a settings*.json; a Write/Edit
               that changes hooks, disableAllHooks, enabledPlugins or allowManagedHooksOnly, the
               file compared as JSON before and after; `claude plugin disable`), or git settings
               that redirect a push, redefine a git word or run a program (`git config` writes of
               remote.*, branch.*.merge / remote / pushRemote, alias.*, core.hooksPath /
               editor / sshCommand / fsmonitor, sequence.editor, include.* / includeIf.*, url.*,
               push.default, filter / diff / merge drivers; `git remote add` / `set-url` /
               `rename` / `set-head`; and the same as a file write — Write / Edit, a redirect,
               tee, cp, PowerShell Set-Content / Add-Content / Out-File — to .git/config, any
               worktree's or submodule's config, .git/hooks/* or core.hooksPath, .git/info/
               attributes, ~/.gitconfig, $XDG_CONFIG_HOME/git/config, gh's config.yml; a
               .gitattributes or global attributes write that names a filter / diff / merge
               driver other than lfs and the built-in merge drivers, or copies one in; a link made
               to .git, .claude or a protected path; `gh alias set` naming a gated write, `gh
               alias import`; a write git itself makes at a path an option or a patch names —
               checkout-index --prefix (each tracked file under it), archive -o, diff / log
               --output, format-patch -o, bundle create, the paths of a readable apply / am patch
               under --directory with -p, a .gitattributes driver in one; tar -x of a readable
               archive's members, or of a `git archive --prefix=…` piped into it) — never
               allowed. Paths are compared without case, as macOS and Windows file systems do
               (`.GIT/config` is `.git/config`).
               A settings file is any settings*.json in the config dir ($CLAUDE_CONFIG_DIR or
               ~/.claude) or in a `.claude` dir, or managed-settings.json, matched on the path as
               written and on its realpath (a symlink to dotfiles is still the settings);
               reading them is fine. The guard's own files are the copy that runs: with
               `--plugin-dir` on a dev clone, a /ticket session cannot edit that clone's
               skills/ticket/ or hooks/ (deliberate — the session would be editing its own
               guard); develop the plugin with /ticket from the installed copy instead
  opaque       a command the guard cannot read: a program named by $VAR or $(…) that plausibly
               is git (its name says git; or it names no other tool and its first argument is
               a writing git subcommand; a literal basename after it, `$ROOT/gradlew`, decides), a
               git subcommand built from $VAR or $(…) (`git $S origin`), runner targets named
               exactly push / publish / release / deploy / ship (`make push`, `npm run release`),
               a `git -c` / --config-env of a setting that runs a program, defines an alias or
               changes a remote (core.editor / sshCommand / fsmonitor / hooksPath / pager,
               sequence.editor, alias.*, remote.*, url.*, include*, credential helpers, diff /
               merge / filter drivers, …; a plain pager or editor name passes, as in the
               environment), one of branch.* / push.default before a push; `git --exec-path=…`;
               GIT_CONFIG_* (but GIT_CONFIG_GLOBAL/SYSTEM=/dev/null, GIT_CONFIG_NOSYSTEM),
               GIT_SSH_COMMAND, GIT_EDITOR, GIT_SEQUENCE_EDITOR, GIT_PAGER, EDITOR, … set before git
               or exported earlier in the command (a plain editor or pager name passes); a git or
               gh write that git runs for itself — rebase -x, bisect run, submodule foreach,
               filter-branch --*-filter, filter-repo callbacks, difftool / mergetool -x, grep -O,
               --upload-pack / --receive-pack — or that xargs,
               parallel, find -exec, fd -x run once per input; in PowerShell, Invoke-Expression
               of a built string (`iex $cmd`, `iex ('git ' + 'push')`), an encoded command (pwsh
               -EncodedCommand / -enc / -e, from Bash too), a call `&` / dot-source `.` (with or
               without a space: `&'git'`, `&("git")`, `.( … )`) of a name that is not a literal —
               `& "gi$('t')"`, `&("gi"+"t")`, `& $g` — unless it plainly names another tool, and a
               `$var` / `(…)` program that may be git (`&("{0}{1}" -f …)`, `&(Get-Command gi*)`),
               `git @args`, git after `$env:` / Set-Item env: / [Environment]::
               SetEnvironmentVariable or a cmd `set` of a setting as above (names without case, as
               Windows reads them; HOME, XDG_CONFIG_HOME and USERPROFILE move the global config);
               a git write at a path built at run time, apply / am --directory outside the
               repository, --unsafe-paths with a patch from stdin, a patch it cannot open; a gh
               word it does not know while the command sets GH_CONFIG_DIR / XDG_CONFIG_HOME (Bash,
               export, env, $env:, cmd set) to a config.yml it cannot read or without that alias
               — one it reads gives the alias; a GraphQL request whose query is not
               read (a variable, a substitution, a missing @file or --input, a curl body that is
               not literal JSON) — never allowed. A shell named by
               $VAR (`$SHELL -c "…"`) is read like `sh -c`; `pwsh -c "…"` from Bash like PowerShell
  skill-post   a skill told to post (/code-review --comment, --post) — never allowed
  delegate     SendMessage — the user confirms it (permissionDecision "ask")
  schedule     RemoteTrigger (other than list / get / list_runs / get_run_log) and CronCreate: work
               that runs later or in the cloud, outside this guard — the user confirms it ("ask");
               subagents are refused
  stash        git stash push — subagents only; the orchestrator stashes on the Step 5 answer

The PowerShell tool is read like Bash where it can be: git and gh words in each statement (call
operators, `git.exe`, `& (Get-Command git)`, backtick escapes undone; a script block `&{…}`,
`.{…}`, `{ … }`, `@(…)`, `[void](…)` and `$(…)`, also inside "…", read as statements),
Start-Process arguments ('push','origin', @('push'), -FilePath git.exe -ArgumentList 'push
origin'), what a literal
Invoke-Expression / cmd /c string carries, Invoke-WebRequest / Invoke-RestMethod / curl.exe
writes, and writes to protected paths — in order after Set-Location / cd, Copy-Item / Move-Item
into a directory as DIR/<name>, [IO.File]::Write* / Copy / Move, a `$p = '…'` literal read where
$p is used. A failed PowerShell call uses its command up.

A command comes from the session transcript, read back to the latest message the user typed
(origin.kind == "human"). A message the user queued while the model was busy adds its orders to
the message before it, where it arrived — unless it holds back: a stop word («стоп», «подожди»,
«не сейчас», wait), a negated, cancelled or deferred gated action («не коммить пока», «пуш
позже», «отмена»), or a change of plan whatever it names («передумал», «я сам закоммичу»,
«сначала …», "first …", «перед этим», «дай посмотреть», «не надо», "let me see"). That voids
every command given so far, and the message itself grants nothing. Taking the step over is read
in the first person only: «я сам», «сам запушу», "I'll do it" revoke; «сделай сам» / «давай сам»
(the model is told to do it) do not. A negation about anything
else («тесты не трогай») voids nothing.

  typed text     conservative: imperatives such as «закоммить», «запушь», «залей ветку»,
                 «переведи в ревью» (status), «создай задачу» (create-task), «удали ветку»
                 (branch-delete; "pull the latest" orders nothing), «залейся в мейн» /
                 «смерджи в мейн» (commit + merge-local; «залей» also the push to the base
                 branch); negation scoped to its list item, conditional or cancelled sentences
                 dropped.
                 A question («почему git push упал?», "commit?") orders nothing unless it is an
                 imperative or a polite request («закоммить?», «можешь закоммитить?», "could you
                 push it?"). A git/gh command counts only as the whole item, never mentioned
                 inside a sentence; pasted text — fenced blocks, `>` lines, the middle of a long
                 message (only the first and last 4 KB are read), a quotation (paired quotes,
                 single ones included, after «говорит», «написано», «сказано», "says"; double
                 quotes after a colon: «тикет говорит: «запушь»») — never counts. English verbs need a git object: "revert that
                 commit", "push to github", "commit with message …", not "revert these
                 functions". A status command covers the tasks it names, each once («переведи
                 86abc1 и 86abc2 в ревью» is those two; a PR or commit link, «PR #45» is no
                 task); naming none, one change. A message to a chat or mail needs somewhere to
                 go («отправь сообщение», «напиши в слак», «отправь письмо»), an event the
                 calendar («создай событие в календаре», «назначь встречу») — «напиши сообщение
                 коммита» and «добавь событие в лог» are no send. «обнови описание PR» / "update
                 the PR description" is a tracker edit.
  the commit skill  a typed `/kensei-toolkit:commit` (or `/commit`) is «закоммить» + «запушь»:
                 inside a /ticket run that skill hands both to the gate. `pr` in its arguments
                 adds the PR, `main` the merge into and push to the base branch (keys only as
                 the leading argument words; without the push, `main` is the local merge alone);
                 `local` / `locally` / `no-push`, or any word of a push in the arguments («не
                 пушить», "push later"), leave the push out; a hint that holds something back
                 («не вливай в main») leaves only the commit; `plan` orders nothing. When the
                 model invokes the skill kensei-toolkit:commit after a typed «закоммить» (not
                 a typed /kensei-toolkit:commit, which settles the push itself), the push joins
                 that command once — not when the
                 user's words mention a push at all («не пушь», «пуш потом») or hold it back
                 («только закоммить», «локально»); the model's own
                 arguments to it add nothing else
  AskUserQuestion answers after that message: an option the user picked grants exactly the tags
                 in its label — [commit] [merge-local] [push] [pr] [merge] [force-push]
                 [publish] [repo-admin] [reset] [delete-branch] [status] [create-task] [tracker-edit]
                 [delete-task] [delete-comment] [send]; [publish] grants the object its label names
                 (релиз / release, репо / repo, гист / gist) and [repo-admin] the kind its label
                 names (visibility / rename / archive, секрет / variable, workflow / CI, защита /
                 ruleset, удалить релиз / гист) — nothing when it names none; [post]
                 approves the option's `preview` as the comment text, if the preview was shown
                 in full

A command is used up by the call it authorized: one commit, one push, one status write — and one
call may not carry the same action twice. A create-task command is the exception: it covers every
task of the batch it was given for, until the user's next message. A call the user rejected uses nothing up, nor does one
that failed (is_error: a non-zero exit, a rejected push, a commit a hook refused) — for a shell
command only when the failure is the gated step's own: one command, or an `&&` chain ending in
it. In `git push; false` or `git push && npm test` the push may have happened, so it is used
up. An MCP call that failed is used up unless the head of its error plainly refuses it (a
validation error, invalid params / -32602, a 4xx code, "not found"): after a timeout or an
unclear error the write may have landed, and it is not repeated blind. A comment text that was posted cannot be posted again;
every text field of the call must be the approved text.
When the model invoked /ticket itself, what the user typed before that does not count.

Two registrations, because a skill's frontmatter hooks do not reach its subagents (measured):

  guard.py --main      SKILL.md frontmatter; the main session once /ticket is invoked. Marks the
                       session as a ticket session, then checks the command.
  guard.py --subagent  plugin hooks/hooks.json via hooks/subagent-guard.sh, which runs this only
                       for subagent calls (`agent_id` set) in a marked session. Subagents are
                       refused every gated action — only the orchestrator commits, pushes or
                       writes to the tracker.
  guard.py --post      SKILL.md frontmatter, PostToolUse; only notes this session's run (below).

This session's run is the run directory whose RUN.md the main session last wrote (Write / Edit,
a shell redirect into it, or a PowerShell Set-Content / Out-File / redirect), noted by
`guard.py --post` once the call has run; the marker keeps it. Only its `base_branch:` joins the base branches, and only its PR-BODY.md is an approved PR
body; before the session writes a RUN.md there is none, and another task's RUN.md in the
same repository never counts.

Output: nothing when the call is not gated or is authorized — the normal permission flow
continues, the hook never grants anything. A JSON "deny" with the reason otherwise ("ask" for
SendMessage, RemoteTrigger and CronCreate in the main session). Internal errors fail closed for calls that look gated.

Known gaps, where the skill's text rules alone apply: git run from a script or an interpreter
(`sh ./x.sh`, `python -c`, a .ps1), the browser, a child `claude` session, tracker CLIs other
than gh, git settings exported by an earlier command, a patch fed to git apply / am on stdin,
a tar archive from stdin or one tarfile cannot list, a git archive written earlier in the same
command included (only -C .git/hooks is tamper; .git and ~/.claude pass), a tar's attached
-C<dir>, name rewriting (--transform, --xform, -s) or run in a subshell after cd ((cd .git &&
tar -x), sh -c), any tar in PowerShell (no tar check there), other extractors and patch(1)
(unzip, Expand-Archive, python -m tarfile, busybox tar, pax), git merge-file (its first path
gets no protected-path check, only the PR-write one), globs in a target path (.git/conf*),
PowerShell env writes the regex misses (-Value or -Force before -Path, New-Item -Name X -Path
env:, an Environment:: or computed env: path, Copy-Item / Rename-Item into env:, Set-Location
env:, Start-Process -Environment), GH_CONFIG_DIR set for a nested shell (X=… bash -c, cmd /c
"set X=… & gh") or by readonly / eval / read, HOME / $env:AppData / $env:USERPROFILE moving gh's
config (HOME only makes git opaque), a git alias for checkout-index / archive / apply in the
tamper check, a gh config.yml
that exists and is rewritten in the same command (read as it was), mailbox housekeeping
(labels, trash), a
PowerShell variable set other than by a literal in the same command (judged by name), reported
speech without quotes («тикет говорит: запушь» is read as an order). PR-BODY.md is approved by
its hash in RUN.md: the guard trusts that `pr_body_sha256:` was written when the user approved
the text shown at the gate. A cancel word drops the sentence
(«отмени встречу», "cancel the meeting" grant nothing; a [send] option does). A typed status
command allows any status change of a named task, closing the issue included — the guard cannot
see the target status. A typed «сначала прогони тесты, потом закоммить» grants the commit at
once — the order rests on the skill text.
Skill-frontmatter hooks do not come back in a resumed session (`claude --resume`, measured on
2.1.286) until the skill is invoked again: `--main` and `--post` are off until then; the plugin
hooks.json wrapper does not depend on the skill (by design, not measured after a resume).
The transcript fields read here (origin.kind, queued_command attachments, toolUseResult,
is_error) were checked against Claude Code 2.1.286 (testdata/transcript-2.1.286.jsonl);
task-notification queued_command entries there also carry `renderedInHumanTurn`, which is not
read — their origin is not human. ClickUp operators are judged by their names' words: the live
catalogue had none enabled (testdata/clickup-operators.json).
"""

import fnmatch
import hashlib
import json
import os
import re
import shlex
import subprocess
import tarfile
import sys

# --- classification tables ----------------------------------------------------------------

TRACKER_SERVER = re.compile(
    r"(?<![a-z0-9])(?:clickup|jira|atlassian|confluence|linear|github|gitlab|youtrack|asana|"
    r"notion|trello|monday|shortcut|taiga|plane|gitkraken)(?![a-z0-9])", re.I)
GIT_SERVER = re.compile(r"^git$|git[-_]?mcp|^mcp[-_]?git$|gitkraken", re.I)
READ_WORDS = {
    "get", "list", "search", "find", "filter", "read", "fetch", "view", "query", "download",
    "export", "describe", "show", "lookup", "check", "count", "schema", "operators", "hierarchy",
    "members", "authenticate", "authentication", "whoami", "me", "info", "diff", "log",
}
WRITE_WORDS = {
    "create", "update", "delete", "add", "remove", "move", "merge", "set", "attach", "upload",
    "start", "stop", "assign", "unassign", "close", "reopen", "edit", "post", "send",
    "transition", "link", "unlink", "archive", "request", "execute", "mark", "tag", "untag",
    "push", "fork", "star", "dismiss", "approve", "submit", "rerun", "cancel", "trigger",
    "reply", "resolve", "write", "rename", "transfer", "lock", "unlock", "pin", "unpin",
}
COMMENT_WORDS = {"comment", "comments", "message", "messages", "chat", "reply", "note", "notes",
                 "story"}
TEXT_KEYS = re.compile(r"comment|body|text|content|message|markdown", re.I)
ID_KEY = re.compile(r"(?:^|_|-)id$|[a-z]Id$|^id$", re.I)


def text_key(key):
    """A key that names comment text — `comment_text`, `body`; never an id (`comment_id`)."""
    key = str(key)
    return bool(TEXT_KEYS.search(key)) and not ID_KEY.search(key) and key != "type"


GIT_HISTORY = {"commit", "merge", "rebase", "cherry-pick", "revert", "am", "commit-tree"}
NON_WRITING = {"--abort", "--quit", "--dry-run", "--no-commit", "--show-current-patch",
               "--edit-todo"}
BASE_BRANCHES = {"main", "master", "develop", "dev", "trunk", "release", "production", "prod",
                 "staging", "stable"}
# gh: commands that only read or touch local config; anything else is a write
GH_LOCAL_GROUPS = {"auth", "config", "alias", "completion", "help", "extension", "version",
                   "browse", "search", "status", "codespace", "co", "copilot"}
GH_READ_VERBS = {"view", "list", "ls", "diff", "checks", "status", "download", "watch", "clone",
                 "checkout", "get", "field-list", "item-list", "verify", "verify-asset", "check",
                 "logs"}
# skills that publish what the guard cannot see when invoked with these flags
PUBLISH_FLAGS = re.compile(r"(?<![\w-])--(?:comment|post)(?![\w-])")
# kinds refused to subagents only — the orchestrator may run them (the Step 5 "stash it")
SUBAGENT_ONLY = {"stash"}
GIT_BUILTINS = {
    "add", "am", "apply", "archive", "bisect", "blame", "branch", "bundle", "cat-file",
    "check-ignore", "checkout", "cherry", "cherry-pick", "clean", "clone", "commit",
    "commit-tree", "config", "count-objects", "describe", "diff", "diff-files", "diff-index",
    "diff-tree", "difftool", "fetch", "for-each-ref", "format-patch", "fsck", "gc", "grep",
    "hash-object", "help", "hook", "init", "lfs", "log", "ls-files", "ls-remote", "ls-tree",
    "maintenance", "merge", "merge-base", "mergetool", "mv", "name-rev", "notes", "prune",
    "pull", "push", "range-diff", "read-tree", "rebase", "reflog", "remote", "repack",
    "replace", "reset", "restore", "rev-list", "rev-parse", "revert", "rm", "shortlog", "show",
    "show-ref", "sparse-checkout", "stash", "status", "submodule", "subtree", "switch",
    "symbolic-ref", "tag", "update-index", "update-ref", "var", "version", "whatchanged",
    "worktree", "write-tree",
}
GIT_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
                       "--config-env", "--super-prefix"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}
SHELL_KEYWORDS = {"if", "then", "else", "elif", "fi", "do", "done", "while", "until", "case",
                  "esac", "{", "}", "!", "time", "coproc", "function", "select"}
WRAPPERS = {  # program -> options that take a value
    "command": set(), "builtin": set(), "nohup": set(), "exec": set(), "noglob": set(),
    "time": set(), "chronic": set(), "unbuffer": set(), "doas": {"-u"},
    "sudo": {"-u", "-g", "-C", "-h", "-p", "-U", "-r", "-t", "-D"},
    "caffeinate": {"-t", "-w"}, "nice": {"-n"}, "ionice": {"-c", "-n"}, "stdbuf": set(),
    "timeout": {"-s", "-k"}, "gtimeout": {"-s", "-k"}, "arch": set(),
    "xargs": {"-I", "-n", "-P", "-L", "-s", "-d", "-E", "-a"},
}
GITISH = re.compile(r"(?<![^\s;&|(`'\"=])(?:\S*/)?(git|gh)(?=\s)", re.I)  # at token starts only
# a command word produced by a substitution: `$(echo git) push`, `` `which git` push ``
SUBST_PROGRAM = re.compile(r"(?:^|[;&|(\n])\s*(?:\w+=\S*\s+)*((?:\$\([^()]*\)|`[^`]*`)\S*)\s+"
                           r"([^;&|\n]*)")

# --- reading what the user said -----------------------------------------------------------

NEG = re.compile(r"(?<![\w'])(?:не|ни|нет|без|нельзя|никогда|рано|стоп|don'?t|dont|do\s+not|"
                 r"not|never|no|nor|without|stop)(?![\w'])")
REMIND = re.compile(r"(?<![\w'])(?:не\s+забудь(?:те)?|don'?t\s+forget(?:\s+to)?)(?![\w'])")
DEFER = re.compile(r"(?<![\w'])(?:позже|попозже|later|after|после|не\s+сейчас|not\s+(?:yet|now))"
                   r"(?![\w'])")
TAIL = re.compile(r"(?:(?:пока|ещ[её]|только|just|only|but)\s+)*(?:не\s+(?:сейчас|сегодня|сразу|надо|"
                  r"нужно|стоит)|позже|попозже|later|not\s+(?:yet|now|today|right\s+now)|рано|"
                  r"пока\s+рано)")
PAIR = re.compile(r"(?<![\w/.-])(commit|push|коммит|пуш)\s*/\s*(?=(?:commit|push|коммит|пуш)\s*"
                  r"(?:please|pls|пожалуйста)?\s*(?:[,.;!?]|$))")
CANCEL = re.compile(r"(?<![\w'])(?:отмен\w*|cancel\w*|скип\w*|skip\w*|отбой|не\s+нужн\w*|"
                    r"not\s+needed)(?![\w'])")
COND = re.compile(r"(?<![\w'])(?:если(?!\s+что(?:[\s,—–-]|$))|когда|как\s+только|перед\s+тем\s+как|"
                  r"прежде\s+чем|if|when|once|unless|before|или|or)(?![\w'])")
POLITE = re.compile(r"(?<![\w'])(?:можешь|можете|сможешь|можно|please|pls|пожалуйста|"
                    r"could\s+you|can\s+you|would\s+you)(?![\w'])")
SPLIT = re.compile(r"\s*(?:[,;:—–&+]|\s-\s|(?<![\w'])(?:и|а|но|затем|потом|then|and|but)"
                   r"(?![\w']))\s*")
POLITE_LEAD = re.compile(r"^(?:(?:could|can|would|will)\s+you\s+(?:please\s+)?|please\s+|"
                         r"pls\s+|plz\s+)+")
LEAD = re.compile(r"^(?:(?:ну|ок|окей|ok|okay|да|yes|yeah|yep|lgtm|давай|тогда|теперь|сейчас|"
                  r"now|so|go\s+ahead|go|just|also|ещ[её]|только|сразу|plz|pls|please|"
                  r"пожалуйста)\s+)+")
BASE_WORDS = r"(?:main|master|develop|мастер|мейн|мэйн|девелоп)"
PR_WORDS = r"(?:пр|pr|пулл?-?реквест\w*|пул\s+реквест\w*|pull\s+request|мр|mr|merge\s+request)"
POUR = r"(?:за|в)?(?:лей|ливай)(?:ся|тесь|те)?"  # «залей(ся)», «влей», «заливай» … в мейн
# a word between the verb and «в мейн» that is not the PR: «смерджи пр в мейн» merges the PR
NOT_PR = r"(?!(?:пр|pr|пулл?\S*|пул|pull|мр|mr|merge\s+request)(?![\w-]))"
# publish and repo-admin objects: whole nouns, not «релизную ветку», «репорт», "release build"
RELEASE_RU = r"релиз(?:а|ы|ов|ом)?(?![\w-])"
REPO_WORD = (r"(?:репо|репозитори(?:й|я|ю|и|ем)|repos?|repository)(?![\w-])")
NO_PREP = r"(?:\s+(?!(?:в|во|к|на|из|по|для|от|с|со|in|to|into|for)\s)\S+)?"  # «создай в репо …»
NOT_RELEASE = (r"(?!\s+(?:branch|build|candidate|notes?|mode|config\w*|flag|script|job|pipeline|"
               r"process|checklist|version|tag)(?![\w-]))")
NOT_REPO = (r"(?!\s+(?:pattern|class|interface|layer|object|method|module|abstraction|"
            r"implementation|instance|field|wrapper|mock|stub|test|type|struct|service|helper|"
            r"паттерн|класс|интерфейс|слой|метод|модул)\w*)")
CHAT_RU = (r"(?:слак|slack|чат|телеграм\w*|telegram|дискорд|discord|тимс|teams|"
           r"канал\w*|личк\w*)(?![\w-])")
RU_IMPERATIVE = {
    "commit": r"(?:за)?комм?ит(?:ь|ни|ьте|ните)|(?:за|с)?мерд?ж(?:и|ни)(?!\s+(?:\S+\s+)?"
              r"(?:пр|pr|пулл\w*|pull|mr|мр)\b)|ребейзни|засквошь|сквошни|(?:за)?амендь|амендни|"
              r"(?:сделай|сделайте|создай|оформи|собери|упакуй|схлопни)(?:\s+\S+){0,3}\s+коммит\w*|" +
              POUR + r"(?:\s+" + NOT_PR + r"\S+){0,2}\s+(?:в|на)\s+" + BASE_WORDS + r"|"
              r"мерд?ж(?:\s+" + NOT_PR + r"\S+)?\s+(?:в|на)\s+" + BASE_WORDS,
    "push": r"(?:(?:за)?пуш(?:ь|ни|ьте|ните)|запуш)(?!\s+(?:в|на)\s+" + BASE_WORDS + r")|"
            r"за(?:лей|ливай)(?:те|ся)?(?:\s+\S+){0,2}\s+(?:(?:на|в)\s+)?(?:ветк\w*|бранч\w*|"
            r"origin|remote|ремоут|гит|github|гитхаб)",
    "pr": r"(?:открой|создай|сделай|заведи)(?:\s+\S+)?\s+" + PR_WORDS,
    "merge": r"(?:за|с)?мерд?ж(?:и|ни)(?:\s+\S+)?\s+(?:пр|pr|пулл\w*|pull\s+request|mr|мр)",
    "force": r"форсни|(?:за)?форс-?пуш\w*|(?:удали|снеси|грохни)(?:\s+\S+){0,2}\s+(?:ветк|бранч)\w*"
             r"(?:\s+\S+)?\s+(?:на|с|со|из|в)\s+(?:origin|remote|ремоут\w*|гитхаб\w*|github|сервер\w*|"
             r"удал[её]нн\w*)|(?:за)?пуш\w*(?:\s+\S+)?\s+(?:в|на)\s+" + BASE_WORDS +
             r"|" + POUR + r"(?:\s+\S+){0,2}\s+(?:в|на)\s+" + BASE_WORDS,
    "history": r"сбрось|откати|подтяни|спулль|(?:верни|достань|примени|восстанови)(?:\s+\S+)?\s+"
               r"(?:ст[еэ]ш\w*|stash)|(?:почисти|очисти)(?:\s+\S+){0,2}\s+(?:рабоч\w+|дерев\w*|"
               r"worktree|untracked)|выкинь(?:\s+\S+){0,2}\s+изменени\w*",
    "branch-delete": r"(?:удали|снеси|грохни)(?:\s+\S+){0,2}\s+(?:ветк\w*|бранч\w*)",
    "merge-local": POUR + r"(?:\s+" + NOT_PR + r"\S+){0,2}\s+(?:в|на)\s+" + BASE_WORDS + r"|"
                   r"(?:с|за)?мерд?ж(?:и|ни)?(?:\s+" + NOT_PR + r"\S+)?\s+(?:в|на)\s+" +
                   BASE_WORDS,
    "status": r"(?:переведи|переведите|перекинь|перенеси|двинь|кинь|поставь)(?:\s+\S+){0,3}\s+"
               r"(?:в|на)\s+(?:\S+\s+)?(?:ревью|review|работу|progress|done|готово|qa|тест\w*|"
               r"closed|закрыт\w*)|(?:смени|поменяй|измени|обнови|поставь)(?:\s+\S+){0,2}\s+"
               r"статус\w*|(?:возьми|бери)(?:\s+\S+)?\s+в\s+работу|закрой(?:\s+\S+)?\s+"
               r"(?:задач\w*|тикет\w*|issue)|переоткрой\w*|статус\s*(?:→|->)",
    "tracker": r"назначь\w*(?!(?:\s+\S+)?\s+(?:встреч|созвон|митинг|событи)\w*)|"
               r"(?:обнови|поправь|измени|перепиши)\s+описани\w*(?:\s+(?:к|у|в|для))?\s+" +
               PR_WORDS,
    # a message with somewhere to go: a letter, a chat, «отправь сообщение» — not «напиши
    # сообщение коммита»; a meeting, or an event in the calendar — not «добавь событие в лог»
    "send": r"(?:отправь|пошли|перешли|ответь|напиши)(?:\s+\S+){0,2}\s+(?:(?:на\s+)?письм\w*|"
            r"мейл\w*|email|(?:в|во)\s+" + CHAT_RU + r")|(?:отправь|пошли|перешли)(?:\s+\S+)?\s+"
            r"сообщени\w*(?!(?:\s+\S+)?\s+(?:в\s+|к\s+|для\s+)?коммит\w*)|напиши(?:\s+\S+)?\s+"
            r"сообщени\w*(?:\s+\S+)?\s+(?:в|во)\s+" + CHAT_RU + r"|"
            r"(?:создай|назначь|поставь|добавь|перенеси)(?:\s+\S+){0,2}\s+(?:встреч\w*|"
            r"созвон\w*|митинг\w*)|(?:создай|назначь|поставь|добавь)(?:\s+\S+){0,2}\s+"
            r"(?:событи\w*(?:\s+\S+){0,2}\s+)?в\s+календар\w*|"
            r"(?:прими|отклони)(?:\s+\S+)?\s+приглашени\w*",
    "create-task": r"(?:создай|создайте|заведи|заведите|добавь|добавьте)"
                   r"(?:\s+(?!(?:в|во|к|на|из|по|для|от)\s)\S+){0,2}\s+"
                   r"(?:задач\w*|тикет\w*|таск\w*|issue\w*|подзадач\w*)"
                   r"(?!\s+(?:коммент|комментари|comment)\w*)",
    "publish-release": r"(?:опубликуй|выпусти|создай|сделай)" + NO_PREP + r"\s+(?:" + RELEASE_RU +
                       r"|release(?![\w-])" + NOT_RELEASE + r")",
    "publish-repo": r"(?:создай|заведи|форкни)" + NO_PREP + r"\s+" + REPO_WORD + NOT_REPO,
    "publish-gist": r"(?:создай|заведи)" + NO_PREP + r"\s+(?:гист(?:а|ы)?|gists?)(?![\w-])",
    "repo-admin-settings": r"(?:сделай|переведи)(?:\s+\S+)?\s+" + REPO_WORD + r"\s+(?:в\s+)?"
                           r"(?:публичн\w*|приватн\w*|открыт\w*|закрыт\w*|public|private)|"
                           r"(?:переименуй|заархивируй|архивируй|разархивируй)(?:\s+\S+)?\s+" +
                           REPO_WORD,
    "repo-admin-secrets": r"(?:поставь|задай|добавь|обнови|смени|удали|установи)(?:\s+\S+)?\s+"
                          r"(?:секрет(?:ы|а|ов)?|secrets?|переменн\w*\s+(?:окружени\w*\s+)?"
                          r"(?:в\s+)?(?:репо\w*|ci|actions|github)|deploy[- ]?keys?|"
                          r"деплой[- ]?ключ\w*)(?![\w-])(?!(?:\s+\S+){0,2}\s+(?:в|во|из)\s+"
                          r"(?:код|конфиг|файл|класс|модул|скрипт|тест|\.?env)\w*)",
    "repo-admin-ci": r"(?:запусти|перезапусти|включи|выключи|отключи)(?:\s+\S+)?\s+(?:workflow|"
                     r"воркфло\w*|ci|джоб\w*|пайплайн\w*)(?![\w-])",
    "repo-admin-protection": r"(?:включи|выключи|настрой|поставь|сними|убери)(?:\s+\S+)?\s+"
                             r"(?:защит\w*\s+(?:\S+\s+)?(?:ветк\w*|бранч\w*|branch\w*|тег\w*|"
                             r"main|master|мейн\w*|мастер\w*)|ruleset\w*|branch\s+protection)",
    "repo-admin-delete": r"удали(?:\s+\S+)?\s+(?:" + RELEASE_RU + r"|releases?|гист(?:а|ы)?|gists?)"
                         r"(?![\w-])",
    "delete": r"(?:удали|сотри|снеси)(?:\s+(?:этот|тот|мой|свой|последний|предыдущий|старый|наш|"
              r"его))?\s+(?:коммент\w*|комментари\w*)"
              r"(?!(?:\s+\S+){0,2}\s+(?:в|из)\s+(?:код|файл))",
    "destructive": r"удали(?:\s+\S+){0,2}\s+(?:задач\w*|тикет\w*)",
}
RU_INFINITIVE = {  # only with a polite marker or «не забудь»
    "commit": r"(?:за)?комм?ит(?:ить|нуть)",
    "push": r"(?:за)?пуш(?:ить|нуть)",
    "status": r"перевести(?:\s+\S+){0,3}\s+(?:в|на)\s+(?:\S+\s+)?(?:ревью|review|работу|done)",
    "create-task": r"(?:создать|завести)(?:\s+\S+){0,2}\s+(?:задач\w*|тикет\w*|issue\w*)",
}
# An English verb orders a git action only with a git object: "commit it", "push the changes",
# "revert that commit" — not "revert these functions" or "reset the counter".
GIT_OBJECT = (r"(?:changes?|commits?|work|fix(?:es)?|branch(?:es)?|diff|stuff|everything|all|"
              r"result|edits?|code|latest)")
EN_TAIL = (r"(?=\s*$|\s+(?:these|those)(?:\s+(?:please|pls|plz|now))?\s*$|\s+(?:it|this|that|them|everything|now|please|pls|plz|asap|already|"
           r"--\S*)(?![\w'])|\s+(?:(?:the|my|your|these|those|this|that|all|our|its)\s+)+"
           + GIT_OBJECT + r"(?![\w'])|\s+" + GIT_OBJECT + r"(?![\w'])|\s+(?:to|into|onto|with)\s+"
           r"(?:the\s+)?(?:branch|remote|origin|upstream|github|gitlab|\S*/\S+|" + BASE_WORDS + r")(?![\w'])|"
           r"\s+with\s+(?:the\s+|a\s+)?(?:message|msg|-m)(?![\w']))")
EN_STATUS = (r"(?:review|done|qa|testing|test|closed|complete|completed|backlog|todo|to\s+do|"
             r"ready|blocked|in\s+(?:review|progress|qa|testing|work)|progress|\"[^\"]+\")")
EN_IMPERATIVE = {  # must start the list item
    "commit": r"(?:commit|amend|rebase|squash|cherry-?pick|revert|merge(?!\s+(?:the\s+)?"
              r"(?:pr|pull)))" + EN_TAIL,
    "push": r"push" + EN_TAIL,
    "pr": r"(?:open|create|raise|file)\s+(?:a\s+|the\s+)?(?:pr|pull\s+request|mr|merge\s+request)",
    "merge": r"merge\s+(?:the\s+|this\s+)?(?:pr|pull\s+request)",
    "force": r"force[- ]?push|push(?:\s+\S+){0,2}\s+(?:to|into)\s+(?:origin/)?" + BASE_WORDS +
             r"(?![\w'])|delete\s+(?:the\s+|this\s+|that\s+|my\s+)*(?:remote\s+branch(?:es)?|"
             r"branch(?:es)?(?:\s+\S+)?\s+(?:on|from|in)\s+(?:the\s+)?(?:origin|remote|github|"
             r"gitlab|server))(?![\w-])",
    # "pull the latest" is no reset: it names what to bring in, not a history rewrite
    "history": r"(?:pull(?!\s+(?:in\s+)?(?:the\s+)?latest(?![\w']))|reset)" + EN_TAIL +
               r"|(?:pop|apply|restore)\s+(?:the\s+)?stash|"
               r"stash\s+pop|discard\s+(?:(?:the|my|all|local)\s+)*changes",
    "branch-delete": r"delete\s+(?:the\s+|this\s+|that\s+|my\s+|local\s+)*branch(?:es)?"
                     r"(?![\w-])(?!\s+protection)",
    "merge-local": r"merge(?:\s+(?!(?:pr|pull|mr|merge)(?![\w-]))\S+){0,2}\s+(?:to|into)\s+"
                   r"(?:origin/)?" + BASE_WORDS + r"(?![\w'])",
    "status": r"(?:move|set|change|mark|close|reopen)(?:\s+\S+){0,3}\s+(?:status|"
              r"(?:as\s+|to\s+)?" + EN_STATUS + r"(?![\w']))|(?:close|reopen)\s+"
              r"(?:the\s+|this\s+)?(?:task|issue|ticket)",
    "tracker": r"(?:update|edit|rewrite)\s+(?:the\s+)?(?:pr|pull\s+request|mr)\s+"
               r"(?:description|body)|assign\s+(?:the\s+|this\s+)?(?:task|issue|ticket)|assign\s+(?:it\s+|this\s+|"
               r"(?:the\s+|this\s+)?(?:task|issue|ticket)\s+)?to\s+me(?![\w'])",
    "send": r"(?:send|forward|reply\s+to)\s+(?:the\s+|an?\s+|this\s+|that\s+|my\s+)?(?:e-?mail|"
            r"mail|message|reply|invite|invitation)|post\s+(?:it\s+|this\s+|that\s+)?(?:to|in|on)\s+"
            r"(?:the\s+)?(?:slack|discord|teams|telegram|channel|chat)|(?:create|schedule|book|"
            r"reschedule)\s+(?:a\s+|an\s+|the\s+|this\s+)?(?:meeting|event|call)(?![\w-])|"
            r"(?:accept|decline)\s+(?:the\s+|this\s+)?(?:invite|invitation|meeting)",
    "create-task": r"(?:create|open|file|add)\s+(?:a\s+|an\s+|the\s+|these\s+|new\s+)*"
                   r"(?:tasks?|issues?|tickets?)(?![\w-])(?!\s+comments?)",
    "publish-release": r"(?:publish|create|cut)\s+(?:a\s+|the\s+|new\s+)*release(?![\w-])" +
                       NOT_RELEASE,
    "publish-repo": r"(?:create|fork)\s+(?:a\s+|the\s+|new\s+|private\s+|public\s+)*"
                    r"(?:repo|repository)(?![\w-])" + NOT_REPO,
    "publish-gist": r"create\s+(?:a\s+|the\s+|new\s+)*gist(?![\w-])",
    "repo-admin-settings": r"make\s+(?:the\s+|this\s+)?(?:repo|repository)\s+(?:public|private)|"
                           r"(?:rename|archive|unarchive)\s+(?:the\s+|this\s+)?(?:repo|repository)"
                           r"(?![\w-])",
    # a secret or variable of the repository or CI — not "add a local variable for the count"
    "repo-admin-secrets": r"(?:set|add|update|delete|remove|rotate)\s+(?:a\s+|an\s+|the\s+)?"
                          r"(?:new\s+)?(?:(?:repo|repository|ci|actions|github|org)\s+"
                          r"(?:secret|variable)s?(?![\w-])|(?:\S+\s+)?secrets?(?:\s+\w*_[\w_]*)?"
                          r"(?=\s*$|\s+(?:in|on|to|for|from)\s+(?:the\s+)?(?:repo|repository|ci|"
                          r"actions|github)(?![\w-]))|(?:secret|variable)s?(?:\s+\S+)?\s+(?:in|on|to|"
                          r"for|from)\s+(?:the\s+)?(?:repo|repository|ci|actions|github)(?![\w-])|"
                          r"deploy\s+keys?(?![\w-]))",
    "repo-admin-ci": r"(?:re)?run\s+(?:the\s+)?(?:\S+\s+)?(?:workflow|ci|pipeline)(?![\w-])|"
                     r"(?:enable|disable)\s+(?:the\s+)?(?:\S+\s+)?workflow(?![\w-])",
    "repo-admin-protection": r"(?:enable|disable|add|set\s+up|remove)\s+(?:the\s+)?branch\s+"
                             r"protection|(?:create|update|delete)\s+(?:a\s+|the\s+)?ruleset",
    "repo-admin-delete": r"delete\s+(?:the\s+)?(?:release|gist)(?![\w-])",
    "delete": r"(?:delete|remove)\s+(?:(?:the|that|this|my|last)\s+)*comment(?=\s*$|\s+(?:from|on|"
              r"in)\s+(?:the\s+)?(?:task|ticket|issue|tracker))",
    "destructive": r"delete\s+(?:the\s+|this\s+)?(?:task|issue|ticket)",
}
# A typed git/gh command counts only when it is the whole list item ("git push", "git commit
# это") — not when it is mentioned ("почему git push упал", "объясни gh pr create").
LITERAL = [(re.compile(r"git\s+commit\b"), "commit"), (re.compile(r"git\s+push\b"), "push"),
           (re.compile(r"git\s+(?:pull|reset)\b"), "history"),
           (re.compile(r"gh\s+pr\s+create\b"), "pr"), (re.compile(r"gh\s+pr\s+merge\b"), "merge"),
           (re.compile(r"gh\s+issue\s+create\b"), "create-task")]
LITERAL_ARGS = re.compile(r"(?:\s+(?:[!-~]+|это|все|всё|пожалуйста|плиз|плз))*")
SQUASH = re.compile(r"(?<![\w-])(?:засквошь|сквошни|схлопни|squash)(?![\w-])")
BARE = {"коммит": "commit", "commit": "commit", "пуш": "push", "push": "push", "pr": "pr",
        "пр": "pr", "pull request": "pr", "пулл реквест": "pr", "пул реквест": "pr",
        "пулреквест": "pr", "mr": "pr", "мр": "pr", "merge request": "pr"}
SEGMENT_STATUS = re.compile(r"^(?:\S+\s+)?(?:(?:probe|fix|full)\s+)?в\s+(?:ревью|review|работу)$")

TAGS = {"commit": "commit", "merge-local": "merge-local", "push": "push", "pr": "pr",
        "merge": "merge", "force-push": "force", "reset": "history",
        "delete-branch": "branch-delete",
        "status": "status", "create-task": "create-task", "tracker-edit": "tracker", "send": "send",
        "delete-task": "destructive", "delete-comment": "delete"}
# [reset] keeps granting a branch delete, as SKILL.md describes it ("deleting a branch")
TAG_ALSO = {"reset": {"branch-delete"}}
# [publish] and [repo-admin] grant the objects their option's label names; naming none grants nothing
LABEL_OBJECTS = {
    "publish": {
        "publish-release": r"(?<!\w)(?:релиз(?:а|ы|ов)?|releases?)(?![\w-])",
        "publish-repo": r"(?<!\w)(?<!в )(?<!во )(?<!in )(?<!to )" + REPO_WORD,
        "publish-gist": r"(?<!\w)(?:гист(?:а|ы)?|gists?)(?![\w-])"},
    "repo-admin": {
        "repo-admin-settings": r"видим|visib|публичн|(?<!\w)public(?!\w)|приватн|private|"
                               r"переимен|renam|архив|archiv",
        "repo-admin-secrets": r"секрет|secret|переменн|variable|deploy.?key|деплой.?ключ",
        "repo-admin-ci": r"workflow|воркфло|(?<!\w)ci(?!\w)|пайплайн|pipeline|джоб|(?<!\w)jobs?"
                         r"(?!\w)",
        "repo-admin-protection": r"защит|protect|ruleset",
        "repo-admin-delete": r"(?:удал|delet)\w*\s+(?:\S+\s+)?(?:релиз|release|гист|gist)"},
}
LABEL_OBJECTS = {tag: {a: re.compile(rx, re.I) for a, rx in objs.items()}
                 for tag, objs in LABEL_OBJECTS.items()}
TAG_RE = re.compile(r"\[(" + "|".join(re.escape(t) for t in sorted(
    list(TAGS) + list(LABEL_OBJECTS), key=len, reverse=True)) + r"|post)\]", re.I)
REPO_ADMIN = {"repo-admin-settings", "repo-admin-secrets", "repo-admin-ci",
              "repo-admin-protection", "repo-admin-delete"}
# create-task is not used up: one command covers the batch of tasks the dialog agreed on, which a
# tracker without a bulk operator creates one call per task
CONSUMED_BY_USE = {"commit", "merge-local", "push", "pr", "merge", "force", "publish-release",
                   "publish-repo", "publish-gist", "history", "branch-delete", "status", "tracker", "destructive",
                   "delete", "send"} | REPO_ADMIN


def normalize_text(s):
    return s.lower().replace("ё", "е")


def sentences(text):
    return [s for s in re.split(r"(?<=[.!?;])\s+|\n+", text) if s.strip()]


DETECT_EDGE = 4096  # detect() reads this much from each end of a long message
URL = re.compile(r"[a-z][a-z0-9+.-]{0,15}://\S{0,2048}")
FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,}).*?(?:^[ \t]*\1[ \t]*$|\Z)", re.M | re.S)


# a quotation, not an order: text in paired quotes after «говорит», «написано», "says" (single
# quotes too), or in double quotes after a colon («тикет говорит: «запушь»», `ticket: "push"`)
_QUOTES = r"(?:«[^«»\n]*(?:«[^«»\n]*»[^«»\n]*)*»|\"[^\"\n]*\"|“[^”\n]*”|„[^“”\n]*[“”])"
QUOTED = re.compile(r"(?<![\w'])(?:говор\w*|сказа\w*|написа\w*|пиш(?:ет|ут)|гласит|просит|"
                    r"says|said|say|writes|wrote|reads|states|asks)\s*:?\s*(?:" + _QUOTES +
                    r"|'[^'\n]*')|:\s*" + _QUOTES)


def typed_lines(text):
    """The text a user typed, without what they pasted: fenced blocks and `>` quoted lines."""
    if len(text) > 2 * DETECT_EDGE:  # a pasted log: its middle is not an order, and regexes
        text = text[:DETECT_EDGE] + "\n" + text[-DETECT_EDGE:]  # stay within the hook timeout
    text = FENCE.sub("\n", text)
    return "\n".join(ln for ln in text.split("\n") if not ln.lstrip().startswith(">"))


def detect(text):
    """Actions a typed message orders. Conservative: when in doubt, nothing."""
    text = PAIR.sub(r"\1, ", URL.sub(" ", QUOTED.sub(" ", normalize_text(typed_lines(text)))))
    found = set()
    for sent in sentences(text):
        sent = " ".join(sent.split())  # one space between words keeps SPLIT linear
        if COND.search(sent) or CANCEL.search(sent):
            continue
        sent = re.sub(r"(?<![\w'])потом(?=\s*(?:[,.;!?]|$))", " позже", sent)
        added, at_start, no_push = set(), set(found), False
        question = sent.rstrip().endswith("?")
        polite = bool(POLITE.search(sent))
        sent_negative = bool(NEG.search(REMIND.sub(" ", sent)) or DEFER.search(sent))
        for raw in SPLIT.split(sent.strip(" .!?;")):
            seg = raw.strip(" .!?;\"'«»()")
            if not seg:
                continue
            remind = bool(REMIND.search(seg))
            seg_clean = REMIND.sub(" ", seg)
            if NEG.search(seg_clean) or DEFER.search(seg_clean):
                if TAIL.fullmatch(LEAD.sub("", seg_clean.strip()).strip()):
                    found -= added  # «запушь, но не сейчас»: the tail cancels the item before it
                no_push = no_push or bool(re.search(r"пуш|push", seg_clean))
                added = set()
                continue
            before = set(found)
            core = LEAD.sub("", seg_clean.strip()).strip()
            for pattern, action in LITERAL:
                m = pattern.match(core)
                if m and not question and LITERAL_ARGS.fullmatch(core[m.end():]):
                    found.add(action)
                    args = core[m.end():].split()
                    if action == "push" and push_kind(args, look=False) == "force":
                        found.add("force")
            for action, pattern in RU_IMPERATIVE.items():
                if re.search(r"(?<![\w-])(?:" + pattern + r")(?![\w-])", core):
                    found.add(action)
            if polite or remind:
                for action, pattern in RU_INFINITIVE.items():
                    if re.search(r"(?<![\w-])(?:" + pattern + r")(?![\w-])", core):
                        found.add(action)
            if not question or polite:  # "commit?" asks; "could you commit it?" orders
                en = POLITE_LEAD.sub("", core) if polite else core
                for action, pattern in EN_IMPERATIVE.items():
                    if re.match(r"(?:" + pattern + r")", en):
                        found.add(action)
            if SEGMENT_STATUS.match(core) and not question:
                found.add("status")
            if core in BARE and not question and not sent_negative:
                found.add(BARE[core])
            if "commit" in found - before and SQUASH.search(core):
                found.add("history")  # squashing several commits needs reset --soft
            added = found - before
        if no_push:  # «залей в мейн, но не пушь»: the merge stays local
            found -= {"push", "force"} - at_start
    return found


# a task named in a message: a tracker link, a KEY-123 key, #123, or a tracker id such as
# 86c1x2y3z — not a PR, a commit, a code or a doc link, nor «PR #45»
TASK_REF = re.compile(r"(?<![\w-])(?i:(?:pr|пр|пулл?\w*|pull(?:\s+request)?|mr|мр|merge\s+request|коммит\w*|"
                      r"commit)\s*)?(?:[a-z][a-z0-9+.-]{0,15}://\S+|(?<![\w-])(?:[A-Z][A-Z0-9]{1,9}-"
                      r"\d+|#\d+|\d(?=[0-9a-z]*[a-z])[0-9a-z]{4,11})(?![\w-]))")
NOT_TASK_URL = re.compile(r"/(?:pulls?|merge_requests|commits?|compare|blob|tree|actions|runs|"
                          r"releases)(?:/|$)", re.I)


def status_refs(text):
    """The tasks a typed status command names: links and ids, as written."""
    refs = set()
    for m in TASK_REF.finditer(typed_lines(text or "")):
        ref = m.group(0)
        if re.match(r"(?i)(?:pr|пр|пул|pull|mr|мр|merge|коммит|commit)", ref) and \
                not re.match(r"(?i)[a-z][a-z0-9+.-]{0,15}://", ref):
            continue  # «PR #45», «коммит 3fa9c1d»
        ref = ref.rstrip(".,;:!?)»\"'")
        if "://" in ref and (NOT_TASK_URL.search(ref) or not (
                TRACKER_SERVER.search(ref) or TRACKER_PATH.search(ref) or
                re.search(r"/(?:browse|issues?|tasks?|t)/[\w-]+", ref))):
            continue  # a PR, a commit, a doc: no task
        refs.add(ref)
    return refs


def status_grants(text):
    """How many status changes a typed status command covers: one per task it names (a link or
    an id), at least one — «переведи 86abc1 и 86abc2 в ревью» is two; «переведи задачи в ревью»
    names none and is one, so the next task is asked about."""
    return max(1, len(status_refs(text)))


def ref_matches(ref, targets):
    """Whether a status call that names `targets` (its task ids) changes the task `ref` names:
    the same id, `#12` and 12, or an id that is a segment of the link."""
    r = ref.lower()
    parts = {p for p in re.split(r"[/?#&=]+", r) if p}
    for t in targets:
        t = str(t).lower().lstrip("#")
        if t and (t == r.lstrip("#") or t in parts):
            return True
    return False


def normalize_comment(s):
    s = s.replace("\r\n", "\n").strip()
    fence = re.match(r"^(`{3,}|~{3,})[^\n]*\n(.*)\n[ \t]*\1$", s, re.S)
    if fence:
        s = fence.group(2)
    lines = [ln.rstrip() for ln in s.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def split_multi(answer):
    """Picks of a multiSelect answer — the CLI joins them with ', ' and JSON-quotes any pick
    that contains ', ' or '"'."""
    items, i = [], 0
    while i < len(answer):
        if answer[i] == '"':
            try:
                value, end = json.JSONDecoder().raw_decode(answer, i)
                items.append(value)
                i = end
            except ValueError:
                items.append(answer[i:])
                break
        else:
            j = answer.find(", ", i)
            if j < 0:
                items.append(answer[i:])
                break
            items.append(answer[i:j])
            i = j
        if answer.startswith(", ", i):
            i += 2
    return items


# --- reading the transcript ---------------------------------------------------------------

def blocks_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def human_text(entry):
    if entry.get("type") == "attachment":
        text = blocks_text(entry["attachment"].get("prompt"))
    else:
        text = blocks_text(entry.get("message", {}).get("content"))
    if "<command-name>" in text:
        args = re.search(r"<command-args>(.*?)</command-args>", text, re.S)
        text = args.group(1) if args else ""
    return text


COMMIT_SKILL = re.compile(r"^/?(?:kensei-toolkit:)?commit$")
COMMIT_KEYS = {"plan", "local", "no-merge", "no-push", "pr", "main"}
NO_PUSH_ARGS = re.compile(r"(?<![\w-])(?:local(?:ly)?|no-push)(?![\w-])|без\s+пуш|локальн|"
                          r"только\s+(?:за)?комм?ит", re.I)
PUSH_WORD = re.compile(r"пуш|push", re.I)  # any word of a push: then only detect() grants it


def commit_command(text):
    """What the commit skill typed as a command orders: `/kensei-toolkit:commit` (or `/commit`)
    is «закоммить» + «запушь» — inside a /ticket run that skill hands both to the gate. Its
    leading argument words are keys: `pr` adds the PR, `main` the merge into the base branch
    and the push there, `local` / `no-push` leave the push out, `plan` orders nothing; the rest
    is a message hint, read as typed text. Any word of a push in the arguments («не пушить»,
    "push later") leaves the implicit push out, and a hint that holds something back («не
    вливай в main») keeps only the commit. -> the actions, or None when the text is not that
    command."""
    m = re.match(r"\s*(?:<command-message>[^<]*</command-message>\s*)?"
                 r"<command-name>\s*(/[^<\s]*)\s*</command-name>", text or "")
    if m:
        args = re.search(r"<command-args>(.*?)</command-args>", text, re.S)
        name, args = m.group(1), args.group(1) if args else ""
    else:  # a queued message keeps the typed form: `/kensei-toolkit:commit pr`
        m = re.match(r"\s*(/\S+)(.*)\Z", text or "", re.S)
        if not m:
            return None
        name, args = m.group(1), m.group(2)
    if not COMMIT_SKILL.match(name):
        return None
    words = normalize_text(args).split()
    keys = set()
    for w in words:
        if w not in COMMIT_KEYS:
            break
        keys.add(w)
    if "plan" in keys:
        return set()
    grants = {"commit"} | detect(args)
    if revokes(args):
        return grants
    if not NO_PUSH_ARGS.search(args) and not PUSH_WORD.search(args):
        grants.add("push")
    if "pr" in keys:
        grants.add("pr")
    if "main" in keys:  # the push to main ("force") only with the push: `main local` is local
        grants |= {"merge-local", "force"} if "push" in grants else {"merge-local"}
    return grants


def raw_text(entry):
    if entry.get("type") == "attachment":
        return blocks_text(entry["attachment"].get("prompt"))
    return blocks_text(entry.get("message", {}).get("content"))


def is_human(entry):
    if entry.get("isSidechain") or entry.get("isMeta"):
        return False
    if entry.get("type") == "user":
        origin = entry.get("origin")
        return isinstance(origin, dict) and origin.get("kind") == "human"
    if entry.get("type") == "attachment":
        att = entry.get("attachment") or {}
        origin = att.get("origin")
        return (att.get("type") == "queued_command" and att.get("commandMode", "prompt") == "prompt"
                and isinstance(origin, dict) and origin.get("kind") == "human")
    return False


def is_queued(entry):
    return entry.get("type") == "attachment"


MAX_QUEUED = 8


def scan_back(transcript_path, max_bytes=64 << 20):
    """Return (human_entry or None, entries after it in order, saw_origin, truncated).

    A message the user queued while the model was busy does not end the scan: the scan goes on to
    the message typed before it, and the queued ones stay in the entries, where they were read."""
    after, saw_origin, human, queued = [], False, None, 0
    with open(transcript_path, "rb") as f:
        f.seek(0, 2)
        pos = size = f.tell()
        tail = b""
        while pos > 0 and size - pos < max_bytes and human is None:
            step = min(1 << 20, pos)
            pos -= step
            f.seek(pos)
            lines = (f.read(step) + tail).split(b"\n")
            tail = lines[0] if pos > 0 else b""
            for raw in reversed(lines if pos == 0 else lines[1:]):
                if not raw.strip():
                    continue
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue
                if "origin" in entry or "origin" in (entry.get("attachment") or {}):
                    saw_origin = True
                if is_human(entry):
                    if is_queued(entry) and queued < MAX_QUEUED:
                        queued += 1
                        after.append(entry)
                        continue
                    human = entry
                    break
                if entry.get("type") in {"assistant", "user"} and not entry.get("isSidechain"):
                    after.append(entry)
    after.reverse()
    if human is None and queued and pos == 0:  # the transcript starts with a queued message
        first = next(i for i, e in enumerate(after) if is_queued(e))
        human, after = after[first], after[first + 1:]
    return human, after, saw_origin, human is None and pos > 0


STOP = re.compile(r"(?<![\w'])(?:подожди\w*|погоди\w*|стой(?:те)?|wait|hold|отставить|halt)"
                  r"(?![\w'])")


BARE_STOP = re.compile(r"(?:стоп|стой(?:те)?|нет|не\s+надо|не\s+нужно|no|nope|stop|halt|отмена|"
                       r"отбой|don'?t|do\s+not|"
                       r"cancel|отставить|hold(?:\s+on)?|wait|подожди\w*|погоди\w*)")
NO_LEAD = re.compile(r"^(?:(?:no|nope|нет|не|ой|oh|oops)[,\s]+)+")
NONE = re.compile(r"(?<![\w'])(?:никак\w*|ничего|nothing|none|no\s+more)(?![\w'])")
GATED_WORDS = re.compile(r"(?:комм?ит|commit|мерд?ж|merge|пуш|push|зал[еи]|вле|форс|force|\bpr\b|"
                         r"\bпр\b|пулл|pull|реквест|request|статус|status|ревью|review|сброс|"
                         r"reset|подтян|стэш|стеш|stash|откат|revert|restore|clean|удали|delete|"
                         r"коммент|comment|пост|post|опубликуй|publish|задач|task|ветк|branch|"
                         r"main|master|мейн|мастер)")


# a change of plan, whatever it names: the user takes the step over, wants something done or
# shown first, or changed their mind
REPLAN = re.compile(r"(?<![\w'])(?:передумал\w*|раздумал\w*|я\s+(?:\w+\s+){0,2}сам[аи]?|"
                    r"сначала|сперва|для\s+начала|"
                    r"перед\s+(?:коммит\w*|пуш\w*|мерд?ж\w*|этим|тем)|"
                    r"дай(?:те)?\s+(?:\S+\s+)?(?:посмотреть|глянуть|взглянуть|проверить|"
                    r"почитать)|дай(?:те)?\s+(?:я\s+)?(?:гляну|посмотрю|проверю|взгляну|почитаю)|"
                    r"давай(?:те)?\s+я\s+\S*(?:у|ю)|i'?ll\s+do\s+it|i\s+will\s+do\s+it|"
                    r"не\s+надо|first|myself|changed\s+my\s+mind|never\s?mind|"
                    r"let\s+me\s+(?:see|look|check|review|do)|hold\s+off)(?![\w'])")
# «сам закоммичу», «сам всё запушу», «сам разберусь», «закоммичу сам» — first person by the
# verb's -у/-ю(сь), which a noun shares («сделай всю работу сам»): a clause that also addresses
# the model (an imperative, «ты», «вы») is the model told to do it alone, as are «сделай сам» /
# «давай сам» / «пушни ветку сам», and holds nothing back
SELF = re.compile(r"(?<![\w'])(?:сам[аи]?\s+(?:\S+\s+){0,2}\S*(?:у|ю)(?:сь)?|"
                  r"\w+(?:у|ю)(?:сь)?\s+(?:\w+\s+)?сам[аи]?)(?![\w'])")
# an imperative: -ай/-яй/-уй, -лей (залей), -[шжт]ни (пушни, мержни), -ите, -ь except -усь/-юсь
# (разберусь) and the words listed; and their -те forms
YOU = re.compile(r"(?<![\w'])(?:ты|вы|\w+(?:[аяу]й|[шжт]ни)(?:те)?|\w*лей(?:те)?|"
                 r"(?!(?:теперь|опять|чуть|день|путь|ветвь|здесь|весь|ведь|вновь|лишь|пусть|"
                 r"мой|твой|свой)(?![\w'])|\w*[ую]сь(?![\w']))\w+ь(?:те)?|\w+ите)(?![\w'])")


def takes_over(text):
    """The user says they do the step themselves (SELF, in a clause that addresses no one)."""
    for clause in re.split(r"[,.;!?…:()]|\s(?:а|но|и)\s", text):
        if SELF.search(clause) and not YOU.search(clause):
            return True
    return False


def revokes(text):
    """A queued message that holds back a command: a list item that negates, cancels or defers
    a gated action («не коммить пока», «пуш позже»), a bare «не сейчас» / «позже», one that is
    or starts with a stop word («стоп», «подожди», wait), or a change of plan whatever it names
    (REPLAN: «я передумал», «сначала покажи дифф», "first show me the diff", «дай посмотреть»,
    «не надо»; takes_over: «сам закоммичу», but not «сделай сам»). «тесты не трогай» holds back
    nothing the guard gates."""
    t = " ".join(PAIR.sub(r"\1, ", URL.sub(" ", normalize_text(typed_lines(text)))).split())
    if REPLAN.search(t) or takes_over(t):
        return True
    for sent in sentences(t):
        for raw in SPLIT.split(sent.strip(" .!?;")):
            seg = raw.strip(" .!?;\"'«»()")
            core = LEAD.sub("", seg).strip()
            if not core:
                continue
            led = NO_LEAD.sub("", core)  # "no wait", «нет, стой»
            if BARE_STOP.fullmatch(core) or TAIL.fullmatch(core) or STOP.match(core) or \
                    led != core and (BARE_STOP.fullmatch(led) or STOP.match(led)):
                return True
            seg_clean = REMIND.sub(" ", seg)
            if (NEG.search(seg_clean) or NONE.search(seg) or CANCEL.search(seg)
                    or DEFER.search(seg)) and \
                    GATED_WORDS.search(seg):
                return True
    return False


# a call a hook or the permission flow stopped before it ran
NOT_RUN = re.compile(r"\[ticket guard\] Blocked|^PreToolUse:|hook error|"
                     r"Permission to use \S+ (?:has been|was) denied", re.I | re.M)
# an MCP server that plainly refused the call: nothing was written. Read at the head of the
# error only, where the code and the server's verdict stand — an error that echoes the request
# ("crash when asset not found") or a timeout "after 400 ms" is not a refusal
REFUSED = re.compile(
    r"^(?:<tool_use_error>\s*)?(?:(?:MCP\s+)?error\b[^:\n]{0,40}:\s*)?(?:"
    r"InputValidationError|validation\s+(?:error|failed)|invalid\s+(?:argument|param|input|"
    r"request|arguments|parameters)|bad\s+request|unauthori[sz]ed|forbidden|"
    r"(?:HTTP\s*|status(?:\s+code)?:?\s*)?4(?:00|01|03|04|05|09|10|22)\b|"
    r"(?:\S+\s+){0,2}(?:not\s+found|does\s+not\s+exist|is\s+required)\b)"
    r"|^(?:<tool_use_error>\s*)?MCP\s+error\s+-32602\b|InputValidationError|"
    r"\b(?:HTTP|status(?:\s+code)?:?)\s*4(?:00|01|03|04|05|09|10|22)\b", re.I)


def executed(block, call=None):
    """Whether a tool result means the call may have done its work. A call the guard denied and
    one the user rejected or interrupted did not. A shell command that failed (`is_error`: a
    non-zero exit, a rejected push, a hook that refused the commit) did not when its failure
    must be the gated part's own (see `error_is_own`). An MCP call that failed did only if the
    server did not plainly refuse it: a timeout or an unclear error may have landed the write."""
    text = result_text(block).lstrip()
    if text.startswith(("The user doesn't want to proceed", "[Request interrupted by user")):
        return False
    if not block.get("is_error"):
        return True
    if NOT_RUN.search(text[:2000]):
        return False
    call = call or {}
    if call.get("name") in {"Bash", "Monitor"}:
        return not error_is_own((call.get("input") or {}).get("command") or "")
    if call.get("name") == "PowerShell":
        return True  # its failures are not read: a failed PowerShell call used its command up
    if str(call.get("name", "")).startswith("mcp__"):
        return not REFUSED.search(text.split("\n", 1)[0][:300])
    return False


def error_is_own(command):
    """Whether a failed shell command's exit status can only come from a step that did nothing:
    one simple command, or an `&&` chain whose gated step is the last one (redirects allowed). In `git push; false`
    or `git push && npm test` the push may have gone through before the failure."""
    command, fed, _ = strip_heredocs(command)  # `git commit -F - <<EOF` is one command
    if fed:
        return False
    subst = re.compile(r"\$\(([^()`]*)\)|`([^`]*)`")
    while subst.search(command):  # `-m "$(cat <<EOF …)"`: a substitution that gates nothing
        m = subst.search(command)
        if classify_shell(m.group(1) if m.group(1) is not None else m.group(2), None):
            return False
        command = command[:m.start()] + "x" + command[m.end():]
    if "`" in command or "$(" in command or "\n" in command.strip():
        return False
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return False
    segments, segment = [], []
    for token in tokens:
        if token and set(token) <= set("<>&") and set(token) & set("<>"):
            continue  # a redirect (`2>&1`, `> log`) keeps the exit status
        if token and set(token) <= set(";&|()<>"):
            if token != "&&":
                return False  # `;`, `||`, a pipe, a background job or a subshell
            segments.append(segment)
            segment = []
        else:
            segment.append(token)
    if not segment:
        return False
    return not any(classify_shell(shlex.join(seg), None) for seg in segments)


def unprefix(text):
    return "\n".join(re.sub(r"^(?:[ \t]*>)*[ \t]*", "", ln) for ln in text.split("\n"))


def result_text(block):
    content = block.get("content")
    return blocks_text(content) if isinstance(content, list) else str(content or "")


def read_authorization(transcript_path, cwd):
    """Replay what happened since the user's latest message.

    Returns (human_text, actions, approved_comments, used_comments, unseen_comments, failure)."""
    if not transcript_path or not os.path.isfile(transcript_path):
        return None, set(), set(), set(), set(), "no transcript to read the user's command from"
    human, after, saw_origin, truncated = scan_back(transcript_path)
    if human is None:
        if truncated:
            failure = "the user's latest message is beyond the guard's scan window"
        elif not saw_origin:
            failure = ("no message typed by the user was found — a headless or SDK prompt, or a "
                       "transcript format this guard does not know")
        else:
            failure = "no message typed by the user was found"
        if not after:
            return None, set(), set(), set(), set(), failure
    text = human_text(human) if human else None
    typed = detect(text) if text else set()
    ordered = commit_command(raw_text(human)) if human else None
    if ordered is not None:
        typed = ordered
    actions = set(typed)
    # the push a model-invoked commit skill adds, once per command; a typed commit command
    # (/kensei-toolkit:commit) has settled the push itself
    skill_push = ordered is not None
    # «не пушь», «пуш потом», «только закоммить», «локально»: no skill push
    said_push = bool(text and (PUSH_WORD.search(text) or NO_PUSH_ARGS.search(text)))
    approved, used, unseen = set(), set(), set()
    # status grants: the tasks a typed status command named, each changed once; or, when it
    # named none (or an option carried [status]), one change of any task
    refs, open_status = set(), False
    if "status" in typed:
        refs = status_refs(text)
        open_status = not refs
    shown = unprefix(normalize_comment("\n".join(
        blocks_text(e["message"].get("content")) for e in after if e.get("type") == "assistant")))
    calls, replay = {}, {"replay": True}
    for entry in after:
        if is_queued(entry):  # queued mid-turn: adds its orders, or holds everything back
            text = queued_text = human_text(entry)
            said_push = said_push or bool(PUSH_WORD.search(queued_text)
                                          or NO_PUSH_ARGS.search(queued_text))
            if revokes(queued_text):  # it voids every command so far and grants nothing itself
                actions, approved, typed, refs, open_status = set(), set(), set(), set(), False
                continue
            granted = commit_command(raw_text(entry))
            if granted is None:
                granted = detect(queued_text)
            else:
                skill_push = True
            if "status" in granted:  # adds to what the earlier message named
                named = status_refs(queued_text)
                refs |= named
                open_status = open_status or not named
            actions |= granted
            typed |= granted
            continue
        content = entry.get("message", {}).get("content")
        if entry.get("type") == "assistant" and isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    calls[b.get("id")] = b
                    inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                    if b.get("name") == "Skill" and re.search(
                            r"(?:^|:)ticket$", str(inp.get("skill", ""))):
                        actions -= typed  # the model invoked /ticket: earlier typing is void
                        if "status" in typed:
                            refs, open_status = set(), False
                        typed = set()
                    elif b.get("name") == "Skill" and str(inp.get("skill", "")) == \
                            "kensei-toolkit:commit" and "commit" in typed and not skill_push \
                            and not said_push \
                            and not NO_PUSH_ARGS.search(str(inp.get("args") or "")):
                        # the model took the user's typed «закоммить» to the commit skill, which
                        # is commit + push: the push joins the command (the skill's own
                        # arguments, chosen by the model, add nothing else)
                        skill_push = True
                        actions.add("push")
                        typed = typed | {"push"}
        elif entry.get("type") == "user" and isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    call = calls.get(b.get("tool_use_id"))
                    if not call or not executed(b, call):
                        continue
                    for item in classify(call.get("name", ""), call.get("input") or {}, cwd,
                                         state=replay):
                        if item[0] == "status":
                            hit = next((r for r in sorted(refs) if ref_matches(
                                r, item[2] if len(item) > 2 else [])), None)
                            if hit:
                                refs.discard(hit)  # that named task is done
                            else:
                                open_status = False
                        elif item[0] in CONSUMED_BY_USE:
                            actions.discard(item[0])
                        elif item[0] == "base-sync" and "merge-local" not in actions:
                            actions.discard("commit")  # under merge-local it is part of the merge
                        elif item[0] == "comment":
                            for t in comment_candidates(item):
                                used.add(normalize_comment(t))
                                approved.discard(normalize_comment(t))
            answer = entry.get("toolUseResult")
            if isinstance(answer, dict) and isinstance(answer.get("answers"), dict):
                granted, ok, missed = read_answers(answer, shown)
                open_status = open_status or "status" in granted
                actions |= granted
                approved |= ok
                unseen |= missed
    global STATUS_LEFT
    STATUS_LEFT = (frozenset(refs), open_status)
    actions.discard("status")
    if refs or open_status:
        actions.add("status")
    return text, actions, approved, used, unseen, (failure if human is None else None)


STATUS_LEFT = None  # (task refs a status command named and not yet changed, one open change)


def read_answers(result, shown):
    if result.get("afkTimeoutMs") or result.get("followUp"):
        return set(), set(), set()
    granted, approved, unseen = set(), set(), set()
    questions = {q.get("question"): q for q in result.get("questions") or [] if isinstance(q, dict)}
    annotations = result.get("annotations") if isinstance(result.get("annotations"), dict) else {}
    for question, answer in result["answers"].items():
        if not isinstance(answer, str):
            continue
        q = questions.get(question) or {}
        options = {o.get("label"): o for o in q.get("options") or [] if isinstance(o, dict)}
        note = annotations.get(question) if isinstance(annotations.get(question), dict) else {}
        notes = note.get("notes")
        if isinstance(notes, str) and notes.strip():
            granted |= detect(notes)  # a pick with reservations grants nothing by itself
            continue
        picks = [answer] if answer in options or not q.get("multiSelect") else split_multi(answer)
        for pick in picks:
            option = options.get(pick)
            if option is None:
                granted |= detect(pick)  # free text the user typed ("Other")
                continue
            for tag in TAG_RE.findall(pick):
                tag = tag.lower()
                if tag in TAGS:
                    granted.add(TAGS[tag])
                    granted |= TAG_ALSO.get(tag, set())
                    continue
                if tag in LABEL_OBJECTS:
                    granted |= {a for a, rx in LABEL_OBJECTS[tag].items()
                                if rx.search(TAG_RE.sub(" ", pick))}
                    continue
                preview = option.get("preview")
                if not isinstance(preview, str) or not preview.strip():
                    continue
                norm = normalize_comment(preview)
                previews = [normalize_comment(o.get("preview") or "") for o in options.values()]
                seen = note.get("preview")
                displayed = (isinstance(seen, str) and normalize_comment(seen) == norm) \
                    or (bool(norm) and unprefix(norm) in shown)
                if previews.count(norm) == 1:
                    (approved if displayed else unseen).add(norm)
    return granted, approved, unseen


# --- what the tool call would do ----------------------------------------------------------

def strip_heredocs(command):
    """Remove heredoc bodies; return (command, bodies fed to a shell, all bodies)."""
    lines, out, fed, bodies = command.split("\n"), [], [], []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        for m in re.finditer(r"<<(?!<)-?\s*(['\"]?)([^\s'\";&|()<>]+)\1", line):
            body, start = [], i
            while i < len(lines) and lines[i].strip() != m.group(2):
                body.append(lines[i])
                i += 1
            if i >= len(lines):  # never closed: keep the lines, they are still commands
                out.extend(lines[start:])
                break
            i += 1
            bodies.append("\n".join(body))
            before = re.split(r"[;&|(]", line[:m.start()])[-1].split()
            words = [os.path.basename(w).lower() for w in before if not re.match(r"^\w+=", w)]
            if words and words[0] in SHELLS and not any(
                    re.fullmatch(r"-[a-z]*c[a-z]*", w) for w in words[1:]):
                fed.append(bodies[-1])
    return "\n".join(out), fed, bodies


def split_segments(command):
    lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=";&|()<>")
    lexer.whitespace_split = True
    lexer.commenters = ""
    segment, segments = [], []
    for token in lexer:
        if token and set(token) <= set(";&|()<>"):
            if segment:
                segments.append(segment)
            segment = []
        else:
            segment.append(token)
    if segment:
        segments.append(segment)
    return segments


def raw_scan(command, cwd, depth, gh_env=None):
    """Conservative fallback: every git/gh word anywhere, parsed up to the next separator."""
    found = []
    # `--body "$(cat <file>)"` stays one word: the PR body check reads it as that file
    command = re.sub(r"[\"']?\$\(\s*cat\s+(?:--\s+)?['\"]?([^\s'\"()$`;&|<>]+)['\"]?\s*\)[\"']?",
                     lambda m: "\0cat:" + m.group(1), command)
    for m in GITISH.finditer(command):
        rest = re.split(r"[;&|\n)`]", command[m.end():], 1)[0]
        args = rest.replace("'", " ").replace('"', " ").split()
        if m.group(1).lower() == "git":
            found += classify_git(args, cwd, depth + 1, {})
        else:
            found += classify_gh(args, cwd, env=gh_env)
    return found


_GIT_CACHE = {}


def git_query(args, cwd, dash_c):
    key = (tuple(args), cwd, dash_c)
    if key not in _GIT_CACHE:
        cmd = ["git"] + (["-C", dash_c] if dash_c else []) + list(args)
        try:
            out = subprocess.run(cmd, cwd=cwd or None, capture_output=True, text=True, timeout=2)
            _GIT_CACHE[key] = out.stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            _GIT_CACHE[key] = None
    return _GIT_CACHE[key]


def git_alias(name, cwd, dash_c, inline):
    if name in inline:
        return inline[name]
    return git_query(["config", "--get", f"alias.{name}"], cwd, dash_c)


# short options that take a value, per git subcommand: in a bundle, the rest after one of these
# letters is its value (`push -on` is push-option "n", not a dry run)
SHORT_WITH_VALUE = {
    "push": "o", "clean": "e", "commit": "mFcCtS", "merge": "mFsXS", "rebase": "sXSx",
    "cherry-pick": "mXS", "revert": "mXS", "checkout": "bB", "switch": "cC", "branch": "u",
    "restore": "s", "stash": "m", "tag": "mFu", "am": "pSC",
}


def expand_flags(args, sub=None):
    """`-fu` → `-f`, `-u`: git accepts bundled short flags. The bundle ends at a letter that
    takes a value (SHORT_WITH_VALUE[sub]); the rest of the bundle is that value."""
    valued, out = SHORT_WITH_VALUE.get(sub, ""), []
    for a in args:
        if re.fullmatch(r"-[A-Za-z]{2,}", a):
            for i, c in enumerate(a[1:], 2):
                out.append("-" + c)
                if c in valued:
                    out.append(a[i:])  # the value as its own word, as `-o n` would give
                    break
        else:
            out.append(a)
    return out


PUSH_OPTS_WITH_VALUE = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}


def push_kind(rest, cwd=None, dash_c=None, look=True, branch=None):
    """"push", "force" (rewrites or targets a base branch) or None (a dry run). `branch`: the
    branch an earlier `git switch` of the same command moved to."""
    rest = expand_flags(rest, "push")
    flags = [a for a in rest if a.startswith("-")]
    if {"--dry-run", "-n"} & set(flags):
        return None
    if any(f in {"-f", "--force", "--delete", "-d", "--mirror", "--prune", "--all", "--branches",
                 "--tags"}
           or f.startswith(("--force-with-lease", "--force-if-includes")) for f in flags):
        return "force"
    positional, skip = [], False
    for a in rest:
        if skip:
            skip = False
        elif a in PUSH_OPTS_WITH_VALUE:
            skip = True
        elif not a.startswith("-"):
            positional.append(a)
    repo_opt = option_value(rest, ["--repo"])
    if repo_opt and not positional:
        positional = [repo_opt]
    bases = base_set(cwd, dash_c) if look else BASE_BRANCHES
    for spec in positional[1:]:
        dst = spec.split(":", 1)[-1]
        if spec.startswith(("+", ":")) or dst.replace("refs/heads/", "") in bases:
            return "force"
    if not look:
        return "push"
    head = branch or git_query(["symbolic-ref", "-q", "--short", "HEAD"], cwd, dash_c)
    if all(p in {"HEAD", "@"} for p in positional[1:]) and head in bases:
        return "force"  # no destination named and the checkout is on the base branch
    if any(names_base(d, bases) or d == "*" for d in configured_destinations(
            positional[:1], positional[1:], head, cwd, dash_c)):
        return "force"  # the repository's own settings send this push to a base branch
    return "push"


def git_config_all(key, cwd, dash_c):
    out = git_query(["config", "--get-all", key], cwd, dash_c)
    return out.split("\n") if out else []


def refspec_map(src, spec, head=None):
    """Where the push refspec `spec` (`refs/heads/*:refs/heads/*`, `HEAD:main`) sends `src`, or
    None when it does not match it. `HEAD` on the left is the branch `head`."""
    left, _, right = spec.lstrip("+").partition(":")
    right = right or left
    full = src if src.startswith("refs/") else "refs/heads/" + src
    if "*" in left:
        pre, _, post = left.partition("*")
        if full.startswith(pre) and full.endswith(post) and len(full) >= len(pre) + len(post):
            return right.replace("*", full[len(pre):len(full) - len(post) or None], 1)
        return None
    if left in {"HEAD", "@"}:
        return (src if right in {"HEAD", "@"} else right) if src == head else None
    if left in {src, full}:
        return right
    return None


def configured_destinations(remote, refspecs, head, cwd, dash_c):
    """The branches a push without an explicit destination updates under the repository's own
    settings: remote.<r>.push (any refspec, `+` and wildcards that reach other branches count as
    "*"), else push.default with branch.<b>.merge — `upstream`, `simple`, `tracking` send the
    branch to its upstream, `matching` to every branch of the same name ("*"). A refspec named
    without `:<dst>` is mapped through remote.<r>.push the same way."""
    if not head:
        return set()
    remote = remote[0] if remote else (
        git_query(["config", "--get", f"branch.{head}.pushRemote"], cwd, dash_c) or
        git_query(["config", "--get", "remote.pushDefault"], cwd, dash_c) or
        git_query(["config", "--get", f"branch.{head}.remote"], cwd, dash_c) or "origin")
    mapped = git_config_all(f"remote.{remote}.push", cwd, dash_c) if \
        re.fullmatch(r"[\w./-]+", remote) and "://" not in remote else []
    out = set()
    srcs = [s.split(":", 1)[0] for s in refspecs if ":" not in s] or ([] if refspecs else [head])
    if mapped:
        for spec in mapped:
            if spec.startswith("+") or spec.startswith(":") or (
                    "*" in spec and not refspecs):
                out.add("*")  # a forced, deleting or all-branches refspec
            for src in srcs:
                dst = refspec_map(head if src in {"HEAD", "@"} else src, spec, head)
                if dst:
                    out.add(re.sub(r"^refs/heads/", "", dst))
        return out
    if refspecs:
        return out
    mode = (git_query(["config", "--get", "push.default"], cwd, dash_c) or "simple").lower()
    if mode == "matching":
        return {"*"}
    if mode in {"upstream", "tracking", "simple"}:
        merge = git_query(["config", "--get", f"branch.{head}.merge"], cwd, dash_c)
        if merge:
            out.add(re.sub(r"^refs/heads/", "", merge))
    return out


def default_branch(cwd, dash_c):
    out = git_query(["symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"], cwd, dash_c)
    return out.split("/", 1)[-1] if out else None


def base_set(cwd, dash_c):
    """Branch names that count as a base branch here: the usual names, the remote's default
    branch and the `base_branch:` of this session's run."""
    return BASE_BRANCHES | run_base_branches() | ({default_branch(cwd, dash_c)} - {None})


def names_base(ref, bases):
    """`main`, `origin/main`, `refs/heads/main`, `refs/remotes/upstream/main` name a base."""
    ref = re.sub(r"^refs/(?:heads|remotes)/", "", ref)
    return ref in bases or "/" in ref and ref.split("/", 1)[1] in bases


RUN_BASE = re.compile(r"^[ \t>*-]*`?base[_ ]branch`?\s*:\s*`?([^\s`]+)", re.M | re.I)


def run_base_branches():
    """The `base_branch:` in this session's RUN.md (RUN_DIR). No run recorded — none: another
    task's RUN.md in the same repository says nothing about this one."""
    if not RUN_DIR:
        return set()
    try:
        with open(os.path.join(RUN_DIR, "RUN.md"), encoding="utf-8", errors="replace") as f:
            head = f.read(8192)
    except OSError:
        return set()
    found = set()
    for m in RUN_BASE.finditer(head):
        branch = m.group(1).strip("`'\"").replace("refs/heads/", "")
        if branch and branch not in {"HEAD", "(detached)"}:
            found.add(branch)
    return found


# settings that decide where a push goes or what a git word runs — writing one is tampering, and a
# one-off `git -c` of the push ones is opaque
CONFIG_TAMPER = re.compile(
    r"^(?:remote\.(?:.+\.(?:push|url|pushurl|mirror)|pushdefault)|branch\..+\.(?:merge|remote|"
    r"pushremote)|alias\..+|core\.hookspath|include\..+|includeif\..+|"
    r"url\..+\.(?:insteadof|pushinsteadof)|push\.default)$")
CONFIG_SECTION = re.compile(r"^(?:remote|branch|alias|include|includeif|url|push)(?:\.|$)")
PUSH_CONFIG = re.compile(r"^(?:remote\.|branch\.|url\.|push\.default$)")
# settings that run a program, redefine a git word or redirect a remote: a one-off `git -c` of
# one is opaque whatever the subcommand (`git -c core.editor='sh -c "git push"' commit`)
RUNS_CONFIG = re.compile(
    r"^(?:core\.(?:editor|sshcommand|fsmonitor|hookspath|pager|askpass|gitproxy|"
    r"alternaterefscommand)|sequence\.editor|alias\..+|remote\..+|url\..+|include\..+|"
    r"includeif\..+|credential\..*helper|gpg\.(?:.+\.)?program|diff\.(?:external|.+\.(?:command|"
    r"textconv))|(?:diff|merge)tool\..+\.(?:cmd|path)|merge\..+\.driver|filter\..+|"
    r"pager\..+|uploadpack\..+|receivepack\..+|protocol\..+|ssh\.variant|"
    r"submodule\..+\.update|interactive\.difffilter|web\.browser|browser\..+\.(?:cmd|path)|"
    r"sendemail\..+|man\..+\.(?:cmd|path))$")
# subcommands that push, or run git commands that inherit `-c` (submodule foreach)
PUSHING_SUBS = {"push", "send-pack", "http-push", "subtree", "lfs", "submodule"}
CONFIG_OPTS_WITH_VALUE = {"-f", "--file", "--blob", "--type", "--default", "--comment", "--value",
                          "-t"}
CONFIG_READ = {"--get", "--get-all", "--get-regexp", "--get-urlmatch", "-l", "--list",
               "--get-color", "--get-colorbool", "--show-origin", "--show-scope"}
CONFIG_WRITE = {"--add", "--unset", "--unset-all", "--replace-all", "--rename-section",
                "--remove-section", "-e", "--edit"}


def config_tamper(args):
    """`git config` writing a setting that redirects a push or redefines a git word
    (remote.*.push/url, branch.*.merge/remote, alias.*, core.hooksPath, include.*, url.*.insteadOf,
    push.default), or `git remote add` / `set-url` / `rename` / `set-head`. Reads pass."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in GIT_OPTS_WITH_VALUE else 1
    if i >= len(args):
        return False
    sub, rest = args[i], args[i + 1:]
    if sub == "remote":
        return bool(rest) and rest[0] in {"set-url", "rename", "add", "set-head", "set-branches"}
    if sub != "config":
        return False
    write, positional, skip = False, [], False
    if rest and rest[0] in {"set", "unset", "rename-section", "remove-section", "edit"}:
        write, rest = True, rest[1:]
        if not rest or rest[0] == "edit":
            return True
    elif rest and rest[0] in {"get", "list", "get-color", "get-colorbool"}:
        return False
    for a in rest:
        if skip:
            skip = False
        elif a.split("=", 1)[0] in CONFIG_OPTS_WITH_VALUE and "=" not in a:
            skip = True
        elif a in {"-e", "--edit"}:
            return True
        elif a in CONFIG_READ:
            return False
        elif a in CONFIG_WRITE:
            write = True
        elif not a.startswith("-"):
            positional.append(a)
    if not positional or not (write or len(positional) >= 2):
        return False
    key = positional[0].lower()
    return bool(CONFIG_TAMPER.match(key) or RUNS_CONFIG.match(key) or CONFIG_SECTION.match(key) and
                ({"--rename-section", "--remove-section"} & set(rest) or
                 args[i + 1:i + 2] in (["rename-section"], ["remove-section"])))


def git_ref(arg, cwd, dash_c):
    return bool(git_query(["rev-parse", "-q", "--verify", "--end-of-options", arg + "^{commit}"],
                          cwd, dash_c))


def classify_git(args, cwd, depth, inline, state=None):
    """What `git <args>` does, and opaque when it writes files at a path the guard cannot read
    (git_file_writes)."""
    found = classify_git_words(args, cwd, depth, inline, state)
    unread = git_file_writes(args, cwd)[1]
    if unread:
        found.append(("opaque", f"`git {unread[:80]}`: files written at a path the guard cannot "
                                "read"))
    return found


def classify_git_words(args, cwd, depth, inline, state=None):
    """What `git <args>` does. `state` carries the branch an earlier `git switch` / `git
    checkout` of the same command moved to."""
    state = {} if state is None else state
    dash_c, push_config, runs_config, i = None, None, None, 0
    while i < len(args) and args[i].startswith("-"):
        opt = args[i].split("=", 1)[0]
        if opt in GIT_OPTS_WITH_VALUE:
            value = args[i].split("=", 1)[1] if "=" in args[i] and opt.startswith("--") else (
                args[i + 1] if i + 1 < len(args) else "")
            if opt == "-C":
                dash_c = value
            if opt == "--exec-path" and "=" in args[i]:
                runs_config = args[i]  # git's own programs taken from another directory
            if opt in {"-c", "--config-env"}:
                key, _, setting = value.partition("=")
                key = key.lower()
                if RUNS_CONFIG.match(key) and not (opt == "-c" and benign_program(key, setting)):
                    runs_config = value
                elif PUSH_CONFIG.match(key):
                    push_config = value
            i += 1 if "=" in args[i] and opt.startswith("--") else 2
        else:
            i += 1
    if i >= len(args):
        return []
    sub, rest = args[i], args[i + 1:]
    if runs_config:
        # a one-off setting that runs a program, redefines a git word or redirects a remote
        return [("opaque", f"`git {'-c ' * (not runs_config.startswith('--'))}{runs_config[:60]} "
                           f"{sub}`: a one-off setting that runs a program, defines an alias or "
                           "changes a remote")]
    if push_config and (sub not in GIT_BUILTINS or sub in PUSHING_SUBS):
        # a one-off setting that changes where a push goes; a fetch or a switch it leaves alone
        return [("opaque", f"`git -c {push_config[:60]} {sub}`: a one-off setting that changes "
                           "where a push goes")]
    if "$" in sub or "`" in sub:
        return [("opaque", f"`git {sub} …`: a git subcommand built from a variable or a "
                           "substitution")]
    if sub not in GIT_BUILTINS and depth < 3:
        alias = git_alias(sub, cwd, dash_c, inline)
        if alias:
            if alias.startswith("!"):
                return classify_shell(alias[1:] + " " + " ".join(shlex.quote(a) for a in rest),
                                      cwd, depth + 1)
            return classify_git(shlex.split(alias) + rest, cwd, depth + 1, inline, state)
    carried = carried_commands(sub, rest)
    callback = next((a for a in rest if sub == "filter-repo" and re.match(
        r"^--\w[\w-]*-callback(?:=|$)", a)), None)
    if callback:  # Python run for each commit, blob or ref: it may call git or gh itself
        return [("opaque", f"`git filter-repo {callback[:40]} …` runs code of its own for each "
                           "commit, blob or ref")]
    if any(classify_shell(c, cwd, depth + 1) for c in carried):
        # `git rebase -x 'git push'`, `git submodule foreach git push`, `git bisect run …`: the
        # command runs once per commit, submodule or step, beyond what one grant covers
        return [("opaque", f"`git {sub} …` runs a git or gh write of its own "
                           f"(`{carried[0][:60]}`) once per commit, submodule or step")]
    if sub == "submodule" and "foreach" in rest or sub == "bisect" and "run" in rest[:1] or \
            sub in {"difftool", "mergetool"}:
        return []
    flags = set(expand_flags(rest, sub))
    before_dashdash = rest[:rest.index("--")] if "--" in rest else rest
    after_dashdash = rest[rest.index("--") + 1:] if "--" in rest else []
    positional = [a for a in before_dashdash if not a.startswith("-")]
    if sub in {"switch", "checkout"} and positional and "--" not in rest:
        state["branch"] = state["here"] = positional[0]
    if sub in GIT_HISTORY:
        if flags & NON_WRITING or (sub in {"cherry-pick", "revert"} and "-n" in flags):
            return []
        if sub in {"merge", "rebase"}:
            return classify_integration(sub, rest, positional, cwd, dash_c, state)
        if state.get("here") and names_base(state["here"], base_set(cwd, dash_c)):
            # `git switch main && git cherry-pick task/1-x`: integrating into the base branch
            return [("merge-local", f"git {sub} on {state['here']} after switching to it")]
        if sub == "commit" and not state.get("replay") and git_query(
                ["rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd, dash_c):
            head = state.get("branch") or git_query(["symbolic-ref", "-q", "--short", "HEAD"],
                                                    cwd, dash_c)
            if head and names_base(head, base_set(cwd, dash_c)):
                # concluding a merge into the base branch that stopped on a conflict
                return [("merge-local", f"git commit concluding a merge into {head}")]
        return [("commit", f"git {sub}")]
    if sub == "push":
        kind = push_kind(rest, cwd, dash_c, branch=state.get("branch"))
        label = "git push (force, delete, all/tags/mirror or to the base branch)" \
            if kind == "force" else "git push"
        return [(kind, label)] if kind else []
    if sub in {"send-pack", "http-push", "send-email"}:
        return [("push", f"git {sub}")]
    if sub in {"subtree", "lfs"} and "push" in rest:
        return [("push", f"git {sub} push")]
    if sub == "pull" and len(positional) >= 2 and not any(":" in r for r in positional[1:]):
        bases = base_set(cwd, dash_c)
        head = state.get("branch") or (None if state.get("replay") else git_query(
            ["symbolic-ref", "-q", "--short", "HEAD"], cwd, dash_c))
        if all(names_base(r, bases) for r in positional[1:]) and not (
                head and names_base(head, bases)) and (head or state.get("replay")):
            # `git pull origin main` on the task branch: the base catching up, as a merge
            return [("base-sync", f"git pull of the base branch into {head or 'the task branch'}")]
    if sub == "fetch" and positional[:1] == ["."] and any(
            ":" in r and names_base(r.split(":", 1)[1].lstrip("+"), base_set(cwd, dash_c))
            for r in positional[1:]):
        return [("merge-local", "git fetch . <branch>:<base> (fast-forwards the base branch)")]
    if sub in {"pull", "update-ref", "filter-branch", "filter-repo"}:
        return [("history", f"git {sub}")]
    if sub == "symbolic-ref" and len(positional) >= 2:
        return [("history", "git symbolic-ref")]
    if sub == "replace" and (len(positional) >= 2 or flags & {"-d", "--delete", "-f"}):
        return [("history", "git replace")]
    if sub == "reset":
        if flags & {"--hard", "--soft", "--merge", "--keep"} or any(
                re.match(r"^(?:HEAD[~^]\S*|@[~^]\S*|[0-9a-f]{7,40}|origin/\S+|@\{\S+\})$", a)
                or (a not in {"HEAD", "@"} and git_ref(a, cwd, dash_c)) for a in positional):
            return [("history", "git reset")]
    if sub == "branch" and flags & {"-d", "-D", "--delete"}:
        return [("branch-delete", "git branch --delete")]
    if sub == "branch" and flags & {"-f", "--force", "-M", "-C"}:
        return [("history", "git branch (force move, rename or copy over)")]
    if sub == "checkout" and flags & {"-B"} or sub == "switch" and flags & {"-C",
                                                                          "--force-create"}:
        return [("history", f"git {sub} (reset a branch)")]
    if sub == "worktree" and positional[:1] == ["remove"] and flags & {"-f", "--force"}:
        return [("history", "git worktree remove --force (drops uncommitted work)")]
    if sub == "stash":
        action = rest[0] if rest and not rest[0].startswith("-") else "push"
        if action in {"drop", "clear", "pop", "apply", "branch"}:
            return [("history", f"git stash {action}")]
        if action in {"push", "save"}:
            return [("stash", "git stash")]
        return []
    # local changes thrown away: needs the same command as a reset
    if sub == "clean" and flags & {"-f", "--force"} and not flags & {"-n", "--dry-run"}:
        return [("history", "git clean (deletes untracked files)")]
    if sub in {"checkout", "switch"} and flags & {"-f", "--force", "--discard-changes"}:
        return [("history", f"git {sub} --force (discards changes)")]
    if sub == "checkout" and not flags & {"-b", "--orphan", "-p", "--patch"}:
        paths = after_dashdash if "--" in rest else [
            a for a in positional if not (git_ref(a, cwd, dash_c)
                                          or git_ref("origin/" + a, cwd, dash_c))]
        if paths:
            return [("history", "git checkout <paths> (discards changes)")]
    if sub == "restore" and not flags & {"-p", "--patch"} and (
            not flags & {"-S", "--staged"} or flags & {"-W", "--worktree"}):
        return [("history", "git restore (discards changes)")]
    return []


# git options whose value is a command git runs: `rebase -x`, `difftool -x`, `grep -O`,
# `fetch --upload-pack`, `push --receive-pack`
CARRIER_OPTS = {
    "rebase": {"-x", "--exec"}, "difftool": {"-x", "--extcmd"}, "mergetool": {"-x", "--extcmd"},
    "grep": {"-O", "--open-files-in-pager"},
    "fetch": {"--upload-pack"}, "pull": {"--upload-pack"}, "clone": {"-u", "--upload-pack"},
    "ls-remote": {"--upload-pack"}, "archive": {"--exec"}, "push": {"--receive-pack", "--exec"},
    "submodule": set(),
    "filter-branch": {"--tree-filter", "--index-filter", "--msg-filter", "--env-filter",
                      "--commit-filter", "--parent-filter", "--tag-name-filter", "--setup"},
}


def carried_commands(sub, rest):
    """Commands a git subcommand runs for itself: the values of CARRIER_OPTS, what follows
    `submodule foreach` and `bisect run`."""
    out, opts = [], CARRIER_OPTS.get(sub, set())
    for j, a in enumerate(rest):
        name, eq, value = a.partition("=")
        if name in opts and name.startswith("--") and eq:
            out.append(value)
        elif a in opts and j + 1 < len(rest):
            out.append(rest[j + 1])
        elif a[:2] in opts and len(a) > 2 and not a.startswith("--"):
            out.append(a[2:])  # `-x'git push'`, `-Ocmd`
    if sub == "submodule" and "foreach" in rest:
        tail = rest[rest.index("foreach") + 1:]
        while tail and tail[0] in {"--recursive", "-q", "--quiet"}:
            tail = tail[1:]
        if tail:
            out.append(" ".join(tail))
    if sub == "bisect" and rest[:1] == ["run"] and len(rest) > 1:
        out.append(" ".join(shlex.quote(a) for a in rest[1:]))
    return out


def classify_integration(sub, rest, positional, cwd, dash_c, state):
    """git merge / rebase: into a base branch (merge-local), a base branch into the task branch
    (base-sync), or any other history write (commit)."""
    valued = {"-m", "-F", "--file", "-s", "--strategy", "-X", "--strategy-option", "--onto",
              "-x", "--exec", "--message", "--into-name"}
    positional, skip = [], False
    for a in rest[:rest.index("--")] if "--" in rest else rest:
        if skip:
            skip = False
        elif a in valued:
            skip = True
        elif not a.startswith("-"):
            positional.append(a)
    bases = base_set(cwd, dash_c)
    head = state.get("branch") or git_query(["symbolic-ref", "-q", "--short", "HEAD"], cwd,
                                            dash_c)
    if sub == "rebase" and len(positional) >= 2:
        head = positional[1]  # `git rebase <upstream> <branch>` checks <branch> out first
    upstream = positional[:1] if sub == "rebase" else positional
    onto = option_value(rest, ["--onto"])
    names_a_base = any(names_base(r, bases) for r in upstream + ([onto] if onto else []))
    if state.get("replay") and not state.get("branch") and names_a_base:
        # replaying an earlier call: today's HEAD may not be the branch it ran on, and a base
        # merged into something is the task branch catching up
        return [("base-sync", f"git {sub} of the base branch into the task branch")]
    if head and names_base(head, bases):
        short = re.sub(r"^refs/heads/", "", head)
        own = re.compile(r"^(?:HEAD|@|" + re.escape(short) + r")(?:[~^]\d*)+$|^[0-9a-f]{7,40}$")
        if sub == "rebase" and not onto and ("--root" in rest or upstream and
                                             own.match(upstream[0])):
            # `git rebase -i HEAD~3` on main rewrites its own commits: no other branch comes in
            return [("commit", f"git rebase of {head}'s own commits")]
        if len(upstream) == 1 and not onto and re.sub(r"^refs/remotes/", "", upstream[0]) \
                .split("/", 1)[-1] == short and "/" in re.sub(r"^refs/remotes/", "", upstream[0]):
            # `git merge --ff-only origin/main` on main: bringing it up to date is a pull
            return [("history", f"git {sub} of {upstream[0]} into {head} (updates it, as a pull)")]
        return [("merge-local", f"git {sub} into {head}")]
    if names_a_base:
        return [("base-sync", f"git {sub} of the base branch into {head or 'this branch'}")]
    return [("commit", f"git {sub}")]


def option_value(args, names):
    for i, a in enumerate(args):
        for n in names:
            if a == n and i + 1 < len(args):
                return args[i + 1]
            if n.startswith("--") and a.startswith(n + "="):
                return a[len(n) + 1:]
    return None


def read_body(args, cwd, body_names, file_names):
    if {"-e", "--editor", "-w", "--web"} & set(args):
        return None
    body = option_value(args, body_names)
    if body is not None:
        return body
    path = option_value(args, file_names)
    if path and path != "-":
        try:
            with open(os.path.join(cwd or "", os.path.expanduser(path)), encoding="utf-8") as f:
                return f.read()
        except OSError:
            return None
    return None


def approved_pr_body():
    """(path, text) of this session's approved PR body, <run_dir>/PR-BODY.md, or (None, None)."""
    if not RUN_DIR:
        return None, None
    path = os.path.join(RUN_DIR, "PR-BODY.md")
    try:
        with open(path, "rb") as f:
            return path, f.read().decode("utf-8", errors="replace")
    except OSError:
        return None, None


CAT_FILE = re.compile(r"^\$\(\s*cat\s+(?:--\s+)?(['\"]?)([^\s'\"()$`;&|<>]+)\1\s*\)$")


RUN_PR_HASH = re.compile(r"^[ \t>*-]*`?pr_body_sha256`?\s*:\s*`?([0-9a-fA-F]{64})\b", re.M)
BODY_CHECKS = 0  # PR body checks made so far: classify() asks whether a call made one


def approved_pr_hash():
    """The last `pr_body_sha256:` in this session's RUN.md: the hash of PR-BODY.md as the user
    approved it at the gate, or None."""
    if not RUN_DIR:
        return None
    try:
        with open(os.path.join(RUN_DIR, "RUN.md"), encoding="utf-8", errors="replace") as f:
            found = RUN_PR_HASH.findall(f.read(1 << 20))
    except OSError:
        return None
    return found[-1].lower() if found else None


def pr_body_issue(text=None, file=None, cwd=None):
    """Why a PR body is not the approved <run_dir>/PR-BODY.md, or None when it is: the file
    itself (`--body-file`, `body=@file`, `"$(cat file)"`), or text equal to it byte for byte —
    trailing line breaks aside, and a line break may reach here as " ; " from the shell parser.
    The file is the approved one only while its sha256 is the `pr_body_sha256:` that RUN.md
    recorded when the user approved it."""
    global BODY_CHECKS
    BODY_CHECKS += 1
    if text is not None and file is not None:  # `--body-file <approved> --body other`: both
        return pr_body_issue(text, None, cwd) or pr_body_issue(None, file, cwd)
    if text is not None and CAT_FILE.match(text):
        text, file = None, CAT_FILE.match(text).group(2)
    elif text is not None and text.startswith("\0cat:"):  # the same, as raw_scan hands it over
        text, file = None, text[5:]
    path, approved = approved_pr_body()
    if path is None:
        return "this session's run has no PR-BODY.md"
    recorded = approved_pr_hash()
    if recorded is None:
        return ("RUN.md records no `pr_body_sha256:` — after the user approves PR-BODY.md, add "
                "`pr_body_sha256: <sha256 of PR-BODY.md>` (`shasum -a 256 PR-BODY.md`) to RUN.md")
    with open(path, "rb") as f:
        actual = hashlib.sha256(f.read()).hexdigest()
    if actual != recorded:
        return ("PR-BODY.md changed since the user approved it (its sha256 is not RUN.md's "
                "`pr_body_sha256:`) — show the new text and have it approved again")
    if file is not None:
        if file == "-":
            return "a body read from stdin cannot be checked"
        where = os.path.realpath(os.path.join(cwd or os.getcwd(), os.path.expanduser(file)))
        if where == os.path.realpath(path):
            return None
        try:
            with open(where, "rb") as f:
                text = f.read().decode("utf-8", errors="replace")
        except OSError:
            return f"the body file {file} cannot be read"
    if text is None:
        return "the body does not come from PR-BODY.md"
    # a shell command's line breaks reach here as " ; " (split_segments reads lines as commands)
    if text in {approved, approved.rstrip("\n"), approved.replace("\n", " ; "),
                approved.rstrip("\n").replace("\n", " ; ")}:
        return None
    return "the body differs from the approved PR-BODY.md"


def pr_body_check(what, args, cwd):
    """A ("pr-body", reason) item when a gh PR body is not the approved PR-BODY.md. --web leaves
    the body to the user in the browser."""
    if {"-w", "--web"} & set(args):
        return []
    text = option_value(args, ["--body", "-b"])
    file = option_value(args, ["--body-file", "-F"])
    issue = pr_body_issue(text, file, cwd)
    return [("pr-body", f"{what}: {issue}")] if issue else []


def mcp_pr_body(name, inp):
    for key in ("body", "description"):
        if isinstance(inp.get(key), str):
            issue = pr_body_issue(inp[key])
            return [("pr-body", f"{name}: {issue}")] if issue else []
    return [("pr-body", f"{name}: the body does not come from PR-BODY.md")] if \
        "create" in split_words(name) else []


# gh groups that change a repository, its secrets or CI → the repo-admin kind each one is
GH_ADMIN_GROUPS = {"secret": "secrets", "variable": "secrets", "environment": "secrets",
                   "deploy-key": "secrets", "ssh-key": "secrets", "gpg-key": "secrets",
                   "workflow": "ci", "run": "ci", "cache": "ci", "ruleset": "protection",
                   "attestation": "settings", "repo": "settings"}
# gh api endpoints of repository, CI and account settings
GH_API_REPO = re.compile(
    r"^/?(?:repos/[^/]+/[^/]+/?$|orgs/[^/]+/?$|(?:user|orgs/[^/]+)/repos/?$|gists\b|"
    r"(?:repos/[^/]+/[^/]+|orgs/[^/]+|user)/(?:actions|secrets|variables|rulesets|hooks|keys|"
    r"gpg_keys|ssh_signing_keys|codespaces/secrets|dependabot|collaborators|pages|environments|"
    r"releases|deployments|forks|transfer|topics|branches/[^/]+/protection|tags/protection|"
    r"dispatches|vulnerability-alerts|automated-security-fixes|autolinks|properties|"
    r"interaction-limits|invitations)\b)")


def api_admin_kind(endpoint):
    """The repo-admin kind of a write to a repository, org or account settings endpoint."""
    if re.search(r"/(?:secrets|variables|environments|keys|gpg_keys|ssh_signing_keys)\b",
                 "/" + endpoint):
        return "repo-admin-secrets"
    if re.search(r"/(?:rulesets|protection)\b", endpoint):
        return "repo-admin-protection"
    if re.search(r"/(?:actions|dispatches|deployments)\b", endpoint):
        return "repo-admin-ci"
    return "repo-admin-settings"


# gh's own commands: an alias never overrides one of them
GH_COMMANDS = {"agent-task", "alias", "api", "attestation", "auth", "browse", "cache", "co",
               "codespace", "completion", "config", "extension", "gist", "gpg-key", "help",
               "issue", "label", "org", "pr", "preview", "project", "release", "repo", "ruleset",
               "run", "search", "secret", "ssh-key", "status", "variable", "version", "workflow",
               "copilot", "accessibility"}


GH_ENV = {"GH_CONFIG_DIR", "XDG_CONFIG_HOME"}  # where gh reads config.yml (and its aliases)


def gh_config_file(env=None):
    env = {**os.environ, **(env or {})}
    base = env.get("GH_CONFIG_DIR") or (
        os.path.join(env["XDG_CONFIG_HOME"], "gh") if env.get("XDG_CONFIG_HOME")
        else os.path.join(env.get("APPDATA", ""), "GitHub CLI") if os.name == "nt"
        else os.path.expanduser("~/.config/gh"))
    return os.path.join(base, "config.yml")


def gh_env_of(assignments, cwd, into=None):
    """GH_CONFIG_DIR / XDG_CONFIG_HOME among (name, value) pairs (names without case, as
    Windows reads them), as {name: path}; a value built at run time is kept with its `$`."""
    env = dict(into or {})
    for name, value in assignments:
        if name.upper() in GH_ENV:
            env[name.upper()] = value if re.search(r"[$`%!]", value) or not value else \
                (resolve_path(value, cwd) or value)
    return env


def gh_alias(name, env=None):
    """The expansion of a gh alias (`gh alias set p 'pr merge'` → "pr merge"), from the
    `aliases:` block of gh's config.yml, or None. A value the guard cannot read is "!" — a shell
    alias it cannot see."""
    text = read_text(gh_config_file(env), None) or ""
    block = re.search(r"^aliases:[ \t]*\n((?:[ \t]+.*\n?|[ \t]*\n)*)", text, re.M)
    if not block:
        return None
    for line in block.group(1).splitlines():
        m = re.match(r"^[ \t]+(['\"]?)([^:'\"]+)\1:[ \t]*(.*)$", line)
        if m and m.group(2).strip() == name:
            value = m.group(3).strip()
            if value[:1] in {"'", '"'} and value[-1:] == value[:1] and len(value) > 1:
                value = value[1:-1].replace("''", "'") if value[0] == "'" else \
                    value[1:-1].encode().decode("unicode_escape", errors="replace")
            return "!" if value[:1] in {"|", ">", ""} else value
    return None


def expand_gh_alias(alias, args):
    """An alias's words with `$1`, `$2` … filled from `args`; the rest of `args` follows."""
    used, out = set(), []
    for w in shlex.split(alias):
        m = re.fullmatch(r"\$(\d+)", w)
        if m and 0 < int(m.group(1)) <= len(args):
            used.add(int(m.group(1)) - 1)
            out.append(args[int(m.group(1)) - 1])
        else:
            out.append(w)
    return out + [a for k, a in enumerate(args) if k not in used]


def classify_gh(args, cwd, push_read=False, depth=0, env=None):
    """`env`: GH_CONFIG_DIR / XDG_CONFIG_HOME the same command sets for gh (gh_env_of)."""
    rest = list(args)
    while rest and rest[0].startswith("-"):
        rest = rest[2:] if rest[0] in {"-R", "--repo", "--hostname"} else rest[1:]
    if rest and rest[0] not in GH_COMMANDS and depth < 3:
        moved = sorted(k for k in env or {} if k in GH_ENV)
        unread = any(re.search(r"[$`%!]", env[k]) or not env[k] for k in moved)
        alias = None if unread else gh_alias(rest[0], env)
        if moved and alias is None:  # the config may be written in this very command
            return [("opaque", f"`gh {rest[0]}` with {moved[0]} set: an alias from a config "
                               "the guard cannot read")]
        if alias == "!":
            return [("opaque", f"`gh {rest[0]}`: a shell alias the guard cannot read")]
        if alias and alias.startswith("!"):  # gh runs it with sh
            return classify_shell(alias[1:] + " " + " ".join(shlex.quote(a) for a in rest[1:]),
                                  cwd, depth + 1)
        if alias:
            try:
                return classify_gh(expand_gh_alias(alias, rest[1:]), cwd, push_read, depth + 1,
                                   env)
            except ValueError:
                return [("opaque", f"`gh {rest[0]}`: an alias the guard cannot read")]
    if rest[:2] in (["alias", "set"], ["alias", "import"]):
        return gh_alias_set(rest[2:], cwd)
    if len(rest) < 2:
        return []
    group, verb, tail = rest[0], rest[1], rest[2:]
    if group == "api":
        return classify_gh_api(rest[1:], cwd)
    if "--push" in tail and not push_read:
        # `--push` may also be another flag's value (`--subject --push`): the command keeps
        # its own class too
        return [("push", f"gh {group} {verb} --push")] + classify_gh(args, cwd, True, depth)
    if group == "release" and verb in {"create", "upload", "edit"}:
        return [("publish-release", f"gh release {verb}")]
    if group == "gist" and verb in {"create", "edit", "rename"}:
        return [("publish-gist", f"gh gist {verb}")]
    if group == "repo" and verb in {"create", "fork"}:
        return [("publish-repo", f"gh repo {verb}")]
    if group == "repo" and verb == "sync" or group == "pr" and verb == "update-branch":
        force = {"--force", "-f", "--rebase"} & set(expand_flags(tail))  # sync -f: hard reset
        return [("force" if force else "push", f"gh {group} {verb} (updates a branch)")]
    if group == "repo" and verb == "delete":
        return [("destructive", "gh repo delete")]
    if group in {"release", "gist"} and verb in {"delete", "delete-asset"}:
        return [("repo-admin-delete", f"gh {group} {verb}")]
    if group == "repo" and verb in {"deploy-key", "autolink"}:  # gh repo deploy-key add …
        group, verb = verb, (tail[0] if tail else "list")
    if group in GH_LOCAL_GROUPS or verb in GH_READ_VERBS or verb.startswith("-") or \
            group == "repo" and verb in {"set-default", "license", "gitignore", "credits"}:
        return []  # reads, local git config, templates
    if group in {"release", "gist"}:
        return [(f"publish-{group}", f"gh {group} {verb}")]
    if group in GH_ADMIN_GROUPS or group == "autolink":  # not tracker fields
        kind = GH_ADMIN_GROUPS.get(group, "settings")
        return [(f"repo-admin-{kind}", f"gh {group} {verb} (repository or CI settings)")]
    if group not in {"issue", "pr"}:  # gh project, label, workflow run, secret set, repo edit, …
        return [("tracker", f"gh {group} {verb}")]
    if group == "issue" and verb == "create":
        return [("create-task", "gh issue create")]
    if group == "pr" and verb == "create":
        return [("pr", "gh pr create")] + pr_body_check("gh pr create", tail, cwd)
    if group == "pr" and verb == "edit" and {"-b", "--body", "-F", "--body-file"} & {
            a.split("=", 1)[0] for a in tail}:
        return [("tracker", "gh pr edit")] + pr_body_check("gh pr edit", tail, cwd)
    if group == "pr" and verb == "merge":
        return [("merge", "gh pr merge")]
    if verb in {"comment", "review"}:
        if verb == "review" and not ({"-c", "--comment", "-b", "--body", "-F", "--body-file",
                                      "-r", "--request-changes"} & set(tail)):
            return [("tracker", "gh pr review")]
        return [("comment", f"gh {group} {verb}",
                 read_body(tail, cwd, ["--body", "-b"], ["--body-file", "-F"]))]
    if verb == "delete":
        return [("destructive", f"gh {group} delete")]
    if group == "issue" and verb in {"close", "reopen"}:
        target = [a for j, a in enumerate(tail) if not a.startswith("-") and not (j and tail[
            j - 1] in {"-R", "--repo", "-c", "--comment", "-r", "--reason"})][:1]
        found = [("status", f"gh issue {verb}", target)]
    else:
        found = [("tracker", f"gh {group} {verb}")]
    comment = option_value(tail, ["--comment", "-c"])
    if verb == "close" and comment is not None:
        found.append(("comment", f"gh {group} close --comment", comment))
    return found


GH_FIELD_FLAGS = {"-f": False, "--raw-field": False, "-F": True, "--field": True}
GH_API_VALUE_FLAGS = {"-X", "--method", "-H", "--header", "-f", "-F", "--field", "--raw-field",
                      "--input", "-q", "--jq", "-t", "--template", "-p", "--preview",
                      "--hostname", "--cache"}


def gh_fields(tail):
    """(name, value, typed) of each `gh api` field, in every spelling pflag takes: `-f k=v`,
    `-fk=v`, `-if k=v`, `--field k=v`, `--field=k=v`. `typed` is -F/--field, which reads
    `@file` and converts literals."""
    out, j = [], 0
    while j < len(tail):
        a, field = tail[j], None
        if a in GH_FIELD_FLAGS and j + 1 < len(tail):
            field, typed = tail[j + 1], GH_FIELD_FLAGS[a]
            j += 1
        elif a.startswith("--") and a.split("=", 1)[0] in GH_FIELD_FLAGS and "=" in a:
            field, typed = a.split("=", 1)[1], GH_FIELD_FLAGS[a.split("=", 1)[0]]
        else:
            m = re.match(r"^-i*([fF])(.+)$", a)  # -i (--include) is the only short boolean
            if m:
                field, typed = m.group(2), m.group(1) == "F"
        if field is not None:
            name, eq, value = field.partition("=")
            out.append((name, value if eq else None, typed))
        j += 1
    return out


def gh_api_endpoint(tail):
    """The first argument of `gh api` that is not an option or an option's value."""
    j = 0
    while j < len(tail):
        a = tail[j]
        if a in GH_API_VALUE_FLAGS:
            j += 2
            continue
        if not a.startswith("-") or a == "-":
            return a
        j += 1
    return ""


def gh_alias_set(args, cwd):
    """`gh alias set <name> <expansion>`: an alias that names a gated write (`'pr merge'`, a
    shell alias `!git push`) redefines a gh word the way `git config alias.p push` does; one the
    guard cannot read (`gh alias import`, `--clobber` from stdin) counts too."""
    words = [a for a in args if not a.startswith("-")]
    if "--shell" in args or "-s" in args:
        words[1:2] = ["!" + words[1]] if len(words) > 1 else []
    if len(words) < 2 or words[1] == "-":
        return [("tamper", "gh alias import / set from a file or stdin (redefines gh words)")]
    expansion = words[1]
    try:
        found = classify_shell(expansion[1:], cwd, 1) if expansion.startswith("!") else \
            classify_gh(shlex.split(expansion), cwd, depth=3)
    except ValueError:
        found = [("opaque", expansion)]
    if found:
        return [("tamper", f"gh alias set {words[0]} '{expansion[:40]}' (redefines a gh word as "
                           "a gated write)")]
    return []


def classify_gh_api(tail, cwd):
    method = ""
    for i, a in enumerate(tail):
        m = re.match(r"^(?:-X|--method=?)(\w*)$", a)
        if m:
            method = (m.group(1) or (tail[i + 1] if i + 1 < len(tail) else "")).upper()
    fields = gh_fields(tail)
    endpoint = gh_api_endpoint(tail)
    endpoint = re.sub(r"^https?://[^/]+/(?:api/v3/)?", "", endpoint)  # a full URL
    if re.fullmatch(r"/*(?:api/)?graphql/*", endpoint):
        return classify_graphql(tail, cwd)
    if method == "GET" or (not method and not fields and not any(
            a.split("=", 1)[0] == "--input" for a in tail)):
        return []
    method = method or "POST"
    body, body_file, nested = None, None, False
    for name, value, typed in fields:
        if name == "body" and value is not None:  # `-F body=@file` reads the file
            body, body_file = (None, value[1:]) if value.startswith("@") and typed else \
                (value, None)
        elif re.search(r"\[body\]", name):  # `-F x[body]=…`: a nested field of the same name
            nested = True
    if "comments" in endpoint:
        if method == "DELETE":
            return [("delete", "gh api DELETE comment")]
        return [("comment", "gh api (comment)", body)]
    if re.search(r"/pulls/\d+/merge$", endpoint):
        return [("merge", "gh api merge")]
    from_input = any(a.split("=", 1)[0] == "--input" for a in tail)
    if re.search(r"/pulls/?$", endpoint):
        issue = "a body read from --input" if from_input else \
            "a body passed as a nested field" if nested else pr_body_issue(body, body_file, cwd)
        return [("pr", f"gh api {method} {endpoint}")] + \
            ([("pr-body", f"gh api {endpoint}: {issue}")] if issue else [])
    if re.search(r"/pulls/\d+/?$", endpoint) and (body is not None or body_file is not None
                                                     or from_input or nested):
        issue = "a body read from --input" if from_input else \
            "a body passed as a nested field" if nested else pr_body_issue(body, body_file, cwd)
        return [("tracker", f"gh api {method} {endpoint}")] + \
            ([("pr-body", f"gh api {endpoint}: {issue}")] if issue else [])
    if method == "POST" and re.search(r"/issues/?$", endpoint):
        return [("create-task", f"gh api POST {endpoint}")]
    if "/git/refs" in endpoint:
        return [("force", f"gh api {method} {endpoint}")]
    if GH_API_REPO.search(endpoint):
        if method == "DELETE" and re.search(r"^/?repos/[^/]+/[^/]+/?$", endpoint):
            return [("destructive", f"gh api DELETE {endpoint}")]
        if method == "DELETE" and re.search(r"/releases\b|^/?gists\b", endpoint):
            return [("repo-admin-delete", f"gh api DELETE {endpoint}")]
        if re.search(r"^/?gists\b", endpoint):
            return [("publish-gist", f"gh api {method} {endpoint}")]
        if re.search(r"/releases\b", endpoint):  # also …/releases/<id>/assets, like gh release upload
            return [("publish-release", f"gh api {method} {endpoint}")]
        if re.search(r"^/?(?:(?:user|orgs/[^/]+)/repos|repos/[^/]+/[^/]+/forks)/?$", endpoint) \
                and method == "POST":
            return [("publish-repo", f"gh api {method} {endpoint}")]
        return [(api_admin_kind(endpoint), f"gh api {method} {endpoint} (repository settings)")]
    return [("tracker", f"gh api {method}")]


# GraphQL mutations by name → the class of the same write through gh or the REST API
GRAPHQL_CLASSES = [
    (re.compile(r"^(?:mergePullRequest|enablePullRequestAutoMerge|enqueuePullRequest|"
                r"mergeBranch)$"), "merge"),
    (re.compile(r"^createPullRequest$"), "pr"),
    (re.compile(r"^(?:update|archive|unarchive|transfer|cloneTemplate)Repository$"),
     "repo-admin-settings"),
    (re.compile(r"^(?:create|update|delete)(?:BranchProtectionRule|RepositoryRuleset)$"),
     "repo-admin-protection"),
    (re.compile(r"^(?:createRepository|forkRepository)$"), "publish-repo"),
    (re.compile(r"^deleteRepository$"), "destructive"),
    (re.compile(r"^(?:createRef|updateRefs?|deleteRef)$"), "force"),
    (re.compile(r"^createCommitOnBranch$"), "push"),
    (re.compile(r"^createIssue$"), "create-task"),
    (re.compile(r"^(?:deleteIssue|deleteProjectV2Item|deleteDiscussion)$"), "destructive"),
    (re.compile(r"^(?:delete\w*Comment|deletePullRequestReviewComment)$"), "delete"),
    (re.compile(r"^(?:add|update)\w*Comment\w*$|^addPullRequestReview\w*$|"
                r"^submitPullRequestReview$|^addDiscussionComment$"), "comment"),
    (re.compile(r"^(?:create|update)Release$"), "publish-release"),
    (re.compile(r"^deleteRelease$"), "repo-admin-delete"),
    (re.compile(r"^(?:create|update|delete)(?:Deployment\w*|Environment)$"), "repo-admin-ci"),
]


def graphql_mutations(query):
    """The top-level fields of each `mutation` operation in a GraphQL document."""
    names = []
    for m in re.finditer(r"\bmutation\b[^{]*\{", query):
        depth, j, start = 1, m.end(), m.end()
        while j < len(query) and depth:
            c = query[j]
            if c in "{(":
                if depth == 1:  # a field of the operation: its name ends the text before it
                    head = query[start:j]
                    f = re.search(r"(?:\w+\s*:\s*)?([A-Za-z_]\w*)\s*$", head)
                    if f:
                        names.append(f.group(1))
                depth += 1
            elif c in ")}":
                depth -= 1
                if depth == 1:
                    start = j + 1
            elif c in "\"'":
                end = query.find(c, j + 1)
                j = end if end > 0 else len(query)
            j += 1
    return names


def read_text(path, cwd, limit=1 << 20):
    """A file's text, or None when it cannot be read (stdin, a missing file, a name built from
    an expansion)."""
    if not path or path == "-" or re.search(r"[$`\0]", path):
        return None
    try:
        with open(os.path.join(cwd or "", os.path.expanduser(path)), encoding="utf-8",
                  errors="replace") as f:
            return f.read(limit)
    except OSError:
        return None


def graphql_literal(query):
    """Whether a GraphQL document reached the guard as it will be sent: not a shell or
    PowerShell variable, a command substitution or a file it could not read. A `$name` is a
    GraphQL variable only when the operation declares it (`($name: Type)`); any other is an
    expansion, and every document has a `{`."""
    if not isinstance(query, str) or "{" not in query or "\0" in query or "`" in query or \
            re.search(r"\$[({]", query):
        return False
    return set(re.findall(r"\$(\w+)", query)) <= set(re.findall(r"\$(\w+)\s*:", query))


def classify_graphql(tail, cwd):
    """gh api graphql: the query passes as a field (`-f query=…`, `-fquery=…`, `--raw-field
    query=…`, `-F query=@file`) or in a JSON `--input` file."""
    queries, variables, unread = [], {}, []
    for name, value, typed in gh_fields(tail):
        if value is None:
            continue
        if name == "query":
            if typed and value.startswith("@"):
                text = read_text(value[1:], cwd)
                if text is None:
                    unread.append(f"the query file {value[1:]}")
                    continue
                value = text
            queries.append(value)
        else:
            variables[name] = (value, typed)
    input_vars = None
    source = option_value(tail, ["--input"])
    if source is not None:
        try:
            data = json.loads(read_text(source, cwd) or "")
        except ValueError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("query"), str):
            queries.append(data["query"])
            input_vars = data.get("variables")
        else:
            unread.append(f"--input {source}")
    return graphql_classes("gh api graphql", queries, variables, input_vars, cwd, unread)


def graphql_classes(where, queries, variables, input_vars, cwd, unread=()):
    """Each mutation of a GraphQL request, classed as the same write through gh —
    createPullRequest a PR (its body held to PR-BODY.md), mergePullRequest a merge,
    updatePullRequest a tracker edit (its body held to PR-BODY.md), comments as comments; any
    other mutation a tracker edit. A query the guard cannot read is opaque: it may be any of
    them."""
    if unread or not queries or not all(graphql_literal(q) for q in queries):
        what = unread[0] if unread else "no query" if not queries else \
            "a query built from a variable, a substitution or a file"
        return [("opaque", f"{where}: {what} — the guard cannot read the GraphQL it sends")]
    joined = "\n".join(queries)
    if not re.search(r"\bmutation\b", joined):
        return []
    names = graphql_mutations(joined)
    if not names:
        return [("opaque", f"{where}: a mutation the guard cannot read")]
    nested = [n for n in variables if re.search(r"\[body\]", n)]
    input_body = graphql_has_body(input_vars)
    found = []
    for name in names:
        kind = next((k for rx, k in GRAPHQL_CLASSES if rx.match(name)), None)
        if name == "updatePullRequest":
            found.append(("tracker", f"{where} (updatePullRequest)"))
            if re.search(r"\bbody\s*:", joined) or "body" in variables or nested or input_body:
                found += graphql_pr_body(where, name, joined, variables, nested, input_body, cwd)
        elif name in {"closePullRequest", "reopenPullRequest"}:
            found.append(("tracker", f"{where} ({name})"))
        elif kind == "pr":
            found.append(("pr", f"{where} (createPullRequest)"))
            found += graphql_pr_body(where, name, joined, variables, nested, input_body, cwd)
        elif kind == "comment":
            found.append(("comment", f"{where} ({name})", None))
        elif kind:
            found.append((kind, f"{where} ({name})"))
        else:
            found.append(("tracker", f"{where} ({name})"))
    return found


def graphql_has_body(value):
    """Whether JSON variables carry a `body` anywhere."""
    if isinstance(value, dict):
        return "body" in value or any(graphql_has_body(v) for v in value.values())
    if isinstance(value, list):
        return any(graphql_has_body(v) for v in value)
    return False


def graphql_pr_body(where, name, query, variables, nested=(), input_body=False, cwd=None):
    """The PR body check of a GraphQL PR write: the body passes as a `body` variable that is the
    approved file (`-F body=@<run_dir>/PR-BODY.md`) or its text; one written into the query, a
    nested field (`-F input[body]=…`) or JSON variables cannot be checked."""
    if nested or input_body:
        issue = "a body passed as a nested field or in JSON variables cannot be checked " \
                "(pass it as `-F body=@<run_dir>/PR-BODY.md` and `body: $body`)"
    elif re.search(r"\bbody\s*:\s*\$body\b", query) and "body" in variables:
        value, typed = variables["body"]
        issue = pr_body_issue(None, value[1:], cwd) if typed and value.startswith("@") else \
            pr_body_issue(value, None, cwd)
    else:
        issue = "a body written into the GraphQL query cannot be checked (pass it as " \
                "`-F body=@<run_dir>/PR-BODY.md` and `body: $body`)"
    return [("pr-body", f"{where} ({name}): {issue}")] if issue else []


def skip_prefix(tokens):
    i = 0
    while i < len(tokens):
        t = tokens[i]
        name = os.path.basename(t).lower()
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t) or t in SHELL_KEYWORDS:
            i += 1
        elif name == "env":
            i += 1
            while i < len(tokens) and (tokens[i].startswith("-") or "=" in tokens[i]):
                if tokens[i] == "-S":
                    break
                i += 2 if tokens[i] in {"-u", "-C"} else 1
            if i < len(tokens) and tokens[i] == "-S":
                return i - 1  # env -S "<command>": handled as a command carrier
        elif name in WRAPPERS:
            i += 1
            while i < len(tokens) and tokens[i].startswith("-"):
                i += 2 if tokens[i] in WRAPPERS[name] else 1
            if name in {"timeout", "gtimeout"} and i < len(tokens):
                i += 1
        else:
            break
    return i


def classify_shell(command, cwd, depth=0, state=None):
    if depth > 4:
        return []
    command = command.replace("\\\n", " ")
    command, fed, bodies = strip_heredocs(command)
    found = []
    for body in fed:
        found += classify_shell(body, cwd, depth + 1)
    for inner in re.findall(r"\$\(([^()]*)\)|`([^`]*)`", command):
        found += classify_shell(inner[0] or inner[1], cwd, depth + 1)
    if "<<<" in command or re.search(r"\|\s*(?:\S*/)?(?:sh|bash|zsh|dash|ksh)\b(?!\s+-\w*c)",
                                     command) or re.search(r"\beval\b|\$\(", command):
        found += raw_scan(command, cwd, depth)
        for body in bodies:  # a heredoc piped into a shell or captured runs as commands
            found += raw_scan(body, cwd, depth)
    for m in SUBST_PROGRAM.finditer(command):
        try:
            words = shlex.split(m.group(2))
        except ValueError:
            words = m.group(2).split()
        if plausibly_git(m.group(1), words):
            found.append(("opaque", "a program named by a command substitution that may be git"))
    try:
        segments = split_segments(command)
    except ValueError:
        return found + raw_scan(command, cwd, depth)
    per_segment, state, exported, gh_env = {}, {} if state is None else state, [], {}
    for tokens in segments:
        seg_start = len(found)
        i = skip_prefix(tokens)
        if i >= len(tokens) or os.path.basename(tokens[i]) in {"export", "declare", "typeset"}:
            exported += [t for t in tokens if risky_git_env(t)]  # set for the commands after it
            gh_env = gh_env_of(assignments(tokens), cwd, gh_env)
            continue
        prog = os.path.basename(tokens[i]).lower()
        args = tokens[i + 1:]
        if prog in {"cd", "pushd"} and args and not args[0].startswith("-"):
            moved = resolve_path(args[0], cwd)  # `cd <run> && gh pr create --body-file …`
            cwd = moved if moved and os.path.isdir(moved) else cwd
            continue
        risky = [t for t in tokens[:i] if risky_git_env(t)] + exported
        if risky and "git" in {os.path.basename(t).lower() for t in tokens[i:]}:
            found.append(("opaque", f"git run with `{risky[0][:60]}` from the environment: it "
                                    "can run a program or change git's settings"))
        if re.sub(r"\.exe$", "", prog) in {"pwsh", "powershell"}:  # PowerShell run from a shell
            if any(PS_ENCODED.match(a) for a in args):
                found.append(("opaque", f"`{prog} -EncodedCommand …`: an encoded command"))
            carrier = next((args[j + 1] for j, a in enumerate(args[:-1])
                            if a.lower() in {"-c", "-command", "-cmd"}), None)
            if carrier:
                found += classify_powershell(carrier, cwd)
            continue
        if re.sub(r"\.exe$", "", prog) == "cmd":  # cmd /c "set GIT_SSH_COMMAND=x && git …"
            carried = " ".join(args)
            risky_set = cmd_risky_env(carried)
            if risky_set and re.search(r"(?i)(?<![\w-])git(?:\.exe)?(?![\w-])", carried):
                found.append(("opaque", f"git run with `{risky_set[:60]}` from the environment: "
                                        "it can run a program or change git's settings"))
            found += raw_scan(carried, cwd, depth, gh_env_of(
                [(m.group(1), m.group(2).strip()) for m in CMD_SET.finditer(carried)], cwd,
                gh_env))
            continue
        if prog in SHELLS or prog in {"su", "eval", "env"}:
            carrier = None
            if prog == "eval":
                carrier = " ".join(args)
            else:
                for j, a in enumerate(args):
                    if re.fullmatch(r"-[a-z]*c[a-z]*", a) or (prog == "env" and a == "-S"):
                        carrier = args[j + 1] if j + 1 < len(args) else None
                        break
            if carrier:
                found += classify_shell(carrier, cwd, depth + 1)
            if prog != "env":
                continue
        # every git/gh word in the segment: covers find -exec, xargs, timeout, …
        inner = []
        for j in range(i, len(tokens)):
            name = os.path.basename(tokens[j]).lower()
            if name == "git":
                inner += classify_git(tokens[j + 1:], cwd, depth, {}, state)
            elif name == "gh":
                inner += classify_gh(tokens[j + 1:], cwd,
                                     env=gh_env_of(assignments(tokens[:j]), cwd, gh_env))
            elif j > i and name in SHELLS and j + 2 < len(tokens) and \
                    re.fullmatch(r"-[a-z]*c[a-z]*", tokens[j + 1]):
                inner += classify_shell(tokens[j + 2], cwd, depth + 1)  # find -exec sh -c '…'
        fanout = prog in FANOUT or any(os.path.basename(t).lower() in FANOUT
                                       for t in tokens[:i])
        if fanout and inner:
            # xargs / parallel / find -exec run it once per input, with arguments the guard
            # never sees (`echo main | xargs git push origin`)
            found.append(("opaque", f"`{' '.join(tokens[:i + 1])[:40]} … git/gh`: run once per "
                                    "input with arguments the guard cannot see"))
        else:
            found += inner
        found += classify_http(prog, args, cwd)
        runner = re.sub(r"^\$\{?|\}$", "", prog) if "$" in tokens[i] else prog
        if "$" in tokens[i] and runner not in RUNNERS:
            carrier = next((args[j + 1] for j, a in enumerate(args[:-1])
                            if re.fullmatch(r"-[a-z]*c[a-z]*", a)), None)
            if carrier is not None:  # `$SHELL -c "git push"`: read the carried command too
                found += classify_shell(carrier, cwd, depth + 1)
            if plausibly_git(tokens[i], args):
                found.append(("opaque", f"`{tokens[i]} …`: a program named by a variable or a "
                                        "substitution that may be git"))
        if runner in RUNNERS and any(PUBLISH_TARGET.fullmatch(t)
                                     for t in runner_targets(runner, args)):
            found.append(("opaque", f"`{prog} {' '.join(args)[:60]}`: a project script that "
                                    "looks like it publishes"))
        for action in {item[0] for item in found[seg_start:]} & CONSUMED_BY_USE:
            per_segment[action] = per_segment.get(action, 0) + 1
    found += [("repeat", f"{n} × {a} in one call") for a, n in per_segment.items() if n > 1]
    return found


# environment variables that make git run a program or read other settings
GIT_ENV_PROGRAM = re.compile(r"^(?:GIT_(?:EDITOR|SEQUENCE_EDITOR|PAGER|SSH|SSH_COMMAND|ASKPASS|"
                             r"EXTERNAL_DIFF|PROXY_COMMAND|EXEC_PATH|TEMPLATE_DIR|DIR|"
                             r"COMMON_DIR)|SSH_ASKPASS|EDITOR|VISUAL|PAGER|HOME|XDG_CONFIG_HOME|"
                             r"USERPROFILE)$")
BENIGN_PROGRAM = {"cat", "less", "more", "true", "false", "vi", "vim", "nvim", "nano", "emacs",
                  "code", "head", "tail", "ssh", ":"}


def benign_program(key, value):
    """`git -c core.pager=cat`, `-c core.editor=true`, `-c pager.log=false`: a pager or an editor
    named plainly, as GIT_PAGER=cat and GIT_EDITOR=true pass in the environment."""
    return (key in {"core.pager", "core.editor", "sequence.editor"} or key.startswith("pager.")) \
        and bool(re.fullmatch(r"[\w./:-]*", value)) and \
        os.path.basename(value) in BENIGN_PROGRAM | {""}


def assignments(tokens):
    """The (name, value) pairs among `NAME=value` words."""
    return [m.groups() for m in (re.match(r"^([A-Za-z_]\w*)=(.*)$", t, re.S) for t in tokens)
            if m]


def risky_git_env(token):
    """`GIT_CONFIG_PARAMETERS=…`, `GIT_CONFIG_COUNT=…`, `GIT_SSH_COMMAND='sh -c …'`,
    `GIT_EDITOR=…`: an assignment before git that changes its settings or runs a program.
    `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`, `GIT_PAGER=cat` and other plain
    names of an editor or pager pass; GIT_INDEX_FILE and the like are not settings."""
    m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", token, re.S)
    if not m:
        return False
    name, value = m.group(1), m.group(2)
    if name.startswith("GIT_CONFIG"):
        if name in {"GIT_CONFIG_NOSYSTEM"}:
            return False
        return not (name in {"GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"} and value == "/dev/null")
    if not GIT_ENV_PROGRAM.match(name):
        return False
    if name in {"GIT_DIR", "GIT_COMMON_DIR", "GIT_TEMPLATE_DIR", "GIT_EXEC_PATH", "HOME",
                "XDG_CONFIG_HOME", "USERPROFILE"}:
        return True  # another repository's config and hooks, or other git programs
    return not (re.fullmatch(r"[\w./:-]*", value) and
                os.path.basename(value) in BENIGN_PROGRAM | {""})


# programs that run the command they carry once per input, with arguments added at run time
FANOUT = {"xargs", "gxargs", "parallel", "find", "gfind", "fd", "fdfind", "watch", "entr", "rush"}


GIT_VOCAB = re.compile(r"(?:push|commit|merge|rebase|reset|pull|cherry-pick|revert|stash|checkout|"
                       r"switch|restore|clean|filter-branch|update-ref)(?![a-z])", re.I)
GIT_WRITING = {"push", "commit", "merge", "rebase", "reset", "pull", "cherry-pick", "revert",
               "stash", "checkout", "switch", "restore", "clean", "filter-branch", "filter-repo",
               "update-ref", "am", "branch", "send-email", "send-pack", "symbolic-ref", "replace",
               "subtree", "submodule", "lfs", "commit-tree", "tag"}


GIT_GLOBAL_FLAGS = {"-p", "--paginate", "-P", "--no-pager", "--bare", "--no-replace-objects",
                    "--literal-pathspecs", "--glob-pathspecs", "--noglob-pathspecs",
                    "--icase-pathspecs", "--no-optional-locks", "--no-advice", "--no-lazy-fetch"}


EXPANSION = re.compile(r"\$\{[^}]*\}|\$\([^()]*(?:\([^()]*\)[^()]*)*\)|`[^`]*`|\$\w+")
# words in a variable or substitution that name a tool other than git, or a git setting
OTHER_TOOL_WORDS = {
    "editor", "pager", "ssh", "askpass", "dir", "root", "home", "config", "hooks", "toplevel",
    "work", "tree", "docker", "podman", "dotnet", "npm", "npx", "yarn", "pnpm", "bun", "node",
    "deno", "gradle", "gradlew", "mvn", "maven", "cargo", "go", "python", "python3", "py", "pip",
    "make", "cmake", "ninja", "unity", "kubectl", "helm", "bazel", "nuget", "msbuild",
    "xcodebuild", "swift", "java", "ruby", "gem", "bundle", "terraform", "aws", "gcloud", "az",
    "flutter", "dart", "conan", "brew", "hg", "svn", "p4", "rsync", "scp", "curl", "wget",
    "cc", "cxx", "gcc", "clang", "ld", "tar", "zip", "unzip", "sed", "awk", "grep", "find",
}


def plausibly_git(program, args):
    """Whether a program named by $VAR or $(…) may be git. A literal basename after the
    expansion decides by itself (`$ROOT/gradlew` is not git; `$ROOT/git` is read as git where
    it stands). A bare expansion whose words name git (`$GIT`, `${GIT_BIN}`, `$(which git)`)
    is git unless its first argument is a known read-only subcommand; one that names another
    tool (`$DOTNET`, `$DOCKER`, `$GIT_EDITOR`) is not; any other (`$CMD`, `$X`) is git when its
    first argument — after nothing but git's own global options — is a writing git
    subcommand. `$PY -m pytest -k checkout` and `$CC -o tag main.c` are not."""
    program = program.strip("\"'")
    marked = EXPANSION.sub("\0", program)
    if "/" in marked and "\0" not in marked.rsplit("/", 1)[1]:
        return False  # a literal basename: `git` there is classified as git directly
    words = {w.lower() for w in re.findall(r"[A-Za-z0-9]+", program)}
    i, global_only = 0, True
    while i < len(args) and args[i].startswith("-"):
        opt = args[i].split("=", 1)[0]
        if opt in GIT_OPTS_WITH_VALUE:
            i += 1 if "=" in args[i] else 2
        else:
            global_only = global_only and args[i] in GIT_GLOBAL_FLAGS
            i += 1
    first = args[i] if i < len(args) else ""
    if words & OTHER_TOOL_WORDS:
        return False
    if "git" in words:
        return bool(first) and (first in GIT_WRITING or first not in GIT_BUILTINS)
    return global_only and first in GIT_WRITING


RUNNERS = {"make", "gmake", "just", "task", "rake", "npm", "pnpm", "yarn", "bun", "mise"}
PUBLISH_TARGET = re.compile(r"push|publish|release|deploy|ship", re.I)  # whole target names
RUNNER_OPTS_WITH_VALUE = {"-C", "-f", "--file", "--makefile", "-I", "--include-dir", "-o",
                          "--old-file", "-W", "--what-if", "--assume-new", "--new-file", "-d",
                          "--dir", "--justfile", "--working-directory", "-t", "--taskfile",
                          "-r", "-R", "--prefix", "-w", "--workspace", "--filter", "-F", "--cwd",
                          "--dotenv-path", "--shell", "--color", "-E", "--env"}


def runner_targets(runner, args):
    """The target slot of a runner: every make / rake / task target, the first just recipe, the
    script after npm / pnpm / yarn / bun / mise `run` (or the first word: `npm publish`, `yarn
    release`) — not a test filter (`bun test push`) or a script's own arguments."""
    words, skip = [], False
    for a in args[:args.index("--")] if "--" in args else args:
        if skip:
            skip = False
        elif a.startswith("-"):
            skip = a in RUNNER_OPTS_WITH_VALUE
        elif not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", a):
            words.append(a)
    if runner in {"make", "gmake", "rake", "task"}:
        return [w for w in words if not w.isdigit()]  # `make -j 4 push`
    if words and words[0] in {"run", "run-script", "rr", "r"} and runner != "just":
        return words[1:2]
    return words[:1]
TRACKER_PATH = re.compile(r"/rest/api/\d|/rest/agile/|/api/v\d+/(?:task|issue|projects)|"
                          r"/youtrack/api/|/api/issues", re.I)
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# incoming webhooks and bot APIs of chat and mail services: a POST there is a message sent
WEBHOOK = re.compile(r"hooks\.slack\.com|slack\.com/api/(?:chat|files)\.|"
                     r"discord(?:app)?\.com/api/(?:v\d+/)?(?:webhooks|channels/\d+/messages)|"
                     r"webhook\.office\.com|outlook\.office(?:365)?\.com/webhook|"
                     r"logic\.azure\.com|api\.telegram\.org/bot|hooks\.zapier\.com|"
                     r"chat\.googleapis\.com|api\.sendgrid\.com|api\.mailgun\.net|"
                     r"api\.postmarkapp\.com", re.I)
# MCP servers of mail, chat and calendar services: what they send leaves the session for good
SEND_SERVER = re.compile(r"(?<![a-z0-9])(?:g?mail|e-?mail|outlook|office365|slack|discord|"
                         r"telegram|whatsapp|mattermost|zulip|rocket-?chat|twilio|sendgrid|mailgun|"
                         r"postmark|smtp|imessage|calendar|gcal|calendly)(?![a-z0-9])|"
                         # words with other meanings count only as the server's whole name
                         r"^(?:claude-ai-|mcp-)?(?:signal|sms|zoom|teams|ms-?teams|exchange)"
                         r"(?:-(?:mcp|server|api))?$", re.I)
# on any other server, a tool whose own name sends mail or a message, or writes a calendar event
MAIL_VERBS = {"send", "reply", "forward"}
MAIL_OBJECTS = {"mail", "mails", "email", "emails", "gmail", "message", "messages", "sms", "draft",
                "drafts", "dm", "dms"}
EVENT_VERBS = {"create", "update", "delete", "respond", "insert", "patch", "cancel", "move",
               "add", "remove", "edit", "rsvp", "accept", "decline", "reschedule"}
EVENT_OBJECTS = {"event", "events", "meeting", "meetings", "calendar", "calendars", "invite",
                 "invitation", "invitations"}
SEND_WORDS = {"send", "reply", "forward", "post", "publish", "invite", "respond", "schedule",
              "share", "broadcast", "rsvp", "react", "reaction", "reactions"}
CALENDAR_WRITES = {"create", "update", "delete", "move", "insert", "patch", "cancel", "add",
                   "import", "quick", "remove", "edit"}


def classify_http(prog, args, cwd=None):
    """curl, wget and httpie calls that write to a tracker API."""
    urls = [a for a in args if re.match(r"^(?:https?://)?[\w.-]+\.[a-z]{2,}(?:[:/]|$)", a, re.I)
            or "://" in a]
    webhook = any(WEBHOOK.search(u) for u in urls)
    if not webhook and not any(TRACKER_SERVER.search(u) or TRACKER_PATH.search(u) for u in urls):
        return []
    write = False
    if prog == "curl":
        for j, a in enumerate(args):
            m = re.match(r"^(?:-X|--request)(?:=)?(\w*)$", a)
            if m:
                method = (m.group(1) or (args[j + 1] if j + 1 < len(args) else "")).upper()
                write = write or method in WRITE_METHODS
            elif re.match(r"^(?:-d|--data(?:-\w+)?|--json|-F|--form(?:-string)?|-T|--upload-file)"
                          r"(?:=|@|$)", a) or re.match(r"^-[dFT]\S", a):
                write = True
    elif prog == "wget":
        write = any(re.match(r"^--(?:post-data|post-file|body-data|body-file)(?:=|$)", a)
                    or re.match(r"^--method=?(?!get\b)(?!head\b)\w", a, re.I) for a in args)
    elif prog in {"http", "https", "xh", "xhs"}:
        positional = [a for a in args if not a.startswith("-")]
        method = positional[0].upper() if positional and positional[0].isalpha() else ""
        items = [a for a in positional if re.match(r"^[\w.\[\]-]*(?:=|:=|=@|:=@|@)", a)]
        write = method in WRITE_METHODS or (method != "GET" and bool(items))
    else:
        return []
    if not write:
        return []
    if webhook:
        return [("send", f"{prog} to a chat or mail webhook")]
    if any(re.search(r"/graphql/?(?:[?#]|$)", u) for u in urls):
        return http_graphql(prog, args, cwd)
    if any(re.search(r"/comments?\b", u) for u in urls):
        return [("comment", f"{prog} to a tracker comment API", None)]
    return [("tracker", f"{prog} to a tracker API")]


def http_graphql(prog, args, cwd):
    """A POST to a tracker's GraphQL endpoint (api.github.com/graphql, api.linear.app/graphql):
    its mutations are classed as through gh api graphql. The JSON body is read from the
    command (curl -d/--data*/--json, wget --post-data/--body-data, httpie `query=…`) or from its
    `@file`; anything else is opaque."""
    where = f"{prog} to a GraphQL API"
    bodies, unread = [], []
    if prog in {"curl", "wget"}:
        names = r"-d|--data|--data-raw|--data-binary|--data-ascii|--data-urlencode|--json" \
            if prog == "curl" else r"--post-data|--body-data|--post-file|--body-file"
        j = 0
        while j < len(args):
            a = args[j]
            m = re.match(rf"^({names})(?:=(.*))?$", a, re.S) or \
                (re.match(r"^(-d)(.+)$", a, re.S) if prog == "curl" else None)
            if m:
                value = m.group(2)
                if value is None and j + 1 < len(args):
                    value, j = args[j + 1], j + 1
                raw = m.group(1) == "--data-raw"
                if m.group(1) in {"--post-file", "--body-file"}:
                    value = "@" + (value or "")
                if value and value.startswith("@") and not raw:
                    text = read_text(value[1:], cwd)
                    if text is None:
                        unread.append(f"the body file {value[1:]}")
                    else:
                        bodies.append(text)
                elif value is not None:
                    bodies.append(value)
            elif prog == "curl" and re.match(r"^(?:-F|--form|-T|--upload-file)", a):
                unread.append(f"`{a}`")
            j += 1
    else:  # httpie: query=… items; anything else is not read
        query = [a.split("=", 1)[1] for a in args if a.startswith("query=")]
        bodies = [json.dumps({"query": q}) for q in query]
    queries, variables = [], None
    for body in bodies:
        try:
            data = json.loads(body)
        except ValueError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("query"), str):
            queries.append(data["query"])
            variables = data.get("variables") if variables is None else variables
        else:
            unread.append("a request body that is not literal JSON with a query")
    return graphql_classes(where, queries, {}, variables, cwd, unread)


MCP_ADMIN_WORDS = [
    ("repo-admin-secrets", {"secret", "secrets", "variable", "variables", "key", "keys"}),
    ("repo-admin-protection", {"ruleset", "rulesets", "protection"}),
    ("repo-admin-ci", {"workflow", "workflows", "actions", "action", "dispatch", "rerun", "cache",
                       "caches", "job", "jobs", "pipeline", "pipelines"}),
]


def split_words(name):
    return set(re.split(r"[_\-\s]+", re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()))


def nested_texts(value, key_ok=False):
    """String leaves whose own key names text (lists pass the key on; dicts do not)."""
    if isinstance(value, str):
        return [value] if key_ok else []
    if isinstance(value, dict):
        out = []
        for k, v in value.items():
            out += nested_texts(v, text_key(k))
        return out
    if isinstance(value, list):
        return [t for v in value for t in nested_texts(v, key_ok)]
    return []


def comment_candidates(item):
    texts = item[2] if len(item) > 2 else None
    if texts is None:
        return []
    return [texts] if isinstance(texts, str) else list(texts)


def comment_texts(inp):
    """Comment text a call would post: text-named string fields, plus nested rich text joined."""
    texts = [v for k, v in inp.items() if isinstance(v, str) and text_key(k) and v.strip()]
    nested = [t for t in nested_texts({k: v for k, v in inp.items() if not isinstance(v, str)})
              if t.strip()]
    if nested:
        texts.append("".join(nested))
    return texts


def classify_mcp(tool_name, tool_input):
    m = re.match(r"^mcp__(.+?)__(.+)$", tool_name)
    if not m:
        return []
    server, words = m.group(1), split_words(m.group(2))
    inp = tool_input if isinstance(tool_input, dict) else {}
    if SEND_SERVER.search(server.replace("_", "-")) and not TRACKER_SERVER.search(server):
        calendar = re.search(r"calendar|gcal|calendly|zoom", server, re.I)
        chat = not re.search(r"mail|outlook|exchange|office365|smtp|sendgrid|postmark", server, re.I)
        chat_edit = chat and words & {"message", "messages", "post", "posts", "canvas",
                                      "canvases"} and \
            words & {"update", "edit", "delete", "remove", "create", "add", "share"}
        verbs = words & SEND_WORDS
        if words & READ_WORDS:  # `get_post`, `list_reactions` read; a send verb still sends
            verbs &= MAIL_VERBS | {"respond", "invite", "rsvp", "broadcast"}
        if verbs or calendar and words & CALENDAR_WRITES or chat_edit:
            return [("send", tool_name)]
        return []
    if not TRACKER_SERVER.search(server) and not GIT_SERVER.search(server) and (
            words & MAIL_VERBS and words & MAIL_OBJECTS or
            words & EVENT_VERBS and words & EVENT_OBJECTS):
        return [("send", tool_name)]
    if GIT_SERVER.search(server):
        if "commit" in words:
            return [("commit", tool_name)]
        if "push" in words:
            return [("push", tool_name)]
        if words & {"reset", "rebase", "merge", "checkout", "stash", "restore", "clean"}:
            return [("history", tool_name)]
        if not TRACKER_SERVER.search(server) or words & {"git", "status", "diff", "log"}:
            return []
    if not TRACKER_SERVER.search(server):
        return []
    if {"execute", "operator"} <= words:
        return classify_operator(tool_name, inp)
    if {"resolve", "assignees"} <= words:
        return []
    request = words & {"request", "requests"}
    verbs = words - request - ({"merge"} if request else set()) if words & {"pull", "merge"} and \
        request else words
    if words & READ_WORDS and not verbs & WRITE_WORDS:
        return []
    has_text = any(isinstance(v, str) and v.strip() and text_key(k) for k, v in inp.items())
    reviewish = words & {"review", "thread", "comment", "comments", "note", "notes"}
    if re.search(r"github|gitlab|gitkraken", server, re.I) and not reviewish:
        if request and words & {"pull", "merge", "mr"} and "create" in words:
            return [("pr", tool_name)] + mcp_pr_body(tool_name, inp)
        if request and words & {"pull", "merge", "mr"} and words & {"update", "edit"} and \
                isinstance(inp.get("body") or inp.get("description"), str):
            return [("tracker", tool_name)] + mcp_pr_body(tool_name, inp)
        if "merge" in words and (request or "mr" in words) and not words & {"update"}:
            return [("merge", tool_name)]
        # secrets, CI and protection first: create_repository_secret is no new repository
        for kind, names in MCP_ADMIN_WORDS:
            if words & names:
                return [(kind, tool_name)]
        objects = words & {"repository", "repo", "gist", "release", "releases"}
        kind = "publish-release" if objects & {"release", "releases"} else \
            "publish-gist" if "gist" in objects else "publish-repo"
        if objects and words & {"create", "fork", "publish"}:
            return [(kind, tool_name)]
        if objects and "delete" in words:
            return [("destructive" if kind == "publish-repo" else "repo-admin-delete", tool_name)]
        if objects and words & {"update", "edit"}:
            return [("repo-admin-settings" if kind == "publish-repo" else kind, tool_name)]
        if "push" in words or (words & {"file", "files", "branch", "tag"}
                               and words & {"create", "update", "delete", "push"}):
            return [("push", tool_name)]
    return classify_tracker_write(tool_name, words, inp, has_text)


def classify_tracker_write(name, words, inp, has_text):
    if words & COMMENT_WORDS or ("review" in words and has_text):
        if words & {"delete", "remove"}:
            return [("delete", name)]
        texts = comment_texts(inp)
        if texts:
            return [("comment", name, texts)]
        if words & {"create", "add", "post", "reply", "send", "write"}:
            return [("comment", name, None)]
    if words & {"delete", "remove", "archive", "merge"} and \
            words & {"task", "tasks", "issue", "issues", "page", "pages", "list", "folder",
                     "document", "doc", "docs", "project", "space"} and \
            not words & {"from", "tag", "tags", "label", "labels", "link", "links", "dependency",
                         "watcher", "watchers", "attachment", "requests"}:
        return [("destructive", name)]
    item = words & ITEM_WORDS
    if item and words & {"create", "add", "new", "duplicate"} and \
            not words & {"tag", "tags", "label", "labels", "link", "links", "dependency",
                         "watcher", "watchers", "attachment", "list", "time", "entry"}:
        return [("create-task", name)]
    if item and words & {"save", "upsert"} and not {"id", "issueId", "issue_id",
                                                     "identifier"} & set(inp):
        return [("create-task", name)]  # Linear save_issue without an id creates one
    if words & {"many", "bulk"}:
        return [("tracker", name)]
    changed = {k for k in inp if not id_key(k) or k in STATUS_KEYS}  # `stateId` is a change
    status_tool = bool(words & {"transition", "transitions"} or words & {"status", "state"} and
                       words & {"set", "update", "change", "move"} or
                       item and words & {"close", "reopen"})
    if changed <= STATUS_KEYS and (status_tool or changed and
                                   words & {"update", "edit", "save", "set", "upsert"}):
        return [("status", name, call_targets(inp))]
    texts = [v for k, v in inp.items() if re.match(r"comment", str(k), re.I) and
             isinstance(v, str) and v.strip()]
    extra = [("comment", f"{name} (comment)", texts)] if texts else []
    if status_tool or changed & STATUS_KEYS:
        return [("tracker", f"{name} (changes the status together with other fields — a "
                            "[status] command covers a call that changes only the status)")] + extra
    return [("tracker", name)] + extra


ITEM_WORDS = {"task", "tasks", "issue", "issues", "ticket", "tickets", "subtask", "subtasks",
              "story", "bug", "card"}
STATUS_KEYS = {"status", "state", "statusId", "stateId", "status_id", "state_id", "transition",
               "transitionId", "transition_id", "state_reason", "stateReason"}
ID_EXTRA = {"owner", "repo", "repository", "issue_number", "number", "key", "identifier",
            "issueIdOrKey", "custom_task_ids", "cloudId", "team_id", "workspace_id", "model",
            "operator"}


TARGET_SKIP = {"owner", "repo", "repository", "cloudId", "team_id", "workspace_id", "model",
               "operator", "custom_task_ids", "team"}


def call_targets(inp):
    """The ids a tracker call names its task by: `task_id`, `issueIdOrKey`, `issue_number`, …"""
    out = []
    for k, v in (inp or {}).items():
        if isinstance(v, dict):  # an operator's body
            out += call_targets(v)
        elif id_key(k) and k not in TARGET_SKIP | STATUS_KEYS and isinstance(v, (str, int)) \
                and not isinstance(v, bool):
            out.append(str(v))
    return out


def id_key(key):
    """A key that names what is changed, not a change: `task_id`, `issueIdOrKey`, `owner`."""
    key = str(key)
    return bool(ID_KEY.search(key)) or key in ID_EXTRA or key.endswith("IdOrKey")


def word_list(name):
    """`Task.getMany` → ["task", "get", "many"]."""
    return [w for w in re.split(r"[_\-\s.]+", re.sub(r"([a-z0-9])([A-Z])", r"\1_\2",
                                                     name).lower()) if w]


def reads_only(model, operator):
    """An operator that only reads: get, list, get_many, getMany, Task.Get, get_comments,
    list_children, search — its first word after the model's own is a read word and none is a
    write word. The live catalog had no operators enabled to check these names against."""
    words, own = word_list(operator), set(word_list(model))
    if not words or set(words) & WRITE_WORDS:
        return False
    first = next((w for w in words if w not in own), words[0])  # `Task.Get` → get
    return words[0] in READ_WORDS or first in READ_WORDS


def classify_operator(name, inp):
    """clickup_execute_operator: a `<model>.<operator>` pair — reads pass, writes are judged by
    the pair as a dedicated tool of that name would be."""
    model, operator = str(inp.get("model") or ""), str(inp.get("operator") or "")
    label = f"{name} {model}.{operator}"
    if reads_only(model, operator):
        return []
    words = split_words(model) | split_words(operator)
    body = inp.get("body") if isinstance(inp.get("body"), dict) else {}
    if words & COMMENT_WORDS and operator not in {"delete", "delete_many"}:
        texts = comment_texts(body)
        return [("comment", label, texts or None)]
    found = classify_tracker_write(label, words - {"children"}, body, bool(comment_texts(body)))
    return [(k, what, call_targets(inp)) if k == "status" else (k, what, *rest)
            for k, what, *rest in found]


# --- PowerShell ---------------------------------------------------------------------------

PS_HTTP = {"invoke-webrequest", "iwr", "invoke-restmethod", "irm", "curl", "wget"}
PS_RUNNERS = {"invoke-expression", "iex", "start-process", "saps", "start", "invoke-command",
              "icm", "cmd", "pwsh", "powershell", "bash", "sh", "wsl", "start-job", "sajb"}
PS_WRITERS = {"set-content", "sc", "add-content", "ac", "out-file", "new-item", "ni",
              "remove-item", "rm", "del", "erase", "ri", "rd", "rmdir", "move-item", "mv", "mi",
              "move", "copy-item", "cp", "cpi", "copy", "clear-content", "clc", "rename-item",
              "ren", "rni", "tee-object", "tee", "set-itemproperty", "sp", "export-csv",
              "export-clixml", "set-acl", "new-itemproperty"}


def ps_segments(command, keep_ops=False):
    """PowerShell statements as word lists: backtick escapes undone, split at ; | && || and line
    ends outside quotes, the call operators `&` and `.` dropped (kept with `keep_ops`) — also
    when written without a space (`&'git'`, `&("git")`, `.(…)`, `&{…}`). A script block
    (`{ git push }`, `&{…}`, `.{…}`, `ForEach-Object { … }`) and an array or a cast of a
    pipeline (`@(git push)`, `[void](git push)`, `(git push)`) are statements of their own;
    `${name}` stays a word, and `(…)` right after a call operator stays the program it names."""
    command = re.sub(r"`\r?\n", " ", command)
    command = re.sub(r"`(.)", r"\1", command)
    command = re.sub(r"(?:^|(?<=[\s;|{(]))([&.])(?=['\"($@{])", r"\1 ", command)
    out, word, words, quote, i, parens = [], "", [], None, 0, []
    while i < len(command):
        c = command[i]
        if not quote and (c in "{}" and not (c == "{" and word.endswith("$")) or
                          c == "(" and not words and (word in {"", "@"} or
                                                      re.fullmatch(r"(?:\[[^\]]*\])+", word)) or
                          c == ")" and parens and parens[-1]):
            if c == "(":
                parens.append(True)
                word = ""
            elif c == ")":
                parens.pop()
            if word:
                words.append(word)
            if words:
                out.append(words)
            word, words = "", []
            i += 1
            continue
        if not quote and c == "(":
            parens.append(False)
        elif not quote and c == ")" and parens:
            parens.pop()
        if c == "{" and word.endswith("$") and not quote:  # ${name}: one word up to its `}`
            end = command.find("}", i)
            end = len(command) - 1 if end < 0 else end
            word += command[i:end + 1]
            i = end + 1
            continue
        if quote:
            if c == quote:
                if command[i + 1:i + 2] == quote:  # '' inside '…'
                    word += c
                    i += 1
                else:
                    quote = None
            else:
                word += c
        elif c in "'\"":
            quote = c
        elif c in ";|\n" or command.startswith("&&", i):
            if word:
                words.append(word)
            if words:
                out.append(words)
            word, words = "", []
            if command.startswith(("&&", "||"), i):
                i += 1
        elif c.isspace():
            if word:
                words.append(word)
            word = ""
        else:
            word += c
        i += 1
    if word:
        words.append(word)
    if words:
        out.append(words)
    if keep_ops:
        return out
    return [[w for w in seg if w not in {"&", "."}] or seg for seg in out]


def ps_program(word):
    word = re.sub(r"^(?:\[[^\]]*\])*[@$]?\(+", "", word)  # @(git, $(git, [void](git
    name = re.split(r"[\\/]", word.strip("() "))[-1].lower()
    return re.sub(r"\.(?:exe|cmd|bat|ps1)$", "", name)


# Start-Process parameters around the program's own arguments
PS_PROCESS_FLAGS = {"-argumentlist", "-args", "-wait", "-nonewwindow", "-passthru", "-filepath",
                    "-loaduserprofile", "-usenewenvironment", "-noprofile", "-noninteractive"}
PS_PROCESS_VALUES = {"-windowstyle", "-workingdirectory", "-verb", "-credential",
                     "-redirectstandardoutput", "-redirectstandarderror", "-redirectstandardinput"}
# a string PowerShell runs that is not a plain literal: `iex $cmd`, `iex ('git ' + 'push')`
PS_IEX_BUILT = re.compile(r"(?i)(?<![\w-])(?:invoke-expression|iex)\s+(?:-command\s+)?"
                          r"(?!'[^'\n]*'\s*(?:$|[;|)}\n]))(?!\"[^\"$`\n]*\"\s*(?:$|[;|)}\n]))\S")
PS_ENCODED = re.compile(r"(?i)^-(?:e|ec|en|enc|enco\w*|encodedcommand|encodedarguments|ea)$")


def ps_words(args):
    """A program's arguments as Start-Process passes them: 'push','origin' and @('push') split
    into words, its own parameters dropped."""
    words = [w for w in re.split(r"[,'\"@()\s]+", " ".join(args)) if w]
    out, skip = [], False
    for w in words:
        if skip:
            skip = False
        elif w.lower() in PS_PROCESS_VALUES:
            skip = True
        elif w.lower() not in PS_PROCESS_FLAGS:
            out.append(w)
    return out


def ps_expression_end(seg):
    """The index of the word that closes the `(…)` expression `seg` starts with."""
    depth = 0
    for k, w in enumerate(seg):
        depth += w.count("(") - w.count(")")
        if depth <= 0:
            return k
    return len(seg) - 1


def ps_call_opaque(program, rest, called):
    """Whether a program a PowerShell statement calls through `&` / `.` or names by `$var` /
    `(…)` may be git. A literal name decides by itself (`git` there is read as git); so does
    `(Get-Command <name>)`, a literal basename after an expansion (`"$root\\build.ps1"`,
    `(Join-Path $x b.ps1)`) or words naming another tool (`$python`). Anything else after a
    call operator is opaque; a bare `$var` statement is judged as in Bash."""
    text = program.strip()
    inner = text[1:-1].strip() if text.startswith("(") and text.endswith(")") else text
    if not re.search(r"[$()+\[\s*?]", inner):
        return False  # a literal program name
    m = re.fullmatch(r"(?i)(?:get-command|gcm)\s+(?:-name\s+)?['\"]?([\w.\\/:*?\[\]-]+)['\"]?"
                     r"(?:\s+-\w+)*", inner)
    if m:  # a wildcard may match git: `(Get-Command gi*)`
        name = ps_program(m.group(1))
        return any(fnmatch.fnmatchcase(g, name) for g in ("git", "gh"))
    built = re.search(r"(?i)\+|-(?:join|replace|f|split|creplace)\b|\[char|\.(?:replace|substring|"
                      r"insert|trim|tolower|toupper)\b|\$\(", inner)
    words = {w.lower() for w in re.findall(r"[A-Za-z0-9]+", inner)}
    if words & OTHER_TOOL_WORDS and not built:
        return False
    if built:  # a name put together at run time: `("gi" + "t")`, `"gi$('t')"`
        return called or plausibly_git("$" + text if text.startswith("(") else text, rest)
    last = re.split(r"[\\/\s'\"]+", inner.rstrip(") '\""))[-1]
    if re.search(r"[\\/\s]", inner) and not re.search(r"[$()+\[]", last) or \
            re.search(r"(?i)\.(?:ps1|psm1|exe|cmd|bat)$", last) and \
            not re.search(r"[$()+]", last):
        return ps_program(last) == "git"
    if not called:
        return plausibly_git("$" + text if text.startswith("(") else text, rest)
    return True


# PowerShell ways to set an environment variable: $env:X = …, ${env:X} = …, Set-Item env:X …,
# New-Item -Path Env:X …, [Environment]::SetEnvironmentVariable('X', …)
PS_ENV_SET = [
    re.compile(r"(?i)\$\{?env:([\w$()]+)\}?\s*[+]?=(?!=)\s*([^;\n|]*)"),
    re.compile(r"(?i)(?<![\w-])(?:set-item|si|new-item|ni|set-content|sc|add-content|ac)\s+"
               r"(?:-(?:literal)?path\s+)?['\"]?env:[\\/]?([^\s'\"]+)['\"]?\s+(?:-value\s+)?"
               r"([^;\n|]*)"),
    re.compile(r"(?i)\[(?:system\.)?environment\]::setenvironmentvariable\(\s*([^,]*),\s*"
               r"((?:'[^']*'|\"[^\"]*\"|[^,)])*)"),
]


def ps_risky_env(command):
    """The first PowerShell assignment of an environment variable that changes git's settings
    or makes it run a program (as risky_git_env reads `NAME=value` in Bash), or None. A name or
    a value that is not a literal counts as risky."""
    plain = re.sub(r"`(.)", r"\1", command)
    for rx in PS_ENV_SET:
        for m in rx.finditer(plain):
            name, value = m.group(1).strip().strip("'\""), m.group(2).strip()
            literal = re.fullmatch(r"'([^']*)'|\"([^\"$`]*)\"|([^\s'\"$`(]*)", value)
            value = next((g for g in literal.groups() if g is not None), "") if literal \
                else "$" + value
            # Windows names are case-insensitive: $env:Git_Ssh_Command is GIT_SSH_COMMAND
            if not re.fullmatch(r"\w+", name) or risky_git_env(f"{name.upper()}={value}"):
                return m.group(0).strip()
    return cmd_risky_env(plain) if CMD_CARRIER.search(plain) else None


def ps_gh_env(command, cwd):
    """GH_CONFIG_DIR / XDG_CONFIG_HOME a PowerShell command (or a cmd `set` in it) sets."""
    plain, pairs = re.sub(r"`(.)", r"\1", command), []
    for rx in PS_ENV_SET:
        for m in rx.finditer(plain):
            name, value = m.group(1).strip().strip("'\""), m.group(2).strip()
            literal = re.fullmatch(r"'([^']*)'|\"([^\"$`]*)\"|([^\s'\"$`(]*)", value)
            if not re.fullmatch(r"\w+", name):  # a name built at run time may be either
                pairs.append(("GH_CONFIG_DIR", "$" + name))
            else:
                pairs.append((name, next((g for g in literal.groups() if g is not None), "")
                              if literal else "$" + value))
    if CMD_CARRIER.search(plain):
        pairs += [(m.group(1), m.group(2).strip()) for m in CMD_SET.finditer(plain)]
    return gh_env_of(pairs, cwd)


# cmd.exe: `cmd /c "set GIT_SSH_COMMAND=x && git fetch"`, `set /p GIT_DIR=<f`, `set "X=y"`
CMD_CARRIER = re.compile(r"(?i)(?<![\w.-])cmd(?:\.exe)?['\"]?\s+/[a-z]")
CMD_SET = re.compile(r"(?i)(?<![\w-])set\s+(?:/[ap]\s+)?\"?([^\s=\"&|]+)=([^&|\n\"]*)")


def cmd_risky_env(text):
    """The first cmd.exe `set NAME=value` that changes git's settings or makes it run a program
    (names without case, as Windows reads them), or None. `set /p` reads the value at run time."""
    for m in CMD_SET.finditer(text):
        name, value = m.group(1), m.group(2).strip()
        if "/p" in m.group(0).lower().split() or "%" in value or "!" in value:
            value = "$" + value
        if not re.fullmatch(r"\w+", name) or risky_git_env(f"{name.upper()}={value}"):
            return m.group(0).strip()
    return None


def classify_powershell(command, cwd):
    """The PowerShell tool, read like Bash where it can be: git and gh words anywhere in a
    statement — also as Start-Process arguments ('push','origin', @('push'), `-FilePath git.exe
    -ArgumentList 'push origin'`) and `& (Get-Command git) push` — and curl / Invoke-WebRequest
    to a tracker or a webhook. What runs through a literal string (`Invoke-Expression "…"`,
    `cmd /c`) is read for git and gh words. Opaque: a program named by a variable or a
    `(…)` expression that may be git, Invoke-Expression of a built string, an encoded command."""
    found = []
    if PS_IEX_BUILT.search(re.sub(r"`(.)", r"\1", command)):
        found.append(("opaque", "Invoke-Expression of a string built at run time"))
    for inner in re.findall(r"\$\(((?:[^()]|\([^()]*\))*)\)", re.sub(r"`(.)", r"\1", command)):
        found += classify_powershell(inner, cwd)  # $(…), also inside "…": it runs
    risky = ps_risky_env(command)
    if risky and re.search(r"(?i)(?<![\w-])git(?:\.exe)?(?![\w-])", command):
        found.append(("opaque", f"git run with `{risky[:60]}` from the environment: it can run a "
                                "program or change git's settings"))
    for seg, raw in zip(ps_segments(command), ps_segments(command, keep_ops=True)):
        prog = ps_program(seg[0])
        args = seg[1:]
        named = False
        for j, w in enumerate(seg):
            name = ps_program(w)
            if name not in {"git", "gh"}:
                continue
            named = True
            splat = next((a for a in seg[j + 1:] if re.match(r"^@\w", a)), None)
            if splat:  # git @args: the arguments are an array the guard never sees
                found.append(("opaque", f"`{name} {splat}`: arguments splatted from a variable"))
            run = (lambda a: classify_git(a, cwd, 0, {})) if name == "git" else \
                (lambda a: classify_gh(a, cwd, env=ps_gh_env(command, cwd)))
            res = run(seg[j + 1:])
            split = ps_words(seg[j + 1:])
            # Start-Process git -ArgumentList 'push','origin'; git @('push', 'origin')
            if not res and split != seg[j + 1:] and (j or any(
                    re.match(r"^@\(|.*,", w) for w in seg[j + 1:])):
                res = run(split)
            found += res
        if prog in PS_RUNNERS:
            found += raw_scan(" ".join(args), cwd, 0)
        if prog in {"pwsh", "powershell"} and any(PS_ENCODED.match(a) for a in args):
            found.append(("opaque", f"`{seg[0]} -EncodedCommand …`: an encoded command"))
        called = raw[0] in {"&", "."} and len(raw) > 1
        if (called or seg[0].startswith(("$", "("))) and not named:
            # `& $g push`, `& (Get-Command x) push`, `&("gi"+"t") push`, `. $script`
            end = ps_expression_end(seg) if seg[0].startswith("(") else 0
            program, rest = " ".join(seg[:end + 1]), seg[end + 1:]
            if ps_call_opaque(program, rest, called):
                found.append(("opaque", f"`{program[:60]} …`: a program named by a variable or "
                                        "an expression that may be git"))
        if prog in PS_HTTP:
            method = (option_value([a.lower() for a in args], ["-method"]) or "").upper()
            body = any(a.lower() in {"-body", "-infile", "-form"} for a in args)
            if prog in {"curl", "wget"} and not any(a.lower().startswith("-method") for a in args):
                found += classify_http(prog, args, cwd)  # curl.exe, not the Invoke-WebRequest alias
            elif method in WRITE_METHODS or body:
                found += classify_http("curl", ["-X", "POST"] + [a for a in args if "://" in a])
    return found


PS_CD = {"cd", "set-location", "sl", "chdir", "push-location", "pushd"}
PS_COPIERS = {"copy-item", "cp", "cpi", "copy", "move-item", "mv", "mi", "move"}
PS_DELETERS = {"remove-item", "rm", "del", "erase", "ri", "rd", "rmdir", "move-item", "mv", "mi",
               "move"}
# .NET file writes: [IO.File]::WriteAllText("…", …), [System.IO.File]::Copy(a, b), …
PS_DOTNET = re.compile(r"(?i)\[(?:system\.)?io\.(?:file|directory)\]::\s*(\w+)\s*\((.*)")
PS_ARG = re.compile(r"'((?:[^']|'')*)'|\"([^\"]*)\"|(\$[\w:{}]+[^\s,)]*)")


def ps_opt(args, names):
    """A PowerShell parameter's value, the name matched without case."""
    for i, a in enumerate(args[:-1]):
        if a.lower() in names:
            return args[i + 1]
    return None


def ps_positional(args):
    out, i = [], 0
    while i < len(args):
        if args[i].startswith("-") and len(args[i]) > 1:
            i += 1 if args[i].lower() in {"-recurse", "-force", "-confirm", "-whatif",
                                          "-passthru", "-container"} else 2
            continue
        out.append(args[i])
        i += 1
    return out


def tamper_powershell(command, cwd, protected, marks):
    """A PowerShell statement that writes, moves or deletes a protected path, names the guard's
    markers, writes git settings, or redirects into such a path — in order, after the cwd an
    earlier Set-Location / cd moved to; Copy-Item / Move-Item into a directory as DIR/<name>;
    .NET [IO.File] writes. A `$name = '…'` literal assigned in the command is read where the
    variable is used; another variable is judged by the names that matter."""
    plain = re.sub(r"`(.)", r"\1", command)
    known = {m.group(1).lower(): m.group(3) for m in re.finditer(
        r"\$(\w+)\s*=\s*(['\"])([^'\"\n]*)\2", plain)}

    def norm(p):
        p = re.sub(r"\$(\w+)", lambda m: known.get(m.group(1).lower(), m.group(0)), p)
        p = re.sub(r"^\$(?:env:)?(?:HOME|USERPROFILE)(?=[\\/]|$)", "~", p, flags=re.I)
        return p.replace("\\", "/")

    def hits(p, deleting=False):
        p = norm(p)
        return any(m in fold(p) for m in marks) or touches(p, cwd, protected, deleting)

    for m in PS_DOTNET.finditer(plain):
        method, rest = m.group(1).lower(), m.group(2)
        if re.match(r"(?:write|append|copy|move|delete|replace|create|open|set|encrypt)", method):
            literals = [a or b or c for a, b, c in PS_ARG.findall(rest.split(";")[0])]
            if any(hits(a.replace("''", "'"), method.startswith(("delete", "move")))
                   for a in literals):
                return True
    for seg in ps_segments(command):
        prog = ps_program(seg[0])
        args = seg[1:]
        if prog in PS_CD:
            where = ps_opt(args, {"-path", "-literalpath"}) or next(iter(ps_positional(args)), None)
            if where:
                cwd = resolve_path(norm(where), cwd) or UNKNOWN_CWD
            continue
        if prog == "git" and (config_tamper(args) or any(
                hits(p) for p in git_file_writes(args, cwd)[0])):
            return True
        kind = (ps_opt(args, {"-itemtype", "-type"}) or "").lower()
        target = ps_opt(args, {"-target", "-value"})
        if prog in {"new-item", "ni"} and kind in {"symboliclink", "junction", "hardlink"} and \
                target and link_to_protected(norm(target), cwd, protected):
            return True
        if prog == "claude" and {"plugin", "plugins"} & set(seg) and not {
                "list", "validate", "--help", "-h"} & set(seg):
            return True
        paths = [w for w in args if not w.startswith("-")]
        redirects = [seg[j + 1] for j, w in enumerate(seg[:-1]) if re.fullmatch(r"\d?>>?", w)]
        redirects += [w.lstrip("0123456789>") for w in seg if re.match(r"^\d?>>?\S", w)]
        writer = prog in PS_WRITERS
        for p in redirects + (paths if writer else []):
            if hits(p, prog in PS_DELETERS):
                return True
        if prog in PS_COPIERS:
            src = ps_opt(args, {"-path", "-literalpath"})
            dest = ps_opt(args, {"-destination"})
            pos = ps_positional(args)
            src = [src] if src else pos[:1]
            dest = dest or (pos[1] if len(pos) > 1 else None)
            if dest:
                parts = [norm(p) for p in src + [dest]]
                if any(hits(t) for t in copy_targets(parts)) or (
                        "-recurse" in {a.lower() for a in args} and
                        fills_protected_dir(parts, "cp", cwd, protected)):
                    return True
        if writer is False and prog not in READERS and any(m in fold(w) for w in seg for m in marks):
            return True
    return False


# --- tampering with the guard -------------------------------------------------------------

CONFIG_DIR = os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude")
PLUGIN_ROOT = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
SETTINGS_NAME = re.compile(r"^(?:settings[^/]*|managed-settings)\.json$", re.I)


def fold(path):
    """A path compared as the file system does on macOS and Windows: without case
    (`.GIT/config` is `.git/config` there). On a case-sensitive file system this only refuses a
    few more names."""
    return path.lower() if isinstance(path, str) else path


def settings_file(path, cwd=None):
    """Whether `path` is a Claude Code settings file: settings*.json in the config dir or in any
    `.claude` dir, or managed-settings.json — checked on the path as written and on its realpath,
    and against the realpaths of the known settings files (a dotfiles symlink target)."""
    if not path:
        return False
    written = os.path.abspath(os.path.join(cwd or os.getcwd(), os.path.expanduser(path)))
    real = os.path.realpath(written)
    config = {fold(os.path.abspath(CONFIG_DIR)), fold(os.path.realpath(CONFIG_DIR))}
    for p in (fold(written), fold(real)):
        name, parent = os.path.basename(p), os.path.dirname(p)
        if SETTINGS_NAME.match(name) and (name.startswith("managed-") or parent in config
                                          or os.path.basename(parent) == ".claude"):
            return True
    return fold(real) in known_settings(cwd)


_KNOWN_SETTINGS = {}


def known_settings(cwd):
    """Realpaths of the settings files that exist for this config dir and project."""
    if cwd not in _KNOWN_SETTINGS:
        dirs = [CONFIG_DIR, os.path.join(cwd or os.getcwd(), ".claude")]
        top = git_query(["rev-parse", "--show-toplevel"], cwd, None) if cwd else None
        if top:
            dirs.append(os.path.join(top, ".claude"))
        found = set()
        for d in dirs:
            try:
                names = os.listdir(d)
            except OSError:
                continue
            found |= {fold(os.path.realpath(os.path.join(d, n))) for n in names
                      if SETTINGS_NAME.match(n)}
        _KNOWN_SETTINGS[cwd] = found
    return _KNOWN_SETTINGS[cwd]
DELETERS = {"rm", "rmdir", "unlink", "shred", "mv", "trash", "srm"}
MODIFIERS = {"truncate", "chmod", "chown", "chgrp", "chflags", "touch", "xattr", "setfacl", "tee"}
COPIERS = {"cp", "install", "ln", "rsync", "ditto", "scp"}  # the last argument is written
IN_PLACE = {"sed", "gsed", "perl"}
READERS = {"cat", "head", "tail", "less", "more", "ls", "stat", "file", "wc", "grep", "egrep",
           "fgrep", "rg", "ag", "test", "[", "echo", "printf", "readlink", "realpath", "du", "diff",
           "cmp", "jq", "sort", "uniq", "cut", "awk", "sed", "gsed", "tree", "md5", "md5sum",
           "shasum", "sha1sum", "sha256sum", "xxd", "od", "basename", "dirname", "cd", "pushd",
           "find", "true", "column", "nl", "strings", "hexdump", "bat", "fd", "eza", "exa", "tac",
           "cksum", "git", "gh", "open"}


def protected_paths(data):
    """(paths a call may not write, paths a call may not delete or move along with a parent)."""
    paths = [MARK_DIR, os.path.join(PLUGIN_ROOT, "skills", "ticket"),
             os.path.join(PLUGIN_ROOT, "hooks"), os.path.join(CONFIG_DIR, "plugins")]
    transcript = (data or {}).get("transcript_path")
    if isinstance(transcript, str) and transcript.endswith(".jsonl"):
        paths += [transcript, transcript[:-len(".jsonl")]]  # and its subagent transcripts
    return [fold(os.path.realpath(os.path.expanduser(p))) for p in paths]


def session_marks(data):
    """Strings that name this session's transcript or the guard's markers wherever they appear."""
    session = re.sub(r"[^A-Za-z0-9_-]", "", (data or {}).get("session_id") or "")
    return ["task-runs/.guard"] + ([fold(session) + ".jsonl"] if len(session) >= 8 else [])


UNKNOWN_CWD = "$PWD"  # after a `cd` the guard cannot follow: relative paths are judged by name


def resolve_path(token, cwd):
    token = re.sub(r"^\$\{?HOME\}?(?=/|$)", os.path.expanduser("~"), token)
    token = os.path.expanduser(token)
    if "$" in token or "`" in token or cwd == UNKNOWN_CWD and not os.path.isabs(token):
        return None
    return os.path.realpath(os.path.join(cwd or os.getcwd(), token))


def touches(token, cwd, protected, deleting):
    """Whether writing (deleting, when `deleting`) the path `token` reaches a protected path."""
    path = resolve_path(token, cwd)
    if path is None:  # $VAR we cannot expand: judge by the names that matter only
        return bool(re.search(r"(?:^|/)\.guard(?:/|$)|\.claude/plugins(?:/|$)|(?:^|/)(?:settings[^/]*|"
                              r"managed-settings)\.json$|(?:^|/)guard\.py$|skills/ticket(?:/|$)|"
                              r"hooks/(?:hooks\.json|subagent-guard\.sh)$", token, re.I)) or \
            not deleting and bool(GIT_SETTINGS_NAME.search(token)) or \
            deleting and bool(re.search(r"(?:^|/)task-runs/?$", token, re.I))
    written = re.sub(r"^\$\{?HOME\}?(?=/|$)", os.path.expanduser("~"), token)
    if settings_file(written, cwd) or settings_file(path, cwd):
        return True
    if not deleting and (git_settings_file(path, cwd) or git_settings_file(
            os.path.abspath(os.path.join(cwd or os.getcwd(), os.path.expanduser(written))), cwd)):
        return True
    glob = re.search(r"[*?\[]", path)
    if glob:
        base = os.path.dirname(path[:glob.start()] + "x")
        if touches(base + "/x", cwd, protected, False):
            return True
    path = fold(path)
    for p in protected:
        if path == p or path.startswith(p + "/"):
            return True
        if deleting and p.startswith(path.rstrip("/") + "/"):
            return True
        if glob and any(fnmatch.fnmatchcase(c, path) for c in ([p] + (ancestors(p)
                                                                       if deleting else []))):
            return True
    return False


# git's own settings and hooks, by name: <gitdir>/config, config.worktree, hooks/*,
# info/attributes (also under worktrees/ and modules/), ~/.gitconfig, ~/.config/git/config
GIT_SETTINGS_NAME = re.compile(
    r"(?:^|/)\.git/(?:[^/]+/)*(?:config|config\.worktree|hooks/[^/]+|info/attributes)$|"
    r"(?:^|/)\.gitconfig$|(?:^|/)git/config$|(?:^|/)\.git/(?:[^/]+/)*hooks/?$|"
    r"(?:^|/)gh/config\.yml$|(?:^|/)GitHub CLI/config\.yml$", re.I)
# a driver git runs as a program; Git LFS (filter=lfs diff=lfs merge=lfs) and the built-in merge
# drivers run nothing the user has not installed
GIT_ATTR_DRIVER = re.compile(r"(?:^|[\s\\'\"])(?:filter|diff|merge)\s*=\s*"
                             r"(?!(?:lfs|text|binary|union)(?![\w.-]))", re.M)


def global_git_files():
    home = os.path.expanduser("~")
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    files = [os.path.join(home, ".gitconfig"), os.path.join(xdg, "git", "config"),
             os.path.join(home, ".config", "git", "config"), "/etc/gitconfig"]
    if os.environ.get("GIT_CONFIG_GLOBAL"):
        files.append(os.path.expanduser(os.environ["GIT_CONFIG_GLOBAL"]))
    files.append(gh_config_file())  # gh aliases
    return {fold(os.path.realpath(f)) for f in files}


def git_settings_file(path, cwd):
    """Whether writing `path` changes git's settings or hooks: a config of the repository, any
    worktree or submodule, a hook (also under core.hooksPath), info/attributes, the global
    config. Checked by name and against this repository's git dirs."""
    if not path:
        return False
    if GIT_SETTINGS_NAME.search(path):
        return True
    real = fold(os.path.realpath(path))
    if real in global_git_files():
        return True
    dirs, hooks = git_dirs(cwd)
    if any(real == h or real.startswith(h + "/") for h in hooks):
        return True
    for d in dirs:
        if real.startswith(d + "/") and re.fullmatch(
                r"(?:(?:worktrees|modules)/.+/)?(?:config|config\.worktree|hooks/[^/]+|hooks|"
                r"info/attributes)", real[len(d) + 1:]):
            return True
    return False


_GIT_DIRS = {}


def git_dirs(cwd):
    """(this repository's git dir and common dir, its core.hooksPath dir) as real paths."""
    if cwd not in _GIT_DIRS:
        dirs, hooks = set(), set()
        if cwd and cwd != UNKNOWN_CWD:
            for args in (["rev-parse", "--git-dir"], ["rev-parse", "--git-common-dir"]):
                out = git_query(args, cwd, None)
                if out:
                    dirs.add(fold(os.path.realpath(os.path.join(cwd, out))))
            out = git_query(["config", "--get", "core.hooksPath"], cwd, None)
            if out:
                hooks.add(fold(os.path.realpath(os.path.join(cwd, os.path.expanduser(out)))))
        _GIT_DIRS[cwd] = (dirs, hooks)
    return _GIT_DIRS[cwd]


def ancestors(path):
    out = []
    while os.path.dirname(path) != path:
        path = os.path.dirname(path)
        out.append(path)
    return out


def shell_tokens(command):
    lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=";&|()<>")
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def copy_parts(args, prog="cp"):
    """(destination, sources) of cp / mv / install / ln / rsync, or (None, [])."""
    positional, target, i = [], None, 0
    dash_t = prog in {"cp", "mv", "install", "ln", "gcp", "gmv"}  # rsync -t keeps times
    while i < len(args):
        a = args[i]
        if dash_t and a in {"-t", "--target-directory"} and i + 1 < len(args):
            target, i = args[i + 1], i + 2
            continue
        if a.startswith("--target-directory="):
            target = a.split("=", 1)[1]
        elif dash_t and a.startswith("-t") and len(a) > 2 and not a.startswith("--"):
            target = a[2:]
        elif not a.startswith("-"):
            positional.append(a)
        i += 1
    if target is None:
        if len(positional) < 1:
            return None, []
        target, positional = positional[-1], positional[:-1]
    return target, positional


def copy_targets(args, prog="cp"):
    """What cp / mv / install / ln / rsync write: the destination, and DIR/<name> of each source
    when it is a directory (`cp x/settings.json ~/.claude/`, `cp -t ~/.claude x/settings.json`)."""
    target, sources = copy_parts(args, prog)
    if target is None:
        return []
    names = {os.path.basename(p.rstrip("/")) for p in sources if p.rstrip("/")}
    return [target] + [os.path.join(target, n) for n in sorted(names)]


def fills_protected_dir(args, prog, cwd, protected):
    """A directory's contents copied into the config dir, or into a directory of the config dir or
    the plugin that holds a protected path: `cp -r x/ ~/.claude/`, `rsync -a evil/ ~/.claude/` —
    whatever the source holds, settings.json included, lands there."""
    target, sources = copy_parts(args, prog)
    dest = resolve_path(target, cwd) if target else None
    if dest is None:
        return False
    contents = [p for p in sources if re.search(r"/\.?$|/\*$", p) or
                os.path.isdir(resolve_path(p, cwd) or "")]
    if not contents:
        return False
    config, root, dest = fold(os.path.realpath(CONFIG_DIR)), fold(PLUGIN_ROOT), fold(dest)
    inside = dest == config or dest.startswith((config + "/", root + "/")) or dest == root
    return inside and (dest == config or any(p == dest or p.startswith(dest.rstrip("/") + "/")
                                              for p in protected))


def link_to_protected(src, cwd, protected):
    """Whether a link made to `src` reaches a protected path, git's settings or a settings dir:
    `ln -s .git x` and then `echo … > x/config` in the same command writes .git/config through a
    name the guard does not know yet."""
    if re.search(r"(?:^|[/\\])\.(?:git|claude)(?:[/\\]|$)", src, re.I):
        return True
    path = resolve_path(src, cwd)
    if path is None:
        return False
    f = fold(path).rstrip("/") or "/"
    dirs, hooks = git_dirs(cwd)
    reach = set(protected) | dirs | hooks | global_git_files() | {
        fold(os.path.realpath(CONFIG_DIR))}
    return any(r == f or r.startswith(f + "/") or f.startswith(r + "/") for r in reach) or \
        touches(src, cwd, protected, False)


# git that writes files where an option or a patch says, beyond its own worktree
GIT_OUTPUT_SUBS = {"diff", "log", "show", "diff-tree", "diff-index", "diff-files", "range-diff",
                   "whatchanged", "archive"}
GIT_PATH_WRITERS = GIT_OUTPUT_SUBS - {"diff", "log", "show"} | {
    "checkout-index", "apply", "am", "bundle", "format-patch"}
TAR_VALUE_LETTERS = set("fCbTXKLNgV")  # tar letters that take a value (`tar -C dir -xf a.tar`)


def git_split(args):
    """(the -C directory or None, the subcommand, its arguments) of `git <args>`."""
    dash_c, i = None, 0
    while i < len(args) and args[i].startswith("-"):
        opt = args[i].split("=", 1)[0]
        if opt in GIT_OPTS_WITH_VALUE:
            if opt == "-C" and i + 1 < len(args):
                dash_c = os.path.join(dash_c, args[i + 1]) if dash_c else args[i + 1]
            i += 1 if "=" in args[i] and opt.startswith("--") else 2
        else:
            i += 1
    return dash_c, (args[i] if i < len(args) else ""), args[i + 1:]


def opt_values(args, longs, shorts=()):
    """The values of `--long=v`, `--long v`, `-s v` and `-sv` up to `--`."""
    out, j = [], 0
    while j < len(args) and args[j] != "--":
        a, nxt = args[j], args[j + 1] if j + 1 < len(args) else ""
        name, eq, value = a.partition("=")
        if a.startswith("--") and name in longs:
            out.append(value if eq else nxt)
            j += 0 if eq else 1
        elif a in shorts:
            out.append(nxt)
            j += 1
        elif any(a.startswith(sh) and len(a) > len(sh) for sh in shorts):
            out.append(a[2:])
        j += 1
    return out


def patch_paths(text, strip):
    """The paths a patch (or an mbox of them) creates, changes, deletes or renames, with
    `strip` leading components taken off as `git apply -p<strip>` does."""
    out = []

    def cut(p):
        parts = p.strip('"').split("/")
        return "/".join(parts[strip:]) if len(parts) > strip else None

    for m in re.finditer(r"^(?:(?:\+\+\+|---) (\S+)|diff --git (\S+) (\S+)|"
                         r"(?:rename|copy) (?:from|to) (.+))$", text, re.M):
        for p in m.group(1, 2, 3):
            if p and p != "/dev/null" and cut(p):
                out.append(cut(p))
        if m.group(4):
            out.append(m.group(4).strip())
    return out


def git_file_writes(args, cwd):
    """Files `git <args>` writes at a path an option or a patch names: checkout-index
    --prefix (each tracked file under it), archive / diff / log --output, format-patch -o,
    bundle create, apply / am with the paths inside the patch under --directory. Returns
    (paths to check as written, what the guard cannot read — a path built at run time, a
    --directory outside the repository, a patch it cannot open — or None)."""
    dash_c, sub, rest = git_split(args)
    where = os.path.join(cwd or os.getcwd(), os.path.expanduser(dash_c)) if dash_c else \
        (cwd or os.getcwd())
    out, unread = [], []
    lit = lambda v: bool(v) and not re.search(r"[$`]", v)  # noqa: E731
    at = lambda p: os.path.join(where, os.path.expanduser(p))  # noqa: E731
    named = []
    if sub == "checkout-index":
        named = opt_values(rest, {"--prefix"})
    elif sub in GIT_OUTPUT_SUBS:
        named = opt_values(rest, {"--output"}, ("-o",) if sub == "archive" else ())
    elif sub == "format-patch":
        named = [os.path.join(d, "x") for d in opt_values(rest, {"--output-directory"}, ("-o",))]
    elif sub == "bundle" and rest[:1] == ["create"] and len(rest) > 1:
        named = [next((a for a in rest[1:] if not a.startswith("-")), "-")]
        named = [] if named == ["-"] else named
    for p in named:
        if not lit(p):
            unread.append(f"{sub} … {p[:40]}")
        elif sub != "checkout-index":
            out.append(at(p))
        else:
            values = {v for v in opt_values(rest, {"--prefix"})}
            files = [a for a in rest if not a.startswith("-") and a not in values]
            if not files or {"-a", "--all", "--stdin"} & set(rest):
                files += (git_query(["ls-files"], where, None) or "").splitlines()[:5000]
            # `config` too: the tracked files may not be listed (a stage, --stdin, no repo)
            out += [at(p + f) for f in files + ["x", "config"]]
    if sub in {"apply", "am"}:
        top = git_query(["rev-parse", "--show-toplevel"], where, None) or where
        directory = (opt_values(rest, {"--directory"}) or [""])[-1]
        strip = (opt_values(rest, set(), ("-p",)) or ["1"])[-1]
        strip = int(strip) if strip.isdigit() else 1
        root = os.path.normpath(os.path.join(top, directory))
        if directory and not lit(directory):
            unread.append(f"{sub} --directory={directory[:40]}")
        elif directory and not (root == os.path.normpath(top) or
                                root.startswith(os.path.normpath(top) + os.sep)):
            unread.append(f"{sub} --directory={directory[:40]} (outside the repository)")
        values = {v for v in opt_values(rest, {"--directory", "--exclude", "--include",
                                               "--whitespace", "--build-fake-ancestor",
                                               "--patch-format", "--resolvemsg"})}
        patches = [a for a in rest if not a.startswith("-") and a not in values]
        if not patches and "--unsafe-paths" in rest:
            unread.append(f"{sub} --unsafe-paths with a patch from stdin")
        for f in patches:
            text = read_text(f, where, 8 << 20)
            if text is None:
                unread.append(f"{sub} {f[:40]} (a patch the guard cannot read)")
                continue
            paths = patch_paths(text, strip)
            out += [os.path.join(root, p) for p in paths]
            if any(os.path.basename(p) == ".gitattributes" for p in paths) and \
                    GIT_ATTR_DRIVER.search(text):
                out.append(os.path.join(top, ".git", "info", "attributes"))  # a filter driver
    return out, ("; ".join(unread) or None)


def tar_parts(args):
    """(whether `tar <args>` extracts, the -C directory or None, the -f archive or None)."""
    extract, directory, archive = False, None, None
    if args and re.fullmatch(r"[A-Za-z]+", args[0]):  # old style: `tar xzf a.tar -C dir`
        bundle, rest, k = args[0], args[1:], 0
        extract = "x" in bundle
        for ch in bundle:
            if ch in TAR_VALUE_LETTERS and k < len(rest):
                directory = rest[k] if ch == "C" else directory
                archive = rest[k] if ch == "f" else archive
                k += 1
        args = rest[k:]
    j = 0
    while j < len(args):
        a, nxt = args[j], args[j + 1] if j + 1 < len(args) else None
        name, eq, value = a.partition("=")
        if a in {"-x", "--extract", "--get"}:
            extract = True
        elif name in {"--directory", "--file"}:
            v = value if eq else nxt
            j += 0 if eq else 1
            directory, archive = (v, archive) if name == "--directory" else (directory, v)
        elif re.fullmatch(r"-[A-Za-z]+", a):
            letters = a[1:]
            extract = extract or "x" in letters
            for n, ch in enumerate(letters):
                if ch in TAR_VALUE_LETTERS:
                    v = letters[n + 1:] or nxt
                    j += 0 if letters[n + 1:] else 1
                    directory = v if ch == "C" else directory
                    archive = v if ch == "f" else archive
                    break
        j += 1
    return extract, directory, archive


def tar_written(args, cwd, piped_from):
    """The paths `tar -x` writes: each member of an archive it can read, each tracked file under
    the --prefix of a `git archive` piped into it, else only DIR/x."""
    _, directory, archive = tar_parts(args)
    dest = os.path.join(cwd or os.getcwd(), os.path.expanduser(directory or "."))
    names = ["x"]
    if archive and archive != "-":
        path = resolve_path(archive, cwd)
        try:
            with tarfile.open(path) as t:
                names += t.getnames()[:5000]
        except (OSError, tarfile.TarError, TypeError, ValueError, EOFError):
            pass
    elif piped_from and os.path.basename(piped_from[skip_prefix(piped_from)]).lower() == "git":
        gargs = piped_from[skip_prefix(piped_from) + 1:]
        dash_c, sub, rest = git_split(gargs)
        if sub == "archive":
            prefix = (opt_values(rest, {"--prefix"}) or [""])[-1]
            where = os.path.join(cwd or os.getcwd(), dash_c) if dash_c else cwd
            names += [prefix + f for f in ["x"] + (git_query(
                ["ls-files"], where, None) or "").splitlines()[:5000]]
    return [os.path.join(dest, n) for n in names]


def tamper_shell(command, cwd, protected, marks, depth=0):
    if depth > 4:
        return False

    def hits(token, cwd, protected, deleting):
        return any(m in fold(token) for m in marks) or touches(token, cwd, protected, deleting)

    command, fed, _ = strip_heredocs(command.replace("\\\n", " "))
    if any(tamper_shell(b, cwd, protected, marks, depth + 1) for b in fed):
        return True
    for inner in re.findall(r"\$\(([^()]*)\)|`([^`]*)`", command):
        if tamper_shell(inner[0] or inner[1], cwd, protected, marks, depth + 1):
            return True
    try:
        tokens = shell_tokens(command)
    except ValueError:
        return any(m in fold(command) for m in marks)
    segments, segment, targets, redirect, piped = [], [], [], False, []
    for t in tokens:
        if t and set(t) <= set(";&|()<>"):
            if ">" in t:
                redirect = True
                continue
            if "<" in t:
                continue
            if segment or targets:
                segments.append((segment, targets))
                piped.append(t == "|")
            segment, targets = [], []
        elif redirect:
            redirect = False
            if not t.startswith("&"):
                targets.append(t)
        else:
            segment.append(t)
    if segment or targets:
        segments.append((segment, targets))
    for k, (seg, targets) in enumerate(segments):  # in order: a `cd` moves where later relative
        if any(hits(t, cwd, protected, False) for t in targets):  # paths land
            return True
        i = skip_prefix(seg)
        if i >= len(seg):
            continue
        prog, args = os.path.basename(seg[i]).lower(), seg[i + 1:]
        paths = [a for a in args if not a.startswith("-")]
        if prog in {"cd", "pushd"} and paths:
            cwd = resolve_path(paths[0], cwd) or UNKNOWN_CWD  # `cd "$X"`: judged by name after
            continue
        if prog == "ln" and any(link_to_protected(src, cwd, protected)
                                for src in copy_parts(args, prog)[1]):
            return True
        if prog in COPIERS | {"mv"} and (any(hits(t, cwd, protected, False)
                                             for t in copy_targets(args, prog)) or
                                         fills_protected_dir(args, prog, cwd, protected)):
            return True
        if prog in SHELLS or prog == "eval" or "$" in seg[i]:  # `$SHELL -c "…"` too
            carrier = " ".join(args) if prog == "eval" else next(
                (args[j + 1] for j, a in enumerate(args[:-1])
                 if re.fullmatch(r"-[a-z]*c[a-z]*", a)), None)
            if carrier and tamper_shell(carrier, cwd, protected, marks, depth + 1):
                return True
        if prog in DELETERS and any(hits(a, cwd, protected, True) for a in paths):
            return True
        if prog in MODIFIERS and any(hits(a, cwd, protected, False) for a in paths):
            return True
        if prog in IN_PLACE and any(re.match(r"^-[a-z]*i", a) or a.startswith("--in-place")
                                    for a in args) and \
                any(hits(a, cwd, protected, False) for a in paths):
            return True
        if prog == "dd" and any(a.startswith("of=") and hits(a[3:], cwd, protected, False)
                                for a in args):
            return True
        if prog == "find":
            roots = []
            for a in args:
                if a.startswith(("-", "(", "!")):
                    break
                roots.append(a)
            execs = [args[j + 1] for j, a in enumerate(args[:-1])
                     if a in {"-exec", "-execdir", "-ok", "-okdir"}]
            if ("-delete" in args or any(os.path.basename(e) in DELETERS | MODIFIERS
                                         for e in execs)) and \
                    any(hits(r, cwd, protected, True) for r in roots or ["."]):
                return True
        if prog == "claude" and {"plugin", "plugins"} & set(args) and not {
                "list", "validate", "--help", "-h"} & set(args):
            return True
        if prog == "git" and config_tamper(args):
            return True
        if prog == "git" and any(hits(p, cwd, protected, False)
                                 for p in git_file_writes(args, cwd)[0]):
            return True  # checkout-index --prefix=.git/hooks/, apply --directory=…, -o …
        if prog in {"tar", "gtar", "bsdtar"} and tar_parts(args)[0] and any(
                hits(p, cwd, protected, False) for p in tar_written(
                    args, cwd, segments[k - 1][0] if k and piped[k - 1] else None)):
            return True  # git archive --prefix=.git/hooks/ HEAD | tar -x
        if prog == "git":
            dash_c = option_value(args, ["-C"])
            where = resolve_path(dash_c, cwd) if dash_c else os.path.realpath(cwd or os.getcwd())
            where = fold(where)
            if where and any(where == p or where.startswith(p + "/") for p in protected) and \
                    GIT_VOCAB.search(" ".join(args)):
                return True
        if prog not in READERS | DELETERS | MODIFIERS | COPIERS and \
                any(m in fold(a) for a in args for m in marks):
            return True  # an interpreter or tool we do not know, pointed at the markers
    return False


HOOK_SETTINGS = ("hooks", "disableAllHooks", "enabledPlugins", "allowManagedHooksOnly")


def attributes_file(path):
    """A .gitattributes, or the global attributes file (~/.config/git/attributes)."""
    return os.path.basename(fold(path)) == ".gitattributes" or bool(
        re.search(r"(?:^|/)git/attributes$", path, re.I))


GIT_ATTR_WRITE = re.compile(
    r"(?i)(?:>>?|\btee\b|\b(?:cp|mv|install|ln|rsync|ditto|copy-item|move-item|cpi|mi|copy|"
    r"move|set-content|add-content|out-file|sc|ac)\b)[^;&|\n]*?(?:^|[\s'\"=/\\])"
    r"(?:\.gitattributes|git[/\\]attributes)(?=$|[\s'\";&|)])", re.M)
GIT_ATTR_COPY = re.compile(r"(?i)\b(?:cp|mv|install|ln|rsync|ditto|copy-item|move-item|cpi|mi|"
                           r"copy|move)\b[^;&|\n]*?\.gitattributes")


def attributes_write(command):
    """A shell or PowerShell write to a .gitattributes that names a filter / diff / merge driver
    (`echo '* filter=x' >> .gitattributes`), or a copy over one whose text it cannot see."""
    if not GIT_ATTR_WRITE.search(command):
        return False
    return bool(GIT_ATTR_DRIVER.search(command) or GIT_ATTR_COPY.search(command))


def settings_change(tool_name, inp, path):
    """Whether a Write/Edit of a settings*.json changes what decides which hooks run. The file
    before and after the call are compared as JSON; text that does not parse counts as a change."""
    try:
        with open(path, encoding="utf-8") as f:
            before, missing = f.read(4 << 20), False
    except FileNotFoundError:
        before, missing = "{}", True
    except OSError:
        return True
    if tool_name == "Write":
        after = inp.get("content")
    elif tool_name == "Edit":
        old, new = inp.get("old_string"), inp.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str):
            return True
        after = before
        if old == "":  # creates the file with new_string; fails on an existing one
            after = new if missing else before
        elif old in before:
            after = before.replace(old, new) if inp.get("replace_all") else \
                before.replace(old, new, 1)
        # an old_string that is not there: the Edit fails and changes nothing
    else:
        return True  # NotebookEdit on a settings file
    if not isinstance(after, str):
        return True
    try:
        a, b = json.loads(before or "{}"), json.loads(after or "{}")
    except ValueError:
        return True
    if not isinstance(a, dict) or not isinstance(b, dict):
        return True
    return any(a.get(k) != b.get(k) for k in HOOK_SETTINGS)


def tamper(tool_name, tool_input, data):
    inp = tool_input if isinstance(tool_input, dict) else {}
    protected, marks = protected_paths(data), session_marks(data)
    cwd = (data or {}).get("cwd")
    hit = False
    if tool_name in {"Bash", "Monitor"}:
        hit = tamper_shell(inp.get("command") or "", cwd, protected, marks) or \
            attributes_write(str(inp.get("command") or ""))
    elif tool_name == "PowerShell":
        hit = tamper_powershell(str(inp.get("command") or ""), cwd, protected, marks) or \
            attributes_write(str(inp.get("command") or ""))
    elif tool_name in {"Write", "Edit", "NotebookEdit"}:
        path = inp.get("file_path") or inp.get("notebook_path") or ""
        if isinstance(path, str) and path:
            resolved = resolve_path(path, cwd) or path
            if attributes_file(path):
                text = inp.get("content") if tool_name == "Write" else inp.get("new_string")
                hit = not isinstance(text, str) or bool(GIT_ATTR_DRIVER.search(text))
            elif settings_file(path, cwd):
                hit = settings_change(tool_name, inp, resolved)
            else:
                hit = touches(path, cwd, protected, False) or any(m in fold(resolved) for m in marks)
    if hit:
        return [("tamper", f"{tool_name} on the guard itself, its markers, the session "
                           "transcript, the plugin install, the hooks settings or a git setting "
                           "that redirects a push or redefines a git command")]
    return []


BODY_NAMES = re.compile(r"(?:^|[/\\'\"=@:\s])(?:PR-BODY|RUN)\.md(?![\w.-])", re.I)
BODY_READERS = {"cat", "head", "tail", "less", "more", "grep", "egrep", "fgrep", "rg", "wc",
                "sha256sum", "shasum", "md5", "md5sum", "cksum", "diff", "cmp", "ls", "stat",
                "test", "[", "file", "bat", "echo", "printf", "cd", "pushd", "popd", "true",
                "false", "pwd", "sleep", "date", "basename", "dirname", "realpath", "which"}
# git that only reads or sends: it writes no file, whatever its arguments
GIT_NO_FILES = {"push", "fetch", "status", "log", "rev-parse", "show-ref", "branch", "remote",
                "ls-remote", "describe", "symbolic-ref", "rev-list", "merge-base", "diff", "show"}
NOT_LITERAL = re.compile(r"[$`{}*?\[]")


def body_writer_segment(seg):
    """Whether a statement in the same command as a PR write may write PR-BODY.md or RUN.md.
    Only plain reads pass, with gh and git that write no file: anything else (cp, tee, sed,
    python, tar, a function or an alias defined on the spot, a name built from `$P` or
    `PR-BODY.{md,x}`) may write it while the command runs, after the guard checked the file."""
    i = skip_prefix(seg)
    if i >= len(seg):
        return False  # `P=…`: an assignment writes no file
    if seg[0:i] and any(re.match(r"^GIT_(?:DIR|WORK_TREE)=", t) for t in seg[:i]):
        return True
    prog, args = os.path.basename(seg[i]).lower(), seg[i + 1:]
    if any(BODY_NAMES.search(" " + t) for t in seg[i + 1:]) and prog not in BODY_READERS | {"gh"}:
        return True
    if prog in BODY_READERS:
        return False
    if prog == "gh":
        return any(a in {"download", "-D", "--dir", "-O", "--output", "--clobber"} or
                   a.startswith(("--dir=", "--output=")) for a in args)
    if prog == "git":
        if any(a in {"-C", "--work-tree", "--git-dir", "-c"} or
               a.startswith(("--work-tree=", "--git-dir=", "--output", "-o")) for a in args):
            return True
        sub = git_split(args)[1]
        if sub in GIT_PATH_WRITERS or any(a.startswith("--output") for a in args):
            return True  # checkout-index --prefix=<run>/, apply --directory=…, archive -o …
        return sub not in GIT_NO_FILES and any(NOT_LITERAL.search(a) for a in args)
    return True


def body_written_shell(command, depth=0):
    """Whether a shell command that writes a PR may also write PR-BODY.md or RUN.md: a redirect
    into it or into a name the guard cannot read (`> $P`, `> PR-BODY.{md,x}`), or a statement
    body_writer_segment does not pass. Then the file the guard checked before the call is not
    the one the PR gets: the PR write goes in a command of its own."""
    if depth > 4:
        return True
    command, _, _ = strip_heredocs(command.replace("\\\n", " "))
    for inner in re.findall(r"\$\(([^()]*)\)|`([^`]*)`|<\(([^()]*)\)|>\(([^()]*)\)", command):
        if body_written_shell(next(x for x in inner if x), depth + 1):
            return True
    try:
        tokens = shell_tokens(command)
    except ValueError:
        return True
    segment, redirect, segments = [], False, []
    for t in tokens + [";"]:
        if t and set(t) <= set(";&|()<>"):
            if ">" in t:
                redirect = True
                continue
            if "<" in t:
                continue
            if segment:
                segments.append(segment)
            segment = []
        elif redirect:
            redirect = False
            if BODY_NAMES.search(" " + t) or NOT_LITERAL.search(t):
                return True
        else:
            segment.append(t)
    for seg in segments:
        i = skip_prefix(seg)
        prog = os.path.basename(seg[i]).lower() if i < len(seg) else ""
        if prog in SHELLS or prog == "eval":
            return True  # a command carried as a string: its writes are not all readable here
        if body_writer_segment(seg):
            return True
    return False


def body_written_powershell(command):
    """body_written_shell for PowerShell: a redirect into PR-BODY.md, RUN.md or a name built at
    run time, a .NET file write, or a statement other than a plain read, gh or git."""
    plain = re.sub(r"`(.)", r"\1", command)
    readers = {"get-content", "gc", "cat", "type", "get-filehash", "select-string", "sls",
               "test-path", "write-output", "write-host", "echo", "cd", "set-location", "sl",
               "push-location", "pop-location", "get-item", "gi", "get-childitem", "ls", "dir",
               "get-location", "pwd", "start-sleep"}
    for m in PS_DOTNET.finditer(plain):
        if not m.group(1).lower().startswith(("read", "exists", "open")):
            return True
    if re.search(r"(?i)\[(?:system\.)?(?:io\.|environment\]|diagnostics\.)", plain):
        return True
    for seg in ps_segments(command):
        redirects = [seg[j + 1] for j, w in enumerate(seg[:-1]) if re.fullmatch(r"\d?>>?", w)]
        redirects += [w for w in seg if re.match(r"^\d?>>?\S", w)]
        if any(BODY_NAMES.search(" " + w) or NOT_LITERAL.search(w) for w in redirects
               if not re.fullmatch(r"&\d", w)):
            return True
        prog = ps_program(seg[0])
        if prog in readers:
            continue
        if prog in {"gh", "git"} and body_writer_segment(seg):
            return True
        if prog not in {"gh", "git"}:
            return True
    return False


def classify_skill(tool_input):
    """A skill that posts what the guard cannot read: /code-review --comment, ultra --post."""
    inp = tool_input if isinstance(tool_input, dict) else {}
    name, args = str(inp.get("skill") or ""), str(inp.get("args") or "")
    if PUBLISH_FLAGS.search(args):
        return [("skill-post", f"Skill {name} {args[:60]}".strip())]
    return []


def classify(tool_name, tool_input, cwd, data=None, state=None):
    """What a call does. `state` (the replay of the transcript) carries the branch a replayed
    `git switch` moved to from one call to the next."""
    found = tamper(tool_name, tool_input, data) if data is not None else []
    if state is not None:
        state.pop("here", None)  # a switch in an earlier call is not a switch in this one
    checks = BODY_CHECKS
    if tool_name in {"Bash", "Monitor"}:
        command = str((tool_input or {}).get("command") or "")
        found += classify_shell(command, cwd, 0, state)
        if BODY_CHECKS > checks and body_written_shell(command):
            found.append(("pr-body", "PR-BODY.md or RUN.md is written in the same command as "
                                     "the PR write"))
    elif tool_name == "PowerShell":
        command = str((tool_input or {}).get("command") or "")
        found += classify_powershell(command, cwd)
        if BODY_CHECKS > checks and body_written_powershell(command):
            found.append(("pr-body", "PR-BODY.md or RUN.md is written in the same command as "
                                     "the PR write"))
    elif tool_name.startswith("mcp__"):
        found += classify_mcp(tool_name, tool_input)
    elif tool_name == "Skill":
        found += classify_skill(tool_input)
    elif tool_name == "SendMessage":
        found.append(("delegate", "SendMessage to another agent or session"))
    elif tool_name == "RemoteTrigger" and str((tool_input or {}).get("action") or "") not in \
            {"list", "get", "list_runs", "get_run_log"}:
        found.append(("schedule", "RemoteTrigger (a cloud routine runs outside this session)"))
    elif tool_name == "CronCreate":
        found.append(("schedule", "CronCreate (a prompt that runs later, outside the user's "
                                  "command)"))
    unique, seen = [], set()
    for item in found:
        key = json.dumps(item, ensure_ascii=False, default=str)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


# --- decision -----------------------------------------------------------------------------

HOW = {
    "commit": ("«закоммить»", "[commit]"),
    "merge-local": ("«смерджи в мейн» / «залей в мейн»", "[merge-local]"),
    "base-sync": ("«закоммить» / «залей в мейн»", "[commit] or [merge-local]"),
    "push": ("«запушь»", "[push]"),
    "pr": ("«открой PR»", "[pr]"),
    "merge": ("«смержи PR»", "[merge]"),
    "force": ("«форс-пушь» / «запушь в main» / «залей в мейн»", "[force-push]"),
    "publish-release": ("«выпусти релиз»", "[publish] and names the release"),
    "publish-repo": ("«создай репо»", "[publish] and names the repo"),
    "publish-gist": ("«создай гист»", "[publish] and names the gist"),
    "repo-admin-settings": ("«сделай репо публичным» / «переименуй репо» / «заархивируй репо»",
                            "[repo-admin] and names that change (visibility, rename, archive)"),
    "repo-admin-secrets": ("«поставь секрет»", "[repo-admin] and names the secret or variable"),
    "repo-admin-ci": ("«запусти workflow»", "[repo-admin] and names the workflow"),
    "repo-admin-protection": ("«включи защиту ветки»", "[repo-admin] and names the protection"),
    "repo-admin-delete": ("«удали релиз» / «удали гист»",
                          "[repo-admin] and names deleting the release or gist"),
    "history": ("«сбрось» / «подтяни» / «верни стэш»", "[reset]"),
    "branch-delete": ("«удали ветку» / \"delete the branch\"", "[delete-branch] or [reset]"),
    "status": ("«переведи в ревью» / «закрой задачу»", "[status]"),
    "create-task": ("«создай задачу»", "[create-task]"),
    "tracker": ("«назначь на меня»", "[tracker-edit]"),
    "destructive": ("«удали задачу»", "[delete-task]"),
    "delete": ("«удали коммент»", "[delete-comment]"),
    "send": ("«отправь письмо» / «напиши в слак» / «создай встречу»", "[send]"),
}


STEMS = {"commit": r"комм?ит|commit|мерд?ж|merge", "push": r"пуш|push|залей",
         "merge-local": r"мерд?ж|merge|зал[еи]|вле|мейн|main", "base-sync": r"комм?ит|commit|мерд?ж|merge|зал[еи]",
         "pr": r"\bpr\b|\bпр\b|пулл|pull", "status": r"статус|ревью|review|status|работу|закр|close",
         "create-task": r"созда|завед|задач|task|issue", "tracker": r"назнач|assign|задач|task",
         "publish-release": r"релиз|release|публик|publish",
         "publish-repo": r"репо|repo", "publish-gist": r"гист|gist",
         "repo-admin-settings": r"репо|repo|публичн|public|приватн|private|переимен|rename|архив|"
                                r"archive",
         "repo-admin-secrets": r"секрет|secret|переменн|variable|ключ|key",
         "repo-admin-ci": r"workflow|воркфло|ci\b|пайплайн|pipeline|джоб|job",
         "repo-admin-protection": r"защит|protect|ruleset",
         "repo-admin-delete": r"удали|delete",
         "merge": r"мерд?ж|merge", "force": r"форс|force|main|master|мейн|мастер",
         "history": r"сброс|reset|pull|подтян|стэш|стеш|stash|откат|restore|clean",
         "branch-delete": r"удали|снеси|грохни|delete|ветк|бранч|branch",
         "delete": r"удали|delete", "destructive": r"удали|delete",
         "send": r"отправ|пошли|перешли|ответь|письм|слак|slack|чат|chat|send|mail|встреч|событи|"
                 r"meeting|event|invite|приглаш"}


def decide(gated, text, actions, approved, used, unseen, failure, status=None):
    """`status`: (refs, open) from read_authorization — a status call changes a task the command
    named, unless one change of any task is open."""
    problems = []
    # what the latest message itself ordered (a change of plan in it orders nothing)
    ordered = detect(text) if text and not revokes(text) else set()
    for item in gated:
        action, what = item[0], item[1]
        if action == "tamper":
            problems.append(f"{what}: the guard's own state is off limits in a /ticket session.")
        elif action == "opaque":
            problems.append(f"{what}: the guard cannot tell what this runs. Write the git or gh "
                            "command out plainly, so it can be checked against the user's command.")
        elif action == "skill-post":
            problems.append(f"{what}: this skill would post to the PR or tracker text the guard "
                            "never sees. Run it without the posting flag and show the result; the "
                            "user posts it, or a comment goes through Task comments.")
        elif action == "pr-body":
            problems.append(f"{what}. A PR's text is the one the user approved: create or edit "
                            "it with `--body-file <run_dir>/PR-BODY.md` (publish.md, \"Open a "
                            "PR\"); a changed text is approved again first.")
        elif action == "schedule":
            problems.append(f"{what}: work started this way runs outside this guard; the user "
                            "confirms it.")
        elif action == "delegate":
            problems.append(f"{what}: another session is outside this guard, so it is not a way "
                            "to run anything the guard refuses here.")
        elif action == "repeat":
            problems.append(f"{what}: one command is one action — split the call, each repeat "
                            "needs its own command.")
        elif action == "comment":
            texts = [normalize_comment(t) for t in comment_candidates(item)]
            if texts and all(t in approved for t in texts):
                continue
            if texts and any(t in unseen for t in texts):
                problems.append(
                    f"{what}: the user picked [post] for this text, but it was never shown in full "
                    "— the dialog cuts long previews and it was not printed verbatim in your "
                    "messages since their latest message. Print the whole text as plain text (not "
                    "quoted or indented), then ask again.")
            elif texts and any(t in used for t in texts):
                problems.append(f"{what}: this exact text was already posted since the user's "
                                "latest message; posting it again needs a new approval.")
            else:
                problems.append(
                    f"{what}: a tracker comment is posted only after the user approved these exact "
                    "words — an AskUserQuestion, after their latest message, whose picked option "
                    "carries [post] in its label and exactly this text as its `preview`, shown in "
                    "full (publish.md, Task comments, step 4). Post the text unchanged.")
        elif action == "base-sync" and actions & {"commit", "merge-local"}:
            continue
        elif action == "status" and action in actions and status and not status[1] and not any(
                ref_matches(r, item[2] if len(item) > 2 else []) for r in status[0]):
            named = ", ".join(sorted(status[0]))
            problems.append(f"{what}: the user's status command named {named[:200]} — this call "
                            "changes another task, or names it in a way the guard cannot match. "
                            "Another task needs its own command — typed (e.g. «переведи <id> в "
                            "ревью») or an AskUserQuestion option whose label carries [status].")
        elif action not in actions:
            typed, tag = HOW[action]
            if action in ordered:
                problems.append(f"{what}: the user's latest message gave this command and it was "
                                "already used once since; another one needs a new command — typed "
                                f"(e.g. {typed}) or an AskUserQuestion option whose label carries "
                                f"{tag}.")
                continue
            hint = ""
            if text and re.search(STEMS.get(action, r"$^"), normalize_text(text)):
                hint = (" (The latest message mentions it, but not as a plain order the guard "
                        "reads — conditional, deferred, negated, misspelt or unusual phrasing is "
                        "not counted.)")
            problems.append(f"{what}: no command for it since the user's latest message, or it was "
                            f"already used once; the user authorizes it by typing it (e.g. {typed}) "
                            f"or by picking an AskUserQuestion option whose label carries {tag}."
                            + hint)
    if not problems:
        return None
    note = ""
    if text is not None:
        snippet = " ".join(text.split())
        note = f" Latest user message: «{snippet[:120]}{'…' if len(snippet) > 120 else ''}»."
    elif failure:
        note = f" ({failure}.)"
    return ("[ticket guard] Blocked. " + " ".join(problems) + note +
            " Stop and ask the user; do not retry this call or reach the same result another "
            "way until they give the command.")


# The skill text names the same override (SKILL.md, "The run directory").
TASK_RUNS = os.path.expanduser(os.environ.get("KENSEI_TASK_RUNS_DIR") or "~/.claude/task-runs")
MARK_DIR = os.path.join(TASK_RUNS, ".guard")
MARK_TTL = 14 * 24 * 3600


def marker(session_id):
    safe = re.sub(r"[^A-Za-z0-9_-]", "", session_id or "")
    return os.path.join(MARK_DIR, safe) if safe else None


# this session's run directory: the one whose RUN.md the session last wrote, kept in its marker
RUN_DIR = None
RUN_WRITE = re.compile(r"(?:>>?|\btee(?:\s+-a)?)\s*[\"']?((?:[^\s\"';&|<>]*/)?RUN\.md)(?![\w.])")
CD = re.compile(r"(?:^|[;&|(]\s*)cd\s+[\"']?([^\s\"';&|<>()]+)")


def run_dir_of(path, cwd=None):
    """The run directory `path` is the RUN.md of (<runs root>/<repo>/<task>/RUN.md), or None."""
    if not isinstance(path, str) or os.path.basename(path) != "RUN.md":
        return None
    path = resolve_path(path, cwd)
    run = os.path.dirname(path or "")
    if path and os.path.dirname(os.path.dirname(run)) == os.path.realpath(TASK_RUNS) and \
            not os.path.basename(os.path.dirname(run)).startswith("."):
        return run
    return None


def note_run(session_id, tool_name, tool_input, cwd):
    """Record in the marker the run whose RUN.md this call wrote (Write / Edit, a Bash redirect
    or tee into it, a PowerShell Set-Content / Out-File / redirect; a bare RUN.md resolves
    against the last `cd` / Set-Location before it)."""
    tool_input = tool_input or {}
    paths = []
    if tool_name in {"Write", "Edit", "MultiEdit", "NotebookEdit"}:
        paths = [(tool_input.get("file_path"), cwd)]
    elif tool_name == "Bash":
        command = str(tool_input.get("command") or "")
        for m in RUN_WRITE.finditer(command):
            where = cwd
            for d in CD.finditer(command[:m.start()]):
                where = resolve_path(d.group(1), where) or where
            paths.append((m.group(1), where))
    elif tool_name == "PowerShell":  # Set-Content / Out-File / a redirect, after Set-Location
        where = cwd
        for seg in ps_segments(str(tool_input.get("command") or "")):
            prog, args = ps_program(seg[0]), seg[1:]
            words = [re.sub(r"^\$(?:env:)?(?:HOME|USERPROFILE)(?=[\\/]|$)", "~", w,
                            flags=re.I).replace("\\", "/") for w in seg]
            if prog in PS_CD:
                target = ps_opt(args, {"-path", "-literalpath"}) or next(
                    iter(ps_positional(args)), None)
                if target:
                    where = resolve_path(words[seg.index(target)], where) or where
                continue
            out = [words[j + 1] for j, w in enumerate(seg[:-1]) if re.fullmatch(r"\d?>>?", w)]
            out += [w.lstrip("0123456789>") for w in words if re.match(r"^\d?>>?\S", w)]
            if prog in PS_WRITERS:
                out += [w for w in words[1:] if not w.startswith("-")]
            paths += [(w, where) for w in out if os.path.basename(w) == "RUN.md"]
    path = marker(session_id)
    for p, where in reversed(paths):
        run = run_dir_of(p, where)
        if run and path and os.path.exists(path):
            with open(path, "w") as f:
                f.write(f"ticket session\nrun: {run}\n")
            return


def marked_run(session_id):
    path = marker(session_id)
    try:
        with open(path or "", encoding="utf-8", errors="replace") as f:
            m = re.search(r"^run: (.+)$", f.read(4096), re.M)
    except OSError:
        return None
    return m.group(1) if m else None


def activate(session_id):
    path = marker(session_id)
    if not path:
        return
    if os.path.exists(path):
        os.utime(path, None)
        return
    os.makedirs(MARK_DIR, exist_ok=True)
    with open(path, "w") as f:
        f.write("ticket session\n")
    now = os.path.getmtime(path)
    for name in os.listdir(MARK_DIR):
        old = os.path.join(MARK_DIR, name)
        try:
            if now - os.path.getmtime(old) > MARK_TTL:
                os.unlink(old)
        except OSError:
            pass


def subagent_reason(gated):
    what = ", ".join(item[1] for item in gated)
    return (f"[ticket guard] Blocked: {what}. In a /ticket session subagents never commit, push "
            "or write to the tracker — the orchestrator does that, and only on the user's "
            "command. Do not retry or work around this; report what you would have done.")


def deny(reason, decision="deny"):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "permissionDecision": decision,
                                             "permissionDecisionReason": reason}}))


# kinds the user confirms at the permission prompt in the main session; subagents are refused
ASK_KINDS = {
    "delegate": "[ticket guard] SendMessage hands work to another agent or session, which this "
                "guard does not watch. The user confirms it.",
    "schedule": "[ticket guard] RemoteTrigger and CronCreate start work later or in the cloud, "
                "where this guard does not watch and no command of the user stands behind it. The "
                "user confirms it.",
}


def main():
    global RUN_DIR
    mode = sys.argv[1] if len(sys.argv) > 1 else "--main"
    if mode == "--post":  # PostToolUse: the call ran, so its RUN.md is this session's run
        try:  # it only notes the run: whatever goes wrong, it says nothing and blocks nothing
            data = json.loads(sys.stdin.read())
            if isinstance(data, dict) and not data.get("agent_id"):
                note_run(data.get("session_id"), data.get("tool_name", ""),
                         data.get("tool_input"), data.get("cwd"))
        except Exception:  # noqa: BLE001
            pass
        return 0
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
        tool_name = data.get("tool_name", "")
        session, agent = data.get("session_id"), data.get("agent_id")
        if mode == "--subagent":
            path = marker(session)
            if not agent or not path or not os.path.exists(path):
                return 0
        else:
            activate(session)
        cwd = data.get("cwd")
        RUN_DIR = marked_run(session)
        gated = classify(tool_name, data.get("tool_input") or {}, cwd, data)
        if not gated:
            return 0
        if agent:
            reason = subagent_reason(gated)
        else:
            gated = [g for g in gated if g[0] not in SUBAGENT_ONLY]
            if not gated:
                return 0
            if {g[0] for g in gated} <= set(ASK_KINDS):
                deny(" ".join(ASK_KINDS[k] for k in sorted({g[0] for g in gated})), "ask")
                return 0  # not refused: the user decides at the prompt
            text, actions, approved, used, unseen, failure = read_authorization(
                data.get("transcript_path"), cwd)
            reason = decide(gated, text, actions, approved, used, unseen, failure, STATUS_LEFT)
    except Exception as exc:  # fail closed for anything that looks gated
        if not re.search(r"\bgit\b|\bgh\b|commit|push|merge|rebase|comment|status|mcp__|curl|"
                         r"\.guard|SendMessage|--comment|--post|settings|hooks|PowerShell|RemoteTrigger|"
                         r"CronCreate|webhook", raw, re.I):
            return 0
        if mode == "--subagent" and '"agent_id"' not in raw:
            return 0
        reason = f"[ticket guard] Blocked: the guard failed ({type(exc).__name__}: {exc}); " \
                 "it fails closed. Tell the user."
    if reason:
        deny(reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
