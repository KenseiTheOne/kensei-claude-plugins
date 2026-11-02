---
description: Resumes the same /ticket run stopped at the publish gate as ticket-commit-at-gate, but the user types /kensei-toolkit:commit there. The commit skill hands over to the ticket's gate — its commit (12.3) and its push (12.4) — and its script, which refuses inside a ticket run, is not run.
expected_outcome: Exactly one commit, on task/LOCAL-17-discount-range, holding src/pricing.py and tests/test_pricing.py only, made by the ticket's own steps (the check against the reviewed tree ran); sessioncommit.py never run; the task branch pushed to origin, main neither moved nor pushed; no PR, no merge, no amend.
tags: [ticket, gate, commit]
max_turns: 30
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Skill, AskUserQuestion, TodoWrite, Bash, Write, Edit]
---

/kensei-toolkit:commit
