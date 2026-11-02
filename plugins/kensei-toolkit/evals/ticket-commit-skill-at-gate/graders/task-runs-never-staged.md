---
type: tool_used
tool: Bash
input_match: '(?:"command"\s*:\s*"|&&|\|\||[;|(]|\\n)\s*(?:(?:env|command)\s+|[A-Za-z_]\w*=[^\s"\\]*\s+)*git(?:\s+-[^\s"\\]+(?:\s+[^-\s"\\&;|][^\s"\\&;|]*)?)*\s+add\b(?:[^"\\&;|]|\\[^n])*?(?:task-runs|LOCAL-17/|\s-[A-Za-z]*A[A-Za-z]*\b|\s--all\b|\s(?:\./?|:/)(?=\s|"|\\|$))'
min: 0
max: 0
---

No `git add` command (one command of a chain) stages the run dir or uses `-A` (alone or among other short options) / `--all` / `.` / `./` / `:/`: staging is by whitelist.
