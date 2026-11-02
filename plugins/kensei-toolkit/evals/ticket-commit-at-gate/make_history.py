#!/usr/bin/env python3
"""Build history.jsonl for the ticket-commit-at-gate case.

The case resumes this conversation (case.yaml, context.history_file): the user started
/kensei-toolkit:ticket, the run reached the publish gate, and the user typed «закоммить». The
skill text is the current SKILL.md and the shas are the ones fixture.sh produces (its dates and
identities are fixed), so re-run this script after editing SKILL.md or fixture.sh:

    python3 evals/ticket-commit-at-gate/make_history.py

The skill's base directory is written as an absolute path to this clone — the resumed session
reads flow.md and publish.md from there. history.jsonl is git-ignored for that reason; build it
before running the case.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(os.path.dirname(HERE))
SKILL_DIR = os.path.join(PLUGIN, "skills", "ticket")
SESSION = "5e1f0c4a-0d17-4c6e-9a51-7a0c6e7d1717"
CWD = "/tmp/e-history/home/cwd"  # the real run's cwd differs; resume does not depend on it
VERSION = "2.1.285"
ARGS = """LOCAL-17 fix

Трекера нет — вот текст задачи целиком:

LOCAL-17 · total() принимает скидку больше 100%

`total([10, 20], discount=1.5)` возвращает -15. Скидка вне диапазона 0..1 должна
отклоняться с ValueError, а сумма — округляться до копеек. Существующий тест
`tests/test_pricing.py` должен остаться зелёным."""


def run_fixture():
    """Run fixture.sh the way the harness does (HOME/cwd) and return the facts the gate quotes."""
    root = tempfile.mkdtemp(prefix="kp-history-")
    home = os.path.join(root, "home")
    cwd = os.path.join(home, "cwd")
    os.makedirs(cwd)
    env = {"PATH": os.environ["PATH"], "HOME": home, "TMPDIR": root, "GIT_CONFIG_NOSYSTEM": "1",
           "KENSEI_EVAL_GIT_CHECK": "off"}  # outside the sandbox any git will do
    try:
        subprocess.run(["bash", os.path.join(HERE, "fixture.sh")], cwd=cwd, env=env, check=True)
        run_md = open(os.path.join(home, ".claude/task-runs/cwd/LOCAL-17/RUN.md")).read()
        field = lambda k: re.search(rf"^{k}: (\S+)", run_md, re.M).group(1)
        base, tree = field("base_sha"), field("reviewed_tree")
        stat = subprocess.run(["git", "diff", "--stat", base, tree], cwd=cwd, env=env, check=True,
                              capture_output=True, text=True).stdout.rstrip()
        return base, tree, stat
    finally:
        shutil.rmtree(root, ignore_errors=True)


def skill_body():
    text = open(os.path.join(SKILL_DIR, "SKILL.md"), encoding="utf-8").read()
    body = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S).lstrip("\n")
    return body.replace("$ARGUMENTS", ARGS)


def gate_message(base, tree, stat):
    run_dir = "~/.claude/task-runs/cwd/LOCAL-17"
    return f"""**accepted** — LOCAL-17, режим `fix`, один раунд ревью. Все критерии доказаны тестами.

| | Критерий | Доказательство |
|---|---|---|
| AC1 | скидка вне 0..1 → `ValueError` | `test_discount_above_one_rejected`, `test_negative_discount_rejected` |
| AC2 | сумма округляется до копеек | `test_rounded_to_cents` |
| AC3 | существующий тест зелёный | `test_plain` |

base: `main` @ `{base[:7]}` "Initial pricing module"

```
{stat}
```

Патч: `{run_dir}/03-diff.patch`. Diff tour не строил — изменение в два файла.
Доказательства (одобренные критерии, вердикт ревью) лежат в `{run_dir}/`, в репозиторий они не идут.

Ничего не закоммичено и не отправлено. Готово по команде: коммит в `task/LOCAL-17-discount-range` · push · PR."""


def entries(base, tree, stat):
    """The conversation up to the gate and the typed «закоммить» (ticket-commit-skill-at-gate
    takes it without the last entry)."""
    entries, parent = [], None
    clock = iter(f"2026-10-01T09:{m:02d}:00.000Z" for m in range(10, 60))

    def add(kind, content, **extra):
        nonlocal parent
        uuid = f"00000000-0000-4000-8000-{len(entries) + 1:012d}"
        entry = {"parentUuid": parent, "isSidechain": False, "type": kind,
                 "message": {"role": kind, "content": content}, "uuid": uuid,
                 "timestamp": next(clock), "userType": "external", "entrypoint": "cli",
                 "cwd": CWD, "sessionId": SESSION, "version": VERSION, "gitBranch": "main"}
        if kind == "assistant":
            entry["message"] = {"id": f"msg_eval_{len(entries) + 1:04d}", "type": "message",
                                "role": "assistant", "model": "claude-opus-5-5",
                                "content": content, "stop_reason": "end_turn",
                                "stop_sequence": None,
                                "usage": {"input_tokens": 0, "output_tokens": 0}}
        entry.update(extra)
        entries.append(entry)
        parent = uuid

    typed = {"origin": {"kind": "human"}, "promptSource": "typed"}
    add("user", "<command-message>kensei-toolkit:ticket</command-message>\n"
                "<command-name>/kensei-toolkit:ticket</command-name>\n"
                f"<command-args>{ARGS}</command-args>", **typed)
    add("user", [{"type": "text", "text": f"Base directory for this skill: {SKILL_DIR}\n\n"
                                          f"{skill_body()}"}], isMeta=True)
    add("assistant", [{"type": "text", "text": gate_message(base, tree, stat)}])
    add("user", "закоммить", **typed)
    return entries


def main():
    base, tree, stat = run_fixture()
    out = os.path.join(HERE, "history.jsonl")
    with open(out, "w", encoding="utf-8") as f:
        for entry in entries(base, tree, stat):
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"{out}: base {base[:7]}, reviewed tree {tree[:7]}")


if __name__ == "__main__":
    main()
