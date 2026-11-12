#!/usr/bin/env bash
# Scaffold: a small repo with a brainstorm plan of three steps, nothing implemented yet.
set -euo pipefail
git init -q -b main .
git config user.name "eval"
git config user.email "eval@example.invalid"
mkdir -p src docs/brainstorms
printf 'def fetch(url):\n    raise NotImplementedError\n' > src/client.py
cat > docs/brainstorms/2026-11-11-offline-cache-plan.md <<'PLAN'
# Plan: offline cache for the CLI

Brainstorm doc: `docs/brainstorms/2026-11-11-offline-cache.md`.

**Goal.** Cache API responses on disk so the CLI works without a network.

## Steps

### 1. Cache store
- `src/cache.py`: get/put keyed by URL, JSON files under `~/.cache/cli`.
- **Done when** `python3 -m pytest tests/test_cache.py` passes.

### 2. Use the cache in fetch
- `src/client.py`: read from the cache when offline, write after a successful fetch.
- **Done when** `python3 -m pytest tests/test_client.py` passes.

### 3. README section
- `README.md`: how the cache works and how to clear it.
- **Done when** the section exists.

## Acceptance criteria
- The CLI answers from the cache with the network off.
- `cli cache clear` empties it.

## Out of scope
- Cache expiry.
PLAN
git add -A
git commit -q -m "Plan for the offline cache"
