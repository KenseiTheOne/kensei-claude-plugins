#!/usr/bin/env python3
"""Install the kensei-statusline: copy the wrapper and point settings.json at it.

    python3 setup.py --dry-run   # print what would change, touch nothing
    python3 setup.py             # do it

Writes <config dir>/scripts/kensei-statusline.py (the config dir is CLAUDE_CONFIG_DIR, or ~/.claude)
and sets statusLine.type / statusLine.command in <config dir>/settings.json, plus
statusLine.refreshInterval = 5 when there is none. Every other field of settings.json, and every
other statusLine field (an existing refreshInterval, padding, ...), is kept. Before
settings.json changes, a copy is saved next to it as settings.json.bak-<timestamp>. A statusLine in
settings.local.json wins over settings.json; setup does not edit that file and only reports it as
local_override_status_line. Prints one JSON object describing what was done.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WRAPPER_SRC = os.path.join(HERE, "..", "..", "scripts", "kensei-statusline-wrapper.py")
CONFIG_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
SETTINGS = os.path.join(CONFIG_DIR, "settings.json")
LOCAL_SETTINGS = os.path.join(CONFIG_DIR, "settings.local.json")
WRAPPER_DST = os.path.join(CONFIG_DIR, "scripts", "kensei-statusline.py")
DISMISS = os.path.join(CONFIG_DIR, ".statusline-no-setup")
# Claude Code re-runs the statusline when effort changes, but not when ultracode is toggled: only
# the periodic refresh picks that up. The statusline caches its transcript reading, so an idle
# refresh is cheap.
DEFAULT_REFRESH_INTERVAL = 5


def command_for(path: str) -> str:
    return f'python3 "{path.replace(os.sep, "/")}"'


def read_settings(path: str) -> dict:
    """The settings object in path ({} when missing or empty). Raises ValueError when it is not
    a JSON object."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        text = f.read()
    settings = json.loads(text) if text.strip() else {}
    if not isinstance(settings, dict):
        raise ValueError(f"{path} is not a JSON object")
    return settings


def local_status_line():
    """statusLine from settings.local.json, which takes precedence over settings.json. Setup
    leaves that file alone, so the caller only reports it. An unreadable file reports nothing."""
    try:
        return read_settings(LOCAL_SETTINGS).get("statusLine")
    except (OSError, ValueError):
        return None


def plan() -> dict:
    """What setup would change. Raises ValueError when settings.json is not a JSON object."""
    settings = read_settings(SETTINGS)
    old = settings.get("statusLine")
    new = dict(old) if isinstance(old, dict) else {}
    new.update(type="command", command=command_for(WRAPPER_DST))
    new.setdefault("refreshInterval", DEFAULT_REFRESH_INTERVAL)
    return {"settings": settings, "old_status_line": old, "new_status_line": new}


def write_settings(settings: dict) -> None:
    """Replace settings.json atomically. A symlinked settings.json (dotfiles) is written through
    to its target so the link stays, and the file keeps its permission bits."""
    real = os.path.realpath(SETTINGS)
    os.makedirs(os.path.dirname(real), exist_ok=True)
    tmp = f"{real}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
            f.write("\n")
        if os.path.exists(real):
            shutil.copymode(real, tmp)
        os.replace(tmp, real)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def run(dry_run: bool) -> dict:
    p = plan()
    report = {
        "config_dir": CONFIG_DIR,
        "settings": SETTINGS,
        "wrapper": WRAPPER_DST,
        "wrapper_replaced": os.path.exists(WRAPPER_DST),
        "old_status_line": p["old_status_line"],
        "new_status_line": p["new_status_line"],
        "local_settings": LOCAL_SETTINGS,
        "local_override_status_line": local_status_line(),
        "backup": None,
        "dismiss_marker_removed": os.path.exists(DISMISS),
        "dry_run": dry_run,
    }
    if dry_run:
        return report

    os.makedirs(os.path.dirname(WRAPPER_DST), exist_ok=True)
    shutil.copyfile(WRAPPER_SRC, WRAPPER_DST)
    os.chmod(WRAPPER_DST, 0o755)

    settings = p["settings"]
    if settings.get("statusLine") != p["new_status_line"]:
        if os.path.exists(SETTINGS):
            backup = f"{SETTINGS}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
            shutil.copy2(SETTINGS, backup)
            report["backup"] = backup
        settings["statusLine"] = p["new_status_line"]
        write_settings(settings)

    if os.path.exists(DISMISS):
        os.unlink(DISMISS)
    return report


def main() -> int:
    try:
        report = run("--dry-run" in sys.argv[1:])
    except (OSError, ValueError) as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        return 1
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
