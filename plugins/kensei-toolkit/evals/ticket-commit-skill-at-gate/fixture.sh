#!/usr/bin/env bash
# Scaffold: the same /ticket run stopped at the publish gate as ticket-commit-at-gate — this case
# differs only in the conversation (history.jsonl ends with a typed /kensei-toolkit:commit).
set -euo pipefail
exec bash "$(cd "$(dirname "$0")" && pwd)/../ticket-commit-at-gate/fixture.sh"
