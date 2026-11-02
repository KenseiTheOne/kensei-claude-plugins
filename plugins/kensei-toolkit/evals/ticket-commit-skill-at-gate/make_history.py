#!/usr/bin/env python3
"""Build history.jsonl for the ticket-commit-skill-at-gate case.

The conversation of ticket-commit-at-gate up to the publish gate, then the user types
/kensei-toolkit:commit (Claude Code records the command and the skill's text after it). Re-run
after editing either SKILL.md or the gate case's fixture.sh or make_history.py:

    python3 evals/ticket-commit-skill-at-gate/make_history.py
"""
import importlib.util
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "gate_history", os.path.join(HERE, "..", "ticket-commit-at-gate", "make_history.py"))
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
COMMIT_DIR = os.path.join(gate.PLUGIN, "skills", "commit")


def commit_body():
    text = open(os.path.join(COMMIT_DIR, "SKILL.md"), encoding="utf-8").read()
    body = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S).lstrip("\n")
    return body.replace("$ARGUMENTS", "").replace("${CLAUDE_SKILL_DIR}", COMMIT_DIR)


def main():
    entries = gate.entries(*gate.run_fixture())[:-1]  # the gate, without its typed «закоммить»
    parent = entries[-1]["uuid"]
    for i, (content, extra) in enumerate([
            ("<command-message>kensei-toolkit:commit</command-message>\n"
             "<command-name>/kensei-toolkit:commit</command-name>\n<command-args></command-args>",
             {"origin": {"kind": "human"}, "promptSource": "typed"}),
            ([{"type": "text", "text": f"Base directory for this skill: {COMMIT_DIR}\n\n"
                                       f"{commit_body()}"}], {"isMeta": True})]):
        uuid = f"00000000-0000-4000-8000-{len(entries) + 1:012d}"
        entry = dict(parentUuid=parent, isSidechain=False, type="user",
                     message={"role": "user", "content": content}, uuid=uuid,
                     timestamp=f"2026-10-01T09:{50 + i:02d}:00.000Z", userType="external",
                     entrypoint="cli", cwd=gate.CWD, sessionId=gate.SESSION, version=gate.VERSION,
                     gitBranch="main", **extra)
        entries.append(entry)
        parent = uuid
    out = os.path.join(HERE, "history.jsonl")
    with open(out, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"{out}: {len(entries)} entries")


if __name__ == "__main__":
    main()
