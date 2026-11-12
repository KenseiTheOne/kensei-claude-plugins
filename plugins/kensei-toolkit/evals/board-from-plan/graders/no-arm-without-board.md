---
type: tool_used
tool: Bash
input_match: 'board\.py\S{0,3}\s+arm'
min: 0
max: 0
---

No board was published, so there is no URL to bind the session to; arming with a made-up link
would make the Stop hook nag about a board that does not exist. `\S{0,3}` covers the closing
quote around the script path, escaped or not.
