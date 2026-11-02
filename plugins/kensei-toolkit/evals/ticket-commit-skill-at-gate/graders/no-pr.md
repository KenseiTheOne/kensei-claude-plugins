---
type: tool_used
tool: Bash
input_match: '(?:"command"\s*:\s*"|&&|\|\||[;|(]|\\n)\s*(?:(?:env|command)\s+|[A-Za-z_]\w*=[^\s"\\]*\s+)*gh(?:\s+-[^\s"\\]+(?:\s+[^-\s"\\&;|][^\s"\\&;|]*)?)*\s+pr\s+(?:create|merge)\b'
min: 0
max: 0
---

The command was commit and push: no PR is opened or merged.
