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
TASK_RUNS = os.path.join(TMP, "task-runs")  # the ticket skill's runs root, never the real one
BASE_ENV = {k: v for k, v in os.environ.items()
            if not k.startswith(("GIT_", "CLAUDE_", "KENSEI_"))}
BASE_ENV.update(GIT_CONFIG_GLOBAL=GITCONFIG, GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t",
                GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
                KENSEI_TASK_RUNS_DIR=TASK_RUNS)
os.environ.update(GIT_CONFIG_GLOBAL=GITCONFIG, GIT_CONFIG_NOSYSTEM="1",  # in-process calls
                  KENSEI_TASK_RUNS_DIR=TASK_RUNS)
CLOCK = itertools.count()


def tick():
    """The real clock, a little apart each call: the attribution compares an edit's time with
    the time of HEAD's commits."""
    next(CLOCK)
    time.sleep(0.002)
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


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
        self.extra_env = {}
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
                    CLAUDE_CODE_SESSION_ID=sid or self.s.sid, **self.extra_env)

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


def named_cmds(plan):
    """commands.json: {file: the texts of every command naming it, oldest first}."""
    if not plan["bash_commands"]:
        return {}
    with open(plan["bash_commands"], encoding="utf-8") as fh:
        every = json.load(fh)
    return {p: [every["commands"][n] for n in idx] for p, idx in every["named_by"].items()}


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
        self.assertEqual(plan["verdict"], "skipped")

    def test_adjacent_session_and_foreign_hunks_are_tangled(self):
        c = Case({"a.txt": lines(20)})
        c.t.edit(c.p("a.txt"), "line 10\n", "SESSION 10\n")
        write_file(c.p("a.txt"), read_file(c.p("a.txt")).replace(b"line 11\n", b"FOREIGN 11\n"))
        code, out, err, plan = c.plan()
        st = status_of(plan, "a.txt")
        self.assertEqual((st["status"], st["reason"]), ("skipped", "tangled"), out)
        self.assertIn("entangled with other changes — someone else's, or ones committed outside "
                      "/commit (lines", out)

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
        self.assertEqual(plan["bash_named"], ["old.txt"])
        self.assertIn("named by this session's shell commands (1 changed or untracked here", out)
        self.assertNotIn("$ rm", out)
        code, out, err, plan = c.plan("--declare", "old.txt")
        st = status_of(plan, "old.txt")
        self.assertEqual((st["status"], st["how"]), ("commit", "delete"))
        self.assertIn("whole, unverified: old.txt", out)
        self.assertEqual(c.apply()[0], 0)
        self.assertNotIn("old.txt", g(c.repo, "ls-files"))
        self.assertEqual(g(c.repo, "status", "--porcelain"), "")

    def test_shell_commands_name_the_changed_files_they_mention(self):
        c = Case({"a.txt": "a\n", "src/b.txt": "b\n", "src/c.txt": "c\n"})
        write_file(c.p("src/b.txt"), "b by sed\n")
        write_file(c.p("src/c.txt"), "c by a script\n")
        write_file(c.p("gen.txt"), "generated\n")
        for cmd in ("sed -i '' 's/b/B/' src/b.txt", f"python3 gen.py > {c.repo}/gen.txt",
                    "ls -la 2>/dev/null", "git status --short"):
            c.t.bash(cmd)
        code, out, err, plan = c.plan()
        self.assertEqual(plan["bash_named"], ["src/b.txt", "gen.txt"])  # git status order
        self.assertEqual(named_cmds(plan), {"src/b.txt": ["sed -i '' 's/b/B/' src/b.txt"],
                                            "gen.txt": [f"python3 gen.py > {c.repo}/gen.txt"]})
        self.assertNotIn("$ ", out)


    def test_edited_file_deleted_later_without_declaration_is_skipped(self):
        c = Case({"a.txt": lines(5)})
        c.t.edit(c.p("a.txt"), "line 1", "SESSION")
        os.remove(c.p("a.txt"))
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.txt")["reason"], "deleted")

    def test_notebook_deleted_after_the_sessions_edit_is_skipped_not_committed(self):
        c = Case({"nb.ipynb": '{"cells": []}\n', "x.txt": "x\n"}, remote=True)
        write_file(c.p("nb.ipynb"), '{"cells": [1]}\n')
        c.t.call("NotebookEdit", {"notebook_path": c.p("nb.ipynb"), "new_source": "1"})
        os.rename(c.p("nb.ipynb"), c.p("analysis.ipynb"))  # the user renames it
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "nb.ipynb")["status"], "skipped")
        self.assertEqual(status_of(plan, "nb.ipynb")["reason"], "deleted")
        self.assertEqual(plan["verdict"], "skipped")
        self.assertIn("nb.ipynb", g(c.remote, "ls-tree", "--name-only", "main"))

    def test_binary_file_deleted_after_the_sessions_edit_is_skipped_not_committed(self):
        c = Case({"b.dat": "a\0b\n"})
        c.t.edit(c.p("b.dat"), "a", "A")
        os.remove(c.p("b.dat"))
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "b.dat")["status"], "skipped")
        self.assertEqual(status_of(plan, "b.dat")["reason"], "deleted")

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

    def test_new_unity_asset_without_its_meta_is_skipped_until_unity_imports_it(self):
        c = Case({"ProjectSettings/ProjectVersion.txt": "6000\n", "Assets/Scripts.meta": "g\n",
                  "Assets/Scripts/Old.cs": "class Old {}\n", "Assets/Scripts/Old.cs.meta": "g\n"})
        c.t.write(c.p("Assets/Scripts/Shield.cs"), "class Shield {}\n")
        c.t.write(c.p("Assets/Combat/Hit.cs"), "class Hit {}\n")  # a new folder
        write_file(c.p("Assets/Combat/Hit.cs.meta"), "g\n")  # but no Assets/Combat.meta yet
        c.t.write(c.p("Assets/Notes~/todo.md"), "x\n")  # Unity imports nothing under a ~ folder
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "Assets/Scripts/Shield.cs")["reason"], "no-meta")
        self.assertEqual(status_of(plan, "Assets/Combat/Hit.cs")["reason"], "no-meta")
        self.assertIn("Skip:     Assets/Scripts/Shield.cs — a new asset with no .meta yet — Unity "
                      "has not imported it", out)
        self.assertEqual(status_of(plan, "Assets/Notes~/todo.md")["status"], "commit")
        write_file(c.p("Assets/Scripts/Shield.cs.meta"), "g\n")  # Unity imported them
        write_file(c.p("Assets/Combat.meta"), "g\n")
        self.assertEqual(c.land()[0], 0)
        self.assertEqual(c.in_commit(), {"Assets/Scripts/Shield.cs", "Assets/Scripts/Shield.cs.meta",
                                         "Assets/Combat/Hit.cs", "Assets/Combat/Hit.cs.meta",
                                         "Assets/Combat.meta", "Assets/Notes~/todo.md"})

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
        self.assertIn("Коммит:   да — 1 файл (1 частично: a.txt — другие правки (чужие или "
                      "твои через Bash) останутся в "
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

    def test_origin_moved_on_main_stops_after_the_commit_and_lands_after_a_merge(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
        clone_and_push(c.remote, {"b.txt": "upstream\n"})
        g(c.repo, "fetch", "-q")
        c.t.edit(c.p("a.txt"), "line 1", "SESSION")
        code, out, err, plan = c.plan(lang="ru")
        self.assertIn("origin/main ушёл вперёд на 1 коммит — влей его в main (git merge "
                      "origin/main) и снова /commit", out)
        want = ("origin/main is 1 commit ahead — merge it into main (git merge origin/main) and "
                "run /commit again")
        code, out, err, plan = c.plan()
        self.assertEqual(plan["verdict"], "go", out)
        self.assertIn("Push:     no — will stop after the commit: " + want, out)
        code, out, err = c.apply()
        self.assertEqual(code, 1, out + err)
        self.assertIn("Commit:   done", out)
        self.assertIn("Push:     stop — " + want, out)
        self.assertNotIn("Rebase", out)
        self.assertEqual(read_file(c.p("b.txt")), b"b\n")  # nothing merged by /commit
        self.assertEqual(g(c.remote, "log", "-1", "--format=%s", "main"), "upstream change\n")
        g(c.repo, "merge", "-q", "--no-edit", "origin/main")  # the way out the stop names
        code, out, err, plan = c.plan()
        self.assertEqual(plan["verdict"], "go", out)
        self.assertEqual(c.apply()[0], 0)
        self.assertEqual(g(c.remote, "rev-parse", "main"), g(c.repo, "rev-parse", "HEAD"))

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

    def test_branch_behind_main_stops_naming_the_merge_and_lands_after_it(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"})
        g(c.repo, "checkout", "-q", "-b", "feature")
        g(c.repo, "checkout", "-q", "main")
        write_file(c.p("b.txt"), "main moved\n")
        g(c.repo, "commit", "-qam", "main moved")
        g(c.repo, "checkout", "-q", "feature")
        main = g(c.repo, "rev-parse", "main")
        c.t.edit(c.p("a.txt"), "line 3", "FEATURE")
        code, out, err, plan = c.plan(lang="ru")
        self.assertIn("main ушёл вперёд на 1 коммит — влей main в ветку (git merge main) и снова "
                      "/commit", out)
        want = ("main is 1 commit ahead — merge main into the branch (git merge main) and run "
                "/commit again")
        code, out, err, plan = c.plan()
        self.assertIn("Into main: no — will stop after the commit: " + want, out)
        code, out, err = c.apply()
        self.assertEqual(code, 1, out + err)
        self.assertIn("Commit:   done", out)
        self.assertIn("Into main: stop — " + want, out)
        self.assertEqual(g(c.repo, "rev-parse", "main"), main)
        g(c.repo, "merge", "-q", "--no-edit", "main")
        self.assertEqual(c.land()[0], 0)
        self.assertEqual(g(c.repo, "rev-parse", "main"), g(c.repo, "rev-parse", "feature"))

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


    def test_origin_main_ahead_of_the_branch_stops_and_lands_after_a_merge(self):
        c = self.worktree_case({"a.txt": lines(5), "b.txt": "b\n"})
        clone_and_push(c.remote, {"b.txt": "upstream\n"})
        g(c.wt, "fetch", "-q")
        c.t.edit(os.path.join(c.wt, "a.txt"), "line 2", "FEATURE")
        want = ("origin/main is 1 commit ahead — merge it into the branch (git merge origin/main) "
                "and run /commit again")
        code, out, err, plan = c.plan(repo=c.wt)
        self.assertIn("Into main: no — will stop after the commit: " + want, out)
        main = g(c.repo, "rev-parse", "main")
        code, out, err = self.apply_in(c, c.wt)
        self.assertEqual(code, 1, out + err)
        self.assertIn("Into main: stop — " + want, out)
        self.assertEqual(g(c.repo, "rev-parse", "main"), main)
        g(c.wt, "merge", "-q", "--no-edit", "origin/main")
        c.plan(repo=c.wt)
        code, out, err = self.apply_in(c, c.wt)
        self.assertEqual(code, 0, out + err)
        tip = g(c.wt, "rev-parse", "HEAD")
        self.assertEqual((g(c.repo, "rev-parse", "main"), g(c.remote, "rev-parse", "main")),
                         (tip, tip))
        self.assertEqual(read_file(c.p("b.txt")), b"upstream\n")  # main's checkout moved too

    def test_main_and_origin_main_both_ahead_name_both_merges(self):
        c = self.worktree_case({"a.txt": lines(5), "b.txt": "b\n", "c.txt": "c\n"})
        clone_and_push(c.remote, {"b.txt": "upstream\n"})
        c.t.edit(c.p("c.txt"), "c\n", "on main\n")  # this session's, committed on main
        c.plan("--no-push", repo=c.repo)
        self.assertEqual(c.apply("--no-push")[0], 0)
        g(c.wt, "fetch", "-q")
        c.t.edit(os.path.join(c.wt, "a.txt"), "line 2", "FEATURE")
        code, out, err, plan = c.plan(repo=c.wt)
        self.assertIn("Into main: no — will stop after the commit: main and origin/main diverged "
                      "and are both ahead (2 commits) — merge both into the branch (git merge "
                      "origin/main, then git merge main) and run /commit again", out)
        code, out, err = self.apply_in(c, c.wt)
        self.assertEqual(code, 1, out + err)
        g(c.wt, "merge", "-q", "--no-edit", "origin/main")
        g(c.wt, "merge", "-q", "--no-edit", "main")
        c.plan(repo=c.wt)
        code, out, err = self.apply_in(c, c.wt)
        self.assertEqual(code, 0, out + err)
        tip = g(c.wt, "rev-parse", "HEAD")
        self.assertEqual((g(c.repo, "rev-parse", "main"), g(c.remote, "rev-parse", "main")),
                         (tip, tip))

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

    def test_own_bash_git_commit_is_not_published_and_the_stop_says_why(self):
        for quiet in (False, True):
            with self.subTest(quiet=quiet):
                c = Case({"a.txt": lines(5), "b.txt": "x\ny\n"}, remote=True)
                c.t.edit(c.p("a.txt"), "line 2", "LINE 2")
                g(c.repo, "commit", "-qam", "bash commit")
                sha = g(c.repo, "rev-parse", "--short", "HEAD").strip()
                c.t.bash("git commit -qam 'bash commit'" if quiet else
                         "git commit -am 'bash commit'",
                         "" if quiet else f"[main {sha}] bash commit\n 1 file changed")
                c.t.edit(c.p("b.txt"), "y", "Y")
                code, out, err, plan = c.plan()
                self.assertEqual(plan["verdict"], "go", out)
                self.assertEqual([f["path"] for f in plan["files"] if f["status"] == "commit"],
                                 ["b.txt"])
                self.assertIn("will stop after the commit: main has unpushed commits not made "
                              "by this session's /commit: " + sha, out)
                code, out, err = c.apply()
                self.assertEqual(code, 1, out + err)
                self.assertIn("Commit:   done", out)
                self.assertIn("bash commit — not publishing them; review and push them "
                              "yourself, then run /commit again, or drop them", out)
                self.assertFalse(has_object(c.remote, sha))


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

    def test_workflow_write_over_an_existing_file_is_skipped_with_the_reason(self):
        for how in ("read first", "edited first", "never read"):
            with self.subTest(how=how):
                c = Case({"a.txt": lines(20)})
                write_file(c.p("a.txt"), lines(20).replace("line 15\n", "FOREIGN 15\n"))
                wf = c.s.subagent("agent-w9", workflow=True)
                if how == "read first":
                    wf.read(c.p("a.txt"))
                elif how == "edited first":
                    wf.edit(c.p("a.txt"), "line 2\n", "EDIT 2\n")
                wf.write(c.p("a.txt"),
                         read_file(c.p("a.txt")).decode().replace("line 5\n", "SESSION 5\n"))
                code, out, err, plan = c.plan(lang="ru")
                st = status_of(plan, "a.txt")
                self.assertEqual((st["status"], st["reason"]), ("skipped", "rewrite-unknown"), out)
                self.assertEqual(plan["verdict"], "skipped")
                self.assertIn("Коммит:   нет — правки сессии пропущены (1), см. Пропуск", out)
                self.assertIn("Чужое:    не трогаю 0 изменённых файлов", out)
                self.assertIn("Пропуск:  a.txt — агент перезаписал файл целиком, не знаю, что в "
                              "нём было до этого — передай --declare, если весь файл твой", out)
                code, out, err, plan = c.plan("--declare", "a.txt")
                self.assertEqual(status_of(plan, "a.txt")["status"], "commit", out)

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

    def test_edits_made_after_the_sessions_own_bash_commit_are_committed(self):
        body = "".join(f"l{i}\n" for i in range(1, 15))
        c = Case({"a.txt": body, "b.txt": "x\n"})
        agent = c.s.subagent("agent-a1")
        c.t.edit(c.p("a.txt"), "l2\n", "L2 main\n")
        c.t.bash("git commit -qam wip && npm test", "tests ok\n", between=lambda: (
            g(c.repo, "commit", "-qam", "wip"), agent.edit(c.p("a.txt"), "l12\n", "L12 agent\n")))
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.txt")["status"], "commit", out)
        self.assertIn("leaving alone 0 modified files", out)
        self.assertEqual(c.apply()[0], 0)
        self.assertEqual(c.show("a.txt"), body.replace("l2\n", "L2 main\n")
                         .replace("l12\n", "L12 agent\n"))

    def test_re_edit_after_a_bash_commit_next_to_a_foreign_change_is_skipped_saying_why(self):
        c = Case({"a.txt": lines(30)})
        c.t.edit(c.p("a.txt"), "line 5\n", "S1\n")
        g(c.repo, "commit", "-qam", "by the session through bash")
        c.t.bash("git commit -qam x")
        write_file(c.p("a.txt"), read_file(c.p("a.txt")).replace(b"line 6\n", b"FOREIGN\n"))
        c.t.edit(c.p("a.txt"), "S1\n", "S2\n")
        code, out, err, plan = c.plan()
        st = status_of(plan, "a.txt")
        self.assertEqual((st["status"], st["reason"]), ("skipped", "tangled"), out)
        self.assertIn("entangled with other changes — someone else's, or ones committed "
                      "outside /commit", out)

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

    def test_file_or_folder_whose_case_on_disk_differs_from_git_is_skipped_truthfully(self):
        if not case_insensitive_fs():
            self.skipTest("case-sensitive filesystem")
        for lang, want in (("en", "Skip:     Assets/Scripts/A.cs — git tracks it as "
                                  "Assets/scripts/A.cs — the letter case on disk differs"),
                           ("ru", "git хранит его как Assets/scripts/A.cs — на диске другой "
                                  "регистр букв")):
            c = Case({"Assets/scripts/A.cs": lines(10), "Assets/scripts/B.cs": lines(10),
                      "pc.cs": lines(5)})
            os.rename(c.p("Assets/scripts"), c.p("Assets/Scripts"))  # a folder renamed on disk
            os.rename(c.p("pc.cs"), c.p("PC.cs"))  # a file renamed on disk to its class name
            self.assertEqual(g(c.repo, "status", "--porcelain"), "")
            c.t.edit(c.p("Assets/Scripts/A.cs"), "line 3\n", "SESSION 3\n")
            c.t.edit(c.p("PC.cs"), "line 2\n", "SESSION 2\n")
            code, out, err, plan = c.plan(lang=lang)
            self.assertEqual(plan["verdict"], "skipped", out)
            self.assertEqual((status_of(plan, "PC.cs")["reason"], status_of(plan, "PC.cs")[
                "detail"]), ("case-disk", "pc.cs"), out)
            self.assertIn(want, out)
            self.assertNotIn("untracked before", out)
            self.assertEqual(plan["foreign"], {"modified": 0, "untracked": 0, "staged": 0}, out)

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

    def crlf_case(self):
        return Case({"a.cs": lines(30).replace("\n", "\r\n"), "b.txt": "b\n"})

    def test_crlf_blob_under_text_auto_is_skipped_not_renormalised(self):
        c = self.crlf_case()
        write_file(c.p(".gitattributes"), "* text=auto\n")
        g(c.repo, "add", ".gitattributes")
        g(c.repo, "commit", "-qm", "attrs")
        c.t.edit(c.p("a.cs"), "line 5\n", "SESSION 5\n")
        c.t.edit(c.p("b.txt"), "b", "B")
        code, out, err, plan = c.plan()
        self.assertEqual((status_of(plan, "a.cs")["status"], status_of(plan, "a.cs")["reason"]),
                         ("skipped", "eol"), out)
        self.assertIn("Skip:     a.cs — committing it here would rewrite every line ending", out)
        self.assertEqual(c.apply()[0], 0)
        self.assertEqual(c.in_commit(), {"b.txt"})

    def test_crlf_blob_under_autocrlf_is_skipped_not_renormalised(self):
        c = self.crlf_case()
        g(c.repo, "config", "core.autocrlf", "true")
        c.t.edit(c.p("a.cs"), "line 5\n", "SESSION 5\n")
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.cs")["reason"], "eol", out)

    def test_crlf_blob_under_autocrlf_input_is_skipped_lf_file_still_committed(self):
        c = self.crlf_case()
        g(c.repo, "config", "core.autocrlf", "input")
        c.t.edit(c.p("a.cs"), "line 5\n", "SESSION 5\n")
        c.t.edit(c.p("b.txt"), "b", "B")
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.cs")["reason"], "eol", out)
        self.assertEqual(c.apply()[0], 0)
        self.assertEqual(c.in_commit(), {"b.txt"})


class HiddenFromStatus(unittest.TestCase):

    def test_assume_unchanged_or_skip_worktree_file_edited_by_session_is_skipped_by_name(self):
        for flag in ("--assume-unchanged", "--skip-worktree"):
            c = Case({"a.txt": lines(5)})
            g(c.repo, "update-index", flag, "a.txt")
            c.t.edit(c.p("a.txt"), "line 3", "SESSION")
            code, out, err, plan = c.plan()
            self.assertEqual((status_of(plan, "a.txt")["status"], plan["verdict"]),
                             ("skipped", "skipped"), out)
            self.assertIn("Skip:     a.txt — marked assume-unchanged/skip-worktree in git", out)
            self.assertNotIn("no new changes", out)


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
        self.assertIn("not made by this session's /commit", out)

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

    def test_new_files_of_a_declared_folder_get_a_skip_line_not_a_foreign_count(self):
        for lang, want, foreign in (
                ("en", "Skip:     new files in declared folders not taken (2: gen/x.txt, "
                       "gen/y.txt) — not created by this session's edits; --declare each of yours "
                       "by its path", "Foreign:  leaving alone 0 modified files, 1 untracked"),
                ("ru", "Пропуск:  не взяты новые файлы в объявленных папках (2: gen/x.txt, "
                       "gen/y.txt) — их создала не правка сессии; передай --declare каждого "
                       "своего по пути", "Чужое:    не трогаю 0 изменённых файлов, 1 "
                                         "неотслеживаемый")):
            with self.subTest(lang=lang):
                c = Case({"gen/a.txt": "a\n"})
                c.t.edit(c.p("gen/a.txt"), "a", "A")
                write_file(c.p("gen/x.txt"), "x\n")
                write_file(c.p("gen/y.txt"), "y\n")
                write_file(c.p("other.txt"), "someone\n")
                code, out, err, plan = c.plan("--declare", "gen", lang=lang)
                self.assertEqual(code, 0, out + err)
                self.assertIn(want, out)
                self.assertIn(foreign, out)

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

    def test_local_main_ahead_by_own_commits_and_behind_origin_stops_then_lands(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
        g(c.repo, "checkout", "-q", "-b", "feature")
        c.t.edit(c.p("a.txt"), "line 3", "FEATURE")
        self.assertEqual(c.land("--no-push")[0], 0)
        clone_and_push(c.remote, {"b.txt": "upstream\n"})
        g(c.repo, "fetch", "-q")
        code, out, err, plan = c.plan()
        self.assertEqual((code, plan["verdict"]), (1, "stop"), out)
        self.assertIn("Push:     no — stop: origin/main is 1 commit ahead — merge it into the "
                      "branch (git merge origin/main) and run /commit again", out)
        g(c.repo, "merge", "-q", "--no-edit", "origin/main")
        code, out, err = c.land()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(g(c.remote, "rev-parse", "main"), g(c.repo, "rev-parse", "main"))
        self.assertIn("FEATURE", g(c.remote, "show", "main:a.txt"))
        self.assertEqual(g(c.remote, "show", "main:b.txt"), "upstream\n")

    def test_push_that_lost_a_race_names_the_way_out_and_lands_after_it(self):
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
        self.assertIn("origin got new commits meanwhile; run /commit again", out)
        code, out, err, plan = c.plan()
        self.assertEqual(code, 1, out + err)
        self.assertIn("origin/main is 1 commit ahead — merge it into the branch (git merge "
                      "origin/main) and run /commit again", out)
        g(c.repo, "merge", "-q", "--no-edit", "origin/main")
        code, out, err = c.land()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(g(c.remote, "show", "main:b.txt"), "teammate\n")
        self.assertIn("FEATURE", g(c.remote, "show", "main:a.txt"))

    def test_plan_judges_the_branch_against_origin_main_when_local_main_is_stale(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
        clone_and_push(c.remote, {"b.txt": "upstream\n"})
        g(c.repo, "fetch", "-q")
        g(c.repo, "checkout", "-q", "-b", "feature", "--no-track", "origin/main")
        c.t.edit(c.p("a.txt"), "line 1", "own")
        self.assertEqual(c.land("--no-merge", "--no-push")[0], 0)
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

    def test_branch_without_a_local_main_stops_naming_how_to_create_it(self):
        c = Case({"a.txt": lines(5)}, remote=True)
        g(c.repo, "checkout", "-q", "-b", "feature")
        g(c.repo, "branch", "-q", "-D", "main")
        c.t.edit(c.p("a.txt"), "line 3", "FEATURE")
        want = ("there is no local main branch — create it (git branch main origin/main) and run "
                "/commit again; or /commit no-merge")
        code, out, err, plan = c.plan()
        self.assertIn("Into main: no — will stop after the commit: " + want, out)
        code, out, err = c.apply()
        self.assertEqual(code, 1, out + err)
        self.assertIn("Into main: stop — " + want, out)
        self.assertEqual(g(c.repo, "branch", "--list", "main"), "")
        g(c.repo, "branch", "main", "origin/main")
        self.assertEqual(c.land()[0], 0)
        self.assertEqual(g(c.remote, "rev-parse", "main"), g(c.repo, "rev-parse", "feature"))

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



    def test_merge_of_origin_main_into_the_branch_is_not_foreign(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
        g(c.repo, "checkout", "-q", "-b", "feature")
        c.t.edit(c.p("a.txt"), "line 1", "own earlier")
        self.assertEqual(c.land("--no-merge", "--no-push")[0], 0)
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
        self.assertIn("Into main: stop — fetch failed (", out)
        self.assertIn("Push:     no — as asked", out)

    def test_plan_names_foreign_history_before_pushing_to_a_new_origin(self):
        c = Case({"a.txt": lines(5)})
        remote = os.path.realpath(mkdir())
        g(remote, "init", "-q", "--bare", "-b", "main")
        g(c.repo, "remote", "add", "origin", remote)
        c.t.edit(c.p("a.txt"), "line 1", "SESSION")
        code, out, err, plan = c.plan()
        self.assertIn("will stop after the commit: main has unpushed commits not made by "
                      "this session's /commit", out)
        g(c.repo, "checkout", "-q", "-b", "feature")  # the same from a branch going into main
        code, out, err, plan = c.plan()
        self.assertIn("will stop after the commit: main has unpushed commits not made by "
                      "this session's /commit", out)
        code, out, err = c.apply()
        self.assertIn("main has unpushed commits not made by this session's /commit", out)
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

    def test_shell_commands_of_every_agent_name_files_left_out(self):
        c = Case({"a.txt": "a\n", "scripts/check.sh": "echo 1\n", "docs/notes.md": "n\n"})
        for n in range(45):
            c.t.bash(f"echo {n} > /dev/null; touch /tmp/x{n}")
        agent = c.s.subagent("agent-a0")
        write_file(c.p("scripts/check.sh"), "echo 1\necho 2\n")
        agent.bash("python3 - <<'EOF'\n" + "x = 1\n" * 60 +
                   "p = \"scripts/check.sh\"\nopen(p, 'a').write('echo 2\\n')\nEOF")
        wf = c.s.subagent("agent-w1", workflow=True)
        write_file(c.p("docs/notes.md"), "n2\n")
        wf.bash("sed -i '' s/n/n2/ notes.md && false", "Exit code 1", error=True)
        rejected = c.s.subagent("agent-a2")
        write_file(c.p("untouched.txt"), "someone's\n")
        rejected.bash("rm untouched.txt", "The user doesn't want to proceed with this tool use.",
                      error=True)
        c.t.edit(c.p("a.txt"), "a", "A")
        code, out, err, plan = c.plan()
        # the refused rm never ran: untouched.txt is not named; the failed sed is, marked
        self.assertEqual(plan["bash_named"], ["docs/notes.md", "scripts/check.sh"])
        self.assertEqual(named_cmds(plan)["docs/notes.md"],
                         ["[failed] sed -i '' s/n/n2/ notes.md && false"])
        self.assertEqual(sorted(named_cmds(plan)), plan["bash_named"])
        self.assertIn('p = "scripts/check.sh"', named_cmds(plan)["scripts/check.sh"][0])
        self.assertIn("named by this session's shell commands (2 changed or untracked here", out)


    def test_named_files_are_listed_up_to_twenty_with_the_total(self):
        c = Case({"a.txt": "a\n"})
        for n in range(25):
            write_file(c.p(f"gen/f{n:02d}.txt"), "x\n")
            c.t.bash(f"python3 make.py > gen/f{n:02d}.txt")
        code, out, err, plan = c.plan()
        self.assertEqual(len(plan["bash_named"]), 25)
        self.assertEqual(len(named_cmds(plan)), 25)
        self.assertIn("gen/f19.txt … (20 of 25 shown; all in plan.json bash_named)", out)



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
    """What the session did itself: its own staging and its own chmod."""




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
        self.assertEqual(named_cmds(plan), {"run.sh": ["chmod +x run.sh"]})
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


    def test_main_fast_forward_never_overwrites_an_ignored_file_in_mains_checkout(self):
        c = Case({"a.txt": lines(5), ".gitignore": ".vscode/\n"}, remote=True)
        write_file(c.p(".vscode/settings.json"), '{"personal":"mine"}\n')
        clone_and_push(c.remote, {".gitignore": "", ".vscode/settings.json": '{"team":1}\n'})
        g(c.repo, "fetch", "-q")
        wt = os.path.join(os.path.realpath(mkdir()), "wt")
        g(c.repo, "worktree", "add", "-q", "-b", "feat", wt, "origin/main")
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

    def test_new_files_left_out_of_a_locked_index_are_not_counted_foreign(self):
        c = Case({"a.txt": lines(5)})
        c.t.edit(c.p("a.txt"), "line 3", "LINE 3")
        c.t.write(c.p("n1.txt"), "n1\n")
        c.t.write(c.p("n2.txt"), "n2\n")
        c.plan()
        write_file(c.p(".git/index.lock"), "")
        try:
            self.assertEqual(c.apply()[0], 1)
        finally:
            os.remove(c.p(".git/index.lock"))
        code, out, err, plan = c.plan()
        self.assertEqual(plan["foreign"], {"modified": 0, "untracked": 0, "staged": 0}, out)
        self.assertEqual(plan["bash_named"], [])
        self.assertEqual(c.apply()[0], 0)
        self.assertEqual(g(c.repo, "status", "--porcelain"), "")

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


    def test_shell_names_match_paths_and_unique_file_names_as_whole_words(self):
        cmds = ["sed -i '' s/a/b/ src/check.sh", "touch ~/x/notes.txt.bak",
                "rm -f 'my file.txt'", "cat /Users/me/app/Assets/Big.cs | head",
                "rm -f other\\ name.txt", "git add Docs/Readme.MD"]
        same = lambda t: t  # noqa: E731
        folded = lambda t: unicodedata.normalize("NFC", t).casefold()  # noqa: E731
        paths = ["scripts/check.sh", "notes.txt", "eck.sh", "my file.txt", "Assets/Big.cs",
                 "a/dup.txt", "b/dup.txt", "other name.txt", "docs/readme.md"]
        named, by = sc.shell_named(cmds + ["cat dup.txt"], paths, same)
        self.assertEqual(named, ["scripts/check.sh", "my file.txt", "Assets/Big.cs",
                                 "other name.txt"])
        self.assertNotIn("cat dup.txt", by)
        named, by = sc.shell_named(cmds, paths, folded)
        self.assertIn("docs/readme.md", named)


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
                want = {"branch, pushed": "; if the branch is yours, to push the branch alone: "
                                          "/commit no-merge",
                        "branch, local": "; review them, merge them into main and push it "
                                         "yourself, or drop them from the branch",
                        # the branch holds main's commit too: no-merge would stop on it as well
                        "local main": "; review and push them yourself, then run /commit "
                                      "again\n"}[where]
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


    def test_branch_already_in_main_pushes_main_left_unpushed_earlier(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
        wt = worktree_session(c, "feat")
        g(wt, "push", "-q", "-u", "origin", "feat")
        before = g(c.remote, "rev-parse", "feat")
        c.t.edit(os.path.join(wt, "a.txt"), "line 3", "LINE 3")
        c.plan("--no-push", repo=wt)
        self.assertEqual(c.apply("--no-push")[0], 0)
        code, out, err, plan = c.plan(repo=wt)
        self.assertIn("Into main: no — branch feat is already in main", out)
        self.assertIn("Push:     yes — origin/main\n", out)
        code, out, err = c.apply()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(g(c.remote, "rev-parse", "main"), g(wt, "rev-parse", "HEAD"))
        self.assertEqual(g(c.remote, "rev-parse", "feat"), before)  # the branch is not pushed



# --- regressions from the 2.2.1 review, round 1 -------------------------------------------

def teammate_merge(c, cwd, extra):
    """A merge of origin/main into the checkout at cwd with changes of its own (a hand-resolved
    `git pull`): `extra` maps files to content added inside the merge; "-s ours" makes it a
    merge that keeps the branch's tree and drops origin's change."""
    if extra == "-s ours":
        g(cwd, "merge", "-q", "-s", "ours", "--no-edit", "origin/main")
        return
    g(cwd, "merge", "-q", "--no-commit", "--no-ff", "origin/main")
    for rel, data in extra.items():
        write_file(os.path.join(cwd, rel), data)
        g(cwd, "add", rel)
    g(cwd, "commit", "-q", "--no-edit")


class SessionsAndOwnCommits(unittest.TestCase):

    def test_earlier_sessions_edits_left_out_of_a_commit_are_committed_when_it_is_added(self):
        c = Case({"a.txt": lines(30)})
        earlier = Session(c.cfg, c.repo)
        earlier.main.edit(c.p("a.txt"), "line 2\n", "FROM EARLIER\n")
        c.t.edit(c.p("a.txt"), "line 25\n", "FROM THIS\n")
        self.assertEqual(c.land("--no-push")[0], 0)  # this session alone: its line only
        self.assertNotIn("FROM EARLIER", c.show("a.txt"))
        code, out, err, plan = c.plan("--session", c.s.sid, "--session", earlier.sid)
        st = status_of(plan, "a.txt")
        self.assertEqual((st["status"], st["events"]), ("commit", 1), out)
        self.assertIn("Commit:   yes — 1 file", out)
        self.assertIn("Foreign:  leaving alone 0 modified files", out)
        self.assertEqual(c.apply()[0], 0)
        self.assertIn("FROM EARLIER", c.show("a.txt"))
        self.assertIn("FROM THIS", c.show("a.txt"))

    def test_file_a_bash_commit_left_unsplittable_is_skipped_naming_declare(self):
        def deletion_after_it(c, p, wf):
            wf.edit(p, "line 8\n", "")

        def re_edit_next_to_someones_change(c, p, wf):
            write_file(p, read_file(p).replace(b"line 190\n", b"FOREIGN\n"))
            c.t.edit(p, "S1\n", "S2\n")
        for setup in (deletion_after_it, re_edit_next_to_someones_change):
            with self.subTest(setup=setup.__name__):
                c = Case({"a.txt": lines(200)})
                p = c.p("a.txt")
                c.t.edit(p, "line 5\n", "S1\n")
                c.t.bash("git commit -qam x", between=lambda: g(c.repo, "commit", "-qam", "x"))
                setup(c, p, c.s.subagent("agent-w3", workflow=True))
                code, out, err, plan = c.plan()
                self.assertEqual((status_of(plan, "a.txt")["status"], plan["verdict"]),
                                 ("skipped", "skipped"), out)
                self.assertIn(" — --declare it if the whole file is yours", out)
                code, out, err, plan = c.plan(lang="ru")
                self.assertIn(" — передай --declare, если весь файл твой", out)


    def test_agent_write_over_a_file_the_session_created_and_committed_is_committed(self):
        for creator in ("main session", "workflow agent"):
            with self.subTest(creator=creator):
                c = Case({"a.txt": "a\n"})
                wf = c.s.subagent("agent-w4", workflow=True)
                (c.t if creator == "main session" else wf).write(c.p("new.txt"), "one\n")
                self.assertEqual(c.land("--no-push")[0], 0)
                wf.write(c.p("new.txt"), "one\ntwo\n")
                code, out, err, plan = c.plan()
                st = status_of(plan, "new.txt")
                self.assertEqual((st["status"], st.get("reason")), ("commit", "unverified"), out)
                self.assertEqual(c.apply()[0], 0)
                self.assertEqual(c.show("new.txt"), "one\ntwo\n")

    def test_created_file_committed_by_hand_never_reverts_a_committed_foreign_line(self):
        """The session creates Foo.cs, a person commits it by hand; a teammate commits a line
        into it, then removes it on disk (not committed yet). The commit must not take that
        removal: HEAD's change since the create is not this session's work undone."""
        S = "class Foo {\n  void A() {}\n  void B() {}\n}\n"
        DEBUG = S.replace("  void B", "  // debug: remove me\n  void B")
        for lang, want in (("en", "Foo.cs — this session's edits are entangled with other "
                                  "changes"),
                           ("ru", "Foo.cs — правки сессии сцеплены с другими")):
            for later_edit in (False, True):
                with self.subTest(lang=lang, later_edit=later_edit):
                    c = Case({"README.md": "readme\n"})
                    c.t.write(c.p("Foo.cs"), S)
                    time.sleep(1.1)  # the hand commit comes later than the Write, by the second
                    g(c.repo, "add", "Foo.cs")
                    g(c.repo, "commit", "-qm", "add Foo")  # by hand, outside /commit
                    write_file(c.p("Foo.cs"), DEBUG)
                    g(c.repo, "commit", "-qam", "teammate: debug line")
                    write_file(c.p("Foo.cs"), S)  # the teammate removes it, not committed
                    if later_edit:
                        c.t.edit(c.p("Foo.cs"), "void A() {}", "void A() { Run(); }")
                    c.t.edit(c.p("README.md"), "readme\n", "readme v2\n")
                    code, out, err, plan = c.plan(lang=lang)
                    self.assertEqual(code, 0, out + err)
                    st = status_of(plan, "Foo.cs")
                    self.assertEqual((st["status"], st.get("reason")), ("skipped", "tangled"), out)
                    self.assertIn(want, out)
                    self.assertEqual(c.apply()[0], 0)
                    self.assertIn("// debug: remove me", c.show("Foo.cs"))
                    self.assertEqual(c.show("README.md"), "readme v2\n")

    def test_created_file_committed_by_hand_then_edited_by_the_session_is_committed(self):
        c = Case({"README.md": "readme\n"})
        S = "class Foo {\n  void A() {}\n  void B() {}\n}\n"
        c.t.write(c.p("Foo.cs"), S)
        time.sleep(1.1)
        g(c.repo, "add", "Foo.cs")
        g(c.repo, "commit", "-qm", "add Foo")  # by hand
        write_file(c.p("Foo.cs"), S + "// teammate\n")
        g(c.repo, "commit", "-qam", "teammate line")  # kept on disk too
        c.t.edit(c.p("Foo.cs"), "void A() {}", "void A() { Run(); }")
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "Foo.cs")["status"], "commit", out)
        self.assertEqual(c.apply()[0], 0)
        self.assertEqual(c.show("Foo.cs"), S.replace("void A() {}", "void A() { Run(); }")
                         + "// teammate\n")

    def test_agent_rewrite_of_a_file_the_session_created_is_marked_unverified(self):
        c = Case({"a.txt": "a\n"})
        wf = c.s.subagent("agent-w5", workflow=True)
        wf.write(c.p("docs/plan.md"), "# plan\n")
        write_file(c.p("docs/plan.md"), "# plan\nUSER NOTE: do not ship\n")  # someone, between
        wf.read(c.p("docs/plan.md"))
        wf.write(c.p("docs/plan.md"), "# plan\nUSER NOTE: do not ship\n- step\n")
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "docs/plan.md")["reason"], "unverified", out)
        self.assertIn("Commit:   yes — 1 file (1 whole, unverified: docs/plan.md)", out)


class SkippedWork(unittest.TestCase):

    def test_session_work_that_is_all_skipped_is_reported_as_skipped_not_as_nothing(self):
        c = Case({"a.txt": lines(10)})
        wf = c.s.subagent("agent-w6", workflow=True)
        wf.write(c.p("a.txt"), lines(10).replace("line 5\n", "AGENT 5\n"))
        code, out, err, plan = c.plan()
        self.assertEqual((code, plan["verdict"]), (0, "skipped"), out)
        self.assertIn("Commit:   no — this session's changes are skipped (1), see Skip", out)
        self.assertNotIn("no new changes", out)
        self.assertIn("Foreign:  leaving alone 0 modified files", out)



class EvilMerges(unittest.TestCase):
    """A merge commit with changes of its own is someone's work, whoever made it."""

    def test_merge_commit_with_changes_of_its_own_is_not_published(self):
        for extra in ({"c.txt": "TEAMMATE CHANGE INSIDE THE MERGE\n"}, "-s ours"):
            with self.subTest(extra=extra):
                c = Case({"a.txt": lines(5), "b.txt": "b\n", "c.txt": "c\n"}, remote=True)
                g(c.repo, "checkout", "-q", "-b", "feature")
                c.t.edit(c.p("a.txt"), "line 1", "own earlier")
                self.assertEqual(c.land("--no-merge", "--no-push")[0], 0)
                clone_and_push(c.remote, {"b.txt": "upstream\n"})
                g(c.repo, "fetch", "-q")
                teammate_merge(c, c.repo, extra)
                c.t.edit(c.p("a.txt"), "line 3", "SESSION")
                code, out, err, plan = c.plan()
                self.assertIn("will stop after the commit: branch feature has commits not made "
                              "by this session's /commit", out)
                code, out, err = c.apply()
                self.assertEqual(code, 1, out + err)
                self.assertEqual(g(c.remote, "show", "main:c.txt"), "c\n")
                self.assertEqual(g(c.remote, "show", "main:b.txt"), "upstream\n")

    def test_merge_commit_with_changes_of_its_own_on_main_is_not_published(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n", "c.txt": "c\n", "d.txt": "d\n"},
                 remote=True)
        c.t.edit(c.p("a.txt"), "line 1", "own earlier")
        self.assertEqual(c.land("--no-push")[0], 0)
        clone_and_push(c.remote, {"b.txt": "upstream\n"})
        g(c.repo, "fetch", "-q")
        teammate_merge(c, c.repo, {"c.txt": "TEAMMATE\n"})
        clone_and_push(c.remote, {"d.txt": "upstream again\n"})
        c.t.edit(c.p("a.txt"), "line 3", "SESSION")
        c.plan()
        code, out, err = c.apply()
        self.assertEqual(code, 1, out + err)
        self.assertIn("not made by this session's /commit", out)
        self.assertEqual(c.show("c.txt"), "TEAMMATE\n")
        self.assertEqual(read_file(c.p("c.txt")), b"TEAMMATE\n")
        self.assertEqual(g(c.remote, "show", "main:c.txt"), "c\n")

    def test_clean_merge_of_the_origin_branch_does_not_stop_no_merge(self):
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
        self.assertEqual(c.apply("--no-merge")[0], 1)  # origin/topic has a commit it lacks
        g(c.repo, "pull", "-q", "--no-rebase", "--no-edit")  # the way out the stop names
        code, out, err, plan = c.plan("--no-merge")
        self.assertEqual(plan["verdict"], "go", out)
        code, out, err = c.apply("--no-merge")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(g(c.remote, "rev-parse", "topic"), g(c.repo, "rev-parse", "HEAD"))


class RacesDuringApply(unittest.TestCase):

    def test_commit_someone_made_on_top_of_mine_during_the_commit_is_not_taken_back(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
        c.t.edit(c.p("a.txt"), "line 3", "LINE 3")
        c.plan()

        def on_top():
            write_file(c.p("other.txt"), "other\n")
            g(c.repo, "add", "other.txt")
            g(c.repo, "commit", "-qm", "someone else: other.txt", "--", "other.txt")
        code, out = captured(lambda: apply_in_process(c, after_commit=on_top))
        self.assertEqual(code, 1, out)
        self.assertIn("nothing taken back", out)
        self.assertEqual(g(c.repo, "log", "-2", "--format=%s"),
                         "someone else: other.txt\nSession work\n")
        mine = g(c.repo, "rev-parse", "HEAD~1").strip()
        code, out, err, plan = c.plan()  # the next plan finds mine below theirs
        self.assertIn(mine, sc.load_records(c.cfg, [c.s.sid])["commits"])
        self.assertIn("Index:", out)
        self.assertEqual(c.apply()[0], 1)  # theirs is not published
        self.assertEqual(g(c.repo, "status", "--porcelain"), "")  # the index is put back
        self.assertEqual(g(c.remote, "log", "--format=%s", "main"), "init\n")



class WaysOut(unittest.TestCase):

    def test_no_merge_pushes_a_branch_whose_upstream_is_not_its_origin_branch(self):
        for how in ("pushed without -u", "tracking origin/main"):
            with self.subTest(how=how):
                c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
                if how == "pushed without -u":
                    g(c.repo, "checkout", "-q", "-b", "feature")
                else:
                    g(c.repo, "fetch", "-q")
                    g(c.repo, "checkout", "-q", "-b", "feature", "--track", "origin/main")
                write_file(c.p("b.txt"), "by hand\n")
                g(c.repo, "commit", "-qam", "bash commit X")
                g(c.repo, "push", "-q", "origin", "feature")
                c.t.edit(c.p("a.txt"), "line 3", "FEATURE")
                code, out, err, plan = c.plan()
                self.assertIn("if the branch is yours, to push the branch alone: /commit no-merge", out)
                code, out, err, plan = c.plan("--no-merge")
                self.assertEqual(plan["verdict"], "go", out)
                self.assertIn("Push:     yes — origin/feature\n", out)
                code, out, err = c.apply("--no-merge")
                self.assertEqual(code, 0, out + err)
                self.assertEqual(g(c.remote, "rev-parse", "feature"),
                                 g(c.repo, "rev-parse", "HEAD"))

    def test_no_merge_is_offered_for_foreign_main_commits_only_when_the_branch_is_clean(self):
        c = Case({"a.txt": "a\n", "b.txt": "b\n"}, remote=True)
        write_file(c.p("b.txt"), "someone's on main\n")
        g(c.repo, "commit", "-qam", "someone's on main")
        wt = os.path.join(os.path.realpath(mkdir()), "wt")
        g(c.repo, "worktree", "add", "-q", "-b", "feature/x", wt, "origin/main")
        c.s = Session(c.cfg, wt)
        c.t = c.s.main
        c.t.edit(os.path.join(wt, "a.txt"), "a", "a2")
        code, out, err, plan = c.plan(repo=wt)
        self.assertIn("review and push them yourself, then run /commit again, or /commit "
                      "no-merge to push the branch alone", out)

    def test_branch_with_foreign_commits_stops_before_main_is_moved(self):
        c = Case({"a.txt": "a\n", "b.txt": "b\n", "c.txt": "c\n"}, remote=True)
        clone_and_push(c.remote, {"c.txt": "upstream\n"})
        write_file(c.p("c.txt"), "someone's uncommitted work on main\n")  # blocks a fast-forward
        wt = worktree_session(c, "feature/x")
        write_file(os.path.join(wt, "b.txt"), "foreign\n")
        g(wt, "commit", "-qam", "foreign: X")
        c.t.edit(os.path.join(wt, "a.txt"), "a", "a2")
        main = g(c.repo, "rev-parse", "main")
        c.plan(repo=wt)
        code, out, err = c.apply()
        self.assertEqual(code, 1, out + err)
        self.assertIn("branch feature/x has commits not made by this session's /commit", out)
        self.assertNotIn("not moved", out)
        self.assertEqual(g(c.repo, "rev-parse", "main"), main)

    def test_stacked_branch_names_foreign_commits_as_on_origin_not_unpushed(self):
        c = Case({"a.txt": "a\n", "b.txt": "b\n"}, remote=True)
        g(c.repo, "checkout", "-q", "-b", "colleague")
        write_file(c.p("b.txt"), "teammate\n")
        g(c.repo, "commit", "-qam", "teammate work")
        g(c.repo, "push", "-q", "origin", "colleague")
        g(c.repo, "checkout", "-q", "-b", "mine")
        c.t.edit(c.p("a.txt"), "a", "a2")
        code, out, err, plan = c.plan("--no-merge")
        self.assertIn("mine carries commits not made by this session's /commit from other "
                      "branches of origin", out)
        self.assertIn("git push -u origin mine", out)
        self.assertNotIn("unpushed", out)
        code, out, err = c.apply("--no-merge")
        self.assertEqual(code, 1, out + err)
        self.assertNotIn("mine", g(c.remote, "branch"))


class Evidence(unittest.TestCase):

    def test_every_named_file_keeps_the_commands_that_named_it(self):
        c = Case({"a.txt": "a\n"})
        for n in range(50):
            write_file(c.p(f"gen/f{n:02d}.txt"), "y\n")
            c.t.bash(f"sed -i '' s/x/y/ gen/f{n:02d}.txt")
        for n in range(60):
            c.t.bash(f"echo {n}")
        code, out, err, plan = c.plan()
        self.assertEqual(sorted(named_cmds(plan)), plan["bash_named"])
        self.assertEqual(named_cmds(plan)["gen/f00.txt"], ["sed -i '' s/x/y/ gen/f00.txt"])
        self.assertTrue(all(named_cmds(plan).values()))

    def test_files_git_status_does_not_list_are_not_read_or_replayed(self):
        files = {f"f{n}.txt": lines(5) for n in range(6)}
        files[".gitattributes"] = "* text=auto\n"
        c = Case(files)
        for n in range(6):
            c.t.edit(c.p(f"f{n}.txt"), "line 2", f"EDIT {n}")
        g(c.repo, "commit", "-qam", "committed by hand")
        c.t.edit(c.p("f0.txt"), "line 3", "NEW")
        calls = []
        from unittest import mock
        real = sc.git
        with mock.patch.object(sc, "git", lambda cwd, *a, **kw: (calls.append(a),
                                                                   real(cwd, *a, **kw))[1]):
            code, out, err = plan_in_process(c.repo, c)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(sum(1 for a in calls if "cat-file" in a), 1, calls)
        self.assertIn("unchanged (already in HEAD or reverted): 5", out)
        for n in range(1, 6):
            c.t.edit(c.p(f"f{n}.txt"), "line 3", f"NEW {n}")
        c.plan()
        calls.clear()
        with mock.patch.object(sc, "git", lambda cwd, *a, **kw: (calls.append(a),
                                                                   real(cwd, *a, **kw))[1]):
            code, out = captured(lambda: apply_in_process(c, "--no-push"))
        self.assertEqual(code, 0, out)
        blobs = [a for a in calls if "hash-object" in a and "tree" not in a]
        self.assertEqual(blobs, [("hash-object", "-w", "--stdin-paths"),
                                 ("hash-object", "--stdin-paths")])
        self.assertEqual(g(c.repo, "status", "--porcelain"), "")


# --- regressions from the 2.2.1 review, round 2 -------------------------------------------

WORDS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india",
         "juliet", "kilo", "lima", "mike", "november", "oscar", "papa", "quebec", "romeo",
         "sierra", "tango", "uniform", "victor", "whiskey", "xray", "yankee", "zulu"]
USINGS = ("using System.Collections;\nusing UnityEngine;\n\npublic class A : MonoBehaviour\n{\n"
          + "".join(f"    int f{i} = {i};\n" for i in range(1, 21)) + "}\n")


def block(i):
    """Ten lines that look the same in every method but its name."""
    return (f"  void M{i}() {{\n    a();\n    b();\n    c();\n    if (x == null)\n    Run();\n"
            "    d();\n    e();\n    f();\n  }\n")


LOOKALIKES = "class A {\n" + "".join(block(i) for i in range(1, 6)) + "}\n"
M2_OLD = "  void M2() {\n    a();\n    b();\n    c();\n    if (x == null)\n"
M2_NEW = M2_OLD + "      return;\n"


class OlderEditsHeldInHead(unittest.TestCase):
    """An older session edit counts as committed only when HEAD has its new side and not its old
    one: a deletion's new side (context only) is in any HEAD."""

    def test_older_deletion_next_to_a_foreign_change_is_skipped_not_dropped(self):
        cases = {
            "agent deletes a line": ({"a.txt": "".join(w + "\n" for w in WORDS)}, "a.txt",
                                     ("delta\necho\n", "echo\n"), (b"charlie\n", b"CHARLIE\n"),
                                     ("yankee\n", "YANKEE\n"), "delta\n"),
            "first line deleted": ({"a.txt": "x\n" + lines(30)}, "a.txt",
                                   ("x\nline 1\n", "line 1\n"), (b"", b"FOREIGN\n"),
                                   ("line 25\n", "S25\n"), "x\n"),
            "first using removed": ({"A.cs": USINGS}, "A.cs",
                                    ("using System.Collections;\nusing UnityEngine;\n",
                                     "using UnityEngine;\n"), (b"", b"using System.Linq;\n"),
                                    ("    int f15 = 15;\n", "    int f15 = 150;\n"),
                                    "System.Collections"),
        }
        for name, (files, rel, first, foreign, later, gone) in cases.items():
            with self.subTest(name=name):
                c = Case(files)
                who = c.s.subagent("agent-w1", workflow=True) if name.startswith("agent") \
                    else c.t
                who.edit(c.p(rel), *first)
                data = read_file(c.p(rel))
                write_file(c.p(rel), foreign[1] + data if not foreign[0] else
                           data.replace(*foreign))
                c.t.edit(c.p(rel), *later)
                code, out, err, plan = c.plan()
                st = status_of(plan, rel)
                self.assertEqual((st["status"], st.get("reason")), ("skipped", "tangled"), out)
                self.assertNotIn(gone, read_file(c.p(rel)).decode())  # still on disk only


class ForwardFromHead(unittest.TestCase):
    """Replaying an undo-less edit on HEAD is checked against the disk."""

    def test_replay_from_head_that_the_disk_disagrees_with_is_skipped(self):
        # someone put the deleted line back
        c = Case({"a.txt": lines(30)})
        wf = c.s.subagent("agent-w1", workflow=True)
        wf.edit(c.p("a.txt"), "line 4\n", "")
        data = read_file(c.p("a.txt")).replace(b"line 3\n", b"line 3\nline 4\n")
        write_file(c.p("a.txt"), data.replace(b"line 25\n", b"FOREIGN\n"))
        code, out, err, plan = c.plan()
        self.assertEqual((status_of(plan, "a.txt")["status"], status_of(plan, "a.txt")["reason"]),
                         ("skipped", "ambiguous"), out)
        # the deleted text is HEAD's line 3 there, but the agent deleted a later copy of it
        h = "a\nb\nx\nc\n" + lines(20) + "z\n"
        c = Case({"a.txt": h})
        write_file(c.p("a.txt"), h.replace("a\nb\nx\nc\n", "a\nb\ny\nc\n").replace("z\n",
                                                                                 "x\nz\n"))
        wf = c.s.subagent("agent-w1", workflow=True)
        wf.edit(c.p("a.txt"), "x\n", "")
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.txt")["status"], "skipped", out)

    def test_replay_from_head_that_matches_the_disk_is_split(self):
        c = Case({"a.txt": lines(30)})
        wf = c.s.subagent("agent-w1", workflow=True)
        wf.edit(c.p("a.txt"), "line 4\n", "")
        write_file(c.p("a.txt"), read_file(c.p("a.txt")).replace(b"line 25\n", b"FOREIGN\n"))
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.txt")["status"], "partial", out)
        self.assertEqual(c.apply()[0], 0)
        self.assertEqual(c.show("a.txt"), lines(30).replace("line 4\n", ""))


class LookalikesOnTheirOwnLine(unittest.TestCase):

    def test_created_file_edit_shifted_onto_a_lookalike_block_is_skipped(self):
        c = Case({"README": "r\n"})
        c.t.write(c.p("A.cs"), LOOKALIKES)
        note = "".join(f"  // note {i}\n" for i in range(10))  # one block's height, above
        write_file(c.p("A.cs"), LOOKALIKES.replace("class A {\n", "class A {\n" + note, 1))
        c.t.edit(c.p("A.cs"), M2_OLD, M2_NEW)
        code, out, err, plan = c.plan()
        st = status_of(plan, "A.cs")
        self.assertEqual((st["status"], st.get("detail")), ("skipped", "repeated"), out)

    def test_created_file_edit_in_repeated_code_is_committed_when_the_disk_is_the_sessions(self):
        c = Case({"README": "r\n"})
        c.t.write(c.p("A.cs"), LOOKALIKES)
        c.t.edit(c.p("A.cs"), M2_OLD, M2_NEW)
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "A.cs")["status"], "commit", out)
        self.assertEqual(c.apply()[0], 0)
        self.assertEqual(c.show("A.cs"), LOOKALIKES.replace(M2_OLD, M2_NEW))


class ShellEvidence(unittest.TestCase):

    def test_refused_shell_command_does_not_name_a_foreign_file(self):
        refusals = ["Permission for this action was denied by the Claude Code auto mode "
                    "classifier. Reason: [Interfere With Workloads] … attempt to accomplish "
                    "this action using other tools",
                    "The user doesn't want to proceed with this tool use.",
                    "PreToolUse:Bash hook error: [guard] Blocked."]
        for text in refusals:
            with self.subTest(text=text[:30]):
                c = Case({"src/Foo.cs": lines(30)})
                write_file(c.p("src/Foo.cs"),
                           read_file(c.p("src/Foo.cs")).replace(b"line 25\n", b"FOREIGN 25\n"))
                wf = c.s.subagent("agent-w1", workflow=True)
                wf.bash("sed -i '' 's/line 5/SESSION 5/' src/Foo.cs", text, error=True)
                wf.edit(c.p("src/Foo.cs"), "line 5\n", "SESSION 5\n")
                code, out, err, plan = c.plan()
                self.assertEqual(status_of(plan, "src/Foo.cs")["status"], "partial", out)
                self.assertEqual(plan["bash_named"], [], out)
                self.assertEqual(named_cmds(plan), {})
                self.assertNotIn("named by this session's shell commands", out)

    def test_file_name_shared_with_the_sessions_own_file_names_nothing(self):
        for extra in (None, "--declare", "untracked c/SKILL.md"):
            with self.subTest(extra=extra):
                c = Case({"a/SKILL.md": "x\n", "b/SKILL.md": "b\n"})
                c.t.edit(c.p("a/SKILL.md"), "x\n", "x2\n")
                c.t.bash("cd a && sed -i '' s/x/y/ SKILL.md")
                write_file(c.p("b/SKILL.md"), "someone's\n")
                if extra == "untracked c/SKILL.md":
                    write_file(c.p("c/SKILL.md"), "someone else's\n")
                code, out, err, plan = c.plan(*(["--declare", "a/SKILL.md"]
                                                if extra == "--declare" else []))
                self.assertEqual(status_of(plan, "a/SKILL.md")["status"], "commit", out)
                self.assertEqual(plan["bash_named"], [], out)

class RecordsAcrossRepositories(unittest.TestCase):

    def test_pending_commit_of_another_repository_waits_for_a_plan_there(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
        other = make_repo({"o.txt": "o\n"})
        c.t.edit(c.p("a.txt"), "line 3", "LINE 3")
        c.t.edit(os.path.join(other, "o.txt"), "o", "O")
        c.plan()

        def killed():
            raise SystemExit(143)
        with self.assertRaises(SystemExit):
            captured(lambda: apply_in_process(c, after_commit=killed))
        self.assertEqual(len(sc.load_records(c.cfg, [c.s.sid])["pending"]), 1)
        c.plan(repo=other)  # the same session, the other repository first
        self.assertEqual(len(sc.load_records(c.cfg, [c.s.sid])["pending"]), 1)
        code, out, err, plan = c.plan()
        self.assertIn("Index:    will put back the entries my commit", out)
        self.assertIn("Push:     yes — origin/main", out)
        self.assertEqual(sc.load_records(c.cfg, [c.s.sid])["pending"], [])

    def test_other_repository_with_nothing_left_gets_no_elsewhere_line(self):
        c = Case({"a.txt": "a\n"})
        other = make_repo({"o.txt": "o\n", "p.txt": "p\n"})
        c.t.edit(os.path.join(other, "o.txt"), "o", "O")
        c.t.edit(os.path.join(other, "p.txt"), "p", "P")
        c.t.edit(c.p("a.txt"), "a", "A")
        code, out, err, plan = c.plan()
        self.assertIn(f"Elsewhere: this session also edited {other} (2 files)", out)
        g(other, "commit", "-qm", "landed there", "--", "o.txt")
        code, out, err, plan = c.plan()
        self.assertIn(f"Elsewhere: this session also edited {other} (1 file)", out)
        g(other, "commit", "-qam", "all landed there")
        code, out, err, plan = c.plan()
        self.assertNotIn("Elsewhere:", out)
        self.assertEqual(plan["other_repos"], {})


class DiskDuringApply(unittest.TestCase):

    def test_save_between_the_stale_check_and_the_hashing_makes_the_plan_stale(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
        c.t.edit(c.p("a.txt"), "line 3", "LINE 3")
        c.plan()
        head = g(c.repo, "rev-parse", "HEAD")

        def save(args):
            if "read-tree" in args:
                write_file(c.p("a.txt"), read_file(c.p("a.txt")) + b"SOMEONE'S UNFINISHED\n")
        code, out = captured(lambda: apply_in_process(c, on_git=save))
        self.assertEqual(code, 3, out)
        self.assertEqual(g(c.repo, "rev-parse", "HEAD"), head)
        self.assertEqual(g(c.remote, "rev-parse", "main"), head)
        self.assertEqual(sc.load_records(c.cfg, [c.s.sid])["pending"], [])


def orphan_case():
    c = Case({"a.txt": lines(5)}, remote=True)
    g(c.repo, "checkout", "-q", "--orphan", "gh-pages")
    g(c.repo, "rm", "-rfq", "--cached", ".")
    os.remove(c.p("a.txt"))
    return c


class BranchesAndRemotes(unittest.TestCase):

    def test_orphan_branch_is_pushed_alone_on_every_run(self):
        for first in ("local", "default"):
            with self.subTest(first=first):
                c = orphan_case()
                c.t.write(c.p("index.html"), "<h1>one</h1>\n")
                args = ("--no-merge", "--no-push") if first == "local" else ()
                self.assertEqual(c.land(*args)[0], 0)
                c.t.edit(c.p("index.html"), "one", "two")
                code, out, err, plan = c.plan()
                self.assertIn("Into main: no — a new branch with no history", out)
                self.assertIn("Push:     yes — origin/gh-pages", out)
                code, out, err = c.apply()
                self.assertEqual(code, 0, out + err)
                self.assertIn("Into main: no — a new branch with no history", out)
                self.assertEqual(g(c.remote, "rev-parse", "gh-pages"),
                                 g(c.repo, "rev-parse", "gh-pages"))
                self.assertEqual(g(c.remote, "log", "--format=%s", "main"), "init\n")
                self.assertEqual(g(c.repo, "log", "--format=%s", "main"), "init\n")
                self.assertEqual(g(c.repo, "ls-tree", "--name-only", "gh-pages"),
                                 "index.html\n")

    def test_stale_origin_head_after_a_rename_is_not_followed(self):
        for how in ("old ref kept", "old ref pruned"):
            with self.subTest(how=how):
                c = Case({"a.txt": lines(5)}, remote=True)
                if how == "old ref kept":
                    g(c.repo, "update-ref", "refs/remotes/origin/master", "HEAD")
                g(c.repo, "symbolic-ref", "refs/remotes/origin/HEAD",
                  "refs/remotes/origin/master")
                c.t.edit(c.p("a.txt"), "line 3", "LINE 3")
                code, out, err, plan = c.plan()
                self.assertIn("Into main: no — already on main", out)
                self.assertIn("Push:     yes — origin/main\n", out)
                code, out, err = c.apply()
                self.assertEqual(code, 0, out + err)
                self.assertEqual(g(c.remote, "for-each-ref", "--format=%(refname)").split(),
                         ["refs/heads/main"])
        c = Case({"a.txt": lines(5)}, remote=True)
        g(c.repo, "update-ref", "refs/remotes/origin/master", "HEAD")
        g(c.repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/master")
        g(c.repo, "checkout", "-q", "-b", "feature")
        c.t.edit(c.p("a.txt"), "line 3", "LINE 3")
        code, out, err, plan = c.plan()
        self.assertIn("if origin's default branch was renamed (there is main) — git remote "
                      "set-head origin -a", out)
        code, out, err = c.apply()
        self.assertEqual(code, 1, out + err)
        self.assertIn("Into master: stop — there is no local master branch", out)
        self.assertEqual(g(c.repo, "branch", "--list", "master"), "")
        self.assertEqual(g(c.remote, "for-each-ref", "--format=%(refname)").split(),
                         ["refs/heads/main"])

    def test_no_merge_is_not_offered_when_the_origin_branch_has_commits_the_branch_lacks(self):
        for where in ("local main", "branch"):
            with self.subTest(where=where):
                c = Case({"a.txt": "a\n", "b.txt": "b\n", "c.txt": "c\n"}, remote=True)
                if where == "local main":
                    write_file(c.p("b.txt"), "someone's on main\n")
                    g(c.repo, "commit", "-qam", "someone's on main")
                wt = os.path.join(os.path.realpath(mkdir()), "wt")
                g(c.repo, "worktree", "add", "-q", "-b", "feature/x", wt, "origin/main")
                c.s = Session(c.cfg, wt)
                c.t = c.s.main
                if where == "branch":
                    write_file(os.path.join(wt, "b.txt"), "foreign\n")
                    g(wt, "commit", "-qam", "foreign: earlier work")
                g(wt, "push", "-q", "-u", "origin", "feature/x")
                other = os.path.realpath(mkdir())
                g(other, "clone", "-q", "-b", "feature/x", c.remote, ".")
                write_file(os.path.join(other, "c.txt"), "teammate\n")
                g(other, "commit", "-qam", "teammate on feature/x")
                g(other, "push", "-q", "origin", "feature/x")
                g(wt, "fetch", "-q")
                c.t.edit(os.path.join(wt, "a.txt"), "a", "a2")
                code, out, err, plan = c.plan(repo=wt)
                self.assertNotIn("/commit no-merge to push the branch alone", out)
                self.assertNotIn("to push the branch alone: /commit no-merge", out)
                if where == "branch":
                    g(wt, "merge", "-q", "--no-edit", "origin/feature/x")
                    c.plan("--no-merge", repo=wt)
                    code, out, err = c.apply("--no-merge")
                    self.assertEqual(code, 0, out + err)

    def test_stop_advice_names_commands_that_work(self):
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
        for lang, want in (("en", "merge origin/topic into the branch first (git merge "
                                  "origin/topic)"),
                           ("ru", "сначала влей origin/topic в ветку (git merge origin/topic)")):
            code, out, err, plan = c.plan("--no-merge", lang=lang)
            self.assertIn(want, out)
            self.assertNotIn("git pull", out)

    def test_push_rejected_by_a_protected_main_names_the_way_out(self):
        c = Case({"a.txt": lines(5)}, remote=True)
        hook = os.path.join(c.remote, "hooks", "pre-receive")
        write_file(hook, "#!/bin/sh\nwhile read old new ref; do\n  if [ \"$ref\" = "
                   "refs/heads/main ]; then echo 'main is protected' >&2; exit 1; fi\ndone\n")
        os.chmod(hook, 0o755)
        wt = worktree_session(c, "feature")
        c.t.edit(os.path.join(wt, "a.txt"), "line 3", "FEATURE")
        c.plan(repo=wt)
        code, out, err = c.apply()
        self.assertEqual(code, 1, out + err)
        self.assertIn("— if main is protected: /commit no-merge pushes the branch for a pull "
                      "request; local main stays ahead of origin/main by 1 commit of this "
                      "session's", out)
        c.plan("--no-merge", repo=wt)
        code, out, err = c.apply("--no-merge")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(g(c.remote, "rev-parse", "feature"), g(wt, "rev-parse", "HEAD"))

    def test_push_failed_by_a_pre_push_hook_is_not_called_a_refusal_by_origin(self):
        for lang, want in (("en", "Push:     stop — push failed: tests failed: 3 failing"),
                           ("ru", "Push:     стоп — push не прошёл: tests failed: 3 failing")):
            c = Case({"a.txt": lines(5)}, remote=True)
            hook = c.p(".git/hooks/pre-push")
            write_file(hook, "#!/bin/sh\necho 'tests failed: 3 failing' >&2\nexit 1\n")
            os.chmod(hook, 0o755)
            c.t.edit(c.p("a.txt"), "line 3", "LINE 3")
            c.plan(lang=lang)
            code, out, err = c.apply("--lang", lang)
            self.assertEqual(code, 1, out + err)
            self.assertIn(want, out)
            self.assertNotIn("rejected", out)
            self.assertNotIn("protected", out)
            self.assertNotIn("отклонил", out)

    def test_fetch_failing_on_a_bad_origin_url_is_not_called_no_network(self):
        c = Case({"a.txt": lines(5)}, remote=True)
        g(c.repo, "remote", "set-url", "origin", "/nonexistent/repo.git")
        c.t.edit(c.p("a.txt"), "line 3", "LINE 3")
        c.plan()
        code, out, err = c.apply()
        self.assertEqual(code, 1, out + err)
        self.assertIn("Push:     stop — fetch failed (", out)
        self.assertIn("the commit stays local", out)
        self.assertNotIn("no network", out)

    def test_remote_not_named_origin_is_named_in_the_push_line(self):
        c = Case({"a.txt": lines(5)}, remote=True)
        g(c.repo, "remote", "rename", "origin", "github")
        c.t.edit(c.p("a.txt"), "line 3", "LINE 3")
        want = "Push:     no — no remote named origin (remotes: github); push it yourself"
        code, out, err, plan = c.plan()
        self.assertIn(want, out)
        code, out, err = c.apply()
        self.assertEqual(code, 0, out + err)
        self.assertIn(want, out)


    def test_stop_before_the_push_still_prints_the_push_line(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
        wt = worktree_session(c, "feature")
        write_file(os.path.join(wt, "b.txt"), "by hand\n")
        g(wt, "commit", "-qam", "bash commit on the branch")
        c.t.edit(os.path.join(wt, "a.txt"), "line 3", "LINE 3")
        c.plan(repo=wt)
        code, out, err = c.apply()
        self.assertEqual(code, 1, out + err)
        self.assertIn("Into main: stop — branch feature has commits", out)
        self.assertTrue(out.rstrip("\n").endswith("Push:     no — stopped before"), out)

    def test_dirty_main_checkout_in_the_way_is_foreseen_and_named(self):
        c = Case({"CHANGELOG.md": "# log\n", "a.txt": "a\n"}, remote=True)
        wt = worktree_session(c, "feature")
        write_file(c.p("CHANGELOG.md"), "# log\n- someone's line\n")
        c.t.edit(os.path.join(wt, "CHANGELOG.md"), "# log\n", "# log\n- session's line\n")
        want = (f"the checkout of main ({c.repo}) has uncommitted changes in CHANGELOG.md that "
                "the merge would overwrite; commit or remove them there and run /commit again, "
                "or /commit no-merge to push the branch alone")
        code, out, err, plan = c.plan(repo=wt)
        self.assertEqual(plan["verdict"], "go", out)
        self.assertIn("Into main: no — will stop after the commit: " + want, out)
        code, out, err = c.apply()
        self.assertEqual(code, 1, out + err)
        self.assertIn("Into main: stop — main not moved: " + want, out)
        self.assertNotIn("stash", out)
        self.assertEqual(read_file(c.p("CHANGELOG.md")), b"# log\n- someone's line\n")
        c.plan("--no-merge", repo=wt)
        self.assertEqual(c.apply("--no-merge")[0], 0)



# --- regressions from the 2.2.1 review, round 3 -------------------------------------------

def yaml_entry(name, enabled):
    """Eight lines that look the same in every entry but its name and flag."""
    return (f"- name: {name}\n  a: 1\n  b: 1\n  c: 1\n  enabled: {enabled}\n  d: 1\n  e: 1\n"
            "  f: 1\n")


class HeldEditsAreChecked(unittest.TestCase):
    """A file the replay cannot split is skipped, never committed without an older edit of the
    session's; an undo at a hunk's own line among lookalikes counts only when the result is the
    disk."""

    def test_older_edit_next_to_someones_uncommitted_change_is_not_taken_as_committed(self):
        def next_to_a_foreign_deletion(p, wf):  # the conflict path
            write_file(p, read_file(p).replace(b"line 12\n", b""))
            wf.edit(p, "line 11\nline 13\n", "line 11\n")
            wf.edit(p, "line 3\n", "line 3 changed\n")

        def deleting_a_foreign_line_too(p, wf):  # the replay from HEAD
            write_file(p, read_file(p).replace(b"line 2\n", b"line 2\nFOREIGN\n"))
            wf.edit(p, "line 2\nFOREIGN\n", "")
            wf.edit(p, "line 10\n", "line 10 changed\n")

        def anchored_on_the_line_above(p, wf):  # the conflict path, a non-empty new side
            write_file(p, read_file(p).replace(b"line 2\n", b"line 2\nFOREIGN\n"))
            wf.edit(p, "line 1\nline 2\nFOREIGN\n", "line 1\n")
            wf.edit(p, "line 10\n", "line 10 changed\n")
        for setup in (next_to_a_foreign_deletion, deleting_a_foreign_line_too,
                      anchored_on_the_line_above):
            with self.subTest(setup=setup.__name__):
                c = Case({"a.txt": lines(14)})
                setup(c.p("a.txt"), c.s.subagent("agent-w1", workflow=True))
                code, out, err, plan = c.plan()
                self.assertEqual(status_of(plan, "a.txt")["status"], "skipped", out)
                self.assertEqual(plan["verdict"], "skipped", out)
                self.assertIn("Skip:     a.txt — ", out)


    def test_undo_on_a_lookalike_block_moved_onto_the_hunks_line_is_skipped(self):
        head = "items:\n" + yaml_entry("alpha", "true") + yaml_entry("beta", "false") + \
            yaml_entry("gamma", "true") + yaml_entry("delta", "true")
        c = Case({"list.yaml": head})
        p = c.p("list.yaml")
        c.t.edit(p, "  enabled: false\n", "  enabled: true\n")  # the session turns beta on
        write_file(p, read_file(p).decode().replace("items:\n", "items:\n" +
                                                    yaml_entry("zeta", "true"), 1))
        code, out, err, plan = c.plan()
        st = status_of(plan, "list.yaml")
        self.assertEqual((st["status"], st.get("detail")), ("skipped", "repeated"), out)
        self.assertNotIn("no new changes of this session here", out)
        self.assertIn("Skip:     list.yaml — cannot locate this session's change for sure — the "
                      "text occurs more than once in the file", out)


class BranchPushes(unittest.TestCase):





    def test_git_hints_are_left_out_of_status_lines(self):
        proc = subprocess.CompletedProcess([], 1, b"", b"hint: Diverging branches can't be "
                                           b"fast-forwarded\nhint: git rebase\nfatal: Not possible "
                                           b"to fast-forward, aborting.\n")
        self.assertEqual(sc.errtext(proc), "fatal: Not possible to fast-forward, aborting.")

    def test_default_branch_with_no_local_copy_stops_naming_both_ways_out(self):
        c = Case({"a.txt": lines(5)}, remote=True)
        g(c.repo, "push", "-q", "origin", "main:develop")
        g(c.remote, "symbolic-ref", "HEAD", "refs/heads/develop")
        g(c.repo, "fetch", "-q")
        g(c.repo, "remote", "set-head", "origin", "-a")
        g(c.repo, "checkout", "-q", "-b", "feature", "origin/develop")
        c.t.edit(c.p("a.txt"), "line 3", "LINE 3")
        main = g(c.remote, "rev-parse", "main")
        want = ("there is no local develop branch — create it (git branch develop origin/develop) "
                "and run /commit again; if origin's default branch was renamed (there is main) — "
                "git remote set-head origin -a; or /commit no-merge")
        code, out, err, plan = c.plan()
        self.assertIn("Into develop: no — will stop after the commit: " + want, out)
        code, out, err = c.apply()
        self.assertEqual(code, 1, out + err)
        self.assertIn("Into develop: stop — " + want, out)
        g(c.repo, "branch", "develop", "origin/develop")
        self.assertEqual(c.land()[0], 0)
        self.assertEqual(g(c.remote, "rev-parse", "develop"), g(c.repo, "rev-parse", "feature"))
        self.assertEqual(g(c.remote, "rev-parse", "main"), main)


class StopsAfterTheCommit(unittest.TestCase):

    def test_mismatch_after_the_commit_still_prints_the_merge_and_push_lines(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
        wt = worktree_session(c, "feature")
        a = os.path.join(wt, "a.txt")
        c.t.edit(a, "line 3", "LINE 3")
        c.plan(repo=wt)
        code, out = captured(lambda: apply_in_process(
            c, after_commit=lambda: write_file(a, read_file(a) + b"SOMEONE\n")))
        self.assertEqual(code, 1, out)
        self.assertIn("still differ from the commit: a.txt (changed during the commit) — the "
                      "commit is made, but not merging or pushing; check those files and run "
                      "/commit again to merge and push it", out)
        self.assertIn("Into main: no — stopped before", out)
        self.assertTrue(out.rstrip("\n").endswith("Push:     no — stopped before"), out)

    def test_stop_for_foreign_commits_names_them_in_the_users_language(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
        write_file(c.p("b.txt"), "by hand\n")
        g(c.repo, "commit", "-qam", "bash commit")
        c.t.edit(c.p("a.txt"), "line 3", "LINE 3")
        for lang, want in (("en", "bash commit — not publishing them; review and push them "
                                  "yourself, then run /commit again, or drop them"),
                           ("ru", "bash commit — их не публикую; проверь и запушь их сам, потом "
                                  "снова /commit, или убери их")):
            code, out, err, plan = c.plan(lang=lang)
            self.assertIn(want, out)
            self.assertNotIn("<id>", out)


class ShellEvidenceInFull(unittest.TestCase):

    def test_every_command_naming_a_partly_committed_file_is_in_commands_json(self):
        c = Case({"a.txt": lines(30)})
        p = c.p("a.txt")
        c.t.bash("cat a.txt")
        write = ("python3 - <<'EOF'\np = 'a.txt'; s = open(p).read()\n"
                 "open(p, 'w').write(s.replace('line 25', 'BASH 25'))\nEOF")
        c.t.bash(write, between=lambda: write_file(p, read_file(p).replace(b"line 25",
                                                                           b"BASH 25")))
        for n in range(4):
            c.t.bash(f"grep -n 'line {n}' a.txt")
        c.t.edit(p, "line 5\n", "SESSION 5\n")
        code, out, err, plan = c.plan()
        self.assertEqual(status_of(plan, "a.txt")["status"], "partial", out)
        self.assertIn("every naming command, uncut, oldest first: " + plan["bash_commands"], out)
        named = named_cmds(plan)["a.txt"]
        self.assertEqual(len(named), 6)
        self.assertEqual(named[1], write)


class OutsideARepository(unittest.TestCase):

    def test_repository_line_outside_a_repository_does_not_claim_it_was_the_only_one_edited(self):
        c = Case({"a.txt": lines(5)})
        other = make_repo({"o.txt": "o\n"})
        c.t.edit(c.p("a.txt"), "line 1", "SESSION")
        c.t.edit(os.path.join(other, "o.txt"), "o", "O")
        g(other, "commit", "-qam", "landed there by hand")
        home = os.path.realpath(mkdir())
        for lang, want in (("en", "the current directory is not in git; this is the only "
                                  "repository with this session's work left"),
                           ("ru", "текущая папка не в git; несделанное этой сессии есть только "
                                  "в нём")):
            proc = subprocess.run([sys.executable, SCRIPT, "plan", "--out", mkdir(), "--lang",
                                   lang], cwd=home, env=c.env(), capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn(want, proc.stdout)
            self.assertNotIn("only this one", proc.stdout)

    def test_repositories_with_nothing_of_the_session_left_are_not_listed(self):
        c = Case({"a.txt": lines(5)}, remote=True)
        other = make_repo({"o.txt": "o\n"})
        c.t.edit(c.p("a.txt"), "line 1", "SESSION")
        c.t.edit(os.path.join(other, "o.txt"), "o", "O")
        home = os.path.realpath(mkdir())

        def plan_here():
            return subprocess.run([sys.executable, SCRIPT, "plan", "--out", mkdir()], cwd=home,
                                  env=c.env(), capture_output=True, text=True)
        proc = plan_here()
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn(other, proc.stderr)
        g(other, "commit", "-qam", "landed there by hand")
        proc = plan_here()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn(f"Repository: {c.repo}", proc.stdout)
        c.plan("--no-push")  # committed by /commit, not pushed yet: still listed
        self.assertEqual(c.apply("--no-push")[0], 0)
        proc = plan_here()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn(f"Repository: {c.repo}", proc.stdout)
        self.assertIn("Push:     yes — origin/main", proc.stdout)
        self.assertEqual(c.land()[0], 0)
        proc = plan_here()
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("nothing of this session's is left to commit or push in the repositories "
                      "it edited (2 with nothing of it left to commit or push)", proc.stderr)


# --- tasks, /ticket runs and PR repositories ---------------------------------------------

def ticket_marker(sid, task="LOCAL-17"):
    """The ticket guard's marker of a session in a /ticket run (skills/ticket/guard.py)."""
    os.makedirs(os.path.join(TASK_RUNS, ".guard"), exist_ok=True)
    with open(os.path.join(TASK_RUNS, ".guard", sid), "w") as fh:
        fh.write(f"ticket session\nrun: {os.path.join(TASK_RUNS, 'repo', task)}\n")


def fake_gh(c):
    """A gh on PATH for the case that only logs its calls: /commit must never run it. -> the log
    path (absent while gh never ran)."""
    d = os.path.realpath(mkdir())
    log = os.path.join(d, "calls.log")
    with open(os.path.join(d, "gh"), "w") as fh:
        fh.write(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{log}'\n")
    os.chmod(os.path.join(d, "gh"), 0o755)
    c.extra_env["PATH"] = d + os.pathsep + BASE_ENV.get("PATH", "")
    return log


def pr_history(c, n=4):
    """Main's first-parent history of a PR repository: n merges of pull requests, pushed."""
    for i in range(1, n + 1):
        g(c.repo, "checkout", "-q", "-b", f"f{i}")
        write_file(c.p(f"f{i}.txt"), f"{i}\n")
        g(c.repo, "add", f"f{i}.txt")
        g(c.repo, "commit", "-qm", f"feature {i}")
        g(c.repo, "checkout", "-q", "main")
        g(c.repo, "merge", "-q", "--no-ff", "-m", f"Merge pull request #{i} from o/f{i}", f"f{i}")
    g(c.repo, "push", "-q", "origin", "main")


class TicketRuns(unittest.TestCase):

    def test_plan_and_apply_stop_inside_a_ticket_run(self):
        c = Case({"a.txt": lines(5)})
        c.t.edit(c.p("a.txt"), "line 3", "SESSION")
        code, out, err, plan = c.plan()
        self.assertEqual(code, 0, out + err)
        ticket_marker(c.s.sid)
        code, out, err = c.apply()  # a plan made before the run began does not slip through
        self.assertEqual(code, 1, out + err)
        self.assertIn("Commit:   no — stop: a /ticket run LOCAL-17 is on — its gate makes the "
                      "commit", out)
        code, out, err, plan = c.plan(lang="ru")
        self.assertEqual(code, 1, out + err)
        self.assertIn("Коммит:   нет — стоп: идёт прогон /ticket LOCAL-17 — коммит делает шлюз "
                      "тикета", out)
        self.assertIn("verdict: stop", out)
        self.assertEqual(g(c.repo, "rev-list", "--count", "HEAD"), "1\n")

    def test_earlier_session_of_a_ticket_run_passed_with_session_is_named_as_bypassing_the_gate(
            self):
        c = Case({"a.txt": lines(5)})
        earlier = Session(c.cfg, c.repo)
        earlier.main.edit(c.p("a.txt"), "line 3", "TICKET WORK")
        ticket_marker(earlier.sid)
        code, out, err, plan = c.plan("--session", c.s.sid, "--session", earlier.sid)
        self.assertEqual(code, 0, out + err)
        self.assertIn(f"Task:     session {earlier.sid} ran /ticket LOCAL-17 — its work bypasses "
                      f"the ticket gate (/resume {earlier.sid} to go through it)", out)

    def test_ticket_invocation_in_the_transcript_stops_without_the_marker(self):
        c = Case({"a.txt": lines(5)})
        c.t._put({"type": "user", "origin": {"kind": "human"}, "message": {
            "role": "user", "content": "<command-message>kensei-toolkit:ticket</command-message>"
            "\n<command-name>/kensei-toolkit:ticket</command-name>\n<command-args>"
            "https://app.clickup.com/t/9012/869fejukt fix</command-args>"}})
        c.t.edit(c.p("a.txt"), "line 3", "SESSION")
        code, out, err, plan = c.plan()
        self.assertEqual(code, 1, out + err)
        self.assertIn("a /ticket run 869fejukt is on", out)

    def test_ticket_text_in_a_command_output_is_no_run(self):
        c = Case({"a.txt": lines(5)})
        c.t.bash("grep -r command-name ~/.claude/projects",
                 "<command-message>kensei-toolkit:ticket</command-message>\n"
                 "<command-name>/kensei-toolkit:ticket</command-name>")
        c.t.edit(c.p("a.txt"), "line 3", "SESSION")
        self.assertEqual(c.plan()[0], 0)


class Tasks(unittest.TestCase):
    URL = "https://app.clickup.com/t/9012/869fejukt"

    def test_task_ids_are_read_from_tracker_links(self):
        for ref, tid in [(self.URL, "869fejukt"), ("https://app.clickup.com/t/869fejukt",
                                                   "869fejukt"),
                         ("https://acme.atlassian.net/browse/ENG-12", "ENG-12"),
                         ("https://linear.app/acme/issue/ENG-3/fix-the-thing", "ENG-3"),
                         ("https://github.com/o/r/issues/45", "45"), ("#45", "45"),
                         ("869fejukt", "869fejukt")]:
            self.assertEqual(sc.task_of(ref)[0], tid, ref)
        self.assertIsNone(sc.task_of("not an id!"))

    def test_task_id_from_a_board_link_is_the_opened_issue_not_the_board(self):
        for ref, tid in [
                ("https://acme.atlassian.net/jira/software/projects/ENG/boards/3"
                 "?selectedIssue=ENG-12", "ENG-12"),
                ("https://acme.atlassian.net/jira/software/projects/ENG/issues/?jql=x"
                 "&selectedIssue=ENG-12", "ENG-12"),
                ("https://yt.acme.com/agiles/1-2/current?issue=PROJ-7", "PROJ-7"),
                ("https://yt.acme.com/issue/PROJ-7/some-title", "PROJ-7"),
                ("https://www.notion.so/acme/Fix-it-0123456789abcdef0123456789abcdef",
                 "0123456789abcdef0123456789abcdef"),
                ("https://app.asana.com/0/1200000000000001/1200000000000002",
                 "1200000000000002")]:
            self.assertEqual(sc.task_of(ref)[0], tid, ref)
        for ref in ("https://acme.atlassian.net/jira/software/projects/ENG/boards/3",
                    "https://yt.acme.com/agiles/1-2/current"):
            self.assertIsNone(sc.task_of(ref), ref)

    def test_task_link_without_an_id_stops_and_asks_for_the_id(self):
        c = Case({"a.txt": lines(5)})
        c.t.edit(c.p("a.txt"), "line 3", "SESSION")
        code, out, err, plan = c.plan("--task", "https://acme.atlassian.net/jira/software/"
                                      "projects/ENG/boards/3")
        self.assertEqual(code, 2, out + err)
        self.assertIn("no task id in it — pass --task <id>", out + err)
        self.assertTrue(sc.mentions_task("Fix (869fejukt)\n", {"id": "869fejukt", "url": None}))
        self.assertFalse(sc.mentions_task("Fix 869fejuktx\n", {"id": "869fejukt", "url": None}))
        self.assertFalse(sc.mentions_task("Fix 45 items\n", {"id": "45", "url": None}))
        self.assertTrue(sc.mentions_task("Fix (#45)\n", {"id": "45", "url": None}))

    def test_task_on_main_gets_its_branch_and_main_follows_it(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
        c.t.edit(c.p("a.txt"), "line 3", "SHIELD")
        write_file(c.p("b.txt"), "foreign\n")
        args = ("--task", self.URL, "--branch", "task/869fejukt-shield-hit")
        code, out, err, plan = c.plan(*args, lang="ru")
        self.assertEqual(code, 0, out + err)
        self.assertIn("Ветка:    task/869fejukt-shield-hit — создам от main", out)
        self.assertIn("Задача:   869fejukt — ссылка в сообщении коммита", out)
        self.assertIn("В main:   да — ветка task/869fejukt-shield-hit → fast-forward main", out)
        code, out, err = c.apply(msg="Shield hit effect (869fejukt)\n\n" + self.URL + "\n")
        self.assertEqual(code, 0, out + err)
        self.assertIn("Ветка:    task/869fejukt-shield-hit — создана от main", out)
        self.assertIn("Задача:   869fejukt — ссылка в сообщении коммита", out)
        self.assertEqual(g(c.repo, "branch", "--show-current"), "task/869fejukt-shield-hit\n")
        self.assertEqual(g(c.repo, "rev-parse", "main"), g(c.repo, "rev-parse", "HEAD"))
        self.assertEqual(g(c.remote, "rev-parse", "main"), g(c.repo, "rev-parse", "HEAD"))
        self.assertIn("SHIELD", c.show("a.txt"))
        self.assertEqual(read_file(c.p("b.txt")), b"foreign\n")  # someone's change stays

    def test_message_without_the_task_is_refused_before_any_write(self):
        c = Case({"a.txt": lines(5)}, remote=True)
        c.t.edit(c.p("a.txt"), "line 3", "SHIELD")
        c.plan("--task", self.URL, "--branch", "task/869fejukt-shield-hit")
        code, out, err = c.apply(msg="Shield hit effect\n")
        self.assertEqual(code, 2, out + err)
        self.assertIn("does not name task 869fejukt", err)
        self.assertEqual(g(c.repo, "branch", "--show-current"), "main\n")
        self.assertEqual(g(c.repo, "branch", "--list", "task/*"), "")
        self.assertEqual(g(c.repo, "rev-list", "--count", "HEAD"), "1\n")

    def test_existing_branch_of_the_task_stops_naming_it(self):
        c = Case({"a.txt": lines(5)}, remote=True)
        g(c.repo, "checkout", "-q", "-b", "task/869fejukt-old")
        g(c.repo, "commit", "-q", "--allow-empty", "-m", "earlier work (869fejukt)")
        g(c.repo, "checkout", "-q", "main")
        c.t.edit(c.p("a.txt"), "line 3", "SHIELD")
        code, out, err, plan = c.plan("--task", "869fejukt", "--branch", "task/869fejukt-new")
        self.assertEqual((code, plan["verdict"]), (1, "stop"), out + err)
        self.assertIn("Branch:   no — stop: branch task/869fejukt-old already exists — not "
                      "switching onto it", out)
        self.assertEqual(c.apply(msg="x (869fejukt)\n")[0], 1)
        self.assertEqual(g(c.repo, "branch", "--show-current"), "main\n")

    def test_task_line_says_named_and_is_left_out_of_a_stopped_plan(self):
        c = Case({"a.txt": lines(5)}, remote=True)
        c.t.edit(c.p("a.txt"), "line 3", "SHIELD")
        code, out, err, plan = c.plan("--task", "869fejukt", "--no-push")
        self.assertIn("Task:     869fejukt — named in the commit message (checked at apply)", out)
        code, out, err = c.apply(msg="Shield (869fejukt)\n")
        self.assertIn("Task:     869fejukt — named in the commit message\n", out)
        g(c.repo, "checkout", "-q", "main")
        c.t.edit(c.p("a.txt"), "line 4", "MORE")
        code, out, err, plan = c.plan("--task", "869fejukt")  # its branch is there: a stop
        self.assertEqual(plan["verdict"], "stop", out + err)
        self.assertNotIn("Task:", out)

    def test_existing_task_branch_already_in_main_is_offered_for_deletion(self):
        for lang, want in (("en", "Branch:   no — stop: branch task/869fejukt-old is already in "
                                  "main — delete it (git branch -d task/869fejukt-old) and run "
                                  "/commit again"),
                           ("ru", "Ветка:    нет — стоп: ветка task/869fejukt-old уже в main — "
                                  "удали её (git branch -d task/869fejukt-old) и снова /commit")):
            with self.subTest(lang=lang):
                c = Case({"a.txt": lines(5)})
                g(c.repo, "branch", "task/869fejukt-old")  # landed: main holds all of it
                c.t.edit(c.p("a.txt"), "line 3", "SHIELD")
                code, out, err, plan = c.plan("--task", "869fejukt", lang=lang)
                self.assertEqual((code, plan["verdict"]), (1, "stop"), out + err)
                self.assertIn(want, out)
                self.assertNotIn("git switch", out)

    def test_another_branch_of_the_task_does_not_offer_another_branch_name(self):
        c = Case({"a.txt": lines(5)})
        g(c.repo, "branch", "task/869fejukt-old")
        c.t.edit(c.p("a.txt"), "line 3", "SHIELD")
        code, out, err, plan = c.plan("--task", "869fejukt", "--branch", "task/869fejukt-new")
        self.assertNotIn("--branch", out)
        g(c.repo, "branch", "task/869fejukt-new")
        code, out, err, plan = c.plan("--task", "869fejukt", "--branch", "task/869fejukt-new")
        self.assertNotIn("--branch", out)
        code, out, err, plan = c.plan("--pr", "--branch", "task/869fejukt-new")
        self.assertIn("or pass another name with --branch", out)

    def test_taken_branch_checked_out_in_a_worktree_says_commit_there_not_switch(self):
        c = Case({"a.txt": lines(5)})
        wt = os.path.join(os.path.realpath(mkdir()), "ticket-869fejukt")
        g(c.repo, "worktree", "add", "-q", "-b", "task/869fejukt-shield", wt, "main")
        c.t.edit(c.p("a.txt"), "line 3", "SHIELD")
        code, out, err, plan = c.plan("--task", "869fejukt")
        self.assertEqual((code, plan["verdict"]), (1, "stop"), out + err)
        self.assertIn(f"branch task/869fejukt-shield already exists and is checked out in the "
                      f"worktree at {wt} — commit there", out)
        self.assertNotIn("git switch", out)

    def test_stopped_plan_on_another_branch_does_not_forecast_committing_there(self):
        c = Case({"a.txt": lines(5)})
        g(c.repo, "checkout", "-q", "-b", "feature")
        c.t.edit(c.p("a.txt"), "line 3", "SHIELD")
        write_file(c.p("a.txt"), read_file(c.p("a.txt")) + b"theirs\n")
        g(c.repo, "add", "a.txt")  # someone staged a change on the session's path
        code, out, err, plan = c.plan("--task", "869fejukt")
        self.assertEqual(plan["verdict"], "stop", out + err)
        self.assertIn("Branch:   no — stopped, see Commit", out)
        self.assertNotIn("committing there", out)

    def test_direct_task_branch_of_another_session_is_not_committed_onto(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
        c.t.edit(c.p("a.txt"), "line 3", "A WORK")
        c.plan("--task", "T1", "--branch", "task/T1-a", "--no-merge", "--no-push")
        self.assertEqual(c.apply("--no-merge", "--no-push", msg="A work (T1)\n")[0], 0)  # the shared checkout stays on task/T1-a
        a, tip = c.s, g(c.repo, "rev-parse", "HEAD")
        c.s = Session(c.cfg, c.repo)  # another tab in the same checkout, no task
        c.s.main.edit(c.p("b.txt"), "b", "B WORK")
        code, out, err, plan = c.plan()
        self.assertEqual((code, plan["verdict"]), (1, "stop"), out + err)
        self.assertIn("Branch:   no — stop: the branch task/T1-a holds commits not made by this "
                      "session's /commit: ", out)
        self.assertIn("go back to main (git switch main) and run /commit again for a branch of "
                      "its own; if an earlier session's /commit made them (a session that has "
                      "ended, not another open tab: --session also takes all of its uncommitted "
                      "edits), pass its --session as well as this one's", out)
        self.assertEqual(c.apply()[0], 1)
        self.assertEqual(g(c.repo, "rev-parse", "HEAD"), tip)
        self.assertIn("B WORK", read_file(c.p("b.txt")).decode())
        # A, after the merge of main its own stop names, still goes ahead on its branch
        other = os.path.join(os.path.realpath(mkdir()), "main-wt")
        g(c.repo, "worktree", "add", "-q", other, "main")
        write_file(os.path.join(other, "m.txt"), "m\n")
        g(other, "add", "m.txt")
        g(other, "commit", "-qm", "on main")
        g(c.repo, "merge", "-q", "--no-edit", "main")
        c.s = a
        c.t.edit(c.p("a.txt"), "line 4", "A MORE")
        code, out, err, plan = c.plan("--task", "T1", "--no-push")
        self.assertEqual(plan["verdict"], "go", out + err)

    def test_ticket_gate_commit_on_its_task_branch_is_not_committed_onto(self):
        for lang, want in (("en", "if it is a /ticket gate commit, /resume that session and "
                                  "publish at its gate"),
                           ("ru", "если это коммит шлюза /ticket — /resume ту сессию и публикуй в "
                                  "её шлюзе")):
            with self.subTest(lang=lang):
                c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
                g(c.repo, "switch", "-q", "-c", "task/LOCAL-17-discount")
                write_file(c.p("a.txt"), lines(5).replace("line 2", "GATE"))
                g(c.repo, "commit", "-qam", "Discount (LOCAL-17)")  # the gate's own commit
                head_sha = g(c.repo, "rev-parse", "HEAD")
                c.t.edit(c.p("b.txt"), "b", "B WORK")
                code, out, err, plan = c.plan("--task", "LOCAL-17", lang=lang)
                self.assertEqual((code, plan["verdict"]), (1, "stop"), out + err)
                self.assertIn(want, out)
                self.assertNotIn("git switch", out)  # going back ends at "already exists"
                self.assertEqual(c.apply("--task", "LOCAL-17", msg="B (LOCAL-17)\n")[0], 1)
                self.assertEqual(g(c.repo, "rev-parse", "HEAD"), head_sha)

    def test_no_merge_with_a_task_on_main_plans_the_push_of_the_new_branch(self):
        c = Case({"a.txt": lines(5)}, remote=True)
        c.t.edit(c.p("a.txt"), "line 3", "SHIELD")
        code, out, err, plan = c.plan("--task", "T1", "--branch", "task/T1-a", "--no-merge")
        self.assertEqual((code, plan and plan["verdict"]), (0, "go"), out + err)
        self.assertIn("Push:     yes — origin/task/T1-a (new branch on origin)", out)
        self.assertEqual(c.apply("--no-merge", msg="Shield (T1)\n")[0], 0)
        self.assertEqual(g(c.remote, "rev-parse", "task/T1-a"), g(c.repo, "rev-parse", "HEAD"))

    def test_no_merge_on_another_sessions_pushed_branch_does_not_push_into_it(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
        c.t.edit(c.p("a.txt"), "line 3", "A WORK")
        c.plan("--task", "T1", "--branch", "task/T1-a", "--no-merge", "--no-push")
        self.assertEqual(c.apply("--no-merge", "--no-push", msg="A work (T1)\n")[0], 0)
        g(c.repo, "push", "-q", "-u", "origin", "task/T1-a")  # A's branch and PR
        tip = g(c.remote, "rev-parse", "task/T1-a")
        c.s = Session(c.cfg, c.repo)
        c.s.main.edit(c.p("b.txt"), "b", "B WORK")
        code, out, err, plan = c.plan("--no-merge")
        self.assertEqual((code, plan["verdict"]), (1, "stop"), out + err)
        self.assertEqual(c.apply("--no-merge")[0], 1)
        self.assertEqual(g(c.remote, "rev-parse", "task/T1-a"), tip)

    def test_branch_stop_says_when_git_switch_back_would_be_refused(self):
        c = Case({"a.txt": lines(5), "CLAUDE.md": lines(12)}, remote=True)
        c.t.edit(c.p("CLAUDE.md"), "line 3", "RULE")
        write_file(c.p("CLAUDE.md"), read_file(c.p("CLAUDE.md")).replace(b"line 10", b"THEIRS"))
        c.plan("--task", "T1", "--branch", "task/T1-a", "--no-merge", "--no-push")
        self.assertEqual(c.apply("--no-merge", "--no-push", msg="A work (T1)\n")[0], 0)  # CLAUDE.md committed in part
        c.s = Session(c.cfg, c.repo)
        c.s.main.edit(c.p("a.txt"), "line 3", "B WORK")
        for args in (("--task", "T2"), ()):
            with self.subTest(args=args):
                code, out, err, plan = c.plan(*args)
                self.assertEqual(plan["verdict"], "stop", out + err)
                self.assertIn("git switch main would be refused: CLAUDE.md has uncommitted "
                              "changes (maybe someone else's) that main changes; no stash", out)
                self.assertNotIn("go back to main", out)
        proc = subprocess.run(["git", "switch", "main"], cwd=c.repo, env=BASE_ENV,
                              capture_output=True)
        self.assertNotEqual(proc.returncode, 0)  # git does refuse it

    def test_stopped_plan_forecasts_no_branch_and_no_task_link(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"})
        c.t.edit(c.p("a.txt"), "line 3", "SHIELD")
        write_file(c.p("a.txt"), read_file(c.p("a.txt")) + b"theirs\n")
        g(c.repo, "add", "a.txt")  # someone staged a change on the session's path
        code, out, err, plan = c.plan("--task", "869fejukt")
        self.assertEqual(plan["verdict"], "stop", out + err)
        self.assertIn("Branch:   no — stopped, see Commit", out)
        self.assertNotIn("will create it", out)
        g(c.repo, "reset", "-q")
        c2 = Case({"a.txt": lines(5)})
        code, out, err, plan = c2.plan("--task", "869fejukt")  # nothing to commit
        self.assertNotIn("Task:", out)

    def test_task_on_another_tasks_branch_stops(self):
        c = Case({"a.txt": lines(5)})
        g(c.repo, "checkout", "-q", "-b", "task/869fhab12-npc-shield")
        c.t.edit(c.p("a.txt"), "line 3", "SHIELD")
        code, out, err, plan = c.plan("--task", "869fzzz99")
        self.assertEqual((code, plan["verdict"]), (1, "stop"), out + err)
        self.assertIn("Branch:   no — stop: task/869fhab12-npc-shield is another task's branch, "
                      "not 869fzzz99's; go back to main (git switch main) and run /commit again", out)
        code, out, err, plan = c.plan("--task", "869fhab12")
        self.assertIn("Branch:   task/869fhab12-npc-shield — committing there", out)

    def test_branch_made_despite_a_failing_post_checkout_hook_is_reported_made(self):
        c = Case({"a.txt": lines(5)})
        write_file(c.p(".git/hooks/post-checkout"), "#!/bin/sh\necho hook broke >&2\nexit 2\n")
        os.chmod(c.p(".git/hooks/post-checkout"), 0o755)
        c.t.edit(c.p("a.txt"), "line 3", "SHIELD")
        c.plan("--task", "869fejukt", "--branch", "task/869fejukt-shield", "--no-push")
        code, out, err = c.apply("--no-push", msg="Shield (869fejukt)\n")
        self.assertEqual(code, 0, out + err)
        self.assertIn("Branch:   task/869fejukt-shield — created from main (the post-checkout "
                      "hook failed: hook broke)", out)
        self.assertIn("SHIELD", c.show("a.txt", "task/869fejukt-shield"))

    def test_task_on_another_branch_is_committed_there(self):
        c = Case({"a.txt": lines(5)}, remote=True)
        g(c.repo, "checkout", "-q", "-b", "feature")
        c.t.edit(c.p("a.txt"), "line 3", "SHIELD")
        code, out, err, plan = c.plan("--task", self.URL, "--no-merge")
        self.assertIn("Branch:   feature — committing there", out)
        code, out, err = c.apply("--no-merge", msg="Shield\n\n" + self.URL + "\n")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(g(c.repo, "branch", "--show-current"), "feature\n")
        self.assertEqual(g(c.remote, "rev-parse", "feature"), g(c.repo, "rev-parse", "HEAD"))

    def test_refused_commit_takes_the_new_branch_back(self):
        c = Case({"a.txt": lines(5)}, remote=True)
        hook = c.p(".git/hooks/pre-commit")
        write_file(hook, "#!/bin/sh\nexit 1\n")
        os.chmod(hook, 0o755)
        c.t.edit(c.p("a.txt"), "line 3", "SHIELD")
        c.plan("--task", "869fejukt", "--branch", "task/869fejukt-x")
        code, out, err = c.apply(msg="x (869fejukt)\n")
        self.assertEqual(code, 1, out + err)
        self.assertIn("Branch:   task/869fejukt-x removed — no commit; back on main", out)
        self.assertEqual(g(c.repo, "branch", "--show-current"), "main\n")
        self.assertEqual(g(c.repo, "branch", "--list", "task/*"), "")


class PullRequestRepos(unittest.TestCase):

    def test_pr_history_is_detected_and_main_is_never_touched(self):
        c = Case({"a.txt": lines(5)}, remote=True)
        pr_history(c)
        log = fake_gh(c)
        main = g(c.repo, "rev-parse", "main")
        c.t.edit(c.p("a.txt"), "line 3", "SESSION")
        code, out, err, plan = c.plan(lang="ru")
        self.assertEqual(code, 0, out + err)
        self.assertIn("Ветка:    session/<тема коммита> — создам от main", out)
        self.assertIn("В main:   нет — в этом репо через PR (4 из 5 последних коммитов main "
                      "пришли через PR)", out)
        self.assertIn("PR:       не открываю сам — открой PR из session/<тема> в main", out)
        code, out, err = c.apply(msg="Ускорить загрузку уровня\n\nТело коммита.\n")
        self.assertEqual(code, 0, out + err)
        self.assertIn("Ветка:    session/uskorit-zagruzku-urovnya — создана от main", out)
        self.assertIn("Push:     готово — origin/session/uskorit-zagruzku-urovnya", out)
        self.assertIn("PR:       не открываю сам — открой PR из session/uskorit-zagruzku-urovnya в "
                      "main (с GitHub CLI: gh pr create --fill --base main --head "
                      "session/uskorit-zagruzku-urovnya)", out)
        self.assertEqual(g(c.repo, "rev-parse", "main"), main)
        self.assertEqual(g(c.remote, "rev-parse", "main"), main)
        self.assertEqual(g(c.repo, "rev-parse", "--abbrev-ref", "@{upstream}"),
                         "origin/session/uskorit-zagruzku-urovnya\n")
        self.assertFalse(os.path.exists(log))  # gh never ran

    def test_pr_line_names_the_github_page_to_open_the_pr_and_gh_never_runs(self):
        for url in ("https://github.com/o/r.git", "git@github.com:o/r.git"):
            with self.subTest(url=url):
                c = Case({"a.txt": lines(5)}, remote=True)
                g(c.repo, "config", f"url.{c.remote}.insteadOf", url)
                g(c.repo, "remote", "set-url", "origin", url)
                g(c.repo, "checkout", "-q", "-b", "task/77-x")
                log = fake_gh(c)
                c.t.edit(c.p("a.txt"), "line 3", "SESSION")
                want = ("PR:       not opened by /commit — open it (or see the open one): "
                        "https://github.com/o/r/compare/main...task/77-x?expand=1")
                code, out, err, plan = c.plan("--pr", "--task", "77")
                self.assertIn(want, out)
                code, out, err = c.apply("--pr", "--task", "77", msg="Fix (#77)\n")
                self.assertEqual(code, 0, out + err)
                self.assertIn(want, out)
                self.assertEqual(g(c.remote, "rev-parse", "task/77-x"), g(c.repo, "rev-parse",
                                                                          "HEAD"))
                self.assertFalse(os.path.exists(log))  # gh never ran

    def test_session_branch_name_taken_on_origin_gets_a_suffix(self):
        c = Case({"a.txt": lines(5)}, remote=True)
        pr_history(c)
        g(c.repo, "push", "-q", "origin", "main:refs/heads/session/fix-typo")
        g(c.repo, "fetch", "-q", "origin")
        c.t.edit(c.p("a.txt"), "line 3", "SESSION")
        c.plan()
        code, out, err = c.apply(msg="Fix typo\n")
        self.assertEqual(code, 0, out + err)
        self.assertIn("Branch:   session/fix-typo-2 — created from main", out)

    def test_pr_branch_of_another_session_is_not_committed_onto_even_with_its_task(self):
        for lang, want in (("en", "Branch:   no — stop: the branch task/T1-x holds commits not "
                                  "made by this session's /commit: "),
                           ("ru", "нет — стоп: на ветке task/T1-x коммиты не из /commit этой "
                                  "сессии: ")):
            c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
            c.t.edit(c.p("a.txt"), "line 3", "X WORK")
            c.plan("--pr", "--task", "T1", "--branch", "task/T1-x")
            self.assertEqual(c.apply("--pr", msg="X work (T1)\n")[0], 0)
            tip = g(c.remote, "rev-parse", "task/T1-x")
            x_sid = c.s.sid
            c.s = Session(c.cfg, c.repo)  # another session in the same checkout, no task
            c.s.main.edit(c.p("b.txt"), "b", "Y WORK")
            code, out, err, plan = c.plan("--pr", lang=lang)
            self.assertEqual(plan["verdict"], "stop", out + err)
            self.assertIn(want, out)
            self.assertIn("X work (T1)", out)
            self.assertEqual(c.apply("--pr")[0], 1)
            self.assertEqual(g(c.remote, "rev-parse", "task/T1-x"), tip)
            self.assertIn("Y WORK", read_file(c.p("b.txt")).decode())
            # the task taken from the branch name proves nothing: still a stop, and no push
            c.s.main.edit(c.p("b.txt"), "Y WORK", "Y WORK (T1)")
            code, out, err, plan = c.plan("--pr", "--task", "T1", lang=lang)
            self.assertEqual(plan["verdict"], "stop", out + err)
            self.assertIn(want, out)
            self.assertNotIn("Task:", out)
            self.assertNotIn("Задача:", out)
            self.assertEqual(c.apply("--pr", msg="Y work (T1)\n")[0], 1)
            self.assertEqual(g(c.remote, "rev-parse", "task/T1-x"), tip)
            # the task's earlier session, passed with --session, makes those commits its own
            code, out, err, plan = c.plan("--pr", "--task", "T1", "--session", x_sid,
                                          "--session", c.s.sid, lang=lang)
            self.assertEqual(plan["verdict"], "go", out + err)

    def test_pushed_branch_without_this_sessions_commits_is_neither_pushed_nor_given_a_pr(self):
        for shape in ("another session's", "a person's"):
            for lang, want in (("en", "no — the branch holds no commit made by this session's "
                                      "/commit — if the branch is yours, open its PR yourself"),
                               ("ru", "нет — на ветке нет коммитов /commit этой сессии — если "
                                      "ветка твоя, открой её PR сам")):
                with self.subTest(shape=shape, lang=lang):
                    c = Case({"a.txt": lines(5)}, remote=True)
                    pr_history(c)
                    if shape == "a person's":
                        g(c.repo, "checkout", "-q", "-b", "feature/human")
                        write_file(c.p("h.txt"), "wip\n")
                        g(c.repo, "add", "h.txt")
                        g(c.repo, "commit", "-qm", "Human WIP")
                        g(c.repo, "push", "-q", "-u", "origin", "feature/human")
                    else:
                        c.t.edit(c.p("a.txt"), "line 3", "X WORK")
                        c.plan("--task", "T1", "--branch", "task/T1-x")
                        self.assertEqual(c.apply(msg="X work (T1)\n")[0], 0)
                        c.s = Session(c.cfg, c.repo)  # this session changed nothing here
                    code, out, err, plan = c.plan(lang=lang)
                    self.assertEqual(plan["verdict"], "nothing", out + err)
                    self.assertIn("PR:       " + want, out)
                    code, out, err = c.apply()
                    self.assertEqual(code, 0, out + err)
                    self.assertIn("PR:       " + want, out)

    def test_someones_unpushed_clean_merge_on_their_branch_is_not_pushed(self):
        for mode in ("--pr", "--no-merge"):
            with self.subTest(mode=mode):
                c = Case({"a.txt": lines(5)}, remote=True)
                g(c.repo, "checkout", "-q", "-b", "feature/human")
                write_file(c.p("h.txt"), "wip\n")
                g(c.repo, "add", "h.txt")
                g(c.repo, "commit", "-qm", "Human WIP")
                g(c.repo, "push", "-q", "-u", "origin", "feature/human")
                other = os.path.join(os.path.realpath(mkdir()), "other")
                g(c.repo, "worktree", "add", "-q", other, "main")
                write_file(os.path.join(other, "m.txt"), "m\n")
                g(other, "add", "m.txt")
                g(other, "commit", "-qm", "on main")
                g(other, "push", "-q", "origin", "main")
                g(c.repo, "fetch", "-q", "origin")
                g(c.repo, "merge", "-q", "--no-edit", "origin/main")  # theirs, clean, unpushed
                tip = g(c.remote, "rev-parse", "feature/human")
                code, out, err, plan = c.plan(mode)  # this session changed nothing here
                self.assertEqual(plan["verdict"], "nothing", out + err)
                self.assertIn("Push:     no — the branch holds no commit made by this session's "
                              "/commit", out)
                self.assertEqual(c.apply(mode)[0], 0)
                self.assertEqual(g(c.remote, "rev-parse", "feature/human"), tip)

    def test_rerun_with_nothing_new_names_the_pr_page_again(self):
        c = Case({"a.txt": lines(5)}, remote=True)
        g(c.repo, "checkout", "-q", "-b", "feature")
        c.t.edit(c.p("a.txt"), "line 3", "SESSION")
        c.plan("--pr")
        self.assertEqual(c.apply("--pr")[0], 0)
        code, out, err, plan = c.plan("--pr")
        self.assertEqual(plan["verdict"], "nothing", out + err)
        self.assertIn("Push:     no — origin/feature already has everything", out)
        self.assertIn("PR:       not opened by /commit — open a PR from feature into main", out)

    def test_pr_branch_with_nothing_beyond_main_is_neither_pushed_nor_promised_a_pr(self):
        c = Case({"a.txt": lines(5)}, remote=True)
        g(c.repo, "checkout", "-q", "-b", "feature")
        code, out, err, plan = c.plan("--pr")
        self.assertEqual(plan["verdict"], "nothing", out + err)
        self.assertIn("PR:       no — the branch has nothing beyond origin/main", out)
        self.assertNotIn("open a PR", out)
        code, out, err = c.apply("--pr")
        self.assertEqual(code, 0, out + err)
        self.assertNotIn("feature", g(c.remote, "branch"))

    def test_main_override_in_a_pr_repository_fast_forwards_main(self):
        c = Case({"a.txt": lines(5)}, remote=True)
        pr_history(c)
        c.t.edit(c.p("a.txt"), "line 3", "SESSION")
        code, out, err, plan = c.plan("--main")
        self.assertNotIn("Branch:", out)
        code, out, err = c.apply("--main")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(g(c.remote, "rev-parse", "main"), g(c.repo, "rev-parse", "HEAD"))
        self.assertNotIn("PR:", out)

    def test_direct_commits_naming_issues_are_not_taken_for_squash_merges(self):
        for committer, mode in (("t@t", "direct"), ("noreply@github.com", "pr")):
            with self.subTest(committer=committer):
                c = Case({"a.txt": lines(5)}, remote=True)
                env = dict(BASE_ENV, GIT_COMMITTER_EMAIL=committer)
                for i in range(1, 4):
                    write_file(c.p(f"f{i}.txt"), f"{i}\n")
                    g(c.repo, "add", f"f{i}.txt")
                    subprocess.run(["git", "commit", "-qm", f"Fix thing {i} (#{i})"], cwd=c.repo,
                                   env=env, check=True)
                g(c.repo, "push", "-q", "origin", "main")
                log = fake_gh(c)
                c.t.edit(c.p("a.txt"), "line 3", "SESSION")
                code, out, err, plan = c.plan("--task", "#14", lang="ru")
                self.assertEqual((code, plan["mode"]), (0, mode), out + err)
                if mode == "pr":
                    self.assertIn("В main:   нет — в этом репо через PR (3 из 4 последних "
                                  "коммитов main пришли через PR)", out)
                    continue
                self.assertNotIn("через PR", out)
                code, out, err = c.apply(msg="Fix level load (#14)\n")
                self.assertEqual(code, 0, out + err)
                self.assertEqual(g(c.remote, "rev-parse", "main"), g(c.repo, "rev-parse", "HEAD"))
                self.assertEqual(g(c.repo, "rev-parse", "main"), g(c.repo, "rev-parse", "HEAD"))
                self.assertFalse(os.path.exists(log))  # gh never ran

    def test_pr_repository_on_main_with_someones_unpushed_commit_says_review_them(self):
        for lang, want in (("en", "hand work — not publishing them; review and push them "
                                  "yourself"),
                           ("ru", "hand work — их не публикую; проверь и запушь их сам")):
            with self.subTest(lang=lang):
                c = Case({"a.txt": lines(5)}, remote=True)
                pr_history(c)
                write_file(c.p("h.txt"), "h\n")
                g(c.repo, "add", "h.txt")
                g(c.repo, "commit", "-qm", "hand work")
                code, out, err, plan = c.plan(lang=lang)
                self.assertEqual((code, plan["verdict"]), (1, "stop"), out + err)
                self.assertIn(want, out)
                self.assertNotIn("git switch -c", out)

    def test_pr_repository_on_main_with_unpushed_commits_stops(self):
        c = Case({"a.txt": lines(5), "b.txt": "b\n"}, remote=True)
        pr_history(c)
        c.t.edit(c.p("a.txt"), "line 3", "SESSION")
        self.assertEqual(c.land("--main", "--no-push")[0], 0)
        code, out, err, plan = c.plan()
        self.assertEqual((code, plan["verdict"]), (1, "stop"), out + err)
        self.assertIn("main has 1 commit origin lacks, and this repository takes work through "
                      "PRs — not pushing main", out)


if __name__ == "__main__":
    try:
        unittest.main(verbosity=1)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
