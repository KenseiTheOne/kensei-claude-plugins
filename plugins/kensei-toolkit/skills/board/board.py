#!/usr/bin/env python3
"""Board marker and Stop-hook nudge for /kensei-toolkit:board.

  board.py arm --session <id> --url <board-url> [--plan <path>]
      Bind this session to a board: writes <boards dir>/<session>.json.
  board.py disarm --session <id>
      Unbind the session (the board is finished or abandoned).
  board.py nudge  < Stop-hook payload on stdin
      If this turn did work (Edit, Write, NotebookEdit, Bash that does more than read, PowerShell,
      Agent, Workflow) and made no successful write to the bound board after the last piece of
      it, print a Stop-hook block whose reason asks the model to bring the board up to date — or
      to say in one line that nothing on it moved. Prints nothing otherwise. Never blocks twice in
      a row (stop_hook_active).

A turn starts at a prompt from outside the model: a typed message, a background-task
notification, an agent's report, an SDK prompt. Messages queued while the model works do not end
the turn (the work before them still counts); Stop-hook feedback and other meta entries do not
either.

The boards dir is $KENSEI_TASK_BOARDS_DIR or ~/.claude/task-boards; a marker older than 14 days
is ignored. hooks/board-nudge.sh runs `nudge` only for sessions that have a marker.
"""

import argparse
import json
import os
import re
import shlex
import sys
import time

BOARDS = os.path.expanduser(os.environ.get("KENSEI_TASK_BOARDS_DIR") or "~/.claude/task-boards")
MARK_TTL = 14 * 24 * 3600
WORK_TOOLS = {"Edit", "Write", "NotebookEdit", "Bash", "PowerShell", "Agent", "Workflow"}
BOARD_WRITES = {"set", "update", "delete", "str_replace", "batch"}
# the skill's own calls to this script are bookkeeping, not work on a task
SELF_CALL = re.compile(r"\bboard\.py\S{0,3}\s+(?:arm|disarm)\b")
ARTIFACT_ID = re.compile(r"claude\.ai/(?:code/)?artifact/([A-Za-z0-9_-]+)")
MAX_SCAN = 64 << 20
MAX_QUEUED = 8

# Bash that only reads is not work: every command in it starts with one of these and nothing
# writes to a file. Anything unknown is work — a miss here only skips one reminder.
READERS = {"cat", "head", "tail", "less", "ls", "wc", "grep", "egrep", "fgrep", "rg", "jq", "pwd",
           "stat", "file", "which", "tree", "du", "df", "echo", "printf", "uniq", "cut", "tr",
           "diff", "cmp", "basename", "dirname", "realpath", "readlink", "date", "cd", "true"}
GIT_READERS = {"status", "log", "diff", "show", "rev-parse", "ls-files", "blame", "grep",
               "describe", "shortlog", "reflog"}
FIND_WRITERS = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf",
                "-fls"}
SEPARATORS = {"|", "||", "&&", ";", "&", "|&"}
NULL_TARGETS = {"/dev/null", "/dev/stdout", "/dev/stderr"}
ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def reads_only(command):
    """True when a shell command only reads: each part a known reader, no write to a file."""
    if any(x in command for x in ("$(", "`", "<(", ">(", "<<")):  # substitutions, heredocs
        return False
    try:  # a line break between commands is a separator like ";"
        lex = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        return False
    parts, part, i = [], [], 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in SEPARATORS:
            parts.append(part)
            part = []
        elif set(tok) <= set("<>&") and ">" in tok:  # >, >>, &>, >&: only to a null sink or an fd
            target = tokens[i + 1] if i + 1 < len(tokens) else ""
            if part and part[-1].isdigit():
                part.pop()
            if not (target in NULL_TARGETS or (tok.endswith("&") and target.isdigit())):
                return False
            i += 1
        elif tok == "<":
            i += 1  # reading from a file is still reading
        else:
            part.append(tok)
        i += 1
    parts.append(part)
    parts = [p for p in parts if p]  # "a ;; b", a trailing ";"
    return bool(parts) and all(part_reads(part) for part in parts)


def part_reads(words):
    while words and ASSIGN.match(words[0]):  # S=/path, or S=/path cat …
        words = words[1:]
    if not words:
        return True
    prog, args = os.path.basename(words[0]), words[1:]
    if prog in READERS:
        return True
    if prog == "sed":
        return "-n" in args and not any(a.startswith(("-i", "--in-place")) for a in args)
    if prog == "find":
        return not FIND_WRITERS & set(args)
    if prog == "sort":
        return not any(a.startswith(("-o", "--output")) for a in args)
    if prog == "git":
        rest = list(args)
        while rest and rest[0].startswith("-"):  # git -C dir / -c k=v / --no-pager …
            flag = rest.pop(0)
            if flag in {"-C", "-c"} and rest:
                rest.pop(0)
        return bool(rest) and rest[0] in GIT_READERS
    return False


REASON = (
    "Board check (kensei-toolkit:board): this turn did work, and the task board {url} was not "
    "written after it. If a task on the board started, finished, hit a question, or the user "
    "answered, accepted or sent work back in chat, or a new task appeared, update the board now "
    "with ArtifactData (status, check, question; review when you finished, done only on the "
    "user's word). If nothing on the board moved, say so in one short line and stop — do not "
    "write to the board just to satisfy this check.")


def marker(session_id):
    safe = re.sub(r"[^A-Za-z0-9_-]", "", session_id or "")
    return os.path.join(BOARDS, safe + ".json") if safe else None


def read_marker(session_id):
    path = marker(session_id)
    if not path or not os.path.isfile(path):
        return None
    if time.time() - os.path.getmtime(path) > MARK_TTL:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and artifact_id(data.get("url")) else None


def artifact_id(url):
    m = ARTIFACT_ID.search(url or "") if isinstance(url, str) else None
    return m.group(1) if m else None


def is_queued(entry):
    """A message queued while the model was busy: it arrives inside a turn, not as a new one."""
    return entry.get("type") == "attachment" and \
        (entry.get("attachment") or {}).get("type") == "queued_command"


def is_turn_start(entry):
    """A prompt from outside the model: typed, a task notification, an agent report, an SDK
    prompt. Not a tool result, Stop-hook feedback or another meta entry without an origin."""
    if entry.get("type") != "user" or entry.get("isSidechain"):
        return False
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return False
    if isinstance(entry.get("origin"), dict):
        return True
    if entry.get("isMeta"):
        return False
    if isinstance(content, str) and content.startswith("<local-command"):
        return False
    return True  # an SDK prompt, or a prompt from a Claude Code without `origin`


def turn_entries(transcript_path):
    """The main-session entries since the turn started, oldest first."""
    entries, queued = [], 0
    with open(transcript_path, "rb") as f:
        f.seek(0, 2)
        pos = size = f.tell()
        tail, done = b"", False
        while pos > 0 and size - pos < MAX_SCAN and not done:
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
                if is_queued(entry) and queued < MAX_QUEUED:
                    queued += 1
                    continue
                if is_turn_start(entry) or is_queued(entry):
                    done = True
                    break
                if not entry.get("isSidechain"):
                    entries.append(entry)
    entries.reverse()
    return entries


def turn_tool_uses(entries):
    """The turn's tool_use blocks in order, each with "failed" set when its result was an error."""
    uses, failed = [], set()
    for entry in entries:
        content = (entry.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if entry.get("type") == "assistant" and block.get("type") == "tool_use":
                uses.append(block)
            elif block.get("type") == "tool_result" and block.get("is_error"):
                failed.add(block.get("tool_use_id"))
    return [dict(u, failed=u.get("id") in failed) for u in uses]


def is_work(use):
    name = use.get("name")
    if name not in WORK_TOOLS:
        return False
    if name in {"Bash", "PowerShell"}:
        cmd = (use.get("input") or {}).get("command") or ""
        return not SELF_CALL.search(cmd) and not (name == "Bash" and reads_only(cmd))
    return True


def is_board_write(use, board):
    if use.get("name") != "ArtifactData":
        return False
    inp = use.get("input") or {}
    return inp.get("action") in BOARD_WRITES and artifact_id(inp.get("url")) == board and \
        not use.get("failed")


def needs_nudge(uses, board):
    last_work = last_write = -1
    for i, use in enumerate(uses):
        if is_work(use):
            last_work = i
        elif is_board_write(use, board):
            last_write = i
    return last_work >= 0 and last_write < last_work


def nudge(payload):
    if not isinstance(payload, dict) or payload.get("stop_hook_active"):
        return None
    mark = read_marker(payload.get("session_id"))
    transcript = payload.get("transcript_path")
    if not mark or not transcript or not os.path.isfile(transcript):
        return None
    if not needs_nudge(turn_tool_uses(turn_entries(transcript)), artifact_id(mark["url"])):
        return None
    return {"decision": "block", "reason": REASON.format(url=mark["url"])}


def main(argv):
    ap = argparse.ArgumentParser(prog="board.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("arm")
    a.add_argument("--session", required=True)
    a.add_argument("--url", required=True)
    a.add_argument("--plan", default="")
    d = sub.add_parser("disarm")
    d.add_argument("--session", required=True)
    sub.add_parser("nudge")
    args = ap.parse_args(argv)

    if args.cmd == "nudge":
        try:  # bytes, so a non-UTF-8 locale cannot break the payload or the reply
            payload = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
        except ValueError:
            return 0
        try:
            out = nudge(payload)
        except OSError:
            return 0  # an unreadable transcript is no reason to hold the model back
        if out:
            sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8") + b"\n")
        return 0

    path = marker(args.session)
    if not path or "$" in args.session:
        print("board.py: no session id — was ${CLAUDE_SESSION_ID} substituted?", file=sys.stderr)
        return 2
    if args.cmd == "disarm":
        if os.path.isfile(path):
            os.remove(path)
        print("disarmed")
        return 0
    if not artifact_id(args.url):
        print("board.py: --url is not a claude.ai artifact link: " + args.url, file=sys.stderr)
        return 2
    os.makedirs(BOARDS, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"url": args.url, "plan": args.plan,
                   "armedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, f)
    print("armed " + path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
