#!/usr/bin/env bash
# Scaffold: a repo that keeps brainstorm docs in docs/brainstorms/, so the skill writes them here.
set -euo pipefail
git init -q -b main .
git config user.name "eval"
git config user.email "eval@example.invalid"
mkdir -p src docs/brainstorms
printf 'def fetch(url):\n    raise NotImplementedError\n' > src/client.py
printf '# Brainstorms\n' > docs/brainstorms/README.md
git add -A
git commit -q -m "Initial CLI"
