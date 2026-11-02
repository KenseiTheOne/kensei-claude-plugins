---
type: regex
target:
  source: file
  path: .git/logs/HEAD
pattern: '\t(?:commit \(amend\)|commit \(merge\)|merge |rebase|reset: |pull)'
match: not_contains
---

No amend, merge, rebase, reset or pull: the command was one commit.
