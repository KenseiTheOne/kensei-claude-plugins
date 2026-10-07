# Changelog

Versions are per plugin and live in each plugin's `.claude-plugin/plugin.json`. Release tags are
`<plugin>--v<version>` (`claude plugin tag`).

## kensei-toolkit 2.1.1 — 2026-10

### diff-tour
- The file list no longer sticks to the top of the page. It sat inside the sticky toolbar, so on a
  change of a hundred files it covered half the screen while reading code. Now it scrolls away
  with the page; only the one-line view toggle and legend stay pinned. A file chip still jumps to
  its file, with the file header just below the pinned bar.

## kensei-toolkit 2.0.1 — 2026-10

Closes the guard gaps and the open items listed under "Not done" in 2.0.0. No breaking change:
every 2.0.0 tag keeps its meaning, and `[send]` and `[delete-branch]` are new. Stricter in a few
places: `git branch -d`, which passed with no command in 2.0.0, now needs a branch-delete
command; a PR write runs in a command of its own; mail, chat and calendar sends need `[send]`. A
bare «подтяни» / `git pull` needs `[reset]`, as in 2.0.0.

### ticket guard (`guard.py`)
- **Mail, chat and calendar are gated: `send` / `[send]`, one message or event per grant.**
  Sends, replies, forwards, posts, reactions, invites and invite answers on mail and chat MCP
  servers, chat message edits and deletes, any calendar event write, Slack canvases, and mail or
  event tools on any other server (Outlook, M365, Workspace, Resend). Webhook POSTs through curl,
  wget or httpie (Slack, Discord, Office, Telegram bots and others) too. Drafts and reads pass.
  Typed: «отправь письмо», «напиши в слак», «создай встречу», «прими приглашение», "send the
  email", "post it to slack", "schedule a meeting"; a message phrase needs somewhere to go, so
  «напиши сообщение коммита» and «добавь событие в лог» send nothing. «назначь встречу» is now a
  send, not a tracker edit. A cancelled meeting needs a `[send]` option.
- **`RemoteTrigger` and `CronCreate` are in the matcher**: the main session asks the user,
  subagents are refused; listing triggers and reading their runs pass.
- **PowerShell is guarded like Bash** (it is in the matcher): git and gh found anywhere in a
  statement (`&` calls, `git.exe`, backtick escapes, Start-Process, `cmd /c`), tracker and webhook
  writes through Invoke-WebRequest / Invoke-RestMethod, tamper through cmdlets, `[IO.File]` and
  redirects. Script blocks and pipelines in an array, a cast or a subexpression are read as
  statements (`&{git push}`, `.{…}`, `ForEach-Object { … }`, `@(git push)`, `[void](git push)`,
  `"$(git push)"`). Refused because they cannot be read: `iex` of anything but a plain literal,
  encoded commands, `& (…)` or `$var` programs that may be git (`&("{0}{1}" -f 'gi','t')`,
  `&(Get-Command gi*)`), `git @args`, and git after an environment variable that changes its
  settings or runs a program — `$env:GIT_CONFIG_PARAMETERS=…`, `$env:GIT_SSH_COMMAND=…`,
  `Set-Item env:…`, `[Environment]::SetEnvironmentVariable(…)`, a cmd `set` — as in Bash
  (`$env:GIT_PAGER='cat'` passes). Names are matched without case, as Windows reads them
  (`$env:git_ssh_command`, `$env:Git_Config_Parameters`, `cmd /c "set Git_Dir=…"`); a `cmd /c`
  run from Bash is read for git and gh too. A RUN.md written through PowerShell sets the
  session's run (the `PostToolUse` matcher includes it). The Windows support claim stands.
- **The PR body is the approved one.** `gh pr create`, `gh pr edit --body…`, `gh api …/pulls` with
  a body, GraphQL `createPullRequest` / `updatePullRequest` and GitHub/GitLab MCP PR writes pass
  only with this run's `<run_dir>/PR-BODY.md`: `--body-file`, `"$(cat …)"`, `gh api -F body=@…`
  (GraphQL: `body: $body` with `-F body=@…`), or identical text. The file counts as approved only
  while its sha256 equals the last `pr_body_sha256:` line in `RUN.md`, which publish.md records
  when the user approves the text: no line → refused with a hint, another hash → "changed since
  the user approved it". The PR write runs in a command of its own: one that may also write
  `PR-BODY.md` or `RUN.md` while it runs is refused — any statement but plain reads (`cat`,
  `shasum`), `git` and `gh`, a redirect into a name built at run time (`> $P`,
  `> PR-BODY.{md,x}`) or into either file in any case (`pr-body.md`); Bash or PowerShell. A
  body in a nested field (`gh api -F input[body]=…`, GraphQL variables from `--input`) cannot be
  checked and is refused. `--body-file` together with `--body` checks both. `--fill`, stdin,
  another text, or no run are refused, and no tag approves another text. «обнови описание PR» /
  "update the PR description" grant the edit (`[tracker-edit]` still works). Before, «открой PR»
  let any body through.
- **Quoted orders are not commands**: «тикет говорит: «запушь»», `says: "push"` grant nothing; a
  whole message in quotes and an order outside the quotes still count.
- **Writes after `cd` and copies into protected places**: redirects follow `cd` / `pushd` /
  subshells in order; `cp`/`mv`/`install`/`ln`/`rsync`/`ditto`/`scp` check the target directory
  (`-t`, `--target-directory`) and a directory copied over `~/.claude` (`cp -r x/ ~/.claude/`).
- **Git settings that redirect a push or run a program are tampering**: `git config` writes to
  `remote.*`, `branch.*`, `alias.*`, `url.*`, `include*`, `core.hooksPath`, `push.default`,
  `core.editor`, `core.fsmonitor`, drivers and credential helpers, section rename/remove, `-e`,
  `git remote add` / `set-url` / `set-head` / `rename`. The same settings written as files are
  tampering too (Write/Edit, redirects, `tee`, `cp`/`mv`, PowerShell `Set-Content` and the like):
  `.git/config` of the repository, a worktree or a submodule, `.git/hooks/*` and the
  `core.hooksPath` directory, `.git/info/attributes`, `~/.gitconfig`,
  `$XDG_CONFIG_HOME/git/config`, `/etc/gitconfig`, `$GIT_CONFIG_GLOBAL`, gh's `config.yml` (its
  aliases), and a `.gitattributes` or the global attributes file naming a `filter=` / `diff=` /
  `merge=` driver other than Git LFS (`filter=lfs`) and the built-in merge drivers. Paths are
  compared without case, as on macOS and Windows (`.GIT/config`, `.git/HOOKS/pre-push`,
  `~/.CLAUDE/settings.json`), and a link made to `.git`, `.claude` or a protected path (`ln -s
  .git x`, `New-Item -ItemType SymbolicLink`) is tampering too.
- **Files git itself writes at a path an option or a patch names are checked as writes**:
  `checkout-index --prefix=…` (each tracked file under the prefix, so `--prefix=.git/` over a
  tracked `config` is `.git/config`), `archive -o`, `diff` / `log --output`, `format-patch -o`,
  `bundle create`, and `git apply` / `git am` — the paths inside a readable patch, with `-p` and
  under `--directory` (a project `.claude/settings.json`, a `.gitattributes` naming a driver).
  `tar -x` is checked by the members of an archive it can read, or by each tracked file under
  the `--prefix` of a `git archive` piped into it (`git archive --prefix=.git/hooks/ HEAD | tar
  -x`), and always by its `-C` directory. Refused as unreadable: such a path built at run time
  (`--prefix=$X/`, `-o "$OUT"`), `--directory` outside the repository or not literal,
  `--unsafe-paths` with a patch from stdin, a patch file it cannot open (written in the same
  command). In a PR's own command these git writers count as rewriting `PR-BODY.md`.
- **gh aliases are resolved**: `gh p 1` after `gh alias set p 'pr merge'` is a merge, a shell
  alias (`!git push`) is read as that command, one the guard cannot read is refused. `gh alias
  set` naming a gated write, and `gh alias import`, are tampering.
- **One-off settings before git are refused as unreadable** (`opaque`): `git -c` / `--config-env`
  with any of those keys, `core.sshCommand`, `core.pager`, `sequence.editor`, `remote.*` or
  `url.*`, before any subcommand; `git --exec-path=…`; `GIT_CONFIG_*`, `GIT_SSH_COMMAND`,
  `GIT_EDITOR`, `GIT_PAGER`, `GIT_EXEC_PATH`, `EDITOR` and the like set before git or exported
  earlier in the same command. `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM`,
  `GIT_INDEX_FILE` and a plain pager or editor name pass, in the environment and in `-c` alike
  (`GIT_PAGER=cat`, `git -c core.pager=cat log`, `git -c core.editor=true rebase --continue`).
  `HOME`, `XDG_CONFIG_HOME` and `USERPROFILE` before git are refused too: they move the global
  git config.
- **gh reads its config where the command points it**: `GH_CONFIG_DIR` / `XDG_CONFIG_HOME` set
  before gh (`VAR=… gh`, `export`, `env`, `$env:` in any case, cmd `set`) → its aliases are read
  from that directory; a gh word gh does not know is refused as unreadable when that config
  cannot be read or has no such alias (it may be written in the same command).
- **A plain `git push` goes where the repository's settings send it**: `git push` / `git push
  <remote>` and a branch pushed without `:dst` are resolved from `remote.<r>.push`,
  `branch.<b>.pushRemote` / `remote.pushDefault`, `push.default` and `branch.<b>.merge` (config
  only, no network). Landing on a base branch, a `+` refspec, a wildcard of all branches or
  `push.default=matching` → a force push. Before, a bare `git push` from a branch tracking
  `main` was a plain push.
- **Git and gh writes run by another command are unreadable**: `rebase -x`, `bisect run`,
  `submodule foreach`, `filter-branch --tree-filter` / `--index-filter` and its other filters,
  `filter-repo` callbacks, `difftool` / `mergetool -x`, `grep -O`, `--upload-pack` /
  `--receive-pack`, and git or gh through `xargs`, `parallel`, `find -exec`, `fd`, `watch` or
  `entr` are refused when the command holds a git or gh write; `rebase -x 'npm test'` passes.
- **PowerShell call operators without a space** (`&'git'`, `&("git")`, `.( "git" )`, `&{git
  push}`) are read as git; a program that is not a plain literal (`& $g`, `&("gi"+"t")`) is
  refused unless it clearly names another tool.
- **GitHub GraphQL mutations are classified by name**: `createPullRequest` a PR,
  `mergePullRequest` / auto-merge a merge, `updatePullRequest` / `closePullRequest` /
  `reopenPullRequest` a tracker edit, `createRef` / `updateRef` / `deleteRef` a force push,
  `createIssue` a task, release, repository and comment mutations their own classes, any other
  mutation a tracker edit; queries pass. The query is read in every spelling gh takes
  (`-f query=`, `-fquery=`, `--raw-field`, `--field=query=`, `-F query=@file`, a JSON `--input`
  file), at `graphql`, `/graphql` or a full URL, and from curl, wget and httpie bodies posted to a
  tracker's `/graphql` (`-d`, `--data*`, `--json`, `@file`). A query the guard cannot read — a
  variable or substitution (`"$Q"`, `"$(cat q.graphql)"`, PowerShell `$q`), a missing file, a body
  that is not literal JSON — is refused as unreadable; before, such a request passed with no
  command at all. `gh api` fields in the joined spellings (`-fbody=…`) count for REST calls too.
- `guard.py --post` never denies: any error ends it silently with exit 0.
- **Plural and precise status**: a typed status command covers each task it names once (URL,
  `KEY-123`, `#123`, a ClickUp id); a PR or commit link is no task; with none named it covers one
  change. Before, «переведи задачи в ревью» allowed one change for any task.
- `git worktree remove --force` needs `[reset]`.
- **Branch delete is its own class, `branch-delete`**: `git branch -d` / `-D` / `--delete`, granted
  by «удали / снеси / грохни ветку», "delete the branch", the new `[delete-branch]` tag, or
  `[reset]`; it grants no reset, clean or worktree remove. In 2.0.0 `-d` passed with no command
  and `-D` needed `[reset]`. «удали ветку на origin» is also a force
  push. `branch -f` / `-M` / `-C` stay history writes.
- More English phrases: "push to github", "push these", "commit with message …", "assign it to
  me".
- Fewer false blocks: a `$VAR` path is judged only by protected names (markers, plugin install,
  settings files, the guard's own files), so `> "$LOG_DIR/hooks.log"` passes; `gh copilot` is
  local.
- Tests for `gh api` PR create, merge and `git/refs` writes; a smoke test on a sanitized real
  Claude Code 2.1.286 transcript (typed command, queued message, answers) and the ClickUp tool
  catalogue with each tool's class, both in `skills/ticket/testdata/`. 132 guard tests.
- Measured: skill-frontmatter hooks do not register again in a resumed session until the skill
  is invoked again.

### ticket
- **A third smaller**: the ticket text read per run (`SKILL.md` + `flow.md` + `publish.md`) went
  from 80.4 KB in 2.0.0 to 54.3 KB, below 1.8's 60.2 KB (`SKILL.md` + `flow.md`; `wc -c`).
  Rationale and repetition were cut; every rule, step and cross-reference stays. The guard's
  internals live in its docstring.
- Commands table and "The guard" rewritten as what the user sees: rows for `reset`, `delete a
  branch` («удали ветку», «снеси ветку») and `send`, the `[send]` and `[delete-branch]` tags, the
  PR-body rule with its hash, the status-per-task rule, quoted text, the new always-refused cases
  (git settings as files, files git writes where an option or a patch says, one-off settings
  before git and gh, git run by another command), and Known gaps limited to what is still open.
- **The PR body approval is recorded**: when the user approves `PR-BODY.md` ("create as is
  [pr]", or a new PR description), publish.md appends `pr_body_sha256: <sha256>` to `RUN.md` in
  its own call, again after each re-approval; the guard refuses the PR without it.
- After `--resume`, invoke `/ticket <same id>` again: that turns the guard back on and offers to
  continue from the step reached.
- The gate opens every `checked by eye` capture in the system image viewer, so the user sees the
  frames, not only their paths: `/usr/bin/open` on macOS (as diff-tour does, past a terminal's
  own `open` wrapper), `xdg-open` on Linux, `start` on Windows.
- The gate's diff tour goes into the run directory (`--out-root <run_dir>`), so the page linked
  from `REPORT.md` is not rotated out of the shared cache.
- «обнови описание PR» rewrites `PR-BODY.md`, fact-checks and shows it, then runs `gh pr edit
  --body-file`.

### diff-tour
- On a branch with no history in common with the upstream or default branch (an orphan branch),
  it no longer shows a diff against that unrelated history: collect prints `No changes.` and a
  line that HEAD shares no history with `<ref>`, suggesting a ref or range to pass — also when
  the orphan branch was pushed with an upstream of its own.
- `--out-root` is the caller's way to keep a page: runs under it are rotated on their own.
- The page's HTML, CSS and JS moved to `assets/`; golden pages rendered by 2.0.0 check that the
  output is byte for byte the same.
- The noise-file syntax and run-directory rotation moved to the skill's `README.md`; leftover
  headless advice and an unused constant removed. 68 tests.

### brainstorm
- Triggers on «брейншторм» and «побрейнштормим».

### Repository
- `scripts/check.sh` (tests, validate, strict YAML, now including each `case.yaml`) is the one
  release check; CI and the README call it, so they no longer check different things.
- CI pins Claude Code 2.1.286 and runs the tests on Python 3.9 as well.
- README: a Requirements section (Claude Code 2.1.286+, python3 3.9+, git 2.25+, 2.28 for the
  tests), the toolkit link goes to its section, the structure lists the new files.
- `displayName` and `homepage` in both `plugin.json`.
- Evals: the two ticket cases stop at once with "needs git" when the only git is the Xcode shim
  the macOS eval sandbox cannot run; `evals/README.md` explains the Homebrew git fix and sketches
  a manual Linux CI job, and warns that other git-using cases fail the same way there.

### Not done in this release
- No live run of the two ticket eval cases yet: this machine has no Homebrew git, and the Linux
  CI job in `evals/README.md` is a sketch nobody has run. The commit path rests on the guard
  tests, the real-transcript smoke test and the gate case's fixture.
- That the plugin's subagent hook keeps working after `--resume` follows from its design (it does
  not depend on the skill), not from a measurement.
- Still not gated, by choice: mailbox housekeeping (labels, trash, spam), git settings
  (`GIT_CONFIG_*`, `GIT_SSH_COMMAND`, …) exported by an earlier call (the same command is now
  caught), git from a script or `.ps1`, a gh extension, unquoted reported speech, the target
  status of a status command, a patch fed to `git apply` on stdin, a git alias for
  `checkout-index` / `archive` / `apply` in the tamper check, and a gh `config.yml` rewritten in
  the same command that points gh at it. The ticket text lists them as Known gaps.
- Guard gaps found in the last review round, left as Known gaps (each fails open only when the
  model already broke the skill's rules):
  - A tar from stdin or one the guard cannot list, a git archive written earlier in the same
    command included, is caught only for `-C .git/hooks`: `.git` and `~/.claude` pass.
  - tar: an attached `-C<dir>`, name rewriting (`--transform`, `--xform`, `-s`), a tar in a
    subshell after `cd` (`(cd .git && tar -x)`, `sh -c`), and any tar run from PowerShell.
  - Other extractors and `patch` are not checked: `unzip`, `Expand-Archive`, `python -m tarfile`,
    `busybox tar`, `pax`, `patch -p1 < x.diff`.
  - `git merge-file` writes its first path, which gets no protected-path check.
  - Globs in a target path are not expanded (`.git/conf*`, `--prefix=.gi?/`, `cp x .git/conf*`).
  - PowerShell env writes in another shape: `-Value` or `-Force` before `-Path` (`Set-Item`,
    `New-Item`, `Set-Content`), `New-Item -Name … -Path env:`, an `Environment::` or computed
    `env:` path (only `SetEnvironmentVariable($n, …)` is opaque), `Copy-Item` / `Rename-Item`
    into `env:`, `Set-Location env:` then a bare name, `Start-Process -Environment` (PS 7.4+).
  - gh's config dir: `GH_CONFIG_DIR` set in a parent for gh in a nested shell (`X=… bash -c`,
    PowerShell `cmd /c "set X=… & gh"`) or by `readonly` / `eval` / `read`; and `HOME`,
    `$env:AppData`, `$env:USERPROFILE`, which move gh's config, are not read for gh.
- The PR-body hash proves the file has not changed since `pr_body_sha256:` was written, not that
  the user approved it: the guard trusts the skill to write that line only at the approval.
- `publish.md`'s approval record is checked against the guard by a probe, not yet by a live run
  that opens a PR.
- ClickUp operators are still judged by their names' words: the live catalogue has none enabled.
- PerfectWar still needs `.claude/task-flow-rules.md` with `worktree_setup:` for worktree runs
  (owner).
- Installing through `claude plugin update` and restarting old sessions (owner).

## kensei-statusline 1.5.1 — 2026-10

- The usage-limit fetch moved to `scripts/usage.py`; the background refresh runs
  `usage.py --refresh-usage`. Output is byte for byte the same; if `usage.py` is missing or broken,
  only the server limit rows disappear and the rest of the statusline still prints.
- Setup stops on a dry-run `error` (for example a broken `settings.json`) right after the dry run,
  before describing any change.
- Tests for the SessionStart setup check (missing, broken or non-object `settings.json`,
  `settings.local.json`, the opt-out marker), the wrapper run from the installed location, the
  refresh command, git states and rendering edge cases: 69 tests, about 95% line coverage of both
  scripts.

## kensei-toolkit 2.1.0 — 2026-10

### codex-img (new)
- Generates or edits images through the Codex CLI's built-in image tool, on the user's ChatGPT
  subscription — no OpenAI API key. `codex_img.py gen` makes one image under a chosen path (never
  overwriting: `name.v2.png`); `batch` runs a JSON manifest one image after another.
- Up to 5 reference images per call: a style reference keeps a series consistent, or the image to
  edit. `--transparent` asks for a transparent background and checks the PNG really has alpha
  (`no_alpha` otherwise; stdlib decoder, no Pillow).
- The script does the deterministic work itself: the prompt goes to `codex exec` on stdin (no
  `-i` swallowing and no cmd.exe quoting on Windows), the Codex agent runs in a read-only,
  ephemeral session and only calls the image tool, and the image is taken from
  `$CODEX_HOME/generated_images/<thread_id>/` by the id in the first `--json` event.
- Stops a batch on the image limit, including the hidden `image_gen` limit that Codex reports only
  in the agent's reply (`rate_limited`), on a Codex that will not start (`codex_failed`), and
  after two turns in a row that end with a Codex error (`codex_failing`: auth expired mid-run). A
  refusal that merely quotes the description ("a speed limit sign") is not mistaken for a limit.
- Where each item went is recorded in `<manifest>.codex-img-state.json` (relative paths, written
  atomically, so it survives a moved project or a crash mid-write). `--resume` skips an image
  this manifest already made and redoes one that failed its check in place; a file the batch did
  not write — the user's own `fire.png` — is never skipped or reported, and replaced only when
  the item sets `"overwrite": true`. An unreadable state file stops `--resume` before Codex runs.
- A timeout kills the whole Codex process tree (process group / `taskkill /T`) and keeps an image
  that was saved before it, unless it was cut off mid-write (a PNG without its end is rejected).
  Ctrl+C, SIGTERM or SIGHUP to the script (a background batch hitting its time limit) takes Codex
  down too, so no orphaned turn keeps spending quota; SIGKILL cannot be caught.
- Always answers with JSON: a malformed manifest (wrong types, `"false"` as a string, two items
  with the same `out`, an `out` that is not `.png`) is `bad_manifest` before anything is spent, an
  unexpected crash is `internal: …`. `--out` must be a `.png`; a free name is claimed atomically,
  so parallel runs never write the same `name.vN.png`. On Windows an npm `codex.cmd` is bypassed
  for `node codex.js` when found; otherwise reference paths with characters cmd.exe interprets are
  passed as safe copies.
- The skill confirms the cost before a series of 3+ images, runs it in the background, looks at
  every result, retries at most twice per image and asks before generating an image nobody
  asked for. Measured cost per image: ~50–90 s, ~30k input tokens of Codex quota (~70k with a
  reference).
- Limits, stated in the skill: the model, quality and size are Codex's choice (gpt-image-2 per
  its source; GPT Image 2.5 Flare/Sunburst by name needs the API), no masks.
- Requires Codex CLI 0.158+ (for `transparent_background`) signed in with ChatGPT on a paid plan.

### svg-diagram (new)
- Draws a README or docs diagram (architecture, data flow, pipeline, "how it works") as a
  hand-built SVG in one dark gradient style: glowing gradient cards, labelled arrows, a dashed
  amber "magic" path, takeaway badges. One SVG per README language from one `TEXT` dict, embedded
  where the ASCII or mermaid diagram was, with a real-sentence `alt`.
- The picture comes from a small generator script built on `diagram_kit.py` (stdlib only). The
  skill copies the kit next to the generator, so the committed generator rebuilds the picture
  without the plugin and survives a plugin update. `example.py` rebuilds the KenseiUnityMCP "How it
  works" picture in English and Russian.
- `--strict` estimates every string's width (SVG text never wraps; separate factors for Cyrillic
  capitals) and reports text out of its card, box, pill or edge on both axes, a card shorter than
  its lines need, text or shapes off the canvas and texts overlapping each other. It builds and
  reports every language before it exits 1. Lines are not checked: a label crossed by an arrow is
  left to the visual pass, and arrow labels are placed off the line along its normal so a
  diagonal edge does not strike through its own label.
- Every picture is rendered to PNG (headless Chrome or Edge, `CHROME=path` to choose one;
  `rsvg-convert` as the fallback) and looked at before it is handed over. A failed or timed-out
  render raises with the renderer's output and deletes the old PNG first, so a stale picture is
  never mistaken for the new one. With no renderer the skill says the picture was not looked at.
- Badges size themselves to the canvas (up to 300 px each, so 2–4 fit at 960 px); arrowhead
  markers get valid ids for any ink colour.
- `diagram_kit_test.py`: width estimate, both-axis layout checks, minimum card heights, badge
  widths, arrow-label placement, marker ids, `--strict` across languages, renderer failures and
  timeouts, and the reference example building clean; run by `scripts/check.sh`.

## kensei-toolkit 2.0.0 — 2026-10

Breaking: `ticket --unattended` is removed (see Removed), `todo` no longer writes a `TODO.md` unless
given its path, and the ticket guard has new answer tags (`[merge-local]`, `[create-task]`,
`[tracker-edit]`, `[publish]`, `[repo-admin]`) with `[status]` narrowed to the status field. Rolls up the hotfixes
planned as 1.8.2 and the ticket rework planned as 1.9.0.

### Removed
- **`ticket --unattended`** and everything that served it: `--task-id`, `unattended.md`, the
  `## Unattended` section, `caller-ticket.md`, `RESULT.json` (including the planned `contract: 2`)
  and the guard's unattended branch. The toolkit no longer carries a headless contract.
  **Migration:** the night runner gets its own `night:ticket` skill in its own repository; runner
  v1 keeps working by loading a frozen toolkit 1.8.0 copy with `--plugin-dir`. An old command that
  still passes `--unattended` (or any other `--` option) stops at Step 0 and says so, instead of
  running as an interactive run.

### learn
- Deduplicates against every place a takeaway could already live: project, nested and local
  `CLAUDE.md`, `~/.claude/CLAUDE.md` and the project's auto-memory (`MEMORY.md` as an index, the
  matching memory files opened before proposing).
- Routes each takeaway to one home: project `CLAUDE.md` for rules every contributor and headless
  run needs, the auto-memory for facts about the user (this project only), `~/.claude/CLAUDE.md`
  for preferences that hold in every project. Memory is written one fact per file with
  frontmatter, and `MEMORY.md` gets only a pointer line.
- Never proposes secrets, tokens, private or signed URLs, internal hostnames or third parties'
  personal data. With no project `CLAUDE.md`, the target `<repo root>/CLAUDE.md` is shown and
  created on approval.
- Selection is grouped by destination, every option is a real item (no All / None / Remaining)
  and every question has 2–4 options; Other rewords an item or moves it (`2: global`). Replies in
  the user's language. Still user-invoked only.
- Questions follow the same rule as todo: one question per destination, named in its text; more
  than 4 items split into balanced batches with no single-item batch; a single item is an
  Apply / Skip question. Argument text that is not a path narrows the review to that topic.

### todo
- **Breaking:** delivers to where the user already keeps tasks instead of a `TODO.md` it looked for
  or created: personal items to the Todoist Inbox (Todoist MCP or a CLI that is really Todoist,
  otherwise a paste-ready block), project work to ClickUp into a list the user picks in this run
  (read-only duplicate check first; no status, assignees, priority or dates unless asked). A
  markdown file is written only when its path is the argument; other argument text is a focus
  hint.
- Every draft carries an evidence line (quote or fact from the session); drafts are deduplicated
  and marked with a destination, `(?)` when it is a guess. Selection is multiSelect per
  destination, batches of up to 4 with no single-item batch, no All / None / Remaining; edits go
  through Other. Nothing is created before the user ticks it in this run. Still user-invoked only.
- **No built-in ClickUp workspace or server name.** The ClickUp tools are found on whichever server
  provides them (`clickup_*`); the workspace comes from the project rules, `CLAUDE.md` (project or
  personal) or the session's ClickUp task, otherwise from the workspace hierarchy, asking when
  there are several. Lists are looked up before the question, so drafts and the list choice come
  in one question. Without ClickUp it says so in one line and prints the drafts.
- Each ClickUp list option ends with `[create-task]`, the ticket guard's tag for creating tasks,
  so `/todo` works in a session where `/ticket` ran. A list typed through Other, or a draft moved
  to ClickUp by an edit, gets one confirmation («Create N tasks in <list>?») first.

### unity-review
- Rewritten as a Unity-specialist review: four lenses (Performance, Memory & lifecycle, Unity
  architecture, Platform) in modes `quick` (one agent, Critical and Warning only), `perf`, `arch`,
  `full`. **Changed meaning:** Code Quality, Bug Hunter and Security are gone — generic review is
  `/code-review`'s job, so `quick` no longer means "Code Quality + Bug Hunter".
- Arguments work: `[quick|perf|arch|full] [paths | ref | a..b] [focus]`. A mode named in words
  («проверь на перф», «по памяти», «полное ревью») counts as given; otherwise it is asked once,
  after the scope is known. Scope: uncommitted work with untracked files; on a clean tree, the
  branch's work against the default branch (diff-tour looks at the upstream first, unity-review
  does not); a ref, a range or paths. Paths in findings are relative to the repository root.
- Reviewers are read-only by allowlist: Read, Grep, Glob, ToolSearch only to load Unity MCP
  tools, read-only git and shell commands (`git diff` without `--output`, `find` without
  `-delete`/`-exec`), and Unity MCP tools whose name starts with `get_`, `find_`, `list_`, `read_` (plus `ping`,
  `status`); anything else (`open_scene`, `execute_menu_item`, `call_method`, dumps) is named in
  the report instead of called. The verifier gets the same paragraph word for word. Reviewers
  see the whole project for serialized references (GUIDs and field names of changed or deleted scripts), and every finding
  needs `path:line`, a failure scenario and a fix. A fresh agent tries to refute each finding;
  the report groups confirmed ones by severity, lists unconfirmed ones apart, and has no score.
- Project facts are read from files, not guessed: platforms from build profiles and the
  per-platform `ProjectSettings` maps, scripting backend, domain reload, Burst/Jobs only when an
  `.asmdef` or the code uses them.
- **A project review skill** (such as `pw-review`) stays the main review for a general request. When
  the skill was picked automatically and the user named a lens the project skill lacks («проверь на
  перф» in a project whose review skill has no performance category), those lenses run here, with
  one line saying the general review is the project skill's. Only Performance, Memory & lifecycle
  and Platform run here this way; an architecture-only request goes to a project skill that has an
  architecture category. A project's own "lifecycle" categories do not count as the Unity memory
  and lifecycle lens.
- Domain reload: Unity 6 still writes `m_EnterPlayModeOptionsEnabled` but no longer reads it; the
  skill reads the `DisableDomainReload` flag of `m_EnterPlayModeOptions` instead.
- Checklists moved to `lenses.md`, read only by the reviewer agents; `flow.md` is gone.

### ticket
- **Run notes stay out of the repository**, so a task commit no longer carries review files into
  the project's history. This reverses the 1.5.0 design choice that the basis of acceptance
  travels to origin with the commit: approved criteria and review files are no longer copied to
  `.task-runs/<id>/` and committed. They stay in the run dir and are named at the gate.
  They go into a commit only when the project sets `notes_path:` in `.claude/task-flow-rules.md`;
  a path under `.gitignore` is skipped with one line in the report.
- **One frame for every agent**, because the read-only Explore agent could not write its file:
  agents are `general-purpose`, so the context agent
  actually writes `01-context.md` (the orchestrator saves the reply there if it did not); the
  40-line reply cap stays, justified once; models are inherited from the session unless project
  rules name one for a role. `general-purpose` is required only for the agents that write a file.
- **PR body is fact-checked like a task comment**: drafted from `REPORT.md` into
  `<run_dir>/PR-BODY.md`, checked by a fresh agent, shown in full, and created with
  `gh pr create --body-file` only on the `[pr]` answer.
- **Kept from the unattended mode, now for every run**: a `manual` criterion is reported as
  `NOT PROVEN — needs a human look`; tests already failing on `base_sha` (named in the user's
  instructions or project rules) are reported apart from new failures; a test command named in
  the user's instructions comes first in the "first hit wins" list.
- Tests and captures must come from the run's own checkout: an editor, editor MCP test runner or
  device on another checkout gives `NOT PROVEN`.
- Any `--word` standing alone as an option stops the run at Step 0; a `--flag` inside a quoted or
  named command from the instructions («тесты: dotnet test --filter Combat») belongs to it. Every
  "stop, outcome blocked/tampered" ends at Step 12 with a `REPORT.md`; a second review with
  blocking findings ends the run as `stopped`.
- The runs root is `~/.claude/task-runs`, or `KENSEI_TASK_RUNS_DIR` when set; the guard reads the
  same variable.
- **Gate builds a diff tour** of `<base_sha>..<reviewed_tree>` with notes from the criteria,
  review and `03-changes.md`.
- **Visual proof by the agent**: an `agent-visual` evidence type (screenshot, MCP, adb,
  logcat), guided by the project's verification map, is tried before `manual`; captures go to
  `<run_dir>/evidence/` and into the report and the review.
- **Worktree as an option, base shown at Step 5**: `worktree: always|ask|never` in project
  rules, otherwise a non-default "separate worktree" answer, with a package-restore step
  (`worktree_setup:`). Not the default, because a cold Unity checkout does not build. The run dir
  is keyed to the main checkout (git common dir).
- **Base-branch work at the gate**: merge into the base branch (local `--no-ff`, tests re-run, no
  push) is its own command and its own tag, `[merge-local]`; `[merge]` is a PR merge only. One
  «закоммить и залей в мейн» runs commit → local merge → push without asking again; «смерджи в
  мейн» orders the commit and the merge. Push to the base branch lists every commit it publishes
  and is never forced; a documented rebase path when the base moved (patch-id compare with the
  reviewed tree, tests re-run). Switch, merge and rebase require a clean tree and offer a worktree
  or a stash, never `--autostash`. Before a local merge the skill fetches the target and asks if
  the local `<target>` is behind `origin` (updating it is a `[reset]` command of its own).
- **The target branch.** «Мейн» / "main" means the default branch; when the run was based on
  another task's branch, the skill asks which branch to merge into or push to. A PR goes against
  `base_branch` when that is not the default branch, says in its first line that it is stacked,
  and the commits it carries towards the default branch are listed before it is created. Before,
  a stacked task's PR went to main together with the other task's unreviewed commits.
- **Changes after the gate** (the most common request at the gate in real runs): a fresh
  implementer makes them, never the orchestrator; a new requirement is appended to the approved
  criteria as an addendum whose hash goes into `RUN.md` (`criteria_addendum:`); in `full` mode a
  new automatic criterion gets its test first; then tests, review and report run once more.
- The tag list names every guard tag: `[commit] [merge-local] [push] [pr] [merge] [force-push]
  [publish] [repo-admin] [reset] [status] [create-task] [tracker-edit] [delete-task] [delete-comment]`, and
  `[post]` for an approved text. Other skills that create tasks in a ticket session use
  `[create-task]`.
- **Split text**: `SKILL.md` (rules, Steps 0–5, Commands, guard) + `flow.md` (Steps 6–11) +
  `publish.md` (gate, task comments and PR body, read at the gate). Each rule stated once; the
  `guard.py` docstring is the guard's full specification; the design-doc revision history dropped.
  A run reads ≈53 KB up to the gate (`SKILL.md` + `flow.md`, was ≈60 KB), ≈80 KB with
  `publish.md` — the total grew with the new features (local merge, target branch, changes after
  the gate). The "publish only on command" rule is stated once (rule 7) and referenced
  elsewhere.
- Parallel reviewers read the snapshot (`git show <tree>:<path>`), and test runs that write files
  finish before they start. Russian triggers in the description; `--transport http` in
  `trackers.md`; Windows/python3 requirement documented; PerfectWar specifics
  (`generated_paths`, `test_report_parts`) are examples from project rules.

### ticket guard (`guard.py`)
- **Force pushes in disguise**: bundled short flags are read one by one per subcommand
  (`-fu`, `-uf` force; `-fn` dry run; a value-taking flag such as `-o` ends the bundle). `--all`,
  `--tags`, `--mirror`, a push to `production`/`prod`/`staging`/`stable` or to the `base_branch:`
  of the `RUN.md` this session last wrote (recorded by a `PostToolUse` hook) count as force, and so does the remote's default branch (`origin/HEAD`);
  `send-email`, `send-pack`, `http-push` count as push.
- **Real phrases are recognised**: «push и pr», «PR»/«пр», «залей ветку», «залей(ся) в мейн»
  (commit + local merge + push to the base branch), «смерджи/мердж в мейн» (commit + local merge),
  «смерджи пр» (PR merge only), "push it to main", «верни стэш», «создай задачу», «назначь …»,
  «опубликуй релиз». A test checks every example in the `SKILL.md` command table.
- **Local merge is its own class, `merge-local`**: a `git merge`/`git rebase` into a base branch
  (checked out, switched to in the same command, or `git rebase <upstream> <base>`), and a
  cherry-pick, `am` or commit right after `git switch <base>`. The task commit does not use it up,
  so commit → merge → push passes after one «закоммить и залей в мейн». Bringing a base branch
  into the task branch (`git merge origin/main`, `git rebase --onto origin/main …`) is allowed by
  `[commit]` or `[merge-local]`, and so is `git pull <remote> <base>` on the task branch;
  fast-forwarding local main from `origin/main` is a history write (`[reset]`). On a base branch,
  a commit that concludes a merge (`MERGE_HEAD` present) and `git fetch . <branch>:<base>` are
  `merge-local`; squashing the branch's own commits (`git rebase -i HEAD~3`) is `commit`.
- **Tracker writes split by what they change.** `create-task` / `[create-task]`: creating tasks
  (`clickup_create_task`, a create through `clickup_execute_operator`, `create_issue`, Linear
  `save_issue` without an id, `gh issue create`, `gh api POST …/issues`); one command covers a
  whole batch until the user's next message, because a tracker without a bulk operation creates
  one task per call. `/todo` tags its ClickUp options with it.
  `status` / `[status]`: only the status field (an update whose only changed key is status or
  state, transition tools, `gh issue close/reopen`). Every other tracker field is `tracker` /
  `[tracker-edit]` («назначь на меня»), and `gh pr close` is now one of them. Before, «переведи в ревью» let through
  any tracker or `gh` write, including `gh repo create --public`.
- **Publishing is `publish` / `[publish]`, one grant per object**: creating or editing a release
  (`gh release create/upload/edit`, writes to `…/releases`), a repository (`gh repo create/fork`,
  `POST user/repos|orgs/*/repos|…/forks`) or a gist (`gh gist create/edit/rename`, `gists`). A
  typed command grants only the object it names («выпусти релиз», «создай репо», «создай гист»;
  "cut a release", "fork a repo"); a picked `[publish]` option grants the object its label names
  (релиз/release, репо/repo, гист/gist) and nothing when it names none. So «выпусти релиз» does not
  cover `gh repo create --push`, a visibility change or a secret. This is stricter than putting
  them under push, so «запушь» cannot create a public repository.
- **Repository settings, secrets and CI are `repo-admin` / `[repo-admin]`**, in five kinds, one
  call per grant: settings (`gh repo edit/rename/archive/unarchive`, autolinks, repo `PATCH`,
  hooks, collaborators), secrets (secrets, variables, environments, deploy/ssh/gpg keys), CI
  (`gh workflow`, `gh run`, caches, dispatches, deployments), protection (rulesets, branch and tag
  protection) and deletes of a release or gist. Typed: «сделай репо публичным», «поставь секрет»,
  «запусти workflow», «включи защиту ветки», «удали релиз»; "rerun the ci", "set the X secret".
  The tag grants only the kind its label names. `gh repo delete`, `gh api -X DELETE repos/<o>/<r>`
  and MCP repository deletes stay destructive. Any `gh` command with `--push` also counts as push,
  on top of its own class (`gh pr merge … --push` needs `[merge]` too). GitHub, GitLab and
  GitKraken MCP writes map to the same grants; reads (`gh api` GET) are free.
- **Owner decisions** (confirmed for 2.0.0): the old single `[publish]` is split into per-object
  `[publish]` and per-kind `[repo-admin]`; `[tracker-edit]` stays a tag of its own; bringing the
  base branch into the task branch is allowed by `[commit]` or `[merge-local]`; one
  `[create-task]` command is not used up within the message, so a batch of tasks passes;
  `gh pr close` is a tracker write (`[tracker-edit]`).
- **A message queued mid-turn** adds its orders to the message before it. One that says stop or
  holds a gated action back voids every command so far and grants nothing itself: «не надо»,
  «стой», «передумал», «я сам», «сам закоммичу», «давай я сделаю», «сначала …», «перед этим /
  перед коммитом», «дай посмотреть / гляну», "first …", "changed my mind", "let me check",
  "I'll do it", "hold off", "no wait". So «сначала покажи дифф, потом закоммить» asks again.
  «сам тест упал» voids nothing. These phrases are mandatory refusals in the tests.
- **A failed call** uses nothing up when the gated step is the whole command or ends an `&&`
  chain (a hook-refused commit, a rejected push). An MCP call uses its command up unless the
  server plainly refused it (validation error, `-32602`, a 4xx, "not found" on the first line of
  the error): after a timeout, a 500 or an unclear error the write may have happened, so the same
  comment is not posted twice. This reverses the earlier 2.0.0 draft, where any failed MCP call
  freed its command.
- **Tamper: reads pass, writes to the guard are refused.** Read-only `find`, and `rm`/`mv`/`cp`
  inside the run dir pass (RUN.md → RUN.prev.md works again). Refused: writes to the `.guard`
  markers, the session and subagent transcripts, the guard's own `skills/ticket/` and `hooks/` (with
  `--plugin-dir`, the dev clone — deliberate), `~/.claude/plugins`, any Bash write to a settings
  file (`settings*.json` in the config dir — `CLAUDE_CONFIG_DIR` honoured — or in any `.claude` dir,
  and `managed-settings.json`, matched on the written path and on the realpath, so a symlink into
  dotfiles is covered), a Write/Edit that changes `hooks`, `disableAllHooks`, `enabledPlugins` or
  `allowManagedHooksOnly` (compared as parsed JSON), and `claude plugin` other than list/validate.
- **Fewer accidental grants, fewer blind spots**: questions, git commands mentioned inside a
  sentence and pasted text grant nothing; English verbs need a git object. Gated now: throwing
  local changes away (`clean -f`, `checkout -- <paths>`, `restore`, `stash pop/apply/drop`;
  `[reset]`), `reset <ref>`, `checkout -B`, `switch -C`, `filter-*`; `gh` anything off its read
  list; `curl --json`/`-d@`/`--request=POST`, wget and httpie writes, self-hosted tracker APIs;
  ClickUp `execute_operator` judged by model + operator. `git stash push` is refused to subagents
  only. Always refused: a git subcommand built from `$` or backticks (`git $S`,
  `git "$(echo push)"`, `` git `echo push` ``); a program named by `$VAR`/`$(…)` that is plausibly
  git (`$GIT`, `$(which git)`, or any unnamed program whose first argument is a writing git
  subcommand); `$SHELL -c "…"` is read like `sh -c`; a runner target named exactly push,
  publish, release, deploy or ship (`make push`, `npm publish`); a `Skill` call with
  `--comment`/`--post`. `SendMessage` asks the user. Ordinary commands no longer trip this, since
  they could never be unblocked: `$PY -m pytest -k checkout`, `$ROOT/gradlew build`,
  `$GIT_EDITOR`, `npm run release-notes` and `make deploy-docs` pass.
- Comment check: every text field must equal the approved text; `*_id` keys are not text. Tracker
  names match on word boundaries (`plane` ≠ `planetscale`; `gitkraken` added).
- **Linear time**: typed text is read from its first and last 4 KB, URL and split regexes
  are linear, and a speed test guards it, so a huge command no longer outruns the 15 s timeout.
- The subagent hook's shell one-liner is now `hooks/subagent-guard.sh`, tested to start no Python
  outside a marked session; `Skill` and `SendMessage` are in both matchers and `MultiEdit` is
  gone. `SendMessage` asks before the transcript is read, so a malformed transcript cannot turn
  the ask into a refusal. It takes the hook input's first `session_id`, so one inside
  `tool_input` cannot point it at another session.
- Tests: a headless invocation (`claude -p`) that contains «закоммить и запушь» grants nothing,
  and ticket text read from a file or echoed by the model grants no gated call.

### diff-tour
- **The model can run it**: `disable-model-invocation` removed; the description triggers on
  «покажи дифф», «что поменялось», «дифф-тур», «ревью перед коммитом», or when another skill or
  CLAUDE.md says to show changes this way. Still read-only and still only on request.
- **No silently lost file**: the throwaway index is copied with `copy2`, keeping its mtime,
  so a same-size edit in the same second is not treated as clean (collect and drift check); the
  test is deterministic.
- After a commit, with nothing uncommitted, it shows the commits beyond the upstream (or the
  default branch) and names that base instead of stopping at "No changes.".
- Ranges accept a tree id on either side (`<base_sha>..<reviewed_tree>`), for callers such as
  ticket; `--out-root` is documented; a "When another skill calls it" section.
- highlight.js pinned with SRI; `~/.cache/kensei-diff` is 0700 and keeps the newest 20 runs per
  repository. Russian example notes, unambiguous size and read-length rules, by-product files are
  `inferred`, ResourceWarnings in tests fixed.

### brainstorm
- Docs go outside the project by default: `.claude/brainstorm-rules.md`, then the project's
  existing `docs/brainstorms`, then `~/docs/brainstorms`; the path is named before writing.
- Fewer questions: one "Asking questions" section; design sections come in batches of up to
  three with one "which need changes?" question; independent enumerable questions are batched
  (up to 4); no empty "needs changes" options — edits go in Other. One decision at a time stays.
- Questions, options, the doc and the plan are in the user's language. The 1.8.1 behaviour (no
  `EnterPlanMode`, implementation only on "Start here") is kept and has an eval case.

### Repository
- `evals/` with `claude plugin eval` cases: diff-tour fires on «покажи дифф», brainstorm
  never enters plan mode, ticket stops at criteria without committing and its context agent writes
  `01-context.md`, unity-review with a mode given asks nothing and edits nothing, learn and todo
  write nothing before approval, and `ticket-commit-at-gate` resumes a run stopped at the publish
  gate, types «закоммить» and checks one commit on the task branch, no run notes in the index, no
  push, PR or merge. The smoke command uses `--scaffold`. Evals run on Opus only, every command
  with `--judge-model opus`; stray session transcripts and eval outputs are git-ignored.
- Live run on Opus 5.5, 3 runs per case, `--judge-model opus`, $19.77: `brainstorm-no-plan-mode`,
  `diff-tour-on-show-diff`, `unity-review-readonly`, `learn-asks-before-writing` and
  `todo-asks-before-creating` all 1.00. Both ticket cases were blocked by the macOS eval sandbox
  (git through the xcrun shim cannot run); in the one run where git worked, the gate case made
  exactly one commit holding the two fix files, with no push, amend or run notes. Graders found
  wrong in the run were fixed.
- learn and todo: when `AskUserQuestion` is unavailable (headless sessions), the same question is
  asked in plain text and the skill waits for the answer.
- Version only in `plugin.json`; marketplace has a description; keywords updated.
- CI: script tests on Linux and macOS, `claude plugin validate --strict`, strict-YAML frontmatter,
  release tag vs `plugin.json`.
- README: requirements (`python3`), skill table with invocation (manual / model) and an example
  phrase per skill, hooks in the structure, the development and release loop.

### Not done in this release
- Full live runs of the two ticket cases: they need Linux CI or a Homebrew git the macOS eval
  sandbox may read. Until then the commit path rests on the guard tests, the gate case's fixture
  and the one run where git worked. A live probe on Claude Code 2.1.286 confirmed that the skill's
  frontmatter `PreToolUse` and `PostToolUse` hooks register once the skill is invoked (also as a
  `/plugin:skill` in a `-p` prompt) and that `${CLAUDE_PLUGIN_ROOT}` expands; a denied call gets
  no `PostToolUse`, so `guard.py --post` never sees it. Whether the hooks register in a resumed
  session (`--resume`, as the gate case uses) is still unverified. Regenerate
  `evals/ticket-commit-at-gate/history.jsonl` with `make_history.py` before running it.
- Installing through `claude plugin update`, restarting old sessions and fixing the auto-memory
  note that still says to edit the plugin cache — owner actions, described in the README.
- Guard gaps left for 2.0.1 (each fails open only when the model already broke the skill's rules):
  - a gated phrase quoted inside the user's message still counts as a command («тикет говорит:
    «запушь»» lets `git push` through, as in 1.8);
  - the PR body is not compared with the approved `PR-BODY.md`; «открой PR» lets any
    `gh pr create --body …` through, so the fact-check rests on the skill's rules;
  - a redirect after `cd` is checked against the old directory (`cd ~/.claude && echo … >
    settings.json`), and `cp x/settings.json ~/.claude/` is not caught;
  - `git config remote.origin.push …` / `alias.*` are not treated as tampering, so a later plain
    push can land elsewhere;
  - mail, chat and calendar MCP sends (Gmail `send_message`, Slack), `curl` to webhooks,
    `RemoteTrigger` and `CronCreate` are not gated; the `PowerShell` tool is not in the matcher;
  - `git worktree remove --force` is not gated, though the skill lists it as a command;
  - «переведи задачи в ревью» (plural) allows one status change; `gh copilot` is refused as a
    tracker write.
- A commit on a base branch the session was already on stays `commit` (the user may choose to work
  on main); only one right after `git switch <base>` in the same command, or one that concludes a
  merge, is `merge-local`.
- The size of the ticket text is deferred to 2.0.1: up to the gate it is smaller than in
  1.8 (≈53 KB, was ≈60 KB), but with `publish.md` a run reads ≈80 KB, more than before.
- PerfectWar still needs `.claude/task-flow-rules.md` with `worktree_setup:` for worktree runs.
- diff-tour's HTML/CSS/JS stays inside `difftour.py` (no functional gain; tests use it directly).
- No smoke test on a fresh real transcript: the transcript fields were checked against Claude Code
  2.1.285 and recorded in the guard docstring.
- ClickUp operator names are classified by their words; the live operator catalogue was empty
  and could not be cross-checked.
- `displayName` / `homepage` in manifests (cosmetic).

## kensei-statusline 1.5.0 — 2026-10

- Output tokens include workflow subagents (recursive transcript scan); before, about two thirds of
  a workflow-heavy session was missing.
- Prices per exact model version (Fable 5.1, Opus 5.5, Sonnet 5.5, Haiku 4.5 and earlier), with
  1.25× / 2× cache-write rates for 5-minute / 1-hour writes; ids are normalised (`[1m]`, Bedrock
  prefixes including `global.` and `us-gov.`, inference-profile ARNs, snapshot dates). An unknown
  model and fast-mode requests show `n/a` instead of a guess. Used only when Claude Code does not
  report the cost.
- `git --no-optional-locks` on every git call, so the statusline never takes the index lock.
- The wrapper ships as a file and resolves the installed version from `installed_plugins.json`
  (project install first), skipping orphaned copies and honouring `CLAUDE_CONFIG_DIR`.
- Setup runs `setup.py`: it keeps `refreshInterval` and your other `statusLine` fields, backs up
  `settings.json`, writes through a symlink keeping file mode, offers a dry run, and reports a
  `statusLine` in `settings.local.json` that would override it. The setup offer mentions the OAuth
  token read.
- The SessionStart check honours `CLAUDE_CONFIG_DIR` and `settings.local.json` and runs on
  `startup` only. The setup trigger moved from the non-standard `trigger:` field into the
  description.
- Project stats cached per HEAD (they were ~80% of render time).
- README: setup is offered, not run automatically; agents are grouped by `subagent_type`.
- `statusline_test.py` added, including the usage-limit fetch (keychain and `.credentials.json`
  token, cache, backoff per HTTP code, lock takeover, stale cache, the Fable row) with stubbed
  network and keychain: 91% line coverage of `statusline.py` (coverage.py). The refresh cancels
  its 30-second kill timer when done. Not done: splitting out `usage.py` (the tests do not need
  it).

## Earlier history

Reconstructed from git; dates are commit dates.

### kensei-toolkit
- **1.8.1** (2026-09-30): brainstorm saves a standalone `<slug>-plan.md` and asks Save only / Start
  here / Revise instead of entering plan mode. Repository renamed to `kensei-claude-plugins`, MIT
  license.
- **1.8.0** (2026-09-29): ticket `--unattended` for headless runners (REPORT.md, commit-msg.txt,
  RESULT.json); snapshot index in the git dir, built without `rm`.
- **1.7.0** (2026-09-24): diff-tour — annotated diff page for self-review before a commit.
- **1.6.0** (2026-09-22): ticket publishes only on command; reviewed task comments; guard hook.
- **1.5.0** (2026-09-10): ticket — run one tracker task end to end.
  Committing the acceptance notes dates from this design; reversed in 2.0.0.
- **1.4.0** (2026-06-11): brainstorm skill.
- **1.3.0** (2026-05-26): todo skill; pr-preview removed.
- **1.2.x** (2026-05): learn skill (1.2.0); pr-preview overhaul; unity-review always shows the mode
  picker.
- **1.1.x** (2026-04): pr-preview skill with standalone HTML output; frontmatter quoting fixes.
- **1.0.0** (2026-04-08): first release as `toolkit`, renamed `kensei-toolkit`; unity-review.

### kensei-statusline
- **1.4.0** (2026-09-24): per-model usage meters (e.g. Fable) from the claude.ai usage endpoint;
  setup hook moved to `hooks/hooks.json`.
- **1.3.1** (2026-06-11): usage limits on their own line.
- **1.3.0** (2026-06-11): subscription usage limits with reset times.
- **1.2.0** (2026-04-15): context size instead of cumulative input tokens.
- **1.1.0** (2026-04-08): `python3` in the statusline command; setup skill and SessionStart hook.
- **1.0.0** (2026-04-06): first release — model, context, tokens, cost, subagents, git, project
  stats.
