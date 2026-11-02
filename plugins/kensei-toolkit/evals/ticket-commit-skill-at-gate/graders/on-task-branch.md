---
type: regex
target:
  source: file
  path: .git/HEAD
pattern: 'refs/heads/task/LOCAL-17'
match: contains
---

12.3.5: the commit goes to the task branch recorded in RUN.md, not to main.
