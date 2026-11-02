---
type: regex
target:
  source: file
  path: .git/index
pattern: 'task-runs|LOCAL-17/|RUN\.md|REPORT\.md|0[0-4]-[a-z-]+\.(?:md|patch)'
match: not_contains
---

The index lists every path of the committed tree (plus anything left staged). Run notes —
RUN.md, REPORT.md, 00-ticket … 04-review — must be in neither.
