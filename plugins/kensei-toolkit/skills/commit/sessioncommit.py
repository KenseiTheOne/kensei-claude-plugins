#!/usr/bin/env python3
"""commit — land this session's work in git and leave everyone else's alone.

  sessioncommit.py plan [--repo DIR] [--session ID]... [--declare PATH]... [--out DIR]
                        [--lang ru|en] [--task LINK|ID] [--branch NAME] [--pr|--main]
                        [--no-merge] [--no-push]
      Read this session's transcripts (the main one, its subagents and workflow agents), find
      the files it edited in the repository and split each into this session's changes and
      everyone else's. Writes plan.json and the content to commit into a private run directory
      (default <config>/cache/kensei-commit/<repo>/<stamp>, the newest KEEP_RUNS kept). Read-only
      for the repository: no fetch, no index write. Prints the status block (what will be
      committed, skipped, left alone, merged and pushed), then details for the model.
      Exit 0: go, or nothing to do; 1: stop (nothing may be applied); 2: error.
  sessioncommit.py apply --plan RUN/plan.json [--message-file FILE|-] [--task LINK|ID]
                         [--pr|--main] [--no-merge] [--no-push]
      Re-check the plan against the repository (exit 3 when it moved) and that the message names
      the task (exit 2), make the planned branch from HEAD (`git switch -c`), commit exactly the
      planned content through a private index (the repository's hooks and signing still apply),
      set the real index entries of those paths to the new commit and nothing else, then
      publish: in a repository that takes work through pull requests, push the branch and name
      the page to open its PR on (no PR is opened or edited here); elsewhere by fast-forward
      only — push the branch, or move the default branch to it and push that. Never rebased, merged, forced or stashed; anything
      else stops with the merge to run. One status line per step.
      Exit 0: every planned step done; 1: stopped at a step (its line says why; earlier steps
      stay done); 2: error; 3: stale plan.

Both refuse (exit 1, a stop line) inside a /ticket run of this session: the ticket's publish
gate commits there, with its own checks and guard.

Where a file holds this session's edits and someone else's, the commit gets HEAD plus this
session's hunks only: the session's edits are undone from the working tree (reverse replay of
the transcript patches) to get the file without them, and `git merge-file` combines that with
HEAD. See README.md for why and for the known gaps.
"""

import argparse
import datetime
import difflib
import glob
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.parse

KEEP_RUNS = 20          # run directories kept per repository
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
SHELL_TOOLS = ("Bash", "PowerShell")
SID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
TOOL_ID_RE = re.compile(r'"tool_use_id"\s*:\s*"([^"\\]+)"')
RUN_NAME_RE = re.compile(r"\d{8}-\d{6}(?:-\d+)?$")
LANG = "en"


class Fail(Exception):
    """A user-facing error: printed as is, exit 2."""


class Stale(Exception):
    """The repository moved since plan: exit 3."""


class NotFound(Exception):
    """A session hunk is not in the file any more (a later edit rewrote it)."""


class Ambiguous(Exception):
    """A session edit matches more than one place, or none can be told apart."""


def T(ru, en, **kw):
    return (ru if LANG == "ru" else en).format(**kw)


def plural(n, one, few, many):
    n = abs(n) % 100
    if 11 <= n <= 19:
        return many
    n %= 10
    return one if n == 1 else few if 2 <= n <= 4 else many


def n_files(n):
    return T("{n} " + plural(n, "файл", "файла", "файлов"),
             "{n} file" + ("" if n == 1 else "s"), n=n)


def n_commits(n):
    return T("{n} " + plural(n, "коммит", "коммита", "коммитов"),
             "{n} commit" + ("" if n == 1 else "s"), n=n)


def short(sha):
    return sha[:7] if sha else "-"


# --- git ---------------------------------------------------------------------------------

def git_env(index=None):
    env = dict(os.environ, LC_ALL="C", LANGUAGE="C", GIT_TERMINAL_PROMPT="0",
               GIT_EDITOR="true", GIT_MERGE_AUTOEDIT="no")
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX", "GIT_COMMON_DIR",
                "GIT_OBJECT_DIRECTORY", "GIT_LITERAL_PATHSPECS", "GIT_GLOB_PATHSPECS"):
        env.pop(key, None)
    if index:
        env["GIT_INDEX_FILE"] = index
    return env


def git(cwd, *args, ok=(0,), index=None, stdin=None, literal=False):
    cmd = ["git", "-c", "core.quotepath=off"] + (["--literal-pathspecs"] if literal else [])
    sub = next((a for i, a in enumerate(args) if not a.startswith("-")
                and (i == 0 or args[i - 1] != "-c")), args[0] if args else "")
    try:
        proc = subprocess.run(cmd + list(args), cwd=cwd, env=git_env(index), input=stdin,
                              capture_output=True)
    except OSError as e:  # a missing directory (a deleted worktree), no git binary
        raise Fail(f"git {sub} failed: {e}")
    if ok is not None and proc.returncode not in ok:
        err = (proc.stderr or proc.stdout).decode("utf-8", "replace").strip()
        raise Fail(f"git {sub} failed: {err}")
    return proc


# User config that would touch someone's work on the way: the fast-forward of main in its
# checkout never stashes their changes (merge.autoStash).
NO_SIDE_EFFECTS = ("-c", "merge.autoStash=false")


def git_state(git_dir):
    """The unfinished operation in a checkout's git dir, or None. SQUASH_MSG and MERGE_MSG are
    a message someone prepared (`git merge --squash`, `git cherry-pick -n`) for their next
    commit: any commit in that checkout would delete it."""
    for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge",
                 "rebase-apply", "BISECT_LOG", "SQUASH_MSG", "MERGE_MSG"):
        if os.path.exists(os.path.join(git_dir, name)):
            return name
    return None


OP_NAMES = {"SQUASH_MSG": "merge --squash (SQUASH_MSG)",
            "MERGE_MSG": "merge or cherry-pick -n (MERGE_MSG)"}


def op_name(op):
    return OP_NAMES.get(op, op)


def op_stop(op, git_dir):
    """What is unfinished in a checkout, and the way out of it."""
    if op in ("rebase-merge", "rebase-apply"):
        return T("идёт rebase — заверши его (git rebase --continue) или отмени (git rebase "
                 "--abort)", "a rebase is in progress — finish it (git rebase --continue) or "
                 "abort it (git rebase --abort)")
    if op in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD"):
        verb = {"MERGE_HEAD": "merge", "CHERRY_PICK_HEAD": "cherry-pick",
                "REVERT_HEAD": "revert"}[op]
        go_on = "git commit" if verb == "merge" else f"git {verb} --continue"
        return T("незаконченный {v} — заверши его ({c}) или отмени (git {v} --abort)",
                 "an unfinished {v} — finish it ({c}) or abort it (git {v} --abort)",
                 v=verb, c=go_on)
    if op == "BISECT_LOG":
        return T("идёт git bisect — заверши его (git bisect reset)",
                 "a git bisect is in progress — end it (git bisect reset)")
    if op in ("SQUASH_MSG", "MERGE_MSG"):
        # A message prepared by `git merge --squash` / `git cherry-pick -n` (or left after one
        # was undone with git restore, which keeps it): any commit here would delete it.
        return T("лежит заготовка сообщения коммита от {o}: {p} — закоммить подготовленное "
                 "или, если оно брошено, удали этот файл",
                 "a commit message prepared by {o} is waiting: {p} — commit the prepared "
                 "change, or, if it was abandoned, delete that file",
                 o="git merge --squash" if op == "SQUASH_MSG" else
                 "git merge / cherry-pick -n", p=os.path.join(git_dir, op))
    return T("в репозитории незаконченный {op} — заверши или отмени его",
             "an unfinished {op} is in progress — finish or abort it", op=op_name(op))


def text_of(proc):
    return proc.stdout.decode("utf-8", "surrogateescape")


def errtext(proc):
    """git's message, without its `hint:` lines (advice for a person at the prompt, which would
    crowd out the reason in a one-line status)."""
    text = ((proc.stderr or b"") + (proc.stdout or b"")).decode("utf-8", "replace")
    return "\n".join(ln for ln in text.split("\n") if not ln.startswith("hint:")).strip()


def oneline(s, limit=200):
    s = " ".join(s.split())
    return s if len(s) <= limit else s[:limit - 1] + "…"


def rev(root, ref):
    p = git(root, "rev-parse", "-q", "--verify", ref + "^{commit}", ok=None)
    return p.stdout.decode().strip() if p.returncode == 0 else None


def rev_list(root, include, exclude=None):
    """Commits reachable from include and from none of exclude (a ref or a list of refs)."""
    excludes = [exclude] if isinstance(exclude, str) else list(exclude or [])
    args = ["rev-list", include] + ["^" + e for e in excludes if e] + ["--"]
    return text_of(git(root, *args)).split()


def is_ancestor(root, a, b):
    return git(root, "merge-base", "--is-ancestor", a, b, ok=None).returncode == 0


def parents_of(root, sha):
    return text_of(git(root, "rev-list", "--parents", "-n1", sha, "--")).split()[1:]


def chunks(seq, size=200):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


class Repo:
    def __init__(self, path):
        p = git(path, "rev-parse", "--show-toplevel", ok=None)
        if p.returncode != 0:
            raise Fail(f"not inside a git working tree: {path}")
        self.root = canonical(text_of(p).strip())
        r = self.root
        self.git_dir = os.path.realpath(os.path.join(r, text_of(git(r, "rev-parse",
                                                                    "--git-dir")).strip()))
        self.common_dir = os.path.realpath(os.path.join(r, text_of(git(
            r, "rev-parse", "--git-common-dir")).strip()))
        self.head = rev(r, "HEAD")
        sym = text_of(git(r, "symbolic-ref", "-q", "HEAD", ok=(0, 1))).strip()
        self.branch = sym[len("refs/heads/"):] if sym.startswith("refs/heads/") else None
        self.worktree = self.git_dir != self.common_dir
        self.empty_tree = text_of(git(r, "hash-object", "-t", "tree", "--stdin",
                                      stdin=b"")).strip()

    def base(self):
        return self.head or self.empty_tree

    def ls_tree(self, paths, treeish=None):
        """{path: (mode, sha)} for the blobs at these paths in treeish (default HEAD)."""
        treeish = treeish or self.head
        out = {}
        if not treeish or not paths:
            return out
        for part in chunks(sorted(paths)):
            data = git(self.root, "ls-tree", "-r", "-z", treeish, "--", *part, literal=True).stdout
            for rec in data.split(b"\0"):
                if not rec:
                    continue
                meta, path = rec.split(b"\t", 1)
                mode, kind, sha = meta.decode().split()
                if kind == "blob":
                    out[os.fsdecode(path)] = (mode, sha)
        return out

    def ls_dirs(self, dirs):
        """The directories among `dirs` that exist as trees in HEAD."""
        found = set()
        if not self.head or not dirs:
            return found
        for part in chunks(sorted(dirs)):
            data = git(self.root, "ls-tree", "-z", self.head, "--", *part, literal=True).stdout
            for rec in data.split(b"\0"):
                if rec:
                    found.add(os.fsdecode(rec.split(b"\t", 1)[1]))
        return found

    _batch = None
    _plain = frozenset()
    FILTER_ATTRS = ("text", "eol", "crlf", "filter", "ident", "working-tree-encoding")

    def plain_paths(self, paths):
        """Note which paths no eol or smudge filter applies to: their HEAD blob is the working
        tree's form as is, read through one `cat-file --batch` process (--filters cannot be
        batched: it reports the unfiltered size)."""
        paths = [p for p in paths if "\n" not in p]
        if not paths or text_of(git(self.root, "config", "core.autocrlf",
                                    ok=(0, 1))).strip().lower() in ("true", "1", "yes", "on",
                                                                     "input"):
            return
        data = git(self.root, "check-attr", "-z", "--stdin", *self.FILTER_ATTRS,
                   stdin=b"".join(os.fsencode(p) + b"\0" for p in paths), ok=None)
        if data.returncode != 0:
            return
        f = data.stdout.split(b"\0")
        filtered = {os.fsdecode(f[i]) for i in range(0, len(f) - 2, 3)
                    if f[i + 2] != b"unspecified"}
        self._plain = frozenset(p for p in paths if p not in filtered)

    def head_bytes(self, path, sha):
        """HEAD's version of path as the working tree would hold it (smudge and eol filters
        applied), so it compares with the file on disk."""
        if path in self._plain:
            data = self._batched(sha)
            if data is not None:
                return data
        p = git(self.root, "cat-file", "--filters", f"{self.head}:{path}", ok=None)
        if p.returncode == 0:
            return p.stdout
        return git(self.root, "cat-file", "blob", sha).stdout

    def _batched(self, sha):
        try:
            if self._batch is None:
                self._batch = subprocess.Popen(
                    ["git", "cat-file", "--batch"], cwd=self.root, env=git_env(),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            b = self._batch
            b.stdin.write(sha.encode() + b"\n")
            b.stdin.flush()
            head = b.stdout.readline().split()
            if len(head) != 3 or head[1] != b"blob" or not head[2].isdigit():
                return None
            data = b.stdout.read(int(head[2]))
            b.stdout.read(1)
            return data
        except (OSError, ValueError):
            self.close()
            return None

    def close(self):
        b, self._batch = self._batch, None
        if b is not None:
            try:
                b.stdin.close()
                b.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                b.kill()

    def precompose(self):
        """True when git spells paths in Unicode NFC whatever the filesystem holds (macOS's
        core.precomposeUnicode, which `git init` sets there)."""
        p = git(self.root, "config", "--bool", "core.precomposeunicode", ok=(0, 1))
        return text_of(p).strip() == "true"

    def staged(self):
        """Paths whose index entry differs from HEAD (someone staged something there)."""
        data = git(self.root, "diff-index", "--cached", "-z", "--name-only", "--no-renames",
                   self.base(), "--").stdout
        return {os.fsdecode(p) for p in data.split(b"\0") if p}

    def unmerged(self):
        return bool(git(self.root, "ls-files", "-u", "-z").stdout)

    def status(self):
        """[(XY, path)] — porcelain v1, untracked files listed one by one, no index write."""
        data = git(self.root, "--no-optional-locks", "status", "--porcelain=v1", "-z",
                   "--untracked-files=all", "--no-renames").stdout
        return [(rec[:2].decode(), os.fsdecode(rec[3:])) for rec in data.split(b"\0") if rec]

    def in_progress(self):
        return git_state(self.git_dir)

    def ignored(self, paths):
        if not paths:
            return set()
        data = git(self.root, "check-ignore", "-z", "--stdin", ok=(0, 1),
                   stdin=b"".join(os.fsencode(p) + b"\0" for p in paths)).stdout
        return {os.fsdecode(p) for p in data.split(b"\0") if p}

    def filemode(self):
        p = git(self.root, "config", "--bool", "core.filemode", ok=(0, 1))
        return text_of(p).strip() != "false"


# --- transcripts -------------------------------------------------------------------------

def config_dir():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


def transcript_files(cfg, sid):
    """The session's main transcript and every subagent / workflow-agent transcript."""
    if not SID_RE.match(sid):
        raise Fail(f"not a session id: {sid!r}")
    projects = glob.escape(os.path.join(cfg, "projects"))
    files = sorted(glob.glob(os.path.join(projects, "*", sid + ".jsonl")))
    for d in sorted(glob.glob(os.path.join(projects, "*", sid))):
        files += sorted(glob.glob(os.path.join(glob.escape(d), "subagents", "**", "*.jsonl"),
                                  recursive=True))
    if not files:
        raise Fail(f"no transcript of session {sid} under {os.path.join(cfg, 'projects')}")
    return files


def blocks(entry):
    msg = entry.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def result_text(block, tur):
    parts = []
    content = block.get("content")
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        parts += [c.get("text", "") for c in content if isinstance(c, dict)]
    if isinstance(tur, dict) and isinstance(tur.get("stdout"), str):
        parts.append(tur["stdout"])
    return "\n".join(parts)


_NAMES = {}


def _dir_names(d):
    if d not in _NAMES:
        try:
            _NAMES[d] = os.listdir(d)
        except OSError:
            _NAMES[d] = None
    return _NAMES[d]


def _fold(name):
    return unicodedata.normalize("NFC", name).casefold()


def canonical(path):
    """realpath, with every existing component spelled as it is on disk. A case-insensitive
    filesystem (macOS, Windows) opens `assets/scripts/a.cs` as `Assets/Scripts/A.cs`, and
    realpath keeps the spelling it was given; git compares paths case-sensitively, so a
    misspelt path would be a new file to it. A missing tail (a deleted file) is kept as given."""
    path = os.path.realpath(path)
    drive, rest = os.path.splitdrive(path)
    parts = [p for p in rest.split(os.sep) if p]
    cur = drive + os.sep
    for i, comp in enumerate(parts):
        names = _dir_names(cur)
        if names is None or comp in names:
            if names is None:
                return os.path.join(cur, *parts[i:])
            cur = os.path.join(cur, comp)
            continue
        if not os.path.lexists(os.path.join(cur, comp)):
            return os.path.join(cur, *parts[i:])
        hits = [n for n in names if _fold(n) == _fold(comp)]
        cur = os.path.join(cur, hits[0] if len(hits) == 1 else comp)
    return cur


def event_path(tur, inp, cwd):
    path = (tur or {}).get("filePath") or inp.get("file_path") or inp.get("notebook_path")
    if not isinstance(path, str) or not path:
        return None
    if not os.path.isabs(path):
        if not cwd:
            return None
        path = os.path.join(cwd, path)
    return canonical(path)


def tool_events(name, inp, tur, cwd, text=""):
    """The file events one successful edit-tool call leaves. Kinds:
    patch (structuredPatch hunks), create (whole content: a Write that created the file, or an
    Edit with an empty old_string), rewrite (a Write over an existing file with no patch in the
    transcript, as workflow agents leave it: what the file held before is unknown, so it is
    the session's only when the session created the file), replace (old/new strings from the
    tool input when the transcript has no toolUseResult), opaque (NotebookEdit: no patch)."""
    path = event_path(tur, inp, cwd)
    if not path:
        return []
    if name == "NotebookEdit":
        return [{"path": path, "kind": "opaque"}]
    if tur is not None and tur.get("type") == "create" and isinstance(tur.get("content"), str):
        return [{"path": path, "kind": "create", "content": tur["content"]}]
    if tur is not None and isinstance(tur.get("structuredPatch"), list):
        hunks = [h for h in tur["structuredPatch"] if isinstance(h, dict)]
        return [{"path": path, "kind": "patch", "hunks": hunks}] if hunks else []
    if name == "Write" and isinstance(inp.get("content"), str):
        kind = "create" if "created successfully" in text else "rewrite"
        return [{"path": path, "kind": kind, "content": inp["content"]}]
    edits = [inp] if name == "Edit" else inp.get("edits") if name == "MultiEdit" else None
    out = []
    for ed in edits if isinstance(edits, list) else []:
        old, new = ed.get("old_string"), ed.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str):
            continue
        if old == "":
            out.append({"path": path, "kind": "create", "content": new})
        else:
            out.append({"path": path, "kind": "replace", "old": old, "new": new,
                        "all": bool(ed.get("replace_all"))})
    return out


# A tool result that says the call never ran: refused by the user, a permission rule, the auto
# mode classifier or a safety check, blocked by a PreToolUse hook, or rejected before running.
NEVER_RAN = ("<tool_use_error>", "Permission for this action was denied",
             "Permission for this command was denied", "Permission to use ",
             "The user doesn't want to ", "PreToolUse:")


def never_ran(text):
    text = text.lstrip()
    if text.startswith("Error: "):
        text = text[len("Error: "):]
    return text.startswith(NEVER_RAN)


def read_transcripts(files):
    """(edit events in time order, the shell commands the session's agents sent — any agent's,
    oldest first — as [(text, failed)]). A command refused or blocked before it ran is left out;
    one that ran and exited non-zero is kept, marked failed."""
    uses, seen, events, shell = {}, set(), [], {}
    for fi, path in enumerate(files):
        try:
            fh = open(path, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for ln, line in enumerate(fh):
                if '"tool_use"' not in line and not (
                        '"tool_result"' in line and
                        any((i in uses or i in shell) and i not in seen
                            for i in TOOL_ID_RE.findall(line))):
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if entry.get("type") == "assistant":
                    for b in blocks(entry):
                        name, tid = b.get("name"), b.get("id")
                        if b.get("type") != "tool_use":
                            continue
                        inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                        if name in SHELL_TOOLS and isinstance(inp.get("command"), str):
                            shell.setdefault(tid, [str(entry.get("timestamp") or ""),
                                                   inp["command"], None])
                        elif name in EDIT_TOOLS:
                            uses[tid] = (name, inp, entry.get("cwd"))
                    continue
                if entry.get("type") != "user":
                    continue
                results = [b for b in blocks(entry) if b.get("type") == "tool_result"]
                tur = entry.get("toolUseResult") if len(results) == 1 else None
                ts = str(entry.get("timestamp") or "")
                for k, b in enumerate(results):
                    tid = b.get("tool_use_id")
                    if tid in shell and tid not in seen:
                        seen.add(tid)
                        if b.get("is_error"):
                            refused = never_ran(result_text(b, None)) or (
                                isinstance(tur, str) and never_ran(tur))
                            shell[tid][2] = "refused" if refused else "failed"
                        continue
                    if tid not in uses or tid in seen:
                        continue
                    seen.add(tid)
                    if b.get("is_error") or isinstance(tur, str):
                        continue  # a failed edit changed nothing
                    name, inp, cwd = uses[tid]
                    for j, ev in enumerate(tool_events(name, inp, tur if isinstance(tur, dict)
                                                       else None, cwd or entry.get("cwd"),
                                                       result_text(b, tur))):
                        ev["order"] = (ts, fi, ln, k, len(events))
                        ev["id"] = f"{tid}:{j}"
                        events.append(ev)
    events.sort(key=lambda ev: ev["order"])
    return events, [(cmd, state == "failed") for _, cmd, state in
                    sorted(shell.values(), key=lambda s: s[0]) if state != "refused"]


# --- files the session's shell commands name ---------------------------------------------
# The script never takes a path from a shell command: these are pointers for the model, which
# declares the files that are the session's. A file left out costs work, a file named in vain
# costs a look, so the match is crude on purpose.

NAME_CHARS = r"[\w.@+~-]"
PATH_RUN_RE = re.compile(r"[\w.@+~/-]+")


def path_keys(text):
    """Every run of path characters in the text, and each tail of it that starts after a slash:
    `rm /Users/me/app/src/a.cs` gives `src/a.cs` and `a.cs` among others — a repository path or a
    file name is named when it is one of them, as a whole word."""
    keys = set()
    for run in PATH_RUN_RE.findall(text):
        keys.add(run)
        keys.update(run[i + 1:] for i, c in enumerate(run) if c == "/")
    return keys


def shell_named(cmds, paths, fold, universe=()):
    """(the paths among `paths` that the commands name, {path: indexes of the commands naming
    it, oldest first}).
    A path is named by itself (repository-relative, so also inside an absolute path), or by its
    file name when no other path in `paths` or `universe` (every changed path, the session's own
    files included, also when they are committed) has that name. fold spells text and paths
    alike (Unicode form, letter case where git ignores it)."""
    texts = [fold(c.replace("\\ ", " ").replace("\\", "/")) for c in cmds]
    whole = "\n".join(texts)
    where = {}
    for n, text in enumerate(texts):
        for key in path_keys(text):
            where.setdefault(key, []).append(n)
    names = {}
    for p in set(paths) | set(universe):
        names.setdefault(fold(os.path.basename(p)), []).append(p)
    named, by = [], {}
    for p in paths:
        hits = set()
        for key in [fold(p)] + ([fold(os.path.basename(p))]
                                if len(names[fold(os.path.basename(p))]) == 1 else []):
            if PATH_RUN_RE.fullmatch(key):
                hits.update(where.get(key, ()))
            else:  # a space or another character no run holds: look for it as a whole word
                rx = re.compile(r"(?<!" + NAME_CHARS + ")" + re.escape(key) + r"(?!" +
                                NAME_CHARS + ")")
                if rx.search(whole):
                    hits.update(n for n, text in enumerate(texts) if rx.search(text))
        if hits:
            named.append(p)
            by[p] = sorted(hits)
    return named, by


# --- own commits -------------------------------------------------------------------------

def records_dir(cfg):
    return os.path.join(cfg, "cache", "kensei-commit", "sessions")


def _read_records(cfg, sid):
    try:
        with open(os.path.join(records_dir(cfg), sid + ".json"), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_records(cfg, sids):
    """{"commits": set, "unsynced": {abs path: {stale, entry, commit}}, "pending": [{parent,
    tree, branch, paths, sync}]} merged over the sessions.
    unsynced: real index entries a commit of this skill could not set yet; pending: a commit
    begun but not confirmed (the run may have been killed). The per-path records ("paths":
    {abs path: {event, ts, commit}}) are read per session (collect)."""
    out = {"commits": set(), "unsynced": {}, "pending": []}
    for sid in sids:
        data = _read_records(cfg, sid)
        out["commits"].update(c for c in data.get("commits") or [] if isinstance(c, str))
        for path, u in (data.get("unsynced") or {}).items():
            if isinstance(u, dict) and isinstance(u.get("commit"), str):
                out["unsynced"][path] = u
        for p in data.get("pending") or []:
            if isinstance(p, dict) and isinstance(p.get("tree"), str) and \
                    not any(q["tree"] == p["tree"] and q.get("parent") == p.get("parent")
                            for q in out["pending"]):
                out["pending"].append(p)
    return out


def load_own(cfg, sids):
    return load_records(cfg, sids)["commits"]


def edit_records(cfg, sids, change):
    """Apply change(data) to each session's records file (data as stored, a dict)."""
    d = records_dir(cfg)
    os.makedirs(d, mode=0o700, exist_ok=True)
    for sid in sids:
        path = os.path.join(d, sid + ".json")
        data = _read_records(cfg, sid)
        change(data)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
        os.replace(tmp, path)


def record_own(cfg, sids, shas, paths=None, unsynced=None):
    """Remember, for each session, the commits this skill made (so a later run knows that
    unpushed commits with these ids are the session's own), the last session edit each
    committed path got (so a later run replays only the edits made since), and real index
    entries a commit could not set yet."""
    if not shas and not paths and not unsynced:
        return

    def change(data):
        data["commits"] = sorted(set(c for c in data.get("commits") or [] if isinstance(c, str))
                                 | set(shas or []))
        for key, new in (("paths", paths), ("unsynced", unsynced)):
            data[key] = dict(data.get(key) or {}, **(new or {}))
    edit_records(cfg, sids, change)


def forget(cfg, sids, unsynced=(), pending=None):
    """Drop index entries that need no putting back any more, and a pending commit (it landed and
    was recorded, or never landed)."""
    if not unsynced and pending is None:
        return

    def change(data):
        if unsynced:
            data["unsynced"] = {k: v for k, v in (data.get("unsynced") or {}).items()
                                if k not in unsynced}
        if pending is not None:
            data["pending"] = [p for p in data.get("pending") or [] if not (
                isinstance(p, dict) and p.get("tree") == pending["tree"]
                and p.get("parent") == pending.get("parent"))]
    edit_records(cfg, sids, change)


def settle(cfg, sids, pend, sha, sync=None):
    """A pending commit landed as `sha`: record it as the session's, with its path records and the
    index entries still to set (`sync`, default the ones noted before the commit)."""
    sync = pend.get("sync") if sync is None else sync
    record_own(cfg, sids, [sha], paths={p: dict(r, commit=sha)
                                        for p, r in (pend.get("paths") or {}).items()},
               unsynced={p: dict(u, commit=sha) for p, u in (sync or {}).items()})
    forget(cfg, sids, pending=pend)


# --- text model --------------------------------------------------------------------------
# All matching runs on text normalised to LF ("\r\n" -> "\n"), because transcript patches and
# tool inputs are LF even for CRLF files; terminators are restored when content is rebuilt.

def decode(data):
    return data.decode("utf-8", "surrogateescape")


def encode(text):
    return text.encode("utf-8", "surrogateescape")


def norm(text):
    return text.replace("\r\n", "\n")


def split_lines(text):
    parts = text.split("\n")
    out = [p + "\n" for p in parts[:-1]]
    if parts[-1]:
        out.append(parts[-1])
    return out


def hunk_rows(hunk):
    """[(tag, line)] of a structuredPatch hunk; a line keeps "\n" unless a
    "\\ No newline at end of file" marker follows it."""
    rows = []
    for raw in hunk.get("lines") or []:
        if not isinstance(raw, str) or not raw:
            continue
        tag, body = raw[0], raw[1:]
        if tag == "\\":
            if rows and rows[-1][1].endswith("\n"):
                rows[-1] = (rows[-1][0], rows[-1][1][:-1])
            continue
        if tag in (" ", "-", "+"):
            rows.append((tag, (body[:-1] if body.endswith("\r") else body) + "\n"))
    return rows


def locate(lines, side, expected):
    """Index where `side` occurs exactly: at `expected` if it is there, else the nearest
    occurrence (no fuzz, like GNU patch without fuzz). None when it does not occur."""
    n, m = len(lines), len(side)
    if m == 0:
        return max(0, min(expected, n))
    hi = n - m
    if hi < 0:
        return None
    expected = max(0, min(expected, hi))
    first = side[0]
    for d in range(0, max(expected, hi - expected) + 1):
        for i in ((expected,) if d == 0 else (expected - d, expected + d)):
            if 0 <= i <= hi and lines[i] == first and lines[i:i + m] == side:
                return i
    return None


MAX_FUZZ = 2  # context lines that may be dropped from each end of a hunk, as GNU patch does


def count_at(lines, side):
    """How many places `side` occurs at (up to 2)."""
    m, first, n = len(side), side[0], 0
    for i in range(0, len(lines) - m + 1):
        if lines[i] == first and lines[i:i + m] == side:
            n += 1
            if n > 1:
                break
    return n


def apply_hunks(text, hunks, reverse, guesses=None):
    """Apply a structuredPatch forward, or undo it (reverse). A hunk is matched exactly with
    its full context first, then with up to MAX_FUZZ context lines dropped at each end (a
    neighbouring edit changed them); the changed lines themselves always match exactly — when
    they do not, raises NotFound. A match away from the hunk's own line, or with context
    dropped, counts only when it is the one place in the file the lines occur: a shorter
    pattern also matches lookalike code elsewhere, and the nearest of several is a guess.
    Whether a neighbouring edit really collides with the session's is left to merge-file.
    `guesses` (a list) collects the hunks matched at their own line where the same lines occur
    elsewhere too: the line number is the working tree's at the time of the edit, so it is
    right only when nothing moved the file's lines since — undoing such a hunk may undo a
    lookalike block someone's lines shifted onto that line instead."""
    lines = split_lines(text)
    order = list(reversed(hunks)) if reverse else list(hunks)
    offset = 0
    find_tag = "+" if reverse else "-"
    for h in order:
        rows = hunk_rows(h)
        lead = next((i for i, (t, _) in enumerate(rows) if t != " "), len(rows))
        trail = next((i for i, (t, _) in enumerate(reversed(rows)) if t != " "), len(rows))
        new_len = sum(1 for t, _ in rows if t != "-")
        start = h.get("newStart") if isinstance(h.get("newStart"), int) else 1
        base = (start - 1 if new_len else start) + offset
        at, repeated = None, False
        for fuzz in range(0, MAX_FUZZ + 1):
            cut_front, cut_back = min(fuzz, lead), min(fuzz, trail)
            if fuzz and not (cut_front or cut_back):
                break
            part = rows[cut_front:len(rows) - cut_back]
            find = [ln for t, ln in part if t in (" ", find_tag)]
            put = [ln for t, ln in part if t != find_tag]
            if fuzz and not find:
                break
            at = locate(lines, find, base + cut_front)
            if at is not None and find:
                expected = max(0, min(base + cut_front, len(lines) - len(find)))
                if (fuzz or at != expected) and count_at(lines, find) > 1:
                    at, repeated = None, True
                    continue
                if guesses is not None and count_at(lines, find) > 1:
                    guesses.append(start)
            if at is not None:
                offset = at - cut_front - (start - 1 if new_len else start)
                lines[at:at + len(find)] = put
                break
        if at is None:
            where = f"{start}–{start + max(new_len, 1) - 1}"
            core = [ln for t, ln in rows if t == find_tag]
            if repeated:
                raise Ambiguous("repeated", where)
            if core and locate(lines, core, base + lead) is not None:
                # The session's lines are there; an edit right next to them is not.
                raise NotFound(where, "neighbour")
            raise NotFound(where)
    return "".join(lines)


def occurrences(text, sub):
    out, i = [], text.find(sub)
    while i >= 0:
        out.append(i)
        i = text.find(sub, i + 1)
    return out


def undo_replace(text, ev):
    old, new = norm(ev["old"]), norm(ev["new"])
    if not new:
        raise Ambiguous("deleted-text")
    if ev["all"]:
        if new not in text:
            raise NotFound(None)
        return text.replace(new, old)
    at = occurrences(text, new)
    if not at:
        raise NotFound(None)
    if len(at) > 1:
        # Edit requires old_string to be unique before the edit: keep the candidate that
        # leaves exactly one occurrence of it.
        at = [p for p in at if (text[:p] + old + text[p + len(new):]).count(old) == 1]
        if len(at) != 1:
            raise Ambiguous("repeated")
    p = at[0]
    return text[:p] + old + text[p + len(new):]


def do_replace(text, ev):
    old, new = norm(ev["old"]), norm(ev["new"])
    count = text.count(old)
    if count == 0:
        raise NotFound(None)
    if ev["all"]:
        return text.replace(old, new)
    if count > 1:
        raise Ambiguous("repeated")
    return text.replace(old, new, 1)


def reverse_replay(text, events, guesses=None):
    """The file without these session edits: undo every event, last first (`guesses`: see
    apply_hunks)."""
    for ev in reversed(events):
        if ev["kind"] == "patch":
            text = apply_hunks(text, ev["hunks"], reverse=True, guesses=guesses)
        elif ev["kind"] == "replace":
            text = undo_replace(text, ev)
        elif ev["kind"] == "rewrite":
            raise Ambiguous("rewrite")
        else:
            raise Ambiguous("whole-write")
    return text


def forward(text, events, guesses=None):
    """Replay events forward from `text` (`guesses`: see apply_hunks)."""
    for ev in events:
        if ev["kind"] == "create":
            text = norm(ev["content"])
        elif ev["kind"] == "rewrite":
            raise Ambiguous("rewrite")
        elif ev["kind"] == "patch":
            text = apply_hunks(text, ev["hunks"], reverse=False, guesses=guesses)
        elif ev["kind"] == "replace":
            text = do_replace(text, ev)
    return text


def terminators(raw):
    out = []
    for line in split_lines(raw):
        if line.endswith("\r\n"):
            out.append((line[:-2], "\r\n"))
        elif line.endswith("\n"):
            out.append((line[:-1], "\n"))
        else:
            out.append((line, ""))
    return out


def restore(content, refs):
    """Give LF-normalised `content` the line terminators of the reference texts (raw, in
    priority order): a line matching a reference line takes its terminator, any other line the
    dominant one of the first reference."""
    refs = [r for r in refs if r]
    if not any("\r\n" in r for r in refs):
        return content
    lines = split_lines(content)
    keys = [ln[:-1] if ln.endswith("\n") else ln for ln in lines]
    term = [None] * len(lines)
    for ref in refs:
        rl = terminators(ref)
        sm = difflib.SequenceMatcher(None, [k for k, _ in rl], keys, autojunk=False)
        for a, b, size in sm.get_matching_blocks():
            for j in range(size):
                if term[b + j] is None and rl[a + j][1]:
                    term[b + j] = rl[a + j][1]
    first = refs[0]
    crlf = first.count("\r\n")
    dominant = "\r\n" if crlf * 2 > first.count("\n") else "\n"
    out = []
    for i, ln in enumerate(lines):
        if ln.endswith("\n"):
            out.append(keys[i] + (term[i] or dominant))
        else:
            out.append(ln)
    return "".join(out)


def merge_file(run_dir, current, base, other):
    """git merge-file -p current base other -> (merged text, conflict line ranges)."""
    tmp = os.path.join(run_dir, "merge")
    os.makedirs(tmp, mode=0o700, exist_ok=True)
    names = []
    for label, text in (("current", current), ("base", base), ("other", other)):
        path = os.path.join(tmp, label)
        with open(path, "wb") as fh:
            fh.write(encode(text))
        names.append(path)
    proc = subprocess.run(["git", "merge-file", "-p", "-L", "current", "-L", "base", "-L",
                           "other", *names], capture_output=True, env=git_env())
    shutil.rmtree(tmp, ignore_errors=True)
    if proc.returncode < 0 or proc.returncode > 127:
        raise Fail("git merge-file failed: " + errtext(proc))
    merged = decode(proc.stdout)
    if proc.returncode == 0:
        return merged, []
    ranges, start = [], None
    for i, line in enumerate(merged.split("\n"), 1):
        if line.startswith("<<<<<<< current"):
            start = i
        elif line.startswith(">>>>>>> other") and start is not None:
            ranges.append((start, i))
            start = None
    return None, ranges or [(1, 1)]


# --- per-file attribution ----------------------------------------------------------------

def common_changes(base, a, b):
    """`base` plus only the changes that `a` and `b` both made to it, identically. A change of
    one that touches a different change of the other -> (None, line range in `base`).

    Used where the session's own content S is known (it wrote the whole file): with base S,
    a = the working tree and b = HEAD, a change in the working tree alone was made after the
    session (left out), a change in HEAD alone is the session's uncommitted work being undone in
    HEAD's eyes (left out: S stays), and a change both share — committed since, e.g. a Bash edit
    of the session already in HEAD — is kept, so the commit never reverts it."""
    bl = split_lines(base)

    def changes(other):
        sm = difflib.SequenceMatcher(None, bl, split_lines(other), autojunk=False)
        ol = split_lines(other)
        return [(i1, i2, tuple(ol[j1:j2])) for tag, i1, i2, j1, j2 in sm.get_opcodes()
                if tag != "equal"]

    ca, cb = changes(a), changes(b)
    sa, sb = set(ca), set(cb)

    def touch(c, d):
        return c[0] <= d[1] and d[0] <= c[1]

    for c in ca:
        if c not in sb and any(touch(c, d) for d in cb if d not in sa):
            return None, f"{c[0] + 1}–{max(c[1], c[0] + 1)}"
    out, pos = [], 0
    for i1, i2, rep in sorted(sa & sb):
        out += bl[pos:i1] + list(rep)
        pos = i2
    return "".join(out + bl[pos:]), None


def why(error):
    return str(error.args[0]) if error.args and error.args[0] is not None else None


def is_binary(data):
    return data is not None and b"\0" in data[:8000]


def attribute(events, w_raw, h_raw, declared, run_dir, committed=False, mode_changed=False,
              created=False, head_time=None):
    """What to commit for one path. `events` are the session's edits not committed yet;
    `committed` says earlier ones were (by this skill, as its records say), so HEAD already
    holds them; `mode_changed`: a declared file's exec bit differs from HEAD's; `created`: the
    session created the file (maybe in an edit already committed), so an agent's later Write
    over it is the session's whole content; `head_time`: when HEAD's last commit to the path
    was made (epoch seconds), for a created file HEAD already tracks.
    Returns a dict with status (commit | partial | unchanged | skipped), how (whole | computed |
    delete), content (bytes for computed), reason, detail."""
    def whole(reason=None):
        if w_raw is None:
            if h_raw is None:
                return {"status": "unchanged"}
            return {"status": "commit", "how": "delete", "reason": reason}
        if h_raw is not None and w_raw == h_raw and not mode_changed:
            return {"status": "unchanged"}
        return {"status": "commit", "how": "whole", "reason": reason}

    if declared:
        return whole("declared")
    if w_raw is not None and w_raw == h_raw:
        return {"status": "unchanged"}  # nothing on disk to commit, whatever the edits were
    if committed and not events:
        return {"status": "unchanged"}
    if w_raw is None:  # before the notebook/binary rules: a deletion is never inferred
        if h_raw is None:
            return {"status": "unchanged"}
        return {"status": "skipped", "reason": "deleted"}
    if any(ev["kind"] == "opaque" for ev in events):
        return whole("notebook")
    if is_binary(w_raw) or is_binary(h_raw):
        return whole("binary")
    w_txt = decode(w_raw)
    h_txt = decode(h_raw) if h_raw is not None else None
    w, h = norm(w_txt), (norm(h_txt) if h_txt is not None else None)

    def outcome(c):
        """c: H plus this session's changes, LF-normalised. Lines HEAD has keep HEAD's line
        endings: someone else's editor re-ending the file is not this session's change. When
        HEAD ends every line the same way, new lines end that way too (an editor's LF→CRLF
        conversion of the working tree stays out entirely); only a HEAD with mixed endings
        lets new lines take the working tree's."""
        if h is not None and c == h:
            return {"status": "unchanged"}
        if h is None:
            if c == w:
                return {"status": "commit", "how": "whole"}
            return {"status": "partial", "how": "computed", "content": encode(restore(c, [w_txt]))}
        ends = {t for _, t in terminators(h_txt) if t}
        data = encode(restore(c, [h_txt] if len(ends) == 1 else [h_txt, w_txt]))
        if data == w_raw:
            return {"status": "commit", "how": "whole"}
        return {"status": "partial", "how": "computed", "content": data}

    # A Write over an existing file is the session's whole content only when the session made
    # the file in the first place; otherwise, with no patch, it is a rewrite of unknown content.
    # Nothing tells whether someone changed the file between the creation and that Write, so
    # such a file is committed marked unverified.
    first = next((i for i, ev in enumerate(events) if ev["kind"] == "create"
                  or (created and ev["kind"] == "rewrite")), None)
    if first is not None:
        events = events[:first] + [dict(ev, kind="create", rewrite=True)
                                   if ev["kind"] == "rewrite" else ev for ev in events[first:]]
    anchors = [i for i, ev in enumerate(events) if ev["kind"] == "create"]
    if anchors:
        # The session wrote the whole file: its content from there on is the session's own.
        k = anchors[-1]
        guesses = []
        try:
            s = forward(norm(events[k]["content"]), events[k + 1:], guesses)
        except NotFound:
            return {"status": "skipped", "reason": "rewritten"}
        except Ambiguous as e:
            return {"status": "skipped", "reason": "ambiguous", "detail": why(e)}
        if guesses and s != w:
            # A hunk went to its own line number among lookalike places, and the file differs
            # from what the session made: someone's lines may have moved it onto a lookalike.
            return {"status": "skipped", "reason": "ambiguous", "detail": "repeated"}
        if h is None or s == w:
            res = outcome(s)
        else:
            c, where = common_changes(s, w, h)
            if c is None:
                return {"status": "skipped", "reason": "tangled", "detail": where}
            res = outcome(c)
        try:
            at = datetime.datetime.fromisoformat(
                events[k]["order"][0].replace("Z", "+00:00")).timestamp()
        except (KeyError, IndexError, ValueError):
            at = None
        if h is not None and head_time is not None and (at is None or head_time > int(at)) \
                and res["status"] in ("commit", "partial"):
            # Someone committed the file after the session wrote it (by hand: a create this
            # skill committed leaves no create here). HEAD's change since is not the session's
            # work undone — maybe a person's committed line they then removed on disk — so the
            # commit may take out of HEAD only the session's own later edits: HEAD must be one
            # of the session's states, plus what the working tree shares.
            target = norm(decode(res["content"])) if res.get("how") == "computed" else w

            def reaches(j):
                try:
                    return forward(h, events[j:]) == target
                except (NotFound, Ambiguous):
                    return False
            if not any(reaches(j) for j in range(k + 1, len(events) + 1)):
                return {"status": "skipped", "reason": "tangled", "detail": None}
        if events[k].get("rewrite") and res["status"] in ("commit", "partial"):
            res["reason"] = "unverified"
        return res
    if any(ev["kind"] == "rewrite" for ev in events):
        return {"status": "skipped", "reason": "rewrite-unknown"}
    guesses = []  # hunks undone at their own line among lookalike places
    try:
        b = w
        for ev in reversed(events):
            b = reverse_replay(b, [ev], guesses)
            if h is not None and b == h and w != h:
                # W less its newest session edits is exactly HEAD: all W has beyond HEAD is
                # the session's (the older edits are in HEAD already).
                return outcome(w)
    except NotFound as e:
        if len(e.args) > 1 and e.args[1] == "neighbour":
            return {"status": "skipped", "reason": "tangled", "detail": why(e)}
        return {"status": "skipped", "reason": "notfound", "detail": why(e)}
    except Ambiguous as e:
        # Undo is impossible (deleted text, repeated text, a whole write): replay forward from
        # HEAD instead. Replayed on HEAD, not on the file the edits were made in: every change
        # it makes must be on disk as it is — not undone by someone since, not landed elsewhere.
        if h is not None:
            try:
                c = forward(h, events)
                back, bad = merge_file(run_dir, w, h, c)
                if not bad and back == w:
                    return outcome(c)
            except (NotFound, Ambiguous):
                pass
        return {"status": "skipped", "reason": "ambiguous", "detail": why(e)}
    if h is None:
        if b == "":
            return outcome(w)
        return {"status": "skipped", "reason": "untracked-before"}
    merged, conflicts = merge_file(run_dir, w, b, h)
    if conflicts:
        detail = ", ".join(f"{a}–{z}" for a, z in conflicts)
        return {"status": "skipped", "reason": "tangled", "detail": detail, "lines": conflicts}
    if guesses and merged != w:
        # A hunk was undone at its own line among lookalikes, and the file holds more than the
        # session's edits: someone's lines may have moved a lookalike block onto that line.
        return {"status": "skipped", "reason": "ambiguous", "detail": "repeated"}
    return outcome(merged)


# --- run directories ---------------------------------------------------------------------

def private_dir(path):
    os.makedirs(path, mode=0o700, exist_ok=True)


def new_run_dir(cfg, root):
    name = re.sub(r"[^\w.-]+", "_", os.path.basename(root)) or "repo"
    name += "-" + hashlib.sha1(root.encode("utf-8", "surrogateescape")).hexdigest()[:8]
    repo_dir = os.path.join(cfg, "cache", "kensei-commit", name)
    private_dir(repo_dir)
    base = os.path.join(repo_dir, datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    path, k = base, 1
    while True:
        try:
            os.mkdir(path, 0o700)
            break
        except FileExistsError:
            k += 1
            path = f"{base}-{k}"
    runs = sorted(n for n in os.listdir(repo_dir) if RUN_NAME_RE.match(n)
                  and os.path.isfile(os.path.join(repo_dir, n, "plan.json")))
    for n in runs[:max(len(runs) - (KEEP_RUNS - 1), 0)]:
        shutil.rmtree(os.path.join(repo_dir, n), ignore_errors=True)
    return path


# --- plan --------------------------------------------------------------------------------

def repo_root_of(path):
    """The working-tree root (a directory holding .git) nearest above path, or None."""
    d = path if os.path.isdir(path) else os.path.dirname(path)
    while True:
        if os.path.lexists(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def temp_roots():
    roots = {"/tmp", "/private/tmp", "/var/folders", "/private/var/folders"}
    roots.add(os.path.realpath(tempfile.gettempdir()))
    return roots


def is_temp(path):
    """True for a path in a temporary place — a system temp directory or an agent's
    scratchpad — where a repository is a throwaway clone, not the user's project."""
    if "scratchpad" in path.split(os.sep):
        return True
    return any(path == r or path.startswith(r.rstrip(os.sep) + os.sep) for r in temp_roots())


def rel_of(root, path):
    return os.path.relpath(path, root).replace(os.sep, "/")


def on_disk(path):
    """True when path exists spelt so, letter case included (the Unicode form may differ): a
    case-insensitive filesystem also opens `Foo.cs` when the disk holds `foo.cs`."""
    if not os.path.lexists(path):
        return False
    d, name = os.path.split(path)
    names = _dir_names(d)
    if names is None or name in names:
        return True
    name = unicodedata.normalize("NFC", name)
    return any(unicodedata.normalize("NFC", n) == name for n in names)


def read_worktree(path):
    """(bytes | None, hash): file content, or None when absent (or there only under another
    letter case); a symlink is its target."""
    if not on_disk(path):
        return None, "absent"
    if os.path.islink(path):
        target = os.readlink(path)
        return encode(target), "link:" + target
    if not os.path.isfile(path):
        return None, "absent"
    with open(path, "rb") as fh:
        data = fh.read()
    return data, hashlib.sha256(data).hexdigest()


def declared_path(arg, root, tracked=lambda path: False):
    """A --declare argument as an absolute path: relative ones are relative to the repository
    root, as plan prints paths. A name the disk holds in another letter case is spelt as on
    disk — unless HEAD tracks it as given (tracked(path)): then it is the old name of a
    case-only rename, a deletion next to the new name."""
    path = arg if os.path.isabs(arg) else os.path.join(root, arg)
    path = os.path.normpath(path)
    # Resolve the directory, not the last component: a declared symlink is committed as one.
    name = os.path.basename(path)
    parent = canonical(os.path.dirname(path))
    names = _dir_names(parent) or []
    if name not in names:
        hits = [n for n in names if _fold(n) == _fold(name)]
        if len(hits) == 1 and os.path.lexists(os.path.join(parent, name)) and \
                not tracked(os.path.join(parent, name)):
            name = hits[0]
    return os.path.join(parent, name)


def default_branch(R):
    """origin/HEAD's branch, else main, else master. origin/HEAD is a local note made at clone
    time: after a rename on the server (master → main) it may name a branch origin no longer
    has — ignored when its ref is gone, and overruled by the current branch when that is main or
    master and tracks origin/<itself>."""
    sym = text_of(git(R.root, "symbolic-ref", "-q", "refs/remotes/origin/HEAD", ok=(0, 1))).strip()
    named = sym[len("refs/remotes/origin/"):] if sym.startswith("refs/remotes/origin/") and \
        rev(R.root, sym) else None
    if R.branch in ("main", "master") and R.branch != named and own_upstream(R, R.branch):
        return R.branch
    if named:
        return named
    for name in ("main", "master"):
        if rev(R.root, "refs/heads/" + name) or rev(R.root, "refs/remotes/origin/" + name):
            return name
    return None


def has_origin(R):
    return "origin" in text_of(git(R.root, "remote")).split()


def no_remote(R):
    """The Push line when there is no origin: /commit pushes only there."""
    others = text_of(git(R.root, "remote")).split()
    if others:
        return T("нет — нет remote с именем origin (есть: {r}); запушь сам",
                 "no — no remote named origin (remotes: {r}); push it yourself",
                 r=", ".join(others[:3]) + (" …" if len(others) > 3 else ""))
    return T("нет — нет remote", "no — no remote")


def upstream_of(R, branch):
    p = git(R.root, "rev-parse", "-q", "--verify", "--symbolic-full-name", branch + "@{upstream}",
            ok=None)
    return p.stdout.decode().strip() if p.returncode == 0 else None


def own_upstream(R, branch):
    """The branch's upstream when it is origin/<branch> itself — the only one it is pushed to
    (a branch made from origin/main tracks origin/main, which is not its own place)."""
    up = upstream_of(R, branch)
    return up if up == "refs/remotes/origin/" + branch else None


def branch_ref(R, branch):
    """origin/<branch> when it exists: where the branch alone is pushed — also when it was pushed
    without -u or tracks origin/main."""
    up = "refs/remotes/origin/" + branch
    return up if rev(R.root, up) else None


def unrelated(R, default):
    """True when the branch shares no history with the default branch — an orphan (`git
    checkout --orphan`), before its first commit or after: there is nothing to merge it into,
    and it goes to origin/<branch> alone. A merge would need --allow-unrelated-histories and
    put main's history into it (and the branch's files into main)."""
    if not R.head:
        return True
    refs = [r for r in ("refs/heads/" + default, "refs/remotes/origin/" + default)
            if rev(R.root, r)]
    return bool(refs) and all(git(R.root, "merge-base", "HEAD", r, ok=None).returncode == 1
                              for r in refs)


def no_merge_passes(R, branch, default, own):
    """True when `/commit no-merge` would push the branch: nothing on it beyond origin/<branch>
    (or origin/main) that /commit did not make, and nothing on origin/<branch> it lacks."""
    up, up_main = branch_ref(R, branch), "refs/remotes/origin/" + default
    if up and rev_list(R.root, up, "HEAD"):
        return False
    return not foreign_commits(R, "HEAD", published_refs(R, up, up_main), own,
                               merged=(up_main, up))


def published_refs(R, *refs):
    """The refs among these that exist: what is on them is on origin already. Pushing the branch
    alone publishes nothing that origin/<branch> or origin/main holds (a merge of origin/main
    into the branch brings main's commits along, and they are not someone's unpushed work)."""
    return [r for r in refs if r and rev(R.root, r)]


def worktrees(R):
    """[(path, branch ref | None, prunable)] of every worktree of the repository."""
    out, path, ref, prunable = [], None, None, False
    for line in text_of(git(R.root, "worktree", "list", "--porcelain")).split("\n") + [""]:
        if line.startswith("worktree "):
            path, ref, prunable = line[len("worktree "):], None, False
        elif line.startswith("branch "):
            ref = line[len("branch "):]
        elif line == "prunable" or line.startswith("prunable "):
            prunable = True
        elif not line and path:
            out.append((path, ref, prunable or not os.path.isdir(path)))
            path, ref, prunable = None, None, False
    return out


def checkout_of(R, branch):
    """(path, gone) of the worktree that has the branch checked out, or (None, False). gone:
    the worktree's directory was deleted (`git worktree prune` frees the branch)."""
    for path, ref, gone in worktrees(R):
        if ref == "refs/heads/" + branch:
            return path, gone
    return None, False


def changed_paths(R, a, b):
    """The paths that differ between two commits (`a...b`: since their merge base)."""
    data = git(R.root, "diff", "--name-only", "-z", "--no-renames", a, *([b] if b else []),
               "--").stdout
    return {os.fsdecode(p) for p in data.split(b"\0") if p}


def in_the_way(where, paths):
    """The paths among `paths` with uncommitted tracked changes in the checkout at `where`: a
    merge or fast-forward there that changes them is refused by git (and must never stash)."""
    data = git(where, "--no-optional-locks", "status", "--porcelain=v1", "-z",
               "--untracked-files=no", "--no-renames").stdout
    return sorted({os.fsdecode(rec[3:]) for rec in data.split(b"\0") if rec} & set(paths))


def unity_asset(R, path):
    """A path Unity imports and gives a .meta: inside Assets/ of a Unity project (ProjectSettings/
    beside it), with no hidden or `~` part and not *.tmp."""
    parts = path.split("/")
    if "Assets" not in parts[:-1] or path.endswith(".tmp"):
        return False
    i = parts.index("Assets")
    if any(p.startswith(".") or p.endswith("~") or p.lower() == "cvs" for p in parts[i + 1:]):
        return False
    return os.path.isdir(os.path.join(R.root, *parts[:i], "ProjectSettings"))


def back_to(R, d, then=""):
    """The way back to the default branch a branch stop names: `git switch d` — or, when git
    would refuse it (uncommitted changes in files the switch changes; /commit never stashes),
    why not and what works instead."""
    hit = in_the_way(R.root, changed_paths(R, "refs/heads/" + d, "HEAD")) \
        if rev(R.root, "refs/heads/" + d) else []
    if not hit:
        return T("вернись на {d} (git switch {d}) и снова /commit{x}",
                 "go back to {d} (git switch {d}) and run /commit again{x}", d=d, x=then)
    return T("git switch {d} не пройдёт: в {p} незакоммиченные правки (может, чужие), а {d} эти "
             "файлы меняет; stash не делаю — коммить свою работу из отдельного worktree от {d} "
             "(git worktree add ../<папка> {d}, перенеся туда свои правки) или сначала "
             "разберись с правками в {p}", "git switch {d} would be refused: {p} has uncommitted "
             "changes (maybe someone else's) that {d} changes; no stash — commit your work from "
             "a worktree of {d} (git worktree add ../<dir> {d}, moving your edits there) or deal "
             "with the changes in {p} first", d=d,
             p=", ".join(hit[:3]) + (" …" if len(hit) > 3 else ""))


def checkout_blocks(d, where, paths, no_merge):
    return T("в checkout {d} ({w}) незакоммиченные правки в {p} — merge перезаписал бы их; "
             "закоммить или убери их там и запусти /commit снова{x}",
             "the checkout of {d} ({w}) has uncommitted changes in {p} that the merge would "
             "overwrite; commit or remove them there and run /commit again{x}", d=d, w=where,
             p=", ".join(paths[:5]) + (" …" if len(paths) > 5 else ""),
             x=T(", или /commit no-merge — запушить только ветку",
                 ", or /commit no-merge to push the branch alone") if no_merge else "")


def subjects(R, shas, limit=5):
    out = []
    for sha in shas[:limit]:
        s = text_of(git(R.root, "log", "-1", "--format=%h %s", sha, "--")).strip()
        out.append(oneline(s, 70))
    more = len(shas) - limit
    return "; ".join(out) + (T(" … и ещё {n}", " … and {n} more", n=more) if more > 0 else "")


def clean_merge(R, sha, parents):
    """True when the merge commit holds exactly what git's own merge of its two parents gives
    (`git merge-tree --write-tree`, git 2.38+): nothing added, dropped or resolved by hand in
    it. A hand-resolved conflict, an amended merge or `-s ours` is someone's work."""
    if len(parents) != 2:
        return False
    p = git(R.root, "merge-tree", "--write-tree", "--no-messages", *parents, ok=None)
    tree = text_of(p).split("\n", 1)[0].strip()
    return p.returncode == 0 and bool(tree) and \
        tree == text_of(git(R.root, "rev-parse", sha + "^{tree}")).strip()


def foreign_commits(R, include, exclude, own, merged=()):
    """Commits reachable from include and from none of exclude that are not this session's
    (`own`: the ones this skill recorded). A merge commit whose other parent is in one of
    `merged` (main, origin/main, origin/<branch>: already published or about to be) and whose
    tree is git's clean merge of its parents brings nothing new — a `git merge` / `git pull`
    of main into the branch, whoever ran it — and does not count."""
    excludes = [exclude] if isinstance(exclude, str) else list(exclude or [])
    refs = [r for r in merged if r and rev(R.root, r)]
    data = text_of(git(R.root, "rev-list", "--parents", include,
                       *["^" + e for e in excludes if e], "--"))
    out = []
    for row in data.split("\n"):
        shas = row.split()
        if not shas or shas[0] in own:
            continue
        if len(shas) == 3 and refs and any(is_ancestor(R.root, shas[2], r) for r in refs) \
                and clean_merge(R, shas[0], shas[1:]):
            continue
        out.append(shas[0])
    return out


def still_changed(root, rels):
    """The paths among `rels` that git status in `root` still shows (changed or untracked), or
    None when git cannot read that repository."""
    left = set()
    try:
        for part in chunks(sorted(rels)):
            data = git(root, "--no-optional-locks", "status", "--porcelain=v1", "-z",
                       "--untracked-files=all", "--no-renames", "--", *part, literal=True).stdout
            left.update(os.fsdecode(rec[3:]) for rec in data.split(b"\0") if rec)
    except Fail:
        return None
    return left


def unpushed_own(root, own):
    """True when the repository at `root` holds commits this session's /commit recorded that
    no remote-tracking ref has."""
    if not own or not text_of(git(root, "remote", ok=None)).split():
        return False  # with no remote, nothing is ever pushed: nothing waits for /commit there
    p = git(root, "rev-list", "--branches", "--not", "--remotes", "--", ok=None)
    return p.returncode == 0 and bool(own & set(text_of(p).split()))


# --- /ticket runs, tasks and PR repositories ----------------------------------------------

def task_runs_root():
    """The ticket skill's runs root (its guard reads the same variable)."""
    return os.path.expanduser(os.environ.get("KENSEI_TASK_RUNS_DIR") or "~/.claude/task-runs")


TICKET_CMD = re.compile(r"^\s*<command-message>[^<]*</command-message>\s*"
                        r"<command-name>/(?:[\w.-]+:)?ticket</command-name>\s*"
                        r"(?:<command-args>(.*?)</command-args>)?", re.S)
TICKET_SKILL = re.compile(r"(?:^|:)ticket$")


def task_of(ref):
    """(id, url or None) of a task given as a tracker link or an id: the ClickUp, Jira, Linear,
    GitHub, YouTrack, Asana, Notion id as the ticket skill takes it. A link is read from an
    issue a board opened (?selectedIssue=, ?issue=, ?p=), then the segment after a marker
    (/issue/, /browse/, /t/…), then a Notion page id or an Asana gid; anything else -> None
    rather than a guess from the last segment (a board's own id)."""
    ref = (ref or "").strip()
    idlike = re.compile(r"[A-Za-z0-9][\w.-]*")
    if re.match(r"(?i)https?://", ref):
        parts = urllib.parse.urlsplit(ref)
        query = urllib.parse.parse_qs(parts.query)
        segs = [s for s in parts.path.split("/") if s]
        tid = next((query[k][0] for k in ("selectedIssue", "issue", "p")
                    if k in query and idlike.fullmatch(query[k][0])), None)
        for i, s in enumerate(segs[:-1]):
            if tid is None and s.lower() in ("issue", "issues", "browse", "task", "t"):
                tid = segs[-1] if s.lower() == "t" else segs[i + 1]
        if tid is None and segs:
            last = re.search(r"(?:^|-)([0-9a-f]{32})$|^(\d{6,})$", segs[-1])  # Notion, Asana
            tid = last and (last.group(1) or last.group(2))
        return (tid, ref) if tid and idlike.fullmatch(tid) else None
    tid = ref.lstrip("#")
    return (tid, None) if idlike.fullmatch(tid) else None


def ticket_run(cfg, sid):
    """The /ticket run of session `sid`: its task id ('' when not known), or None when the
    session never ran /ticket. Read from the ticket guard's session marker (<runs root>/.guard/
    <session id>, holding "run: <run dir>") and, for a session whose hooks are not back (a
    resume), from a /ticket invocation in its main transcript."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "", sid or "")
    if not safe:
        return None
    task = None
    try:
        with open(os.path.join(task_runs_root(), ".guard", safe), encoding="utf-8",
                  errors="replace") as fh:
            m = re.search(r"^run: (.+)$", fh.read(4096), re.M)
        task = os.path.basename(m.group(1).strip().rstrip("/")) if m else ""
    except OSError:
        pass
    if task:
        return task
    for path in glob.glob(os.path.join(glob.escape(os.path.join(cfg, "projects")), "*",
                                       safe + ".jsonl")):
        try:
            fh = open(path, "rb")
        except OSError:
            continue
        with fh:
            for raw in fh:
                if b"ticket" not in raw:
                    continue
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(entry, dict) or entry.get("isSidechain"):
                    continue
                args = None
                if entry.get("type") == "user" and not entry.get("isMeta"):
                    msg = entry.get("message")
                    content = msg.get("content") if isinstance(msg, dict) else None
                    if isinstance(content, list):
                        content = "\n".join(b.get("text", "") for b in content if
                                            isinstance(b, dict) and b.get("type") == "text")
                    m = TICKET_CMD.match(content) if isinstance(content, str) else None
                    args = (m.group(1) or "") if m else None
                elif entry.get("type") == "assistant":
                    for b in blocks(entry):
                        inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                        if b.get("type") == "tool_use" and b.get("name") == "Skill" and \
                                TICKET_SKILL.search(str(inp.get("skill") or "")):
                            args = str(inp.get("args") or "")
                if args is not None:
                    words = args.split()
                    found = task_of(words[0]) if words else None
                    task = found[0] if found else (task or "")
    return task


def refuse_in_ticket(cfg, sids, lab):
    """Inside a /ticket run its publish gate commits — not this script. -> exit 1 after the
    stop line, or None when no ticket run is on."""
    own = os.environ.get("CLAUDE_CODE_SESSION_ID")
    for sid in [own] if own else sids:
        task = ticket_run(cfg, sid)
        if task is not None:
            print(label(lab) + T("нет — стоп: идёт прогон /ticket{t} — коммит делает шлюз тикета",
                                 "no — stop: a /ticket run{t} is on — its gate makes the commit",
                                 t=" " + task if task else ""),
                  flush=True)
            print("---\nverdict: stop\nticket: " + (task or "?"))
            return 1
    for sid in sids if own else []:  # an earlier session's work passed with --session
        task = ticket_run(cfg, sid) if sid != own else None
        if task is not None:
            print(label(T("Задача", "Task")) + T(
                "сессия {s} вела /ticket{t} — её работа идёт мимо шлюза тикета (/resume {s}, "
                "чтобы пройти шлюз)", "session {s} ran /ticket{t} — its work bypasses the ticket "
                "gate (/resume {s} to go through it)", s=sid, t=" " + task if task else ""))
    return None


def mentions_task(message, task):
    """True when the commit message names the task: its link, or its id as a whole word (a
    number only as #<n>)."""
    text = message.decode("utf-8", "replace") if isinstance(message, bytes) else message
    if task.get("url") and task["url"] in text:
        return True
    tid = re.escape(task["id"])
    rx = rf"#{tid}(?!\w)" if task["id"].isdigit() else rf"(?<![\w-]){tid}(?![\w-])"
    return bool(re.search(rx, text, re.I))


PR_SUBJECT = re.compile(r"^Merge pull request #\d+|^Merge branch '[^']+' into '[^']+'|"
                        r"^Merged in \S+ \(pull request #\d+\)")
SQUASH_SUBJECT = re.compile(r"\(#\d+\)\s*$")  # also a direct commit naming an issue: see below


def repo_mode(R, default, pr=False, main=False):
    """("pr" | "direct", why): how work reaches the default branch here — through pull requests
    when most of its recent first-parent history came that way (GitHub's "Merge pull request
    #…" or a squash "… (#12)" GitHub committed, GitLab's "Merge branch 'x' into 'main'"), else
    directly; `pr` / `main` (the user's words or the project's rules, passed by the model)
    decide first."""
    if pr or main:
        return ("pr" if pr else "direct"), T("по аргументу", "as asked")
    ref = next((r for r in ("refs/remotes/origin/" + default, "refs/heads/" + default)
                if default and rev(R.root, r)), None)
    if not ref:
        return "direct", None
    rows = text_of(git(R.root, "log", "--first-parent", "-n", "20", "--format=%ae%x00%ce%x00%s",
                       ref, "--", ok=None)).split("\n")
    rows = [r.split("\0") for r in rows if r.count("\0") == 2 and r.split("\0")[2].strip()]
    # "(#12)" at the end is a squash merge only when the forge committed it (GitHub's
    # noreply@github.com, a merge bot): a direct commit naming an issue has its author as committer.
    hits = sum(1 for ae, ce, s in rows if PR_SUBJECT.search(s) or (SQUASH_SUBJECT.search(s) and (
        "noreply@" in ce.lower() or ce.lower() != ae.lower())))
    if len(rows) >= 4 and hits * 2 >= len(rows):
        return "pr", T("{n} из {t} последних коммитов {d} пришли через PR",
                       "{n} of the last {t} commits of {d} came through PRs", n=hits,
                       t=len(rows), d=default)
    return "direct", None


TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    "a b v g d e e zh z i y k l m n o p r s t u f kh ts ch sh sch - y - e yu ya"
                    .split()))


def slug(text, limit=40):
    """A short kebab-case branch slug from a commit subject."""
    out = "".join(TRANSLIT.get(ch, ch) for ch in (text or "").lower())
    out = re.sub(r"[^a-z0-9]+", "-", out).strip("-")
    return out[:limit].rstrip("-") or "work"


def branch_problem(R, name, task_id=None):
    """Why the new branch cannot be made (an invalid name, a branch of that name, another
    branch of the same task), or None."""
    if git(R.root, "check-ref-format", "--branch", name, ok=None).returncode != 0:
        return T("{b} — не годится как имя ветки", "{b} is not a valid branch name", b=name)
    taken = [name] if rev(R.root, "refs/heads/" + name) else []
    if task_id:
        rows = text_of(git(R.root, "for-each-ref", "--format=%(refname:short)",
                           "refs/heads/task/", ok=None)).split()
        taken += [b for b in rows if b == "task/" + task_id or b.startswith(f"task/{task_id}-")]
    if taken:
        where, gone = checkout_of(R, taken[0])
        if where and not gone:  # `git switch` onto it would fail
            return T("ветка {b} уже есть и выгружена в worktree {p} — коммить там",
                     "branch {b} already exists and is checked out in the worktree at {p} — "
                     "commit there", b=taken[0], p=where)
        # with --task any other name stops the same: no other name is offered
        other = "" if task_id else T(", или передай другое имя в --branch",
                                     ", or pass another name with --branch")
        if is_ancestor(R.root, "refs/heads/" + taken[0], "HEAD"):  # landed in main already
            return T("ветка {b} уже в {d} — удали её (git branch -d {b}) и снова /commit",
                     "branch {b} is already in {d} — delete it (git branch -d {b}) and run "
                     "/commit again", b=taken[0], d=R.branch) + other
        return T("ветка {b} уже есть — на неё не переключаюсь; переключись сам (git switch {b}) "
                 "и снова /commit", "branch {b} already exists — not switching onto it; switch "
                 "yourself (git switch {b}) and run /commit again", b=taken[0]) + other
    return None


def collect(args):
    _NAMES.clear()
    cfg = config_dir()
    sids = args.session or ([os.environ["CLAUDE_CODE_SESSION_ID"]]
                            if os.environ.get("CLAUDE_CODE_SESSION_ID") else [])
    if not sids:
        raise Fail("no session id: pass --session or run inside Claude Code "
                   "(CLAUDE_CODE_SESSION_ID)")
    sids = list(dict.fromkeys(sids))
    files, file_sid = [], []  # file_sid[i]: the session transcript files[i] belongs to
    for sid in sids:
        found = transcript_files(cfg, sid)
        files += found
        file_sid += [sid] * len(found)
    events, cmds = read_transcripts(files)
    # Repositories in temp places (an agent's scratchpad clone) are throwaway work, not the
    # user's project — unless this run is in a temp place itself.
    here_temp = is_temp(canonical(os.getcwd()))
    try:
        R = Repo(os.path.abspath(args.repo))
    except Fail:
        if args.repo != ".":
            raise
        # Started outside a repository (a session in ~): take the one this session edited —
        # among those where something of it is left: a file git status still shows, or a
        # commit its /commit recorded and nothing pushed yet.
        edited = {}
        for p in (ev["path"] for ev in events):
            r = repo_root_of(p)
            if r:
                edited.setdefault(canonical(r), set()).add(p)
        temps = [r for r in edited if is_temp(r) and not here_temp]
        own = load_own(cfg, sids)
        roots, done = [], []
        for r in sorted(set(edited) - set(temps)):
            left = still_changed(r, {rel_of(r, p) for p in edited[r]})
            (roots if left is None or left or unpushed_own(r, own) else done).append(r)
        if len(roots) != 1:
            clones = f"{len(temps)} temporary clone{'s' if len(temps) != 1 else ''} (left alone)"
            spent = f"{len(done)} with nothing of it left to commit or push"
            also = "; also " + ", ".join(([spent] if done else []) + ([clones] if temps else []))
            raise Fail("the current directory is not a git repository" + (
                "; this session's work is left in: " + ", ".join(roots) + " — run plan with "
                "--repo <path> for each" + (also if done or temps else "") if roots else
                ", and nothing of this session's is left to commit or push in the repositories "
                "it edited (" + spent + (f"; also {clones}" if temps else "") + ")" if done else
                ", and this session edited files only in " + clones if temps else
                ", and this session edited no file in any repository"))
        R = Repo(roots[0])
        print(T("Репозиторий: {r} — текущая папка не в git; несделанное этой сессии есть "
                "только в нём", "Repository: {r} — the current directory is not in git; this "
                "is the only repository with this session's work left", r=R.root))
    run_dir = os.path.abspath(args.out) if args.out else new_run_dir(cfg, R.root)
    private_dir(run_dir)

    # Which paths: this repository, another repository, or no repository at all. Paths are
    # spelt as git spells them: NFC where git precomposes Unicode (macOS), whatever spelling the
    # filesystem keeps on disk.
    nfc = R.precompose()

    def git_rel(root, p):
        rel = rel_of(root, p)
        return unicodedata.normalize("NFC", rel) if nfc else rel
    by_path, other, outside, temp_clones = {}, {}, set(), set()
    for ev in events:
        p = ev["path"]
        root = repo_root_of(p)
        if root is None:
            outside.add(p)
            continue
        root = canonical(root)
        rel = git_rel(root, p)
        if rel == ".git" or rel.startswith(".git/"):
            outside.add(p)
        elif root == R.root:
            by_path.setdefault(rel, []).append(ev)
        elif is_temp(root) and not here_temp:
            temp_clones.add(root)
        else:
            other.setdefault(root, set()).add(rel)
    # Another repository is named only for the session's files still changed there (committed
    # there since, nothing to /commit); one git cannot read keeps all its files.
    for root, rels in list(other.items()):
        left_there = still_changed(root, rels)
        if left_there is None:
            continue
        if left_there:
            other[root] = left_there
        else:
            del other[root]
    status_rows = R.status()
    declared, missing_decl, refused, untracked_decl = set(), [], [], []
    decl_arg = {}

    def in_head(path):
        rel = git_rel(R.root, path)
        return bool(R.head) and rel in R.ls_tree([rel])
    for arg in args.declare or []:
        p = declared_path(arg, R.root, in_head)
        root = repo_root_of(p)
        if root is None or canonical(root) != R.root:
            missing_decl.append(arg)
            continue
        rel = git_rel(R.root, p)
        decl_arg.setdefault(rel, arg)
        if rel == ".":
            refused.append(arg)  # the whole repository would be `git add -A`
            continue
        if os.path.isdir(p) and not os.path.islink(p):
            # A folder declares its changed tracked files. The untracked files there that this
            # session's own Edit or Write calls created or changed are its own too; the rest
            # may be anyone's and are named, to be declared one by one.
            prefix = rel.rstrip("/") + "/"
            under = [(xy, path) for xy, path in status_rows if path.startswith(prefix)]
            hits = [path for xy, path in under if xy not in ("??", "!!")]
            for xy, path in under:
                if xy == "??":
                    (hits if by_path.get(path) else untracked_decl).append(path)
            declared.update(hits)
            if not under:
                missing_decl.append(arg)
        else:
            declared.add(rel)
    for rel in declared:
        by_path.setdefault(rel, [])
    # A deleted file named with the wrong case (a case-insensitive filesystem opened it under
    # any spelling), by an edit or a declaration: take git's spelling of it — unless HEAD has
    # it spelt as given (the old name of a case-only rename stays a deletion).
    gone = [rel for rel in by_path if not os.path.lexists(os.path.join(R.root, rel))]
    gone_in_head = R.ls_tree(gone) if gone else {}
    gone = [rel for rel in gone if rel not in gone_in_head]
    if gone:
        tracked = {}
        for p in git(R.root, "ls-files", "-z").stdout.split(b"\0"):
            if p:
                tracked.setdefault(_fold(os.fsdecode(p)), os.fsdecode(p))
        for rel in gone:
            real = tracked.get(_fold(rel))
            if real and real != rel:
                by_path.setdefault(real, []).extend(by_path.pop(rel))
                by_path[real].sort(key=lambda ev: ev["order"])
                if rel in declared:
                    declared.discard(rel)
                    declared.add(real)
                    decl_arg.setdefault(real, decl_arg.get(rel))
    # A declared file that is nowhere — not on disk, not in the index, not in HEAD — is a wrong
    # name: say so instead of counting it unchanged.
    nowhere = [rel for rel in declared if not by_path.get(rel)
               and not os.path.lexists(os.path.join(R.root, rel))]
    if nowhere:
        known = set(R.ls_tree(nowhere)) | set(index_entries(R, nowhere))
        for rel in sorted(set(nowhere) - known):
            declared.discard(rel)
            by_path.pop(rel, None)
            missing_decl.append(decl_arg.get(rel) or rel)

    # What the session already committed: only what this skill recorded (per path, the last
    # edit each commit holds). HEAD holds those edits, so only later ones are replayed. A commit
    # the session made with its own `git commit` in Bash is not the skill's: it is foreign to
    # publishing, and the replay finds its edits in HEAD only where they merge cleanly (the same
    # change on both sides); a file it cannot split is skipped, pointing at --declare.
    rec = load_records(cfg, sids)
    prefix = R.root.rstrip(os.sep) + os.sep

    def here(pend):
        """The pending commit was made in this repository (records before 2.2.1 name it only
        by the paths they hold)."""
        if pend.get("repo"):
            return pend["repo"] == R.common_dir
        keys = list(pend.get("paths") or {}) + list(pend.get("sync") or {})
        return any(k.startswith(prefix) for k in keys)
    if any(here(p) for p in rec["pending"]):
        # A run killed right after `git commit` (or while its post-commit hook ran) recorded
        # nothing more: the commit that holds exactly the planned tree on the planned parent
        # is that run's. Another repository's pending commit waits for a plan there.
        for pend in filter(here, rec["pending"]):
            sha = landed(R, pend)
            if sha:
                settle(cfg, sids, pend, sha)
            else:
                forget(cfg, sids, pending=pend)
        rec = load_records(cfg, sids)
    own = set(rec["commits"])

    def in_history(sha):
        return bool(sha and R.head and rev(R.root, sha) and is_ancestor(R.root, sha, R.head))

    # Per session: a commit recorded under a session holds that session's edits only. A run
    # with sessions A and B records under both; a run with A alone says nothing of B's edits.
    path_recs = {sid: {p: r for p, r in (_read_records(cfg, sid).get("paths") or {}).items()
                       if isinstance(r, dict) and isinstance(r.get("ts"), str)} for sid in sids}

    def uncommitted(rel, evs):
        """The path's edits that no commit of this skill in HEAD holds yet, in time order: per
        session, the ones after the last edit its own record names."""
        absp = os.path.join(R.root, rel)
        done = set()
        for sid in sids:
            r = path_recs[sid].get(absp)
            if not (r and in_history(r.get("commit"))):
                continue
            mine = [ev for ev in evs if file_sid[ev["order"][1]] == sid]
            ids = [ev["id"] for ev in mine]
            if r.get("event") in ids:
                done.update(ids[:ids.index(r["event"]) + 1])
            else:
                done.update(ev["id"] for ev in mine if ev["order"][0] <= r["ts"])
        return [ev for ev in evs if ev["id"] not in done]

    head_map = R.ls_tree(by_path.keys())
    staged = R.staged()
    candidates = sorted(by_path)
    untracked = [p for p in candidates if p not in head_map]
    ignored = R.ignored(untracked)
    filemode = R.filemode()
    ignorecase = text_of(git(R.root, "config", "--bool", "core.ignorecase", ok=(0, 1))
                         ).strip() == "true"
    # A file git tracks under another letter case than the disk has (a case-only rename of it
    # or its folder that git does not see): not new, and not committed under the disk's name.
    case_disk = {}
    if ignorecase and any(p not in declared and by_path[p] for p in untracked):
        tracked = {_fold(os.fsdecode(x)): os.fsdecode(x)
                   for x in git(R.root, "ls-files", "-z").stdout.split(b"\0") if x}
        case_disk = {p: tracked[_fold(p)] for p in untracked if p not in declared and
                     unicodedata.normalize("NFC", tracked.get(_fold(p), p)) !=
                     unicodedata.normalize("NFC", p)}
    # A tracked path git status does not list is the same on disk, in the index and in HEAD:
    # nothing to commit, whatever the edits were — no HEAD read, no replay (one git process per
    # file on a repository with `* text=auto`). A file someone marked assume-unchanged or
    # skip-worktree is hidden from git status the same way.
    listed = {path for _, path in status_rows}
    R.plain_paths([p for p in candidates if p in head_map and p in listed])
    hidden = set()  # assume-unchanged (a lowercase tag) or skip-worktree (S): git status is mum
    quiet = [p for p in candidates if p in head_map and p not in listed]
    for part in chunks(quiet):
        rows = git(R.root, "ls-files", "-v", "-z", "--", *part, literal=True, ok=None).stdout
        hidden |= {os.fsdecode(r[2:]) for r in rows.split(b"\0")
                   if r[:1].islower() or r[:1] == b"S"}
    items = []
    for rel in candidates:
        evs = by_path[rel]
        absp = os.path.join(R.root, rel)
        todo = uncommitted(rel, evs)
        cut = len(evs) - len(todo)
        item = {"path": rel, "events": len(todo), "declared": rel in declared}
        if cut:
            item["committed_events"] = cut
        if evs:
            item["last_event"] = {"event": evs[-1].get("id"), "ts": evs[-1]["order"][0]}
        if rel in ignored and rel not in head_map:
            item.update(status="skipped", reason="ignored")
            items.append(item)
            continue
        if os.path.isdir(absp) and not os.path.islink(absp):
            item.update(status="skipped", reason="directory")
            items.append(item)
            continue
        if rel in case_disk:
            item.update(status="skipped", reason="case-disk", detail=case_disk[rel])
            items.append(item)
            continue
        if rel in head_map and rel not in listed and rel not in declared and \
                not os.path.islink(absp):
            item["status"] = "unchanged"
            if todo and rel in hidden:
                item.update(status="skipped", reason="hidden")
            items.append(item)
            continue
        w_raw, w_hash = read_worktree(absp)
        h = head_map.get(rel)
        h_raw = R.head_bytes(rel, h[1]) if h else None
        if os.path.islink(absp) and w_raw is not None:
            same = h is not None and h[0] == "120000" and \
                w_raw == git(R.root, "cat-file", "blob", h[1]).stdout
            res = {"status": "unchanged"} if same else {"status": "commit", "how": "whole"}
        else:
            # A declared file whose only change is its exec bit (the session's chmod).
            chmod = rel in declared and filemode and w_raw is not None and os.name != "nt" \
                and h is not None and h[0] in ("100644", "100755") and \
                ("100755" if os.access(absp, os.X_OK) else "100644") != h[0]
            created = any(ev["kind"] == "create" for ev in evs)
            when = text_of(git(R.root, "log", "-1", "--format=%ct", "HEAD", "--", rel,
                               literal=True)).strip() if h and created else ""
            res = attribute(todo, w_raw, h_raw, rel in declared, run_dir, committed=cut > 0,
                            mode_changed=chmod, created=created,
                            head_time=int(when) if when.isdigit() else None)
        if res.get("status") in ("commit", "partial") and h and w_raw is not None \
                and h[0] != "120000" and rel not in R._plain:
            # git's eol filters would renormalise HEAD's own lines (a CRLF blob under text=auto
            # or autocrlf): every line of the file would change in the commit
            again = git(R.root, "hash-object", "--stdin", "--path=" + rel, stdin=h_raw, ok=None)
            if again.returncode == 0 and text_of(again).strip() != h[1]:
                res = {"status": "skipped", "reason": "eol"}
        item.update(res)
        item["w_hash"] = w_hash
        item["created"] = h is None and w_raw is not None
        item["deleted"] = h is not None and w_raw is None
        items.append(item)

    # Unity: a created or deleted asset brings its .meta, and a new folder its folder .meta.
    known = {it["path"] for it in items}
    companions = {}
    for it in items:
        if it.get("status") not in ("commit", "partial"):
            continue
        if it.get("created"):
            companions.setdefault(it["path"] + ".meta", it["path"])
            parent = os.path.dirname(it["path"])
            while parent:
                companions.setdefault(parent + ".meta", it["path"])
                parent = os.path.dirname(parent)
        elif it.get("deleted"):
            companions.setdefault(it["path"] + ".meta", it["path"])
    companions = {m: o for m, o in companions.items() if m not in known}
    if companions:
        meta_head = R.ls_tree(companions.keys())
        dirs_in_head = R.ls_dirs({m[:-len(".meta")] for m in companions})
        meta_ignored = R.ignored([m for m in companions if m not in meta_head])
        missing = set()
        for meta, owner in sorted(companions.items()):
            absm = os.path.join(R.root, meta)
            folder = meta[:-5]
            if folder != owner and folder in dirs_in_head:
                continue  # the folder is not new, so its .meta is not ours
            exists = os.path.isfile(absm)
            in_head = meta in meta_head
            owner_deleted = any(i["path"] == owner and i.get("deleted") for i in items)
            if exists and not in_head and meta not in meta_ignored and not owner_deleted:
                w_raw, w_hash = read_worktree(absm)
                items.append({"path": meta, "events": 0, "declared": False, "status": "commit",
                              "how": "whole", "reason": "companion", "companion_of": owner,
                              "w_hash": w_hash, "created": True, "deleted": False})
            elif not exists and not in_head and not owner_deleted and \
                    meta not in meta_ignored and unity_asset(R, folder):
                missing.add(meta)  # Unity has not imported it yet
            elif not exists and in_head and owner_deleted:
                items.append({"path": meta, "events": 0, "declared": False, "status": "commit",
                              "how": "delete", "reason": "companion", "companion_of": owner,
                              "w_hash": "absent", "created": False, "deleted": True})
        for it in items:  # without its .meta (or its new folder's) the asset is not committed
            path = it["path"]
            if it.get("created") and it.get("status") in ("commit", "partial") and any(
                    p + ".meta" in missing for p in [path] + [path.rsplit("/", i)[0] for i in
                                                              range(1, path.count("/") + 1)]):
                it.pop("how", None)
                it.update(status="skipped", reason="no-meta")
    head_map.update(R.ls_tree([i["path"] for i in items if i["path"] not in head_map]))

    # The index entries this skill's own earlier commit should have set but could not (the
    # index was locked, the run was killed): recorded before the sync was tried, so only those
    # count — a stage someone made of the same old content on purpose is theirs. Where the
    # index still holds the recorded stale entry and HEAD the commit's, apply puts them right;
    # a record that no longer matches is dropped.
    unsynced, drop = [], []
    for absp, u in sorted(rec["unsynced"].items()):
        if not absp.startswith(prefix):
            continue
        path = git_rel(R.root, absp)
        sha = u.get("commit")
        stale = tuple(u["stale"]) if u.get("stale") else "absent"
        mine = tuple(u["entry"]) if u.get("entry") else None
        if not in_history(sha) or R.ls_tree([path]).get(path) != mine or \
                index_entries(R, [path]).get(path, "absent") != stale:
            drop.append(absp)
            continue
        # the disk against the commit's entry: the stale index makes `git diff HEAD` see a
        # new file the commit holds as deleted
        raw, _ = read_worktree(os.path.join(R.root, path))
        dirty = raw is not None if not mine else raw is None or mine[1] != text_of(git(
            R.root, "hash-object", "--stdin", "--path=" + path, stdin=raw)).strip()
        unsynced.append({"path": path, "commit": sha, "entry": u.get("entry"),
                         "stale": u.get("stale"), "dirty": dirty, "record": absp})
    forget(cfg, sids, unsynced=drop)
    unsynced_paths = {u["path"] for u in unsynced}

    # Content and mode to commit. Edit and Write keep a file's mode, so a tracked file keeps
    # HEAD's: a mode change on disk is someone's chmod, unless the file is declared.
    blob_dir = os.path.join(run_dir, "blobs")
    for n, it in enumerate(items):
        if it.get("status") not in ("commit", "partial"):
            it.pop("content", None)
            continue
        h = head_map.get(it["path"])
        absp = os.path.join(R.root, it["path"])
        if it["how"] == "delete":
            it["mode"] = None
        elif os.path.islink(absp):
            it["mode"] = "120000"
        elif h and (it["how"] == "computed" or not filemode or not it.get("declared")):
            it["mode"] = h[0]
        else:
            executable = os.name != "nt" and os.access(absp, os.X_OK)
            it["mode"] = "100755" if executable else "100644"
        if it["how"] == "computed":
            private_dir(blob_dir)
            name = os.path.join(blob_dir, str(n))
            with open(name, "wb") as fh:
                fh.write(it.pop("content"))
            it["blob"] = name

    # The user's index: nothing staged on a path we would commit — unless what is staged there
    # is what the commit will hold (the session's own `git add`, `git mv`, `git rm`).
    blocked = []
    ours = [it for it in items if it.get("status") in ("commit", "partial")]
    check = [it for it in ours if it["path"] in staged and it["path"] not in unsynced_paths]
    idx_map = index_entries(R, [it["path"] for it in check]) if check else {}
    for it in check:
        path = it["path"]
        idx = idx_map.get(path)
        if path in idx_map and idx is None:
            ok = False  # conflicted
        elif it["how"] == "delete":
            ok = path not in idx_map
        elif idx is None:
            ok = False
        else:
            if it.get("blob"):
                with open(it["blob"], "rb") as fh:
                    data = fh.read()
            else:
                data, _ = read_worktree(os.path.join(R.root, path))
            args_ = ["hash-object", "--stdin"] + ([] if it["mode"] == "120000" else
                                                   ["--path=" + path])
            sha = text_of(git(R.root, *args_, stdin=data or b"")).strip()
            ok = tuple(idx) == (it["mode"], sha)
        if ok:
            it["index"] = list(idx) if idx else None
        else:
            it["blocked"] = True
            blocked.append(path)

    # Where git ignores letter case (macOS, Windows), a new path that differs from one the commit
    # keeps only in case would put both in the tree — two files in one place in every checkout
    # there (a case-only rename with only the new name declared).
    case_clash = []
    added = [it["path"] for it in ours if it["how"] != "delete" and it["path"] not in head_map]
    if added and ignorecase:
        gone_now = {it["path"] for it in ours if it["how"] == "delete"}
        tree, new = {}, set(added)
        listing = git(R.root, "ls-tree", "-r", "-z", "--name-only", R.head).stdout \
            if R.head else b""
        for cand in [os.fsdecode(x) for x in listing.split(b"\0") if x] + added:
            if cand in gone_now:
                continue
            parts = cand.split("/")
            for i in range(1, len(parts) + 1):
                sub = "/".join(parts[:i])
                was = tree.setdefault(_fold(sub), sub)
                if unicodedata.normalize("NFC", was) != unicodedata.normalize("NFC", sub) \
                        and cand in new and (was, sub) not in case_clash:
                    case_clash.append((was, sub))

    # An exec bit set on disk on a file committed whole stays out of the commit (the file keeps
    # HEAD's mode): it is left in the working tree and counted, for the model to declare if
    # the chmod was the session's.
    mode_left = []
    for it in ours:
        h = head_map.get(it["path"])
        absp = os.path.join(R.root, it["path"])
        if not filemode or it["status"] != "commit" or it["how"] != "whole" or not h or \
                it["mode"] not in ("100644", "100755") or os.path.islink(absp) or os.name == "nt":
            continue
        if ("100755" if os.access(absp, os.X_OK) else "100644") != it["mode"]:
            mode_left.append(it["path"])

    # Foreign work: what stays untouched. The session's own skipped files stay too, but they
    # are not someone else's: their Skip lines name them.
    ours_whole = {it["path"] for it in items if it.get("status") == "commit"}
    ours_any = ours_whole | {it["path"] for it in items if it.get("status") == "partial"}
    left_whole = ours_whole - set(mode_left)  # nothing of these stays after the commit
    unsynced_dirty = {u["path"] for u in unsynced if u["dirty"]}
    own_skipped = {it["path"] for it in items
                   if it.get("status") == "skipped" and it.get("reason") != "ignored"}
    own_skipped |= set(case_disk.values())

    def stays_modified(xy, path):
        if path in unsynced_paths and path not in ours_any:
            return path in unsynced_dirty
        return path not in left_whole and xy[1] != " "

    def stays_staged(xy, path):
        return xy[0] not in " ?" and path not in ours_any and path not in unsynced_paths
    foreign = {"modified": 0, "untracked": 0, "staged": 0}
    left_decl = set(untracked_decl)  # a Skip line of their own
    for xy, path in status_rows:
        if path in own_skipped or path in left_decl:
            continue
        if xy == "??":
            foreign["untracked"] += path not in ours_any and path not in unsynced_paths
        elif xy != "!!":
            foreign["modified"] += stays_modified(xy, path)
            foreign["staged"] += stays_staged(xy, path)

    # Files left out of the commit that the session's own shell commands name: the model cannot
    # see the shell commands of subagents and workflow agents, so this is its only pointer.
    # Every changed or untracked file not committed whole is a candidate (a partial file's
    # other part may be the session's Bash edit); a failed command counts too (it may have
    # written before it failed), a refused one does not (it never ran). A bare file name counts
    # only when no other changed file — the ones committed now and the session's own included —
    # has it.
    def fold(text):
        text = unicodedata.normalize("NFC", text)
        return text.casefold() if ignorecase else text
    changed = [path for xy, path in status_rows if xy != "!!"]
    left = [path for path in changed if path not in left_whole
            and (path not in unsynced_paths or path in unsynced_dirty)]
    bash_named, hits = shell_named([c for c, _ in cmds], left, fold,
                                   universe=changed + list(by_path))
    # Every command naming each of those files, uncut and oldest first: one of them may be the
    # write (a subagent's python heredoc) while the others are reads (a grep, a git diff).
    all_commands = None
    if hits:
        every = sorted({n for idx in hits.values() for n in idx})
        pos = {n: i for i, n in enumerate(every)}
        all_commands = os.path.join(run_dir, "commands.json")
        with open(all_commands, "w", encoding="utf-8") as fh:
            json.dump({"commands": [("[failed] " if cmds[n][1] else "") + cmds[n][0]
                                    for n in every],
                       "named_by": {p: [pos[n] for n in idx] for p, idx in sorted(hits.items())}},
                      fh, ensure_ascii=False, indent=1)
    # Files a declared folder holds that went into the commit after all (a .meta companion)
    # are not "not taken".
    untracked_decl = [p for p in untracked_decl if p not in ours_any]

    plan = {
        "version": 1, "repo": R.root, "config": cfg, "sessions": sids, "lang": LANG,
        "head": R.head, "branch": R.branch, "worktree": R.worktree,
        "files": items, "other_repos": {k: sorted(v) for k, v in sorted(other.items())},
        "temp_clones": len(temp_clones),
        "outside": len(outside), "bash_named": bash_named, "bash_commands": all_commands,
        "mode_left": mode_left,
        "unsynced": unsynced,
        "declare_not_found": missing_decl, "declare_refused": refused,
        "declare_untracked": untracked_decl, "foreign": foreign,
        "own_commits": sorted(own), "no_merge": bool(args.no_merge), "case_clash": case_clash,
        "no_push": bool(args.no_push), "run_dir": run_dir, "blocked": blocked,
    }
    R.close()
    return R, plan


def lag(R, d, have_up):
    """What keeps main from moving to the branch by fast-forward: main or origin/main has
    commits the branch lacks. /commit never merges or rebases to get past it: -> the stop text,
    naming the merge to run, or None."""
    a = set(rev_list(R.root, "refs/heads/" + d, "HEAD"))
    o = set(rev_list(R.root, "refs/remotes/origin/" + d, "HEAD")) if have_up else set()
    if not a and not o:
        return None
    if a <= o:
        return T("origin/{d} ушёл вперёд на {n} — влей его в ветку (git merge origin/{d}) и снова "
                 "/commit", "origin/{d} is {n} ahead — merge it into the branch (git merge "
                 "origin/{d}) and run /commit again", d=d, n=n_commits(len(o)))
    if o <= a:
        return T("{d} ушёл вперёд на {n} — влей {d} в ветку (git merge {d}) и снова /commit",
                 "{d} is {n} ahead — merge {d} into the branch (git merge {d}) and run /commit "
                 "again", d=d, n=n_commits(len(a)))
    return T("{d} и origin/{d} разошлись и оба ушли вперёд ({n}) — влей оба в ветку (git merge "
             "origin/{d}, затем git merge {d}) и снова /commit", "{d} and origin/{d} diverged and "
             "are both ahead ({n}) — merge both into the branch (git merge origin/{d}, then git "
             "merge {d}) and run /commit again", d=d, n=n_commits(len(a | o)))


def origin_ahead(b, n):
    """On main: origin/main has commits main lacks — no rebase, no merge by /commit."""
    return T("origin/{b} ушёл вперёд на {n} — влей его в {b} (git merge origin/{b}) и снова "
             "/commit", "origin/{b} is {n} ahead — merge it into {b} (git merge origin/{b}) and run "
             "/commit again", b=b, n=n_commits(n))


def no_main(R, d, have_up):
    """No local main to fast-forward: /commit does not create it. When the other of main /
    master is a local branch, origin/HEAD may be stale (a rename on the server): creating `d`
    would push the old name back, so the way out names both."""
    other = next((n for n in ("main", "master") if n != d and rev(R.root, "refs/heads/" + n)),
                 None)
    way = T("создай её (git branch {d} origin/{d}) и снова /commit", "create it (git branch {d} "
            "origin/{d}) and run /commit again", d=d) if have_up else \
        T("влей вручную", "merge by hand")
    if other:
        way += T("; если основную ветку на origin переименовали (а {o} есть) — git remote "
                 "set-head origin -a", "; if origin's default branch was renamed (there is "
                 "{o}) — git remote set-head origin -a", o=other)
    return T("локальной ветки {d} нет — {w}; или /commit no-merge",
             "there is no local {d} branch — {w}; or /commit no-merge", d=d, w=way)


def branch_behind(b, n):
    return T("в origin/{b} {n}, которых нет в ветке — сначала влей origin/{b} в ветку (git merge "
             "origin/{b})", "origin/{b} has {n} the branch lacks — merge origin/{b} into the "
             "branch first (git merge origin/{b})", b=b, n=n_commits(n))


# Own commits are only the ones this skill made and recorded for the sessions of the run.
# Everything else unpushed on the way is not published — also another session's /commit, and a
# commit the session itself made with `git commit` in Bash: the stop names the commits.

def unpushed_foreign(R, where, shas):
    return T("в {w} незапушенные коммиты не из /commit этой сессии: {l} — их не публикую; "
             "проверь и запушь их сам, потом снова /commit, или убери их",
             "{w} has unpushed commits not made by this session's /commit: {l} — not "
             "publishing them; review and push them yourself, then run /commit again, or drop "
             "them", w=where, l=subjects(R, shas))


def main_foreign_text(R, d, shas, no_merge=True):
    """no_merge: `/commit no-merge` would push the branch (nothing foreign on it either)."""
    return T("в локальном {d} незапушенные коммиты не из /commit этой сессии: {l} — не трогаю "
             "{d}; проверь и запушь их сам, потом снова /commit{x}",
             "local {d} has unpushed commits not made by this session's /commit: {l} — "
             "leaving {d} alone; review and push them yourself, then run /commit again{x}", d=d,
             l=subjects(R, shas),
             x=T(", или /commit no-merge — запушить только ветку",
                 ", or /commit no-merge to push the branch alone") if no_merge else "")


def branch_foreign(R, b, d, shas, own):
    """The stop for commits on the branch that /commit did not make (someone's, or a `git commit`
    the session ran itself), with the way out: `/commit no-merge` where it would pass, else
    review them and merge or drop them by hand."""
    way = T("если ветка твоя — запушить только её: /commit no-merge", "if the branch is yours, "
            "to push the branch alone: /commit no-merge") if no_merge_passes(R, b, d, own) else T(
        "проверь их, влей в {d} и запушь сам, или убери их с ветки",
        "review them, merge them into {d} and push it yourself, or drop them from the branch",
        d=d)
    return T("на ветке {b} коммиты не из /commit этой сессии: {l} — их в {d} не вливаю; {w}",
             "branch {b} has commits not made by this session's /commit: {l} — not merging "
             "them into {d}; {w}", b=b, d=d, l=subjects(R, shas), w=way)


# A file the replay cannot split is skipped; the way out is the same for each: --declare it when
# the whole file is the session's (it is then committed whole, as on disk).
DECLARE = (" — передай --declare, если весь файл твой", " — --declare it if the whole file is yours")
REASONS = {
    "ignored": ("в .gitignore", "ignored by git"),
    "directory": ("это папка — передай файлы", "a directory — declare its files"),
    "notfound": ("правку сессии переписали после неё — не нахожу её в файле",
                 "a later edit rewrote this session's change — it is not in the file"),
    "rewritten": ("правку сессии не найти — файл менялся после неё (например, через Bash)",
                  "this session's edit cannot be found — the file changed after it (e.g. "
                  "through Bash)"),
    "ambiguous": ("не могу однозначно найти правку сессии", "cannot locate this session's "
                  "change for sure"),
    "tangled": ("правки сессии сцеплены с другими — чужими или закоммиченными не через /commit",
                "this session's edits are entangled with other changes — someone else's, or "
                "ones committed outside /commit"),
    "rewrite-unknown": ("агент перезаписал файл целиком, не знаю, что в нём было до этого",
                        "an agent overwrote the whole file and what it held before is unknown"),
    "deleted": ("удалён после правок сессии — передай --declare, если удаляла сессия",
                "deleted after this session's edits — pass --declare if the session deleted it"),
    "hidden": ("помечен в git как assume-unchanged/skip-worktree — не коммичу; сними пометку (git "
               "update-index --no-assume-unchanged / --no-skip-worktree) и снова /commit, если "
               "правка нужна", "marked assume-unchanged/skip-worktree in git — not committed; "
               "clear the flag (git update-index --no-assume-unchanged / --no-skip-worktree) and "
               "run /commit again if the change belongs in it"),
    "eol": ("/commit перепишет концы строк во всём файле (в git он с CRLF, а .gitattributes/"
            "autocrlf их нормализует) — закоммить его сам: git add их сохранит",
            "committing it here would rewrite every line ending (CRLF in git, normalised by "
            ".gitattributes/autocrlf) — commit it by hand; git add keeps them"),
    "case-disk": ("git хранит его как {x} — на диске другой регистр букв (переименование, "
                  "которого git не видит); приведи регистр на диске к git (или git mv) и снова "
                  "/commit", "git tracks it as {x} — the letter case on disk differs (a rename "
                  "git does not see); match the disk to git (or git mv) and run /commit again"),
    "no-meta": ("новый ассет без .meta — Unity его ещё не импортировал: переключись в редактор "
                "(или обнови ассеты) и снова /commit", "a new asset with no .meta yet — Unity "
                "has not imported it: focus the editor (or refresh assets) and run /commit again"),
    "untracked-before": ("файл был вне git до правок сессии — передай --declare, если его "
                         "создала сессия", "the file was untracked before this session edited it "
                         "— pass --declare if the session created it"),
}
DETAILS = {
    "deleted-text": ("правка удаляла текст — её место не определить",
                     "the edit deleted text, so its place is unknown"),
    "repeated": ("такой текст встречается в файле несколько раз",
                 "the text occurs more than once in the file"),
    "whole-write": ("файл переписан целиком", "the file was rewritten whole"),
    "rewrite": ("агент переписал файл целиком, прежнее содержимое неизвестно",
                "an agent rewrote the whole file; what it held before is unknown"),
}
RANGE_RE = re.compile(r"^\d+–\d+(?:, \d+–\d+)*$")


def reason_text(it):
    ru, en = REASONS.get(it.get("reason"), ("?", "?"))
    d = it.get("detail")
    text = T(ru, en, x=d)
    if isinstance(d, str) and RANGE_RE.match(d):
        text += T(" (строки {d})", " (lines {d})", d=d)
    elif d in DETAILS:
        text += " — " + T(*DETAILS[d])
    if it.get("reason") in ("notfound", "rewritten", "ambiguous", "tangled", "rewrite-unknown"):
        text += T(*DECLARE)
    return text


def label(text):
    return (text + ":").ljust(9) + " "


def emit(lines, out=None):
    for lab, text in lines:
        s = label(lab) + text
        print(s, flush=True)
        if out is not None:
            out.append(s)


def plan_cmd(args):
    if args.pr and args.main:
        raise Fail("--pr and --main contradict each other: pass one")
    task = None
    if args.task:
        task = task_of(args.task)
        if not task:
            raise Fail(T("--task {r}: не вижу id задачи — передай --task <id>",
                         "--task {r}: no task id in it — pass --task <id>", r=args.task))
        task = {"id": task[0], "url": task[1]}
    refused = refuse_in_ticket(config_dir(), args.session or [], T("Коммит", "Commit"))
    if refused is not None:
        return refused
    R, plan = collect(args)
    items = plan["files"]
    commit = [i for i in items if i.get("status") in ("commit", "partial")]
    skipped = [i for i in items if i.get("status") == "skipped"]
    own_skips = [i for i in skipped if i.get("reason") != "ignored"]
    own = set(plan["own_commits"])
    lines = []
    stop = None
    plan["default"] = default_branch(R) or R.branch
    plan["mode"], plan["mode_why"] = repo_mode(R, plan["default"], args.pr, args.main)
    plan["task"] = task
    # Where the commit goes: on the default branch, work for a task (or any work in a PR
    # repository) gets a branch of its own, made from HEAD by `git switch -c` at apply — the
    # working tree and the index stay as they are. On any other branch it is committed there.
    plan["new_branch"], branch_stop = None, None
    names_branch = bool(task and R.branch and (R.branch == "task/" + task["id"] or
                                               R.branch.startswith(f"task/{task['id']}-")))
    want_branch = bool(R.head and commit and R.branch == plan["default"]
                       and (task or plan["mode"] == "pr"))
    if want_branch:
        name = args.branch or (f"task/{task['id']}" if task else "")
        plan["new_branch"] = name  # "" — session/<slug of the commit subject>, named at apply
        problem = branch_problem(R, name, task and task["id"]) if name else None
        if problem:
            lines.append((T("Ветка", "Branch"), T("нет — стоп: ", "no — stop: ") + problem))
            branch_stop = T("ветка не создана, см. Ветка", "no branch made, see Branch")
        else:
            lines.append((T("Ветка", "Branch"), T(
                "{b} — создам от {d} (git switch -c; рабочее дерево не трогаю)",
                "{b} — will create it from {d} (git switch -c; the working tree stays)",
                b=name or T("session/<тема коммита>", "session/<commit subject>"), d=R.branch)))
    elif task and commit and R.branch and R.branch.startswith("task/") and not names_branch:
        lines.append((T("Ветка", "Branch"), T(
            "нет — стоп: {b} — ветка другой задачи, не {t}; {w}", "no — stop: {b} is another "
            "task's branch, not {t}'s; {w}", b=R.branch, t=task["id"], w=back_to(
                R, plan["default"]))))
        branch_stop = T("ветка другой задачи, см. Ветка", "another task's branch, see Branch")
    elif ((plan["mode"] == "pr" or (R.branch or "").startswith(("task/", "session/")))
          and commit and R.head and R.branch not in (None, plan["default"])
          and (theirs := foreign_commits(R, "HEAD", [r for r in (
              "refs/remotes/origin/" + plan["default"], "refs/heads/" + plan["default"])
              if rev(R.root, r)], own, merged=("refs/remotes/origin/" + plan["default"],
                                                "refs/heads/" + plan["default"])))):
        # A shared checkout left on another session's (or a /ticket run's) branch: its work and
        # PR are not this session's — whatever --task says (a task taken from the branch name
        # proves nothing). Going back is no way out when --task names this very branch.
        lines.append((T("Ветка", "Branch"), T(
            "нет — стоп: на ветке {b} коммиты не из /commit этой сессии: {s} — свой коммит на "
            "неё не кладу; {w}если их сделал /commit прошлой, уже законченной сессии (не "
            "открытой вкладки: --session берёт и все её незакоммиченные правки) — передай её "
            "--session вместе с --session этой; если это коммит шлюза /ticket — /resume ту "
            "сессию и публикуй в её шлюзе; если ветка твоя — закоммить и запушь сам",
            "no — stop: the branch {b} holds commits not made by this session's /commit: {s} — "
            "not committing onto it; {w}if an earlier session's /commit made them (a session "
            "that has ended, not another open tab: --session also takes all of its uncommitted "
            "edits), pass its --session as well as this one's; if it is a /ticket gate commit, "
            "/resume that session and publish at its gate; if the branch is yours, commit and "
            "push it yourself", b=R.branch, s=subjects(R, theirs, 3), w="" if names_branch else
            back_to(R, plan["default"], T(" — будет своя ветка", " for a branch of its own"))
            + "; ")))
        branch_stop = T("на ветке чужие коммиты, см. Ветка",
                        "the branch holds commits not made here, see Branch")
    elif task and commit and R.branch:
        lines.append((T("Ветка", "Branch"), T("{b} — коммит на неё", "{b} — committing there",
                                              b=R.branch)))
    # An unfinished rebase detaches HEAD: name the rebase, not the detached HEAD.
    if R.in_progress():
        stop = op_stop(R.in_progress(), R.git_dir)
    elif R.branch is None:
        stop = T("не на ветке (detached HEAD) — переключись на ветку и запусти /commit снова",
                 "not on a branch (detached HEAD) — switch to a branch and run /commit again")
    elif R.unmerged():
        stop = T("в индексе неразрешённые конфликты", "the index has unresolved conflicts")
    elif plan["blocked"]:
        stop = T("чужие staged-правки на моих путях: {p} — убери их из индекса (git restore "
                 "--staged) или закоммить сам", "someone staged changes on paths this session "
                 "changed: {p} — unstage them (git restore --staged) or commit them yourself",
                 p=", ".join(plan["blocked"][:5]))
    elif plan["case_clash"]:
        a, b = plan["case_clash"][0]
        stop = T("в коммите оказались бы и {a}, и {b} — они отличаются только регистром букв; "
                 "если это переименование, передай --declare и старое имя (уйдёт как удаление); "
                 "если на диске у папки другой регистр, чем в git, — приведи его к git",
                 "the commit would hold both {a} and {b}, which differ only in letter case; for "
                 "a rename, --declare the old name too (it is committed as a deletion); if a "
                 "folder's case on disk differs from git's, match it to git", a=a, b=b)
    elif branch_stop:
        stop = branch_stop
    if stop and not branch_stop:  # no branch is made, nothing is committed there
        lines = [(lab, T("нет — стоп, см. Коммит", "no — stopped, see Commit")
                  if lab == T("Ветка", "Branch") else text) for lab, text in lines]
    if task and commit and not stop:
        lines.append((T("Задача", "Task"), T("{t} — ссылка в сообщении коммита (проверю при "
                                             "apply)", "{t} — named in the commit message "
                                             "(checked at apply)", t=task["id"])))
    if commit:
        parts = []
        def names(paths):
            return ", ".join(paths[:3]) + (" …" if len(paths) > 3 else "")
        part = [i["path"] for i in commit if i["status"] == "partial"]
        if part:
            parts.append(T("{n} частично: {p} — другие правки (чужие или твои через Bash) "
                           "останутся в рабочем дереве",
                           "{n} partly: {p} — other edits stay in the working tree",
                           n=len(part), p=names(part)))
        # Unverified: nothing tells whose all of it is — declared, a notebook or binary file, or
        # an agent's Write over a file the session created (someone may have changed it between).
        unverified = [i["path"] for i in commit if i["status"] == "commit" and
                      i.get("reason") in ("declared", "notebook", "binary", "unverified")]
        if unverified:
            parts.append(T("{n} целиком без проверки: {p}", "{n} whole, unverified: {p}",
                           n=len(unverified), p=names(unverified)))
        unverified = [i["path"] for i in commit if i["status"] == "partial" and
                      i.get("reason") == "unverified"]
        if unverified:
            parts.append(T("{n} частично, без проверки: {p}", "{n} partly, unverified: {p}",
                           n=len(unverified), p=names(unverified)))
        comp = [i["path"] for i in commit if i.get("reason") == "companion"]
        if comp:
            n = len(comp)
            parts.append(T("{n} " + plural(n, ".meta-спутник", ".meta-спутника", ".meta-спутников"),
                           "{n} .meta companion" + ("" if n == 1 else "s"), n=n))
        dels = sum(1 for i in commit if i.get("how") == "delete")
        if dels:
            parts.append(T("удаление: {n}", "deleted: {n}", n=dels))
        text = T("да — ", "yes — ") + n_files(len(commit)) + (f" ({'; '.join(parts)})"
                                                               if parts else "")
        lines.append((T("Коммит", "Commit"), (T("нет — стоп: ", "no — stop: ") + stop)
                      if stop else text))
    elif stop:
        lines.append((T("Коммит", "Commit"), T("нет — стоп: ", "no — stop: ") + stop))
    elif own_skips:
        lines.append((T("Коммит", "Commit"), T(
            "нет — правки сессии пропущены ({n}), см. Пропуск",
            "no — this session's changes are skipped ({n}), see Skip", n=len(own_skips))))
    elif any(i.get("status") == "unchanged" for i in items) or skipped:
        lines.append((T("Коммит", "Commit"), T("нет — новых правок сессии в этом репозитории "
                                               "нет", "no — no new changes of this session here")))
    else:
        lines.append((T("Коммит", "Commit"), T("нет — в этой сессии нет правок в этом "
                                               "репозитории", "no — this session changed "
                                               "nothing in this repository")))
    ignored = [it["path"] for it in skipped if it.get("reason") == "ignored"]
    for it in skipped:
        if it.get("reason") != "ignored" or len(ignored) == 1:
            lines.append((T("Пропуск", "Skip"), f"{it['path']} — {reason_text(it)}"))
    if len(ignored) > 1:
        # Scratch files the session wrote into ignored folders: one line, not one per file.
        tops = {}
        for p in ignored:
            top = p.split("/", 1)[0] + ("/…" if "/" in p else "")
            tops[top] = tops.get(top, 0) + 1
        where = sorted(tops, key=lambda t: (-tops[t], t))
        where = ", ".join(where[:3]) + (", …" if len(where) > 3 else "")
        n = len(ignored)
        lines.append((T("Пропуск", "Skip"), T(
            "{n} " + plural(n, "файл", "файла", "файлов") + " в .gitignore ({w})",
            "{n} files ignored by git ({w})", n=n, w=where)))
    if plan["declare_untracked"]:
        d = plan["declare_untracked"]
        lines.append((T("Пропуск", "Skip"), T(
            "не взяты новые файлы в объявленных папках ({n}: {p}) — их создала не правка сессии; "
            "передай --declare каждого своего по пути", "new files in declared folders not taken "
            "({n}: {p}) — not created by this session's edits; --declare each of yours by its "
            "path", n=len(d), p=", ".join(d[:3]) + (" …" if len(d) > 3 else ""))))
    if plan["unsynced"]:
        u = plan["unsynced"]
        lines.append((T("Индекс", "Index"), T(
            "верну записи после моего коммита {s} (индекс тогда не обновился): {p}",
            "will put back the entries my commit {s} left stale (the index was locked then): {p}",
            s=short(u[0]["commit"]), p=", ".join(x["path"] for x in u[:5]) +
            (" …" if len(u) > 5 else ""))))
    f = plan["foreign"]
    lines.append((T("Чужое", "Foreign"), T(
        "не трогаю {m} {mw}, {u} {uw}, staged: {s}",
        "leaving alone {m} modified file{ms}, {u} untracked, staged: {s}",
        m=f["modified"], mw=plural(f["modified"], "изменённый файл", "изменённых файла",
                                   "изменённых файлов"),
        ms="" if f["modified"] == 1 else "s", u=f["untracked"],
        uw=plural(f["untracked"], "неотслеживаемый", "неотслеживаемых", "неотслеживаемых"),
        s=f["staged"])))
    verdict = "stop" if stop else None
    if not stop:
        # The same decisions apply makes, on local refs: a forecast of merge and push.
        pub = Publisher(R, plan, own, Collect(lines), plan["no_merge"], plan["no_push"],
                        dry=True, new_commit=bool(commit), branch=None if plan["new_branch"] is None
                        else plan["new_branch"] or T("session/<тема>", "session/<subject>"))
        if not pub.run():
            push_line_after_stop(pub, lines)
        if pub.stopped and not commit:
            verdict, stop = "stop", pub.stopped
        elif not commit and not pub.work and not plan["unsynced"]:
            # Nothing to do, but the session's own skipped files wait for a decision.
            verdict = "skipped" if own_skips else "nothing"
        else:
            verdict = "go"
    for root, paths in plan["other_repos"].items():
        lines.append((T("Ещё", "Elsewhere"), T("правки этой сессии есть и в {r} ({n}) — там свой "
                                              "/commit", "this session also edited {r} ({n}) — "
                                              "run /commit there", r=root, n=n_files(len(paths)))))
    if plan["temp_clones"]:
        n = plan["temp_clones"]
        lines.append((T("Ещё", "Elsewhere"), T(
            "временные клоны: {n} — не трогаю", "temporary clones: {n} — left alone", n=n)))
    plan["verdict"] = verdict
    plan["stop"] = stop
    plan["status_lines"] = []
    emit(lines, plan["status_lines"])
    with open(os.path.join(plan["run_dir"], "plan.json"), "w", encoding="utf-8") as fh:
        json.dump(plan, fh, ensure_ascii=False, indent=1)

    # Details for the model, after the status block.
    print("---")
    print(f"verdict: {verdict}")
    print(f"repo: {R.root} · branch: {R.branch or 'detached'}"
          + (" (linked worktree)" if R.worktree else "") + f" · default: {plan['default']}")
    for it in items:
        st = it.get("status")
        if st not in ("commit", "partial", "skipped"):
            continue
        tag = {"commit": "+", "partial": "~", "skipped": "!"}[st]
        how = it.get("how", "")
        extra = it.get("reason") or ""
        if it.get("companion_of"):
            extra = "companion of " + it["companion_of"]
        print(f"  {tag} {it['path']}  {how} {extra}".rstrip())
    unchanged = sum(1 for i in items if i.get("status") == "unchanged")
    if unchanged:
        print(f"unchanged (already in HEAD or reverted): {unchanged}")
    if plan["outside"]:
        print(f"edited outside any repository (ignored): {plan['outside']}")
    if plan["declare_not_found"]:
        print("declared but not found in this repository (relative paths are taken from its "
              "root) or unchanged: " + ", ".join(plan["declare_not_found"]))
    if plan["declare_refused"]:
        print("declared the whole repository (refused — that would be git add -A; declare the "
              "files one by one): " + ", ".join(plan["declare_refused"]))
    def some(key, limit=20):
        names = plan[key]
        if len(names) <= limit:
            return ", ".join(names)
        return ", ".join(names[:limit]) + f" … ({limit} of {len(names)} shown; all in plan.json " \
            f"{key})"
    if plan["declare_untracked"]:
        print("untracked files under a declared folder (not taken — declare each one that is "
              "this session's by its path): " + some("declare_untracked"))
    if plan["mode_left"]:
        print("exec bit changed on disk but not committed (the file keeps HEAD's mode; declare "
              "it if the chmod was this session's): " + some("mode_left"))
    if plan["bash_named"]:
        print(f"named by this session's shell commands ({len(plan['bash_named'])} changed or "
              "untracked here, not committed whole; a name match, not proof — declare the ones "
              "this session changed; every naming command, uncut, oldest first: " +
              plan["bash_commands"] + "): " + some("bash_named"))
    print(f"plan: {os.path.join(plan['run_dir'], 'plan.json')}")
    return 1 if verdict == "stop" else 0


# --- apply -------------------------------------------------------------------------------

class Collect:
    """Status lines kept for printing later (plan prints its block in one piece)."""

    def __init__(self, lines):
        self.lines = lines

    def say(self, lab, text, step=None, status=None):
        self.lines.append((lab, text))


def push_line_after_stop(pub, lines):
    """A run stopped before the push step: the block still says what became of it."""
    labels = [x[0] if isinstance(x, tuple) else x for x in lines]

    def said(lab):
        return any(x == lab or str(x).startswith(label(lab)) for x in labels)
    if not said("Push"):
        pub.steps.say("Push", pub.no_push_reason() or T("нет — после стопа", "no — stopped before"),
                      "push", "skipped")
    if pub.mode == "pr" and not said("PR"):
        pub.steps.say("PR", T("нет — после стопа", "no — stopped before"), "pr", "skipped")


class Steps:
    def __init__(self):
        self.lines = []
        self.steps = []

    def say(self, lab, text, step=None, status=None):
        s = label(lab) + text
        print(s, flush=True)
        self.lines.append(s)
        if step:
            self.steps.append({"step": step, "status": status, "text": text})


def check_stale(R, plan):
    if R.head != plan["head"]:
        raise Stale(f"HEAD moved: {short(plan['head'])} -> {short(R.head)}")
    if R.branch != plan["branch"]:
        raise Stale(f"branch changed: {plan['branch']} -> {R.branch}")
    if R.in_progress():
        raise Stale(f"{R.in_progress()} started since the plan")
    if R.unmerged():
        raise Stale("unresolved conflicts in the index")
    ours = [i for i in plan["files"] if i.get("status") in ("commit", "partial")]
    staged = R.staged()
    accepted = [it for it in ours if it["path"] in staged and "index" in it]
    now = index_entries(R, [it["path"] for it in accepted]) if accepted else {}
    for it in ours:
        _, w_hash = read_worktree(os.path.join(R.root, it["path"]))
        if w_hash != it["w_hash"]:
            raise Stale(f"{it['path']} changed on disk")
        if it["path"] in staged:
            # Staged at plan time as the commit will hold it (the session's own git add)?
            cur = now.get(it["path"], "absent")
            want = tuple(it["index"]) if it.get("index") else "absent"
            if "index" not in it or cur != want:
                raise Stale(f"{it['path']} is staged now")
        if it.get("blob") and not os.path.isfile(it["blob"]):
            raise Stale(f"planned content of {it['path']} is gone")


def update_index(R, info):
    """`git update-index --index-info` on the real index, waiting up to 5 s for a held
    index.lock. -> the failed process, or None."""
    res = None
    for _ in range(25):
        res = git(R.root, "update-index", "-z", "--index-info", stdin=info, ok=None)
        if res.returncode == 0 or "index.lock" not in errtext(res):
            break
        time.sleep(0.2)
    return res if res is not None and res.returncode != 0 else None


def index_line(path, entry, zero):
    return (f"{entry[0]} {entry[1]}\t" if entry else f"0 {zero}\t").encode() + \
        os.fsencode(path) + b"\0"


def resync(R, plan, steps):
    """Put back the real index entries an earlier commit of this skill could not set (plan's
    `unsynced`, from the session records): only where the index still holds exactly the old
    blob and HEAD has not moved; the records go once that is done.
    -> False when the index could not be written (said why)."""
    todo = plan.get("unsynced") or []
    if not todo or rev(R.root, "HEAD") != plan["head"]:
        return True
    now = index_entries(R, [u["path"] for u in todo])
    fix = []
    for u in todo:
        if now.get(u["path"], "absent") == (tuple(u["stale"]) if u["stale"] else "absent"):
            fix.append(u)
    if not fix:
        return True
    zero = "0" * len(plan["head"])
    info = b"".join(index_line(u["path"], u["entry"], zero) for u in fix)
    paths = ", ".join(u["path"] for u in fix[:5]) + (" …" if len(fix) > 5 else "")
    res = update_index(R, info)
    if res is not None:
        steps.say(T("Индекс", "Index"), T(
            "стоп — не обновлён ({e}); выполни: git reset -q {h} -- {p}",
            "stop — not updated ({e}); run: git reset -q {h} -- {p}", e=oneline(errtext(res), 160),
            h=short(plan["head"]), p=quoted([u["path"] for u in fix])), "index", "stop")
        return False
    forget(plan["config"], plan["sessions"], unsynced=[u.get("record") for u in fix])
    steps.say(T("Индекс", "Index"), T("готово — вернул записи после моего коммита {s}: {p}",
                                      "done — put back the entries of my commit {s}: {p}",
                                      s=short(fix[0]["commit"]), p=paths), "index", "done")
    return True


def index_entries(R, paths, index=None):
    """{path: (mode, sha)} of the real index's (or `index`'s) stage-0 entries; a conflicted path
    maps to None."""
    out = {}
    for part in chunks(sorted(paths)):
        data = git(R.root, "ls-files", "-s", "-z", "--", *part, literal=True, index=index).stdout
        for rec in data.split(b"\0"):
            if not rec:
                continue
            meta, path = rec.split(b"\t", 1)
            mode, sha, stage = meta.decode().split()
            path = os.fsdecode(path)
            out[path] = (mode, sha) if stage == "0" and path not in out else None
    return out


def hash_files(R, paths, write=False):
    """{path: blob id} of these working-tree files through the clean filters of their path, in
    one `git hash-object --stdin-paths` — what `git add` stores, except that hash-object does
    not see the index (git add keeps a CRLF blob's endings; collect() skips such files); a path
    it cannot take on a line is left out for the caller to hash on its own."""
    paths = [p for p in paths if "\n" not in p and "\r" not in p
             and os.path.isfile(os.path.join(R.root, p))
             and not os.path.islink(os.path.join(R.root, p))]
    if not paths:
        return {}
    out = text_of(git(R.root, "hash-object", *(["-w"] if write else []), "--stdin-paths",
                      stdin=b"".join(os.fsencode(p) + b"\n" for p in paths))).split()
    return dict(zip(paths, out)) if len(out) == len(paths) else {}


def quoted(paths):
    return " ".join(shlex.quote(p) for p in paths)


def symbolic_head(R):
    return text_of(git(R.root, "symbolic-ref", "-q", "HEAD", ok=(0, 1))).strip()


class Moved(Exception):
    """HEAD moved (or a merge began) while git commit ran: the commit was taken back."""


def landed(R, pend):
    """The commit a pending record names — the planned tree on the planned parent, on the
    planned branch — or None when it never landed (refused, taken back)."""
    tip = rev(R.root, pend.get("branch") or "HEAD")
    if not tip:
        return None
    parent = pend.get("parent")
    rows = text_of(git(R.root, "log", "--format=%H %T %P", "-n", "200", tip,
                       *(["^" + parent] if parent and rev(R.root, parent) else []), "--",
                       ok=None)).split("\n")
    for row in rows:
        f = row.split()
        if len(f) >= 2 and f[1] == pend["tree"] and f[2:] == ([parent] if parent else []):
            return f[0]
    return None


def commit_step(R, plan, message, steps):
    """Commit the planned content through a private index.
    -> None when git refused, else (new HEAD, verified, ours): verified — the commit is the
    planned tree and the index is in step; ours — the commit holds only the planned tree, so
    it may be recorded and published as this session's. Raises Moved.

    Before `git commit` the run notes a pending commit (planned parent and tree, the path
    records, the index entries to set) in the session records; once the commit is checked it
    becomes a recorded own commit, and the index entries stay recorded until they are set. A run
    killed anywhere on the way (Esc, the Bash timeout, during a post-commit hook) is then picked
    up by the next plan, which finds the commit by its tree and parent."""
    run_dir, cfg, sids = plan["run_dir"], plan["config"], plan["sessions"]
    index = os.path.join(run_dir, "index")
    if os.path.exists(index):
        os.remove(index)
    if R.head:
        git(R.root, "read-tree", R.head, index=index)
    else:
        git(R.root, "read-tree", "--empty", index=index)
    ours = [i for i in plan["files"] if i.get("status") in ("commit", "partial")]
    # Files committed whole are hashed from disk in one git process; computed content and
    # symlinks one by one; the private index gets every entry in one update-index.
    whole = hash_files(R, [it["path"] for it in ours if it["how"] == "whole"
                           and it["mode"] != "120000"], write=True)
    info = []
    for it in ours:
        path = it["path"]
        if it["how"] == "delete":
            info.append(index_line(path, None, "0" * len(R.empty_tree)))
            continue
        sha = whole.get(path)
        if sha is None:
            if it["how"] == "computed":
                with open(it["blob"], "rb") as fh:
                    data = fh.read()
            else:
                data, _ = read_worktree(os.path.join(R.root, path))
            sha = text_of(git(R.root, "hash-object", "-w", "--stdin", *(
                [] if it["mode"] == "120000" else ["--path=" + path]), stdin=data)).strip()
        info.append(index_line(path, (it["mode"], sha), ""))
    # The whole files were hashed from disk just now, after check_stale: a save someone made in
    # between would be in those blobs. Every planned file must still be what plan saw.
    for it in ours:
        if read_worktree(os.path.join(R.root, it["path"]))[1] != it["w_hash"]:
            raise Stale(f"{it['path']} changed on disk")
    if info:
        git(R.root, "update-index", "-z", "--index-info", stdin=b"".join(info), index=index)
    expected = text_of(git(R.root, "write-tree", index=index)).strip()
    msg_file = os.path.join(run_dir, "message.txt")
    with open(msg_file, "wb") as fh:
        fh.write(message)
    paths = [i["path"] for i in ours]
    # The real index: our paths only, set to the new commit's entries — where the entry is
    # still HEAD's old one. Where the session itself had staged what it committed (plan's
    # `index`), that stage is done with too. Someone who stages one of them meanwhile keeps
    # their stage.
    old = R.ls_tree(paths, R.head) if R.head else {}
    accepted = {it["path"]: tuple(it["index"]) if it["index"] else None
                for it in ours if "index" in it}

    def to_sync(now):
        return [p for p in paths if now.get(p) == old.get(p)
                or (p in accepted and now.get(p) == accepted[p])]

    def sync_record(sync, now, entries):
        return {os.path.join(R.root, p): {"stale": list(now[p]) if now.get(p) else None,
                                          "entry": list(entries[p]) if entries.get(p) else None}
                for p in sync}
    path_recs = {os.path.join(R.root, it["path"]): dict(it["last_event"])
                 for it in ours if it.get("last_event")}
    want_ref = "refs/heads/" + plan["branch"]
    planned = index_entries(R, paths, index=index)
    now = index_entries(R, paths)
    pend = {"parent": R.head, "tree": expected, "branch": want_ref, "repo": R.common_dir,
            "paths": path_recs, "sync": sync_record(to_sync(now), now, planned)}
    # Last look before committing: a merge, cherry-pick or rebase begun since the plan would
    # turn this commit into its merge commit.
    if R.in_progress() or rev(R.root, "HEAD") != R.head:
        raise Stale(f"{R.in_progress() or 'HEAD moved'} since the plan")
    if symbolic_head(R) != want_ref:
        raise Stale("the branch changed since the plan")
    edit_records(cfg, sids, lambda data: data.setdefault("pending", []).append(pend))
    proc = git(R.root, "commit", "-q", "-F", msg_file, index=index, ok=None)
    if proc.returncode != 0:
        forget(cfg, sids, pending=pend)
        steps.say(T("Коммит", "Commit"), T("нет — git отказал: {e}", "no — git refused: {e}",
                                           e=errtext(proc) or proc.returncode), "commit", "stop")
        return None
    want_parents = [R.head] if R.head else []
    # Where did it land? By refs: the planned branch now one commit on, from the old HEAD, is
    # this commit — whatever HEAD points to meanwhile (a post-commit hook, or someone, may have
    # switched branches).
    tip = rev(R.root, want_ref)
    if tip and tip != R.head and parents_of(R.root, tip) == want_parents:
        head = tip
    else:
        head = rev(R.root, "HEAD")
        if head == R.head:
            forget(cfg, sids, pending=pend)
            steps.say(T("Коммит", "Commit"), T("нет — git не создал коммит: {e}",
                                               "no — git made no commit: {e}", e=errtext(proc)),
                      "commit", "stop")
            return None
        # Someone committed, began a merge or switched branches between our checks and git
        # commit: our tree was built on the old HEAD and would undo their commit / swallow
        # their merge, or the commit is on a branch that is not the plan's. Take our commit
        # back off the branch it landed on — only while it is the tip — and say so. A commit
        # someone made on top of ours is never taken back: the pending record lets the next
        # plan find ours below it.
        tree = text_of(git(R.root, "rev-parse", head + "^{tree}")).strip()
        if tree != expected:
            steps.say(T("Коммит", "Commit"), T(
                "нет — во время коммита HEAD сдвинулся, и на вершине {s} чужой коммит — ничего "
                "не снимаю; найди мой коммит в git log {s} (дерево {t}) и разберись вручную",
                "no — HEAD moved during the commit and someone's commit {s} is on top — nothing "
                "taken back; find mine in git log {s} (tree {t}) and sort it out by hand",
                s=short(head), t=short(expected)), "commit", "stop")
            return None
        forget(cfg, sids, pending=pend)
        parents = parents_of(R.root, head)
        landed_on = symbolic_head(R)
        back = parents[0] if parents else None
        undo = git(R.root, "update-ref", "-m", "commit: take back", landed_on or "HEAD",
                   back or "0" * len(head), head, ok=None) if back else None
        if back and undo.returncode == 0 and len(parents) > 1 and back == R.head:
            with open(os.path.join(R.git_dir, "MERGE_HEAD"), "w") as fh:
                fh.write("".join(p + "\n" for p in parents[1:]))
        what = T("начался чужой merge", "someone's merge began") if len(parents) > 1 else \
            T("HEAD сдвинулся", "HEAD moved") if parents != want_parents else \
            T("сменилась ветка", "the branch changed")
        done = undo is not None and undo.returncode == 0
        steps.say(T("Коммит", "Commit"), T(
            "нет — во время коммита {w}; мой коммит {s} снят{x} — запусти /commit снова",
            "no — {w} during the commit; my commit {s} was taken back{x} — run /commit again",
            w=what, s=short(head), x="" if done else T(
                " не удалось — сними сам: git update-ref HEAD {p} {s}",
                " — failed; take it back yourself: git update-ref HEAD {p} {s}",
                p=back or "?", s=head)), "commit", "stop")
        raise Moved()
    subject = oneline(text_of(git(R.root, "log", "-1", "--format=%s", head, "--")), 72)
    steps.say(T("Коммит", "Commit"), T("готово — {s} «{m}»", "done — {s} “{m}”", s=short(head),
                                       m=subject), "commit", "done")
    problems = []
    # Did a hook put something else into the commit?
    extra = [os.fsdecode(p) for p in git(R.root, "diff", "--name-only", "-z", expected, head,
                                          "--").stdout.split(b"\0") if p]
    trusted = not extra
    moved_away = symbolic_head(R) != want_ref
    entries = R.ls_tree(paths, head)
    now = index_entries(R, paths)
    # The index goes with the commit HEAD is at: set our entries only while that is this one.
    at_head = rev(R.root, "HEAD") == head
    sync = to_sync(now) if at_head else []
    touched = [p for p in paths if p not in sync] if at_head else []
    if trusted:
        settle(cfg, sids, pend, head, sync=sync_record(sync, now, entries))
    else:
        forget(cfg, sids, pending=pend)
        record_own(cfg, sids, [], unsynced={k: dict(v, commit=head) for k, v in
                                            sync_record(sync, now, entries).items()})
    if extra:
        undo_paths = sorted(set(paths) | set(extra))
        problems.append(T(
            "коммит не совпадает с планом в: {x} (хук поменял индекс?) — не считаю его своим и "
            "не опубликую; откатить: git update-ref HEAD {old} {new} && git reset -q {old} -- {p}",
            "the commit differs from the plan in: {x} (did a hook change the index?) — not "
            "counted as this session's, not published; to undo: git update-ref HEAD {old} {new} "
            "&& git reset -q {old} -- {p}", x=", ".join(extra[:5]), old=R.head or "?", new=head,
            p=quoted(undo_paths)))
    zero = "0" * len(head)
    info = b"".join(index_line(p, entries.get(p), zero) for p in sync)
    res = update_index(R, info) if sync else None
    if res is not None:
        problems.append(T(
            "индекс не обновлён ({e}) — выполни: git reset -q {h} -- {p}; до этого обычный "
            "git commit откатит эти файлы", "index not updated ({e}) — run: git reset -q {h} -- "
            "{p}; until then a plain git commit would undo these files", e=oneline(errtext(res),
                                                                                   160),
            h=short(head), p=quoted(sync)))
    else:
        forget(cfg, sids, unsynced=[os.path.join(R.root, p) for p in sync])
    if touched:
        steps.say(T("Проверка", "Check"), T(
            "на {p} кто-то сделал stage во время коммита — индекс там не трогал",
            "someone staged {p} during the commit — left the index there alone",
            p=", ".join(touched[:5])), "verify", "note")
    if moved_away:
        # HEAD went to another branch while git commit ran (a post-commit hook, or someone):
        # the commit is on the planned branch and recorded; this checkout is not on that branch
        # any more, so nothing is merged or pushed from here.
        steps.say(T("Проверка", "Check"), T(
            "стоп — во время коммита HEAD перешёл на {b}; коммит {s} остался на {w} — не вливаю и "
            "не пушу; вернись на {w} и запусти /commit снова",
            "stop — HEAD moved to {b} during the commit; commit {s} stays on {w} — not merging "
            "or pushing; switch back to {w} and run /commit again",
            b=symbolic_head(R).replace("refs/heads/", "") or "detached HEAD", s=short(head),
            w=plan["branch"]), "verify", "stop")
        return head, False, trusted
    # Files committed whole must now equal the commit on disk (blob hashes, so the index's
    # stat cache does not matter).
    dirty = []
    full = [it["path"] for it in ours if it["status"] == "commit"]
    on_disk_now = hash_files(R, [p for p in full if entries.get(p) and entries[p][0] != "120000"
                                 and os.path.isfile(os.path.join(R.root, p))
                                 and not os.path.islink(os.path.join(R.root, p))])
    for path in full:
        entry = entries.get(path)
        if path in on_disk_now:
            if on_disk_now[path] != entry[1]:
                dirty.append(path)
            continue
        data, _ = read_worktree(os.path.join(R.root, path))
        if data is None or entry is None:
            if (data is None) != (entry is None):
                dirty.append(path)
            continue
        args = ["hash-object", "--stdin"] + ([] if entry[0] == "120000" else
                                             ["--path=" + path])
        if text_of(git(R.root, *args, stdin=data)).strip() != entry[1]:
            dirty.append(path)
    if dirty:
        problems.append(T("после коммита отличаются от него: {p} (их меняли во время коммита) — "
                          "коммит сделан, но не вливаю и не пушу; проверь эти файлы и запусти "
                          "/commit снова, чтобы влить и запушить",
                          "still differ from the commit: {p} (changed during the commit) — the "
                          "commit is made, but not merging or pushing; check those files and run "
                          "/commit again to merge and push it", p=", ".join(dirty[:5])))
    if problems:
        steps.say(T("Проверка", "Check"), T("расхождение — ", "mismatch — ") + "; ".join(problems),
                  "verify", "stop")
        return head, False, trusted
    return head, True, True


class PRFlow:
    """Publishing in a repository that takes work through pull requests (Publisher.pr_flow):
    push the branch with its upstream set and name the page to open its PR on. /commit opens or
    edits no PR (no gh): whose PR it is, and its text, are the user's call. Main is never moved
    here."""

    def pr_flow(self):
        R, b, d = self.R, self.branch, self.default
        why = self.plan.get("mode_why")
        self.steps.say(self.main_label, T("нет — в этом репо через PR", "no — this repository "
                                          "takes work through PRs") + (f" ({why})" if why else ""),
                       "merge", "skipped")
        if b == d:
            # Nothing new to commit (apply makes a branch for that): what is on main stays.
            up = "refs/remotes/origin/" + d
            ahead = rev_list(R.root, "HEAD", up) if rev(R.root, up) else []
            if not ahead and not self.extra:
                self.steps.say("Push", T("нет — нечего публиковать", "no — nothing to publish"),
                               "push", "skipped")
                self.steps.say("PR", T("нет — нечего публиковать", "no — nothing to publish"),
                               "pr", "skipped")
                return True
            if ahead and not self.check_foreign("HEAD", up, d, merged=(up,)):
                return False  # someone's commits: review and push them yourself
            return self.stop("Push", T(
                "в {d} {n} не в origin, а в этом репо работа идёт через PR — с {d} не пушу; "
                "перенеси их на ветку (git switch -c <имя>) и снова /commit, или /commit main — "
                "запушить прямо в {d}", "{d} has {n} origin lacks, and this repository takes "
                "work through PRs — not pushing {d}; move them to a branch (git switch -c "
                "<name>) and run /commit again, or /commit main to push to {d} directly", d=d,
                n=n_commits(len(ahead) + self.extra)), "push")
        if self.skip_push():
            self.steps.say("PR", T("нет — без push", "no — nothing pushed"), "pr", "skipped")
            return True
        if not self.fetch():
            return False
        up, up_main = branch_ref(R, b), "refs/remotes/origin/" + d
        tip = rev(R.root, "refs/heads/" + b) or R.head
        if not self.check_foreign(tip, published_refs(R, up, up_main), b, merged=(up_main, up)):
            return False
        if rev(R.root, up_main) and not self.extra and not rev_list(R.root, tip, up_main):
            for lab in ("Push", "PR"):
                self.steps.say(lab, T("нет — на ветке нет коммитов сверх origin/{d}",
                                      "no — the branch has nothing beyond origin/{d}", d=d),
                               lab.lower(), "skipped")
            return True
        if not self.mine(tip, up_main, "PR"):
            return True
        if up and rev_list(R.root, up, tip):
            return self.stop("Push", branch_behind(b, len(rev_list(R.root, up, tip))), "push")
        if not (up and self.has_everything(tip, up)) and not self.push(
                tip, b, None if upstream_of(R, b) else b):
            return False
        self.steps.say("PR", pr_page(R, b, d), "pr", "note")
        return True

    def mine(self, tip, up_main, *more):
        """With no new commit, a branch whose commits beyond origin/<default> hold none that
        this session's /commit made is not published (someone's branch a shared checkout sits
        on, a person's clean merge on it). -> False after its lines."""
        if self.extra or set(rev_list(self.R.root, tip, up_main if rev(
                self.R.root, up_main) else None)) & self.own:
            return True
        for lab in ("Push",) + more:
            self.steps.say(lab, T("нет — на ветке нет коммитов /commit этой сессии — если ветка "
                                  "твоя, {x} сам", "no — the branch holds no commit made by "
                                  "this session's /commit — if the branch is yours, {x} "
                                  "yourself", x=T("запушь её", "push it") if lab == "Push" else
                                  T("открой её PR", "open its PR")), lab.lower(), "skipped")
        return False


def pr_page(R, b, d):
    """The PR line: the page to open the branch's PR on (GitHub's compare page, which shows an
    open one instead), else what to run."""
    url = text_of(git(R.root, "config", "--get", "remote.origin.url", ok=None)).strip()
    m = re.match(r"(?:[a-z+]+://)?(?:[^@/]+@)?github\.com[:/]+(.+?)(?:\.git)?/*$", url)
    if m:
        return T("не открываю сам — открой (или посмотри открытый): {u}",
                 "not opened by /commit — open it (or see the open one): {u}",
                 u=f"https://github.com/{m.group(1)}/compare/{urllib.parse.quote(d)}..."
                   f"{urllib.parse.quote(b)}?expand=1")
    return T("не открываю сам — открой PR из {b} в {d} (с GitHub CLI: gh pr create --fill --base "
             "{d} --head {b})", "not opened by /commit — open a PR from {b} into {d} (with the "
             "GitHub CLI: gh pr create --fill --base {d} --head {b})", b=b, d=d)


class Publisher(PRFlow):
    """Publish after the commit, by fast-forward only: push the current branch, move main to the
    branch, push main. It never rebases, merges or pulls: anything that is not a fast-forward
    stops with the merge to run. Every method returns True to go on, False when it stopped (and
    said why).

    dry (plan): the same decisions on local refs, nothing fetched or written; `new_commit`: the
    commit apply will make on top of HEAD, counted where it matters. Lines then read as a
    forecast, and a stop that comes after the commit reads "will stop after the commit"."""

    def __init__(self, R, plan, own, steps, no_merge, no_push, dry=False, new_commit=False,
                 branch=None):
        self.R, self.plan, self.own, self.steps = R, plan, own, steps
        self.no_merge, self.no_push = no_merge, no_push
        self.dry, self.extra = dry, int(new_commit)
        self.branch = branch or plan["branch"]
        # plan: the commit goes to a branch apply makes from HEAD, so this checkout leaves main
        self.leaving = bool(dry and branch and branch != plan["branch"])
        self.mode = plan.get("mode") or "direct"
        self.default = plan.get("default") or default_branch(R) or self.branch
        self.remote = has_origin(R)
        self.main_label = T("В {b}", "Into {b}", b=self.default)
        self.work = False  # something is (or would be) moved or pushed
        self.stopped = None

    def stop(self, lab, text, step):
        self.stopped = text
        head = T("стоп — ", "stop — ") if not self.dry else \
            T("нет — остановлюсь после коммита: ", "no — will stop after the commit: ") \
            if self.extra else T("нет — стоп: ", "no — stop: ")
        self.steps.say(lab, head + text, step, "stop")
        return False

    def fetch(self, lab="Push"):
        if not self.remote or self.dry:
            return True
        proc = git(self.R.root, "fetch", "--quiet", "--no-recurse-submodules", "origin", ok=None)
        if proc.returncode != 0:
            e = oneline(errtext(proc), 160)
            if lab == "Push":
                return self.stop("Push", T("fetch не прошёл ({e}); коммит остался локально",
                                           "fetch failed ({e}); the commit stays local", e=e),
                                 "fetch")
            return self.stop(lab, T("fetch не прошёл ({e}); {d} не сдвинут, коммит остался "
                                    "локально", "fetch failed ({e}); {d} not moved, the commit "
                                    "stays local",
                                    e=e, d=self.default), "fetch")  # apply adds the Push line
        return True

    def push(self, src, dst, track=None):
        """Push commit `src` (a sha: the one checked for foreign commits, not whatever a branch
        holds by the time the push runs) to origin/<dst>; `track`: a local branch to give
        origin/<dst> as its upstream afterwards."""
        tracking = "refs/remotes/origin/" + dst
        seen = rev(self.R.root, tracking)
        self.work = True
        if self.dry:
            self.steps.say("Push", T("да — origin/{t}", "yes — origin/{t}", t=dst) + (
                "" if seen else T(" (новая ветка на origin)", " (new branch on origin)")))
            return True
        proc = git(self.R.root, "push", "--quiet", "--no-follow-tags", "--recurse-submodules=no",
                   "origin", f"{src}:refs/heads/{dst}", ok=None)
        if proc.returncode != 0:
            err = errtext(proc)
            # A race: origin/<dst> moved after our fetch. Only then is a rerun the answer.
            git(self.R.root, "fetch", "--quiet", "--no-recurse-submodules", "origin", ok=None)
            race = rev(self.R.root, tracking) != seen
            # Only a refusal by origin is called one: a pre-push hook, a bad URL or a missing
            # key fail before origin sees the push.
            refused = re.search(r"\[(remote )?rejected\]|declined|protected", err, re.I)
            way = ""
            if race:
                way = T(" — на origin появились новые коммиты; запусти /commit снова",
                        " — origin got new commits meanwhile; run /commit again")
            elif dst == self.default and refused:
                # A protected main (pull requests only) rejects every rerun the same way.
                ahead = len(rev_list(self.R.root, src, tracking)) if seen else 0
                way = T(" — если {d} защищён: {x}; локальный {d} остаётся впереди origin/{d} на "
                        "{n} этой сессии", " — if {d} is protected: {x}; local {d} stays ahead of "
                        "origin/{d} by {n} of this session's", d=dst, n=n_commits(ahead), x=T(
                            "/commit no-merge запушит ветку для pull request",
                            "/commit no-merge pushes the branch for a pull request")
                        if self.branch != dst else T(
                            "перенеси работу на ветку (git switch -c <имя>) и запусти /commit "
                            "no-merge", "move the work to a branch (git switch -c <name>) and "
                            "run /commit no-merge"))
            what = T("origin/{d} отклонил push", "origin/{d} rejected the push", d=dst) \
                if refused else T("push не прошёл", "push failed")
            return self.stop("Push", what + f": {oneline(err, 200)}" + way, "push")
        if track:
            git(self.R.root, "branch", "--quiet", "--set-upstream-to=origin/" + dst, track,
                ok=None)
        self.steps.say("Push", T("готово — origin/{d} → {s}", "done — origin/{d} → {s}", d=dst,
                                 s=short(src)), "push", "done")
        return True

    def no_push_reason(self):
        if self.no_push:
            return T("нет — по аргументу", "no — as asked")
        if not self.remote:
            return no_remote(self.R)
        return None

    def skip_push(self):
        """Say why nothing is pushed, when that is asked or there is no origin. -> True then."""
        reason = self.no_push_reason()
        if reason:
            self.steps.say("Push", reason, "push", "skipped")
        return bool(reason)

    def has_everything(self, src, up):
        """origin already holds src (and no new commit is coming on top of it)."""
        if self.extra or rev_list(self.R.root, src, up):
            return False
        self.steps.say("Push", T("нет — {u} уже содержит всё", "no — {u} already has everything",
                                 u=up[len("refs/remotes/"):]), "push", "skipped")
        return True

    def run(self):
        R = self.R
        if not R.head:  # unborn: only plan gets here (apply has made the first commit)
            if self.extra:
                self.steps.say(self.main_label, T("нет — новая ветка без истории",
                                                  "no — a new branch with no history")
                               if self.branch != self.default else
                               T("нет — уже на {b}", "no — already on {b}", b=self.default))
                return self.skip_push() or self.push(None, self.branch)
            self.steps.say("Push", T("нет — нечего публиковать", "no — nothing to publish"))
            return True
        if self.mode == "pr":
            return self.pr_flow()
        if self.branch == self.default:
            return self.on_default()
        orphan = unrelated(R, self.default)
        if self.no_merge or orphan:
            return self.branch_only(orphan)
        return self.into_default()

    def check_foreign(self, include, exclude, where, merged=()):
        foreign = foreign_commits(self.R, include, exclude, self.own, merged)
        if foreign and where != self.default and not set(foreign) & set(text_of(git(
                self.R.root, "rev-list", include, "--not", "--remotes=origin", "--")).split()):
            # all on origin already, on other branches (a stacked PR, a branch of develop)
            return self.stop("Push", T(
                "на {w} коммиты не из /commit этой сессии с других веток origin: {l} — с ними "
                "ветку не публикую; запушь её сам (git push -u origin {w}) и открой PR против её "
                "базы", "{w} carries commits not made by this session's /commit from other "
                "branches of origin: {l} — not publishing the branch with them; push it yourself "
                "(git push -u origin {w}) and open the PR against its base", w=where,
                l=subjects(self.R, foreign)), "push")
        if foreign:
            return self.stop("Push", unpushed_foreign(self.R, where, foreign), "push")
        return True

    def on_default(self):
        R, b = self.R, self.branch
        if self.dry:
            self.steps.say(self.main_label, T("нет — уже на {b}", "no — already on {b}", b=b))
        if self.skip_push():
            return True
        if not self.fetch():
            return False
        up = "refs/remotes/origin/" + b
        track = None if upstream_of(R, b) else b
        tip = rev(R.root, "refs/heads/" + b)
        if not rev(R.root, up):
            return self.check_foreign(tip, None, b) and self.push(tip, b, track)
        if self.has_everything(tip, up) or not self.check_foreign(tip, up, b, merged=(up,)):
            return self.stopped is None
        behind = len(rev_list(R.root, up, tip))
        if behind:
            return self.stop("Push", origin_ahead(b, behind), "push")
        return self.push(tip, b, track)

    def branch_only(self, orphan=False):
        """The branch alone to origin/<branch>: as asked (no-merge), or a branch with no history
        in common with main (an orphan)."""
        R, b = self.R, self.branch
        self.steps.say(self.main_label, T("нет — новая ветка без истории",
                                          "no — a new branch with no history") if orphan else
                       T("нет — по аргументу", "no — as asked"), "merge", "skipped")
        if self.skip_push():
            return True
        if not self.fetch():
            return False
        up = branch_ref(R, b)
        up_main = "refs/remotes/origin/" + self.default
        tip = rev(R.root, "refs/heads/" + b) or R.head  # plan: a branch apply makes from HEAD
        if not self.check_foreign(tip, published_refs(R, up, up_main), b, merged=(up_main, up)):
            return False
        if not self.mine(tip, up_main):
            return True
        if up:
            behind = len(rev_list(R.root, up, tip))
            if behind:
                return self.stop("Push", branch_behind(b, behind), "push")
            if self.has_everything(tip, up):
                return True
        return self.push(tip, b, None if upstream_of(R, b) else b)

    def move_main(self, target):
        """Fast-forward the default branch to `target` (lag() has checked that it is one): in its
        checkout with `git merge --ff-only`, else with `git fetch .`, which refuses anything but a
        fast-forward. Someone else's checkout of main is only moved when it has nothing
        unfinished and no uncommitted change in a file the move changes."""
        R, d = self.R, self.default
        where, gone = checkout_of(R, d)
        if self.leaving and where and os.path.realpath(where) == os.path.realpath(R.root):
            where = None  # apply switches this checkout to the new branch first
        main_ref = "refs/heads/" + d
        if where and gone:
            return self.stop(self.main_label, T(
                "{b} занят удалённым worktree {w} — выполни git worktree prune и запусти /commit "
                "снова", "{b} is held by a deleted worktree {w} — run git worktree prune and "
                "/commit again", b=d, w=where), "merge")
        if where:
            gd = text_of(git(where, "rev-parse", "--absolute-git-dir")).strip()
            op = git_state(gd) or ("conflicts" if git(where, "ls-files", "-u", "-z").stdout
                                   else None)
            if op:
                what = op_stop(op, gd) if op != "conflicts" else T(
                    "неразрешённые конфликты", "unresolved conflicts")
                return self.stop(self.main_label, T(
                    "checkout {b} в {w}: {x}; там, затем запусти /commit снова",
                    "the checkout of {b} at {w}: {x}; there, then run /commit again", b=d,
                    w=where, x=what), "merge")
            moving = changed_paths(R, main_ref, target)
            if self.extra:
                moving |= {it["path"] for it in self.plan["files"]
                           if it.get("status") in ("commit", "partial")}
            hit = in_the_way(where, moving)
            if hit:
                return self.stop(self.main_label, (T("{b} не сдвинут: ", "{b} not moved: ", b=d)
                                                   if not self.dry else "") +
                                 checkout_blocks(d, where, hit, no_merge_passes(
                                     R, self.branch, d, self.own)), "merge")
            if self.dry:
                return True
            # --no-overwrite-ignore: someone's ignored files there (a personal .env, editor
            # settings) stop the move instead of being replaced by tracked ones.
            proc = git(where, *NO_SIDE_EFFECTS, "merge", "--ff-only", "--quiet",
                       "--no-overwrite-ignore", target, ok=None)
            if proc.returncode != 0:
                # git's own advice there says to stash, which /commit never does.
                err = "\n".join(ln for ln in errtext(proc).split("\n")
                                 if "stash" not in ln and ln.strip() != "Aborting")
                return self.stop(self.main_label, T(
                    "{b} не сдвинут: {e} (checkout {w}) — разберись там и запусти /commit снова",
                    "{b} not moved: {e} (checkout {w}) — sort it out there and run /commit "
                    "again", b=d, e=oneline(err, 200), w=where), "merge")
            return True
        if self.dry:
            return True
        proc = git(R.root, "fetch", "--quiet", ".", f"{target}:{main_ref}", ok=None)
        if proc.returncode != 0:
            return self.stop(self.main_label, T("{b} не сдвинут: {e}", "{b} not moved: {e}", b=d,
                                                e=oneline(errtext(proc), 200)), "merge")
        return True

    def into_default(self):
        R, b, d = self.R, self.branch, self.default
        if not self.fetch(self.main_label):
            return False
        main_ref, up = "refs/heads/" + d, "refs/remotes/origin/" + d
        have_up = bool(rev(R.root, up))
        if not rev(R.root, main_ref):
            return self.stop(self.main_label, no_main(R, d, have_up), "merge")
        if have_up:
            foreign = foreign_commits(R, main_ref, up, self.own, merged=(up,))
            if foreign:
                return self.stop(self.main_label, main_foreign_text(
                    R, d, foreign, no_merge_passes(R, b, d, self.own)), "merge")
        excl = [main_ref] + ([up] if have_up else [])
        old_main = rev(R.root, main_ref)
        n = len(rev_list(R.root, "HEAD", excl)) + self.extra
        if not n:
            self.steps.say(self.main_label, T("нет — ветка {b} уже в {d}",
                                              "no — branch {b} is already in {d}", b=b, d=d),
                           "merge", "skipped")
        else:
            # Nothing foreign on the branch — checked before main moves, so a run that stops
            # here never touches main's checkout.
            foreign = foreign_commits(R, "HEAD", excl, self.own, merged=excl + [branch_ref(R, b)])
            if foreign:
                return self.stop(self.main_label, branch_foreign(R, b, d, foreign, self.own),
                                 "merge")
            behind = lag(R, d, have_up)
            if behind:
                return self.stop(self.main_label, behind, "merge")
            if not self.move_main("refs/heads/" + b):
                return False
            self.work = True
            self.steps.say(self.main_label, T(
                "да — ветка {br} → fast-forward {d} ({n})", "yes — branch {br} → fast-forward {d} "
                "({n})", br=b, d=d, n=n_commits(n)) if self.dry else T(
                "готово — {d} → {s} (fast-forward, {n})", "done — {d} → {s} (fast-forward, {n})",
                d=d, s=short(rev(R.root, main_ref)), n=n_commits(n)), "merge", "done")
        if self.skip_push():
            return True
        src = rev(R.root, "HEAD" if self.dry and n else main_ref)  # main once moved
        if have_up and self.has_everything(src, up):
            return True
        if have_up and not is_ancestor(R.root, up, src):
            # Local main is ahead of origin/main and behind it (an earlier no-push run).
            return self.stop("Push", lag(R, d, have_up) or T(
                "локальный {d} разошёлся с origin/{d} — сведи их вручную",
                "local {d} and origin/{d} diverged — reconcile them by hand", d=d), "push")
        return self.check_foreign(src, up if have_up else None, d,
                                  merged=(up, old_main) if have_up else ()) and self.push(src, d)


def stopped_before(R, plan, steps, args):
    """A stop at the commit step: the merge and push lines still say what became of them.
    -> exit code 1."""
    default = plan.get("default") or plan["branch"]
    if plan["branch"] != default:
        steps.say(T("В {b}", "Into {b}", b=default), T("нет — после стопа", "no — stopped before"),
                  "merge", "skipped")
    if args.no_push or plan.get("no_push"):
        why_not = T("нет — по аргументу", "no — as asked")
    elif not has_origin(R):
        why_not = no_remote(R)
    else:
        why_not = T("нет — после стопа", "no — stopped before")
    steps.say("Push", why_not, "push", "skipped")
    if plan.get("mode") == "pr":
        steps.say("PR", T("нет — после стопа", "no — stopped before"), "pr", "skipped")
    return 1


def make_branch(R, plan, message, task, steps):
    """Switch to the planned new branch, made from HEAD (`git switch -c`: the working tree and
    the index stay). -> its name, or None after a stop line."""
    lab = T("Ветка", "Branch")
    name = plan["new_branch"]
    if not name:  # session/<slug of the commit subject>; a taken name gets -2, -3, …
        subject = message.decode("utf-8", "replace").strip().split("\n", 1)[0]
        base = "session/" + slug(subject)
        name = next((n for n in [base] + [f"{base}-{i}" for i in range(2, 10)]
                     if not rev(R.root, "refs/heads/" + n)
                     and not rev(R.root, "refs/remotes/origin/" + n)), base)
    problem = branch_problem(R, name, task and task["id"])
    if problem:
        steps.say(lab, T("нет — стоп: ", "no — stop: ") + problem, "branch", "stop")
        return None
    proc = git(R.root, "switch", "-q", "-c", name, ok=None)
    hook = ""
    if proc.returncode != 0:  # a failing post-checkout hook fails the switch after it is made
        if symbolic_head(R) != "refs/heads/" + name:
            steps.say(lab, T("нет — стоп: git switch -c отказал: {e}", "no — stop: git switch -c "
                             "refused: {e}", e=oneline(errtext(proc), 200)), "branch", "stop")
            return None
        hook = T(" (post-checkout хук упал: {e})", " (the post-checkout hook failed: {e})",
                 e=oneline(errtext(proc), 160))
    steps.say(lab, T("{b} — создана от {d}", "{b} — created from {d}", b=name, d=plan["branch"])
              + hook, "branch", "done")
    return name


def unmake_branch(R, plan, name, old, steps):
    """No commit landed on the branch make_branch made: back to the old branch, and the empty
    branch goes — only while it is checked out here and still at the old HEAD."""
    lab = T("Ветка", "Branch")
    if symbolic_head(R) != "refs/heads/" + name or rev(R.root, "refs/heads/" + name) != R.head:
        steps.say(lab, T("{b} осталась (коммита нет, HEAD уже не на ней) — убери её сам, если не "
                         "нужна: git branch -d {b}", "{b} stays (no commit, HEAD is not on it any "
                         "more) — delete it yourself if unused: git branch -d {b}", b=name),
                  "branch", "note")
        return
    git(R.root, "switch", "-q", old, ok=None)
    if symbolic_head(R) == "refs/heads/" + old:  # by HEAD: a post-checkout hook may fail it
        git(R.root, "update-ref", "-d", "refs/heads/" + name, R.head, ok=None)
        steps.say(lab, T("{b} убрана — коммита нет; снова на {d}", "{b} removed — no commit; back "
                         "on {d}", b=name, d=old), "branch", "undone")
    else:
        steps.say(lab, T("{b} пустая и выгружена — коммита нет; вернись: git switch {d} && git "
                         "branch -d {b}", "{b} is empty and checked out — no commit; go back: git "
                         "switch {d} && git branch -d {b}", b=name, d=old), "branch", "stop")


def apply_cmd(args):
    with open(args.plan, encoding="utf-8") as fh:
        plan = json.load(fh)
    global LANG
    LANG = args.lang or plan.get("lang") or "en"
    if args.pr and args.main:
        raise Fail("--pr and --main contradict each other: pass one")
    if args.pr or args.main:
        plan["mode"], plan["mode_why"] = ("pr" if args.pr else "direct"), T("по аргументу",
                                                                            "as asked")
    task = plan.get("task")
    if args.task:
        found = task_of(args.task)
        if not found:
            raise Fail(T("--task {r}: не вижу id задачи — передай --task <id>",
                         "--task {r}: no task id in it — pass --task <id>", r=args.task))
        task = {"id": found[0], "url": found[1]}
    refused = refuse_in_ticket(plan["config"], plan["sessions"], T("Коммит", "Commit"))
    if refused is not None:
        return refused
    R = Repo(plan["repo"])
    _NAMES.clear()
    steps = Steps()
    result = {"commit": None, "steps": steps.steps, "lines": steps.lines}
    run_dir = plan["run_dir"]

    def finish(code):
        result["exit"] = code
        with open(os.path.join(run_dir, "result.json"), "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=1)
        return code

    # The index entries an earlier commit left stale are put back also when the plan stops: its
    # Index line promised it.
    if not resync(R, plan, steps):
        return finish(1)
    if plan.get("verdict") == "stop":
        steps.say(T("Стоп", "Stop"), plan.get("stop") or "?", "plan", "stop")
        return finish(1)
    try:
        check_stale(R, plan)
    except Stale as e:
        print(f"plan is stale, run plan again: {e}", file=sys.stderr)
        return finish(3)
    ours = [i for i in plan["files"] if i.get("status") in ("commit", "partial")]
    own = set(plan.get("own_commits") or []) | load_own(plan["config"], plan["sessions"])
    sids = plan["sessions"]
    if ours:
        if not args.message_file:
            raise Fail("--message-file is required: the plan has files to commit")
        if args.message_file == "-":
            message = sys.stdin.buffer.read()
        else:
            try:
                with open(args.message_file, "rb") as fh:
                    message = fh.read()
            except OSError as e:
                raise Fail(f"cannot read the message file {args.message_file}: "
                           f"{e.strerror or e}")
        if not message.strip():
            raise Fail("the commit message is empty")
        if task and not mentions_task(message, task):
            raise Fail(f"the commit message does not name task {task['id']}: add its link "
                       f"({task['url'] or 'or its id'}) as the repository's history does, then "
                       "run apply again")
        old_branch, branch = plan["branch"], None
        if plan.get("new_branch") is not None:
            branch = make_branch(R, plan, message, task, steps)
            if not branch:
                return finish(stopped_before(R, plan, steps, args))
            plan["branch"] = branch
        if task:
            steps.say(T("Задача", "Task"), T("{t} — ссылка в сообщении коммита",
                                             "{t} — named in the commit message", t=task["id"]))
        try:
            made = commit_step(R, plan, message, steps)
        except Stale as e:
            if branch:
                unmake_branch(R, plan, branch, old_branch, steps)
            print(f"plan is stale, run plan again: {e}", file=sys.stderr)
            return finish(3)
        except Moved:
            if branch:
                unmake_branch(R, plan, branch, old_branch, steps)
            return finish(3)
        if not made:
            if branch:
                unmake_branch(R, plan, branch, old_branch, steps)
            return finish(stopped_before(R, plan, steps, args))
        head, verified, trusted = made
        result["commit"] = head
        if trusted:
            own.add(head)  # recorded by commit_step, with the last edit of each path it holds
        if not verified:
            return finish(stopped_before(R, plan, steps, args))
        R = Repo(plan["repo"])
    else:
        steps.say(T("Коммит", "Commit"), T("нет — новых правок нет; публикую сделанное",
                                           "no — nothing new; publishing what is committed"),
                  "commit", "skipped")
    pub = Publisher(R, plan, own, steps, args.no_merge or plan.get("no_merge"),
                    args.no_push or plan.get("no_push"))
    try:
        ok = pub.run()
    except Fail as e:
        steps.say(T("Стоп", "Stop"), T("ошибка git: {e} — сделанное выше осталось как есть",
                                       "git error: {e} — the steps above stay done",
                                       e=oneline(str(e), 300)), "error", "stop")
        ok = False
    if not ok:
        push_line_after_stop(pub, steps.lines)
    return finish(0 if ok else 1)


def on_term(signum, frame):
    # A kill (the Bash tool's timeout, Esc) runs the `finally` blocks that record what is done.
    raise SystemExit(128 + signum)


def main(argv):
    ap = argparse.ArgumentParser(prog="sessioncommit.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="work out what this session would commit (read-only)")
    p.add_argument("--repo", default=".", help="the repository (default: current directory)")
    p.add_argument("--session", action="append", help="session id (repeatable; default "
                   "$CLAUDE_CODE_SESSION_ID)")
    p.add_argument("--declare", action="append", help="a file (or directory) this session "
                   "changed through Bash; committed whole (repeatable)")
    p.add_argument("--out", help="run directory (default under <config>/cache/kensei-commit)")
    p.add_argument("--lang", choices=("ru", "en"), default="en")
    p.add_argument("--no-merge", action="store_true", help="push the branch alone; do not move "
                   "the default branch")
    p.add_argument("--no-push", action="store_true", help="do not push")
    p.add_argument("--task", help="the task this work is for: a tracker link or an id; apply "
                   "checks that the commit message names it")
    p.add_argument("--branch", help="the branch to create from HEAD when the commit needs one "
                   "(task/<id>-<slug>, session/<slug>)")
    p.add_argument("--pr", action="store_true", help="this repository takes work through pull "
                   "requests: push the branch (the PR is opened by the user), never touch main")
    p.add_argument("--main", action="store_true", help="this repository takes work directly: "
                   "fast-forward main and push it")
    a = sub.add_parser("apply", help="commit, merge and push as planned")
    a.add_argument("--plan", required=True, help="RUN/plan.json from plan")
    a.add_argument("--message-file", help="the commit message, or - for stdin")
    a.add_argument("--lang", choices=("ru", "en"))
    a.add_argument("--no-merge", action="store_true")
    a.add_argument("--no-push", action="store_true")
    a.add_argument("--task")
    a.add_argument("--pr", action="store_true")
    a.add_argument("--main", action="store_true")
    args = ap.parse_args(argv)
    global LANG
    try:
        if args.cmd == "plan":
            LANG = args.lang
            return plan_cmd(args)
        try:
            old = signal.signal(signal.SIGTERM, on_term)
        except ValueError:
            return apply_cmd(args)  # not the main thread
        try:
            return apply_cmd(args)
        finally:
            signal.signal(signal.SIGTERM, old)
    except Fail as e:
        print(f"commit: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
