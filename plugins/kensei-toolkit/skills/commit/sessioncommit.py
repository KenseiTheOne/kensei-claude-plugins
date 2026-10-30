#!/usr/bin/env python3
"""commit — land this session's work in git and leave everyone else's alone.

  sessioncommit.py plan [--repo DIR] [--session ID]... [--declare PATH]... [--out DIR]
                        [--lang ru|en] [--no-merge] [--no-push]
      Read this session's transcripts (the main one, its subagents and workflow agents), find
      the files it edited in the repository and split each into this session's changes and
      everyone else's. Writes plan.json and the content to commit into a private run directory
      (default <config>/cache/kensei-commit/<repo>/<stamp>, the newest KEEP_RUNS kept). Read-only
      for the repository: no fetch, no index write. Prints the status block (what will be
      committed, skipped, left alone, merged and pushed), then details for the model.
      Exit 0: go, or nothing to do; 1: stop (nothing may be applied); 2: error.
  sessioncommit.py apply --plan RUN/plan.json [--message-file FILE|-] [--no-merge] [--no-push]
      Re-check the plan against the repository (exit 3 when it moved), commit exactly the planned
      content through a private index (the repository's hooks and signing still apply), set the
      real index entries of those paths to the new commit and nothing else, then integrate into
      the default branch and push — never forced, never stashed. One status line per step.
      Exit 0: every planned step done; 1: stopped at a step (its line says why; earlier steps
      stay done); 2: error; 3: stale plan.

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

KEEP_RUNS = 20          # run directories kept per repository
MAX_HINTS = 40          # shell commands kept in plan.json as possible file changes
SHOW_HINTS = 12         # of those, the newest printed
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
SHELL_TOOLS = ("Bash", "PowerShell")
SID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
TOOL_ID_RE = re.compile(r'"tool_use_id"\s*:\s*"([^"\\]+)"')
# `[main 1a2b3c4] subject`, `[main (root-commit) 1a2b3c4] subject`, `[detached HEAD 1a2b3c4] …`
COMMIT_LINE_RE = re.compile(r"^\[[^\]\n]*? ([0-9a-f]{7,64})\] ", re.M)
# A shell command that may make commits (`git commit -q` prints no `[branch sha]` line): the
# reflog entries written while it ran may be the session's own commits. A quick filter; the
# git subcommand itself is read by git_call.
GIT_WRITE_RE = re.compile(r"\bgit\b[^\n|;&]*?\s(?:commit|merge|cherry-pick|revert|pull)(?![-\w])")
GIT_COMMIT_SUBS = ("commit", "merge", "pull", "cherry-pick", "revert")
GIT_ARG_OPTS = ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env")
# Reflog messages of entries that made a new commit (not a fast-forward, reset or checkout,
# whose new id is someone else's commit).
OWN_REFLOG_RE = re.compile(r"^(?:commit(?: \([a-z]+\))?|cherry-pick|revert):|^(?:merge|pull)\b.*"
                           r": Merge made by ")
# Shell commands that may have changed files. Hints only: the script never takes paths from them.
HINT_RE = re.compile(r"""(?x)
    \b(?:sed|perl)\b[^|;&\n]*\s-[a-zA-Z]*i\b | \bsed\b[^|;&\n]*--in-place
  | (?:^|[\s;&|(])(?:rm|rmdir|mv|cp|touch|tee|ln|install|rsync|unzip|patch|truncate|dd|chmod)\s
  | \bgit\s+(?:rm|mv|apply|am|restore|checkout|reset|stash|cherry-pick|revert|merge|rebase|pull
              |update-index)(?![-\w])
  | \btar\s+-?[a-zA-Z]*x
  | \b(?:python3?|node|ruby|perl|deno|bun)\s+(?:-[a-zA-Z]*[ce]\b|-(?:\s|$))
  | \b(?:prettier|black|ruff|gofmt|rustfmt|clang-format|dotnet\s+format|npm\s+(?:i|install|ci)
        |yarn|pnpm|pip\s+install|Set-Content|Out-File|Remove-Item|Move-Item|Copy-Item)\b
  | \bUnity(?:\.exe)?\b[^\n]*-(?:batchmode|executeMethod|projectPath)
""")
# A redirect into a file — looked for outside quoted strings (`grep "List<Ship>"` is no redirect).
REDIRECT_RE = re.compile(r"(?<![<>&0-9=-])>{1,2}(?![&=])")
QUOTED_RE = re.compile(r"""'[^']*'|"(?:[^"\\]|\\.)*\"""")
HINT_NOISE_RE = re.compile(r"\d?>>?\s*/dev/null|&>\s*/dev/null|\d?>&\d|<<-?\s*'?\w+'?")


def may_write(text):
    """True when a shell command (or part of one) may change files: a hint, never a path."""
    return bool(HINT_RE.search(text) or REDIRECT_RE.search(QUOTED_RE.sub("''", text)))
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


# Never stash someone's work on the way (merge.autoStash / rebase.autoStash in their config).
NO_AUTOSTASH = ("-c", "merge.autoStash=false", "-c", "rebase.autoStash=false")


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
    return ((proc.stderr or b"") + (proc.stdout or b"")).decode("utf-8", "replace").strip()


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
                                    ok=(0, 1))).strip().lower() in ("true", "1", "yes", "on"):
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


def make_patch(before, after, context=3):
    """structuredPatch-shaped hunks (LF lines, "\\ No newline" markers) from before to after."""
    a, b = split_lines(before), split_lines(after)
    hunks = []
    for group in difflib.SequenceMatcher(None, a, b, autojunk=False).get_grouped_opcodes(context):
        i1, i2, j1, j2 = group[0][1], group[-1][2], group[0][3], group[-1][4]
        out = []

        def add(tag, line):
            out.append(tag + line.rstrip("\n"))
            if not line.endswith("\n"):
                out.append("\\ No newline at end of file")
        for tag, a1, a2, b1, b2 in group:
            if tag == "equal":
                for line in a[a1:a2]:
                    add(" ", line)
            else:
                for line in a[a1:a2]:
                    add("-", line)
                for line in b[b1:b2]:
                    add("+", line)
        hunks.append({"oldStart": i1 + 1 if i2 > i1 else i1, "oldLines": i2 - i1,
                      "newStart": j1 + 1 if j2 > j1 else j1, "newLines": j2 - j1, "lines": out})
    return hunks


READ_ROW_RE = re.compile(r"^\s*(\d+)\t(.*)$")
READ_MAX_LINES = 2000   # Read shows at most this many lines without offset/limit
READ_MAX_WIDTH = 2000   # and cuts longer lines


def read_content(inp, text, tur):
    """The whole file a Read call showed (LF text), or None when it may be cut or is not text.
    "unchanged" when Read said the file did not change since the agent's last Read."""
    if inp.get("offset") not in (None, 0, 1) or inp.get("limit") is not None or \
            inp.get("pages") is not None:
        return None
    f = tur.get("file") if isinstance(tur, dict) else None
    if isinstance(f, dict) and isinstance(f.get("content"), str):
        if f.get("startLine", 1) == 1 and f.get("numLines") == f.get("totalLines"):
            return norm(f["content"])
        return None
    rows = text.split("\n")
    if not READ_ROW_RE.match(rows[0]) and "unchanged since" in text.lower():
        return "unchanged"  # no file rows at all: a stub, not file text that says so
    body = []
    for i, row in enumerate(rows):
        m = READ_ROW_RE.match(row)
        if not m:
            rest = "\n".join(rows[i:]).strip()
            if rest and not rest.startswith("<system-reminder>"):
                return None
            break
        if int(m.group(1)) != len(body) + 1:
            return None
        body.append(m.group(2))
    if not body:
        if "unchanged since" in text.lower():
            return "unchanged"
        return "" if "contents are empty" in text else None
    if len(body) >= READ_MAX_LINES or any(len(b) >= READ_MAX_WIDTH for b in body):
        return None
    return "\n".join(body)


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
    transcript, as workflow agents leave it: what the file held before is unknown here),
    replace (old/new strings from the tool input when the transcript has no toolUseResult),
    opaque (NotebookEdit: no patch)."""
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


def resolve_rewrite(ev, fi, events, last_read):
    """Turn a rewrite (a Write over an existing file, no patch recorded) into a patch: the file
    before it is the agent's last whole Read of the path in the same transcript plus the
    agent's own edits since. Left a rewrite when there is no such Read."""
    seen = last_read.get((fi, ev["path"]))
    while True:
        if not seen or seen[0] is None:
            return ev
        content, since, prev = seen
        if isinstance(content, tuple):
            content = read_content(*content)
            if content == "unchanged":
                # "File unchanged since last read": the agent's previous view still holds.
                seen = prev
                continue
            if content is None:
                return ev
        break
    between = [e for e in events[since:] if e["path"] == ev["path"] and e["order"][1] == fi]
    try:
        before = forward(content, between)
    except (NotFound, Ambiguous):
        return ev
    if any(e["kind"] in ("rewrite", "opaque") for e in between):
        return ev
    return {"path": ev["path"], "kind": "patch", "hunks": make_patch(before, norm(ev["content"])),
            "from_read": True}


def read_transcripts(files):
    """Edit events in time order, shell commands that may have changed files (full text, with
    their timestamps), commit shas from shell output (with the output's timestamp) and the time
    spans of shell commands that may have made commits ((start, end) timestamps)."""
    uses, seen, events, hints, shas, git_runs = {}, set(), [], [], [], []
    # (file index, path) -> (whole content | Read call | None, len(events) at it, previous entry)
    last_read = {}
    tools = EDIT_TOOLS + SHELL_TOOLS + ("Read",)
    for fi, path in enumerate(files):
        try:
            fh = open(path, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for ln, line in enumerate(fh):
                if '"tool_use"' in line:
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    if entry.get("type") != "assistant":
                        continue
                    for b in blocks(entry):
                        name = b.get("name")
                        if b.get("type") != "tool_use" or name not in tools:
                            continue
                        inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                        uses[b.get("id")] = (name, inp, entry.get("cwd"),
                                             str(entry.get("timestamp") or ""))
                elif '"tool_result"' in line:
                    if not any(i in uses and i not in seen for i in TOOL_ID_RE.findall(line)):
                        continue
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    if entry.get("type") != "user":
                        continue
                    results = [b for b in blocks(entry) if b.get("type") == "tool_result"]
                    tur = entry.get("toolUseResult") if len(results) == 1 else None
                    ts = str(entry.get("timestamp") or "")
                    for k, b in enumerate(results):
                        tid = b.get("tool_use_id")
                        if tid not in uses or tid in seen:
                            continue
                        seen.add(tid)
                        name, inp, cwd, start = uses[tid]
                        text = result_text(b, tur)
                        if name in SHELL_TOOLS:
                            # A shell command that exited non-zero ("Exit code N") may still
                            # have changed files or committed before the part that failed; one
                            # the user rejected or a hook or permission blocked never ran.
                            if (b.get("is_error") or isinstance(tur, str)) and \
                                    not text.lstrip().startswith("Exit code"):
                                continue
                            cmd = inp.get("command")
                            where = cwd or entry.get("cwd")
                            if isinstance(cmd, str):
                                if may_write(HINT_NOISE_RE.sub(" ", cmd)):
                                    hints.append((ts, cmd, where))
                                if GIT_WRITE_RE.search(cmd):
                                    git_runs.append((start, ts, cmd, where))
                            shas += [(s, ts) for s in COMMIT_LINE_RE.findall(text)]
                            continue
                        if b.get("is_error") or isinstance(tur, str):
                            continue  # a failed edit changed nothing
                        tur_d = tur if isinstance(tur, dict) else None
                        if name == "Read":
                            p = event_path(tur_d, inp, cwd or entry.get("cwd"))
                            if p:
                                # Parsed only if a rewrite of the path needs it. A Read that
                                # shows no file rows ("unchanged since last read") keeps the one
                                # before it to fall back on.
                                rows = READ_ROW_RE.match(text.split("\n", 1)[0])
                                last_read[(fi, p)] = ((inp, text, tur_d), len(events),
                                                      None if rows else last_read.get((fi, p)))
                            continue
                        for j, ev in enumerate(tool_events(name, inp, tur_d,
                                                           cwd or entry.get("cwd"), text)):
                            ev["order"] = (ts, fi, ln, k, len(events))
                            ev["id"] = f"{tid}:{j}"
                            if ev["kind"] == "rewrite":
                                order, eid = ev["order"], ev["id"]
                                ev = resolve_rewrite(ev, fi, events, last_read)
                                ev["order"], ev["id"] = order, eid
                            events.append(ev)
                            if name == "Write" and isinstance(inp.get("content"), str):
                                # After a Write the file is its content: a later Write of the
                                # agent builds on that, not on an older Read.
                                last_read[(fi, ev["path"])] = (norm(inp["content"]), len(events),
                                                               None)
                            elif ev["kind"] == "opaque":
                                last_read[(fi, ev["path"])] = (None, len(events), None)
                elif '"edited_text_file"' in line:
                    # "This file was modified by the user or a linter": the agent's earlier Read
                    # no longer shows the file, and Claude Code lets it write without a new Read.
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    att = entry.get("attachment")
                    if entry.get("type") != "attachment" or not isinstance(att, dict) or \
                            att.get("type") != "edited_text_file":
                        continue
                    p = event_path({"filePath": att.get("filename")}, {}, entry.get("cwd"))
                    if p:
                        last_read[(fi, p)] = (None, len(events), None)
    events.sort(key=lambda ev: ev["order"])
    hints.sort(key=lambda h: h[0])
    return events, hints, shas, git_runs


# --- shell commands that may have touched the repository ---------------------------------

SEGMENT_RE = re.compile(r"&&|\|\||[;\n|&]")
CD_RE = re.compile(r"^[\s({]*(?:builtin\s+)?(cd|chdir|pushd|popd|Set-Location|sl|Push-Location"
                   r"|Pop-Location)(?:\s+(.*?))?[\s)}]*$")
_PATH_CHARS = r"""[^\s'"`;|&<>(){}$*?,\]]*"""
# An absolute path in a command: /x, ~/x, $HOME/x, C:\x — at the start of a word.
PATH_TOKEN_RE = re.compile(r"""(?:^|(?<=[\s'"=:(`<>,\[]))((?:~|\$HOME|\$\{HOME\})?/"""
                           + _PATH_CHARS + r"|[A-Za-z]:[\\/]" + _PATH_CHARS + ")")
NOWHERE = object()  # a directory the command moved to that cannot be told (`cd "$TMP"`)


VAR_RE = re.compile(r"\$(?:\{(\w+)\}|(\w+))")
ASSIGN_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_]\w*)=(.*?)\s*$")
PREFIX_WORDS = ("do", "then", "else", "sudo", "env", "time", "nohup", "command", "exec", "xargs")
COPY_TOOLS = ("cp", "rsync", "install", "ln", "scp")  # only their last operand is written


def expand(word, env):
    """~, $VAR and ${VAR} from env (HOME and the command's own NAME=value assignments); None
    when the word holds a variable or a command substitution that cannot be told."""
    if word == "~" or word.startswith("~/"):
        word = env["HOME"] + word[1:]
    unknown = []

    def var(m):
        value = env.get(m.group(1) or m.group(2))
        if value is None:
            unknown.append(m.group(0))
        return value or ""
    word = VAR_RE.sub(var, word)
    return None if unknown or "$" in word or "`" in word else word


def shell_words(text):
    try:
        return shlex.split(text, posix=True)
    except ValueError:
        return text.split()


def shell_dir(arg, cur, env):
    """Where `cd <arg>` goes from cur: a path, None (cur unrecorded, so unrecorded) or NOWHERE."""
    words = [w for w in shell_words(arg or "")
             if w not in ("-L", "-P", "-e", "-Path", "-LiteralPath", "--")]
    if not words:
        return env["HOME"]
    word = expand(words[0], env)
    if word is None or word == "-" or any(c in word for c in "*?["):
        return NOWHERE
    if not os.path.isabs(word):
        if cur is None or cur is NOWHERE:
            return cur
        word = os.path.join(cur, word)
    return os.path.normpath(word)


def inside(path, root):
    p = canonical(path)
    return p == root or p.startswith(root.rstrip(os.sep) + os.sep)


HEREDOC_RE = re.compile(r"(?<!<)<<-?(?!<)\s*(['\"]?)(\w+)\1")


def heredoc_split(cmd):
    """(the command without its here-document bodies, the bodies a command runs). The body of
    `cat > f <<EOF` or `tee f <<EOF` is the text written to f, not something that runs."""
    rows, out, bodies, i = cmd.split("\n"), [], [], 0
    while i < len(rows):
        out.append(rows[i])
        i += 1
        for m in HEREDOC_RE.finditer(out[-1]):
            start = i
            while i < len(rows) and rows[i].strip() != m.group(2):
                i += 1
            head = SEGMENT_RE.split(out[-1][:m.start()])[-1].split()
            if not head or head[0] not in ("cat", "tee"):
                bodies.append("\n".join(rows[start:i]))
            i += 1
    return "\n".join(out), bodies


def operands(seg):
    """(command name, the words after it) of one simple command: options, assignments and
    redirection operators left out (a redirection's target stays)."""
    words = [re.sub(r"^\d?[<>]{1,2}", "", w) for w in shell_words(seg)]
    words = [w for w in words if w and not w.startswith("-") and w not in "()[]{}!"]
    while words and (re.match(r"^[A-Za-z_]\w*=", words[0]) or words[0] in PREFIX_WORDS):
        words.pop(0)
    return (words[0], words[1:]) if words else ("", [])


def names_inside(text, env, root):
    """True when text names an absolute path inside the repository (/x, ~/x, $HOME/x, C:/x)."""
    for m in PATH_TOKEN_RE.finditer(text):
        p = expand(m.group(1), env)
        if p and os.path.isabs(p) and inside(p, root):
            return True
    return False


def git_call(seg):
    """(subcommand, [-C directories]) of a simple command that runs git, or None."""
    words = shell_words(seg)
    i = 0
    while i < len(words) and (not words[i].strip("({!") or re.match(r"^[A-Za-z_]\w*=", words[i])
                              or words[i].lstrip("({") in PREFIX_WORDS):
        i += 1
    if i >= len(words) or os.path.basename(words[i].lstrip("({")) != "git":
        return None
    i, dirs = i + 1, []
    while i < len(words) and words[i].startswith("-"):
        if words[i] in GIT_ARG_OPTS:
            if words[i] == "-C" and i + 1 < len(words):
                dirs.append(words[i + 1])
            i += 1
        i += 1
    return (words[i].rstrip(")}") if i < len(words) else ""), dirs


def shell_parts(text, cwd, env):
    """(simple command, the directory it runs in) for each part of a shell command: the tool
    call's cwd, followed through the command's own cd, pushd/popd and NAME=value assignments
    (kept in env); a cd inside ( … ) or $( … ) ends with it, and `git -C <dir>` runs in <dir>.
    The directory is a path, None (the call recorded no cwd) or NOWHERE (cannot be told)."""
    cur, stack, sub = cwd or None, [], []
    for seg in SEGMENT_RE.split(text):
        if not seg.strip():
            continue
        depth = seg.count("(") - seg.count(")")
        sub += [cur] * max(depth, 0)
        m, cd = ASSIGN_RE.match(seg), CD_RE.match(seg)
        if m and " " not in m.group(2).strip("'\""):
            words = shell_words(m.group(2))
            value = expand(words[0], env) if len(words) == 1 else None
            env.pop(m.group(1), None) if value is None else env.__setitem__(m.group(1), value)
        elif cd:
            verb = cd.group(1)
            if verb in ("popd", "Pop-Location"):
                cur = stack.pop() if stack else NOWHERE
            else:
                if verb in ("pushd", "Push-Location"):
                    stack.append(cur)
                cur = shell_dir(cd.group(2), cur, env)
        else:
            here = cur
            for d in (git_call(seg) or ("", []))[1]:
                here = shell_dir(d, here, env)
            yield seg, here
        for _ in range(max(-depth, 0)):
            if sub:
                cur = sub.pop()


def commit_dirs(cmd, cwd):
    """The directories a shell command ran `git commit|merge|pull|cherry-pick|revert` in (each
    a path, None or NOWHERE as shell_parts gives them)."""
    env = {"HOME": os.path.expanduser("~")}
    return [d for seg, d in shell_parts(heredoc_split(cmd)[0], cwd, env)
            if (git_call(seg) or ("",))[0] in GIT_COMMIT_SUBS]


def touches_repo(cmd, cwd, root):
    """True when a shell command may have changed files of the repository at root. Only the
    parts of it that may change files (may_write) count, each in the directory it runs in
    (shell_parts): one that names a path inside the repository — absolute, or relative to the
    directory it runs in — or runs inside it and names no file. A part that runs inside but
    names only paths outside (`rm -rf /tmp/x`, `cat > ~/notes.txt`), a read-only part (`ls`,
    `| head`) and the source of a copy do not count; the body of a here-document a command runs
    (`python3 - <<EOF`) is searched for absolute paths. A call whose transcript entry records no
    cwd counts as inside."""
    env = {"HOME": os.path.expanduser("~")}
    text, bodies = heredoc_split(cmd)
    for seg, cur in shell_parts(HINT_NOISE_RE.sub(" ", text), cwd, env):
        if not may_write(seg):
            continue
        name, words = operands(seg)
        if name in COPY_TOOLS and len(words) > 1:
            words = words[-1:]
        paths = [expand(w, env) for w in words]
        if any(p and (os.path.isabs(p) and inside(p, root) or names_inside(p, env, root))
               for p in paths):
            return True
        if cur is None:
            return True
        if cur is NOWHERE:
            continue
        rel = [p for p in paths if p is not None and not os.path.isabs(p)]
        if inside(cur, root):
            if not paths or rel:
                return True
        elif any(inside(os.path.join(cur, p), root) for p in rel):
            return True
    return any(names_inside(b, env, root) for b in bodies)


def repo_hints(hints, root):
    """The shell hints that may have touched this repository, each command once (at its newest
    run), oldest first; and how many commands were left out as run elsewhere."""
    newest, elsewhere = {}, set()
    for ts, cmd, cwd in hints:
        if touches_repo(cmd, cwd, root):
            newest[cmd] = ts
        else:
            elsewhere.add(cmd)
    kept = sorted(newest.items(), key=lambda h: h[1])
    return [(ts, cmd) for cmd, ts in kept], len(elsewhere - set(newest))


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
    """{"commits": set, "paths": {abs path: {event, ts, commit}}, "rewritten": {old: new},
    "unsynced": {abs path: {stale, entry, commit}}, "pending": [{parent, tree, branch, paths,
    sync}]} merged over the sessions. unsynced: real index entries a commit of this skill could
    not set yet; pending: a commit begun but not confirmed (the run may have been killed)."""
    out = {"commits": set(), "paths": {}, "rewritten": {}, "unsynced": {}, "pending": []}
    for sid in sids:
        data = _read_records(cfg, sid)
        out["commits"].update(c for c in data.get("commits") or [] if isinstance(c, str))
        for k, v in (data.get("rewritten") or {}).items():
            if isinstance(k, str) and isinstance(v, str):
                out["rewritten"][k] = v
        for path, rec in (data.get("paths") or {}).items():
            if isinstance(rec, dict) and isinstance(rec.get("ts"), str):
                old = out["paths"].get(path)
                if old is None or rec["ts"] > old["ts"]:
                    out["paths"][path] = rec
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


def epoch_of(ts):
    """Seconds since the epoch of a transcript timestamp (2026-10-26T10:00:00.123Z), or None."""
    try:
        whole = datetime.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError):
        return None
    frac = re.match(r"\.(\d+)", ts[19:])
    return whole.replace(tzinfo=datetime.timezone.utc).timestamp() + \
        (float("0." + frac.group(1)) if frac else 0.0)


def head_logs(R):
    """{checkout root: the path of its HEAD reflog} for every worktree of the repository."""
    out = {}
    for path, _, gone in worktrees(R):
        if gone:
            continue
        p = git(path, "rev-parse", "--absolute-git-dir", ok=None)
        if p.returncode == 0:
            out[canonical(path)] = os.path.join(text_of(p).strip(), "logs", "HEAD")
    return out


def reflog_commits(R, git_runs):
    """[(sha, end timestamp, named)] of the commits made while one of the session's shell
    commands ran `git commit` (merge, pull, cherry-pick, revert) — between the call and its
    result — in the checkout it ran that in, from that checkout's HEAD reflog: the session's
    `git commit -q` prints no `[branch sha]` line. Only entries that made a new commit count —
    not a fast-forward, reset or branch creation. named: the command holds the commit's subject
    (`git commit -m '<subject>'`); a commit that is not named still has to hold the session's
    edits (collect checks) — someone else may commit in that checkout meanwhile. The reflog keeps
    whole seconds, so an entry from the second the command started counts only when named."""
    logs, spans = None, {}
    for start, end, cmd, cwd in git_runs:
        a, z = epoch_of(start), epoch_of(end)
        if a is None or z is None or z < a:
            continue
        for d in commit_dirs(cmd, cwd):
            if d is NOWHERE:
                continue
            if logs is None:
                logs = sorted(head_logs(R).items(), key=lambda kv: -len(kv[0]))
            where = R.root if d is None else d
            log = next((lg for root, lg in logs if inside(where, root)), None)
            if log and (a, z, end, cmd) not in spans.get(log, []):
                spans.setdefault(log, []).append((a, z, end, cmd))
    out = []
    for log, runs in spans.items():
        try:
            with open(log, "rb") as fh:
                rows = fh.read().decode("utf-8", "replace").split("\n")
        except OSError:
            continue
        for row in rows:
            meta, _, msg = row.partition("\t")
            parts = meta.split()
            if len(parts) < 4 or not parts[-2].isdigit() or not OWN_REFLOG_RE.match(msg):
                continue
            t = int(parts[-2])
            subject = msg.split(": ", 1)[1].strip() if ": " in msg else ""
            for a, z, end, cmd in runs:
                named = bool(subject) and subject[:40] in cmd
                if t <= z and (t >= a or (t == int(a) and named)):
                    out.append((parts[1], end, named))
                    break
    return out


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


def record_own(cfg, sids, shas, paths=None, rewritten=None, unsynced=None):
    """Remember, for each session, the commits this skill made (so a later run knows that
    unpushed commits with these ids are the session's own), the last session edit each
    committed path got (so a later run replays only the edits made since), commits a rebase
    gave new ids, and real index entries a commit could not set yet."""
    if not shas and not paths and not rewritten and not unsynced:
        return

    def change(data):
        data["commits"] = sorted(set(c for c in data.get("commits") or [] if isinstance(c, str))
                                 | set(shas or []))
        for key, new in (("paths", paths), ("rewritten", rewritten), ("unsynced", unsynced)):
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


def hunk_sides(hunk):
    """(old lines, new lines) of a structuredPatch hunk."""
    rows = hunk_rows(hunk)
    return [ln for t, ln in rows if t != "+"], [ln for t, ln in rows if t != "-"]


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


def apply_hunks(text, hunks, reverse):
    """Apply a structuredPatch forward, or undo it (reverse). A hunk is matched exactly with
    its full context first, then with up to MAX_FUZZ context lines dropped at each end (a
    neighbouring edit changed them); the changed lines themselves always match exactly — when
    they do not, raises NotFound. A match away from the hunk's own line, or with context
    dropped, counts only when it is the one place in the file the lines occur: a shorter
    pattern also matches lookalike code elsewhere, and the nearest of several is a guess.
    Whether a neighbouring edit really collides with the session's is left to merge-file."""
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


def reverse_replay(text, events):
    """The file without these session edits: undo every event, last first."""
    for ev in reversed(events):
        if ev["kind"] == "patch":
            text = apply_hunks(text, ev["hunks"], reverse=True)
        elif ev["kind"] == "replace":
            text = undo_replace(text, ev)
        elif ev["kind"] == "rewrite":
            raise Ambiguous("rewrite")
        else:
            raise Ambiguous("whole-write")
    return text


def forward(text, events):
    """Replay events forward from `text`."""
    for ev in events:
        if ev["kind"] == "create":
            text = norm(ev["content"])
        elif ev["kind"] == "rewrite":
            raise Ambiguous("rewrite")
        elif ev["kind"] == "patch":
            text = apply_hunks(text, ev["hunks"], reverse=False)
        elif ev["kind"] == "replace":
            text = do_replace(text, ev)
    return text


def contains(ev, text):
    """True when `text` (LF) holds the result of this session edit: its new lines are there."""
    if text is None:
        return False
    try:
        if ev["kind"] == "patch":
            apply_hunks(text, ev["hunks"], reverse=True)
        elif ev["kind"] == "replace":
            return norm(ev["new"]) in text
        elif ev["kind"] in ("create", "rewrite"):
            return norm(ev["content"]) == text
    except NotFound:
        return False
    except Ambiguous:
        return True  # there, more than once
    return True


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


def attribute(events, w_raw, h_raw, declared, run_dir, committed=False, mode_changed=False):
    """What to commit for one path. `events` are the session's edits not committed yet;
    `committed` says earlier ones were (by this skill or the session's own `git commit`), so
    HEAD already holds them; `mode_changed`: a declared file's exec bit differs from HEAD's.
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
    if any(ev["kind"] == "opaque" for ev in events):
        return whole("notebook")
    if is_binary(w_raw) or is_binary(h_raw):
        return whole("binary")
    if w_raw is None:
        if h_raw is None:
            return {"status": "unchanged"}
        return {"status": "skipped", "reason": "deleted"}
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
    first = next((i for i, ev in enumerate(events) if ev["kind"] == "create"), None)
    if first is not None:
        events = events[:first] + [dict(ev, kind="create") if ev["kind"] == "rewrite" else ev
                                   for ev in events[first:]]
    anchors = [i for i, ev in enumerate(events) if ev["kind"] == "create"]
    if anchors:
        # The session wrote the whole file: its content from there on is the session's own.
        k = anchors[-1]
        try:
            s = forward(norm(events[k]["content"]), events[k + 1:])
        except NotFound:
            return {"status": "skipped", "reason": "rewritten"}
        except Ambiguous as e:
            return {"status": "skipped", "reason": "ambiguous", "detail": why(e)}
        if h is None or s == w:
            return outcome(s)
        c, where = common_changes(s, w, h)
        if c is None:
            return {"status": "skipped", "reason": "tangled", "detail": where}
        return outcome(c)
    if any(ev["kind"] == "rewrite" for ev in events):
        return {"status": "skipped", "reason": "rewrite-unknown"}
    try:
        b = reverse_replay(w, events)
    except NotFound as e:
        neighbour = len(e.args) > 1 and e.args[1] == "neighbour"
        return {"status": "skipped", "reason": "tangled" if neighbour else "notfound",
                "detail": why(e)}
    except Ambiguous as e:
        # Undo is impossible (deleted text, repeated text, a whole write): replay forward from
        # HEAD instead.
        if h is None:
            return {"status": "skipped", "reason": "ambiguous", "detail": why(e)}
        try:
            return outcome(forward(h, events))
        except (NotFound, Ambiguous):
            return {"status": "skipped", "reason": "ambiguous", "detail": why(e)}
    if h is None:
        if b == "":
            return outcome(w)
        return {"status": "skipped", "reason": "untracked-before"}
    if w == h:
        return {"status": "unchanged"}
    if b == h:
        return outcome(w)
    merged, conflicts = merge_file(run_dir, w, b, h)
    if conflicts:
        detail = ", ".join(f"{a}–{z}" for a, z in conflicts)
        return {"status": "skipped", "reason": "tangled", "detail": detail, "lines": conflicts}
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
    sym = text_of(git(R.root, "symbolic-ref", "-q", "refs/remotes/origin/HEAD", ok=(0, 1))).strip()
    if sym.startswith("refs/remotes/origin/"):
        return sym[len("refs/remotes/origin/"):]
    for name in ("main", "master"):
        if rev(R.root, "refs/heads/" + name) or rev(R.root, "refs/remotes/origin/" + name):
            return name
    return None


def has_origin(R):
    return "origin" in text_of(git(R.root, "remote")).split()


def upstream_of(R, branch):
    p = git(R.root, "rev-parse", "-q", "--verify", "--symbolic-full-name", branch + "@{upstream}",
            ok=None)
    return p.stdout.decode().strip() if p.returncode == 0 else None


def own_upstream(R, branch):
    """The branch's upstream when it is origin/<branch> itself — the only one it is pushed to
    (a branch made from origin/main tracks origin/main, which is not its own place)."""
    up = upstream_of(R, branch)
    return up if up == "refs/remotes/origin/" + branch else None


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


def tracked_clean(path):
    return not git(path, "status", "--porcelain", "--untracked-files=no").stdout.strip()


def noff_style(R, ref):
    """True when the branch's history is mostly merge commits (the repository merges with
    --no-ff)."""
    parents = text_of(git(R.root, "log", "--first-parent", "-20", "--format=%P", ref, "--",
                          ok=None)).split("\n")
    parents = [p for p in parents if p.strip()]
    merges = sum(1 for p in parents if len(p.split()) > 1)
    return merges >= 2 and merges * 2 >= len(parents)


def subjects(R, shas, limit=5):
    out = []
    for sha in shas[:limit]:
        s = text_of(git(R.root, "log", "-1", "--format=%h %s", sha, "--")).strip()
        out.append(oneline(s, 70))
    more = len(shas) - limit
    return "; ".join(out) + (T(" … и ещё {n}", " … and {n} more", n=more) if more > 0 else "")


def foreign_commits(R, include, exclude, own, merged=()):
    """Commits reachable from include and from none of exclude that are not this session's.
    A merge commit whose other parents are all in one of `merged` (main, origin/main: already
    published or about to be) brings nothing new — the session's own `git merge` / `git pull`,
    which prints no `[branch sha]` line — and does not count."""
    excludes = [exclude] if isinstance(exclude, str) else list(exclude or [])
    refs = [r for r in merged if r and rev(R.root, r)]
    data = text_of(git(R.root, "rev-list", "--parents", include,
                       *["^" + e for e in excludes if e], "--"))
    out = []
    for row in data.split("\n"):
        shas = row.split()
        if not shas or shas[0] in own:
            continue
        if len(shas) > 2 and refs and all(any(is_ancestor(R.root, p, r) for r in refs)
                                          for p in shas[2:]):
            continue
        out.append(shas[0])
    return out


NAME_CHARS = r"[\w.@+-]"


NAME_RUN_RE = re.compile(NAME_CHARS + "+")
NAME_ONLY_RE = re.compile("^" + NAME_CHARS + "+$")


def name_words(cmds):
    """The words of the commands as named_in matches them: runs of name characters."""
    return {w for c in cmds for w in NAME_RUN_RE.findall(c)}


def named_in(cmds, rel, words=None):
    """True when a shell command names this repository path or its file name. A path names
    itself only where its file name does, so the file name is what is looked for: as a whole
    word (`words`, from name_words) when it is made of name characters only."""
    base = os.path.basename(rel) or rel
    if words is not None and NAME_ONLY_RE.match(base):
        return base in words
    rx = re.compile(r"(?<!" + NAME_CHARS + r")" + re.escape(base) + r"(?!" + NAME_CHARS + ")")
    return any(rx.search(c) for c in cmds)


def collect(args):
    _NAMES.clear()
    cfg = config_dir()
    sids = args.session or ([os.environ["CLAUDE_CODE_SESSION_ID"]]
                            if os.environ.get("CLAUDE_CODE_SESSION_ID") else [])
    if not sids:
        raise Fail("no session id: pass --session or run inside Claude Code "
                   "(CLAUDE_CODE_SESSION_ID)")
    files = []
    for sid in sids:
        files += transcript_files(cfg, sid)
    events, all_hints, raw_shas, git_runs = read_transcripts(files)
    # Repositories in temp places (an agent's scratchpad clone) are throwaway work, not the
    # user's project — unless this run is in a temp place itself.
    here_temp = is_temp(canonical(os.getcwd()))
    try:
        R = Repo(os.path.abspath(args.repo))
    except Fail:
        if args.repo != ".":
            raise
        # Started outside a repository (a session in ~): take the one this session edited.
        roots = sorted({canonical(r) for r in map(repo_root_of, (ev["path"] for ev in events))
                        if r})
        temps = [r for r in roots if is_temp(r) and not here_temp]
        roots = [r for r in roots if r not in temps]
        if len(roots) != 1:
            clones = f"{len(temps)} temporary clone{'s' if len(temps) != 1 else ''} (left alone)"
            raise Fail("the current directory is not a git repository" + (
                "; this session edited files in: " + ", ".join(roots) +
                " — run plan with --repo <path> for each" + (f"; also {clones}" if temps else "")
                if roots else ", and this session edited files only in " + clones if temps else
                ", and this session edited no file in any repository"))
        R = Repo(roots[0])
        print(T("Репозиторий: {r} — текущая папка не в git, а сессия правила только его",
                "Repository: {r} — the current directory is not in git, and this session "
                "edited only this one", r=R.root))
    run_dir = os.path.abspath(args.out) if args.out else new_run_dir(cfg, R.root)
    private_dir(run_dir)
    hints, hints_elsewhere = repo_hints(all_hints, R.root)

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

    # What the session already committed: by this skill (recorded per path), or by its own
    # `git commit` in Bash — named by its `[branch sha]` output line, or, for a quiet one, found
    # in the reflog at the time the command ran. HEAD holds those edits, so only later ones
    # are replayed.
    rec = load_records(cfg, sids)
    if rec["pending"]:
        # A run killed right after `git commit` (or while its post-commit hook ran) recorded
        # nothing more: the commit that holds exactly the planned tree on the planned parent
        # is that run's.
        for pend in rec["pending"]:
            sha = landed(R, pend)
            if sha:
                settle(cfg, sids, pend, sha)
            else:
                forget(cfg, sids, pending=pend)
        rec = load_records(cfg, sids)
    own = set(rec["commits"])

    def in_head(sha):
        """sha is in HEAD's history, itself or as the commit a rebase by this skill made of it."""
        seen = set()
        while sha and sha not in seen and R.head:
            seen.add(sha)
            if rev(R.root, sha) and is_ancestor(R.root, sha, R.head):
                return True
            sha = rec["rewritten"].get(sha)
        return False

    def changed_in(sha):
        names = git(R.root, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "--root",
                    sha, ok=None).stdout
        return [os.fsdecode(n) for n in names.split(b"\0") if n]

    def blob_text(sha, rel):
        p = git(R.root, "cat-file", "--filters", f"{sha}:{rel}", ok=None)
        return norm(decode(p.stdout)) if p.returncode == 0 else None

    def before(rel, ts):
        return [ev for ev in by_path.get(rel, []) if ev["order"][0] <= ts]

    # The session's own `git commit` in Bash: the commit its output names is the session's. One
    # found only in the reflog while the command ran must also hold the session's last edit
    # before then of a file it changes — or carry the subject the command gave — else it is
    # someone's who committed in that checkout meanwhile, and stays foreign.
    bash_cut = {}
    found = [(s, ts, True) for s, ts in raw_shas] + reflog_commits(R, git_runs)
    for s, ts, named in found:
        full = rev(R.root, s)
        if not full:
            continue
        names = changed_in(full)
        if not named and not any(before(n, ts) and contains(before(n, ts)[-1], blob_text(full, n))
                                 for n in names):
            continue
        own.add(full)
        if in_head(full):
            for n in names:
                if n not in bash_cut or ts > bash_cut[n][0]:
                    bash_cut[n] = (ts, full)

    def cut_of(rel, evs):
        cut = 0
        r = rec["paths"].get(os.path.join(R.root, rel))
        if r and R.head and in_head(r.get("commit")):
            ids = [ev.get("id") for ev in evs]
            if r.get("event") in ids:
                cut = ids.index(r["event"]) + 1
            else:
                cut = sum(1 for ev in evs if ev["order"][0] <= r["ts"])
        if rel in bash_cut:
            # Edits up to the end of the command that committed, less the last ones the commit
            # does not hold: a parallel agent's edits made after the commit while the command
            # still ran (`git commit && npm test`) are not committed yet.
            ts, sha = bash_cut[rel]
            k = sum(1 for ev in evs if ev["order"][0] <= ts)
            if k > cut:
                text = blob_text(sha, rel)
                while k > cut and not contains(evs[k - 1], text):
                    k -= 1
            cut = max(cut, k)
        return cut

    head_map = R.ls_tree(by_path.keys())
    staged = R.staged()
    candidates = sorted(by_path)
    untracked = [p for p in candidates if p not in head_map]
    ignored = R.ignored(untracked)
    filemode = R.filemode()
    R.plain_paths([p for p in candidates if p in head_map])
    items = []
    for rel in candidates:
        evs = by_path[rel]
        absp = os.path.join(R.root, rel)
        cut = cut_of(rel, evs)
        item = {"path": rel, "events": len(evs) - cut, "declared": rel in declared}
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
            res = attribute(evs[cut:], w_raw, h_raw, rel in declared, run_dir, committed=cut > 0,
                            mode_changed=chmod)
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
            elif not exists and in_head and owner_deleted:
                items.append({"path": meta, "events": 0, "declared": False, "status": "commit",
                              "how": "delete", "reason": "companion", "companion_of": owner,
                              "w_hash": "absent", "created": False, "deleted": True})
    head_map.update(R.ls_tree([i["path"] for i in items if i["path"] not in head_map]))

    # The index entries this skill's own earlier commit should have set but could not (the
    # index was locked, the run was killed): recorded before the sync was tried, so only those
    # count — a stage someone made of the same old content on purpose is theirs. Where the
    # index still holds the recorded stale entry and HEAD the commit's, apply puts them right;
    # a record that no longer matches is dropped.
    unsynced, drop = [], []
    prefix = R.root.rstrip(os.sep) + os.sep
    for absp, u in sorted(rec["unsynced"].items()):
        if not absp.startswith(prefix):
            continue
        path = git_rel(R.root, absp)
        sha, seen = u.get("commit"), set()
        while sha and sha not in seen and not (rev(R.root, sha) and R.head and
                                                is_ancestor(R.root, sha, R.head)):
            seen.add(sha)
            sha = rec["rewritten"].get(sha)
        stale = tuple(u["stale"]) if u.get("stale") else "absent"
        mine = tuple(u["entry"]) if u.get("entry") else None
        if not sha or sha in seen or R.ls_tree([path]).get(path) != mine or \
                index_entries(R, [path]).get(path, "absent") != stale:
            drop.append(absp)
            continue
        dirty = git(R.root, "--no-optional-locks", "diff", "--quiet", "HEAD", "--", path,
                    ok=(0, 1), literal=True).returncode == 1
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
    if added and text_of(git(R.root, "config", "--bool", "core.ignorecase",
                             ok=(0, 1))).strip() == "true":
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

    # Foreign work: what stays untouched.
    ours_whole = {it["path"] for it in items if it.get("status") == "commit"}
    ours_any = ours_whole | {it["path"] for it in items if it.get("status") == "partial"}
    left_whole = ours_whole - set(mode_left)  # nothing of these stays after the commit
    unsynced_dirty = {u["path"] for u in unsynced if u["dirty"]}

    def stays_modified(xy, path):
        if path in unsynced_paths and path not in ours_any:
            return path in unsynced_dirty
        return path not in left_whole and xy[1] != " "
    foreign = {"modified": 0, "untracked": 0, "staged": 0}
    for xy, path in status_rows:
        if xy == "??":
            foreign["untracked"] += path not in ours_any
        elif xy != "!!":
            foreign["modified"] += stays_modified(xy, path)
            if xy[0] not in " ?" and path not in ours_any and path not in unsynced_paths:
                foreign["staged"] += 1
    partial = [it["path"] for it in items if it.get("status") == "partial"]
    # Tracked changes left after our commit block a rebase or merge in this checkout.
    dirty_after = bool(partial) or any(
        xy not in ("??", "!!") and (stays_modified(xy, path) or
                                    (xy[0] not in " ?" and path not in ours_any and
                                     path not in unsynced_paths))
        for xy, path in status_rows)

    # Files left out of the commit that the session's own shell commands name: the model cannot
    # see the shell commands of subagents and workflow agents, so this is its only pointer.
    cmds = [c for _, c in hints]
    words = name_words(cmds)
    left = [path for xy, path in status_rows if xy != "!!" and path not in left_whole]
    left += [it["path"] for it in items if it.get("status") == "skipped"]
    bash_named = []
    for path in left:
        if path not in bash_named and named_in(cmds, path, words):
            bash_named.append(path)
    # Files a declared folder holds that went into the commit after all (a .meta companion)
    # are not "not taken".
    untracked_decl = [p for p in untracked_decl if p not in ours_any]

    plan = {
        "version": 1, "repo": R.root, "config": cfg, "sessions": sids, "lang": LANG,
        "head": R.head, "branch": R.branch, "worktree": R.worktree,
        "files": items, "other_repos": {k: sorted(v) for k, v in sorted(other.items())},
        "temp_clones": len(temp_clones),
        "outside": len(outside), "bash_hints": [c for _, c in hints[-MAX_HINTS:]],
        "bash_hints_total": len(hints), "bash_hints_elsewhere": hints_elsewhere,
        "bash_named": bash_named, "mode_left": mode_left, "unsynced": unsynced,
        "declare_not_found": missing_decl, "declare_refused": refused,
        "declare_untracked": untracked_decl, "foreign": foreign, "dirty_after": dirty_after,
        "own_commits": sorted(own), "no_merge": bool(args.no_merge), "case_clash": case_clash,
        "no_push": bool(args.no_push), "run_dir": run_dir, "blocked": blocked,
    }
    R.close()
    return R, plan


def preview(R, plan, has_files, own):
    """Status lines for merge and push, predicted from local refs (no fetch); they follow the
    same decisions as Publisher. -> (lines, stop reason or None, something to publish).
    A stop that comes after the commit (there are files to commit) is a forecast — apply
    commits and stops there — so it reads "will stop after the commit" and is not a verdict."""
    lines = []
    branch = plan["branch"]
    default = default_branch(R) or branch
    remote = has_origin(R)
    no_push = plan["no_push"]
    plan["default"] = default
    main_label = T("В {b}", "Into {b}", b=default)

    def push_line(target, new=False):
        if no_push:
            return T("нет — по аргументу", "no — as asked")
        if not remote:
            return T("нет — нет remote", "no — no remote")
        return T("да — origin/{t}", "yes — origin/{t}", t=target) + (
            T(" (новая ветка на origin)", " (new branch on origin)") if new else "")

    def stop_at(lab, text, after=None):
        if has_files:
            lines.append((lab, T("нет — остановлюсь после коммита: ",
                                 "no — will stop after the commit: ") + text))
        else:
            lines.append((lab, T("нет — стоп: ", "no — stop: ") + text))
        if after:
            lines.append(after)
        return lines, (None if has_files else text), has_files

    def not_ours(where, shas):
        return T("в {w} чужие незапушенные коммиты: {l} — не публикую чужое; запушь или убери "
                 "их сам", "{w} has unpushed commits that are not this session's: {l} — not "
                 "publishing them; push or drop them yourself", w=where, l=subjects(R, shas))

    if R.head is None:
        if not has_files:
            lines.append(("Push", T("нет — нечего публиковать", "no — nothing to publish")))
            return lines, None, False
        if branch != default:
            lines.append((main_label, T("нет — новая ветка без истории",
                                        "no — a new branch with no history")))
        else:
            lines.append((main_label, T("нет — уже на {b}", "no — already on {b}", b=default)))
        lines.append(("Push", push_line(branch, new=True)))
        return lines, None, True

    if branch == default:
        lines.append((main_label, T("нет — уже на {b}", "no — already on {b}", b=default)))
        up = "refs/remotes/origin/" + branch
        if no_push or not remote:
            lines.append(("Push", push_line(branch)))
            return lines, None, has_files
        if not rev(R.root, up):
            foreign = foreign_commits(R, "HEAD", None, own)
            if foreign:
                return stop_at("Push", not_ours(branch, foreign))
            lines.append(("Push", push_line(branch, new=True)))
            return lines, None, True
        unpushed = rev_list(R.root, "HEAD", up)
        if not has_files and not unpushed:
            lines.append(("Push", T("нет — origin/{b} уже содержит всё",
                                    "no — origin/{b} already has everything", b=branch)))
            return lines, None, False
        foreign = foreign_commits(R, "HEAD", up, own, merged=(up,))
        behind = len(rev_list(R.root, up, "HEAD"))
        if foreign:
            return stop_at("Push", not_ours(branch, foreign))
        if behind and plan["dirty_after"]:
            return stop_at("Push", T(
                "origin/{b} ушёл вперёд на {n}, а в дереве есть чужие незакоммиченные правки — "
                "rebase не делаю; закоммить или убери их и запусти /commit снова",
                "origin/{b} is {n} ahead and the tree has uncommitted changes that are not this "
                "session's — no rebase; commit or remove them and run /commit again",
                b=branch, n=n_commits(behind)))
        text = push_line(branch)
        if behind:
            text += T(" (сначала rebase на origin/{b}: +{n})", " (first rebase onto origin/{b}: "
                      "+{n})", b=branch, n=behind)
        lines.append(("Push", text))
        return lines, None, True

    up_main = "refs/remotes/origin/" + default
    have_up = bool(rev(R.root, up_main))
    # On another branch, not merging: the branch goes to origin/<branch>.
    if plan["no_merge"]:
        lines.append((main_label, T("нет — по аргументу", "no — as asked")))
        if no_push or not remote:
            lines.append(("Push", push_line(branch)))
            return lines, None, has_files
        up = own_upstream(R, branch)
        base = up or (up_main if have_up else None)
        foreign = foreign_commits(R, "HEAD", base, own, merged=(up_main,))
        if foreign:
            return stop_at("Push", not_ours(branch, foreign))
        if up:
            behind = len(rev_list(R.root, up, "HEAD"))
            if behind:
                return stop_at("Push", T(
                    "в origin/{b} {n}, которых нет в ветке — сначала влей origin/{b} в ветку "
                    "(git pull)", "origin/{b} has {n} the branch lacks — merge or pull "
                    "origin/{b} into the branch first", b=branch, n=n_commits(behind)))
            if not has_files and not rev_list(R.root, "HEAD", up):
                lines.append(("Push", T("нет — origin/{b} уже содержит всё",
                                        "no — origin/{b} already has everything", b=branch)))
                return lines, None, False
            lines.append(("Push", push_line(branch)))
            return lines, None, True
        lines.append(("Push", push_line(branch, new=True)))
        return lines, None, True

    # On another branch: into main, then push main.
    main_ref = "refs/heads/" + default
    have_main = bool(rev(R.root, main_ref))
    if not have_main and not have_up:
        return stop_at(main_label, T("нет ветки {b} — влей вручную или запусти с no-merge",
                                     "no branch {b} — merge by hand or run with no-merge",
                                     b=default))
    after_stop = ("Push", T("нет — после стопа", "no — stopped before"))
    excl = [r for r, ok in ((main_ref, have_main), (up_main, have_up)) if ok]
    notes = []
    diverged = False
    if have_main and have_up:
        main_foreign = foreign_commits(R, main_ref, up_main, own, merged=(up_main,))
        if main_foreign:
            return stop_at(main_label, main_foreign_text(R, default, main_foreign), after_stop)
        behind = len(rev_list(R.root, up_main, main_ref))
        ahead = rev_list(R.root, main_ref, up_main)
        if behind and ahead:
            if not is_ancestor(R.root, main_ref, "HEAD"):
                return stop_at(main_label, T(
                    "локальный {b} разошёлся с origin/{b} — сведи их вручную",
                    "local {b} and origin/{b} diverged — reconcile them by hand", b=default),
                    after_stop)
            diverged = True
        elif behind:
            notes.append(T("сначала {b} до origin/{b}: +{n}",
                           "first {b} to origin/{b}: +{n}", b=default, n=behind))
    mine = rev_list(R.root, "HEAD", excl)
    unpushed_main = have_main and (not have_up or bool(rev_list(R.root, main_ref, up_main)))
    bup = own_upstream(R, branch) if remote and not no_push else None
    if not has_files and not mine and not diverged:
        lines.append((main_label, T("нет — ветка {br} уже в {b}",
                                    "no — branch {br} is already in {b}", br=branch, b=default)))
        # The branch's own upstream still lacks what main got (an earlier no-push run).
        branch_too = bool(bup) and bool(rev_list(R.root, "HEAD", bup)) and \
            not rev_list(R.root, bup, "HEAD")
        if unpushed_main and remote and not no_push:
            lines.append(("Push", push_line(default) + (f", origin/{branch}" if branch_too
                                                        else "")))
            return lines, None, True
        if branch_too:
            lines.append(("Push", push_line(branch)))
            return lines, None, True
        lines.append(("Push", push_line(default) if no_push or not remote else
                      T("нет — нечего публиковать", "no — nothing to publish")))
        return lines, None, False
    foreign = foreign_commits(R, "HEAD", excl, own, merged=excl)
    if foreign:
        return stop_at(main_label, branch_foreign(R, branch, default, foreign), after_stop)
    lacking = len({c for r in excl for c in rev_list(R.root, r, "HEAD")})
    if lacking and plan["dirty_after"]:
        return stop_at(main_label, T(
            "в {b} {n} новых, а в дереве ветки чужие незакоммиченные правки — не вливаю {b} в "
            "ветку; закоммить или убери их и запусти /commit снова", "{b} has {n} new and this "
            "tree has uncommitted changes that are not this session's — not merging {b} into "
            "the branch; commit or remove them and run /commit again", b=default,
            n=n_commits(lacking)), after_stop)
    if diverged:
        notes.append(T("сначала origin/{b} в ветку (merge): +{n}",
                       "first origin/{b} into the branch (merge): +{n}", b=default, n=lacking))
    elif lacking:
        notes.append(T("сначала {b} в ветку: +{n}", "first {b} into the branch: +{n}",
                       b=default, n=lacking))
    if not have_up and remote and not no_push:
        # origin has no main yet: everything main holds would be published.
        history = foreign_commits(R, main_ref if have_main else "HEAD", None, own)
        if history:
            lines.append((main_label, T("да — ветка {br} → {b}", "yes — branch {br} → {b}",
                                        br=branch, b=default)))
            return stop_at("Push", not_ours(default, history))
    n = len(mine) + (1 if has_files else 0) + (1 if diverged else 0)  # + the merge commit
    if not have_main:
        how = T("создам {b} на ветке", "create {b} at the branch", b=default)
    elif noff_style(R, main_ref) and checkout_of(R, default)[0]:
        how = T("merge --no-ff в {b}", "merge --no-ff into {b}", b=default)
    else:
        how = T("fast-forward {b}", "fast-forward {b}", b=default)
    pre = f" ({'; '.join(notes)})" if notes else ""
    lines.append((main_label, T("да — ветка {br} → {how} ({n}){pre}",
                                "yes — branch {br} → {how} ({n}){pre}",
                                br=branch, how=how, n=n_commits(n), pre=pre)))
    text = push_line(default)
    if bup:
        text += f", origin/{branch}"
    lines.append(("Push", text))
    return lines, None, True


def main_foreign_text(R, d, shas):
    return T("в локальном {d} чужие незапушенные коммиты: {l} — не трогаю {d}; запушь или "
             "убери их сам, или /commit no-merge — запушить только ветку",
             "local {d} has unpushed commits that are not this session's: {l} — leaving {d} "
             "alone; push or drop them yourself, or /commit no-merge to push the branch alone",
             d=d, l=subjects(R, shas))


def branch_foreign(R, b, d, shas):
    """The stop for commits on the branch that are not this session's, with the way out:
    already on origin/<branch> — the branch alone can be pushed; else they are someone's local
    work, to merge or drop by hand."""
    up = "refs/remotes/origin/" + b
    on_origin = rev(R.root, up) and all(is_ancestor(R.root, c, up) for c in shas)
    way = T("запушить только ветку: /commit no-merge", "to push the branch alone: /commit "
            "no-merge") if on_origin else T("влей их в {d} сам или убери с ветки",
                                            "merge them into {d} yourself or drop them from the "
                                            "branch", d=d)
    return T("на ветке {b} чужие коммиты: {l} — их в {d} не вливаю; {w}",
             "branch {b} has commits that are not this session's: {l} — not merging them into "
             "{d}; {w}", b=b, d=d, l=subjects(R, shas), w=way)


REASONS = {
    "ignored": ("в .gitignore", "ignored by git"),
    "directory": ("это папка — передай файлы", "a directory — declare its files"),
    "notfound": ("мою правку переписали после меня — не нахожу её в файле",
                 "a later edit rewrote this session's change — it is not in the file"),
    "rewritten": ("правку сессии не найти — файл менялся после неё (например, через Bash); "
                  "передай --declare, если весь файл твой",
                  "this session's edit cannot be found — the file changed after it (e.g. "
                  "through Bash); --declare it if the whole file is yours"),
    "ambiguous": ("не могу однозначно найти свою правку", "cannot locate this session's "
                  "change for sure"),
    "tangled": ("мои и чужие правки сцеплены", "this session's and other edits are entangled"),
    "rewrite-unknown": ("агент переписал файл целиком (Write), а что было в нём до этого — "
                        "неизвестно; передай --declare, если весь файл твой",
                        "an agent rewrote the whole file (Write) and what it held before is "
                        "unknown; --declare it if the whole file is yours"),
    "deleted": ("удалён после правок сессии — передай --declare, если удаляла сессия",
                "deleted after this session's edits — pass --declare if the session deleted it"),
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
    text = T(ru, en)
    d = it.get("detail")
    if isinstance(d, str) and RANGE_RE.match(d):
        text += T(" (строки {d})", " (lines {d})", d=d)
    elif d in DETAILS:
        text += " — " + T(*DETAILS[d])
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
    R, plan = collect(args)
    items = plan["files"]
    commit = [i for i in items if i.get("status") in ("commit", "partial")]
    skipped = [i for i in items if i.get("status") == "skipped"]
    own = set(plan["own_commits"])
    lines = []
    stop = None
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
                 "если это переименование, передай --declare и старое имя (уйдёт как удаление)",
                 "the commit would hold both {a} and {b}, which differ only in letter case; for "
                 "a rename, --declare the old name too (it is committed as a deletion)", a=a, b=b)
    if commit:
        parts = []
        part = [i["path"] for i in commit if i["status"] == "partial"]
        if part:
            parts.append(T("{n} частично: {p} — чужие правки останутся в рабочем дереве",
                           "{n} partly: {p} — other edits stay in the working tree",
                           n=len(part), p=", ".join(part[:3]) + (" …" if len(part) > 3 else "")))
        unverified = [i["path"] for i in commit if i.get("reason") in ("declared", "notebook",
                                                                       "binary")]
        if unverified:
            parts.append(T("{n} целиком без проверки: {p}", "{n} whole, unverified: {p}",
                           n=len(unverified), p=", ".join(unverified[:3]) +
                           (" …" if len(unverified) > 3 else "")))
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
        more, pstop, work = preview(R, plan, bool(commit), own)
        lines += more
        if pstop:
            verdict = "stop"
            stop = pstop
        elif not commit and not work and not plan["unsynced"]:
            verdict = "nothing"
        else:
            verdict = "go"
    else:
        plan["default"] = default_branch(R) or R.branch
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
        print("named by this session's shell commands but not committed whole (declare the "
              "ones this session changed): " + some("bash_named"))
    if plan["bash_hints"]:
        shown = plan["bash_hints"][-SHOW_HINTS:]
        total = plan["bash_hints_total"]
        print(f"bash_hints ({total} run in this repository or naming a path in it"
              + (f", newest {len(shown)} shown, {len(plan['bash_hints'])} in plan.json"
                 if len(shown) < total else "")
              + (f"; {plan['bash_hints_elsewhere']} run elsewhere left out"
                 if plan.get("bash_hints_elsewhere") else "")
              + "; files they changed are not seen unless declared):")
        for h in shown:
            print("  $ " + oneline(h, 160))
    elif plan.get("bash_hints_elsewhere"):
        print(f"bash_hints: none run in this repository or naming a path in it "
              f"({plan['bash_hints_elsewhere']} run elsewhere left out)")
    print(f"plan: {os.path.join(plan['run_dir'], 'plan.json')}")
    return 1 if verdict == "stop" else 0


# --- apply -------------------------------------------------------------------------------

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
    for it in ours:
        path = it["path"]
        if it["how"] == "delete":
            git(R.root, "update-index", "--force-remove", "--", path, index=index)
            continue
        if it["how"] == "computed":
            with open(it["blob"], "rb") as fh:
                data = fh.read()
        else:
            data, _ = read_worktree(os.path.join(R.root, path))
        if it["mode"] == "120000":
            sha = text_of(git(R.root, "hash-object", "-w", "--stdin", stdin=data)).strip()
        else:
            sha = text_of(git(R.root, "hash-object", "-w", "--stdin", "--path=" + path,
                              stdin=data)).strip()
        git(R.root, "update-index", "--add", "--cacheinfo", f"{it['mode']},{sha},{path}",
            index=index)
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
    pend = {"parent": R.head, "tree": expected, "branch": want_ref, "paths": path_recs,
            "sync": sync_record(to_sync(now), now, planned)}
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
        # back off the branch it landed on (only if it is still the tip) and say so.
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
    for it in ours:
        if it["status"] != "commit":
            continue
        data, _ = read_worktree(os.path.join(R.root, it["path"]))
        entry = entries.get(it["path"])
        if data is None or entry is None:
            if (data is None) != (entry is None):
                dirty.append(it["path"])
            continue
        args = ["hash-object", "--stdin"] + ([] if entry[0] == "120000" else
                                             ["--path=" + it["path"]])
        if text_of(git(R.root, *args, stdin=data)).strip() != entry[1]:
            dirty.append(it["path"])
    if dirty:
        problems.append(T("после коммита отличаются от него: {p}",
                          "still differ from the commit: {p}", p=", ".join(dirty[:5])))
    if problems:
        steps.say(T("Проверка", "Check"), T("расхождение — ", "mismatch — ") + "; ".join(problems),
                  "verify", "stop")
        return head, False, trusted
    return head, True, True


def ignored_in_the_way(R, added):
    """The git-ignored files a checkout of these added paths would overwrite: at the path itself
    or at one of its folders (an ignored file `notes` where `notes/` comes), spelt in any letter
    case the filesystem opens it under (`Secrets.env` for `secrets.env`)."""
    places = set()
    for a in added:
        parts = a.split("/")
        places.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
    _NAMES.clear()
    found = {}
    for p in sorted(places):
        absp = os.path.join(R.root, p)
        if os.path.lexists(absp) and not (os.path.isdir(absp) and p not in added):
            found[p] = rel_of(R.root, canonical(absp))  # its spelling on disk
    if not found:
        return []
    tracked = {os.fsdecode(x) for x in git(R.root, "ls-files", "-z").stdout.split(b"\0") if x}
    tracked_dirs = {t[:i] for t in tracked for i in range(len(t)) if t[i] == "/"}
    loose = sorted({d for d in found.values() if d not in tracked and d not in tracked_dirs})
    return sorted(R.ignored(loose))


class Publisher:
    """Integrate into the default branch and push, after the commit. Every method returns True
    to go on, False when it stopped (and said why)."""

    def __init__(self, R, plan, own, steps, no_merge, no_push):
        self.R, self.plan, self.own, self.steps = R, plan, own, steps
        self.no_merge, self.no_push = no_merge, no_push
        self.branch = plan["branch"]
        self.default = plan.get("default") or default_branch(R) or self.branch
        self.remote = has_origin(R)
        self.created = []
        self.rewritten = {}
        self.rebased = False
        self.main_label = T("В {b}", "Into {b}", b=self.default)

    def stop(self, lab, text, step):
        self.steps.say(lab, T("стоп — ", "stop — ") + text, step, "stop")
        return False

    def mine(self, shas, rewritten=None):
        """Count these commits as the session's — and record them at once: a run killed
        later (Esc, the Bash timeout during a slow push) must not leave them looking foreign
        to the next one."""
        self.own.update(shas)
        self.created += [s for s in shas if s not in self.created]
        self.rewritten.update(rewritten or {})
        record_own(self.plan["config"], self.plan["sessions"], shas, rewritten=rewritten)

    def fetch(self, lab="Push"):
        if not self.remote:
            return True
        proc = git(self.R.root, "fetch", "--quiet", "--no-recurse-submodules", "origin", ok=None)
        if proc.returncode != 0:
            e = oneline(errtext(proc), 160)
            if lab == "Push":
                return self.stop("Push", T("нет сети ({e}); коммит остался локально",
                                           "no network ({e}); the commit stays local", e=e),
                                 "fetch")
            self.stop(lab, T("нет сети ({e}); {d} не сдвинут, коммит остался локально",
                             "no network ({e}); {d} not moved, the commit stays local", e=e,
                             d=self.default), "fetch")
            self.steps.say("Push", self.no_push_reason() or T("нет — после стопа",
                                                              "no — stopped before"),
                           "push", "skipped")
            return False
        return True

    def push(self, src, dst, set_upstream=False):
        args = ["push", "--quiet", "--no-follow-tags", "--recurse-submodules=no"] + \
            (["-u"] if set_upstream else []) + ["origin", f"{src}:refs/heads/{dst}"]
        tracking = "refs/remotes/origin/" + dst
        seen = rev(self.R.root, tracking)
        proc = git(self.R.root, *args, ok=None)
        if proc.returncode != 0:
            err = errtext(proc)
            # A race: origin/<dst> moved after our fetch. Only then is a rerun the answer.
            git(self.R.root, "fetch", "--quiet", "--no-recurse-submodules", "origin", ok=None)
            race = rev(self.R.root, tracking) != seen
            return self.stop("Push", T("origin/{d} отклонил push: {e}", "origin/{d} rejected the "
                                       "push: {e}", d=dst, e=oneline(err, 200)) + (T(
                                           " — на origin появились новые коммиты; запусти "
                                           "/commit снова", " — origin got new commits "
                                           "meanwhile; run /commit again") if race else ""),
                             "push")
        tip = rev(self.R.root, src)
        self.steps.say("Push", T("готово — origin/{d} → {s}", "done — origin/{d} → {s}", d=dst,
                                 s=short(tip)), "push", "done")
        return True

    def no_push_reason(self):
        if self.no_push:
            return T("нет — по аргументу", "no — as asked")
        if not self.remote:
            return T("нет — нет remote", "no — no remote")
        return None

    def run(self):
        R = self.R
        if not R.head:
            return True
        if self.plan.get("head") is None and self.branch != self.default:
            return self.new_branch()
        if self.branch == self.default:
            return self.on_default()
        if self.no_merge:
            return self.branch_only()
        return self.into_default()

    def check_foreign(self, include, exclude, where, merged=()):
        foreign = foreign_commits(self.R, include, exclude, self.own, merged)
        if foreign:
            return self.stop("Push", T("в {w} чужие незапушенные коммиты: {l} — не публикую "
                                       "чужое; запушь или убери их сам",
                                       "{w} has unpushed commits that are not this session's: "
                                       "{l} — not publishing them; push or drop them yourself",
                                       w=where, l=subjects(self.R, foreign)), "push")
        return True

    def new_branch(self):
        """A branch that had no history before this commit (an orphan): nothing to merge into
        main; it goes to origin/<branch>."""
        self.steps.say(self.main_label, T("нет — новая ветка без истории",
                                          "no — a new branch with no history"), "merge",
                       "skipped")
        reason = self.no_push_reason()
        if reason:
            self.steps.say("Push", reason, "push", "skipped")
            return True
        if not self.fetch():
            return False
        return self.check_foreign("HEAD", None, self.branch) and \
            self.push("refs/heads/" + self.branch, self.branch, set_upstream=True)

    def on_default(self):
        R, b = self.R, self.branch
        reason = self.no_push_reason()
        if reason:
            self.steps.say("Push", reason, "push", "skipped")
            return True
        if not self.fetch():
            return False
        up = "refs/remotes/origin/" + b
        if not rev(R.root, up):
            return self.check_foreign("HEAD", None, b) and \
                self.push("refs/heads/" + b, b, set_upstream=not upstream_of(R, b))
        if not rev_list(R.root, "HEAD", up):
            self.steps.say("Push", T("нет — origin/{b} уже содержит всё",
                                     "no — origin/{b} already has everything", b=b), "push",
                           "skipped")
            return True
        if not self.check_foreign("HEAD", up, b, merged=(up,)):
            return False
        behind = len(rev_list(R.root, up, "HEAD"))
        if behind:
            if not tracked_clean(R.root):
                return self.stop("Push", T(
                    "origin/{b} ушёл вперёд на {n}, а в дереве есть чужие незакоммиченные "
                    "правки — rebase не делаю; закоммить или убери их и запусти /commit снова",
                    "origin/{b} is {n} ahead and the tree has uncommitted changes that are not "
                    "this session's — no rebase; commit or remove them and run /commit again",
                    b=b, n=n_commits(behind)), "rebase")
            if not self.rebase(up, "origin/" + b, behind):
                return False
        return self.push("refs/heads/" + b, b, set_upstream=not upstream_of(R, b))

    def rebase(self, onto, name, behind):
        R = self.R
        # git rebase overwrites ignored files that `onto` starts tracking (a personal .env, an
        # editor's settings): it has no --no-overwrite-ignore. Look first.
        added = [os.fsdecode(p) for p in git(R.root, "diff", "--name-only", "--no-renames", "-z",
                                             "--diff-filter=A", "HEAD", onto, "--"
                                             ).stdout.split(b"\0") if p]
        lost = ignored_in_the_way(R, added)
        if lost:
            return self.stop("Rebase", T(
                "{n} начинает отслеживать файлы, которые у тебя в .gitignore: {p} — rebase их "
                "перезапишет; убери или переименуй их и запусти /commit снова",
                "{n} starts tracking files that are git-ignored here: {p} — a rebase would "
                "overwrite them; move them aside and run /commit again", n=name,
                p=", ".join(lost[:5]) + (" …" if len(lost) > 5 else "")), "rebase")
        before = list(reversed(rev_list(R.root, "HEAD", onto)))
        proc = git(R.root, *NO_AUTOSTASH, "rebase", "--quiet", onto, ok=None)
        if proc.returncode != 0:
            conflict = R.unmerged()
            if git_state(R.git_dir) in ("rebase-merge", "rebase-apply"):
                git(R.root, "rebase", "--abort", ok=None)
            if conflict:
                return self.stop("Rebase", T("конфликт с {n} — rebase отменён, всё как было после "
                                             "коммита; влей вручную",
                                             "conflict with {n} — rebase aborted, everything is as "
                                             "after the commit; merge by hand", n=name), "rebase")
            return self.stop("Rebase", T("rebase на {n} не выполнен: {e} — всё как было после "
                                         "коммита", "rebase onto {n} failed: {e} — everything is "
                                         "as after the commit", n=name,
                                         e=oneline(errtext(proc), 200)), "rebase")
        after = list(reversed(rev_list(R.root, "HEAD", onto)))
        self.mine(after, {o: n for o, n in zip(before, after) if o != n}
                  if len(after) == len(before) else None)
        self.rebased = True
        self.steps.say("Rebase", T("готово — мои коммиты поверх {n} (+{k})",
                                   "done — this session's commits on top of {n} (+{k})", n=name,
                                   k=behind), "rebase", "done")
        return True

    def merge_in(self, ref, name):
        """Merge `ref` into the session's branch (its checkout is clean of tracked changes)."""
        R = self.R
        proc = git(R.root, *NO_AUTOSTASH, "merge", "--no-edit", "--quiet",
                   "--no-overwrite-ignore", ref, ok=None)
        if proc.returncode != 0:
            conflict = R.unmerged()
            if git_state(R.git_dir) == "MERGE_HEAD":
                git(R.root, "merge", "--abort", ok=None)
            if conflict:
                return self.stop("Merge", T("конфликт при вливании {d} в ветку — merge отменён; "
                                            "влей вручную", "conflict merging {d} into the "
                                            "branch — merge aborted; merge by hand", d=name),
                                 "merge")
            return self.stop("Merge", T("{d} в ветку не влит: {e}", "merging {d} into the "
                                        "branch failed: {e}", d=name,
                                        e=oneline(errtext(proc), 200)), "merge")
        self.mine([rev(R.root, "HEAD")])
        self.steps.say("Merge", T("готово — {d} влит в ветку {b}",
                                  "done — {d} merged into branch {b}", d=name, b=self.branch),
                       "merge-base", "done")
        return True

    def branch_only(self):
        R, b = self.R, self.branch
        self.steps.say(self.main_label, T("нет — по аргументу", "no — as asked"), "merge",
                       "skipped")
        reason = self.no_push_reason()
        if reason:
            self.steps.say("Push", reason, "push", "skipped")
            return True
        if not self.fetch():
            return False
        up = own_upstream(R, b)
        up_main = "refs/remotes/origin/" + self.default
        base = up or (up_main if rev(R.root, up_main) else None)
        if not self.check_foreign("HEAD", base, b, merged=(up_main,)):
            return False
        if up:
            behind = len(rev_list(R.root, up, "HEAD"))
            if behind:
                return self.stop("Push", T(
                    "в origin/{b} {n}, которых нет в ветке — сначала влей origin/{b} в ветку "
                    "(git pull)", "origin/{b} has {n} the branch lacks — merge or pull "
                    "origin/{b} into the branch first", b=b, n=n_commits(behind)), "push")
            if not rev_list(R.root, "HEAD", up):
                self.steps.say("Push", T("нет — origin/{b} уже содержит всё",
                                         "no — origin/{b} already has everything", b=b), "push",
                               "skipped")
                return True
        return self.push("refs/heads/" + b, b, set_upstream=not upstream_of(R, b))

    def move_main(self, target, noff):
        """Move the default branch to `target`, fast-forward only (or a --no-ff merge in its
        checkout when the repository merges that way). Someone else's checkout of main is only
        merged into when it has nothing unfinished; their merge is never aborted."""
        R, d = self.R, self.default
        where, gone = checkout_of(R, d)
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
            # --no-overwrite-ignore: someone's ignored files there (a personal .env, editor
            # settings) stop the merge instead of being replaced by tracked ones. The merge
            # message names the branch as `git merge <branch>` would, not its full ref.
            name = target[len("refs/heads/"):] if target.startswith("refs/heads/") else target
            args = ["merge", "--no-ff", "--no-edit", "-m", f"Merge branch '{name}'",
                    "--no-overwrite-ignore", target] if noff \
                else ["merge", "--ff-only", "--quiet", "--no-overwrite-ignore", target]
            proc = git(where, *NO_AUTOSTASH, *args, ok=None)
            if proc.returncode != 0:
                # Nothing was unfinished there before, so a MERGE_HEAD now is this merge's.
                if noff and git_state(gd) == "MERGE_HEAD":
                    git(where, "merge", "--abort", ok=None)
                return self.stop(self.main_label, T(
                    "{b} не сдвинут: {e} (checkout {w}) — разберись там и запусти /commit снова",
                    "{b} not moved: {e} (checkout {w}) — sort it out there and run /commit "
                    "again", b=d, e=oneline(errtext(proc), 200), w=where), "merge")
            if noff:
                self.mine([rev(R.root, main_ref)])
            return True
        proc = git(R.root, "fetch", "--quiet", ".", f"{target}:{main_ref}", ok=None)
        if proc.returncode != 0:
            return self.stop(self.main_label, T("{b} не сдвинут: {e}", "{b} not moved: {e}", b=d,
                                                e=oneline(errtext(proc), 200)), "merge")
        return True

    def published(self, excl):
        """True when some commit of the branch beyond main is on a remote already (pushed,
        with or without -u): such a branch takes main by merge, never by rebase."""
        R = self.R
        beyond = rev_list(R.root, "HEAD", excl)
        local = text_of(git(R.root, "rev-list", "HEAD", *["^" + e for e in excl], "--not",
                            "--remotes", "--")).split()
        return len(local) < len(beyond)

    def into_default(self):
        R, b, d = self.R, self.branch, self.default
        if not self.fetch(self.main_label):
            return False
        main_ref, up = "refs/heads/" + d, "refs/remotes/origin/" + d
        have_main, have_up = bool(rev(R.root, main_ref)), bool(rev(R.root, up))
        if not have_main and not have_up:
            return self.stop(self.main_label, T("нет ветки {d} — влей вручную",
                                                "no branch {d} — merge by hand", d=d), "merge")
        # a. local main against origin/main
        diverged = False
        if have_main and have_up:
            foreign = foreign_commits(R, main_ref, up, self.own, merged=(up,))
            if foreign:
                return self.stop(self.main_label, main_foreign_text(R, d, foreign), "merge")
            behind = len(rev_list(R.root, up, main_ref))
            ahead = rev_list(R.root, main_ref, up)
            if behind and ahead:
                # Local main is ahead only by this session's commits (no-push earlier, or a
                # push that lost a race). They are on the branch already: origin/main comes into
                # the branch by merge, and main then fast-forwards to the branch.
                if not is_ancestor(R.root, main_ref, "HEAD"):
                    return self.stop(self.main_label, T(
                        "локальный {d} разошёлся с origin/{d} — сведи их вручную",
                        "local {d} and origin/{d} diverged — reconcile them by hand", d=d),
                        "merge")
                diverged = True
            elif behind:
                if not self.move_main(up, False):
                    return False
                self.steps.say(self.main_label, T("подтянул {d} до origin/{d} (+{n})",
                                                  "fast-forwarded {d} to origin/{d} (+{n})",
                                                  d=d, n=behind), "update-main", "done")
        excl = [r for r, ok in ((main_ref, have_main), (up, have_up)) if ok]
        # c. nothing foreign on the branch
        foreign = foreign_commits(R, "HEAD", excl, self.own, merged=excl)
        if foreign:
            return self.stop(self.main_label, branch_foreign(R, b, d, foreign), "merge")
        if not diverged and not rev_list(R.root, "HEAD", excl):
            self.steps.say(self.main_label, T("нет — ветка {b} уже в {d}",
                                              "no — branch {b} is already in {d}", b=b, d=d),
                           "merge", "skipped")
        else:
            # b. the branch takes main's new commits first
            lacking = len({c for r in excl for c in rev_list(R.root, r, "HEAD")})
            if lacking:
                if not tracked_clean(R.root):
                    return self.stop(self.main_label, T(
                        "в {d} {n} новых, а в дереве ветки чужие незакоммиченные правки — не "
                        "вливаю {d} в ветку; закоммить или убери их и запусти /commit снова",
                        "{d} has {n} new and this tree has uncommitted changes that are not this "
                        "session's — not merging {d} into the branch; commit or remove them and "
                        "run /commit again", d=d, n=n_commits(lacking)), "merge")
                target = up if diverged or not have_main else main_ref
                name = "origin/" + d if target == up else d
                if not diverged and not self.published(excl):
                    if not self.rebase(target, name, lacking):
                        return False
                elif not self.merge_in(target, name):
                    return False
            # d. move main to the branch head (or create it there)
            n = len(rev_list(R.root, "HEAD", excl))
            if have_main:
                noff = bool(noff_style(R, main_ref) and checkout_of(R, d)[0])
                if not self.move_main("refs/heads/" + b, noff):
                    return False
                how = T("merge --no-ff", "merge --no-ff") if noff else "fast-forward"
                self.steps.say(self.main_label, T("готово — {d} → {s} ({how}, {n})",
                                                  "done — {d} → {s} ({how}, {n})", d=d,
                                                  s=short(rev(R.root, main_ref)), how=how,
                                                  n=n_commits(n)), "merge", "done")
            else:
                tip = rev(R.root, "HEAD")
                proc = git(R.root, "branch", "--no-track", d, tip, ok=None)
                if proc.returncode != 0:
                    return self.stop(self.main_label, T("{d} не создан: {e}", "{d} not created: "
                                                        "{e}", d=d, e=oneline(errtext(proc), 200)),
                                     "merge")
                have_main = True
                self.steps.say(self.main_label, T("готово — создал {d} на {s} ({n})",
                                                  "done — created {d} at {s} ({n})", d=d,
                                                  s=short(tip), n=n_commits(n)), "merge", "done")
        # e. push
        reason = self.no_push_reason()
        if reason:
            self.steps.say("Push", reason, "push", "skipped")
            return True
        src = main_ref if have_main else "refs/heads/" + b
        bup = own_upstream(R, b)
        if have_up and not rev_list(R.root, src, up):
            # main is on origin already; the branch's own upstream may still lack it.
            if not (bup and rev_list(R.root, "HEAD", bup)):
                self.steps.say("Push", T("нет — origin/{d} уже содержит всё",
                                         "no — origin/{d} already has everything", d=d), "push",
                               "skipped")
                return True
        else:
            if not self.check_foreign(src, up if have_up else None, d,
                                      merged=(up,) if have_up else ()):
                return False
            if not self.push(src, d):
                return False
        if bup:
            behind = rev_list(R.root, bup, "HEAD")
            if behind and self.rebased and all(c in self.rewritten for c in behind):
                self.steps.say("Push", T("ветка {b} не запушена: разошлась с origin после "
                                         "rebase (force не делаю)", "branch {b} not pushed: it "
                                         "diverged from origin after a rebase (no force)", b=b),
                               "push-branch", "skipped")
            elif behind:
                self.steps.say("Push", T("ветка {b} не запушена: в origin/{b} {n}, которых нет "
                                         "в ветке: {l} — влей их в ветку",
                                         "branch {b} not pushed: origin/{b} has {n} that are "
                                         "not in the branch: {l} — pull them into the branch",
                                         b=b, n=n_commits(len(behind)), l=subjects(R, behind)),
                               "push-branch", "skipped")
            elif rev_list(R.root, "HEAD", bup):
                return self.push("refs/heads/" + b, b)
        return True


def apply_cmd(args):
    with open(args.plan, encoding="utf-8") as fh:
        plan = json.load(fh)
    global LANG
    LANG = args.lang or plan.get("lang") or "en"
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

    if plan.get("verdict") == "stop":
        steps.say(T("Стоп", "Stop"), plan.get("stop") or "?", "plan", "stop")
        return finish(1)
    if not resync(R, plan, steps):
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
        try:
            made = commit_step(R, plan, message, steps)
        except Stale as e:
            print(f"plan is stale, run plan again: {e}", file=sys.stderr)
            return finish(3)
        except Moved:
            return finish(3)
        if not made:
            return finish(1)
        head, verified, trusted = made
        result["commit"] = head
        if trusted:
            own.add(head)  # recorded by commit_step, with the last edit of each path it holds
        if not verified:
            return finish(1)
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
    finally:
        record_own(plan["config"], sids, pub.created, rewritten=pub.rewritten)
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
    p.add_argument("--no-merge", action="store_true", help="do not merge into the default branch")
    p.add_argument("--no-push", action="store_true", help="do not push")
    a = sub.add_parser("apply", help="commit, merge and push as planned")
    a.add_argument("--plan", required=True, help="RUN/plan.json from plan")
    a.add_argument("--message-file", help="the commit message, or - for stdin")
    a.add_argument("--lang", choices=("ru", "en"))
    a.add_argument("--no-merge", action="store_true")
    a.add_argument("--no-push", action="store_true")
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
