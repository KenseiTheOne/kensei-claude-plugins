#!/usr/bin/env python3
"""Tests for sessioncommit.py — run: python3 sessioncommit_test.py"""

import datetime
import difflib
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "sessioncommit.py")
sys.path.insert(0, HERE)
import sessioncommit as sc  # noqa: E402

TMP = tempfile.mkdtemp(prefix="sessioncommit-test-")
GITCONFIG = os.path.join(TMP, "gitconfig")
with open(GITCONFIG, "w") as fh:
    fh.write("[init]\n\tdefaultBranch = main\n[commit]\n\tgpgsign = false\n")
BASE_ENV = {k: v for k, v in os.environ.items()
            if not k.startswith(("GIT_", "CLAUDE_"))}
BASE_ENV.update(GIT_CONFIG_GLOBAL=GITCONFIG, GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t",
                GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
os.environ.update(GIT_CONFIG_GLOBAL=GITCONFIG, GIT_CONFIG_NOSYSTEM="1")  # for in-process calls
CLOCK = itertools.count()


def tick():
    n = next(CLOCK)
    return f"2026-10-26T10:{n // 60000 % 60:02d}:{n // 1000 % 60:02d}.{n % 1000:03d}Z"


def real_tick():
    """The real time, as a transcript records it: for tests that need the reflog's clock."""
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def stamp(epoch):
    """A transcript timestamp for this many seconds since the epoch."""
    t = datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


class Clock:
    """A transcript clock set by hand (`clock.at = epoch`), each tick 10 ms on: tests that place
    tool calls against commits made at a chosen time (commit_at)."""

    def __init__(self, at):
        self.at = at

    def __call__(self):
        self.at += 0.01
        return stamp(self.at)


def commit_at(repo, epoch, *args):
    """git commit made at this time, as the reflog records it."""
    env = dict(BASE_ENV, GIT_COMMITTER_DATE=f"@{int(epoch)} +0000")
    proc = subprocess.run(["git", "commit", *args], cwd=repo, env=env, capture_output=True)
    assert proc.returncode == 0, proc.stderr
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, env=BASE_ENV,
                          capture_output=True, text=True).stdout.strip()


# --- builders ----------------------------------------------------------------------------

def g(repo, *args, ok=True):
    proc = subprocess.run(["git", *args], cwd=repo, env=BASE_ENV, capture_output=True)
    if ok and proc.returncode != 0:
        raise AssertionError(f"git {args}: {proc.stderr.decode()}")
    return proc.stdout.decode("utf-8", "surrogateescape")


def mkdir():
    return tempfile.mkdtemp(dir=TMP)


def write_file(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data if isinstance(data, bytes) else data.encode())


def read_file(path):
    with open(path, "rb") as fh:
        return fh.read()


def make_repo(files=None):
    path = os.path.realpath(mkdir())
    g(path, "init", "-q", "-b", "main")
    for name, data in (files or {}).items():
        write_file(os.path.join(path, name), data)
    if files:
        g(path, "add", "-A")
        g(path, "commit", "-qm", "init")
    return path


def lines(n, prefix="line"):
    return "".join(f"{prefix} {i}\n" for i in range(1, n + 1))


def split_keep(text):
    parts = text.split("\n")
    return [p + "\n" for p in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def structured_patch(before, after):
    """structuredPatch as Claude Code writes it: 3 lines of context, LF lines, markers."""
    a, b = split_keep(before), split_keep(after)
    hunks = []
    for group in difflib.SequenceMatcher(None, a, b, autojunk=False).get_grouped_opcodes(3):
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


class Transcript:
    """One .jsonl transcript. shape="patch" writes toolUseResult like the main session;
    shape="input" leaves it out, as workflow agents do."""

    def __init__(self, path, cwd, shape="patch"):
        self.path, self.cwd, self.shape = path, cwd, shape
        self.clock = tick
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "a").close()

    def _put(self, entry):
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, separators=(",", ":"), ensure_ascii=False) + "\n")

    def call(self, name, inp, tur=None, error=False, text="ok", cwd=None, between=None):
        """One tool call; `between` runs after the call is recorded and before its result."""
        tid = "toolu_" + uuid.uuid4().hex[:20]
        self._put({"type": "assistant", "timestamp": self.clock(), "cwd": cwd or self.cwd,
                   "message": {"role": "assistant", "content": [
                       {"type": "tool_use", "id": tid, "name": name, "input": inp}]}})
        if between:
            between()
        block = {"type": "tool_result", "tool_use_id": tid, "content": text}
        if error:
            block["is_error"] = True
        entry = {"type": "user", "timestamp": self.clock(), "cwd": cwd or self.cwd,
                 "message": {"role": "user", "content": [block]}}
        if error:
            entry["toolUseResult"] = "Error: " + text
        elif tur is not None and self.shape == "patch":
            entry["toolUseResult"] = tur
        self._put(entry)

    def edit(self, path, old, new, replace_all=False, error=False):
        raw = read_file(path).decode()
        crlf = "\r\n" in raw
        before = raw.replace("\r\n", "\n")
        if not error:
            assert replace_all or before.count(old) == 1, (old, before)
            after = before.replace(old, new) if replace_all else before.replace(old, new, 1)
            write_file(path, after.replace("\n", "\r\n") if crlf else after)
        else:
            after = before
        self.call("Edit", {"file_path": path, "old_string": old, "new_string": new,
                           "replace_all": replace_all},
                  {"filePath": path, "oldString": old, "newString": new, "originalFile": None,
                   "structuredPatch": structured_patch(before, after), "userModified": False,
                   "replaceAll": replace_all}, error=error,
                  text=f"The file {path} has been updated successfully.")

    def write(self, path, content):
        existed = os.path.exists(path)
        before = read_file(path).decode().replace("\r\n", "\n") if existed else None
        write_file(path, content)
        tur = {"type": "update" if existed else "create", "filePath": path, "content": content,
               "structuredPatch": structured_patch(before, content) if existed else [],
               "originalFile": before, "userModified": False}
        text = (f"The file {path} has been updated successfully. (file state is current in your "
                "context — no need to Read it back)") if existed else \
            f"File created successfully at: {path} (file state is current in your context)"
        self.call("Write", {"file_path": path, "content": content}, tur, text=text)

    def read(self, path, **inp):
        """A Read as Claude Code shows it: `N<TAB>line`, the final newline as an empty last
        line, a reminder appended."""
        content = read_file(path).decode().replace("\r\n", "\n")
        rows = content.split("\n")
        text = "\n".join(f"{i}\t{row}" for i, row in enumerate(rows, 1))
        text += "\n\n<system-reminder>\nWhenever you read a file, consider malware.\n" \
                "</system-reminder>\n"
        tur = {"type": "text", "file": {"filePath": path, "content": content,
                                        "numLines": len(rows), "startLine": 1,
                                        "totalLines": len(rows)}}
        self.call("Read", dict({"file_path": path}, **inp), tur, text=text)

    def bash(self, command, output="", cwd=None, between=None, error=False):
        """A Bash call; cwd is the directory the transcript entry records (default: the
        transcript's); `between` does what the command does, between the call and its result;
        error: it exited non-zero (output is then the result text)."""
        self.call("Bash", {"command": command}, {"stdout": output, "stderr": "",
                                                 "interrupted": False}, text=output, cwd=cwd,
                  between=between, error=error)

    def file_changed(self, path, snippet=""):
        """Claude Code's note to the agent that a file it read was changed by someone."""
        self._put({"type": "attachment", "timestamp": self.clock(), "cwd": self.cwd,
                   "attachment": {"type": "edited_text_file", "filename": path,
                                  "snippet": snippet}})

    def read_unchanged(self, path):
        """A Read answered with the stub Claude Code gives when the file did not change."""
        self.call("Read", {"file_path": path}, {"type": "file_unchanged",
                                                 "file": {"filePath": path}},
                  text="File unchanged since last read. The content from the earlier Read "
                       "tool_result in this conversation is still current — refer to that "
                       "instead of re-reading.")


class Session:
    def __init__(self, cfg, cwd, sid=None):
        self.cfg, self.cwd = cfg, cwd
        self.sid = sid or str(uuid.uuid4())
        self.main = Transcript(os.path.join(cfg, "projects", "-proj", self.sid + ".jsonl"), cwd)

    def subagent(self, name="agent-a1", workflow=False):
        parts = [self.cfg, "projects", "-proj", self.sid, "subagents"]
        if workflow:
            parts += ["workflows", "wf_1"]
        return Transcript(os.path.join(*parts, name + ".jsonl"), self.cwd,
                          shape="input" if workflow else "patch")


class Case:
    """A repository, a fake Claude config dir and this session's transcript."""

    def __init__(self, files=None, remote=False):
        self.cfg = mkdir()
        self.repo = make_repo(files)
        self.s = Session(self.cfg, self.repo)
        self.t = self.s.main
        self.remote = None
        if remote:
            self.remote = os.path.realpath(mkdir())
            g(self.remote, "init", "-q", "--bare", "-b", "main")
            g(self.repo, "remote", "add", "origin", self.remote)
            g(self.repo, "push", "-q", "-u", "origin", "main")
            g(self.repo, "remote", "set-head", "origin", "main")

    def p(self, rel):
        return os.path.join(self.repo, rel)

    def env(self, sid=None):
        return dict(BASE_ENV, CLAUDE_CONFIG_DIR=self.cfg,
                    CLAUDE_CODE_SESSION_ID=sid or self.s.sid)

    def plan(self, *args, repo=None, lang="en", sid=None):
        out = mkdir()
        proc = subprocess.run([sys.executable, SCRIPT, "plan", "--repo", repo or self.repo,
                               "--out", out, "--lang", lang, *args], cwd=repo or self.repo,
                              env=self.env(sid), capture_output=True, text=True)
        plan = None
        if os.path.exists(os.path.join(out, "plan.json")):
            with open(os.path.join(out, "plan.json"), encoding="utf-8") as fh:
                plan = json.load(fh)
        self.run_dir = out
        return proc.returncode, proc.stdout, proc.stderr, plan

    def apply(self, *args, msg="Session work\n"):
        msg_file = os.path.join(self.run_dir, "msg-in.txt")
        with open(msg_file, "w", encoding="utf-8") as fh:
            fh.write(msg)
        proc = subprocess.run([sys.executable, SCRIPT, "apply", "--plan",
                               os.path.join(self.run_dir, "plan.json"), "--message-file",
                               msg_file, *args], cwd=self.repo, env=self.env(),
                              capture_output=True, text=True)
        return proc.returncode, proc.stdout, proc.stderr

    def land(self, *args):
        code, out, err, plan = self.plan(*args)
        assert code == 0, out + err
        return self.apply(*args)

    def show(self, rel, ref="HEAD"):
        return g(self.repo, "show", f"{ref}:{rel}")

    def in_commit(self, ref="HEAD"):
        return set(g(self.repo, "show", "--name-only", "--format=", ref).split())

    def files(self, plan, status=None):
        return {f["path"]: f for f in plan["files"]
                if status is None or f.get("status") == status}


def status_of(plan, path):
    return next(f for f in plan["files"] if f["path"] == path)


# --- unit: text model --------------------------------------------------------------------

class TextModel(unittest.TestCase):

    def test_hunk_with_no_newline_markers_round_trips(self):
        before, after = "alpha\nbeta\ngamma", "alpha\nbeta\nGAMMA\ndelta\n"
        hunks = structured_patch(before, after)
        self.assertIn("\\ No newline at end of file", hunks[0]["lines"])
        self.assertEqual(sc.apply_hunks(before, hunks, reverse=False), after)
        self.assertEqual(sc.apply_hunks(after, hunks, reverse=True), before)

    def test_undo_finds_the_nearest_match_when_lines_moved(self):
        before = lines(30)
        after = before.replace("line 20\n", "LINE 20\n")
        hunks = structured_patch(before, after)
        shifted = "new top\n" * 5 + after
        self.assertEqual(sc.apply_hunks(shifted, hunks, reverse=True), "new top\n" * 5 + before)

    def test_undo_of_a_rewritten_hunk_is_not_found(self):
        before = lines(10)
        after = before.replace("line 5\n", "LINE 5\n")
        hunks = structured_patch(before, after)
        with self.assertRaises(sc.NotFound):
            sc.apply_hunks(after.replace("LINE 5", "Line five"), hunks, reverse=True)

    def test_undo_of_a_string_edit_restores_the_original_text(self):
        ev = {"old": "b = 2\r\n", "new": "b = 3\n", "all": False}
        self.assertEqual(sc.undo_replace("a = 1\nb = 3\nc = 4\n", ev), "a = 1\nb = 2\nc = 4\n")

    def test_undo_of_a_string_edit_whose_result_repeats_is_ambiguous(self):
        ev = {"old": "bar()", "new": "foo()", "all": False}
        with self.assertRaises(sc.Ambiguous):
            sc.undo_replace("foo()\nfoo()\nbaz()\n", ev)
        with self.assertRaises(sc.Ambiguous):
            sc.undo_replace("abc", {"old": "x", "new": "", "all": False})

    def test_restore_gives_crlf_back_to_crlf_lines(self):
        raw = "a\r\nb\r\nc\r\n"
        self.assertEqual(sc.restore("a\nB\nc\n", [raw]), "a\r\nB\r\nc\r\n")
        self.assertEqual(sc.restore("a\nb\n", ["a\nb\n"]), "a\nb\n")


# --- attribution -------------------------------------------------------------------------

class ShellHintPlaces(unittest.TestCase):
    """Which shell commands may have touched a repository (unit, no git writes)."""

    def test_home_forms_pushd_and_unrecorded_cwd(self):
        home = os.path.realpath(mkdir())
        repo = os.path.join(home, "proj")
        os.makedirs(os.path.join(repo, "src"))
        away = os.path.join(home, "away")
        os.makedirs(away)
        old = os.environ.get("HOME")
        os.environ["HOME"] = home
        try:
            cases = [
                ("touch ~/proj/a.txt", away, True),
                ("rm -f $HOME/proj/a.txt", away, True),
                ("rm -f ${HOME}/away/a.txt", repo, False),
                ("cd ~/proj && touch a.txt", away, True),
                ("cd src && touch a.txt", repo, True),
                ("cd ../away && touch a.txt", repo + "/src", True),  # still inside: proj/away
                ("cd ../../away && touch a.txt", repo + "/src", False),
                ("pushd /tmp && touch a.txt && popd && touch b.txt", repo, True),
                ("pushd ~/away && touch a.txt", repo, False),
                ("touch a.txt", None, True),  # no cwd recorded: cannot be told
                ("cd ~ && touch a.txt", repo, False),
                ("git -C ~/proj rm -q a.txt", away, True),
                ("rsync -a ~/proj/ ~/away/", away, False),
                ("rsync -a ~/away/ ~/proj/", away, True),
            ]
            for cmd, cwd, want in cases:
                with self.subTest(cmd=cmd, cwd=cwd):
                    self.assertEqual(sc.touches_repo(cmd, cwd, repo), want)
        finally:
            if old is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old


class Attribution(unittest.TestCase):

    def test_clean_single_file_edit_is_committed_whole(self):
        c = Case({"a.txt": lines(5)})
        c.t.edit(c.p("a.txt"), "line 3", "LINE 3")
        code, out, err, plan = c.plan()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(plan["verdict"], "go")
        self.assertEqual((status_of(plan, "a.txt")["status"], status_of(plan, "a.txt")["how"]),
                         ("commit", "whole"))
        code, out, err = c.apply()
        self.assertEqual(code, 0, out + err)
        self.assertIn("LINE 3", c.show("a.txt"))
        self.assertEqual(g(c.repo, "status", "--porcelain"), "")
        self.assertEqual(g(c.repo, "log", "-1", "--format=%s"), "Session work\n")

    def test_new_file_written_then_edited_is_committed(self):
        c = Case({"a.txt": "a\n"})
        c.t.write(c.p("src/new.py"), "print(1)\nprint(2)\n")
        c.t.edit(c.p("src/new.py"), "print(2)", "print(3)")
        code, out, err = c.land()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(c.show("src/new.py"), "print(1)\nprint(3)\n")
        self.assertEqual(c.in_commit(), {"src/new.py"})

    def test_new_file_edited_by_someone_after_the_session_commits_the_session_version(self):
        c = Case({"a.txt": "a\n"})
        c.t.write(c.p("n.txt"), "one\ntwo\n")
        write_file(c.p("n.txt"), "one\ntwo\nforeign\n")
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "n.txt")["status"], "partial")
        self.assertEqual(c.apply()[0], 0)
        self.assertEqual(c.show("n.txt"), "one\ntwo\n")
        self.assertEqual(read_file(c.p("n.txt")), b"one\ntwo\nforeign\n")

    def test_file_written_then_changed_through_bash_and_committed_is_not_reverted(self):
        c = Case({"a.txt": "a\n"})
        c.t.write(c.p("gen.txt"), lines(10))
        write_file(c.p("gen.txt"), lines(10).replace("line 3\n", "SED 3\n"))  # sed -i, no patch
        g(c.repo, "add", "gen.txt")
        g(c.repo, "commit", "-qm", "declared earlier")
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "gen.txt")["status"], "unchanged", out)

    def test_workflow_rewrite_of_a_tracked_file_keeps_later_foreign_edits_out(self):
        c = Case({"a.txt": lines(20)})
        wf = c.s.subagent("agent-w3", workflow=True)
        wf.read(c.p("a.txt"))
        wf.write(c.p("a.txt"), lines(20).replace("line 5\n", "SESSION 5\n"))
        write_file(c.p("a.txt"), read_file(c.p("a.txt")).replace(b"line 15\n", b"FOREIGN\n"))
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.txt")["status"], "partial", out)
        self.assertEqual(c.apply()[0], 0)
        self.assertEqual(c.show("a.txt"), lines(20).replace("line 5\n", "SESSION 5\n"))
        self.assertIn(b"FOREIGN", read_file(c.p("a.txt")))

    def test_workflow_rewrite_overwritten_by_someone_on_the_same_line_is_skipped(self):
        c = Case({"a.txt": lines(20)})
        wf = c.s.subagent("agent-w4", workflow=True)
        wf.read(c.p("a.txt"))
        wf.write(c.p("a.txt"), lines(20).replace("line 5\n", "SESSION 5\n"))
        write_file(c.p("a.txt"), read_file(c.p("a.txt")).replace(b"SESSION 5\n", b"FOREIGN\n"))
        code, out, err, plan = c.plan()
        st = status_of(plan, "a.txt")
        self.assertEqual((st["status"], st["reason"]), ("skipped", "notfound"), out)

    def test_foreign_hunk_above_the_session_edit_stays_in_the_working_tree(self):
        c = Case({"a.txt": lines(30)})
        write_file(c.p("a.txt"), read_file(c.p("a.txt")).replace(b"line 2\n", b"FOREIGN 2\n"))
        c.t.edit(c.p("a.txt"), "line 25", "SESSION 25")
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.txt")["status"], "partial")
        self.assertIn("1 partly: a.txt", out)
        self.assertEqual(c.apply()[0], 0)
        head = c.show("a.txt")
        self.assertIn("SESSION 25", head)
        self.assertNotIn("FOREIGN", head)
        self.assertIn(b"FOREIGN 2", read_file(c.p("a.txt")))
        self.assertIn(b"SESSION 25", read_file(c.p("a.txt")))
        self.assertEqual(g(c.repo, "status", "--porcelain"), " M a.txt\n")

    def test_foreign_hunk_below_the_session_edit_made_later_stays(self):
        c = Case({"a.txt": lines(30)})
        c.t.edit(c.p("a.txt"), "line 4\n", "SESSION 4\n")
        write_file(c.p("a.txt"), read_file(c.p("a.txt")).replace(b"line 28\n", b"FOREIGN\n"))
        self.assertEqual(c.land()[0], 0)
        self.assertIn("SESSION 4", c.show("a.txt"))
        self.assertNotIn("FOREIGN", c.show("a.txt"))
        self.assertIn(b"FOREIGN", read_file(c.p("a.txt")))

    def test_foreign_edit_rewriting_the_session_lines_is_skipped(self):
        c = Case({"a.txt": lines(10)})
        c.t.edit(c.p("a.txt"), "line 5", "SESSION 5")
        write_file(c.p("a.txt"), read_file(c.p("a.txt")).replace(b"SESSION 5", b"FOREIGN 5"))
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.txt")["status"], "skipped")
        self.assertEqual(status_of(plan, "a.txt")["reason"], "notfound")
        self.assertIn("Skip:     a.txt", out)
        self.assertEqual(plan["verdict"], "nothing")

    def test_adjacent_session_and_foreign_hunks_are_tangled(self):
        c = Case({"a.txt": lines(20)})
        c.t.edit(c.p("a.txt"), "line 10\n", "SESSION 10\n")
        write_file(c.p("a.txt"), read_file(c.p("a.txt")).replace(b"line 11\n", b"FOREIGN 11\n"))
        code, out, err, plan = c.plan()
        st = status_of(plan, "a.txt")
        self.assertEqual((st["status"], st["reason"]), ("skipped", "tangled"), out)
        self.assertIn("entangled (lines", out)

    def test_foreign_edit_two_lines_away_inside_the_context_is_split_off(self):
        c = Case({"a.txt": lines(20)})
        c.t.edit(c.p("a.txt"), "line 10\n", "SESSION 10\n")
        write_file(c.p("a.txt"), read_file(c.p("a.txt")).replace(b"line 12\n", b"FOREIGN 12\n"))
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.txt")["status"], "partial", out)
        self.assertEqual(c.apply()[0], 0)
        self.assertEqual(c.show("a.txt"), lines(20).replace("line 10\n", "SESSION 10\n"))

    def test_session_that_reverted_its_change_has_nothing_to_commit(self):
        c = Case({"a.txt": lines(5)})
        c.t.edit(c.p("a.txt"), "line 2", "LINE 2")
        c.t.edit(c.p("a.txt"), "LINE 2", "line 2")
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.txt")["status"], "unchanged")
        self.assertEqual(plan["verdict"], "nothing")
        self.assertIn("Commit:   no — no new changes of this session here", out)

    def test_work_committed_earlier_in_the_session_is_not_committed_again(self):
        c = Case({"a.txt": lines(20)})
        c.t.edit(c.p("a.txt"), "line 3\n", "SESSION 3\n")
        self.assertEqual(c.land()[0], 0)
        write_file(c.p("a.txt"), read_file(c.p("a.txt")).replace(b"line 18\n", b"FOREIGN\n"))
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.txt")["status"], "unchanged")
        self.assertEqual(plan["verdict"], "nothing")
        # A second edit after the first commit is the only thing committed next time.
        c.t.edit(c.p("a.txt"), "line 10\n", "SESSION 10\n")
        self.assertEqual(c.land()[0], 0)
        self.assertIn("SESSION 10", c.show("a.txt"))
        self.assertNotIn("FOREIGN", c.show("a.txt"))
        self.assertEqual(len(g(c.repo, "log", "--format=%h").split()), 3)

    def test_crlf_file_keeps_its_line_endings(self):
        c = Case({"a.cs": lines(20).replace("\n", "\r\n")})
        c.t.edit(c.p("a.cs"), "line 15", "SESSION 15")
        write_file(c.p("a.cs"), read_file(c.p("a.cs")).replace(b"line 2\r\n", b"FOREIGN\r\n"))
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.cs")["status"], "partial")
        self.assertEqual(c.apply()[0], 0)
        blob = g(c.repo, "cat-file", "blob", "HEAD:a.cs").encode()
        self.assertIn(b"SESSION 15\r\n", blob)
        self.assertNotIn(b"FOREIGN", blob)
        self.assertEqual(blob.count(b"\r\n"), 20)
        self.assertEqual(blob.count(b"\n"), 20)

    def test_file_without_trailing_newline(self):
        c = Case({"a.txt": lines(12) + "last line"})
        c.t.edit(c.p("a.txt"), "last line", "LAST LINE")
        write_file(c.p("a.txt"), read_file(c.p("a.txt")).replace(b"line 1\n", b"FOREIGN\n"))
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.txt")["status"], "partial")
        self.assertEqual(c.apply()[0], 0)
        self.assertEqual(c.show("a.txt"), lines(12) + "LAST LINE")

    def test_subagent_edits_are_replayed_in_timestamp_order(self):
        c = Case({"a.txt": lines(10)})
        sub = c.s.subagent()
        c.t.edit(c.p("a.txt"), "line 5", "step one")
        sub.edit(c.p("a.txt"), "step one", "step two")
        c.t.edit(c.p("a.txt"), "step two", "step three")
        write_file(c.p("a.txt"), read_file(c.p("a.txt")).replace(b"line 10\n", b"FOREIGN\n"))
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.txt")["status"], "partial", out)
        self.assertEqual(status_of(plan, "a.txt")["events"], 3)
        self.assertEqual(c.apply()[0], 0)
        self.assertIn("step three", c.show("a.txt"))
        self.assertNotIn("FOREIGN", c.show("a.txt"))

    def test_workflow_agent_edits_without_tool_results_are_found(self):
        c = Case({"a.txt": lines(30), "b.txt": "x\n"})
        wf = c.s.subagent("agent-w1", workflow=True)
        wf.edit(c.p("a.txt"), "line 20", "WORKFLOW 20")
        wf.write(c.p("new.txt"), "made by a workflow\n")
        write_file(c.p("a.txt"), read_file(c.p("a.txt")).replace(b"line 1\n", b"FOREIGN\n"))
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.txt")["status"], "partial")
        self.assertEqual(status_of(plan, "new.txt")["status"], "commit")
        self.assertEqual(c.apply()[0], 0)
        self.assertIn("WORKFLOW 20", c.show("a.txt"))
        self.assertNotIn("FOREIGN", c.show("a.txt"))
        self.assertEqual(c.show("new.txt"), "made by a workflow\n")

    def test_workflow_deletion_edit_falls_back_to_replay_from_head(self):
        c = Case({"a.txt": lines(10)})
        wf = c.s.subagent("agent-w2", workflow=True)
        wf.edit(c.p("a.txt"), "line 4\n", "")
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.txt")["status"], "commit", out)
        self.assertEqual(c.apply()[0], 0)
        self.assertNotIn("line 4\n", c.show("a.txt"))

    def test_failed_edit_is_ignored(self):
        c = Case({"a.txt": lines(5)})
        c.t.edit(c.p("a.txt"), "nope", "NOPE", error=True)
        write_file(c.p("a.txt"), "changed by someone\n")
        code, out, err, plan = c.plan()
        self.assertEqual(plan["files"], [])
        self.assertIn("this session changed nothing in this repository", out)

    def test_other_session_edits_count_as_foreign(self):
        c = Case({"a.txt": lines(30)})
        other = Session(c.cfg, c.repo)
        other.main.edit(c.p("a.txt"), "line 2\n", "OTHER SESSION\n")
        c.t.edit(c.p("a.txt"), "line 25\n", "THIS SESSION\n")
        self.assertEqual(c.land()[0], 0)
        self.assertIn("THIS SESSION", c.show("a.txt"))
        self.assertNotIn("OTHER SESSION", c.show("a.txt"))
        self.assertIn(b"OTHER SESSION", read_file(c.p("a.txt")))

    def test_two_sessions_can_be_committed_together(self):
        c = Case({"a.txt": lines(30)})
        other = Session(c.cfg, c.repo)
        other.main.edit(c.p("a.txt"), "line 2\n", "EARLIER SESSION\n")
        c.t.edit(c.p("a.txt"), "line 25\n", "THIS SESSION\n")
        code, out, err, plan = c.plan("--session", c.s.sid, "--session", other.sid)
        self.assertEqual(status_of(plan, "a.txt")["how"], "whole")


# --- the user's index and other files -------------------------------------------------------

class Surroundings(unittest.TestCase):

    def test_foreign_untracked_and_staged_files_are_left_alone(self):
        c = Case({"a.txt": lines(5), "b.txt": lines(5)})
        c.t.edit(c.p("a.txt"), "line 1", "SESSION")
        write_file(c.p("b.txt"), "staged by someone\n")
        g(c.repo, "add", "b.txt")
        write_file(c.p("u.txt"), "untracked\n")
        code, out, err, plan = c.plan()
        self.assertIn("Foreign:  leaving alone 0 modified files, 1 untracked, staged: 1", out)
        self.assertEqual(c.apply()[0], 0)
        self.assertEqual(c.in_commit(), {"a.txt"})
        self.assertEqual(g(c.repo, "diff", "--cached", "--name-only"), "b.txt\n")
        self.assertEqual(g(c.repo, "status", "--porcelain"), "M  b.txt\n?? u.txt\n")

    def test_foreign_staged_change_on_a_session_path_stops(self):
        c = Case({"a.txt": lines(30)})
        c.t.edit(c.p("a.txt"), "line 1\n", "SESSION\n")
        write_file(c.p("a.txt"), read_file(c.p("a.txt")).replace(b"line 25\n", b"FOREIGN 25\n"))
        g(c.repo, "add", "a.txt")  # someone staged the file with their change in it
        head = g(c.repo, "rev-parse", "HEAD")
        code, out, err, plan = c.plan()
        self.assertEqual(code, 1)
        self.assertEqual(plan["verdict"], "stop")
        self.assertIn("staged", plan["stop"])
        self.assertEqual(c.apply()[0], 1)
        self.assertEqual(g(c.repo, "rev-parse", "HEAD"), head)

    def test_declared_deletion_is_committed(self):
        c = Case({"a.txt": "a\n", "old.txt": "old\n"})
        os.remove(c.p("old.txt"))
        c.t.bash("rm old.txt")
        code, out, err, plan = c.plan()
        self.assertEqual(plan["files"], [])
        self.assertIn("$ rm old.txt", out)
        code, out, err, plan = c.plan("--declare", "old.txt")
        st = status_of(plan, "old.txt")
        self.assertEqual((st["status"], st["how"]), ("commit", "delete"))
        self.assertIn("whole, unverified: old.txt", out)
        self.assertEqual(c.apply()[0], 0)
        self.assertNotIn("old.txt", g(c.repo, "ls-files"))
        self.assertEqual(g(c.repo, "status", "--porcelain"), "")

    def test_shell_commands_that_may_change_files_are_listed_as_hints(self):
        c = Case({"a.txt": "a\n"})
        for cmd in ("sed -i '' 's/a/b/' a.txt", "python3 - <<'EOF'\nopen('a.txt','w')\nEOF",
                    "echo x > out.txt", "git mv a.txt b.txt", "ls -la 2>/dev/null",
                    "git status --short", "grep -n foo a.txt | head"):
            c.t.bash(cmd)
        code, out, err, plan = c.plan()
        self.assertEqual(plan["bash_hints"], [
            "sed -i '' 's/a/b/' a.txt", "python3 - <<'EOF'\nopen('a.txt','w')\nEOF",
            "echo x > out.txt", "git mv a.txt b.txt"])
        self.assertIn("  $ python3 - <<'EOF' open('a.txt','w') EOF\n", out)

    def test_edited_file_deleted_later_without_declaration_is_skipped(self):
        c = Case({"a.txt": lines(5)})
        c.t.edit(c.p("a.txt"), "line 1", "SESSION")
        os.remove(c.p("a.txt"))
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.txt")["reason"], "deleted")

    def test_ignored_file_is_skipped(self):
        c = Case({".gitignore": "*.log\n", "a.txt": "a\n"})
        c.t.write(c.p("debug.log"), "noise\n")
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "debug.log")["reason"], "ignored")
        self.assertIn("Skip:     debug.log — ignored by git", out)

    def test_path_in_another_repo_is_reported(self):
        c = Case({"a.txt": "a\n"})
        other = make_repo({"o.txt": "o\n"})
        c.t.edit(os.path.join(other, "o.txt"), "o", "O")
        c.t.write(os.path.join(mkdir(), "scratch.txt"), "x\n")
        code, out, err, plan = c.plan()
        self.assertEqual(plan["other_repos"], {other: ["o.txt"]})
        self.assertEqual(plan["outside"], 1)
        self.assertIn(f"Elsewhere: this session also edited {other} (1 file)", out)

    def test_unity_meta_companions_of_created_assets_are_included(self):
        c = Case({"Assets/Old.cs": "class Old {}\n", "Assets/Old.cs.meta": "guid: 1\n"})
        c.t.write(c.p("Assets/New/Foo.cs"), "class Foo {}\n")
        write_file(c.p("Assets/New/Foo.cs.meta"), "guid: 2\n")
        write_file(c.p("Assets/New.meta"), "guid: 3\n")
        write_file(c.p("Assets/Other.cs.meta"), "guid: 4\n")  # someone else's
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "Assets/New/Foo.cs.meta")["companion_of"],
                         "Assets/New/Foo.cs")
        self.assertEqual(c.apply()[0], 0)
        self.assertEqual(c.in_commit(), {"Assets/New/Foo.cs", "Assets/New/Foo.cs.meta",
                                         "Assets/New.meta"})
        self.assertIn("?? Assets/Other.cs.meta", g(c.repo, "status", "--porcelain"))

    def test_declared_deletion_takes_its_meta_along(self):
        c = Case({"Assets/Gone.cs": "x\n", "Assets/Gone.cs.meta": "guid: 1\n"})
        os.remove(c.p("Assets/Gone.cs"))
        os.remove(c.p("Assets/Gone.cs.meta"))
        code, out, err, plan = c.plan("--declare", "Assets/Gone.cs")
        self.assertEqual(c.apply()[0], 0)
        self.assertEqual(g(c.repo, "ls-files"), "")

    def test_rejected_commit_stops_and_leaves_the_index_alone(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"})
        hook = c.p(".git/hooks/pre-commit")
        write_file(hook, "#!/bin/sh\necho 'lint failed' >&2\nexit 1\n")
        os.chmod(hook, 0o755)
        c.t.edit(c.p("a.txt"), "line 1", "SESSION")
        write_file(c.p("b.txt"), "staged\n")
        g(c.repo, "add", "b.txt")
        code, out, err = c.land()
        self.assertEqual(code, 1)
        self.assertIn("lint failed", out)
        self.assertEqual(g(c.repo, "log", "--format=%s"), "init\n")
        self.assertEqual(g(c.repo, "status", "--porcelain"), " M a.txt\nM  b.txt\n")

    def test_stale_plan_exits_3(self):
        c = Case({"a.txt": lines(5)})
        c.t.edit(c.p("a.txt"), "line 1", "SESSION")
        c.plan()
        write_file(c.p("a.txt"), read_file(c.p("a.txt")) + b"more\n")
        code, out, err = c.apply()
        self.assertEqual(code, 3, out + err)
        self.assertIn("plan is stale", err)
        self.assertEqual(g(c.repo, "log", "--format=%s"), "init\n")

    def test_detached_head_stops(self):
        c = Case({"a.txt": lines(5)})
        g(c.repo, "checkout", "-q", "--detach")
        c.t.edit(c.p("a.txt"), "line 1", "SESSION")
        code, out, err, plan = c.plan()
        self.assertEqual(code, 1)
        self.assertIn("detached HEAD", out)

    def test_outside_a_repository_the_one_the_session_edited_is_used(self):
        c = Case({"a.txt": lines(5)})
        c.t.edit(c.p("a.txt"), "line 1", "SESSION")
        home = os.path.realpath(mkdir())
        out_dir = mkdir()
        proc = subprocess.run([sys.executable, SCRIPT, "plan", "--out", out_dir], cwd=home,
                              env=c.env(), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn(f"Repository: {c.repo}", proc.stdout)
        other = make_repo({"o.txt": "o\n"})
        c.t.edit(os.path.join(other, "o.txt"), "o", "O")
        proc = subprocess.run([sys.executable, SCRIPT, "plan", "--out", out_dir], cwd=home,
                              env=c.env(), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("run plan with --repo <path> for each", proc.stderr)

    def test_missing_transcript_is_an_error(self):
        c = Case({"a.txt": "a\n"})
        code, out, err, plan = c.plan(sid="no-such-session")
        self.assertEqual(code, 2)
        self.assertIn("no transcript", err)

    def test_status_lines_in_russian_and_english(self):
        c = Case({"a.txt": lines(30)})
        c.t.edit(c.p("a.txt"), "line 20", "SESSION")
        write_file(c.p("a.txt"), read_file(c.p("a.txt")).replace(b"line 1\n", b"FOREIGN\n"))
        code, ru, err, plan = c.plan(lang="ru")
        self.assertIn("Коммит:   да — 1 файл (1 частично: a.txt — чужие правки останутся в "
                      "рабочем дереве)", ru)
        self.assertIn("Чужое:    не трогаю 1 изменённый файл, 0 неотслеживаемых, staged: 0", ru)
        self.assertIn("В main:   нет — уже на main", ru)
        self.assertIn("Push:     нет — нет remote", ru)
        code, en, err, plan = c.plan(lang="en")
        self.assertIn("Commit:   yes — 1 file (1 partly: a.txt — other edits stay in the "
                      "working tree)", en)
        self.assertIn("Into main: no — already on main", en)
        code, out, err = c.apply("--lang", "ru")
        self.assertIn("Коммит:   готово — ", out)
        self.assertIn("Push:     нет — нет remote", out)


# --- merge into main and push ------------------------------------------------------------

def clone_and_push(remote, change):
    other = os.path.realpath(mkdir())
    g(other, "clone", "-q", remote, ".")
    for rel, data in change.items():
        write_file(os.path.join(other, rel), data)
    g(other, "add", "-A")
    g(other, "commit", "-qm", "upstream change")
    g(other, "push", "-q", "origin", "main")


class Publish(unittest.TestCase):

    def test_on_main_commit_is_pushed_to_a_bare_remote(self):
        c = Case({"a.txt": lines(5)}, remote=True)
        c.t.edit(c.p("a.txt"), "line 1", "SESSION")
        code, out, err, plan = c.plan()
        self.assertIn("Push:     yes — origin/main", out)
        code, out, err = c.apply()
        self.assertEqual(code, 0, out + err)
        self.assertIn("Push:     done — origin/main", out)
        self.assertEqual(g(c.remote, "rev-parse", "main"), g(c.repo, "rev-parse", "HEAD"))

    def test_origin_moved_and_clean_tree_rebases_then_pushes(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
        clone_and_push(c.remote, {"b.txt": "upstream\n"})
        c.t.edit(c.p("a.txt"), "line 1", "SESSION")
        code, out, err = c.land()
        self.assertEqual(code, 0, out + err)
        self.assertIn("Rebase:   done", out)
        self.assertEqual(g(c.remote, "log", "--format=%s", "main").split("\n")[:2],
                         ["Session work", "upstream change"])
        self.assertEqual(read_file(c.p("b.txt")), b"upstream\n")

    def test_origin_moved_and_dirty_tree_stops_after_commit(self):
        c = Case({"a.txt": lines(30), "b.txt": "b\n"}, remote=True)
        clone_and_push(c.remote, {"b.txt": "upstream\n"})
        c.t.edit(c.p("a.txt"), "line 20", "SESSION")
        write_file(c.p("a.txt"), read_file(c.p("a.txt")).replace(b"line 1\n", b"FOREIGN\n"))
        code, out, err = c.land()
        self.assertEqual(code, 1, out + err)
        self.assertIn("Commit:   done", out)
        self.assertIn("Push:     stop — origin/main is 1 commit ahead", out)
        self.assertEqual(g(c.repo, "log", "-1", "--format=%s"), "Session work\n")
        self.assertNotEqual(g(c.remote, "rev-parse", "main"), g(c.repo, "rev-parse", "HEAD"))
        self.assertIn(b"FOREIGN", read_file(c.p("a.txt")))

    def test_feature_branch_in_a_worktree_fast_forwards_main_and_pushes(self):
        c = Case({"a.txt": lines(5)}, remote=True)
        wt = os.path.join(os.path.realpath(mkdir()), "wt")
        g(c.repo, "worktree", "add", "-q", "-b", "feature/x", wt)
        c.s = Session(c.cfg, wt)
        c.t = c.s.main
        c.t.edit(os.path.join(wt, "a.txt"), "line 2", "FEATURE")
        code, out, err, plan = c.plan(repo=wt)
        self.assertEqual(code, 0, out + err)
        self.assertIn("Into main: yes — branch feature/x → fast-forward main (1 commit)", out)
        proc = subprocess.run([sys.executable, SCRIPT, "apply", "--plan",
                               os.path.join(c.run_dir, "plan.json"), "--message-file", "-"],
                              input="Feature work\n", cwd=wt, env=c.env(), capture_output=True,
                              text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        tip = g(wt, "rev-parse", "HEAD")
        self.assertEqual(g(c.repo, "rev-parse", "main"), tip)
        self.assertEqual(g(c.remote, "rev-parse", "main"), tip)
        self.assertIn(b"FEATURE", read_file(c.p("a.txt")))  # the main checkout moved too
        self.assertEqual(g(wt, "branch", "--list", "feature/x").strip(), "* feature/x")

    def test_branch_behind_main_is_rebased_before_main_moves(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"})
        g(c.repo, "checkout", "-q", "-b", "feature")
        g(c.repo, "checkout", "-q", "main")
        write_file(c.p("b.txt"), "main moved\n")
        g(c.repo, "commit", "-qam", "main moved")
        g(c.repo, "checkout", "-q", "feature")
        c.t.edit(c.p("a.txt"), "line 3", "FEATURE")
        code, out, err = c.land()
        self.assertEqual(code, 0, out + err)
        self.assertIn("Rebase:   done", out)
        self.assertEqual(g(c.repo, "rev-parse", "main"), g(c.repo, "rev-parse", "feature"))
        self.assertEqual(g(c.repo, "log", "--format=%s", "main").split("\n")[:3],
                         ["Session work", "main moved", "init"])

    def worktree_case(self, files):
        c = Case(files, remote=True)
        wt = os.path.join(os.path.realpath(mkdir()), "wt")
        g(c.repo, "worktree", "add", "-q", "-b", "feature", wt)
        c.wt = wt
        c.s = Session(c.cfg, wt)
        c.t = c.s.main
        return c

    def apply_in(self, c, cwd):
        proc = subprocess.run([sys.executable, SCRIPT, "apply", "--plan",
                               os.path.join(c.run_dir, "plan.json"), "--message-file", "-"],
                              input="Feature work\n", cwd=cwd, env=c.env(), capture_output=True,
                              text=True)
        return proc.returncode, proc.stdout, proc.stderr

    def test_dirty_main_checkout_that_blocks_the_fast_forward_stops(self):
        c = self.worktree_case({"a.txt": lines(5), "b.txt": "b\n"})
        write_file(c.p("a.txt"), "uncommitted in the main checkout\n")
        c.t.edit(os.path.join(c.wt, "a.txt"), "line 2", "FEATURE")
        code, out, err, plan = c.plan(repo=c.wt)
        code, out, err = self.apply_in(c, c.wt)
        self.assertEqual(code, 1, out + err)
        self.assertIn("Into main: stop — main not moved", out)
        self.assertEqual(read_file(c.p("a.txt")), b"uncommitted in the main checkout\n")
        self.assertEqual(g(c.repo, "rev-parse", "main"), g(c.remote, "rev-parse", "main"))

    def test_repository_that_merges_with_no_ff_gets_a_merge_commit(self):
        c = self.worktree_case({"a.txt": lines(5), "b.txt": "b\n"})
        for n in range(2):
            g(c.repo, "checkout", "-q", "-b", f"old{n}")
            write_file(c.p("b.txt"), f"b{n}\n")
            g(c.repo, "commit", "-qam", f"old work {n}")
            g(c.repo, "checkout", "-q", "main")
            g(c.repo, "merge", "-q", "--no-ff", "-m", f"Merge old{n}", f"old{n}")
        g(c.repo, "push", "-q", "origin", "main")
        g(c.wt, "merge", "-q", "--ff-only", "main")
        c.t.edit(os.path.join(c.wt, "a.txt"), "line 2", "FEATURE")
        code, out, err, plan = c.plan(repo=c.wt)
        self.assertIn("merge --no-ff into main", out)
        code, out, err = self.apply_in(c, c.wt)
        self.assertEqual(code, 0, out + err)
        parents = g(c.repo, "log", "-1", "--format=%P", "main").split()
        self.assertEqual(len(parents), 2)
        self.assertEqual(g(c.remote, "rev-parse", "main"), g(c.repo, "rev-parse", "main"))

    def test_pushed_branch_takes_main_by_merge_not_rebase(self):
        c = self.worktree_case({"a.txt": lines(5), "b.txt": "b\n"})
        write_file(os.path.join(c.wt, "c.txt"), "earlier own work\n")
        g(c.wt, "add", "c.txt")
        g(c.wt, "commit", "-qm", "earlier own work")
        sha = g(c.wt, "rev-parse", "--short", "HEAD").strip()
        c.t.bash("git commit -m 'earlier own work'", f"[feature {sha}] earlier own work")
        g(c.wt, "push", "-q", "-u", "origin", "feature")
        clone_and_push(c.remote, {"b.txt": "upstream\n"})
        c.t.edit(os.path.join(c.wt, "a.txt"), "line 2", "FEATURE")
        code, out, err, plan = c.plan(repo=c.wt)
        self.assertEqual(code, 0, out + err)
        code, out, err = self.apply_in(c, c.wt)
        self.assertEqual(code, 0, out + err)
        self.assertIn("Merge:    done — main merged into branch feature", out)
        self.assertEqual(g(c.remote, "rev-parse", "main"), g(c.wt, "rev-parse", "HEAD"))
        self.assertEqual(g(c.remote, "rev-parse", "feature"), g(c.wt, "rev-parse", "HEAD"))
        self.assertEqual(read_file(c.p("b.txt")), b"upstream\n")

    def test_no_merge_pushes_the_branch_only(self):
        c = Case({"a.txt": lines(5)}, remote=True)
        g(c.repo, "checkout", "-q", "-b", "topic")
        c.t.edit(c.p("a.txt"), "line 3", "TOPIC")
        code, out, err = c.land("--no-merge")
        self.assertEqual(code, 0, out + err)
        self.assertIn("Into main: no — as asked", out)
        self.assertEqual(g(c.remote, "rev-parse", "topic"), g(c.repo, "rev-parse", "topic"))
        self.assertNotEqual(g(c.remote, "rev-parse", "main"), g(c.repo, "rev-parse", "topic"))

    def test_no_push_commits_and_stops_there(self):
        c = Case({"a.txt": lines(5)}, remote=True)
        c.t.edit(c.p("a.txt"), "line 3", "LOCAL")
        code, out, err = c.land("--no-push")
        self.assertEqual(code, 0, out + err)
        self.assertIn("Push:     no — as asked", out)
        self.assertNotEqual(g(c.remote, "rev-parse", "main"), g(c.repo, "rev-parse", "HEAD"))

    def test_foreign_unpushed_commit_on_main_stops_before_push(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
        write_file(c.p("b.txt"), "someone's commit\n")
        g(c.repo, "commit", "-qam", "someone else")
        c.t.edit(c.p("a.txt"), "line 1", "SESSION")
        code, out, err, plan = c.plan()
        self.assertIn("Push:     no — will stop after the commit: main has unpushed commits", out)
        self.assertEqual(plan["verdict"], "go")
        code, out, err = c.apply()
        self.assertEqual(code, 1)
        self.assertIn("Commit:   done", out)
        self.assertIn("someone else", out)
        self.assertNotEqual(g(c.remote, "rev-parse", "main"), g(c.repo, "rev-parse", "HEAD"))

    def test_own_commit_from_a_bash_git_commit_is_published(self):
        c = Case({"a.txt": lines(5)}, remote=True)
        write_file(c.p("a.txt"), "committed by the session through bash\n")
        g(c.repo, "commit", "-qam", "bash commit")
        sha = g(c.repo, "rev-parse", "--short", "HEAD").strip()
        c.t.bash("git commit -am 'bash commit'", f"[main {sha}] bash commit\n 1 file changed")
        code, out, err, plan = c.plan()
        self.assertEqual(plan["verdict"], "go", out)
        code, out, err = c.apply()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(g(c.remote, "rev-parse", "main"), g(c.repo, "rev-parse", "HEAD"))

    def test_unborn_repository_gets_its_first_commit(self):
        c = Case()
        c.t.write(c.p("readme.md"), "# hi\n")
        code, out, err = c.land()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(c.in_commit(), {"readme.md"})


# --- regressions from the review ---------------------------------------------------------

CASE_INSENSITIVE_FS = None


def case_insensitive_fs():
    global CASE_INSENSITIVE_FS
    if CASE_INSENSITIVE_FS is None:
        d = mkdir()
        write_file(os.path.join(d, "Probe"), "x")
        CASE_INSENSITIVE_FS = os.path.exists(os.path.join(d, "probe"))
    return CASE_INSENSITIVE_FS


def apply_in_process(c, *args, before_commit=None, on_git=None, after_commit=None):
    """apply run inside this process, with `before_commit` called just before `git commit`
    (after every check apply makes) and `after_commit` just after it returns: races no hook
    can reproduce, or a kill (raise SystemExit). on_git(args) sees every git call before it
    runs."""
    from unittest import mock
    real = sc.git

    def racing(cwd, *a, **kw):
        if on_git:
            on_git(a)
        the_commit = "commit" in a and a[a.index("commit") - 1] != "-c" and kw.get("index")
        if before_commit and the_commit:
            before_commit()
        proc = real(cwd, *a, **kw)
        if after_commit and the_commit:
            after_commit()
        return proc
    msg = os.path.join(c.run_dir, "msg-in.txt")
    with open(msg, "w") as fh:
        fh.write("Session work\n")
    env = {k: v for k, v in BASE_ENV.items() if k.startswith("GIT_")}
    with mock.patch.object(sc, "git", racing), mock.patch.dict(os.environ, env):
        return sc.main(["apply", "--plan", os.path.join(c.run_dir, "plan.json"),
                        "--message-file", msg, *args])


class WorkflowWrites(unittest.TestCase):
    """A Write over an existing file leaves no patch in a workflow agent's transcript."""

    def test_workflow_write_over_a_file_with_foreign_edits_commits_only_the_agents_change(self):
        c = Case({"a.txt": lines(20)})
        write_file(c.p("a.txt"), lines(20).replace("line 15\n", "FOREIGN 15\n"))
        wf = c.s.subagent("agent-w9", workflow=True)
        wf.read(c.p("a.txt"))
        wf.write(c.p("a.txt"), read_file(c.p("a.txt")).decode().replace("line 5\n", "SESSION 5\n"))
        code, out, err, plan = c.plan()
        st = status_of(plan, "a.txt")
        self.assertEqual((st["status"], st["how"]), ("partial", "computed"), out)
        self.assertEqual(c.apply()[0], 0)
        self.assertEqual(c.show("a.txt"), lines(20).replace("line 5\n", "SESSION 5\n"))
        self.assertIn(b"FOREIGN 15", read_file(c.p("a.txt")))

    def test_workflow_write_after_its_own_edits_is_split_from_the_read(self):
        c = Case({"a.txt": lines(20)})
        write_file(c.p("a.txt"), lines(20).replace("line 18\n", "FOREIGN 18\n"))
        wf = c.s.subagent("agent-w8", workflow=True)
        wf.read(c.p("a.txt"))
        wf.edit(c.p("a.txt"), "line 2\n", "EDIT 2\n")
        wf.write(c.p("a.txt"), read_file(c.p("a.txt")).decode().replace("line 9\n", "WRITE 9\n"))
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.txt")["status"], "partial", out)
        self.assertEqual(c.apply()[0], 0)
        self.assertEqual(c.show("a.txt"), lines(20).replace("line 2\n", "EDIT 2\n")
                         .replace("line 9\n", "WRITE 9\n"))

    def test_workflow_write_over_an_existing_file_it_never_read_is_skipped(self):
        c = Case({"a.txt": lines(20)})
        write_file(c.p("a.txt"), lines(20).replace("line 15\n", "FOREIGN 15\n"))
        wf = c.s.subagent("agent-w7", workflow=True)
        wf.write(c.p("a.txt"), read_file(c.p("a.txt")).decode().replace("line 5\n", "SESSION 5\n"))
        code, out, err, plan = c.plan(lang="ru")
        st = status_of(plan, "a.txt")
        self.assertEqual((st["status"], st["reason"]), ("skipped", "rewrite-unknown"), out)
        self.assertEqual(plan["verdict"], "nothing")
        self.assertIn("агент переписал файл целиком", out)

    def test_workflow_write_over_someone_elses_untracked_file_is_skipped(self):
        c = Case({"a.txt": "a\n"})
        write_file(c.p("notes.txt"), "someone's notes\n")
        wf = c.s.subagent("agent-w6", workflow=True)
        wf.write(c.p("notes.txt"), "someone's notes\nand the agent's line\n")
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "notes.txt")["status"], "skipped", out)

    def test_workflow_write_that_created_the_file_is_committed_whole(self):
        c = Case({"a.txt": "a\n"})
        wf = c.s.subagent("agent-w5", workflow=True)
        wf.write(c.p("new.txt"), "one\n")
        wf.write(c.p("new.txt"), "one\ntwo\n")  # a rewrite of its own file
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "new.txt")["status"], "commit", out)
        self.assertEqual(c.apply()[0], 0)
        self.assertEqual(c.show("new.txt"), "one\ntwo\n")

    def test_read_output_is_parsed_only_when_it_shows_the_whole_file(self):
        self.assertEqual(sc.read_content({}, "1\ta\n2\tb\n3\t", None), "a\nb\n")
        self.assertEqual(sc.read_content({}, "     1\ta\n     2\tb", None), "a\nb")
        self.assertEqual(sc.read_content({}, "1\ta\n2\t\n\n<system-reminder>x</system-reminder>",
                                         None), "a\n")
        self.assertIsNone(sc.read_content({"offset": 10}, "10\ta\n", None))
        self.assertIsNone(sc.read_content({"limit": 5}, "1\ta\n", None))
        self.assertIsNone(sc.read_content({}, "1\ta\n3\tc\n", None))
        self.assertIsNone(sc.read_content({}, "1\ta\n... (truncated)", None))


class AlreadyCommitted(unittest.TestCase):
    """Edits this session committed earlier are in HEAD: only later ones are replayed."""

    def test_edit_of_a_line_committed_earlier_in_the_session_is_committed(self):
        c = Case({"a.txt": lines(10)})
        c.t.edit(c.p("a.txt"), "line 5\n", "SESSION A\n")
        self.assertEqual(c.land()[0], 0)
        c.t.edit(c.p("a.txt"), "SESSION A\n", "SESSION B\n")
        code, out, err, plan = c.plan()
        st = status_of(plan, "a.txt")
        self.assertEqual((st["status"], st["how"]), ("commit", "whole"), out)
        self.assertIn("Foreign:  leaving alone 0 modified files", out)
        self.assertEqual(c.apply()[0], 0)
        self.assertIn("SESSION B", c.show("a.txt"))

    def test_revert_of_work_committed_earlier_in_the_session_is_committed(self):
        c = Case({"a.txt": lines(10)})
        c.t.edit(c.p("a.txt"), "line 5\n", "SESSION A\n")
        self.assertEqual(c.land()[0], 0)
        c.t.edit(c.p("a.txt"), "SESSION A\n", "line 5\n")
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.txt")["status"], "commit", out)
        self.assertEqual(c.apply()[0], 0)
        self.assertEqual(c.show("a.txt"), lines(10))
        self.assertEqual(g(c.repo, "status", "--porcelain"), "")

    def test_new_edit_after_a_commit_next_to_a_foreign_change_is_split(self):
        c = Case({"a.txt": lines(30)})
        c.t.edit(c.p("a.txt"), "line 5\n", "SESSION 5\n")
        self.assertEqual(c.land()[0], 0)
        write_file(c.p("a.txt"), read_file(c.p("a.txt")).replace(b"line 6\n", b"FOREIGN 6\n"))
        c.t.edit(c.p("a.txt"), "line 25\n", "SESSION 25\n")
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.txt")["status"], "partial", out)
        self.assertEqual(c.apply()[0], 0)
        self.assertIn("SESSION 25", c.show("a.txt"))
        self.assertNotIn("FOREIGN", c.show("a.txt"))

    def test_edit_after_the_sessions_own_bash_commit_is_committed(self):
        c = Case({"a.txt": lines(10)})
        c.t.edit(c.p("a.txt"), "line 5\n", "S1\n")
        g(c.repo, "commit", "-qam", "by the session through bash")
        sha = g(c.repo, "rev-parse", "--short", "HEAD").strip()
        c.t.bash("git commit -am x", f"[main {sha}] x")
        c.t.edit(c.p("a.txt"), "S1\n", "S2\n")
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.txt")["status"], "commit", out)
        self.assertEqual(c.apply()[0], 0)
        self.assertIn("S2", c.show("a.txt"))

    def test_workflow_deletion_committed_then_a_new_edit_is_committed(self):
        c = Case({"a.txt": lines(20)})
        wf = c.s.subagent("agent-w2", workflow=True)
        wf.edit(c.p("a.txt"), "line 4\n", "")
        self.assertEqual(c.land()[0], 0)
        c.t.edit(c.p("a.txt"), "line 15\n", "SESSION 15\n")
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.txt")["status"], "commit", out)

    def test_commit_taken_back_by_hand_brings_all_the_edits_back(self):
        c = Case({"a.txt": lines(10)})
        c.t.edit(c.p("a.txt"), "line 5\n", "SESSION A\n")
        self.assertEqual(c.land()[0], 0)
        g(c.repo, "reset", "-q", "HEAD~1")  # the user undid the commit, keeping the change
        c.t.edit(c.p("a.txt"), "line 8\n", "SESSION B\n")
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.txt")["status"], "commit", out)
        self.assertEqual(c.apply()[0], 0)
        self.assertIn("SESSION A", c.show("a.txt"))
        self.assertIn("SESSION B", c.show("a.txt"))

    def test_commit_rebased_by_the_skill_still_counts_as_committed(self):
        c = Case({"a.txt": lines(10), "b.txt": "b\n"}, remote=True)
        clone_and_push(c.remote, {"b.txt": "upstream\n"})
        c.t.edit(c.p("a.txt"), "line 5\n", "SESSION A\n")
        code, out, err = c.land()
        self.assertIn("Rebase:   done", out)
        c.t.edit(c.p("a.txt"), "SESSION A\n", "SESSION B\n")
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.txt")["status"], "commit", out)


class Lookalikes(unittest.TestCase):
    """A hunk matched away from its line, or with context dropped, must be the only match."""

    def test_session_hunk_that_matches_two_lookalike_places_is_not_guessed(self):
        head = ("class A {\n  void One() {\n    if (x == null)\n      return;\n    Run();\n"
                "  }\n  void Two() {\n    var y = 2;\n    if (x == null)\n    Run();\n  }\n}\n")
        session = head.replace("    var y = 2;\n    if (x == null)\n    Run();\n",
                               "    var y = 2;\n    if (x == null)\n      return;\n    Run();\n")
        c = Case({"A.cs": head})
        c.t.edit(c.p("A.cs"), "    var y = 2;\n    if (x == null)\n    Run();\n",
                 "    var y = 2;\n    if (x == null)\n      return;\n    Run();\n")
        cur = read_file(c.p("A.cs")).decode().replace(
            "    var y = 2;\n", "    var y = 2;\n" + "".join(f"    // note {i}\n" for i in range(8)))
        write_file(c.p("A.cs"), cur)
        code, out, err, plan = c.plan()
        st = status_of(plan, "A.cs")
        self.assertIn(st["status"], ("partial", "skipped"), out)  # never "unchanged": work lost
        if st["status"] == "partial":
            self.assertEqual(c.apply()[0], 0)
            self.assertEqual(c.show("A.cs"), session)  # the session's return, in Two(), only

    def test_created_file_edit_is_not_moved_to_lookalike_code(self):
        created = ("class A {\n  void One() {\n    if (x == null)\n    Run();\n  }\n"
                   "  void Two() {\n    if (x == null)\n    Run();\n  }\n}\n")
        c = Case({"README": "r\n"})
        c.t.write(c.p("A.cs"), created)
        cur = read_file(c.p("A.cs")).decode().replace(
            "  void One() {\n", "  void One() {\n" + "".join(f"    // note {i}\n"
                                                          for i in range(6)), 1)
        write_file(c.p("A.cs"), cur)
        c.t.edit(c.p("A.cs"), "    // note 5\n    if (x == null)\n    Run();\n",
                 "    // note 5\n    if (x == null)\n      return;\n    Run();\n")
        code, out, err, plan = c.plan()
        st = status_of(plan, "A.cs")
        if st["status"] in ("commit", "partial"):
            self.assertEqual(c.apply()[0], 0)
            two = c.show("A.cs").split("void Two()")[1]
            self.assertNotIn("return;", two)  # never commit code that was not on disk
        else:
            self.assertEqual(st["status"], "skipped")


class PathCase(unittest.TestCase):

    def test_path_spelt_in_another_case_is_committed_under_the_name_on_disk(self):
        if not case_insensitive_fs():
            self.skipTest("case-sensitive filesystem")
        c = Case({"Assets/Scripts/Old.cs": "class Old {}\n", "Assets/Scripts/Old.cs.meta": "g1\n",
                  "Assets/Scripts.meta": "g0\n"})
        c.t.write(os.path.join(c.repo, "Assets/scripts/New.cs"), "class New {}\n")
        write_file(c.p("Assets/Scripts/New.cs.meta"), "g2\n")
        code, out, err, plan = c.plan()
        self.assertEqual(sorted(f["path"] for f in plan["files"]),
                         ["Assets/Scripts/New.cs", "Assets/Scripts/New.cs.meta"], out)
        self.assertEqual(c.apply()[0], 0)
        tree = g(c.repo, "ls-tree", "-r", "--name-only", "HEAD").split()
        self.assertNotIn("Assets/scripts.meta", tree)
        self.assertFalse(any(t.startswith("Assets/scripts/") for t in tree), tree)

    def test_edit_through_a_misspelt_path_counts_as_the_tracked_file(self):
        if not case_insensitive_fs():
            self.skipTest("case-sensitive filesystem")
        c = Case({"Assets/Scripts/Foo.cs": lines(10)})
        c.t.edit(os.path.join(c.repo, "Assets/scripts/foo.cs"), "line 5\n", "SESSION 5\n")
        code, out, err, plan = c.plan()
        st = status_of(plan, "Assets/Scripts/Foo.cs")
        self.assertEqual(st["status"], "commit", out)

    def test_repository_root_spelt_in_another_case_is_this_repository(self):
        if not case_insensitive_fs():
            self.skipTest("case-sensitive filesystem")
        c = Case({"a.txt": lines(5)})
        wrong = os.path.join(os.path.dirname(c.repo), os.path.basename(c.repo).upper())
        c.t.edit(os.path.join(wrong, "a.txt"), "line 2", "SESSION 2")
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.txt")["status"], "commit", out)
        self.assertEqual(plan["other_repos"], {})


class LineEndings(unittest.TestCase):

    def test_someone_elses_line_ending_conversion_stays_out_of_the_commit(self):
        c = Case({"a.cs": lines(10)})
        c.t.edit(c.p("a.cs"), "line 5\n", "SESSION 5\n")
        write_file(c.p("a.cs"), read_file(c.p("a.cs")).replace(b"\n", b"\r\n"))
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.cs")["status"], "partial", out)
        self.assertEqual(c.apply()[0], 0)
        blob = subprocess.run(["git", "cat-file", "blob", "HEAD:a.cs"], cwd=c.repo,
                              capture_output=True).stdout
        self.assertIn(b"SESSION 5", blob)
        # HEAD's lines all end in LF, so the session's line does too: the CRLF on it came
        # from the conversion, not from the session.
        self.assertEqual(blob.count(b"\r\n"), 0)


class ForeignSafety(unittest.TestCase):

    def worktree_case(self, files):
        c = Case(files, remote=True)
        wt = os.path.join(os.path.realpath(mkdir()), "wt")
        g(c.repo, "worktree", "add", "-q", "-b", "feat", wt, "main")
        c.wt = wt
        c.s = Session(c.cfg, wt)
        c.t = c.s.main
        return c

    def apply_in(self, c, cwd, *args):
        proc = subprocess.run([sys.executable, SCRIPT, "apply", "--plan",
                               os.path.join(c.run_dir, "plan.json"), "--message-file", "-",
                               *args], input="Feature work\n", cwd=cwd, env=c.env(),
                              capture_output=True, text=True)
        return proc.returncode, proc.stdout, proc.stderr

    def test_unfinished_merge_in_mains_checkout_is_left_alone(self):
        c = self.worktree_case({"a.txt": lines(10), "b.txt": lines(10)})
        r = c.repo
        g(r, "checkout", "-q", "-b", "other")
        write_file(c.p("b.txt"), lines(10).replace("line 5\n", "OTHER 5\n"))
        g(r, "commit", "-qam", "other")
        g(r, "checkout", "-q", "main")
        write_file(c.p("b.txt"), lines(10).replace("line 5\n", "MAIN 5\n"))
        g(r, "commit", "-qam", "main5")
        g(r, "push", "-q", "origin", "main")
        g(c.wt, "merge", "-q", "--ff-only", "main")
        g(r, "merge", "other", ok=False)
        write_file(c.p("b.txt"), lines(10).replace("line 5\n", "RESOLVED BY HAND 5\n"))
        g(r, "add", "b.txt")
        c.t.edit(os.path.join(c.wt, "a.txt"), "line 3", "LINE 3")
        code, out, err, plan = c.plan(repo=c.wt)
        code, out, err = self.apply_in(c, c.wt)
        self.assertEqual(code, 1, out + err)
        self.assertIn("an unfinished merge — finish it (git commit) or abort it (git merge "
                      "--abort)", out)
        self.assertTrue(os.path.exists(os.path.join(r, ".git", "MERGE_HEAD")))
        self.assertIn(b"RESOLVED BY HAND 5", read_file(c.p("b.txt")))

    def test_autostash_config_never_stashes_mains_checkout(self):
        c = self.worktree_case({"a.txt": lines(10), "b.txt": lines(10)})
        g(c.repo, "config", "merge.autoStash", "true")
        write_file(c.p("a.txt"), lines(10).replace("line 3\n", "PERSON 3\n"))
        c.t.edit(os.path.join(c.wt, "a.txt"), "line 4", "SESSION 4")
        c.plan(repo=c.wt)
        code, out, err = self.apply_in(c, c.wt)
        self.assertEqual(code, 1, out + err)
        self.assertEqual(g(c.repo, "stash", "list"), "")
        self.assertEqual(read_file(c.p("a.txt")), lines(10).replace("line 3\n", "PERSON 3\n")
                         .encode())

    def test_commit_changed_by_a_hook_is_not_recorded_or_published(self):
        c = Case({"a.txt": lines(20)}, remote=True)
        hook = c.p(".git/hooks/pre-commit")
        write_file(hook, '#!/bin/sh\nfiles=$(git diff --cached --name-only --diff-filter=ACM)\n'
                   '[ -n "$files" ] && git add $files\nexit 0\n')
        os.chmod(hook, 0o755)
        c.t.edit(c.p("a.txt"), "line 3\n", "LINE 3 (session)\n")
        write_file(c.p("a.txt"), read_file(c.p("a.txt")).replace(b"line 18\n", b"FOREIGN 18\n"))
        c.plan()
        code, out, err = c.apply()
        self.assertEqual(code, 1, out + err)
        self.assertIn("differs from the plan in: a.txt", out)
        self.assertIn("git update-ref HEAD", out)
        code, out, err, plan = c.plan()
        code, out, err = c.apply()
        self.assertNotIn("FOREIGN 18", g(c.remote, "show", "main:a.txt"))
        self.assertIn("not this session's", out)

    def test_merge_started_between_plan_and_apply_makes_the_plan_stale(self):
        c = Case({"a.txt": lines(10), "b.txt": lines(10)})
        r = c.repo
        g(r, "checkout", "-q", "-b", "other")
        write_file(c.p("b.txt"), lines(10).replace("line 5\n", "OTHER 5\n"))
        g(r, "commit", "-qam", "other change")
        g(r, "checkout", "-q", "main")
        c.t.edit(c.p("a.txt"), "line 3", "LINE 3")
        c.plan()
        head = g(r, "rev-parse", "HEAD")
        g(r, "merge", "--no-commit", "--no-ff", "other")
        code, out, err = c.apply("--no-push")
        self.assertEqual(code, 3, out + err)
        self.assertEqual(g(r, "rev-parse", "HEAD"), head)
        self.assertTrue(os.path.exists(os.path.join(r, ".git", "MERGE_HEAD")))

    def test_head_moved_during_the_commit_takes_the_commit_back(self):
        c = Case({"a.txt": lines(10), "b.txt": lines(10)})
        r = c.repo
        c.t.edit(c.p("a.txt"), "line 3", "LINE 3")
        c.plan()

        def other_commit():
            write_file(c.p("b.txt"), lines(10).replace("line 9\n", "OTHER 9\n"))
            g(r, "commit", "-qm", "other session", "--", "b.txt")
        other = None
        code = apply_in_process(c, "--no-push", before_commit=other_commit)
        other = g(r, "log", "-1", "--format=%s")
        self.assertEqual(code, 3)
        self.assertEqual(other, "other session\n")
        self.assertIn("OTHER 9", c.show("b.txt"))

    def test_merge_begun_during_the_commit_is_given_back(self):
        c = Case({"a.txt": lines(10), "b.txt": lines(10)})
        r = c.repo
        g(r, "checkout", "-q", "-b", "other")
        write_file(c.p("b.txt"), lines(10).replace("line 5\n", "OTHER 5\n"))
        g(r, "commit", "-qam", "other change")
        g(r, "checkout", "-q", "main")
        c.t.edit(c.p("a.txt"), "line 3", "LINE 3")
        c.plan()
        head = g(r, "rev-parse", "HEAD")
        code = apply_in_process(c, "--no-push", before_commit=lambda: g(
            r, "merge", "--no-commit", "--no-ff", "other"))
        self.assertEqual(code, 3)
        self.assertEqual(g(r, "rev-parse", "HEAD"), head)
        self.assertTrue(os.path.exists(os.path.join(r, ".git", "MERGE_HEAD")))

    def test_declared_folder_takes_only_its_tracked_changes(self):
        c = Case({"src/gen/a.cs": lines(5), "src/gen/b.cs": lines(5), "other.txt": "x\n"})
        write_file(c.p("src/gen/a.cs"), lines(5).replace("line 1", "GEN 1"))
        write_file(c.p("src/gen/scratch_notes.txt"), "person's private notes\n")
        code, out, err, plan = c.plan("--declare", "src/gen")
        self.assertEqual(sorted(f["path"] for f in plan["files"]), ["src/gen/a.cs"])
        self.assertIn("src/gen/scratch_notes.txt", out)
        code, out, err, plan = c.plan("--declare", ".")
        self.assertEqual(plan["files"], [])
        self.assertIn("refused", out)

    def test_declared_folder_takes_untracked_files_this_session_created_or_edited(self):
        c = Case({"src/gen/a.cs": lines(5), "other.txt": "x\n"})
        write_file(c.p("src/gen/kept.cs"), "untracked before the session\n")
        c.t.write(c.p("src/gen/new.cs"), lines(5))
        c.t.edit(c.p("src/gen/new.cs"), "line 2", "SESSION 2")
        write_file(c.p("src/gen/new.cs"), read_file(c.p("src/gen/new.cs")) + b"by sed -i\n")
        c.t.edit(c.p("src/gen/kept.cs"), "untracked before", "edited in")
        write_file(c.p("src/gen/a.cs"), lines(5).replace("line 1", "GEN 1"))  # generator
        write_file(c.p("src/gen/notes.txt"), "someone's notes\n")
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "src/gen/new.cs")["status"], "partial", out)
        self.assertEqual(status_of(plan, "src/gen/kept.cs")["reason"], "untracked-before", out)
        code, out, err, plan = c.plan("--declare", "src/gen")
        self.assertEqual(code, 0, out + err)
        taken = {f["path"]: (f["status"], f.get("reason")) for f in plan["files"]}
        self.assertEqual(taken, {"src/gen/a.cs": ("commit", "declared"),
                                 "src/gen/kept.cs": ("commit", "declared"),
                                 "src/gen/new.cs": ("commit", "declared")}, out)
        self.assertEqual(plan["declare_untracked"], ["src/gen/notes.txt"])
        self.assertEqual(plan["declare_not_found"], [])
        self.assertIn("not taken — declare each one that is this session's by its path): "
                      "src/gen/notes.txt\n", out)
        self.assertEqual(c.apply("--no-push")[0], 0)
        self.assertEqual(g(c.repo, "show", "HEAD:src/gen/new.cs"),
                         read_file(c.p("src/gen/new.cs")).decode())
        self.assertEqual(g(c.repo, "status", "--porcelain"), "?? src/gen/notes.txt\n")

    def test_declared_folder_of_only_untracked_session_files_is_not_reported_unchanged(self):
        c = Case({"a.txt": "a\n"})
        c.t.write(c.p("tools/gen.py"), "print(1)\n")
        write_file(c.p("tools/gen.py"), "print(2)\n")  # rewritten through Bash
        code, out, err, plan = c.plan("--declare", "tools")
        self.assertEqual(status_of(plan, "tools/gen.py")["reason"], "declared", out)
        self.assertNotIn("declared but not", out)
        self.assertNotIn("untracked files under a declared folder", out)
        code, out, err, plan = c.plan("--declare", c.repo)
        self.assertEqual(plan["declare_refused"], [c.repo])

    def test_locked_index_names_the_paths_to_reset(self):
        c = Case({"a.txt": lines(10), "b.txt": lines(10)})
        c.t.edit(c.p("a.txt"), "line 3", "LINE 3")
        c.plan()
        write_file(c.p(".git/index.lock"), "")
        try:
            code, out, err = c.apply("--no-push")
        finally:
            os.remove(c.p(".git/index.lock"))
        self.assertEqual(code, 1, out + err)
        head = g(c.repo, "rev-parse", "--short", "HEAD").strip()
        self.assertIn(f"git reset -q {head} -- a.txt", out)

    def test_stage_made_during_the_commit_on_a_session_path_is_kept(self):
        c = Case({"a.txt": lines(20)})
        c.t.edit(c.p("a.txt"), "line 3\n", "LINE 3\n")
        write_file(c.p("a.txt"), read_file(c.p("a.txt")).replace(b"line 18\n",
                                                                 b"OTHER SESSION 18\n"))
        c.plan()
        hook = c.p(".git/hooks/pre-commit")
        write_file(hook, '#!/bin/sh\n( unset GIT_INDEX_FILE; git add a.txt )\nexit 0\n')
        os.chmod(hook, 0o755)
        code, out, err = c.apply("--no-push")
        self.assertIn("OTHER SESSION 18", g(c.repo, "diff", "--cached"))
        self.assertIn("someone staged a.txt during the commit", out)

    def test_push_does_not_publish_local_tags(self):
        c = Case({"a.txt": lines(10)}, remote=True)
        g(c.repo, "config", "push.followTags", "true")
        g(c.repo, "tag", "-a", "v9.0.0", "-m", "not ready")
        c.t.edit(c.p("a.txt"), "line 3", "LINE 3")
        self.assertEqual(c.land()[0], 0)
        self.assertEqual(g(c.remote, "tag", "-l"), "")

    def test_someone_elses_chmod_stays_out_of_a_whole_commit(self):
        c = Case({"run.sh": lines(10)})
        os.chmod(c.p("run.sh"), 0o755)
        c.t.edit(c.p("run.sh"), "line 3", "LINE 3")
        self.assertEqual(c.land("--no-push")[0], 0)
        self.assertTrue(g(c.repo, "ls-tree", "HEAD", "run.sh").startswith("100644"))


class BranchFlows(unittest.TestCase):

    def apply_cwd(self, c, cwd, *args):
        proc = subprocess.run([sys.executable, SCRIPT, "apply", "--plan",
                               os.path.join(c.run_dir, "plan.json"), "--message-file", "-",
                               *args], input="Session work\n", cwd=cwd, env=c.env(),
                              capture_output=True, text=True)
        return proc.returncode, proc.stdout, proc.stderr

    def test_main_held_by_a_deleted_worktree_stops_with_a_status_line(self):
        c = Case({"a.txt": lines(5)}, remote=True)
        g(c.repo, "checkout", "-q", "-b", "feature")
        mw = os.path.join(os.path.realpath(mkdir()), "mainwt")
        g(c.repo, "worktree", "add", "-q", mw, "main")
        shutil.rmtree(mw)
        c.t.edit(c.p("a.txt"), "line 3", "FEATURE")
        c.plan()
        code, out, err = c.apply()
        self.assertEqual(code, 1, out + err)
        self.assertIn("git worktree prune", out)
        self.assertNotIn("Traceback", err)
        self.assertTrue(os.path.exists(os.path.join(c.run_dir, "result.json")))

    def test_local_main_ahead_by_own_commits_and_behind_origin_is_published(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
        g(c.repo, "checkout", "-q", "-b", "feature")
        c.t.edit(c.p("a.txt"), "line 3", "FEATURE")
        self.assertEqual(c.land("--no-push")[0], 0)
        clone_and_push(c.remote, {"b.txt": "upstream\n"})
        code, out, err, plan = c.plan()
        self.assertEqual(plan["verdict"], "go", out)
        code, out, err = c.apply()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(g(c.remote, "rev-parse", "main"), g(c.repo, "rev-parse", "main"))
        self.assertIn("FEATURE", g(c.remote, "show", "main:a.txt"))
        self.assertEqual(g(c.remote, "show", "main:b.txt"), "upstream\n")

    def test_push_that_lost_a_race_lands_on_the_rerun_it_asks_for(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
        other = os.path.realpath(mkdir())
        g(other, "clone", "-q", c.remote, ".")
        write_file(os.path.join(other, "b.txt"), "teammate\n")
        g(other, "commit", "-qam", "teammate")
        flag = os.path.join(other, ".fired")
        hook = c.p(".git/hooks/pre-push")
        write_file(hook, f"#!/bin/sh\nif [ ! -e {flag} ]; then touch {flag}; git -C {other} push "
                   "-q origin main >/dev/null 2>&1; fi\nexit 0\n")
        os.chmod(hook, 0o755)
        g(c.repo, "checkout", "-q", "-b", "feature")
        c.t.edit(c.p("a.txt"), "line 3", "FEATURE")
        code, out, err = c.land()
        self.assertEqual(code, 1, out + err)
        self.assertIn("run /commit again", out)
        code, out, err = c.land()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(g(c.remote, "show", "main:b.txt"), "teammate\n")
        self.assertIn("FEATURE", g(c.remote, "show", "main:a.txt"))

    def test_plan_judges_the_branch_against_origin_main_when_local_main_is_stale(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
        clone_and_push(c.remote, {"b.txt": "upstream\n"})
        g(c.repo, "fetch", "-q")
        g(c.repo, "checkout", "-q", "-b", "feature", "--no-track", "origin/main")
        write_file(c.p("a.txt"), lines(5).replace("line 1", "own"))
        g(c.repo, "commit", "-qam", "own via bash")
        sha = g(c.repo, "rev-parse", "--short", "HEAD").strip()
        c.t.bash("git commit -am 'own via bash'", f"[feature {sha}] own via bash")
        code, out, err, plan = c.plan()
        self.assertEqual(code, 0, out + err)
        self.assertNotIn("upstream change", out)
        self.assertEqual(plan["verdict"], "go")

    def test_nothing_to_publish_does_not_stop_on_a_dirty_tree(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n", "c.txt": "c\n"}, remote=True)
        c.t.edit(c.p("a.txt"), "line 3", "SESSION")
        g(c.repo, "commit", "-qam", "session work")
        g(c.repo, "push", "-q", "origin", "main")
        clone_and_push(c.remote, {"b.txt": "upstream\n"})
        g(c.repo, "fetch", "-q", "origin")
        write_file(c.p("c.txt"), "foreign uncommitted\n")
        code, out, err, plan = c.plan()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(plan["verdict"], "nothing")
        self.assertIn("Push:     no — origin/main already has everything", out)

    def test_branch_already_in_main_with_main_moved_has_nothing_to_publish(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n", "c.txt": "c\n"}, remote=True)
        g(c.repo, "checkout", "-q", "-b", "feature")
        c.t.edit(c.p("a.txt"), "line 3", "SESSION")
        g(c.repo, "commit", "-qam", "session work")
        g(c.repo, "push", "-q", "origin", "feature:main")
        clone_and_push(c.remote, {"b.txt": "upstream\n"})
        g(c.repo, "fetch", "-q", "origin")
        g(c.repo, "branch", "-f", "main", "origin/main")
        write_file(c.p("c.txt"), "foreign uncommitted\n")
        code, out, err, plan = c.plan()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(plan["verdict"], "nothing")

    def test_branch_without_a_local_main_creates_main(self):
        c = Case({"a.txt": lines(5)}, remote=True)
        g(c.repo, "checkout", "-q", "-b", "feature")
        g(c.repo, "branch", "-q", "-D", "main")
        c.t.edit(c.p("a.txt"), "line 3", "FEATURE")
        code, out, err = c.land("--no-push")
        self.assertEqual(code, 0, out + err)
        self.assertIn("Into main: done — created main", out)
        self.assertEqual(g(c.repo, "rev-parse", "main"), g(c.repo, "rev-parse", "feature"))

    def test_no_merge_with_teammate_commits_on_the_origin_branch_says_to_pull(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
        g(c.repo, "checkout", "-q", "-b", "topic")
        g(c.repo, "push", "-q", "-u", "origin", "topic")
        other = os.path.realpath(mkdir())
        g(other, "clone", "-q", "-b", "topic", c.remote, ".")
        write_file(os.path.join(other, "b.txt"), "teammate\n")
        g(other, "commit", "-qam", "teammate on topic")
        g(other, "push", "-q", "origin", "topic")
        g(c.repo, "fetch", "-q")
        c.t.edit(c.p("a.txt"), "line 3", "TOPIC")
        c.plan("--no-merge")
        code, out, err = c.apply("--no-merge")
        self.assertEqual(code, 1, out + err)
        self.assertIn("origin/topic has 1 commit the branch lacks", out)
        self.assertNotIn("run /commit again", out)

    def test_branch_tracking_origin_main_is_not_pushed_as_itself(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
        for n in range(2):
            g(c.repo, "checkout", "-q", "-b", f"old{n}")
            write_file(c.p("b.txt"), f"b{n}\n")
            g(c.repo, "commit", "-qam", f"old work {n}")
            g(c.repo, "checkout", "-q", "main")
            g(c.repo, "merge", "-q", "--no-ff", "-m", f"Merge old{n}", f"old{n}")
        g(c.repo, "push", "-q", "origin", "main")
        g(c.repo, "fetch", "-q", "origin")
        wt = os.path.join(os.path.realpath(mkdir()), "wt")
        g(c.repo, "worktree", "add", "-q", "-b", "feature", wt, "origin/main")
        c.s = Session(c.cfg, wt)
        c.t = c.s.main
        c.t.edit(os.path.join(wt, "a.txt"), "line 2", "FEATURE")
        code, out, err, plan = c.plan(repo=wt)
        self.assertIn("Push:     yes — origin/main\n", out)
        code, out, err = self.apply_cwd(c, wt)
        self.assertEqual(code, 0, out + err)
        self.assertNotIn("after a rebase", out)

    def test_branch_pushed_without_upstream_takes_main_by_merge(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n", "c.txt": "c\n"}, remote=True)
        g(c.repo, "checkout", "-q", "-b", "feature")
        write_file(c.p("c.txt"), "earlier own work\n")
        g(c.repo, "commit", "-qam", "earlier own work")
        sha = g(c.repo, "rev-parse", "HEAD").strip()
        c.t.bash("git commit -am 'earlier own work'", f"[feature {sha[:7]}] earlier own work")
        g(c.repo, "push", "-q", "origin", "feature")
        clone_and_push(c.remote, {"b.txt": "upstream\n"})
        c.t.edit(c.p("a.txt"), "line 2", "FEATURE")
        c.plan()
        code, out, err = c.apply()
        self.assertEqual(code, 0, out + err)
        self.assertNotIn("Rebase:", out)
        g(c.repo, "merge-base", "--is-ancestor", sha, "main")  # the published commit kept its id

    def test_rebase_blocked_by_an_untracked_file_shows_gits_reason(self):
        c = Case({"a.txt": lines(5)}, remote=True)
        clone_and_push(c.remote, {"new.txt": "from upstream\n"})
        write_file(c.p("new.txt"), "someone's untracked draft\n")
        c.t.edit(c.p("a.txt"), "line 1", "SESSION")
        c.plan()
        code, out, err = c.apply()
        self.assertEqual(code, 1, out + err)
        self.assertIn("untracked working tree files would be overwritten", out)
        self.assertNotIn("conflict", out)
        self.assertEqual(read_file(c.p("new.txt")), b"someone's untracked draft\n")

    def test_sessions_own_bash_merge_of_origin_main_is_not_foreign(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
        g(c.repo, "checkout", "-q", "-b", "feature")
        write_file(c.p("a.txt"), lines(5).replace("line 1", "own earlier"))
        g(c.repo, "commit", "-qam", "own earlier")
        sha = g(c.repo, "rev-parse", "--short", "HEAD").strip()
        c.t.bash("git commit -am 'own earlier'", f"[feature {sha}] own earlier\n 1 file changed")
        clone_and_push(c.remote, {"b.txt": "upstream\n"})
        g(c.repo, "fetch", "-q")
        c.t.bash("git merge --no-edit origin/main", g(c.repo, "merge", "--no-edit", "origin/main"))
        c.t.edit(c.p("a.txt"), "line 3", "SESSION")
        c.plan()
        code, out, err = c.apply()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(g(c.remote, "rev-parse", "main"), g(c.repo, "rev-parse", "feature"))

    def test_commits_are_recorded_under_every_session_id(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
        sa, sb = c.s, Session(c.cfg, c.repo)
        sa.main.edit(c.p("a.txt"), "line 1", "FROM A")
        sb.main.edit(c.p("b.txt"), "b", "FROM B")
        real = c.remote
        g(c.repo, "remote", "set-url", "origin", "/nonexistent/remote.git")
        c.s = sb
        c.plan("--session", sa.sid, "--session", sb.sid)
        self.assertEqual(c.apply()[0], 1)
        g(c.repo, "remote", "set-url", "origin", real)
        c.plan()
        code, out, err = c.apply()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(g(c.remote, "rev-parse", "main"), g(c.repo, "rev-parse", "main"))

    def test_no_network_under_no_push_is_reported_on_the_main_line(self):
        c = Case({"a.txt": lines(5)}, remote=True)
        g(c.repo, "checkout", "-q", "-b", "feature")
        g(c.repo, "remote", "set-url", "origin", "/nonexistent/remote.git")
        c.t.edit(c.p("a.txt"), "line 3", "FEATURE")
        code, out, err = c.land("--no-push")
        self.assertEqual(code, 1, out + err)
        self.assertIn("Into main: stop — no network", out)
        self.assertIn("Push:     no — as asked", out)

    def test_plan_names_foreign_history_before_pushing_to_a_new_origin(self):
        c = Case({"a.txt": lines(5)})
        remote = os.path.realpath(mkdir())
        g(remote, "init", "-q", "--bare", "-b", "main")
        g(c.repo, "remote", "add", "origin", remote)
        c.t.edit(c.p("a.txt"), "line 1", "SESSION")
        code, out, err, plan = c.plan()
        self.assertIn("will stop after the commit: main has unpushed commits that are not this "
                      "session's", out)
        g(c.repo, "checkout", "-q", "-b", "feature")  # the same from a branch going into main
        code, out, err, plan = c.plan()
        self.assertIn("will stop after the commit: main has unpushed commits that are not this "
                      "session's", out)
        code, out, err = c.apply()
        self.assertIn("main has unpushed commits that are not this session's", out)
        self.assertEqual(g(remote, "branch", "--list"), "")

    def test_orphan_branch_is_committed_and_pushed_without_merging(self):
        c = Case({"a.txt": lines(5)}, remote=True)
        g(c.repo, "checkout", "-q", "--orphan", "docs")
        g(c.repo, "rm", "-rfq", "--cached", ".")
        os.remove(c.p("a.txt"))
        c.t.write(c.p("index.md"), "# docs\n")
        code, out, err, plan = c.plan()
        self.assertEqual(code, 0, out + err)
        self.assertIn("a new branch with no history", out)
        code, out, err = c.apply()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(g(c.remote, "rev-parse", "docs"), g(c.repo, "rev-parse", "docs"))
        self.assertEqual(g(c.remote, "log", "--format=%s", "main"), "init\n")


class SkillOutput(unittest.TestCase):

    def test_shell_hints_keep_the_newest_commands_whole_and_name_files_left_out(self):
        c = Case({"a.txt": "a\n", "scripts/check.sh": "echo 1\n"})
        for n in range(45):
            c.t.bash(f"echo {n} > /dev/null; touch /tmp/x{n}")
        agent = c.s.subagent("agent-a0")  # sorts before nothing in particular; runs last
        write_file(c.p("scripts/check.sh"), "echo 1\necho 2\n")
        agent.bash("python3 - <<'EOF'\n" + "x = 1\n" * 60 +
                   "p = \"scripts/check.sh\"\nopen(p, 'a').write('echo 2\\n')\nEOF")
        c.t.edit(c.p("a.txt"), "a", "A")
        code, out, err, plan = c.plan()
        self.assertIn('p = "scripts/check.sh"', plan["bash_hints"][-1])
        self.assertEqual(plan["bash_named"], ["scripts/check.sh"])
        self.assertIn("named by this session's shell commands", out)

    def test_shell_hints_list_only_commands_that_may_touch_this_repository(self):
        c = Case({"a.txt": "a\n", "src/a.txt": "a\n"})
        repo, away = c.repo, os.path.realpath(mkdir())
        write_file(c.p("notes.txt"), "someone's notes\n")
        inside = [
            (f"cd {repo}/src && sed -i '' 's/a/b/' a.txt", away),
            (f"echo x > {repo}/gen.txt", away),
            ("git mv a.txt b.txt", repo),
            (f"S={repo}/src; touch $S/new.txt", away),
            (f"python3 - <<'EOF'\nopen('{repo}/y.txt', 'w')\nEOF", away),
            (f"cd {away}; cd {repo} && rm -f stale.txt", repo),
        ]
        outside = [
            ("rm -rf build && touch notes.txt", away),
            (f"cd {away} && sed -i '' 's/a/b/' notes.txt", repo),
            (f"cp {repo}/a.txt {away}/copy.txt", away),
            (f"rm -rf {away}/tmp", repo),
            (f"cat > {away}/s.py <<'EOF'\nprint('{repo}/x')\nEOF", repo),
            ("ls -la | tee /dev/null; grep -n a a.txt | head", repo),
            (f'cd "$(mktemp -d)" && git init && echo x > a.txt', repo),
        ]
        for cmd, cwd in inside + outside:
            c.t.bash(cmd, cwd=cwd)
        code, out, err, plan = c.plan()
        self.assertEqual(plan["bash_hints"], [cmd for cmd, _ in inside])
        self.assertEqual(plan["bash_hints_elsewhere"], len(outside))
        self.assertIn(f"bash_hints ({len(inside)} run in this repository or naming a path in "
                      f"it; {len(outside)} run elsewhere left out;", out)
        self.assertNotIn("notes.txt", plan["bash_named"])
        self.assertNotIn(away + "/tmp", out)

    def test_shell_hints_print_only_the_newest_few(self):
        c = Case({"a.txt": "a\n"})
        for n in range(sc.SHOW_HINTS + 5):
            c.t.bash(f"touch gen{n}.txt")
        c.t.bash(f"touch gen1.txt")  # run again: listed once, at its newest run
        code, out, err, plan = c.plan()
        total = sc.SHOW_HINTS + 5
        self.assertEqual(plan["bash_hints_total"], total)
        self.assertEqual(plan["bash_hints"][-1], "touch gen1.txt")
        self.assertIn(f"bash_hints ({total} run in this repository or naming a path in it, "
                      f"newest {sc.SHOW_HINTS} shown, {total} in plan.json;", out)
        self.assertEqual(sum(1 for ln in out.split("\n") if ln.startswith("  $ ")),
                         sc.SHOW_HINTS)

    def test_skip_reasons_read_in_the_users_language(self):
        c = Case({"a.txt": "a\n"})
        c.t.write(c.p("n.txt"), lines(10))
        write_file(c.p("n.txt"), lines(10).replace("line 5\n", "FOREIGN 5\n"))  # through Bash
        wf = c.s.subagent("agent-w1", workflow=True)
        wf.edit(c.p("n.txt"), "FOREIGN 5\n", "SESSION 5\n")
        c.t.edit(c.p("a.txt"), "a", "A")
        code, out, err, plan = c.plan(lang="ru")
        skip = [ln for ln in out.split("\n") if ln.startswith("Пропуск:")]
        self.assertEqual(len(skip), 1, out)
        self.assertIn("правку сессии не найти", skip[0])
        self.assertNotRegex(skip[0], r"[a-z]{4,} (?:not|found|text)")

    def test_plan_forecasts_a_stop_after_the_commit_without_ending_the_run(self):
        c = Case({"a.txt": lines(30), "b.txt": "b\n"}, remote=True)
        clone_and_push(c.remote, {"b.txt": "upstream\n"})
        g(c.repo, "fetch", "-q")
        c.t.edit(c.p("a.txt"), "line 20", "SESSION")
        write_file(c.p("a.txt"), read_file(c.p("a.txt")).replace(b"line 1\n", b"FOREIGN\n"))
        code, out, err, plan = c.plan(lang="ru")
        self.assertEqual((code, plan["verdict"]), (0, "go"))
        self.assertIn("Push:     нет — остановлюсь после коммита: origin/main ушёл вперёд", out)

    def test_skill_text_keeps_ticket_commits_to_the_ticket(self):
        with open(os.path.join(HERE, "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        description = text.split("description:", 1)[1].split("\n", 1)[0]
        self.assertIn("/ticket", description)
        self.assertIn("verdict: stop", text)


# --- regressions from the second review ---------------------------------------------------

def precompose_repo():
    """True when git here spells paths in NFC whatever the disk holds (macOS)."""
    r = make_repo()
    return g(r, "config", "--bool", "core.precomposeunicode", ok=False).strip() == "true"


def plan_in_process(cwd, c, *args, temp=None):
    """plan run inside this process from cwd, with sessioncommit.is_temp replaced by `temp`.
    -> (exit code, stdout, stderr)."""
    import contextlib
    import io
    from unittest import mock
    out, err = io.StringIO(), io.StringIO()
    env = dict({k: v for k, v in BASE_ENV.items() if k.startswith("GIT_")},
               CLAUDE_CONFIG_DIR=c.cfg, CLAUDE_CODE_SESSION_ID=c.s.sid)
    old = os.getcwd()
    os.chdir(cwd)
    try:
        with mock.patch.object(sc, "is_temp", temp or sc.is_temp), \
                mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = sc.main(["plan", "--out", mkdir(), *args])
    finally:
        os.chdir(old)
    return code, out.getvalue(), err.getvalue()


class StaleReads(unittest.TestCase):
    """A workflow agent's Write is split by its last view of the file: that view must be one
    that still shows the file."""

    def test_workflow_write_after_a_file_changed_reminder_is_skipped(self):
        c = Case({"a.txt": lines(20)})
        wf = c.s.subagent("agent-w1", workflow=True)
        wf.read(c.p("a.txt"))
        foreign = lines(20).replace("line 15\n", "FOREIGN 15\n")
        write_file(c.p("a.txt"), foreign)
        wf.file_changed(c.p("a.txt"), "15\tFOREIGN 15")
        wf.write(c.p("a.txt"), foreign.replace("line 5\n", "SESSION 5\n"))
        code, out, err, plan = c.plan()
        st = status_of(plan, "a.txt")
        self.assertEqual((st["status"], st["reason"]), ("skipped", "rewrite-unknown"), out)
        self.assertEqual(plan["verdict"], "nothing")

    def test_full_reread_mentioning_unchanged_since_is_the_view_the_write_is_split_by(self):
        head = "# Changelog\n\n" + lines(20)
        c = Case({"CHANGELOG.md": head})
        wf = c.s.subagent("agent-w1", workflow=True)
        wf.read(c.p("CHANGELOG.md"))
        foreign = head.replace("# Changelog\n", "# Changelog\n\n- config format unchanged since "
                                                "1.4\n")
        write_file(c.p("CHANGELOG.md"), foreign)
        wf.read(c.p("CHANGELOG.md"))
        wf.write(c.p("CHANGELOG.md"), foreign.replace("line 10\n", "SESSION 10\n"))
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "CHANGELOG.md")["status"], "partial", out)
        self.assertEqual(c.apply("--no-push")[0], 0)
        self.assertEqual(c.show("CHANGELOG.md"), head.replace("line 10\n", "SESSION 10\n"))
        self.assertIn(b"unchanged since 1.4", read_file(c.p("CHANGELOG.md")))

    def test_read_saying_unchanged_since_last_read_falls_back_to_the_read_before_it(self):
        c = Case({"a.txt": lines(20)})
        write_file(c.p("a.txt"), lines(20).replace("line 15\n", "FOREIGN 15\n"))
        wf = c.s.subagent("agent-w1", workflow=True)
        wf.read(c.p("a.txt"))
        wf.read_unchanged(c.p("a.txt"))
        wf.write(c.p("a.txt"), read_file(c.p("a.txt")).decode().replace("line 5\n", "SESSION 5\n"))
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.txt")["status"], "partial", out)
        self.assertEqual(c.apply("--no-push")[0], 0)
        self.assertEqual(c.show("a.txt"), lines(20).replace("line 5\n", "SESSION 5\n"))


class GitSpelling(unittest.TestCase):
    """Paths as git spells them: NFC on macOS, git's letter case for a deleted file."""

    def setUp(self):
        if not precompose_repo():
            self.skipTest("git does not precompose Unicode paths here")
        self.nfd = lambda t: unicodedata.normalize("NFD", t)
        self.nfc = lambda t: unicodedata.normalize("NFC", t)

    def test_edit_of_a_file_whose_name_is_decomposed_on_disk_is_committed(self):
        c = Case({"a.txt": "a\n"})
        write_file(os.path.join(c.repo, self.nfd("мой.txt")), lines(10))
        g(c.repo, "add", "-A")
        g(c.repo, "commit", "-qm", "nfd file")
        c.t.edit(os.path.join(c.repo, self.nfc("мой.txt")), "line 5\n", "SESSION 5\n")
        code, out, err, plan = c.plan()
        st = status_of(plan, self.nfc("мой.txt"))
        self.assertEqual(st["status"], "commit", out)
        self.assertEqual(c.apply("--no-push")[0], 0)
        self.assertIn("SESSION 5", c.show(self.nfc("мой.txt")))
        self.assertEqual(g(c.repo, "status", "--porcelain"), "")

    def test_declared_file_with_a_decomposed_name_leaves_the_index_in_step(self):
        c = Case({"a.txt": "a\n"})
        write_file(os.path.join(c.repo, self.nfd("мой.txt")), lines(10))
        g(c.repo, "add", "-A")
        g(c.repo, "commit", "-qm", "nfd file")
        write_file(os.path.join(c.repo, self.nfd("мой.txt")), lines(10) + "by sed\n")
        code, out, err, plan = c.plan("--declare", os.path.join(c.repo, self.nfc("мой.txt")))
        code, out, err = c.apply("--no-push")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(g(c.repo, "status", "--porcelain"), "")

    def test_new_file_in_a_decomposed_folder_is_committed_and_the_index_stays_clean(self):
        c = Case({"a.txt": "a\n"})
        write_file(os.path.join(c.repo, self.nfd("новый"), "x.txt"), "old\n")
        g(c.repo, "add", "-A")
        g(c.repo, "commit", "-qm", "nfd dir")
        c.t.write(os.path.join(c.repo, self.nfc("новый"), "new.txt"), "hello\n")
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, self.nfc("новый") + "/new.txt")["status"], "commit", out)
        self.assertEqual(c.apply("--no-push")[0], 0)
        self.assertEqual(g(c.repo, "status", "--porcelain"), "")

    def test_declared_deletion_spelt_in_another_case_is_committed(self):
        if not case_insensitive_fs():
            self.skipTest("case-sensitive filesystem")
        c = Case({"Assets/Foo.cs": "x\n", "b.txt": "b\n"})
        os.remove(c.p("Assets/Foo.cs"))
        code, out, err, plan = c.plan("--declare", c.p("assets/foo.cs"))
        st = status_of(plan, "Assets/Foo.cs")
        self.assertEqual((st["status"], st["how"]), ("commit", "delete"), out)
        self.assertEqual(c.apply("--no-push")[0], 0)
        self.assertEqual(g(c.repo, "ls-files"), "b.txt\n")


class OwnWork(unittest.TestCase):
    """What the session did itself is the session's: quiet commits, failed shell commands,
    its own staging, its own chmod."""

    def test_quiet_bash_git_commit_is_the_sessions_own(self):
        c = Case({"a.txt": "one\ntwo\nthree\n", "b.txt": "x\ny\n"}, remote=True)
        c.t.clock = real_tick
        c.t.edit(c.p("a.txt"), "two", "TWO")
        c.t.bash("git commit -qam 'session commit quiet'", "",
                 between=lambda: g(c.repo, "commit", "-qam", "session commit quiet"))
        time.sleep(0.01)
        c.t.edit(c.p("b.txt"), "y", "Y")
        code, out, err, plan = c.plan()
        self.assertEqual(plan["verdict"], "go", out)
        self.assertIn("Push:     yes — origin/main", out)
        self.assertEqual(sorted(f["path"] for f in plan["files"] if f["status"] == "commit"),
                         ["b.txt"])
        self.assertEqual(c.apply()[0], 0)
        self.assertEqual(g(c.remote, "rev-parse", "main"), g(c.repo, "rev-parse", "HEAD"))

    def test_fast_forward_in_a_quiet_command_does_not_make_the_commit_own(self):
        c = Case({"a.txt": "a\n", "b.txt": "b\n"})
        g(c.repo, "checkout", "-q", "-b", "other")
        write_file(c.p("b.txt"), "someone's\n")
        g(c.repo, "commit", "-qam", "someone's commit")
        g(c.repo, "checkout", "-q", "main")
        time.sleep(1.1)  # the reflog keeps whole seconds: someone's commit, then the session's run
        c.t.clock = real_tick
        c.t.bash("git merge -q other", "", between=lambda: g(c.repo, "merge", "-q", "other"))
        sha = g(c.repo, "rev-parse", "HEAD").strip()
        code, out, err, plan = c.plan()
        self.assertNotIn(sha, plan["own_commits"])

    def test_failed_shell_command_still_gives_hints_and_commit_ids(self):
        c = Case({"a.txt": "one\ntwo\n", "b.txt": "x\ny\n"}, remote=True)
        c.t.edit(c.p("a.txt"), "two", "TWO")
        write_file(c.p("b.txt"), "x\nY\n")
        c.t.bash("sed -i '' s/y/Y/ b.txt && grep -c zzz b.txt", "Exit code 1\n0", error=True)
        code, out, err, plan = c.plan()
        self.assertIn("sed -i '' s/y/Y/ b.txt && grep -c zzz b.txt", plan["bash_hints"])
        self.assertIn("b.txt", plan["bash_named"])
        c = Case({"a.txt": "one\ntwo\n", "b.txt": "x\ny\n"}, remote=True)
        c.t.edit(c.p("a.txt"), "two", "TWO")
        g(c.repo, "commit", "-qam", "session work")
        sha = g(c.repo, "rev-parse", "--short", "HEAD").strip()
        c.t.bash("git commit -am 'session work' && git push", f"Exit code 1\n[main {sha}] session "
                 "work\n 1 file changed\n ! [rejected] main -> main (fetch first)", error=True)
        c.t.edit(c.p("b.txt"), "y", "Y")
        code, out, err, plan = c.plan()
        self.assertEqual(plan["verdict"], "go", out)
        self.assertNotIn("not this session's", out)

    def test_sessions_own_git_mv_rm_and_add_do_not_block(self):
        for how in ("mv", "rm", "add"):
            with self.subTest(how=how):
                c = Case({"a.txt": "one\ntwo\n", "b.txt": "x\ny\n"})
                c.t.edit(c.p("a.txt"), "two", "TWO")
                if how == "mv":
                    g(c.repo, "mv", "b.txt", "c.txt")
                    c.t.bash("git mv b.txt c.txt")
                    args = ["--declare", "b.txt", "--declare", "c.txt"]
                elif how == "rm":
                    g(c.repo, "rm", "-q", "b.txt")
                    c.t.bash("git rm b.txt")
                    args = ["--declare", "b.txt"]
                else:
                    c.t.edit(c.p("b.txt"), "y", "Y")
                    g(c.repo, "add", "b.txt")
                    c.t.bash("git add b.txt")
                    args = []
                code, out, err, plan = c.plan(*args)
                self.assertEqual((code, plan["verdict"]), (0, "go"), out + err)
                self.assertIn("staged: 0", out)
                code, out, err = c.apply()
                self.assertEqual(code, 0, out + err)
                self.assertEqual(g(c.repo, "status", "--porcelain"), "")

    def test_sessions_own_chmod_is_hinted_and_counted_as_left_over(self):
        c = Case({"run.sh": "#!/bin/sh\necho hi\n"})
        c.t.edit(c.p("run.sh"), "echo hi\n", "echo hello\n")
        os.chmod(c.p("run.sh"), 0o755)
        c.t.bash("chmod +x run.sh")
        code, out, err, plan = c.plan()
        self.assertEqual(plan["bash_hints"], ["chmod +x run.sh"])
        self.assertEqual(plan["bash_named"], ["run.sh"])
        self.assertEqual(plan["mode_left"], ["run.sh"])
        self.assertIn("Foreign:  leaving alone 1 modified file", out)
        self.assertIn("exec bit changed on disk but not committed", out)
        code, out, err, plan = c.plan("--declare", "run.sh")
        self.assertEqual(c.apply()[0], 0)
        self.assertTrue(g(c.repo, "ls-tree", "HEAD", "run.sh").startswith("100755"))
        self.assertEqual(g(c.repo, "status", "--porcelain"), "")

    def test_file_identical_to_head_is_unchanged_not_skipped(self):
        c = Case({"a.txt": "one\ntwo\n"}, remote=True)
        wf = c.s.subagent(workflow=True)
        wf.write(c.p("a.txt"), "one\nTWO\nthree\n")
        g(c.repo, "commit", "-qam", "by hand")
        g(c.repo, "push", "-q")
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.txt")["status"], "unchanged", out)
        self.assertNotIn("Skip:", out)
        self.assertEqual(plan["verdict"], "nothing")

    def test_bash_commit_rebased_by_the_skill_still_counts_as_committed(self):
        c = Case({"a.txt": lines(10), "b.txt": "b\n", "c.txt": "c\n"}, remote=True)
        c.t.edit(c.p("a.txt"), "line 5\n", "S1\n")
        g(c.repo, "commit", "-qam", "bash commit")
        sha = g(c.repo, "rev-parse", "--short", "HEAD").strip()
        c.t.bash("git commit -am 'bash commit'", f"[main {sha}] bash commit\n 1 file changed")
        clone_and_push(c.remote, {"c.txt": "upstream\n"})
        c.t.edit(c.p("b.txt"), "b\n", "B\n")
        code, out, err = c.land()
        self.assertIn("Rebase:   done", out)
        c.t.edit(c.p("a.txt"), "S1\n", "S2\n")
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.txt")["status"], "commit", out)

    def test_session_line_stays_lf_when_someone_converts_the_file_to_crlf(self):
        c = Case({"a.txt": lines(20)})
        c.t.edit(c.p("a.txt"), "line 5\n", "SESSION 5\n")
        cur = read_file(c.p("a.txt")).decode()
        write_file(c.p("a.txt"), cur.replace("line 15\n", "FOREIGN 15\n").replace("\n", "\r\n"))
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.txt")["how"], "computed", out)
        self.assertEqual(c.apply("--no-push")[0], 0)
        self.assertEqual(c.show("a.txt"), lines(20).replace("line 5\n", "SESSION 5\n"))


class SafeFlows(unittest.TestCase):

    def test_rebase_never_overwrites_an_ignored_file(self):
        c = Case({"a.txt": lines(5), ".gitignore": "local.env\n"}, remote=True)
        write_file(c.p("local.env"), "TOKEN=secret\n")
        clone_and_push(c.remote, {".gitignore": "", "local.env": "TOKEN=template\n"})
        c.t.edit(c.p("a.txt"), "line 1", "SESSION")
        c.plan()
        code, out, err = c.apply()
        self.assertEqual(code, 1, out + err)
        self.assertIn("Rebase:   stop — origin/main starts tracking files that are git-ignored "
                      "here: local.env", out)
        self.assertEqual(read_file(c.p("local.env")), b"TOKEN=secret\n")
        self.assertEqual(g(c.repo, "log", "-1", "--format=%s"), "Session work\n")

    def test_main_fast_forward_never_overwrites_an_ignored_file_in_mains_checkout(self):
        c = Case({"a.txt": lines(5), ".gitignore": ".vscode/\n"}, remote=True)
        write_file(c.p(".vscode/settings.json"), '{"personal":"mine"}\n')
        clone_and_push(c.remote, {".gitignore": "", ".vscode/settings.json": '{"team":1}\n'})
        wt = os.path.join(os.path.realpath(mkdir()), "wt")
        g(c.repo, "worktree", "add", "-q", "-b", "feat", wt)
        c.s = Session(c.cfg, wt)
        c.t = c.s.main
        c.t.edit(os.path.join(wt, "a.txt"), "line 2", "FEATURE")
        c.plan(repo=wt)
        proc = subprocess.run([sys.executable, SCRIPT, "apply", "--plan",
                               os.path.join(c.run_dir, "plan.json"), "--message-file", "-"],
                              input="Feature work\n", cwd=wt, env=c.env(), capture_output=True,
                              text=True)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("main not moved", proc.stdout)
        self.assertEqual(read_file(c.p(".vscode/settings.json")), b'{"personal":"mine"}\n')

    def test_rebased_commits_are_recorded_before_the_push(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
        clone_and_push(c.remote, {"b.txt": "upstream\n"})
        c.t.edit(c.p("a.txt"), "line 1", "SESSION")
        c.plan()
        seen = {}

        def at_push(a):
            if "push" in a and not seen:
                seen["records"] = sc.load_records(c.cfg, [c.s.sid])
                seen["head"] = g(c.repo, "rev-parse", "HEAD").strip()
        self.assertEqual(apply_in_process(c, on_git=at_push), 0)
        self.assertIn(seen["head"], seen["records"]["commits"])
        self.assertIn(seen["head"], seen["records"]["rewritten"].values())

    def test_index_left_stale_by_a_locked_commit_is_put_back_on_the_next_run(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
        c.t.edit(c.p("a.txt"), "line 3", "LINE 3")
        c.plan()
        write_file(c.p(".git/index.lock"), "")
        try:
            self.assertEqual(c.apply()[0], 1)
        finally:
            os.remove(c.p(".git/index.lock"))
        self.assertEqual(g(c.repo, "status", "--porcelain"), "MM a.txt\n")
        code, out, err, plan = c.plan()
        self.assertEqual((code, plan["verdict"]), (0, "go"), out)
        self.assertIn("Index:    will put back the entries my commit", out)
        self.assertIn("leaving alone 0 modified files, 0 untracked, staged: 0", out)
        code, out, err = c.apply()
        self.assertEqual(code, 0, out + err)
        self.assertIn("Index:    done", out)
        self.assertEqual(g(c.repo, "status", "--porcelain"), "")
        write_file(c.p("b.txt"), "b changed\n")
        g(c.repo, "add", "b.txt")
        g(c.repo, "commit", "-qm", "person's commit")
        self.assertIn("LINE 3", c.show("a.txt"))

    def test_stale_index_alone_is_still_put_back_without_a_remote(self):
        c = Case({"a.txt": lines(5)})
        c.t.edit(c.p("a.txt"), "line 3", "LINE 3")
        c.plan()
        write_file(c.p(".git/index.lock"), "")
        try:
            self.assertEqual(c.apply()[0], 1)
        finally:
            os.remove(c.p(".git/index.lock"))
        code, out, err, plan = c.plan()
        self.assertEqual(plan["verdict"], "go", out)
        self.assertEqual(c.apply()[0], 0)
        self.assertEqual(g(c.repo, "status", "--porcelain"), "")

    def test_branch_switched_during_the_commit_takes_the_commit_back(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
        c.t.edit(c.p("a.txt"), "line 3", "LINE 3")
        c.plan()
        head = g(c.repo, "rev-parse", "HEAD")
        code = apply_in_process(c, before_commit=lambda: g(c.repo, "switch", "-q", "-c",
                                                           "person-topic"))
        self.assertEqual(code, 3)
        self.assertEqual(g(c.repo, "rev-parse", "person-topic"), head)
        self.assertEqual(g(c.repo, "rev-parse", "main"), head)
        self.assertEqual(g(c.remote, "rev-parse", "main"), head)

    def test_branch_push_blocked_by_teammate_commits_names_them(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n", "c.txt": "c\n"}, remote=True)
        g(c.repo, "checkout", "-q", "-b", "feat")
        g(c.repo, "push", "-q", "-u", "origin", "feat")
        other = os.path.realpath(mkdir())
        g(other, "clone", "-q", c.remote, ".")
        g(other, "checkout", "-q", "feat")
        write_file(os.path.join(other, "c.txt"), "teammate\n")
        g(other, "commit", "-qam", "teammate on feat")
        g(other, "push", "-q", "origin", "feat")
        g(other, "checkout", "-q", "main")
        write_file(os.path.join(other, "b.txt"), "upstream\n")
        g(other, "commit", "-qam", "upstream main")
        g(other, "push", "-q", "origin", "main")
        c.t.edit(c.p("a.txt"), "line 3", "LINE 3")
        c.plan()
        code, out, err = c.apply()
        self.assertEqual(code, 0, out + err)
        self.assertIn("origin/feat has 1 commit that are not in the branch: ", out)
        self.assertIn("teammate on feat — pull them into the branch", out)
        self.assertNotIn("after a rebase", out)

    def test_prepared_squash_or_cherry_pick_message_is_never_deleted(self):
        for how in ("squash", "cherry"):
            with self.subTest(how=how):
                c = Case({"a.txt": lines(5), "b.txt": "b\n"})
                g(c.repo, "checkout", "-q", "-b", "other")
                write_file(c.p("b.txt"), "other\n")
                g(c.repo, "commit", "-qam", "important message from other")
                g(c.repo, "checkout", "-q", "main")
                c.t.edit(c.p("a.txt"), "line 3", "LINE 3")
                c.plan("--no-push")
                if how == "squash":
                    g(c.repo, "merge", "-q", "--squash", "other")
                    msg = "SQUASH_MSG"
                else:
                    g(c.repo, "cherry-pick", "-n", "other")
                    msg = "MERGE_MSG"
                code, out, err = c.apply("--no-push")
                self.assertEqual(code, 3, out + err)
                self.assertTrue(os.path.exists(c.p(".git/" + msg)))
                code, out, err, plan = c.plan("--no-push")
                self.assertEqual(plan["verdict"], "stop")
                self.assertIn(msg, plan["stop"])


class Surfaces(unittest.TestCase):
    """What the plan shows: where it looks, and how much it prints."""

    def test_scratchpad_clone_is_left_out_of_the_repositories_to_commit(self):
        c = Case({"a.txt": "one\ntwo\nthree\n"})
        scratch = os.path.join(mkdir(), "scratchpad", "kecs")
        os.makedirs(os.path.dirname(scratch))
        g(os.path.dirname(scratch), "clone", "-q", c.repo, scratch)
        home = os.path.realpath(mkdir())
        c.t.edit(os.path.join(c.repo, "a.txt"), "two", "TWO")
        c.t.edit(os.path.join(scratch, "a.txt"), "three", "EXPERIMENT")

        def temp(path):
            return "scratchpad" in path.split(os.sep)
        code, out, err = plan_in_process(home, c, temp=temp)
        self.assertEqual(code, 0, out + err)
        self.assertIn(f"Repository: {c.repo}", out)
        self.assertIn("Elsewhere: temporary clones: 1 — left alone", out)
        self.assertNotIn("kecs", out)
        c2 = Case({"a.txt": "a\n"})
        c2.t.edit(os.path.join(scratch, "a.txt"), "one", "ONE")
        code, out, err = plan_in_process(home, c2, temp=temp)
        self.assertEqual(code, 2)
        self.assertIn("edited files only in 1 temporary clone (left alone)", err)

    def test_unity_batchmode_run_on_the_project_is_a_shell_hint(self):
        repo = os.path.realpath(mkdir())
        cmd = ("/Applications/Unity/Hub/Editor/6000.0.1f1/Unity.app/Contents/MacOS/Unity "
               f"-batchmode -quit -projectPath {repo} -executeMethod Build.Tables")
        self.assertTrue(sc.HINT_RE.search(cmd))
        self.assertTrue(sc.touches_repo(cmd, mkdir(), repo))

    def test_file_names_are_matched_as_whole_words(self):
        cmds = ["sed -i '' s/a/b/ src/check.sh", "touch ~/x/notes.txt.bak", "rm -f my file.txt"]
        words = sc.name_words(cmds)
        for rel, want in (("scripts/check.sh", True), ("check.sh", True), ("notes.txt", False),
                          ("eck.sh", False), ("my file.txt", True), ("file.txt", True)):
            with self.subTest(rel=rel):
                self.assertEqual(sc.named_in(cmds, rel, words), want)
                self.assertEqual(sc.named_in(cmds, rel), want)

    def test_ignored_files_fold_into_one_skip_line(self):
        c = Case({".gitignore": ".tmp/\nbuild/\n", "a.txt": "a\n"})
        for n in range(3):
            c.t.write(c.p(f".tmp/s{n}.py"), "x\n")
        c.t.write(c.p("build/out.txt"), "x\n")
        code, out, err, plan = c.plan(lang="ru")
        skip = [ln for ln in out.split("\n") if ln.startswith("Пропуск:")]
        self.assertEqual(skip, ["Пропуск:  4 файла в .gitignore (.tmp/…, build/…)"], out)
        self.assertEqual(sum(1 for f in plan["files"] if f.get("reason") == "ignored"), 4)

    def test_declared_folders_are_reported_without_contradictions(self):
        c = Case({"README": "r\n"})
        for n in ("run.sh", "plot.py"):
            write_file(c.p("Tools/ecsbench/" + n), "x\n")
        c.t.write(c.p("Assets/Scripts/EcsBench/Bench.cs"), "class B {}\n")
        write_file(c.p("Assets/Scripts/EcsBench/Bench.cs.meta"), "guid: 1\n")
        write_file(c.p("Assets/Scripts/EcsBench.meta"), "guid: 2\n")
        code, out, err, plan = c.plan("--declare", "Tools/ecsbench", "--declare",
                                      "Assets/Scripts/EcsBench", lang="ru")
        self.assertEqual(plan["declare_not_found"], [])
        self.assertEqual(plan["declare_untracked"], ["Tools/ecsbench/plot.py",
                                                     "Tools/ecsbench/run.sh"])
        self.assertIn("2 .meta-спутника", out)


# --- regressions from the third review --------------------------------------------------

def has_object(repo, sha):
    return subprocess.run(["git", "cat-file", "-e", sha + "^{commit}"], cwd=repo, env=BASE_ENV,
                          capture_output=True).returncode == 0


def worktree_session(c, branch):
    """A linked worktree on a new branch, and the session working there."""
    wt = os.path.join(os.path.realpath(mkdir()), "wt")
    g(c.repo, "worktree", "add", "-q", "-b", branch, wt)
    c.s = Session(c.cfg, wt)
    c.t = c.s.main
    return wt


def captured(fn):
    """(fn's result, what it printed)."""
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = fn()
    return res, buf.getvalue()


class SessionCommits(unittest.TestCase):
    """Which commits made while a shell command of the session ran are the session's."""

    def test_commits_others_make_while_a_session_command_runs_stay_foreign(self):
        for how in ("rejected", "read-only", "after-the-end", "not-the-sessions-files"):
            with self.subTest(how=how):
                c = Case({"a.txt": "a\n", "b.txt": "x\ny\n", "c.txt": "c\n"}, remote=True)
                e = int(time.time()) - 600
                c.t.clock = Clock(e - 20)
                c.t.edit(c.p("b.txt"), "y", "Y")
                c.t.edit(c.p("a.txt"), "a", "A")
                made = {}

                def theirs(at, *paths):
                    write_file(c.p("c.txt"), "theirs\n")
                    made["theirs"] = commit_at(c.repo, at, "-qm", "user's own", *paths) \
                        if paths else commit_at(c.repo, at, "-qam", "user's own")

                def during(*steps):
                    def run():
                        for step in steps:
                            step()
                        c.t.clock.at = e + 3.8  # the command's result
                    return run
                c.t.clock.at = e + 0.2
                if how == "rejected":
                    c.t.bash("git commit -am 'session b'", "The user doesn't want to proceed with "
                             "this tool use. The tool use was rejected.", error=True,
                             between=during(lambda: theirs(e + 2)))
                elif how == "read-only":
                    c.t.bash("git merge-base --is-ancestor HEAD origin/main && git log -1",
                             "", between=during(lambda: theirs(e + 2)))
                else:
                    own = during(lambda: made.update(own=commit_at(
                        c.repo, e + 1, "-qm", "session a", "a.txt")),
                        *([lambda: theirs(e + 2, "c.txt")] if how == "not-the-sessions-files"
                          else []))
                    c.t.bash("git commit -qm 'session a' a.txt", "", between=own)
                    if how == "after-the-end":
                        theirs(e + 4)  # the second after the result
                code, out, err, plan = c.plan()
                self.assertNotIn(made["theirs"], plan["own_commits"], out)
                self.assertIn("not this session's: ", out)
                self.assertIn("user's own", out)
                if "own" in made:
                    self.assertIn(made["own"], plan["own_commits"])
                c.apply()
                self.assertFalse(has_object(c.remote, made["theirs"]))

    def test_edit_a_parallel_agent_makes_after_the_sessions_own_commit_is_still_to_commit(self):
        body = "".join(f"l{i}\n" for i in range(1, 15))
        for quiet in (True, False):
            with self.subTest(quiet=quiet):
                c = Case({"a.txt": body, "b.txt": "x\n"})
                e = int(time.time()) - 600
                c.t.clock = Clock(e - 20)
                agent = c.s.subagent("agent-a1")
                agent.clock = Clock(e + 2)
                c.t.edit(c.p("a.txt"), "l2\n", "L2 main\n")
                c.t.clock.at = e + 0.2

                def agent_edits():
                    agent.edit(c.p("a.txt"), "l12\n", "L12 agent\n")
                    c.t.clock.at = e + 3.8
                if quiet:
                    def run():
                        commit_at(c.repo, e + 1, "-qam", "wip")
                        agent_edits()
                    c.t.bash("git commit -qam wip && npm test", "tests ok\n", between=run)
                else:
                    sha = commit_at(c.repo, e + 1, "-qam", "wip")
                    c.t.bash("git commit -am wip && npm test", f"[main {sha[:7]}] wip\n 1 file "
                             "changed\ntests ok\n", between=agent_edits)
                code, out, err, plan = c.plan()
                st = status_of(plan, "a.txt")
                self.assertEqual((st["status"], st.get("committed_events")), ("commit", 1), out)
                self.assertIn("leaving alone 0 modified files", out)
                self.assertEqual(c.apply()[0], 0)
                self.assertIn("L12 agent", c.show("a.txt"))


class DeclaredFiles(unittest.TestCase):

    def test_case_only_rename_commits_the_old_name_as_a_deletion(self):
        if not case_insensitive_fs():
            self.skipTest("case-sensitive filesystem")
        for how in ("git mv", "mv"):
            with self.subTest(how=how):
                c = Case({"Foo.cs": "class Foo {}\n", "b.txt": "x\n"})
                if how == "git mv":
                    g(c.repo, "mv", "Foo.cs", "foo.cs")
                else:
                    os.rename(c.p("Foo.cs"), c.p("foo.cs"))
                c.t.bash(f"{how} Foo.cs foo.cs")
                code, out, err, plan = c.plan("--declare", "Foo.cs", "--declare", "foo.cs")
                self.assertEqual(plan["verdict"], "go", out)
                self.assertEqual((status_of(plan, "Foo.cs")["how"],
                                  status_of(plan, "foo.cs")["how"]), ("delete", "whole"), out)
                self.assertEqual(plan["bash_named"], [])
                code, out, err = c.apply()
                self.assertEqual(code, 0, out + err)
                self.assertEqual(g(c.repo, "ls-tree", "--name-only", "HEAD").split(),
                                 ["b.txt", "foo.cs"])
                self.assertEqual(g(c.repo, "status", "--porcelain"), "")

    def test_new_name_of_a_case_only_rename_declared_alone_stops(self):
        if not case_insensitive_fs():
            self.skipTest("case-sensitive filesystem")
        c = Case({"Foo.cs": "class Foo {}\n", "b.txt": "x\n"})
        os.rename(c.p("Foo.cs"), c.p("foo.cs"))
        code, out, err, plan = c.plan("--declare", "foo.cs")
        self.assertEqual((code, plan["verdict"]), (1, "stop"), out)
        self.assertIn("the commit would hold both Foo.cs and foo.cs, which differ only in letter "
                      "case", out)

    def test_declared_file_whose_only_change_is_its_exec_bit_is_committed(self):
        for arg in ("bin/run.sh", "bin"):
            with self.subTest(declare=arg):
                c = Case({"bin/run.sh": "echo hi\n", "b.txt": "x\n"})
                os.chmod(c.p("bin/run.sh"), 0o755)
                c.t.bash("chmod +x bin/run.sh")
                code, out, err, plan = c.plan("--declare", arg)
                self.assertEqual(status_of(plan, "bin/run.sh")["status"], "commit", out)
                self.assertEqual(c.apply()[0], 0)
                self.assertTrue(g(c.repo, "ls-tree", "HEAD", "bin/run.sh").startswith("100755"))
                self.assertEqual(g(c.repo, "status", "--porcelain"), "")

    def test_declared_paths_are_taken_from_the_repository_root_and_unknown_ones_are_named(self):
        c = Case({"README.md": "root\n", "src/app/README.md": "app\n",
                  "src/app/main.py": "x = 1\n"})
        write_file(c.p("README.md"), "root, by sed\n")
        write_file(c.p("src/app/README.md"), "someone's WIP\n")
        c.t.edit(c.p("src/app/main.py"), "x = 1", "x = 2")
        code, out, err = plan_in_process(c.p("src/app"), c, "--declare", "README.md",
                                         "--declare", "READMEX.md")
        self.assertEqual(code, 0, out + err)
        self.assertIn("\n  + README.md  whole declared\n", out)
        self.assertNotIn("+ src/app/README.md", out)
        self.assertIn("declared but not found in this repository (relative paths are taken from "
                      "its root) or unchanged: READMEX.md", out)

    def test_long_lists_say_how_many_are_left_out(self):
        c = Case({"README": "r\n"})
        for n in range(25):
            write_file(c.p(f"Gen/f{n:02d}.asset"), "x\n")
        code, out, err, plan = c.plan("--declare", "Gen")
        self.assertEqual(len(plan["declare_untracked"]), 25)
        self.assertIn("Gen/f19.asset … (20 of 25 shown; all in plan.json declare_untracked)", out)


class IndexRecords(unittest.TestCase):
    """Index entries and commits a run leaves for the next one: only what it recorded."""

    def test_stage_the_person_made_after_a_commit_is_not_put_back(self):
        for how in ("untrack", "unstage"):
            with self.subTest(how=how):
                c = Case({"a.txt": lines(5), "b.txt": "b\n"})
                c.t.write(c.p("settings.local.json"), '{"token": "abc"}\n')
                c.t.edit(c.p("a.txt"), "line 3", "LINE 3")
                self.assertEqual(c.land("--no-push")[0], 0)
                if how == "untrack":
                    g(c.repo, "rm", "-q", "--cached", "settings.local.json")
                    path = "settings.local.json"
                else:
                    g(c.repo, "reset", "-q", "HEAD~1", "--", "a.txt")
                    path = "a.txt"
                staged = g(c.repo, "ls-files", "-s", path)
                c.t.edit(c.p("b.txt"), "b", "B")
                code, out, err, plan = c.plan("--no-push")
                self.assertEqual(plan["unsynced"], [], out)
                self.assertNotIn("Index:", out)
                self.assertEqual(c.apply("--no-push")[0], 0)
                self.assertEqual(g(c.repo, "ls-files", "-s", path), staged)

    def test_run_killed_right_after_git_commit_is_picked_up_by_the_next_run(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
        c.t.edit(c.p("a.txt"), "line 3", "LINE 3")
        c.plan()

        def killed():
            raise SystemExit(143)
        with self.assertRaises(SystemExit):
            captured(lambda: apply_in_process(c, after_commit=killed))
        self.assertEqual(g(c.repo, "status", "--porcelain"), "MM a.txt\n")
        code, out, err, plan = c.plan()
        self.assertEqual(plan["verdict"], "go", out)
        self.assertIn("Index:    will put back the entries my commit", out)
        self.assertIn("Push:     yes — origin/main", out)
        code, out, err = c.apply()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(g(c.repo, "status", "--porcelain"), "")
        self.assertEqual(g(c.remote, "rev-parse", "main"), g(c.repo, "rev-parse", "HEAD"))
        self.assertEqual(sc.load_records(c.cfg, [c.s.sid])["pending"], [])

    def test_branch_switched_after_the_commit_landed_keeps_it_and_stops(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
        c.t.edit(c.p("a.txt"), "line 3", "LINE 3")
        c.plan()
        old = g(c.repo, "rev-parse", "HEAD").strip()
        code, out = captured(lambda: apply_in_process(
            c, after_commit=lambda: g(c.repo, "switch", "-q", "-c", "topic")))
        self.assertEqual(code, 1, out)
        new = g(c.repo, "rev-parse", "main").strip()
        self.assertNotEqual(new, old)
        self.assertEqual(g(c.repo, "rev-parse", "topic").strip(), new)
        self.assertIn(new, sc.load_records(c.cfg, [c.s.sid])["commits"])
        self.assertIn("HEAD moved to topic during the commit", out)
        self.assertNotIn("taken back", out)
        self.assertEqual(g(c.remote, "rev-parse", "main").strip(), old)
        self.assertEqual(g(c.repo, "status", "--porcelain"), "")


class StopsSayTheWayOut(unittest.TestCase):

    def test_stopped_rebase_is_named_with_its_way_out(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"})
        g(c.repo, "checkout", "-q", "-b", "side")
        write_file(c.p("b.txt"), "side\n")
        g(c.repo, "commit", "-qam", "side")
        g(c.repo, "checkout", "-q", "main")
        write_file(c.p("b.txt"), "main\n")
        g(c.repo, "commit", "-qam", "main")
        g(c.repo, "checkout", "-q", "side")
        g(c.repo, "rebase", "main", ok=False)
        c.t.edit(c.p("a.txt"), "line 3", "LINE 3")
        code, out, err, plan = c.plan()
        self.assertEqual(code, 1, out)
        status = out.split("---")[0]
        self.assertIn("a rebase is in progress — finish it (git rebase --continue) or abort it "
                      "(git rebase --abort)", status)
        self.assertNotIn("detached HEAD", status)

    def test_leftover_squash_message_says_how_to_clear_it(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"})
        g(c.repo, "checkout", "-q", "-b", "other")
        write_file(c.p("b.txt"), "other\n")
        g(c.repo, "commit", "-qam", "other work")
        g(c.repo, "checkout", "-q", "main")
        g(c.repo, "merge", "-q", "--squash", "other")
        g(c.repo, "restore", "--staged", "--worktree", ".")
        c.t.edit(c.p("a.txt"), "line 3", "LINE 3")
        code, out, err, plan = c.plan()
        self.assertEqual(plan["verdict"], "stop", out)
        self.assertIn("commit the prepared change, or, if it was abandoned, delete that file",
                      plan["stop"])
        self.assertIn(os.path.join(".git", "SQUASH_MSG"), plan["stop"])
        self.assertNotIn("--abort", plan["stop"])

    def test_foreign_commit_stops_name_the_way_out(self):
        for where in ("branch, pushed", "branch, local", "local main"):
            with self.subTest(where=where):
                c = Case({"a.txt": "a\n", "b.txt": "b\n"}, remote=True)
                if where == "local main":
                    write_file(c.p("b.txt"), "someone's on main\n")
                    g(c.repo, "commit", "-qam", "someone's on main")
                wt = worktree_session(c, "feature/x")
                if where != "local main":
                    write_file(os.path.join(wt, "b.txt"), "foreign\n")
                    g(wt, "commit", "-qam", "foreign: earlier work")
                if where == "branch, pushed":
                    g(wt, "push", "-q", "-u", "origin", "feature/x")
                c.t.edit(os.path.join(wt, "a.txt"), "a", "a2")
                want = {"branch, pushed": "; to push the branch alone: /commit no-merge",
                        "branch, local": "; merge them into main yourself or drop them from the "
                                         "branch",
                        "local main": "; push or drop them yourself, or /commit no-merge to push "
                                      "the branch alone"}[where]
                code, out, err, plan = c.plan(repo=wt)
                self.assertIn(want, out)
                code, out, err = c.apply()
                self.assertEqual(code, 1, out + err)
                self.assertIn(want, out)
                if where == "branch, pushed":
                    c.plan("--no-merge", repo=wt)
                    code, out, err = c.apply("--no-merge")
                    self.assertEqual(code, 0, out + err)
                    self.assertEqual(g(c.remote, "rev-parse", "feature/x"),
                                     g(wt, "rev-parse", "HEAD"))

    def test_unreadable_message_file_is_an_error_not_a_stop(self):
        c = Case({"a.txt": lines(5)})
        c.t.edit(c.p("a.txt"), "line 3", "LINE 3")
        c.plan()
        proc = subprocess.run([sys.executable, SCRIPT, "apply", "--plan",
                               os.path.join(c.run_dir, "plan.json"), "--message-file",
                               os.path.join(c.run_dir, "missing", "msg.txt")], cwd=c.repo,
                              env=c.env(), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("cannot read the message file", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertEqual(g(c.repo, "rev-list", "--count", "HEAD").strip(), "1")


class PublishingSafely(unittest.TestCase):

    def test_rebase_stops_for_an_ignored_file_in_another_case_or_where_a_folder_comes(self):
        cases = [("folder", "/notes\n", "notes", "my notes\n",
                  {".gitignore": "", "notes/README.md": "team\n"})]
        if case_insensitive_fs():
            cases.append(("case", "*.env\n", "Secrets.env", "TOKEN=mine\n",
                          {".gitignore": "", "secrets.env": "TOKEN=team\n"}))
        for name, ignore, rel, data, upstream in cases:
            with self.subTest(name=name):
                c = Case({"a.txt": lines(5), ".gitignore": ignore}, remote=True)
                write_file(c.p(rel), data)
                clone_and_push(c.remote, upstream)
                c.t.edit(c.p("a.txt"), "line 1", "SESSION")
                c.plan()
                code, out, err = c.apply()
                self.assertEqual(code, 1, out + err)
                self.assertIn("Rebase:   stop — origin/main starts tracking files that are "
                              "git-ignored here: " + rel, out)
                self.assertEqual(read_file(c.p(rel)), data.encode())

    def test_branch_already_in_main_still_pushes_its_own_upstream_and_says_so(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
        wt = worktree_session(c, "feat")
        g(wt, "push", "-q", "-u", "origin", "feat")
        c.t.edit(os.path.join(wt, "a.txt"), "line 3", "LINE 3")
        c.plan("--no-push", repo=wt)
        self.assertEqual(c.apply("--no-push")[0], 0)
        code, out, err, plan = c.plan(repo=wt)
        self.assertIn("Push:     yes — origin/main, origin/feat", out)
        code, out, err = c.apply()
        self.assertEqual(code, 0, out + err)
        self.assertIn("Push:     done — origin/feat", out)
        tip = g(wt, "rev-parse", "HEAD")
        self.assertEqual((g(c.remote, "rev-parse", "main"), g(c.remote, "rev-parse", "feat")),
                         (tip, tip))
        # main pushed by hand, the branch not yet
        c.t.edit(os.path.join(wt, "b.txt"), "b\n", "B\n")
        c.plan("--no-push", repo=wt)
        self.assertEqual(c.apply("--no-push")[0], 0)
        g(c.repo, "push", "-q", "origin", "main")
        code, out, err, plan = c.plan(repo=wt)
        self.assertEqual(plan["verdict"], "go", out)
        self.assertIn("Push:     yes — origin/feat", out)
        code, out, err = c.apply()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(g(c.remote, "rev-parse", "feat"), g(wt, "rev-parse", "HEAD"))

    def test_no_ff_merge_into_main_names_the_branch_plainly(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
        for n in range(2):
            g(c.repo, "checkout", "-q", "-b", f"old{n}")
            write_file(c.p("b.txt"), f"b{n}\n")
            g(c.repo, "commit", "-qam", f"old work {n}")
            g(c.repo, "checkout", "-q", "main")
            g(c.repo, "merge", "-q", "--no-ff", "-m", f"Merge old{n}", f"old{n}")
        g(c.repo, "push", "-q", "origin", "main")
        wt = worktree_session(c, "feature/x")
        c.t.edit(os.path.join(wt, "a.txt"), "line 2", "FEATURE")
        c.plan(repo=wt)
        code, out, err = c.apply()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(g(c.remote, "log", "-1", "--format=%s", "main"),
                         "Merge branch 'feature/x'\n")


class ShellHintsRead(unittest.TestCase):

    def test_relative_paths_from_outside_quoted_spaces_and_subshells(self):
        home = os.path.realpath(mkdir())
        repo = os.path.join(home, "Projects", "app")
        spaced = os.path.join(home, "My project")
        for d in (repo, os.path.join(spaced, "Assets")):
            os.makedirs(d)
        old = os.environ.get("HOME")
        os.environ["HOME"] = home
        try:
            cases = [
                ("sed -i '' s/a/b/ Projects/app/config.yml", home, repo, True),
                ("rm -f Projects/other/x.txt", home, repo, False),
                (f'rm "{spaced}/Assets/Old.cs"', home, spaced, True),
                ("(cd /tmp && tar xzf a.tgz) && rm out.txt", repo, repo, True),
                ("(cd /tmp && rm out.txt)", repo, repo, False),
                ("git -C Projects/app mv a.txt b.txt", home, repo, True),
                ("x=$(cd /tmp && pwd) && rm out.txt", repo, repo, True),
            ]
            for cmd, cwd, root, want in cases:
                with self.subTest(cmd=cmd):
                    self.assertEqual(sc.touches_repo(cmd, cwd, root), want)
        finally:
            if old is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old

    def test_read_only_commands_are_not_shell_hints(self):
        c = Case({"Assets/Scripts/Foo.cs": "class Foo {}\n",
                  "Assets/Scripts/Bar.cs": "class Bar {}\n"})
        write_file(c.p("Assets/Scripts/Foo.cs"), "class Foo { /* someone's WIP */ }\n")
        c.t.edit(c.p("Assets/Scripts/Bar.cs"), "Bar {}", "Bar { int x; }")
        wf = c.s.subagent(workflow=True)
        wf.bash('grep -n "List<Ship>" Assets/Scripts/Foo.cs')
        wf.bash("git merge-base HEAD origin/main && git log --oneline -- Assets/Scripts/Foo.cs")
        code, out, err, plan = c.plan()
        self.assertEqual((plan["bash_hints"], plan["bash_named"]), ([], []), out)
        for cmd in ("echo x > out.txt", "git merge -q other", 'echo "a > b" >> log.txt'):
            with self.subTest(cmd=cmd):
                self.assertTrue(sc.may_write(cmd))



if __name__ == "__main__":
    try:
        unittest.main(verbosity=1)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
