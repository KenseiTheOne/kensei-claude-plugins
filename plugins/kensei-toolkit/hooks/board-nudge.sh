#!/bin/sh
# board nudge (plugin Stop hook).
#
# In a session bound to a task board by /kensei-toolkit:board, a turn that did work without
# writing to the board is handed back once to bring the board up to date.
# skills/board/board.py decides; this wrapper only keeps Python from starting for the stops it can
# never concern, because the hook fires at the end of every turn of every session:
#   - a stop already handed back once (stop_hook_active) — never twice in a row;
#   - a session with no board (no marker in <boards dir>/<session_id>.json; the boards dir is
#     $KENSEI_TASK_BOARDS_DIR or ~/.claude/task-boards, resolved as board.py's BOARDS);
#   - a plugin copy without board.py.
# Everything else goes to board.py nudge, whose JSON answer is passed through unchanged.

input=$(cat)
printf '%s' "$input" | grep -q '"stop_hook_active"[[:space:]]*:[[:space:]]*true' && exit 0
board="$(dirname "$0")/../skills/board/board.py"
[ -f "$board" ] || exit 0
# the first "session_id" is the payload's own
session=$(printf '%s' "$input" |
  grep -o '"session_id"[[:space:]]*:[[:space:]]*"[A-Za-z0-9_-]*"' | head -n 1 |
  sed 's/.*"\([A-Za-z0-9_-]*\)"$/\1/')
[ -n "$session" ] || exit 0
boards=${KENSEI_TASK_BOARDS_DIR:-$HOME/.claude/task-boards}
case $boards in
  "~" | "~/"*) boards="$HOME${boards#"~"}" ;;
  "~"* | [!/]*) boards= ;;  # ~user/… or a relative dir: leave the lookup to board.py
esac
[ -z "$boards" ] || [ -f "$boards/$session.json" ] || exit 0
printf '%s' "$input" | python3 "$board" nudge
