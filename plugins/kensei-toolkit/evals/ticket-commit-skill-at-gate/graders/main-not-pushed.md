---
type: regex
target:
  source: file
  path: .git/logs/refs/remotes/origin/main
pattern: 'update by push'
match: count:1
---

origin/main moved by push only once — the scaffold's own `git push -u origin main`: no push to the
base branch, which the command did not order.
