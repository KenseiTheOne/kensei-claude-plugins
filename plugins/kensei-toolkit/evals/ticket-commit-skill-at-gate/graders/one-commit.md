---
type: regex
target:
  source: file
  path: .git/logs/HEAD
pattern: '\tcommit: '
match: count:1
---

The HEAD reflog has one `commit:` line per new commit; the scaffold's own commit is `commit (initial):`.
