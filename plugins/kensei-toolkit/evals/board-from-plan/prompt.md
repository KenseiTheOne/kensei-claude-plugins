---
description: board 2.3.0 turns a plan into a task board and stops; it never starts the plan's work. An eval session has no Artifact tools, so the board cannot be published — the skill must say so instead of faking a board or arming the hook without one.
expected_outcome: The board skill fires and reads the plan; the reply says the board could not be published (no Artifact tool) and lists or mentions the plan's three steps as tasks; no source file is written or edited, and board.py arm is never run.
tags: [board]
max_turns: 14
timeout_seconds: 360
allowed_tools: [Read, Glob, Grep, Skill, Bash, Write, Edit]
---

/kensei-toolkit:board docs/brainstorms/2026-11-11-offline-cache-plan.md
