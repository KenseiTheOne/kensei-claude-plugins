---
type: tool_used
tool: Bash
input_match: 'git(?:\s+-[^\s"\\]+(?:\s+[^-\s"\\&;|][^\s"\\&;|]*)?)*\s+(?:diff\s+(?:[^"\\&;|]*\s)?--quiet|write-tree)\b'
min: 1
---

The gate's own checks ran: the snapshot rebuilt (`write-tree`, 12.3.1) or the commit verified
against the reviewed tree (`git diff --quiet`, 12.3.8).
