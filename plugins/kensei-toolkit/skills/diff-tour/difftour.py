#!/usr/bin/env python3
"""diff-tour — an annotated diff page for self-review before a commit.

  difftour.py collect [REF | A..B] [--out-root DIR]
      Snapshot the diff into a new run directory (patch.diff, hunks.json, index.txt) and print
      the index of its units; a leading `all` (the skill's switch) is dropped. No argument:
      HEAD against the working tree, untracked files included; when nothing is uncommitted,
      the commits HEAD has beyond its upstream (or else the default branch) instead; a HEAD that shares no history with any of them (an orphan branch) shows
      nothing and says so. REF: the merge-base of REF and HEAD against the working tree, untracked
      included (for a REF that HEAD descends from, that is REF itself). A..B: that range only —
      A and B may be commits or tree ids (a `git write-tree` result). A...B: from the merge-base
      of two commits. Run directories go under DIR/<repo>/ (default ~/.cache/kensei-diff), private
      to the user; the newest KEEP_RUNS per repository are kept.
  difftour.py build RUN_DIR [--open] [--all]
      Check RUN_DIR/notes.json and the reviewers' RUN_DIR/triage-*.json against the snapshot,
      render RUN_DIR/index.html, print the queue, the counts and every unexplained unit. The
      page opens on the queue: suspected failures, then units worth a look (a reviewer's
      question, a risk rule, an author's flag), the rest folded. --all: the whole diff
      only, no triage files needed. Exit 1 with the list of problems if notes.json or a triage
      file is invalid, or there are no triage files without --all; exit 2 on any other error.

A unit is one @@ hunk (id hN), or a whole file when the file has no hunks (binary, pure rename,
mode change — also hN) or matches a noise pattern (nN). Code reaches the page only from
patch.diff; notes.json refers to units by id, anchors a note under one line by quoting a piece
of it, and never carries code. A unit no note mentions is shown as "Unexplained" — that is the
page's signal, not an error. Risk rules (built-in, plus .claude/review-risk read from the base)
and the reviewers can only raise a unit into the queue; only the script folds the rest. Nothing
here writes to the repository or its index.
"""

import argparse
import datetime
import fnmatch
import hashlib
import html
import json
import os
import pathlib
import re
import shutil
import string
import subprocess
import sys
import tempfile
import webbrowser

OUT_ROOT = os.path.expanduser("~/.cache/kensei-diff")
KEEP_RUNS = 20         # run directories kept per repository; older ones are deleted by collect
NOISE_FILE = ".claude/diff-tour-noise"  # project noise patterns, read from the base commit
RISK_FILE = ".claude/review-risk"        # project risk rules, read from the base commit
# Risk rules: what puts a unit into the queue's risk list whatever the reviewers say. One rule
# per line: `path <glob>` (noise-file syntax, old or new path), `line <regex>` (an added or
# removed line), `section <regex>` (the code around the change: the hunk's @@ context or any
# of its unchanged lines). Enum members and MemoryPack layout are not rules but checks that read
# the file at the base (enum_risk, memorypack_risk): a regex cannot tell an append from an insertion.
DEFAULT_RISK = [
    # serialized and networked format
    r"line \bIRpcCommand\b|\[Ghost(Field|Component)\b",
    r"line \[Meta(Method|Service)\b|\bExecutionMode\.",
    r"line \b(MajorMinor|ClientVersion|bundleVersion|schema_?[vV]ersion)\b|\"schema\"\s*:",
    r'line ^\s*"unity(Release)?"\s*:',
    # who may do what
    r"line \b(forbidden_paths|deny_tools|allowed_?tools|trusted_authors|RequirePermission)\b",
    # destructive operations
    r"line \bprocess\.kill\b|\bkill(Tree|Orphans)?\(|\bfs\.rm\b|\b(rm|rmdir|unlink)Sync\(",
    r"line \brm -[a-zA-Z]*[rf]|\b(Directory|File)\.Delete\(|AssetDatabase\.DeleteAsset\(",
    r"line (reset --hard|clean -[a-z]*f|worktree remove|branch -D|push\b.*(--force|\s-f\b))",
    # the same git commands with their arguments as a list: git(["clean", "-ffdxq"])
    r"""line ["'](clean["']\s*,\s*["']-[a-zA-Z]*f|reset["']\s*,\s*["']--hard\b|branch["']\s*,"""
    r"""\s*["']-D\b|worktree["']\s*,\s*["']remove\b)|["']push["']\s*,.*["'](--force\S*|-f)["']""",
    r"line (?<![\w.])(rm|rmdir|unlink)\(|\.(rm|rmdir|unlink)\(|\bshutil\.rmtree\(|"
    r"\bos\.(remove|removedirs)\(",
    # secrets written into the code
    r"""line (?i)\bsk-[a-z0-9_-]{20,}|\bAKIA[0-9A-Z]{16}\b|\bgh[pousr]_[a-z0-9]{30,}|"""
    r"""\bxox[abpr]-[a-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"""
    r"""\b\w{0,40}?(api_?key|(secret|access|private)_?(access_?)?key|secret|token|passw(or)?d)["']?"""
    r"""(\s{0,4}:\s{0,4}[\w&'<>\[\]?]{1,30})?\s{0,4}(:=|=>|[:=])\s{0,4}["'](?!\$\{|<|process\.env|(?-i:[a-z]+(_[a-z]+)+|[A-Z]+(_[A-Z]+)+)["'])"""
    r"""[^"'\s]{12,}["']""",
    # build and module boundaries
    "path *.asmdef", "path *.asmref", "path **/ProjectSettings/", "path **/Packages/manifest.json",
    "path appsettings*.json", "path .github/workflows/",
    # what decides how this tour itself folds and shows the diff
    "path " + NOISE_FILE, "path " + RISK_FILE, "path .gitattributes",
]
RISK_KINDS = ("path", "line", "section")
TEST_PATH = re.compile(r"(^|/)(?i:tests?|__tests__)/|\.Tests?(\.|/)|Tests?\.(cs|swift)$|"
                       r"\.(test|spec)\.[a-z]+$|(^|/)test_[^/]*\.py$|_test\.(py|go)$")
TEST_DEF = re.compile(r"\[(Test|UnityTest|TestCase|TestCaseSource|Fact|Theory)\b|@Test\b|"
                      r"^\s*(test|it)(\.each\b.*?)?[(`]|"
                      r"^\s*(async\s+)?def test_|\bfunc test\w*\(")
ASSERT = re.compile(r"\bAssert\.|\bassert\w*\b|\bexpect\(|#require\(|\bXCTAssert|\bShould\b")
SKIP = re.compile(r"\[(Ignore|Explicit)\b|@(unittest\.)?skip|pytest\.mark\.(skip|xfail)|"
                  r"\.(skip|only)\(|\b(xit|xdescribe|xtest)\(|Assert\.(Ignore|Inconclusive)\(|"
                  r"\bXCTSkip|\bt\.Skip\(|#\[ignore\]")
DEFAULT_NOISE = [
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
    "Cargo.lock", "poetry.lock", "Pipfile.lock", "uv.lock", "Gemfile.lock", "composer.lock",
    "go.sum", "packages.lock.json", "*.min.js", "*.min.css", "*.map",
]
BIG_DIFF = 3000        # changed lines outside noise before collect warns
HIGHLIGHT_MAX = 20000  # diff lines on the page above which syntax highlighting is skipped
# Overrides for whatever the user's .gitconfig says about diff output (noprefix, color, external,
# submodule=log|diff — which would drop a gitlink change or inline the submodule's own files).
DIFF_FLAGS = ["--no-color", "--no-ext-diff", "--no-textconv", "--submodule=short",
              "--src-prefix=a/", "--dst-prefix=b/"]
SOURCES = ("session", "inferred")
KINDS = ("untested", "decision", "temporary", "stray")
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
HLJS = "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"
# Subresource integrity of exactly that file: the browser refuses it if the CDN serves anything
# else. Recompute when the version changes: curl -sL URL | openssl dgst -sha384 -binary | base64
HLJS_SRI = "sha384-F/bZzf7p3Joyp5psL90p/p89AZJsndkSoGwRpXcZhleCWhd8SnRuoYo4d0yirjJp"
LANGS = {
    "cs": "csharp", "py": "python", "js": "javascript", "mjs": "javascript", "jsx": "javascript",
    "ts": "typescript", "tsx": "typescript", "json": "json", "md": "markdown", "yml": "yaml",
    "yaml": "yaml", "sh": "bash", "zsh": "bash", "bash": "bash", "go": "go", "rs": "rust",
    "java": "java", "kt": "kotlin", "c": "c", "h": "cpp", "cpp": "cpp", "hpp": "cpp",
    "cc": "cpp", "hlsl": "cpp", "cginc": "cpp", "shader": "cpp", "compute": "cpp",
    "html": "xml", "xml": "xml", "uxml": "xml", "csproj": "xml", "css": "css", "uss": "css",
    "scss": "scss", "sql": "sql", "rb": "ruby", "swift": "swift", "lua": "lua", "php": "php",
    "ini": "ini", "toml": "ini",
}


class Fail(Exception):
    """A user-facing error: printed as is, exit 2."""


# --- git ---------------------------------------------------------------------------------

def git(root, *args, ok=(0,), index=None, stdin=None):
    env = dict(os.environ, LC_ALL="C", LANG="C")
    for key in ("GIT_EXTERNAL_DIFF", "GIT_DIFF_OPTS"):
        env.pop(key, None)
    if index:
        env["GIT_INDEX_FILE"] = index
    # autoRefreshIndex: a porcelain `git diff` would otherwise rewrite the stat cache of the
    # user's index (and hold index.lock) whenever a tracked file was touched without a change.
    proc = subprocess.run(["git", "-c", "core.quotepath=off", "-c", "diff.autoRefreshIndex=false",
                           *args], cwd=root, env=env, input=stdin, capture_output=True)
    if proc.returncode not in ok:
        err = proc.stderr.decode("utf-8", "replace").strip()
        cmd = next((a for a in args if not a.startswith("-")), args[0])
        raise Fail(f"git {cmd} failed: {err}")
    return proc.stdout


def toplevel(cwd):
    try:
        return os.fsdecode(git(cwd, "rev-parse", "--show-toplevel").strip())
    except Fail:
        raise Fail("not inside a git working tree") from None


def branch(root):
    name = git(root, "symbolic-ref", "--short", "-q", "HEAD", ok=(0, 1)).decode().strip()
    if name:
        return name
    return "detached " + git(root, "rev-parse", "--short", "HEAD").decode().strip()


def commit_of(root, ref):
    return git(root, "rev-parse", "--verify", "--quiet", ref + "^{commit}",
               ok=(0, 1)).decode().strip()


def resolve(root, arg):
    """What the diff compares: a commit range, or a base commit against the working tree.

    A REF other than HEAD is compared from where HEAD's history meets it (the merge-base), so
    `main` shows the branch's own work, not main's newer commits reversed."""
    if arg and ".." in arg:
        spec = {"mode": "range", "range": arg}
        # Pin both ends: build must read the same base after the branch moves on.
        for key, rev in (("base", base_of(root, spec)), ("new", new_of(spec))):
            sha = git(root, "rev-parse", "--verify", "--quiet", rev, ok=(0, 1)).decode().strip()
            if sha:
                spec[key] = sha
        return spec
    head = commit_of(root, "HEAD") or None
    if not arg:
        if head:
            return {"mode": "worktree", "ref": "HEAD", "sha": head, "tip": head, "head": head}
        # No commits yet: the empty tree of this repository's hash (SHA-1 or SHA-256).
        empty = git(root, "hash-object", "-t", "tree", "/dev/null").decode().strip()
        return {"mode": "worktree", "ref": None, "sha": empty, "tip": None, "head": None}
    tip = commit_of(root, arg)
    if not tip:
        raise Fail(f"not a commit: {arg}")
    base = tip
    if head:
        base = git(root, "merge-base", tip, head, ok=(0, 1)).decode().strip() or tip
    return {"mode": "worktree", "ref": arg, "sha": base, "tip": tip, "head": head}


def index_path(root):
    path = os.fsdecode(git(root, "rev-parse", "--git-path", "index").strip())
    return path if os.path.isabs(path) else os.path.join(root, path)


def take(root, spec):
    """The patch bytes for spec, and the untracked nested repositories left out of it.

    Untracked files join the diff as intent-to-add entries of a throwaway copy of the index, so
    git shows them the way a commit would (a symlink stays a link, never followed) in one call.
    The user's index is only read. Attributes come from the base, so a change cannot hide its
    own lines behind a new `-diff` or `binary` in .gitattributes."""
    attrs = "--attr-source=" + base_of(root, spec)
    if spec["mode"] == "range":
        return git(root, attrs, "diff", *DIFF_FLAGS, "-M", spec["range"], "--"), []
    listing = git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
    skipped = [os.fsdecode(p) for p in listing if p.endswith(b"/")]  # nested repositories
    untracked = [p for p in listing if p and not p.endswith(b"/")]
    if not untracked:
        return git(root, attrs, "diff", *DIFF_FLAGS, "-M", spec["sha"], "--"), skipped
    scratch = tempfile.mkdtemp(prefix="difftour-")
    try:
        index = os.path.join(scratch, "index")
        if os.path.exists(index_path(root)):
            # copy2, not copyfile: the copy keeps the index's mtime. git trusts an entry's cached
            # stat only for files older than the index, so a fresh mtime would hide an edit that
            # kept the size and landed in the same second as the last index write.
            shutil.copy2(index_path(root), index)
        git(root, "-c", "core.splitIndex=false", "--literal-pathspecs", "add", "-N",
            "--pathspec-from-file=-", "--pathspec-file-nul", index=index,
            stdin=b"\0".join(untracked))
        return (git(root, attrs, "diff", *DIFF_FLAGS, "-M", spec["sha"], "--", index=index),
                skipped)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def fallback_refs(root):
    """Where to look when nothing is uncommitted: the upstream (commits not pushed yet), then
    the default branch (the branch's own work)."""
    refs = [git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}",
                ok=(0, 128)).decode().strip(),
            git(root, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD",
                ok=(0, 1, 128)).decode().strip(),
            "main", "master", "origin/main", "origin/master"]
    seen = []
    for ref in refs:
        if ref and ref not in seen:
            seen.append(ref)
    return seen


def after_commit(root, spec):
    """(spec, patch, skipped) against the first fallback ref HEAD has commits beyond, or
    (None, unrelated): the fallback refs HEAD shares no history with, when it shares history
    with none of them (an orphan branch), else an empty list."""
    if not spec["head"]:
        return None, []
    unrelated, related = [], False
    for ref in fallback_refs(root):
        if not commit_of(root, ref):
            continue
        tip = commit_of(root, ref)
        if not git(root, "merge-base", ref, spec["head"], ok=(0, 1)).strip():
            unrelated.append(ref)  # no shared history (an orphan branch): not HEAD's work
            continue
        # HEAD's own upstream at HEAD (an orphan branch pushed with -u) relates it to nothing
        related = related or tip != spec["head"]
        later = resolve(root, ref)
        if later["sha"] == spec["head"]:
            continue  # HEAD adds nothing to this ref
        patch, skipped = take(root, later)
        if patch.strip():
            return (later, patch, skipped), []
    return None, ([] if related else unrelated)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


# --- parsing -----------------------------------------------------------------------------

ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


def read_quoted(s):
    """Parse git's C-style quoted path at the start of s → (path, rest after the quote)."""
    out, i = bytearray(), 1
    while i < len(s):
        c = s[i]
        if c == '"':
            return out.decode("utf-8", "replace"), s[i + 1:]
        if c == "\\" and i + 1 < len(s):
            n = s[i + 1]
            if n in ESCAPES:
                out.append(ESCAPES[n])
                i += 2
            elif n in "01234567":
                out.append(int(s[i + 1:i + 4], 8) & 0xFF)
                i += 4
            else:
                out += n.encode()
                i += 2
            continue
        out += c.encode()
        i += 1
    raise Fail(f"unterminated quoted path: {s}")


def unquote(s):
    return read_quoted(s)[0] if s.startswith('"') else s


def strip_prefix(path, prefix):
    return path[len(prefix):] if path.startswith(prefix) else path


def split_git_line(rest):
    """`a/X b/Y` from a `diff --git` line → (X, Y). Only a fallback: ---/+++ and rename lines
    are exact, this line is ambiguous when a path contains ' b/'."""
    if rest.startswith('"'):
        a, tail = read_quoted(rest)
        b = unquote(tail.lstrip(" "))
    elif rest.endswith('"'):
        k = rest.rfind(' "')
        a, b = rest[:k], unquote(rest[k + 1:])
    else:
        half = (len(rest) - 1) // 2
        if len(rest) % 2 == 1 and rest[half] == " " and rest[2:half] == rest[half + 3:]:
            a, b = rest[:half], rest[half + 1:]
        else:
            k = rest.find(" b/")
            a, b = rest[:k], rest[k + 1:]
    return strip_prefix(a, "a/"), strip_prefix(b, "b/")


def parse_header(f, line):
    if line.startswith("new file mode "):
        f["status"], f["new_mode"] = "A", line[14:]
    elif line.startswith("deleted file mode "):
        f["status"], f["old_mode"] = "D", line[18:]
    elif line.startswith("old mode "):
        f["old_mode"] = line[9:]
    elif line.startswith("new mode "):
        f["new_mode"] = line[9:]
    elif line.startswith(("rename from ", "copy from ")):
        f["status"] = "R" if line.startswith("r") else "C"
        f["old"] = unquote(line.split(" ", 2)[2])
    elif line.startswith(("rename to ", "copy to ")):
        f["new"] = unquote(line.split(" ", 2)[2])
    elif line.startswith("similarity index "):
        f["similarity"] = int(line[17:].rstrip("%"))
    elif line.startswith("Binary files "):
        f["binary"] = True
    elif line.startswith("--- ") and line != "--- /dev/null":
        # git appends a TAB to names that contain a space
        f["old"] = strip_prefix(unquote(line[4:].rstrip("\t")), "a/")
    elif line.startswith("+++ ") and line != "+++ /dev/null":
        f["new"] = strip_prefix(unquote(line[4:].rstrip("\t")), "b/")


def parse_hunk(lines, i):
    head = lines[i]
    m = HUNK_RE.match(head)
    if not m:
        raise Fail(f"patch.diff line {i + 1}: malformed hunk header")
    end = head.find(" @@", 3) + 3
    hunk = {"header": head[:end], "section": head[end:].strip(), "old_start": int(m.group(1)),
            "new_start": int(m.group(3)), "lines": [], "line": i + 1}
    old_left = 1 if m.group(2) is None else int(m.group(2))
    new_left = 1 if m.group(4) is None else int(m.group(4))
    i += 1
    # Counting lines, not guessing by prefix: a removed line "-- a/x" reads as "--- a/x".
    while i < len(lines) and (old_left > 0 or new_left > 0 or lines[i].startswith("\\")):
        line = lines[i]
        tag, body = (line[0], line[1:]) if line else (" ", "")  # diff.suppressBlankEmpty
        if tag == " ":
            old_left -= 1
            new_left -= 1
        elif tag == "-":
            old_left -= 1
        elif tag == "+":
            new_left -= 1
        elif tag == "\\":
            body = body.strip()
        else:
            raise Fail(f"patch.diff line {i + 1}: unexpected line inside a hunk")
        hunk["lines"].append([tag, body])
        i += 1
    hunk["added"] = sum(1 for t, _ in hunk["lines"] if t == "+")
    hunk["removed"] = sum(1 for t, _ in hunk["lines"] if t == "-")
    return hunk, i


def parse(text):
    lines = text.split("\n")  # not splitlines(): \x0b, \x0c,   are content
    if lines and lines[-1] == "":
        lines.pop()
    files, i = [], 0
    while i < len(lines):
        if not lines[i].startswith("diff --git "):
            i += 1
            continue
        a_path, b_path = split_git_line(lines[i][len("diff --git "):])
        f = {"old": None, "new": None, "status": "M", "binary": False, "old_mode": None,
             "new_mode": None, "similarity": None, "hunks": [], "line": i + 1}
        i += 1
        while i < len(lines) and not lines[i].startswith(("diff --git ", "@@ ")):
            parse_header(f, lines[i])
            i += 1
        while i < len(lines) and lines[i].startswith("@@ "):
            hunk, i = parse_hunk(lines, i)
            f["hunks"].append(hunk)
        f["old"] = None if f["status"] == "A" else (f["old"] or a_path)
        f["new"] = None if f["status"] == "D" else (f["new"] or b_path)
        f["added"] = sum(h["added"] for h in f["hunks"])
        f["removed"] = sum(h["removed"] for h in f["hunks"])
        files.append(f)
    return files


def patterns_of(text):
    lines = [ln.strip() for ln in text.splitlines()]
    return [ln for ln in lines if ln and not ln.startswith("#")]


def base_of(root, spec):
    """The commit (or tree) the diff starts from — the state the change could not have edited."""
    if spec["mode"] == "worktree":
        return spec["sha"]
    if spec.get("base"):  # pinned by collect, so build reads the same base after new commits
        return spec["base"]
    a, dots, b = spec["range"].partition("...")
    if dots:
        base = git(root, "merge-base", a or "HEAD", b or "HEAD", ok=(0, 1)).decode().strip()
        if not base:
            raise Fail(f"{a or 'HEAD'} and {b or 'HEAD'} share no history: use a..b")
        return base
    return spec["range"].partition("..")[0] or "HEAD"


def new_of(spec):
    """The commit or tree of the diff's new side, or None for the working tree."""
    if spec["mode"] == "worktree":
        return None
    return spec.get("new") or spec["range"].replace("...", "..").partition("..")[2] or "HEAD"


def base_patterns(root, spec, name):
    """The project's patterns file as the base has it: a change that edits or adds the file is
    still judged by the old list, so it cannot take its own lines out of the queue."""
    base = base_of(root, spec)
    if not git(root, "rev-parse", "--verify", "--quiet", base + "^{tree}", ok=(0, 1)).strip():
        raise Fail(f"the base {base} is not in the repository any more")
    out = git(root, "show", f"{base}:{name}", ok=(0, 128))
    return patterns_of(out.decode("utf-8", "replace"))


def rules_at_base(root, spec):
    """(noise patterns, risk rules): the built-in ones and the project's, both from the base."""
    return (DEFAULT_NOISE + base_patterns(root, spec, NOISE_FILE),
            DEFAULT_RISK + base_patterns(root, spec, RISK_FILE))


def risk_rules(rules):
    """(kind, body, compiled regex or None) per valid rule, and the rules that are not valid."""
    out, bad = [], []
    for r in rules:
        kind, _, body = r.partition(" ")
        body = body.strip()
        if kind not in RISK_KINDS or not body:
            bad.append(r)
            continue
        if kind == "path":
            out.append((kind, body, None))
            continue
        try:
            out.append((kind, body, re.compile(body)))
        except re.error:
            bad.append(r)
    return out, bad


def short(s, n=90):
    s = s.strip()
    return s if len(s) <= n else s[:n - 1] + "…"


def unit_risk(f, hunks, rules):
    """Why the script puts a unit — the hunks of file `f` it covers, none for a file without
    hunks — into the risk list: (kind, detail) pairs. A file moved out of a risky path is still
    risky, and so is a test file moved out of the tests."""
    out = []
    for kind, body, rx in rules:
        if kind == "path":
            if any(is_noise(p, [body]) for p in (f["old"], f["new"]) if p):
                out.append(("path", body))
            continue
        for h in hunks:
            if kind == "section":
                hit = next((t for t in [h["section"]] + [t for tag, t in h["lines"] if tag == " "]
                            if rx.search(t)), None)
            else:
                hit = next((t for tag, t in h["lines"] if tag in "+-" and rx.search(t)), None)
            if hit is not None:
                out.append((kind, short(hit)))
                break
    out += weakened_test(f, hunks)
    seen = set()
    return [x for x in out if not (x in seen or seen.add(x))]


def weakened_test(f, hunks):
    """An existing test that checks less: fewer tests or asserts than before, a skip added, or
    the file moved out of the tests. A rewritten assert with the same count is the reviewers'
    to judge — the script cannot tell a tighter one from a looser one."""
    if not (f["old"] and TEST_PATH.search(f["old"])):
        return []
    if f["new"] and not TEST_PATH.search(f["new"]):
        return [("test", f"moved out of the tests: {f['new']}")]

    def count(tag, rx):
        return sum(1 for h in hunks for t, s in h["lines"] if t == tag and rx.search(s))
    tests = (count("-", TEST_DEF), count("+", TEST_DEF))
    asserts = (count("-", ASSERT), count("+", ASSERT))
    out = []
    if tests[0] > tests[1] or asserts[0] > asserts[1]:
        out.append(("test", f"tests −{tests[0]} +{tests[1]}, asserts −{asserts[0]} "
                            f"+{asserts[1]}"))
    skip = next((s for h in hunks for t, s in h["lines"] if t == "+" and SKIP.search(s)), None)
    if skip is not None:
        out.append(("test", short(skip)))
    return out


# --- checks that read the file ----------------------------------------------------------
# The file at the base comes from git; the new side is the base with the hunks applied, so build
# gets the same answer as collect from the same patch.diff.

BRACE_EXT = {"cs", "java", "kt", "ts", "tsx", "js", "mjs", "swift", "c", "h", "cc", "cpp", "hpp",
             "rs", "proto", "fbs", "thrift"}
JSON_EXT = {"json", "asmdef", "asmref"}
COMMENT = re.compile(r"^\s*(//|/\*|\*|#(?!\[)|$)")
ENUM_DECL = re.compile(r"^(?!\s*(import|from)\b)\s*(#?\[[^\]]*\]+\s*|@?\w+(\([^)]*\))?\s+)*enum\b"
                       r"(\s+(class|struct))?(\s*\[\[[^\]]*\]\])?"
                       r"(\s+\w+(<([^<>]|<[^<>]*>)*>|\(([^()]|\([^()]*\))*\))?)?"
                       r"\s*((:|\bimplements\b|\bwhere\b)[^{;=]*)?(\{.*|//.*|/\*.*)?$")
PREPROC = re.compile(r"\s*#\s*(if|ifdef|ifndef|elif|else|endif)\b")
PY_ENUM = re.compile(r"^\s*class\s+\w+\s*\([^)]*\b(Int|Str)?(Enum|Flag)\b")
ENUM_MEMBER = {
    "swift": re.compile(r"^\s*(@\w+(\([^)]*\))?\s*)*(indirect\s+)?case\b"),
    "py": re.compile(r"^\s*[A-Za-z_]\w*\s*(:[^=]+)?=(?!=)"),
    "": re.compile(r"^\s*(\[[^\]]*\]\s*|@\w+\s*)*{m}(\s*,\s*{m})*\s*([,;]\s*)?(//.*|/\*.*)?$"
                   .replace("{m}", r"[A-Za-z_]\w*({p}|\{[^}]*\})?(\s*=\s*([^=,;()\s]|{p})(([^=,;()]|{p})*([^=,;()\s]|{p}))?)?")
                   .replace("{p}", r"\(([^()]|\([^()]*\))*\)")),  # (a, b) and one level inside
}
MP_ORDER = re.compile(r"\bMemoryPackOrder\(\s*(\d+)\s*\)")
MP_UNION = re.compile(r"\bMemoryPackUnion\(\s*(\d+)")
MP_TYPE = re.compile(r"\b(class|struct|record|interface)\s+(?!(class|struct)\b)(\w+)")
MP_ATTR = re.compile(r"\[MemoryPack(Ignore|Include|Constructor|Formatter|AllowSerialize)\b")
MP_FIELD = re.compile(r"^\s*(\[[^\]]*\]\s*)*((public|internal|protected|private|readonly|required|new|"
                      r"volatile|virtual|override|abstract|sealed)\s+)+"
                      r"(?!(class|struct|record|interface|enum|delegate|event)\b)"
                      r"(\([^)]*\)|[\w.:]+(<[^;={}]*>)?)(\[[,\s]*\]|\?)*\s+\w+(\s*,\s*\w+)*\s*"
                      r"(;|=(?!>)|\{\s*(get|init|set)\b|(?P<open>$))")
MP_PRIVATE = re.compile(r"^\s*(\[[^\]]*\]\s*)*((static|readonly|new|volatile|required|unsafe)\s+)*private\b")
CHECKS_OFF = re.compile(r"Il2CppSetOption\(\s*Option\.(\w+)\s*,\s*false\s*\)|"
                        r"\b(unsafe)\s+(partial\s+)?(class|struct)\b|\[(SkipLocalsInit)\b")


def read_blobs(root, rev, paths):
    """{path: text} of the files that exist at rev, through one `git cat-file --batch`."""
    paths = [p for p in dict.fromkeys(paths) if "\n" not in p]
    if not paths:
        return {}
    out = git(root, "cat-file", "--batch", stdin="".join(f"{rev}:{p}\n" for p in paths).encode())
    texts, pos = {}, 0
    for p in paths:
        end = out.index(b"\n", pos)
        head = out[pos:end].split()
        pos = end + 1
        if head[-1:] == [b"missing"]:  # the object name may hold spaces: test the end first
            continue
        if len(head) == 3 and head[1] == b"blob":
            size = int(head[2])
            texts[p] = out[pos:pos + size].decode("utf-8", "replace")
            pos += size + 1
        elif len(head) == 3:  # a tree or a submodule: no content
            pos += int(head[2]) + 1
    return texts


def ext_of(path):
    return path.rpartition(".")[2].lower() if "." in path.rpartition("/")[2] else ""


def wants_base(f):
    """Whether a check reads this file's content: brace languages and Python for enums,
    MemoryPack and switched-off checks, JSON for "only reformatted"."""
    path = f["new"] or f["old"]
    return bool(f["old"]) and not f["binary"] and bool(f["hunks"]) and \
        ext_of(path) in BRACE_EXT | JSON_EXT | {"py"}


def base_texts(root, spec, files):
    """{old path: text at the base} for the files a check reads."""
    return read_blobs(root, base_of(root, spec), [f["old"] for f in files if wants_base(f)])


def text_lines(text):
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def apply_hunks(old, hunks):
    """The new side's lines: the base's with the hunks applied, or None if they do not fit."""
    out, pos = [], 0
    try:
        for h in hunks:
            n_old = sum(1 for t, _ in h["lines"] if t in " -")
            start = h["old_start"] - (1 if n_old else 0)
            if start < pos:
                return None
            out += old[pos:start]
            pos = start
            for t, body in h["lines"]:
                if t in " -":
                    if old[pos] != body:
                        return None
                    pos += 1
                if t in " +":
                    out.append(body)
        return out + old[pos:]
    except IndexError:
        return None


def line_positions(h):
    """(tag, text, old index, new index) per changed line; an added line's old index is the
    base line it goes before, a removed line's new index the new line it was before."""
    n_old = sum(1 for t, _ in h["lines"] if t in " -")
    n_new = sum(1 for t, _ in h["lines"] if t in " +")
    o = h["old_start"] - (1 if n_old else 0)
    n = h["new_start"] - (1 if n_new else 0)
    out = []
    for t, body in h["lines"]:
        if t in "+-":
            out.append((t, body, o, n))
        o += t in " -"
        n += t in " +"
    return out


def code_of(body, hash_comments=False, strings=True):
    """A line without its comments, strings kept as they are (or dropped). Linear: a quote that
    never closes is an ordinary character (a Rust lifetime, an apostrophe)."""
    out, i, n = [], 0, len(body)
    special = re.compile(r"[\"'\\#]" if hash_comments else r"[\"'\\/]")
    while i < n:
        m = special.search(body, i)
        if not m:
            out.append(body[i:])
            break
        out.append(body[i:m.start()])
        i, c = m.start(), m.group()
        if c == "\\":
            out.append(body[i:i + 2])
            i += 2
        elif c in "\"'":
            j = i + 1
            while j < n and body[j] != c:
                j += 2 if body[j] == "\\" else 1
            if j < n:
                out.append(body[i:j + 1] if strings else "")
                i = j + 1
            else:
                out.append(c)
                i += 1
        elif hash_comments:
            if c == "#":
                break
            out.append(c)
            i += 1
        elif body.startswith("//", i):
            break
        elif body.startswith("/*", i):
            j = body.find("*/", i + 2)
            if j < 0:
                break
            out.append(" ")
            i = j + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def hides(lines, i, member):
    """Whether line i hides members from the build or brings them back: an #if (or its kin) or
    a block comment opened or closed here, around at least one line `member(lines, x)` takes for
    a member. A doc comment or an #if around methods hides nothing that is checked."""
    line = lines[i]
    pp = PREPROC.match(line)
    if pp:
        kind = pp.group(1)
        ways = (1,) if kind.startswith("if") else (-1,) if kind == "endif" else (1, -1)
        for step in ways:
            depth, x = 0, i + step
            while 0 <= x < len(lines) and abs(x - i) <= 400:
                k = PREPROC.match(lines[x])
                k = k and k.group(1)
                if k and (k.startswith("if") if step > 0 else k == "endif"):
                    depth += 1
                elif k and (k == "endif" if step > 0 else k.startswith("if")):
                    if not depth:
                        break
                    depth -= 1
                elif not k and member(lines, x):
                    return True
                x += step
        return False
    code = re.sub(r'"(?:\\.|[^"\\])*(?:"|\\?$)', "", line)
    cut = code.find("//")
    if cut >= 0 and not 0 <= code.find("/*") < cut:
        code = code[:cut]
    first_open, first_close = code.find("/*"), code.find("*/")
    if code.rfind("/*") > code.rfind("*/"):
        step, end = 1, "*/"
    elif first_close >= 0 and not 0 <= first_open < first_close:
        step, end = -1, "/*"
    else:
        return False
    x = i + step
    while 0 <= x < len(lines) and end not in lines[x]:
        if member(lines, x) or abs(x - i) > 400:
            return True
        x += step
    return False


def brace_scopes(lines):
    """For each position 0..len(lines), the index of the line opening the innermost {} block
    the position is in (-1: none). Strings and comments are skipped roughly."""
    stack, out = [], []
    for i, line in enumerate(lines):
        out.append(stack[-1] if stack else -1)
        code = code_of(line, strings=False)
        for ch in code:
            if ch == "{":
                stack.append(i)
            elif ch == "}" and stack:
                stack.pop()
    out.append(stack[-1] if stack else -1)
    return out


def header_at(lines, i):
    """The declaration a block opened on line i belongs to, with the attribute lines above it:
    (declaration line, attribute text)."""
    if i < 0:
        return "", ""
    k = i
    if lines[k].strip().startswith("{") and k > 0:
        k -= 1
        while k > 0 and (not lines[k].strip() or re.match(r"^\s*(:|,|where\b)", lines[k]) or
                         lines[k - 1].rstrip().endswith((":", ",")) and not COMMENT.match(lines[k - 1])
                         and not re.match(r"^\s*(public|private|protected)(\s+(slots|Q_SLOTS))?\s*:\s*$", lines[k - 1])
                         and not re.search(r"\b(enum|class|struct|record|interface|union)\s+\w",
                                           code_of(lines[k]))):
            k -= 1
    attrs, a = [], k - 1
    while a >= 0 and re.match(r"^\s*(\[|@)", lines[a]) and len(attrs) < 8:
        attrs.append(lines[a])
        a -= 1
    return lines[k], " ".join(attrs) + " " + lines[k]


def py_header(lines, pos, indent):
    """The innermost Python block a line with this indent at this position is in."""
    if indent == 0:
        return -1, ""
    for k in range(min(pos, len(lines)) - 1, -1, -1):
        line = lines[k]
        if line.strip() and not line.lstrip().startswith("#") and \
                len(line) - len(line.lstrip()) < indent:
            return k, line
    return -1, ""


def moved_pairs(changed, py=False):
    """The changed lines that only gained or lost a trailing comma or whitespace in place (the
    comma an append puts after the old last member): not changes. Lines pair up in order within
    one run of removed lines and the added lines right after it, so a reorder is still a change.
    A line that gained members at its end (`A;` -> `A, B;`) pairs only its removed half: the added
    one is still checked as an insertion."""
    def norm(body):
        return code_of(body, py).rstrip(string.whitespace + ",;").strip()
    out, i = set(), 0
    while i < len(changed):
        gone, came = [], []
        while i < len(changed) and changed[i][0] == "-" and \
                (not gone or changed[i][2] == changed[gone[-1]][2] + 1):
            gone.append(i)
            i += 1
        while i < len(changed) and changed[i][0] == "+" and (
                changed[i][3] == changed[came[-1]][3] + 1 if came else
                not gone or changed[i][2:] == (changed[gone[-1]][2] + 1, changed[gone[-1]][3])):
            came.append(i)
            i += 1
        for g, c in zip(gone, came):
            a, b = norm(changed[g][1]), norm(changed[c][1])
            if a == b:
                out |= {g, c}
            elif b.startswith(a + ","):
                out.add(g)
    return out


def one_line_append(body, changed):
    """Whether a one-line enum only gained members at its end in an added line of the hunk
    (or any members, for Swift's CodingKeys)."""
    head, _, rest = body.partition("{")

    def members(text):
        text = re.sub(r"\bcase\b", "", text.partition("}")[0])
        return [m.strip() for m in text.split(",") if m.strip()]
    old = members(rest)
    for t, b, *_ in changed:
        if t == "+" and b.partition("{")[0].strip() == head.strip():
            new = members(b.partition("{")[2])
            keys = re.search(r"\bCodingKeys\b", head)
            return new[:len(old)] == old or bool(keys) and set(old) <= set(new)
    return False


def next_code(lines, i):
    """The first line from index i on that is not blank or a comment, or None."""
    while i < len(lines):
        if not COMMENT.match(lines[i]):
            return lines[i]
        i += 1
    return None


def memo(ctx, key, make):
    """ctx[key], made once per file: the checks run per hunk and must not rescan the file."""
    if key not in ctx:
        ctx[key] = make()
    return ctx[key]


def enum_risk(f, hunks, old, new, ctx):
    """Enum members removed, changed, reordered or inserted anywhere but the end, and a changed
    declaration (name, underlying type): what renumbers values that saves, packets and other
    builds still hold. Appending, an enum that is only a namespace (no `case` in Swift) and keys
    added to `CodingKeys` are not raised."""
    path = f["new"] or f["old"]
    ext = ext_of(path)
    if ext not in BRACE_EXT and ext != "py":
        return []
    lang = "py" if ext == "py" else "swift" if ext == "swift" else ""
    member = ENUM_MEMBER[lang]
    scopes = memo(ctx, "old_scopes", lambda: brace_scopes(old)) if lang != "py" else None

    def enum_of(line, pos):
        if lang == "py":
            k, head = py_header(old, pos, len(line) - len(line.lstrip()))
            return (k, head) if PY_ENUM.match(head) else None
        k = scopes[min(pos, len(old))]
        head, _ = header_at(old, k)
        return (k, head) if ENUM_DECL.match(head) else None

    def is_member(lines, x):  # also members ENUM_MEMBER cannot read: `B = FLAG(2, 0),`
        line = lines[x]
        return line.strip() not in ("{", "}", "};") and (bool(member.match(line)) or lang != "swift" and
                                                        re.match(r"^\s*[A-Za-z_]\w*\s*([(=,;]|$)", line) is not None)

    for h in hunks:
        changed = line_positions(h)
        same = moved_pairs(changed, lang == "py")
        for j, (t, body, o, n) in enumerate(changed):
            if lang != "py" and enum_of(body, o) and \
                    hides(*((new, n) if t == "+" else (old, o)), is_member):
                return [("enum", short(enum_of(body, o)[1]))]
            if COMMENT.match(body) or j in same:
                continue
            if t == "-" and lang == "py" and PY_ENUM.match(body):  # renamed, or another base
                return [("enum", short(body))]
            if t == "-" and ENUM_DECL.match(body):
                if "{" in body and "}" in body:  # a one-line enum
                    if (lang != "swift" or "case" in body) and not one_line_append(body, changed):
                        return [("enum", short(body))]
                    continue
                if lang != "swift" or ":" in body:  # renamed, or another underlying type
                    return [("enum", short(body))]
            e = enum_of(body, o)
            if not e or not member.match(body) or body.strip() in ("{", "}"):
                continue
            name = re.search(r"\b(enum|class)\s+(\w+)", e[1])
            if t == "-":
                return [("enum", short(e[1]))]
            if name and name.group(2) == "CodingKeys":
                continue
            # What follows the insertion once the lines this hunk adds are skipped: the end of
            # the members, or an old member it went in front of.
            added = {x[3] for x in changed if x[0] == "+"}
            x, prev = n + 1, body
            while x < len(new) and (x in added or COMMENT.match(new[x])):
                prev = new[x] if not COMMENT.match(new[x]) else prev
                x += 1
            after = new[x] if x < len(new) else None
            if lang == "py":
                ends = after is None or \
                    len(after) - len(after.lstrip()) <= len(e[1]) - len(e[1].lstrip()) or \
                    re.match(r"^\s*(def|@|class)\b", after)
            else:
                ends = after is None or after.strip().startswith("}") or \
                    lang == "swift" and not member.match(after) or \
                    re.sub(r"\s*(//.*|/\*.*)$", "", prev).endswith(";")  # Java: members end
            if not ends:
                return [("enum", short(e[1]))]
    return []


def field_at(lines, i):
    """Whether line i declares a field or a property. A declaration the line leaves open counts
    when an accessor block or an initializer follows, not `=> ...` (computed, not stored)."""
    m = MP_FIELD.match(lines[i])
    if not m or m.group("open") is None:
        return bool(m)
    after = next_code(lines, i + 1)
    return after is not None and re.match(r"\s*(\{|;|=(?!>))", after) is not None


def included(lines, i):
    """Whether the member on line i carries a MemoryPack attribute (`[MemoryPackInclude]`) on
    its line or the attribute lines right above it."""
    a = i
    while a >= 0 and (a == i or lines[a].lstrip().startswith("[")):
        if MP_ATTR.search(lines[a]):
            return True
        a -= 1
    return False


def union_owner(lines, i):
    """The name of the type a `[MemoryPackUnion]` on line i is attached to: the first
    declaration from that line on, past attribute and comment lines."""
    for line in lines[i:i + 40]:
        m = MP_TYPE.search(line)
        if m:
            return m.group(3)
        if not (MP_UNION.search(line) or COMMENT.match(line) or line.lstrip().startswith("[")):
            return None
    return None


def memorypack_risk(f, hunks, old, new, ctx):
    """MemoryPack layout of an existing type changed: a member removed, rewritten or reordered
    (order, type, name), one inserted before the last, an order or union tag of that type reused,
    `[MemoryPackable]` itself changed. A new type and edits in comments are not raised."""
    if ext_of(f["new"] or f["old"]) != "cs" or \
            not memo(ctx, "has_mp", lambda: any("MemoryPack" in x for x in old)):
        return []
    scopes = memo(ctx, "old_scopes", lambda: brace_scopes(old))
    new_scopes = memo(ctx, "new_scopes", lambda: brace_scopes(new))

    def packable(pos):
        k = scopes[min(pos, len(old))]
        head, full = header_at(old, k)
        return (k, head) if "MemoryPackable" in full and \
            re.search(r"\b(class|struct|record|interface)\b", head) else None

    def tags():
        orders, unions = {}, {}
        for i, line in enumerate(old):
            m = MP_ORDER.search(line)
            if m:
                orders.setdefault(scopes[i], []).append(int(m.group(1)))
            m = MP_UNION.search(line)
            if m:
                unions.setdefault(union_owner(old, i), []).append(int(m.group(1)))
        return orders, unions
    orders, unions = memo(ctx, "mp_tags", tags)
    old_types = memo(ctx, "mp_types", lambda: {m.group(3) for m in map(MP_TYPE.search, old) if m})

    def is_member(lines, x):
        return field_at(lines, x) or bool(MP_ORDER.search(lines[x]) or MP_ATTR.search(lines[x]))

    def is_new_type(k):  # a block this hunk opens for a type the base side does not have
        m = k in added and MP_TYPE.search(new[k])
        return k in added and not (m and m.group(3) in old_types)

    for h in hunks:
        changed = line_positions(h)
        same = moved_pairs(changed)
        added = {x[3] for x in changed if x[0] == "+"}
        for j, (t, body, o, n) in enumerate(changed):
            side, at = (new, n) if t == "+" else (old, o)
            if packable(o) and hides(side, at, is_member):
                return [("memorypack", short(body))]
            if COMMENT.match(body) or body.strip() in ("{", "}") or j in same:
                continue
            m = MP_UNION.search(body)
            if t == "-" and (m or re.search(r"\bMemoryPackable\b", body)):
                return [("memorypack", short(body))]
            if m:
                owner = union_owner(new, n)
                if int(m.group(1)) <= max(unions.get(owner, []) if owner else [], default=-1):
                    return [("memorypack", short(body))]
            cls = packable(o)
            if not cls or t == "+" and is_new_type(new_scopes[n]):  # inside a type this hunk adds
                continue
            if MP_PRIVATE.match(body) and not included(side, at):
                continue  # not serialized without [MemoryPackInclude]
            if t == "-" and (MP_ORDER.search(body) or MP_ATTR.search(body) or field_at(old, o)):
                return [("memorypack", short(body))]
            if t == "-":
                continue
            m = MP_ORDER.search(body)
            if m and int(m.group(1)) <= max(orders.get(cls[0], []), default=-1):
                return [("memorypack", short(body))]
            if MP_ATTR.search(body):
                return [("memorypack", short(body))]
            if not orders.get(cls[0]) and field_at(new, n):
                k = new_scopes[n]  # the type's block in the new side; its own fields only
                for x in range(n + 1, len(new)):
                    if new_scopes[x] < k:
                        break
                    if new_scopes[x] == k and x not in added and field_at(new, x) and \
                            not COMMENT.match(new[x]):
                        return [("memorypack", short(body))]
    return []


def same_json(old, new):
    """Whether two JSON texts hold the same value, keys in the same order: only reformatted."""
    def load(lines):  # numbers kept as text: in Python true == 1 == 1.0
        return json.loads("\n".join(lines), object_pairs_hook=list,
                          parse_int=lambda s: (int, s), parse_float=lambda s: (float, s))
    try:
        return new is not None and load(old) == load(new)
    except (ValueError, RecursionError):
        return False


def checks_off(lines):
    """The safety checks the file switches off for a class or the whole file, as text."""
    found = []
    for line in lines:
        for m in CHECKS_OFF.finditer(line):
            what = m.group(1) or m.group(2) or m.group(5)
            if what not in found:
                found.append(what)
    return found


def is_noise(path, patterns):
    """gitignore-like globs: a pattern without `/` matches a file name at any depth; one with `/`
    (or a leading `/`) matches from the repository root; a leading `**/` lets it start at any
    directory; a trailing `/` means a directory and everything under it."""
    parts = path.split("/")
    for p in patterns:
        is_dir = p.endswith("/")
        anywhere = p.startswith("**/")
        body = (p[3:] if anywhere else p).strip("/")
        rooted = not anywhere and (p.startswith("/") or "/" in body)
        names = parts[:-1] if is_dir else parts
        if not rooted and "/" not in body:
            candidates = names if is_dir else [parts[-1]]
        else:
            starts = [0] if rooted else range(len(names))
            ends = range(1, len(names) + 1) if is_dir else [len(names)]
            candidates = ["/".join(names[i:k]) for i in starts for k in ends if k > i]
        if any(fnmatch.fnmatchcase(c, body) for c in candidates):
            return True
    return False


def make_units(files, patterns, risk=(), base=None):
    """One unit per hunk, or per file without hunks; a noise file is one unit, and the risk
    rules still read all of it. `base` ({old path: text}) feeds the checks that read the file;
    without it they are skipped."""
    rules, _ = risk_rules(risk)
    units, h, n = [], 0, 0
    for fi, f in enumerate(files):
        f["noise"] = is_noise(f["new"] or f["old"], patterns)
        old = text_lines(base[f["old"]]) if base and f["old"] in base else None
        new = apply_hunks(old, f["hunks"]) if old is not None else \
            [b for hk in f["hunks"] for t, b in hk["lines"] if t == "+"] \
            if f["status"] == "A" else None
        f["checks_off"] = checks_off(new) if new and ext_of(f["new"] or "") == "cs" else []
        f["format_only"] = old is not None and ext_of(f["new"] or "") in JSON_EXT and \
            f["new"] == f["old"] and same_json(old, new)

        ctx = {}

        def risk_of(hunks):
            if f["format_only"]:
                return []
            out = unit_risk(f, hunks, rules)
            if old is not None and new is not None:  # None: the hunks do not fit this base
                out += [x for x in enum_risk(f, hunks, old, new, ctx) +
                        memorypack_risk(f, hunks, old, new, ctx) if x not in out]
            return out
        if f["noise"]:
            n += 1
            units.append({"id": f"n{n}", "file": fi, "hunk": None, "risk": risk_of(f["hunks"])})
        elif not f["hunks"]:
            h += 1
            units.append({"id": f"h{h}", "file": fi, "hunk": None, "risk": risk_of([])})
        else:
            for hi, hunk in enumerate(f["hunks"]):
                h += 1
                units.append({"id": f"h{h}", "file": fi, "hunk": hi, "risk": risk_of([hunk])})
    return units


def load_snapshot(run_dir):
    """Re-parse patch.diff, after checking it is the file collect wrote. The noise patterns and
    risk rules are read again from the base, not taken from hunks.json, which anyone can edit."""
    try:
        with open(os.path.join(run_dir, "hunks.json"), encoding="utf-8") as fh:
            meta = json.load(fh)["meta"]
        with open(os.path.join(run_dir, "patch.diff"), "rb") as fh:
            patch = fh.read()
    except (OSError, ValueError, KeyError) as e:
        raise Fail(f"{run_dir} is not a diff-tour run directory ({e})") from None
    if sha256(patch) != meta["sha256"]:
        raise Fail("patch.diff was modified after collect — rerun collect")
    files = parse(patch.decode("utf-8", "replace"))
    try:
        patterns, risk = rules_at_base(meta["root"], meta["spec"])
        base = base_texts(meta["root"], meta["spec"], files)
    except (Fail, OSError) as e:
        raise Fail(f"cannot re-read the noise and risk rules at the base: {e}") from None
    return meta, files, make_units(files, patterns, risk, base)


# --- unit descriptions -------------------------------------------------------------------

def facts(f):
    """What the file header says, as (key, args) pairs — localized by the caller."""
    out = []
    if f["noise"]:
        out.append(("noise", {}))
    if f["status"] == "A":
        out.append(("new", {}))
    elif f["status"] == "D":
        out.append(("deleted", {}))
    elif f["status"] in ("R", "C"):
        out.append(("renamed", {"old": f["old"], "pct": f["similarity"]}))
    if f["old_mode"] and f["new_mode"] and f["status"] not in ("A", "D"):
        out.append(("mode", {"a": f["old_mode"], "b": f["new_mode"]}))
    if f["binary"]:
        out.append(("binary", {}))
    elif not f["hunks"] and f["status"] in ("A", "D"):
        out.append(("empty", {}))
    if f.get("format_only"):
        out.append(("format_only", {}))
    if f.get("checks_off"):
        out.append(("checks_off", {"what": ", ".join(f["checks_off"])}))
    return out


def unit_path(f):
    return f["new"] or f["old"]


def unit_src(files, u):
    f = files[u["file"]]
    return f if u["hunk"] is None else f["hunks"][u["hunk"]]


INDEX_FACTS = {"noise": "noise", "new": "new file", "deleted": "deleted",
               "renamed": "renamed from {old} ({pct}%)", "mode": "mode {a} -> {b}",
               "binary": "binary", "empty": "empty file",
               "format_only": "only reformatted: the same JSON",
               "checks_off": "checks switched off in this file: {what}"}


def index_entry(files, u):
    f = files[u["file"]]
    src = unit_src(files, u)
    where = ""
    if u["hunk"] is not None:
        where = (src["header"] + " " + src["section"]).strip()
    tags = ", ".join(INDEX_FACTS[k].format(**a) for k, a in facts(f))
    return {"id": u["id"], "status": f["status"], "path": unit_path(f), "where": where,
            "facts": tags, "risk": [f"{k}: {d}" for k, d in u["risk"]],
            "added": src["added"], "removed": src["removed"], "line": src["line"]}


# --- collect -----------------------------------------------------------------------------

RUN_NAME = re.compile(r"(\d{8}-\d{6})(?:-(\d+))?$")


def private_dir(path):
    """mkdir with mode 0700 — the snapshots hold source code."""
    if not os.path.isdir(path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:  # a parallel collect made it first
            pass


def new_run_dir(out_root, repo):
    private_dir(out_root)
    if os.path.abspath(out_root) == OUT_ROOT:
        os.chmod(out_root, 0o700)  # a cache dir made by an older version was 0755
    repo_dir = os.path.join(out_root, re.sub(r"[^\w.-]+", "_", repo) or "repo")
    private_dir(repo_dir)
    base = os.path.join(repo_dir, datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    path, k = base, 1
    while True:
        try:
            os.mkdir(path, 0o700)
            return path
        except FileExistsError:
            k += 1
            path = f"{base}-{k}"


def prune(repo_dir, keep=KEEP_RUNS):
    """Delete all but the newest `keep` run directories in repo_dir. Only directories that
    collect made (a timestamp name and a hunks.json) are touched."""
    runs = []
    for name in os.listdir(repo_dir):
        m = RUN_NAME.match(name)
        if m and os.path.isfile(os.path.join(repo_dir, name, "hunks.json")):
            runs.append(((m.group(1), int(m.group(2) or 1)), name))
    runs.sort()
    for _, name in runs[:max(len(runs) - keep, 0)]:
        shutil.rmtree(os.path.join(repo_dir, name), ignore_errors=True)


def base_label(spec):
    if spec["mode"] == "range":
        return spec["range"]
    if spec["ref"] is None:
        return "empty tree -> working tree + untracked"
    if spec.get("tip") and spec["sha"] != spec["tip"]:
        return (f"merge-base of {spec['ref']} and HEAD ({spec['sha'][:9]}) -> working tree "
                f"+ untracked")
    return f"{spec['ref']} ({spec['sha'][:9]}) -> working tree + untracked"


def collect(arg, out_root):
    root = toplevel(os.getcwd())
    spec = resolve(root, arg)
    patch, skipped = take(root, spec)
    fallback, unrelated = None, []
    if not patch.strip() and not arg:
        fallback, unrelated = after_commit(root, spec)
        if fallback:
            spec, patch, skipped = fallback
    if not patch.strip():
        print("No changes.")
        if unrelated:
            print(f"HEAD shares no history with {', '.join(unrelated)}, so nothing is shown — "
                  "pass a ref or range to compare with (e.g. `collect <ref>` or "
                  "`collect A..B`).")
        for path in skipped:
            print(f"Skipped untracked nested repository: {path}")
        return 0
    files = parse(patch.decode("utf-8", "replace"))
    if not files:
        first = patch.decode("utf-8", "replace").strip().splitlines()[0]
        raise Fail(f"git printed a diff diff-tour cannot read (it starts with {first!r})")
    patterns, risk = rules_at_base(root, spec)
    _, bad_rules = risk_rules(risk)
    units = make_units(files, patterns, risk, base_texts(root, spec, files))
    repo = os.path.basename(root)
    run_dir = new_run_dir(out_root, repo)
    with open(os.path.join(run_dir, "patch.diff"), "wb") as fh:
        fh.write(patch)
    # old_rev / new_rev: where a reviewer reads a file's context (new_rev None: the working tree)
    meta = {"root": root, "repo": repo, "branch": branch(root), "spec": spec,
            "old_rev": base_of(root, spec), "new_rev": new_of(spec),
            "patterns": patterns, "risk": risk, "skipped": skipped, "sha256": sha256(patch),
            "collected": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
    index = [index_entry(files, u) for u in units]
    with open(os.path.join(run_dir, "hunks.json"), "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "meta": meta, "units": index}, fh, ensure_ascii=False, indent=1)
    # index.txt: hunks.json in a form a reviewer can read whole on a diff of thousands of units
    table = index_table(index)
    with open(os.path.join(run_dir, "index.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"root: {root}\nold_rev: {meta['old_rev']}\n"
                 f"new_rev: {meta['new_rev'] or '(working tree)'}\nunits: {len(index)}\n\n")
        fh.write("".join(line + "\n" for line in table))
    prune(os.path.dirname(run_dir))

    noisy = [f for f in files if f["noise"]]
    plain_lines = sum(f["added"] + f["removed"] for f in files if not f["noise"])
    print(f"Run directory: {run_dir}")
    print(f"Base: {base_label(spec)}")
    if fallback:
        print(f"Nothing uncommitted — showing the commits HEAD has beyond {spec['ref']} instead.")
    line = (f"{len(files)} files, {len(units)} units, "
            f"+{sum(f['added'] for f in files)} -{sum(f['removed'] for f in files)}")
    if noisy:
        line += (f" (noise: {len(noisy)} files, +{sum(f['added'] for f in noisy)}"
                 f" -{sum(f['removed'] for f in noisy)})")
    print(line)
    risky = [u for u in units if u["risk"]]
    if risky:
        print(f"Risk rules (built-in + {RISK_FILE} at the base): {len(risky)} units")
    for r in bad_rules:
        print(f"WARNING: {RISK_FILE}: not a valid rule, skipped: {r!r}")
    patch_lines = patch.count(b"\n")
    print(f"patch.diff: {patch_lines} lines")
    for path in skipped:
        print(f"Skipped untracked nested repository: {path}")
    if plain_lines > BIG_DIFF:
        print(f"WARNING: {plain_lines} changed lines outside noise — consider a narrower ref "
              f"or noise patterns in {NOISE_FILE}")
    print()
    for line in table:
        print(line)
    return 0


def index_table(index):
    """One line per unit: id, status, path, where, facts, risk, size and its line in
    patch.diff — what collect prints and index.txt holds."""
    width = max(len(e["path"]) for e in index)
    out = []
    for e in index:
        tags = f"[{e['facts']}]" if e["facts"] else ""
        if e["risk"]:
            tags += f" [risk {'; '.join(e['risk'])}]"
        text = " ".join(x for x in (e["where"], tags) if x)
        out.append(f"{e['id']:<5} {e['status']}  {e['path']:<{width}}  {text}  "
                   f"+{e['added']} -{e['removed']}  L{e['line']}")
    return out


# --- notes.json --------------------------------------------------------------------------

TOP_KEYS = {"lang", "title", "lede", "link", "panels", "files", "notes", "unexplained"}
NOTE_KEYS = {"unit", "after", "side", "text", "source", "kind"}
FILE_KINDS = ("prod", "test", "docs", "config", "meta")
SIDES = ("old", "new")


def text_ok(v):
    return isinstance(v, str) and v.strip() != ""


def unit_hunks(files, u):
    f = files[u["file"]]
    if u["hunk"] is None:
        return list(enumerate(f["hunks"]))
    return [(u["hunk"], f["hunks"][u["hunk"]])]


def anchor_matches(files, u, after, side=None):
    """(hunk, line) positions in unit u whose text contains `after`."""
    found = []
    for hi, h in unit_hunks(files, u):
        for li, (tag, text) in enumerate(h["lines"]):
            # side narrows to changed lines only: "old" removed, "new" added. Context lines
            # match only without side — that is how a duplicate next to its original is picked.
            if tag == "\\" or (side == "old" and tag != "-") or (side == "new" and tag != "+"):
                continue
            if after in text:
                found.append((hi, li))
    return found


def diff_paths(files):
    """Every path the diff names (new and old) → file index."""
    paths = {}
    for fi, f in enumerate(files):
        for p in (f["new"], f["old"]):
            if p:
                paths.setdefault(p, fi)
    return paths


def validate_panels(panels, errs):
    if not isinstance(panels, list):
        errs.append("panels: a list")
        return
    for k, p in enumerate(panels, 1):
        at = f"panels[{k}]"
        if (not isinstance(p, dict) or not text_ok(p.get("title"))
                or set(p) - {"title", "text", "checks"} or len(set(p) & {"text", "checks"}) != 1):
            errs.append(f"{at}: an object with title and exactly one of text, checks")
            continue
        if "text" in p and not text_ok(p["text"]):
            errs.append(f"{at}.text: a non-empty string")
        if "checks" in p:
            if not isinstance(p["checks"], list) or not p["checks"]:
                errs.append(f"{at}.checks: a non-empty list")
                continue
            for j, c in enumerate(p["checks"], 1):
                if not (isinstance(c, dict) and not set(c) - {"pill", "text", "warn"}
                        and text_ok(c.get("pill")) and text_ok(c.get("text"))
                        and isinstance(c.get("warn", False), bool)):
                    errs.append(f"{at}.checks[{j}]: an object {{pill, text, warn?: true|false}}")


def validate_files(entries, files, errs):
    if not isinstance(entries, list):
        errs.append("files: a list")
        return
    paths, seen = diff_paths(files), set()
    for k, e in enumerate(entries, 1):
        at = f"files[{k}]"
        if not isinstance(e, dict) or set(e) - {"path", "role", "kind"} or "path" not in e:
            errs.append(f"{at}: an object {{path, role?, kind?}}")
            continue
        if not isinstance(e["path"], str):
            errs.append(f"{at}.path: a string")
            continue
        if e["path"] not in paths:
            errs.append(f"{at}.path: {e['path']!r} is not in the diff")
        elif paths[e["path"]] in seen:
            errs.append(f"{at}.path: {e['path']!r} is listed twice")
        else:
            seen.add(paths[e["path"]])
        if "role" in e and not text_ok(e["role"]):
            errs.append(f"{at}.role: a non-empty string")
        if "kind" in e and e["kind"] not in FILE_KINDS:
            errs.append(f"{at}.kind: one of {', '.join(FILE_KINDS)}")


def validate(notes, files, units):
    """Every problem in notes.json, as readable lines. A unit without notes is not a problem —
    it is shown as unexplained."""
    if not isinstance(notes, dict):
        return ["the top level must be a JSON object"]
    errs = []
    by_id = {u["id"]: u for u in units}
    extra = set(notes) - TOP_KEYS
    if extra:
        errs.append(f"unknown top-level keys: {', '.join(sorted(extra))}")
    if "lang" in notes and not text_ok(notes["lang"]):
        errs.append("lang: a language code such as \"ru\" or \"en\"")
    for key in ("title", "lede"):
        if not text_ok(notes.get(key)):
            errs.append(f"{key}: required, a non-empty string")
    link = notes.get("link")
    if link is not None and not (
            isinstance(link, dict) and set(link) == {"url", "label"} and text_ok(link["label"])
            and isinstance(link["url"], str) and re.match(r"https?://\S+$", link["url"])):
        errs.append('link: {"url": "https://…", "label": "…"} — http(s) only')
    validate_panels(notes.get("panels", []), errs)
    validate_files(notes.get("files", []), files, errs)

    noted = set()
    items = notes.get("notes")
    if not isinstance(items, list):
        errs.append("notes: required, a list (may be empty)")
        items = []
    for k, n in enumerate(items, 1):
        at = f"notes[{k}]"
        if not isinstance(n, dict):
            errs.append(f"{at}: must be an object")
            continue
        extra = set(n) - NOTE_KEYS
        if extra:
            errs.append(f"{at}: unknown keys: {', '.join(sorted(extra))}")
        uid = n.get("unit")
        u = by_id.get(uid) if isinstance(uid, str) else None
        if u is None:
            errs.append(f"{at}.unit: unknown unit id {uid!r}")
        else:
            noted.add(uid)
        if not text_ok(n.get("text")):
            errs.append(f"{at}.text: required, a non-empty string")
        if n.get("source") not in SOURCES:
            errs.append(f"{at}.source: one of {', '.join(SOURCES)}")
        if "kind" in n and n["kind"] not in KINDS:
            errs.append(f"{at}.kind: one of {', '.join(KINDS)}")
        if "side" in n and (n["side"] not in SIDES or "after" not in n):
            errs.append(f"{at}.side: \"old\" or \"new\", and only together with after")
        if "after" in n:
            if not text_ok(n["after"]) or "\n" in n["after"]:
                errs.append(f"{at}.after: a non-empty piece of one line's text")
            elif u is not None:
                hits = anchor_matches(files, u, n["after"], n.get("side"))
                if not hits:
                    errs.append(f"{at}.after: no line of {uid} contains {n['after']!r}")
                elif len(hits) > 1:
                    hint = "" if n.get("side") else ", or add side"
                    errs.append(f"{at}.after: {len(hits)} lines of {uid} contain "
                                f"{n['after']!r} — quote more of the line{hint}")
    loose = notes.get("unexplained", {})
    if not isinstance(loose, dict):
        errs.append("unexplained: an object {unit id: what changed}")
        loose = {}
    for uid, what in loose.items():
        if uid not in by_id:
            errs.append(f"unexplained: unknown unit id {uid!r}")
        elif uid in noted:
            errs.append(f"unexplained: {uid} has notes, so it is explained — drop one or "
                        f"the other")
        if not isinstance(what, str):
            errs.append(f"unexplained.{uid}: a string (what changed, not why)")
    return errs


# --- triage ------------------------------------------------------------------------------

TRIAGE_KEYS = {"units_seen", "suspects", "risks", "cleared"}
TRIAGE_ITEMS = (("suspects", "text"), ("risks", "check"), ("cleared", "why"))


TRIAGE_MIN = 2  # reviewers the triage step runs; build waits for all of them


def load_triage(run_dir, units):
    """Every triage-<name>.json a fresh reviewer wrote, as [{name, units_seen, suspects, risks, cleared}],
    and the problems in them. Each reviewer has a file of its own; nothing is merged away."""
    ids = {u["id"] for u in units}
    ruled = {u["id"] for u in units if u["risk"]}
    reviews, errs = [], []
    names = sorted(fn for fn in os.listdir(run_dir)
                   if fn.startswith("triage-") and fn.endswith(".json"))
    if 0 < len(names) < TRIAGE_MIN:
        errs.append(f"{len(names)} triage file(s) ({', '.join(names)}) — the triage step runs "
                    f"{TRIAGE_MIN} reviewers; wait for the others")
    for fn in names:
        try:
            with open(os.path.join(run_dir, fn), encoding="utf-8") as fh:
                t = json.load(fh)
        except (OSError, ValueError, RecursionError) as e:
            errs.append(f"{fn}: not valid JSON ({e})")
            continue
        if not isinstance(t, dict):
            errs.append(f"{fn}: must be an object")
            continue
        for k in sorted(set(t) - TRIAGE_KEYS):
            errs.append(f"{fn}: unknown key {k!r}")
        seen = t.get("units_seen")
        if not isinstance(seen, int) or isinstance(seen, bool) or not 0 <= seen <= len(units):
            errs.append(f"{fn}: units_seen must be the number of units the reviewer read "
                        f"(0–{len(units)})")
        for key, field in TRIAGE_ITEMS:
            items = t.get(key, [])
            if not isinstance(items, list):
                errs.append(f"{fn}: {key} must be a list")
                continue
            for i, it in enumerate(items):
                if not isinstance(it, dict) or not isinstance(it.get("unit"), str) \
                        or it["unit"] not in ids:
                    errs.append(f"{fn}: {key}[{i}]: unit must be an id from index.txt")
                elif not text_ok(it.get(field)):
                    errs.append(f"{fn}: {key}[{i}]: {field} must be non-empty text")
                elif key == "cleared" and it["unit"] not in ruled:
                    errs.append(f"{fn}: cleared[{i}]: {it['unit']} was not raised by a risk "
                                f"rule — cleared is only for those")
        reviews.append({"name": fn[len("triage-"):-len(".json")], "units_seen": seen,
                        "suspects": t.get("suspects", []), "risks": t.get("risks", []),
                        "cleared": t.get("cleared", [])})
    return reviews, errs


def queue(units, notes, reviews):
    """(suspects, reasons): suspects are units a reviewer named a concrete failure in, as
    {unit id: [(reviewer, text)]}; reasons are why any unit is worth a look whatever the
    reviewers think — the script's risk rules, a reviewer's risk list, the author's own flags,
    and a reviewer's word on why a rule's hit is safe — as {unit id: [(source, key, text)]}, the
    same text from several reviewers once with their names joined. Only the script folds: a
    unit leaves the queue when nobody and no rule raised it; nothing raised can be dropped here,
    a `cleared` line included. Having a note or not moves no unit either way: on a big diff
    most units have none, and a note is the author's word, not a check."""
    suspects, reasons = {}, {}
    for r in reviews:
        for s in r["suspects"]:
            suspects.setdefault(s["unit"], []).append((r["name"], s["text"]))
    for u in units:
        for k, d in u["risk"]:
            reasons.setdefault(u["id"], []).append(("script", k, d))
    for src, key, field in (("cleared", "cleared", "why"), ("reviewer", "risks", "check")):
        said = {}
        for r in reviews:
            for it in r.get(key, []):
                said.setdefault((it["unit"], it[field]), []).append(r["name"])
        for (uid, text), who in said.items():
            reasons.setdefault(uid, []).append((src, ", ".join(who), text))
    for n in notes["notes"]:
        flag = ("author", n.get("kind"), "")
        if n.get("kind") and flag not in reasons.get(n["unit"], []):
            reasons.setdefault(n["unit"], []).append(flag)
    return suspects, reasons


# --- moved blocks ------------------------------------------------------------------------

MOVED_MIN = 3         # lines in a block before it is shown as moved
MOVED_MEANINGFUL = 2  # of them with 3+ visible characters: `}`, `{` and blanks alone are chance


def moved_lines(fi, f):
    """(file, hunk, line) keys of lines inside a block that was cut and pasted verbatim within
    the file: a run of removed (or added) lines that all reappear on the other side. Exact
    text, so a re-indented block stays a plain deletion and addition, and an edited line breaks
    the run; braces and blank lines ride along (a moved method keeps its closing `}`)."""
    gone, came = set(), set()
    for h in f["hunks"]:
        for tag, text in h["lines"]:
            if tag == "-":
                gone.add(text)
            elif tag == "+":
                came.add(text)
    out = set()
    for hi, h in enumerate(f["hunks"]):
        lines, i = h["lines"], 0
        while i < len(lines):
            tag = lines[i][0]
            if tag not in "+-":
                i += 1
                continue
            other = gone if tag == "+" else came
            j = i
            while j < len(lines) and lines[j][0] == tag and lines[j][1] in other:
                j += 1
            meaningful = sum(1 for x in range(i, j) if len(lines[x][1].strip()) >= 3)
            if j - i >= MOVED_MIN and meaningful >= MOVED_MEANINGFUL:
                out.update((fi, hi, x) for x in range(i, j))
            i = max(j, i + 1)
    return out


# --- page --------------------------------------------------------------------------------

BIG_FILE = 1500     # diff lines in a file with no notes before its card starts folded
SPLIT_MAX = 8000    # diff lines on the page above which only the unified view is rendered

LABELS = {
    "en": {
        "unexplained": "Unexplained", "no_what": "no description", "selfcheck": "Self-check",
        "loose_line": "unexplained", "flags_line": "to check by eye",
        "inferred_line": "inferred from code, not from the conversation",
        "stale": "stale", "stale_line": "the working tree or HEAD changed after this snapshot",
        "inferred": "inferred from code", "uncommitted": "uncommitted", "range": "range",
        "branch": "branch since the base + uncommitted",
        "base_wt": "base {ref} → working tree + untracked",
        "base_mb": "merge-base of {ref} and HEAD ({sha})",
        "base_empty": "empty tree → working tree + untracked",
        "drift": "The working tree or HEAD changed after this snapshot ({time}). The page shows "
                 "the snapshot, not what a commit would contain — rerun the tour before "
                 "committing.",
        "kinds": {"prod": "code", "test": "tests", "docs": "docs", "config": "config",
                  "meta": "service file"},
        "drift_unknown": "Could not re-check the working tree: {reason}",
        "skipped": "Not shown — untracked nested repositories: {paths}.",
        "snapshot": "Snapshot: {dir} — patch.diff, notes.json.",
        "files_nav": "Files", "view": "Diff view", "split": "Side by side", "unified": "Unified",
        "legend": {"add": "added", "del": "removed", "moved": "moved unchanged",
                   "note": "note", "loose": "unexplained"},
        "nonl": "no newline at end of file",
        "flag": {"untested": "Untested", "decision": "Debatable decision",
                 "temporary": "Temporary code", "stray": "Stray change"},
        "fact": {"noise": "noise",
                 "new": "new file", "deleted": "deleted",
                 "renamed": "renamed from {old} ({pct}%)", "mode": "mode {a} → {b}",
                 "binary": "binary", "empty": "empty file",
                 "format_only": "only reformatted", "checks_off": "checks off: {what}"},
        "body": {"binary": "Binary content changed — not shown.",
                 "renamed": "Renamed without content changes.",
                 "mode": "Only the file mode changed.", "empty": "Empty file."},
        "q": {"title": "What to look at", "mode": "Page", "queue": "Queue", "all": "Whole diff",
              "verdict": "{s} suspected · {r} worth a look · {k} of {t} units folded",
              "empty": "Nothing to look at: no reviewer and no rule raised any unit.",
              "suspects": "Suspected failures", "risks": "Worth a look",
              "folded": "The other {k} units are folded: neither the reviewers nor the risk "
                        "rules found anything there. They are in the whole diff.",
              "code": "code", "by_all": "all reviewers", "by": "reviewer {name}",
              "partial": "reviewer {name} read only {seen} of {total} units: what it did not "
                         "read was checked by the other reviewers and the rules alone",
              "loose_count": "{n} units have no author note. That alone does not raise them: "
                             "they are listed in the Self-check panel and marked in the whole "
                             "diff.",
              "why": {"path": "path", "line": "line", "section": "inside",
                      "test": "test weakened", "enum": "enum members",
                      "memorypack": "MemoryPack layout"},
              "author": "author: {flag}", "cleared": "why safe"},
    },
    "ru": {
        "unexplained": "Не объяснено", "no_what": "без описания", "selfcheck": "Самопроверка",
        "loose_line": "не объяснено", "flags_line": "проверь глазами",
        "inferred_line": "выведено из кода, а не из разговора",
        "stale": "снимок устарел", "stale_line": "рабочее дерево или HEAD изменились после снимка",
        "inferred": "выведено из кода", "uncommitted": "не закоммичено",
        "range": "диапазон",
        "branch": "ветка от базы + не закоммичено",
        "base_wt": "база {ref} → рабочее дерево + untracked",
        "base_mb": "общий предок {ref} и HEAD ({sha})",
        "base_empty": "пустое дерево → рабочее дерево + untracked",
        "drift": "Рабочее дерево или HEAD изменились после снимка ({time}). Страница показывает "
                 "снимок, а не то, что уйдёт в коммит, — перезапусти тур перед коммитом.",
        "kinds": {"prod": "код", "test": "тесты", "docs": "документация", "config": "конфиг",
                  "meta": "служебный"},
        "drift_unknown": "Не удалось перепроверить рабочее дерево: {reason}",
        "skipped": "Не показаны — неотслеживаемые вложенные репозитории: {paths}.",
        "snapshot": "Снимок: {dir} — patch.diff, notes.json.",
        "files_nav": "Файлы", "view": "Вид диффа", "split": "Рядом", "unified": "Единый",
        "legend": {"add": "добавлено", "del": "удалено", "moved": "перенесено без изменений",
                   "note": "комментарий", "loose": "не объяснено"},
        "nonl": "нет перевода строки в конце файла",
        "flag": {"untested": "Не проверено", "decision": "Спорное решение",
                 "temporary": "Временный код", "stray": "Лишнее в хунке"},
        "fact": {"noise": "шум",
                 "new": "новый файл", "deleted": "удалён",
                 "renamed": "переименован из {old} ({pct}%)", "mode": "режим {a} → {b}",
                 "binary": "бинарный", "empty": "пустой файл",
                 "format_only": "только форматирование",
                 "checks_off": "проверки отключены: {what}"},
        "body": {"binary": "Бинарное содержимое изменено — не показано.",
                 "renamed": "Переименование без изменений содержимого.",
                 "mode": "Изменился только режим файла.", "empty": "Пустой файл."},
        "q": {"title": "Что смотреть", "mode": "Страница", "queue": "Очередь",
              "all": "Весь дифф",
              "verdict": "подозрений: {s} · стоит взглянуть: {r} · свёрнуто {k} из {t} блоков",
              "empty": "Смотреть нечего: ни ревьюеры, ни правила не подняли ни одного блока.",
              "suspects": "Подозрения", "risks": "Стоит взглянуть",
              "folded": "Остальные {k} блоков свёрнуты: ни ревьюеры, ни правила риска там "
                        "ничего не нашли. Они есть во всём диффе.",
              "code": "код", "by_all": "все ревьюеры", "by": "ревьюер {name}",
              "partial": "ревьюер {name} прочёл только {seen} из {total} блоков: остальное "
                         "проверили лишь другие ревьюеры и правила",
              "loose_count": "Без заметки автора: {n} блоков. Сами по себе они в очередь не "
                             "поднимаются — список в «Самопроверке», во всём диффе они "
                             "помечены.",
              "why": {"path": "путь", "line": "строка", "section": "внутри",
                      "test": "тест ослаблен", "enum": "члены enum",
                      "memorypack": "раскладка MemoryPack"},
              "author": "автор: {flag}", "cleared": "почему безопасно"},
    },
}

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def asset(name):
    """An assets/ file as written, minus the one newline that ends the file."""
    with open(os.path.join(ASSETS, name), encoding="utf-8", newline="") as fh:
        text = fh.read()
    return text[:-1] if text.endswith("\n") else text


# page.html is the document shell ($lang, $title, $style, $body, $highlight, $script); page.css
# holds the light theme and %DARK% where dark.css's tokens go (twice: by system preference and
# by an explicit data-theme).
PAGE = string.Template(asset("page.html"))
CSS = asset("page.css").replace("%DARK%", asset("dark.css")) + "\n"
PAGE_JS = asset("page.js") + "\n"
QUEUE_CSS = asset("queue.css") + "\n"
QUEUE_JS = asset("queue.js") + "\n"


def esc(s):
    return html.escape(str(s), quote=True)


def when(iso):
    return datetime.datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")


def prose(s):
    """Escaped text; `code` spans, **bold** (which may contain code) and line breaks are the
    only markup."""
    codes = []

    def stash(m):
        codes.append(m.group(1))
        return f"\ue000{len(codes) - 1}\ue001"

    text = re.sub(r"`([^`\n]+)`", stash, str(s).replace("\ue000", "").replace("\ue001", ""))
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc(text)).replace("\n", "<br>")
    return re.sub("\ue000(\\d+)\ue001", lambda m: f"<code>{esc(codes[int(m.group(1))])}</code>",
                  text)


def plain(s):
    """Text for places without markup (the browser tab): the markers dropped."""
    return re.sub(r"\*\*|`", "", str(s))


def lang_of(path):
    ext = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    return LANGS.get(ext)


def numbered(h):
    """Hunk lines with old/new numbers; a `\\ No newline` line becomes a mark on the line
    before it."""
    rows, o, n = [], h["old_start"], h["new_start"]
    for li, (tag, text) in enumerate(h["lines"]):
        if tag == "\\":
            if rows:
                rows[-1]["nonl"] = True
            continue
        r = {"li": li, "tag": tag, "text": text, "o": "", "n": "", "nonl": False}
        if tag == " ":
            r["o"], r["n"] = o, n
            o, n = o + 1, n + 1
        elif tag == "-":
            r["o"] = o
            o += 1
        else:
            r["n"] = n
            n += 1
        rows.append(r)
    return rows


class Page:
    """Everything the file cards need, resolved once from notes.json."""

    def __init__(self, files, units, notes):
        self.L = LABELS["ru" if str(notes.get("lang", "")).lower().startswith("ru") else "en"]
        self.files, self.units = files, units
        by_id = {u["id"]: u for u in units}
        self.placed, self.top = {}, {}
        for n in notes["notes"]:
            u = by_id[n["unit"]]
            if "after" in n:
                hi, li = anchor_matches(files, u, n["after"], n.get("side"))[0]
                self.placed.setdefault((u["file"], hi, li), []).append(n)
            else:
                self.top.setdefault(u["id"], []).append(n)
        noted = {n["unit"] for n in notes["notes"]}
        self.loose = [u for u in units if u["id"] not in noted]
        self.loose_ids = {u["id"] for u in self.loose}
        self.what = notes.get("unexplained", {})
        self.notes = notes["notes"]
        self.hunk_unit = {(u["file"], u["hunk"]): u for u in units if u["hunk"] is not None}
        self.file_unit = {u["file"]: u for u in units if u["hunk"] is None}
        self.units_of = {}
        for u in units:
            self.units_of.setdefault(u["file"], []).append(u)
        size = sum(len(h["lines"]) for f in files for h in f["hunks"])
        self.highlight = size <= HIGHLIGHT_MAX
        # Side by side only when some file has two sides to show.
        self.split = size <= SPLIT_MAX and any(
            f["hunks"] and f["status"] not in ("A", "D") for f in files)
        self.moved = set()
        for fi, f in enumerate(files):
            self.moved |= moved_lines(fi, f)
        paths = diff_paths(files)
        self.info = {paths[e["path"]]: e for e in notes.get("files", [])}
        self.order = [paths[e["path"]] for e in notes.get("files", [])]
        self.order += [fi for fi in range(len(files)) if fi not in self.order]

    # notes

    def note(self, n):
        L, head, cls = self.L, [], "note"
        if n.get("kind"):
            head.append(f'<span class="pill warn">{esc(L["flag"][n["kind"]])}</span>')
        if n["source"] == "inferred":
            cls += " inferred"
            head.append(f'<span class="src">{esc(L["inferred"])}</span>')
        return f'<div class="{cls}">{"".join(head)}{prose(n["text"])}</div>'

    def loose_note(self, uid):
        what = self.what.get(uid)
        body = prose(what) if what else esc(self.L["no_what"])
        return (f'<div class="note loose"><span class="pill bad">{esc(self.L["unexplained"])}'
                f'</span>{body}</div>')

    def unit_notes(self, u):
        out = [self.note(n) for n in self.top.get(u["id"], [])]
        if u["id"] in self.loose_ids:
            out.append(self.loose_note(u["id"]))
        return out

    @staticmethod
    def rows_of(notes, cols):
        return [f'<tr class="note-row"><td colspan="{cols}">{n}</td></tr>' for n in notes]

    def after(self, fi, hi, r):
        return [self.note(n) for n in self.placed.get((fi, hi, r["li"]), [])]

    # code

    def cell(self, r, cls="", side=None):
        """side: "o"/"n" — which file the line belongs to, "c" — both (unified context); the
        page script highlights each side of a hunk as one text."""
        text = r["text"]
        cr = text.endswith("\r")
        body = (text[:-1] if cr else text).replace("\r", "␍")  # a CR mid-line stays visible
        tail = '<span class="cr" title="CR">␍</span>' if cr else ""
        if r["nonl"]:
            tail += f'<span class="nonl" title="{esc(self.L["nonl"])}"> ⌀</span>'
        side = side or {"-": "o", "+": "n", " ": "c"}[r["tag"]]
        return (f'<td class="code{cls}"><span class="t" data-s="{side}">{esc(body)}</span>'
                f'{tail}</td>')

    def hunk_row(self, fi, hi, h, cols):
        u = self.hunk_unit.get((fi, hi))
        attr = f' data-unit="{esc(u["id"])}"' if u else ""
        label = (f'{esc(u["id"])} · ' if u else "") + esc(f'{h["header"]} {h["section"]}'.strip())
        rows = [f'<tr class="hunk"{attr}><td class="ln"></td>'
                f'<td colspan="{cols - 1}">{label}</td></tr>']
        if u:
            rows += self.rows_of(self.unit_notes(u), cols)
        return rows

    def unified(self, fi, f, lang, solo=False):
        attr = f' data-lang="{lang}"' if lang else ""
        rows = [f'<table class="diff unified{" solo" if solo else ""}"{attr}>']
        for hi, h in enumerate(f["hunks"]):
            rows += self.hunk_row(fi, hi, h, 4)
            rows += self.unified_rows(fi, hi, h)
        rows.append("</table>")
        return "".join(rows)

    def unified_rows(self, fi, hi, h):
        rows = []
        for r in numbered(h):
            if (fi, hi, r["li"]) in self.moved:
                cls, sign = "moved", "−" if r["tag"] == "-" else "+"
            else:
                cls, sign = {"+": ("add", "+"), "-": ("del", "−"), " ": ("", " ")}[r["tag"]]
            rows.append(f'<tr class="{cls}"><td class="ln">{r["o"]}</td>'
                        f'<td class="ln">{r["n"]}</td><td class="sign">{sign}</td>'
                        f'{self.cell(r)}</tr>')
            rows += self.rows_of(self.after(fi, hi, r), 4)
        return rows

    def side(self, fi, hi, r, num, changed):
        if r is None:
            return '<td class="ln c-empty"></td><td class="code c-empty"></td>'
        cls = " c-moved" if (fi, hi, r["li"]) in self.moved else changed
        return f'<td class="ln{cls}">{r[num]}</td>{self.cell(r, cls, num[0])}'

    def split_table(self, fi, f, lang):
        attr = f' data-lang="{lang}"' if lang else ""
        rows = [f'<table class="diff split"{attr}>']
        for hi, h in enumerate(f["hunks"]):
            rows += self.hunk_row(fi, hi, h, 4)
            lines, i = numbered(h), 0
            while i < len(lines):
                r = lines[i]
                if r["tag"] == " ":
                    rows.append(f'<tr><td class="ln">{r["o"]}</td>{self.cell(r, "", "o")}'
                                f'<td class="ln">{r["n"]}</td>{self.cell(r, "", "n")}</tr>')
                    rows += self.rows_of(self.after(fi, hi, r), 4)
                    i += 1
                    continue
                dels, adds = [], []
                while i < len(lines) and lines[i]["tag"] == "-":
                    dels.append(lines[i])
                    i += 1
                while i < len(lines) and lines[i]["tag"] == "+":
                    adds.append(lines[i])
                    i += 1
                for k in range(max(len(dels), len(adds))):
                    d = dels[k] if k < len(dels) else None
                    a = adds[k] if k < len(adds) else None
                    rows.append(f'<tr>{self.side(fi, hi, d, "o", " c-del")}'
                                f'{self.side(fi, hi, a, "n", " c-add")}</tr>')
                    for r2 in (d, a):
                        if r2:
                            rows += self.rows_of(self.after(fi, hi, r2), 4)
        rows.append("</table>")
        return "".join(rows)

    # queue

    def queue_item(self, u, cls, lines):
        f = self.files[u["file"]]
        where = ""
        if u["hunk"] is not None:
            h = f["hunks"][u["hunk"]]
            head = f'{h["header"]} {h["section"]}'.strip()
            where = f'<span class="where">{esc(head)}</span>'
        out = [f'<li class="qi {cls}"><div class="qhead"><a href="#f{u["file"]}" '
               f'data-unit="{esc(u["id"])}">{esc(u["id"])}</a><span class="fpath">'
               f'{esc(unit_path(f))}</span>{where}</div>'
               f'<ul class="qwhy">{"".join(lines)}</ul>']
        if u["hunk"] is not None:
            h = f["hunks"][u["hunk"]]
            lang = lang_of(unit_path(f)) if self.highlight else None
            attr = f' data-lang="{lang}"' if lang else ""
            out.append(f'<details class="qcode"><summary>{esc(self.L["q"]["code"])}</summary>'
                       f'<div class="scroll"><table class="diff unified solo"{attr}>'
                       f'{"".join(self.unified_rows(u["file"], u["hunk"], h))}</table></div>'
                       f'</details>')
        out.append("</li>")
        return "".join(out)

    def reason_line(self, src, key, text):
        Q = self.L["q"]
        if src == "script":
            return (f'<li><span class="pill warn">{esc(Q["why"][key])}</span>'
                    f'<span><code>{esc(text)}</code></span></li>')
        if src in ("reviewer", "cleared"):
            who = key.split(", ")
            label = Q["by_all"] if len(self.reviewers) > 1 and set(who) == set(self.reviewers) \
                else Q["by"].format(name=key)
            pill = '<span class="pill warn">?</span>' if src == "reviewer" else \
                f'<span class="pill muted">{esc(Q["cleared"])}</span>'
            return (f'<li>{pill}<span>{prose(text)} '
                    f'<span class="by">{esc(label)}</span></span></li>')
        flag = Q["author"].format(flag=self.L["flag"][key])
        return f'<li><span class="pill warn">{esc(flag)}</span><span></span></li>'

    def queue(self, reviews):
        Q = self.L["q"]
        suspects, reasons = queue(self.units, {"notes": self.notes}, reviews)
        self.reviewers = [r["name"] for r in reviews]
        sus = [u for u in self.units if u["id"] in suspects]
        risk = [u for u in self.units if u["id"] in reasons and u["id"] not in suspects]
        folded = len(self.units) - len(sus) - len(risk)
        names = [r["name"] for r in reviews]
        verdict = Q["verdict"].format(s=len(sus), r=len(risk), k=folded, t=len(self.units))
        out = [f'<section class="queue"><p class="verdict">{esc(verdict)}</p>']
        partial = [Q["partial"].format(name=r["name"], seen=r["units_seen"], total=len(self.units))
                   for r in reviews if r["units_seen"] < len(self.units)]
        if partial:
            out.append(f'<ul class="warns">{"".join(f"<li>{esc(p)}</li>" for p in partial)}</ul>')
        if self.loose:
            loose = Q["loose_count"].format(n=len(self.loose))
            out.append(f'<p class="folded">{esc(loose)}</p>')
        if not sus and not risk:
            out.append(f'<p class="empty">{esc(Q["empty"])}</p>')
        if sus:
            out.append(f'<h2>{esc(Q["suspects"])}</h2><ol>')
            for u in sus:
                by = {}
                for name, text in suspects[u["id"]]:
                    by.setdefault(text, []).append(name)
                lines = []
                for text, who in by.items():
                    label = Q["by_all"] if len(names) > 1 and set(who) == set(names) else \
                        Q["by"].format(name=", ".join(who))
                    lines.append(f'<li><span class="pill bad">!</span><span>{prose(text)} '
                                 f'<span class="by">{esc(label)}</span></span></li>')
                lines += [self.reason_line(*r) for r in reasons.get(u["id"], [])]
                out.append(self.queue_item(u, "suspect", lines))
            out.append("</ol>")
        if risk:
            out.append(f'<h2>{esc(Q["risks"])}</h2><ol>')
            for u in risk:
                out.append(self.queue_item(u, "risk",
                                           [self.reason_line(*r) for r in reasons[u["id"]]]))
            out.append("</ol>")
        if folded:
            out.append(f'<p class="folded">{esc(Q["folded"].format(k=folded))}</p>')
        out.append("</section>")
        return "".join(out)

    # file cards

    def card(self, fi):
        L, f = self.L, self.files[fi]
        e = self.info.get(fi, {})
        path = unit_path(f)
        folder, _, name = path.rpartition("/")
        loose = any(u["id"] in self.loose_ids for u in self.units_of[fi])
        stat = (f'<span class="stat-add">+{f["added"]}</span> '
                f'<span class="stat-del">−{f["removed"]}</span>')
        role = ""
        label = e.get("role") or (L["kinds"][e["kind"]] if e.get("kind") else "")
        if label:
            role = f'<span class="role {esc(e.get("kind", ""))}">{esc(label)}</span>'
        chips = "".join(f'<span class="chip">{esc(L["fact"][k].format(**a))}</span>'
                        for k, a in facts(f))
        flag = f'<span class="pill bad">{esc(L["unexplained"])}</span>' if loose else ""
        size = sum(len(h["lines"]) for h in f["hunks"])
        noted = (any(k[0] == fi for k in self.placed)
                 or any(u["id"] in self.top for u in self.units_of[fi]))
        opened = " open" if not f["noise"] and (size <= BIG_FILE or noted) else ""
        parts = [f'<details class="file{" loose" if loose else ""}" id="f{fi}"{opened}>'
                 f'<summary><span class="chev" aria-hidden="true"></span>'
                 f'<span class="fpath"><span class="dir">{esc(folder + "/" if folder else "")}'
                 f'</span>{esc(name)}</span>{role}{chips}{flag}'
                 f'<span class="fstat">{stat}</span></summary>']
        fu = self.file_unit.get(fi)
        if fu:
            parts.append(f'<div class="prelude" data-unit="{esc(fu["id"])}">'
                         f'{"".join(self.unit_notes(fu))}</div>')
        if not f["hunks"]:
            key = ("binary" if f["binary"] else "renamed" if f["status"] in ("R", "C")
                   else "empty" if f["status"] in ("A", "D") else "mode")
            parts.append(f'<p class="empty">{esc(L["body"][key])}</p>')
        else:
            lang = lang_of(path) if self.highlight else None
            # A new or deleted file has one side only: side by side would be half empty.
            solo = f["status"] in ("A", "D") or not self.split
            parts.append(f'<div class="scroll">{self.unified(fi, f, lang, solo)}')
            if self.split and not solo:
                parts.append(self.split_table(fi, f, lang))
            parts.append("</div>")
        parts.append("</details>")
        return "".join(parts)

    def nav(self):
        out = []
        for fi in self.order:
            f = self.files[fi]
            loose = any(u["id"] in self.loose_ids for u in self.units_of[fi])
            name = unit_path(f).rpartition("/")[2]
            cls = ' class="loose"' if loose else ""
            out.append(f'<a href="#f{fi}"{cls}>{esc(name)} '
                       f'<span class="stat-add">+{f["added"]}</span> '
                       f'<span class="stat-del">−{f["removed"]}</span></a>')
        return "".join(out)


def unit_links(page, ids, limit=12):
    by_id = {u["id"]: u for u in page.units}
    links = [f'<a href="#f{by_id[uid]["file"]}" data-unit="{esc(uid)}">{esc(uid)}</a>'
             for uid in ids[:limit]]
    if len(ids) > limit:
        links.append(f"+{len(ids) - limit}")
    return " ".join(links)


def render(meta, files, units, notes, drift, run_dir, reviews=None):
    """The page. With `reviews` (the triage files), it opens on the queue — what is worth a
    look — with a switch to the whole diff; without, it is the whole diff alone."""
    page = Page(files, units, notes)
    L = page.L
    flagged = [n for n in notes["notes"] if n.get("kind")]
    inferred = sum(1 for n in notes["notes"] if n["source"] == "inferred")
    spec = meta["spec"]
    if spec["mode"] == "range":
        base, state = spec["range"], L["range"]
    elif spec["ref"] is None:
        base, state = L["base_empty"], L["uncommitted"]
    else:
        ref = f'{spec["ref"]} ({spec["sha"][:9]})'
        if spec.get("tip") and spec["sha"] != spec["tip"]:
            ref = L["base_mb"].format(ref=spec["ref"], sha=spec["sha"][:9])
        base = L["base_wt"].format(ref=ref)
        # Old run directories have no "head": they were always HEAD-based.
        state = L["uncommitted"] if spec["sha"] == spec.get("head", spec["sha"]) else L["branch"]

    out = []
    eyebrow = []
    link = notes.get("link")
    if link:
        eyebrow.append(f'<a href="{esc(link["url"])}">{esc(link["label"])}</a>')
    eyebrow.append(f"<span>{esc(base)}</span>")
    eyebrow.append(f'<span>{esc(meta["branch"])} · {esc(when(meta["collected"]))} · '
                   f'{esc(state)}</span>')
    out.append(f'<header class="top"><div class="eyebrow">{"".join(eyebrow)}</div>'
               f'<h1>{prose(notes["title"])}</h1><p class="lede">{prose(notes["lede"])}</p>'
               f'</header>')
    if drift == "changed":
        out.append(f'<div class="banner">'
                   f'{esc(L["drift"].format(time=when(meta["collected"])))}</div>')
    elif drift:
        out.append(f'<div class="banner quiet">{esc(L["drift_unknown"].format(reason=drift))}'
                   f'</div>')

    loose_ids = [u["id"] for u in page.loose]
    checks = [f'<li><span class="pill{" bad" if loose_ids else ""}">{len(loose_ids)}</span>'
              f'<span>{esc(L["loose_line"])} {unit_links(page, loose_ids)}</span></li>',
              f'<li><span class="pill{" warn" if flagged else ""}">{len(flagged)}</span>'
              f'<span>{esc(L["flags_line"])} '
              f'{unit_links(page, [n["unit"] for n in flagged])}</span></li>']
    if inferred:
        checks.append(f'<li><span class="pill muted">{inferred}</span>'
                      f'<span>{esc(L["inferred_line"])}</span></li>')
    if drift == "changed":
        checks.append(f'<li><span class="pill warn">{esc(L["stale"])}</span>'
                      f'<span>{esc(L["stale_line"])}</span></li>')
    panels = [f'<div class="panel"><h2>{esc(L["selfcheck"])}</h2>'
              f'<ul class="checks">{"".join(checks)}</ul></div>']
    for p in notes.get("panels", []):
        if "text" in p:
            body = f'<p>{prose(p["text"])}</p>'
        else:
            body = "".join(f'<li><span class="pill{" warn" if c.get("warn") else ""}">'
                           f'{esc(c["pill"])}</span><span>{prose(c["text"])}</span></li>'
                           for c in p["checks"])
            body = f'<ul class="checks">{body}</ul>'
        panels.append(f'<div class="panel"><h2>{esc(p["title"])}</h2>{body}</div>')
    out.append(f'<section class="summary">{"".join(panels)}</section>')
    if reviews is not None:
        Q = L["q"]
        out.append(f'<div class="modebar"><span class="lbl">{esc(Q["mode"])}</span>'
                   f'<div class="modes" role="group" aria-label="{esc(Q["mode"])}">'
                   f'<button type="button" data-mode="queue" aria-pressed="true">'
                   f'{esc(Q["queue"])}</button><button type="button" data-mode="all" '
                   f'aria-pressed="false">{esc(Q["all"])}</button></div></div>')
        out.append(page.queue(reviews))

    seg = ""
    if page.split:
        seg = (f'<div class="seg" role="group" aria-label="{esc(L["view"])}">'
               f'<button type="button" data-view="split" aria-pressed="true">'
               f'{esc(L["split"])}</button><button type="button" data-view="unified" '
               f'aria-pressed="false">{esc(L["unified"])}</button></div>')
    lg = L["legend"]
    legend = (f'<div class="legend" aria-hidden="true">'
              f'<span><i class="sw add"></i>{esc(lg["add"])}</span>'
              f'<span><i class="sw del"></i>{esc(lg["del"])}</span>'
              f'<span><i class="sw mov"></i>{esc(lg["moved"])}</span>'
              f'<span><i class="sw s-note"></i>{esc(lg["note"])}</span>'
              f'<span><i class="sw s-loose"></i>{esc(lg["loose"])}</span></div>')
    # The file list scrolls away with the page: on a big change it is dozens of rows. Only the
    # one-line view toggle and legend stay pinned.
    out.append(f'<nav class="files-nav" aria-label="{esc(L["files_nav"])}">{page.nav()}</nav>')
    out.append(f'<div class="toolbar"><div class="controls">{seg}{legend}</div></div>')
    out.append("<main>" + "".join(page.card(fi) for fi in page.order) + "</main>")

    foot = [f'<span>{esc(L["snapshot"].format(dir=run_dir))}</span>']
    if meta.get("skipped"):
        foot.append(f'<span>{esc(L["skipped"].format(paths=", ".join(meta["skipped"])))}</span>')
    out.append(f'<footer class="foot">{"".join(foot)}</footer></div>')
    hl = ""
    if page.highlight:
        hl = (f'<script src="{HLJS}" integrity="{HLJS_SRI}" crossorigin="anonymous" '
              f'referrerpolicy="no-referrer"></script>\n')
    style, script = CSS, PAGE_JS
    if reviews is not None:
        style, script = CSS + QUEUE_CSS, PAGE_JS + QUEUE_JS
    doc = PAGE.substitute(lang=esc(notes.get("lang", "en")), title=esc(plain(notes["title"])),
                          style=style, body="\n".join(out), highlight=hl, script=script)
    return doc, page.loose


def check_drift(meta):
    """None when the tree and HEAD still match the snapshot, "changed", or why it could not
    tell. A commit made after collect leaves the patch against the old base unchanged, so HEAD
    is compared on its own."""
    spec = meta["spec"]
    if spec["mode"] == "range":
        return None
    try:
        if "head" in spec and (commit_of(meta["root"], "HEAD") or None) != spec["head"]:
            return "changed"
        patch, _ = take(meta["root"], spec)
    except (Fail, OSError) as e:
        return str(e)
    return None if sha256(patch) == meta["sha256"] else "changed"


def open_in_browser(path):
    """The system browser, not a terminal's own pane: cmux (and the like) put an `open` wrapper
    first on PATH that routes *.html into the terminal. /usr/bin/open is out of its reach."""
    if sys.platform == "darwin" and os.path.exists("/usr/bin/open"):
        subprocess.run(["/usr/bin/open", path], check=False)
    else:
        webbrowser.open(pathlib.Path(path).as_uri())


def build(run_dir, open_page, whole=False):
    run_dir = os.path.abspath(os.path.expanduser(run_dir))
    meta, files, units = load_snapshot(run_dir)
    try:
        with open(os.path.join(run_dir, "notes.json"), encoding="utf-8") as fh:
            notes = json.load(fh)
    except OSError:
        raise Fail(f"no notes.json in {run_dir} — write it first") from None
    except ValueError as e:
        print(f"notes.json: 1 problem — fix it and rerun build:\n  - not valid JSON: {e}")
        return 1
    problems = validate(notes, files, units)
    reviews = None
    if not whole:
        reviews, bad = load_triage(run_dir, units)
        problems += bad
        if not reviews and not bad:
            print(f"no triage-*.json in {run_dir} — run the triage step (fresh reviewers write "
                  f"them), or pass --all for the whole diff without a queue")
            return 1
    if problems:
        print(f"notes.json / triage: {len(problems)} problem(s) — fix them and rerun build:")
        for p in problems:
            print(f"  - {p}")
        return 1
    drift = check_drift(meta)
    page, loose = render(meta, files, units, notes, drift, run_dir, reviews)
    path = os.path.join(run_dir, "index.html")
    with open(path, "w", encoding="utf-8", errors="replace") as fh:
        fh.write(page)

    what = notes.get("unexplained", {})
    items = notes["notes"]
    if drift == "changed":
        print("WARNING: the working tree or HEAD changed after collect — the page shows the "
              "snapshot, not what a commit would contain. Rerun collect before committing.")
    elif drift:
        print(f"NOTE: could not re-check the working tree: {drift}")
    print(f"Page: {path}")
    print(f"notes: {len(items)} · flags: {sum(1 for n in items if n.get('kind'))} · "
          f"inferred: {sum(1 for n in items if n['source'] == 'inferred')} · "
          f"unexplained: {len(loose)} (of {len(units)} units)")
    if loose:
        print("Unexplained:")
        width = max(len(unit_path(files[u["file"]])) for u in loose)
        for u in loose:
            print(f"  {u['id']:<5} {unit_path(files[u['file']]):<{width}}  "
                  f"{what.get(u['id']) or '(no description)'}")
    if reviews is not None:
        suspects, reasons = queue(units, notes, reviews)
        risky = [u for u in units if u["id"] in reasons and u["id"] not in suspects]
        print(f"queue: {len(suspects)} suspected · {len(risky)} worth a look · "
              f"{len(units) - len(suspects) - len(risky)} folded "
              f"(reviewers: {', '.join(r['name'] for r in reviews)})")
        for r in reviews:
            if r["units_seen"] < len(units):
                print(f"WARNING: reviewer {r['name']} read only {r['units_seen']} of "
                      f"{len(units)} units")
        for u in units:
            for name, text in suspects.get(u["id"], []):
                print(f"  ! {u['id']:<5} {unit_path(files[u['file']])}  [{name}] {text}")
    if open_page:
        open_in_browser(path)
    return 0


def collect_target(words):
    """The one ref or range among collect's arguments. `all` comes from the skill's
    `/diff-tour all [ref]` and only skips the triage step, so it is dropped — unless the
    repository has a ref of that name and nothing else was given."""
    if words and words[0] == "all":
        named = git(toplevel(os.getcwd()), "rev-parse", "--verify", "-q", "all^{commit}",
                    ok=(0, 1)).strip()
        if len(words) > 1 or not named:
            words = words[1:]
    if len(words) > 1:
        raise Fail(f"collect takes one ref or range, got: {' '.join(words)}")
    return words[0] if words else None


def main(argv):
    ap = argparse.ArgumentParser(prog="difftour.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help="snapshot the diff and print the unit index")
    c.add_argument("target", nargs="*", metavar="target",
                   help="REF, or a range A..B (commits or trees) / A...B; a leading `all` (the "
                        "skill's own switch) is dropped unless it names a ref")
    c.add_argument("--out-root", default=OUT_ROOT,
                   help="where run directories go (default ~/.cache/kensei-diff)")
    b = sub.add_parser("build", help="check notes.json and render index.html")
    b.add_argument("run_dir")
    b.add_argument("--open", action="store_true", help="open the page in the system browser")
    b.add_argument("--all", action="store_true", dest="whole",
                   help="the whole diff only: no triage files, no queue")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "collect":
            return collect(collect_target(args.target), args.out_root)
        return build(args.run_dir, args.open, args.whole)
    except Fail as e:
        print(f"diff-tour: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
