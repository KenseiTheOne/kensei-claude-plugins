#!/usr/bin/env python3
"""Tests for board.py and hooks/board-nudge.sh — run: python3 board_test.py"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BOARDS = tempfile.mkdtemp()
os.environ["KENSEI_TASK_BOARDS_DIR"] = BOARDS  # markers stay out of ~/.claude
sys.path.insert(0, HERE)
import board  # noqa: E402

SCRIPT = os.path.join(HERE, "board.py")
WRAPPER = os.path.join(HERE, "..", "..", "hooks", "board-nudge.sh")
URL = "https://claude.ai/artifact/BoArD123"
OTHER = "https://claude.ai/artifact/OtHeR456"


# --- transcript builders ------------------------------------------------------------------

def human(text):
    return {"type": "user", "origin": {"kind": "human"}, "promptSource": "typed",
            "message": {"role": "user", "content": text}}


def use(name, **inp):
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "t", "name": name, "input": inp}]}}


def result(error=False):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t", "content": "ok", "is_error": error}]}}


def notification(text="<task-notification>done</task-notification>"):
    return {"type": "user", "origin": {"kind": "task-notification"}, "promptSource": "system",
            "message": {"role": "user", "content": text}}


def peer(text="agent report"):
    return {"type": "user", "origin": {"kind": "peer"}, "isMeta": True, "promptSource": "system",
            "message": {"role": "user", "content": text}}


def sdk(text):
    return {"type": "user", "promptSource": "sdk", "message": {"role": "user", "content": text}}


def queued(text, kind="human"):
    return {"type": "attachment", "attachment": {"type": "queued_command", "prompt": text,
                                                 "commandMode": "prompt",
                                                 "origin": {"kind": kind}}}


def hook_feedback():
    return {"type": "user", "isMeta": True, "message": {
        "role": "user", "content": "Stop hook feedback:\nBoard check (kensei-toolkit:board): ..."}}


def edit():
    return use("Edit", file_path="/x.py", old_string="a", new_string="b")


def board_write(url=URL, action="update"):
    return use("ArtifactData", action=action, url=url, collection="tasks", doc_id="t01")


def status_write(url=URL, doc="t01", **fields):
    return use("ArtifactData", action="update", url=url, collection="tasks", doc_id=doc, data=fields)


def transcript(*entries):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    return path


def arm(session="s1", url=URL):
    with open(board.marker(session), "w") as f:
        json.dump({"url": url, "plan": "", "armedAt": "x"}, f)


def payload(path, session="s1", active=False):
    return {"session_id": session, "transcript_path": path, "hook_event_name": "Stop",
            "stop_hook_active": active}


class NudgeTest(unittest.TestCase):
    def setUp(self):
        for name in os.listdir(BOARDS):
            os.remove(os.path.join(BOARDS, name))
        arm()

    def nudge(self, *entries, **kw):
        return board.nudge(payload(transcript(*entries), **kw))

    def test_work_without_board_write_blocks(self):
        out = self.nudge(human("сделай шаг 3"), edit(), result())
        self.assertEqual(out["decision"], "block")
        self.assertIn(URL, out["reason"])

    def test_work_then_board_write_is_quiet(self):
        self.assertIsNone(self.nudge(human("go"), edit(), result(), board_write(), result()))

    def test_board_write_then_more_work_blocks(self):
        out = self.nudge(human("go"), board_write(), result(), use("Bash", command="make test"),
                         result())
        self.assertEqual(out["decision"], "block")

    def test_status_outside_the_board_blocks(self):
        out = self.nudge(human("go"), edit(), status_write(status="open"))
        self.assertIn('t01: status "open"', out["reason"])

    def test_progress_waiting_for_the_user_blocks(self):
        for fields in ({"note": "Сделано, ждёт твоей проверки"}, {"check": "waiting for the user"},
                       {"question": "Какой вариант?"}):
            out = self.nudge(human("go"), status_write(status="progress", **fields))
            self.assertIn("t01: progress, but it waits for the user", out["reason"])

    def test_batch_status_is_checked(self):
        batch = use("ArtifactData", action="batch", url=URL, writes=[
            {"op": "set", "collection": "tasks", "doc_id": "t02", "data": {"status": "todo"}},
            {"op": "update", "collection": "tasks", "doc_id": "t03", "data": {"status": "open"}}])
        out = self.nudge(human("go"), batch)
        self.assertIn("t03", out["reason"])
        self.assertNotIn("t02", out["reason"])

    def test_later_write_fixes_the_status(self):
        self.assertIsNone(self.nudge(human("go"), edit(), status_write(status="progress", note="ждёт решения"),
                                     status_write(status="decision", question="Какой?")))

    def test_right_statuses_are_quiet(self):
        self.assertIsNone(self.nudge(human("go"), status_write(status="review", check="ждёт твоей проверки"),
                                     status_write(doc="t02", status="progress", note="делаю", question={"__delete__": True})))

    def test_status_on_another_board_is_not_checked(self):
        self.assertIsNone(self.nudge(human("go"), status_write(url=OTHER, status="open")))

    def test_read_only_turn_is_quiet(self):
        self.assertIsNone(self.nudge(human("что там?"), use("Read", file_path="/x"), result(),
                                     use("Grep", pattern="x"), result()))

    def test_talk_only_turn_is_quiet(self):
        self.assertIsNone(self.nudge(human("привет")))

    def test_stop_hook_active_is_quiet(self):
        self.assertIsNone(self.nudge(human("go"), edit(), result(), active=True))

    def test_no_marker_is_quiet(self):
        self.assertIsNone(self.nudge(human("go"), edit(), result(), session="other"))

    def test_stale_marker_is_quiet(self):
        old = time.time() - board.MARK_TTL - 60
        os.utime(board.marker("s1"), (old, old))
        self.assertIsNone(self.nudge(human("go"), edit(), result()))

    def test_write_to_another_board_blocks(self):
        out = self.nudge(human("go"), edit(), result(), board_write(OTHER), result())
        self.assertEqual(out["decision"], "block")

    def test_board_read_is_not_a_write(self):
        out = self.nudge(human("go"), edit(), result(), board_write(action="list"), result())
        self.assertEqual(out["decision"], "block")

    def test_earlier_turns_do_not_count(self):
        self.assertIsNone(self.nudge(human("раньше"), edit(), result(), human("теперь"),
                                     use("Read", file_path="/x"), result()))

    def test_create_flow_is_quiet(self):
        # SKILL.md "From a plan": publish, link the plan, fill the board, arm — not handed back
        self.assertIsNone(self.nudge(
            human("/kensei-toolkit:board plan.md"), use("Artifact", file_path="/s/board.html"),
            result(), edit(), result(), board_write(action="batch"), result(),
            use("Bash", command='python3 "/p/skills/board/board.py" arm --session s1 --url ' + URL),
            result()))

    def test_plan_edit_after_filling_blocks(self):
        # the order the reviewers caught: fill, then edit the plan — the edit is work after it
        out = self.nudge(human("/kensei-toolkit:board plan.md"), board_write(action="batch"),
                         result(), edit(), result(),
                         use("Bash", command="python3 board.py arm --session s1 --url " + URL),
                         result())
        self.assertEqual(out["decision"], "block")

    def test_project_file_named_board_py_is_work(self):
        out = self.nudge(human("go"), use("Bash", command="sed -i s/a/b/ src/board.py && pytest"),
                         result())
        self.assertEqual(out["decision"], "block")

    def test_failed_board_write_does_not_count(self):
        out = self.nudge(human("go"), edit(), result(), board_write(), result(error=True))
        self.assertEqual(out["decision"], "block")

    def test_queued_message_does_not_end_the_turn(self):
        out = self.nudge(human("сделай шаг 3"), edit(), result(), queued("и ещё вот что"))
        self.assertEqual(out["decision"], "block")

    def test_notification_turn_does_not_see_earlier_work(self):
        # the earlier turn was handed back and answered "nothing moved"; a notification arrives
        self.assertIsNone(self.nudge(human("go"), use("Bash", command="make", run_in_background=True),
                                     result(), hook_feedback(), notification()))

    def test_agent_report_starts_a_turn(self):
        self.assertIsNone(self.nudge(human("go"), use("Agent", prompt="review"), result(), peer()))

    def test_work_in_a_notification_turn_blocks(self):
        out = self.nudge(human("go"), notification(), edit(), result())
        self.assertEqual(out["decision"], "block")

    def test_sdk_prompt_starts_a_turn(self):
        self.assertIsNone(self.nudge(sdk("first"), edit(), result(), sdk("second")))

    def test_hook_feedback_does_not_start_a_turn(self):
        out = self.nudge(human("go"), edit(), result(), hook_feedback())
        self.assertEqual(out["decision"], "block")

    def test_read_only_bash_is_not_work(self):
        self.assertIsNone(self.nudge(human("что там?"), use("Bash", command="git status && cat a"),
                                     result(), use("Bash", command="grep -rn x src | head"), result()))

    def test_bash_that_writes_is_work(self):
        out = self.nudge(human("go"), use("Bash", command="git status"), result(),
                         use("Bash", command="sed -i s/a/b/ f"), result())
        self.assertEqual(out["decision"], "block")

    def test_workflow_is_work(self):
        out = self.nudge(human("go"), use("Workflow", script="..."), result())
        self.assertEqual(out["decision"], "block")

    def test_subagent_entries_do_not_count(self):
        side = edit()
        side["isSidechain"] = True
        self.assertIsNone(self.nudge(human("go"), side, result()))

    def test_agent_call_is_work(self):
        out = self.nudge(human("go"), use("Agent", prompt="do it"), result())
        self.assertEqual(out["decision"], "block")


class ReadsOnlyTest(unittest.TestCase):
    READS = ["cat a.txt", "cat a | wc -l", "git status", "git -C /x log --oneline -5",
             'grep -rn "a|b" src', "sed -n 1,20p f", "ls -la 2>/dev/null", "cd /x && git diff --stat",
             'find . -name "*.py"', "rg foo 2>&1 | head", "git log --oneline -3\ngit status -sb",
             "S=/tmp/x; cat $S/a.json | jq .n", "cat a;"]
    WRITES = ["cat a > b", "echo hi >> log", "sed -i s/a/b/ f", "pytest", "python3 x.py",
              "find . -delete", "git commit -m x", "git branch -D x", "cat $(rm x)", "cat a | tee b",
              "sort -o out in", "make test", "ls; rm -rf x", "grep x f && cp f g",
              "cat <<EOF > f\nhi\nEOF", "", "cat 'unclosed", "ls\nrm -rf x", "X=1 make",
              "S=/tmp/x; cp a $S", "cat <<EOF\nhi\nEOF"]

    def test_readers(self):
        for cmd in self.READS:
            self.assertTrue(board.reads_only(cmd), cmd)

    def test_writers_and_unknowns(self):
        for cmd in self.WRITES:
            self.assertFalse(board.reads_only(cmd), cmd)


class CliTest(unittest.TestCase):
    def run_cli(self, *args, stdin=""):
        return subprocess.run([sys.executable, SCRIPT, *args], input=stdin, capture_output=True,
                              text=True)

    def test_arm_and_disarm(self):
        r = self.run_cli("arm", "--session", "cli1", "--url", URL, "--plan", "/p.md")
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(board.marker("cli1")) as f:
            self.assertEqual(json.load(f)["url"], URL)
        self.assertEqual(self.run_cli("disarm", "--session", "cli1").returncode, 0)
        self.assertFalse(os.path.exists(board.marker("cli1")))

    def test_arm_refuses_unsubstituted_session(self):
        r = self.run_cli("arm", "--session", "${CLAUDE_SESSION_ID}", "--url", URL)
        self.assertEqual(r.returncode, 2)

    def test_arm_refuses_non_artifact_url(self):
        r = self.run_cli("arm", "--session", "cli2", "--url", "https://example.com/x")
        self.assertEqual(r.returncode, 2)

    def test_nudge_bad_stdin_is_quiet(self):
        r = self.run_cli("nudge", stdin="not json")
        self.assertEqual((r.returncode, r.stdout), (0, ""))


class WrapperTest(unittest.TestCase):
    def setUp(self):
        for name in os.listdir(BOARDS):
            os.remove(os.path.join(BOARDS, name))

    def run_wrapper(self, data):
        return subprocess.run(["sh", WRAPPER], input=json.dumps(data), capture_output=True,
                              text=True, env=dict(os.environ))

    def test_blocks_in_a_board_session(self):
        arm("w1")
        r = self.run_wrapper(payload(transcript(human("go"), edit(), result()), session="w1"))
        self.assertEqual(json.loads(r.stdout)["decision"], "block")

    def test_quiet_without_marker(self):
        r = self.run_wrapper(payload(transcript(human("go"), edit(), result()), session="w2"))
        self.assertEqual(r.stdout, "")

    def test_quiet_when_already_handed_back(self):
        arm("w3")
        r = self.run_wrapper(payload(transcript(human("go"), edit(), result()), session="w3",
                                     active=True))
        self.assertEqual(r.stdout, "")

    def test_true_elsewhere_does_not_count_as_active(self):
        arm("w4")
        data = payload(transcript(human("go"), edit(), result()), session="w4")
        data["last_assistant_message"] = 'set "stop_hook_active": true in the config'
        self.assertEqual(json.loads(self.run_wrapper(data).stdout)["decision"], "block")

    def test_ascii_locale(self):
        arm("w5")
        data = payload(transcript(human("го"), edit(), result()), session="w5")
        data["last_assistant_message"] = "готово — шаг 3"
        env = dict(os.environ, LANG="en_US.US-ASCII", LC_ALL="en_US.US-ASCII")
        r = subprocess.run(["sh", WRAPPER], input=json.dumps(data, ensure_ascii=False).encode(),
                           capture_output=True, env=env)
        self.assertEqual(json.loads(r.stdout.decode("utf-8"))["decision"], "block", r.stderr)

    def test_unusual_boards_dir_is_left_to_python(self):
        rel = os.path.relpath(BOARDS)
        arm("w6")
        env = dict(os.environ, KENSEI_TASK_BOARDS_DIR=rel)
        data = payload(transcript(human("go"), edit(), result()), session="w6")
        r = subprocess.run(["sh", WRAPPER], input=json.dumps(data), capture_output=True,
                           text=True, env=env)
        self.assertEqual(json.loads(r.stdout)["decision"], "block", r.stderr)


if __name__ == "__main__":
    unittest.main()
