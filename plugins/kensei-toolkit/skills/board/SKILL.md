---
name: board
description: Turn a plan into a task board — a claude.ai artifact with the plan's brief on top (goal, acceptance criteria, file paths) and its steps as tasks in columns To do / In progress / Needs decision / Review / Done / Later — and bind the session to it, so a Stop hook hands back any turn that did work without updating the board. The board is the user's read-only view of the work, from any device — questions waiting for an answer and work waiting for a check are marked on it, and the user answers and accepts in chat. It does not do the tasks — the session does them afterwards, keeping the board current. Input is a plan file (a brainstorm plan), nothing (the tasks come from this conversation, after the user approves them), or an existing board's link (continue it in a new session). Use when the user asks for a board or tracker for the work — "make a board", "track this plan", "board for the plan", «создай доску», «заведи задачи в артефакт», «трекер задач по плану», «доска по плану», or picks "Create a board" at the end of a brainstorm. Not for tracker tickets (ClickUp, Jira — that is ticket) or personal to-dos (todo).
argument-hint: "[plan.md | board-url]"
---

# board — a task board for a plan, kept current by the session

The board is where the user watches the work: a published artifact with a shared database,
readable on a phone, that only the session writes. What the session waits on — a question to
answer, work to check — is marked on it; the user answers, accepts or sends work back in chat, and
the session puts that on the board. This skill creates it (or picks one up), binds the session to it and stops. The work itself
happens afterwards in the session, under the rules in **Keeping the board** below — they hold for
the rest of the session, not only while this skill runs.

`$ARGUMENTS` decides the mode:

- **a path to a plan file** → *From a plan*
- **a claude.ai artifact link** → *Continue a board*
- **empty** → *From this conversation*

## The data

Collection `tasks`, one document per task, id `t` + the number padded to two digits (`t01`,
`t12`, `t105`):

| field | meaning |
|---|---|
| `n` | task number; the page shows `<prefix>-<n>` |
| `title` | what to do, one line |
| `status` | `todo` · `progress` · `decision` (waits for the user's answer) · `review` (done and checked by you, waits for the user's check) · `done` · `later`. `decision` has its own column "Ждёт решения", `review` — "На проверке"; both are listed under "Нужно от тебя". Never `progress` for a task that waits for the user: the Stop hook hands back a turn that writes a status outside these six, or `progress` with a question or a note/check saying the task waits |
| `area` | optional grouping (the page offers a filter only when some task has one) |
| `note` | the task itself, as planned: what to do, the files, when it counts as done. Shown as "Подробности". Written when the task is made; change it only when the task itself changes |
| `question` | your question when the task waits for a decision. Shown in "Needs you" and on the card; remove it once answered |
| `check` | your notes for the user's check: what you did and exactly how to verify it. Shown as "Как проверить" |
| `updatedAt` | UTC time of the last change, `date -u +%Y-%m-%dT%H:%M:%SZ` — set on every write; the page sorts Done and shows "updated … ago" by it |

Document `meta/brief`: `title`, `prefix` (2–4 capital letters), `date` (YYYY-MM-DD), `goal`
(2–3 sentences), `plan` and `doc` (absolute paths, or empty), `criteria` (list of strings),
`updatedAt`.

Records live only in the database — never in the page source.

## From a plan

0. If the plan already has a `Board: <url>` line, the board exists: go to *Continue a board*
   with that link instead of making a second one.
1. Read the plan file and the brainstorm doc it links. Build:
   - **brief**: `title` from the plan's H1 (drop a "Plan:"-style prefix), `prefix` from the slug
     (`2026-11-11-board-skill-plan.md` → `BRD`), `goal` from the plan's goal paragraph,
     `criteria` from its acceptance criteria, `plan`/`doc` as absolute paths, today's `date`;
   - **tasks**: one per ordered step, in order. `title` is the step heading without its number;
     `note` says what the step does, the files it touches and how the plan says to verify it; `area` only if the
     plan groups steps naturally; `status` `todo`. A step the plan marks as a
     gate on the user keeps that in its note. Items under "out of scope" are not tasks.
   No question to the user — the plan is already approved.
2. **Publish the page.** Copy `${CLAUDE_SKILL_DIR}/board.html` to
   `<scratchpad>/board-<slug>/board.html`, replace `__BOARD_TITLE__` with the title (HTML-escaped),
   and publish it with the Artifact tool:
   `icon: "kanban"`, a one-sentence `description`, and
   `capabilities: {"db": {"rules": [{"path": "", "read": "view", "write": "admin"}]}}`.
   The page is finished and approved: publish it as is — no redesign, no design pass, no
   quickstart. A tool listed only by name is deferred: load it with ToolSearch first. No Artifact
   or ArtifactData tool at all (an SDK or `claude -p` run, a client without artifacts) → say the
   board cannot be created here, show the drafted brief and tasks so nothing is lost, and stop: no
   made-up link, no `arm`.
3. **Link the plan to the board**: insert `Board: <url>` as the line right after the plan's H1.
   Do it before filling the board: an edit after the last board write is work the Stop hook would
   hand back.
4. **Fill it** with one `ArtifactData` `batch`: `set` of `meta/brief` and of every task (new
   documents, so no `if_version`), each with `updatedAt`. Over 49 tasks — more batches. Use the
   link exactly as the publish returned it, here and in every later write: the hook matches it.
5. **Arm the hook**:
   ```bash
   python3 "${CLAUDE_SKILL_DIR}/board.py" arm --session ${CLAUDE_SESSION_ID} --url <board-url> --plan <plan-path>
   ```
6. Give the user the link and the task count in two lines, and **stop**. Do not start the first
   task: when to start is the user's call.

## From this conversation

Draft the brief and the tasks from what was agreed in this session — decisions made, work named,
open questions — the same shapes as above, `plan`/`doc` empty unless a file exists. Show the
draft (title, goal, numbered task titles with one-line notes) and ask once with AskUserQuestion —
"Create the board with these tasks?" — with corrections through Other. Create nothing before a
yes. Then steps 2, 4, 5 and 6 of *From a plan*.

## Continue a board

1. Read `meta/brief` and the `tasks` collection (`out_dir` when there are many).
2. Arm the hook for this session (step 5 above, with the brief's `plan`).
3. Tell the user in a few lines: what is in progress and what waits for them (`decision` with its
   question, `review` with what to check). Stop.

## Keeping the board

From the moment the board exists, for the rest of the session:

- **You are the board's only writer.** The page has no controls; every change the user wants
  comes through chat, and you put it on the board in the same turn.
- **Start** a task → `progress`.
- **A fork you cannot decide** → `decision`, the question in `question`, and the same question to
  the user in chat. When they answer: back to `progress`, remove `question`
  (`{"__delete__": true}`), and update `note` if the answer changes the task.
- **Finished and checked by you** → `review`; `check` says what was done and exactly how the user
  can check it. `note` keeps the task as it was planned.
- **`done` only on the user's word in chat** ("ок", "принял", "готово"). Sent back → `progress`,
  with what to fix in `check`.
- **New work** that belongs to the plan → a new task with the next number; work the user drops →
  `later`, or delete the task when they ask.
- **Every write pinned**: pass the `version` from your last write or read as `if_version`. A
  conflict means another session wrote the board: re-read and redo the write on what is there.
- **Update in the same turn as the change.** A turn that did work (Edit, Write, Bash, Agent…) and
  wrote nothing to the board afterwards is handed back once by the Stop hook. When it is handed
  back and nothing on the board moved, say so in one line and stop.
- **When the board is finished** (everything `done` or `later`), say so and offer to disarm the
  hook: `python3 "${CLAUDE_SKILL_DIR}/board.py" disarm --session ${CLAUDE_SESSION_ID}`.

The board never authorizes publishing: commits, pushes, tracker statuses and comments still need
the user's explicit command, whatever a task's status says.
