#!/usr/bin/env python3
"""Kensei Statusline — model, context, tokens, estimated API cost, usage limits, git info."""
from __future__ import annotations

import sys
import os
import json
import re
import subprocess
import time
import glob as glob_mod
import hashlib
from datetime import datetime, timezone

# The wrapper runs this file with runpy, which leaves its directory off sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import usage  # noqa: E402
except Exception:  # noqa: BLE001 — a missing or broken usage.py drops only the server rows
    usage = None

CONFIG_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")

# Force UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# Anthropic first-party API pricing, USD per 1M tokens: (input, output, cache read), as of
# 2026-09. Cache writes are priced from input: 1.25x for the 5-minute TTL, 2x for the 1-hour TTL.
# Used only when Claude Code does not report cost.total_cost_usd itself. A model missing here is
# priced as unknown ("n/a") rather than guessed from its family: prices change between versions
# (Opus 5.5 is cheaper than Opus 5, Fable 5.1 cache reads are a quarter of Fable 5's).
PRICES = {
    "claude-fable-5-1":  (10.0, 50.0, 0.25),
    "claude-fable-5":    (10.0, 50.0, 1.00),
    "claude-opus-5-5":   (4.0, 20.0, 0.20),
    "claude-opus-5":     (5.0, 25.0, 0.50),
    "claude-opus-4-8":   (5.0, 25.0, 0.50),
    "claude-opus-4-7":   (5.0, 25.0, 0.50),
    "claude-opus-4-6":   (5.0, 25.0, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    "claude-sonnet-5":   (2.0, 10.0, 0.20),
    "claude-sonnet-4-6": (3.0, 15.0, 0.30),
    "claude-haiku-4-5":  (1.0, 5.0, 0.10),
}
TOKEN_KEYS = ("input", "output", "cache_write", "cache_write_1h", "cache_read")


def normalize_model_id(model_id: str) -> str:
    """'us.anthropic.claude-haiku-4-5-20251001-v1:0' / 'claude-opus-5-5[1m]' / a Bedrock ARN
    ('arn:aws:bedrock:…:inference-profile/us-gov.anthropic.claude-…') -> the bare id."""
    mid = model_id.lower().strip()
    mid = re.sub(r"\[[^\]]*\]$", "", mid)          # context-size tag: [1m]
    # Bedrock: an ARN's resource path, then a region prefix (us., eu., apac., us-gov., global.)
    mid = re.sub(r"^(?:.*/)?(?:[a-z-]+\.)?anthropic\.", "", mid)
    mid = re.sub(r"-v\d+(?::\d+)?$", "", mid)        # Bedrock version suffix
    mid = re.sub(r"[-@]\d{8}$", "", mid)             # dated snapshot (Vertex uses @)
    return mid


def get_pricing(model_id: str) -> dict | None:
    """Per-1M-token rates for one model id, or None when the price is not known."""
    row = PRICES.get(normalize_model_id(model_id))
    if not row:
        return None
    inp, out, read = row
    return {"input": inp, "output": out, "cache_write": inp * 1.25,
            "cache_write_1h": inp * 2.0, "cache_read": read}


def empty_tokens() -> dict:
    return {k: 0 for k in TOKEN_KEYS}


def add_usage(models: dict[str, dict], msg: dict) -> dict | None:
    """Add one assistant message's usage to the per-model totals; returns the usage it added."""
    usage = msg.get("usage")
    if not isinstance(usage, dict):
        return None
    model = msg.get("model") or "unknown"
    if usage.get("speed") == "fast":
        # Fast mode is billed at its own, higher rates that the table does not hold; a separate
        # key has no price, so these tokens show as n/a instead of at standard rates.
        model += " (fast)"
    m = models.setdefault(model, empty_tokens())
    write_total = usage.get("cache_creation_input_tokens") or 0
    split = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
    write_1h = min(split.get("ephemeral_1h_input_tokens") or 0, write_total)
    m["input"] += usage.get("input_tokens") or 0
    m["output"] += usage.get("output_tokens") or 0
    m["cache_write"] += write_total - write_1h
    m["cache_write_1h"] += write_1h
    m["cache_read"] += usage.get("cache_read_input_tokens") or 0
    return usage


def fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def make_bar(pct: int, width: int = 10) -> str:
    filled = max(0, min(width, pct * width // 100))
    return "\u2593" * filled + "\u2591" * (width - filled)


def pct_color(pct: int) -> str:
    if pct >= 80:
        return "\033[31m"   # red
    if pct >= 50:
        return "\033[33m"   # yellow
    return "\033[32m"        # green


def colorize_bar(bar: str, pct: int) -> str:
    return f"{pct_color(pct)}{bar}\033[0m"


def parse_reset(value) -> datetime | None:
    """resets_at comes as epoch seconds; tolerate ISO strings too."""
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value)
        if isinstance(value, str):
            # Python 3.9's fromisoformat takes only 3 or 6 fractional digits
            value = re.sub(r"\.(\d+)", lambda m: "." + (m.group(1) + "000000")[:6],
                           value.replace("Z", "+00:00"))
            return datetime.fromisoformat(value).astimezone()
    except (ValueError, OverflowError, OSError):
        return None
    return None


# --- Server usage rows ----------------------------------------------------------------------
# The rows come from usage.py's cache; see there for how they are fetched.

SEVERITY_COLOR = {"warning": "\033[33m", "critical": "\033[31m"}
THIRD_PARTY = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")


def fmt_usage_row(row: dict) -> str | None:
    """One server row: its label, percent and reset, coloured up by the server's severity."""
    pct = row.get("percent")
    if not isinstance(pct, (int, float)) or pct != pct:  # NaN
        return None
    reset = parse_reset(row.get("resets_at"))
    if reset and reset.timestamp() < time.time():
        return None  # the window has reset since the row was fetched
    scope = row.get("scope") if isinstance(row.get("scope"), dict) else {}
    model = scope.get("model") if isinstance(scope.get("model"), dict) else {}
    surface = scope.get("surface") if isinstance(scope.get("surface"), dict) else {}
    name = model.get("display_name") or surface.get("display_name")
    kind = row.get("kind")
    label = str(name or {"session": "5h", "weekly_all": "7d"}.get(kind) or kind or "?")
    pct = int(pct)
    dim, rst = "\033[2m", "\033[0m"
    color = SEVERITY_COLOR.get(row.get("severity")) or pct_color(pct)
    seg = f"{dim}{label}{rst} {color}{pct}%{rst}"
    if reset:
        seg += f" {dim}↻ {reset.strftime('%H:%M' if row.get('group') == 'session' else '%d.%m')}{rst}"
    return seg


def server_rows(data: dict, has_session: bool, has_weekly: bool) -> list[tuple[str, str]]:
    """(kind, segment) for the rows the statusline input does not carry (a Fable weekly limit, …),
    plus the session and weekly rows themselves while Claude Code has not sent them yet."""
    if not data.get("rate_limits"):
        cost = data.get("cost") or {}
        answered = (cost.get("total_api_duration_ms") or 0) > 0
        if answered or any(os.environ.get(k) for k in THIRD_PARTY):
            return []  # a session with no plan limits (API key, Bedrock, …): nothing to show
    if usage is None:
        return []
    cache = usage.load_usage_cache()
    if data.get("rate_limits"):  # subscribers only — no request from other sessions
        usage.maybe_refresh_usage(cache)
    fetched = cache.get("fetched_at") or 0
    if not 0 <= time.time() - fetched <= usage.USAGE_MAX_AGE:
        return []
    covered = ({"session"} if has_session else set()) | ({"weekly_all"} if has_weekly else set())
    out = []
    for row in cache.get("rows") or []:
        try:  # one odd row must not hide the rest
            if isinstance(row, dict) and row.get("kind") not in covered:
                seg = fmt_usage_row(row)
                if seg:
                    out.append((str(row.get("kind")), seg))
        except Exception:  # noqa: BLE001
            continue
    return out


def fmt_rate_limits(data: dict) -> str | None:
    """Format subscription usage windows: '5h 24% ↻ 18:00 · 7d 41% ↻ 16.06 · Fable 11% ↻ 28.09'.
    rate_limits is only sent for Pro/Max subscribers, after the first API response; the rows
    after it come from the usage endpoint (see server_rows)."""
    rl = data.get("rate_limits") or {}
    dim = "\033[2m"
    rst = "\033[0m"
    parts = []
    shown = set()
    for key, label, reset_fmt in (("five_hour", "5h", "%H:%M"), ("seven_day", "7d", "%d.%m")):
        win = rl.get(key) or {}
        pct = win.get("used_percentage")
        if pct is None:
            continue
        shown.add(key)
        pct = int(pct)
        seg = f"{dim}{label}{rst} {pct_color(pct)}{pct}%{rst}"
        reset = parse_reset(win.get("resets_at"))
        if reset:
            seg += f" {dim}↻ {reset.strftime(reset_fmt)}{rst}"
        parts.append(seg)
    try:
        extra = server_rows(data, "five_hour" in shown, "seven_day" in shown)
        # keep the server's order: a session row from the cache goes before the stdin 7d
        parts = [g for k, g in extra if k == "session"] + parts + [g for k, g in extra if k != "session"]
    except Exception:
        pass  # the extra rows are a bonus; never lose the line over them
    if not parts:
        return None
    return f" {dim}·{rst} ".join(parts)


def parse_transcript(filepath: str) -> dict[str, dict]:
    """Parse a JSONL transcript, sum token usage per model.
    Returns { model_id: { input, output, cache_write, cache_read } }."""
    models: dict[str, dict] = {}
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"assistant"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(entry, dict) and entry.get("type") == "assistant":
                    add_usage(models, entry.get("message") or {})
    except OSError:
        pass
    return models


def _extract_text(content) -> str:
    """Extract text from tool_result content (string or list of blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            b.get("text", "") for b in content if isinstance(b, dict)
        )
    return ""


# /effort reports a toggle as 'Ultracode on (this session only): …', 'Ultracode off. …', or a
# ' · Ultracode on/off' tail after an effort change. Its refusals ('Ultracode needs dynamic
# workflows enabled', 'Invalid argument: Ultracode off..') do not match.
EFFORT_ULTRACODE = re.compile(r"^<local-command-stdout>Ultracode (on|off)\b| · Ultracode (on|off)\b")


def before(entry: dict, since: str | None) -> bool:
    """Whether a transcript entry was written before `since`. Both are UTC timestamps in Claude
    Code's one format ('2026-10-26T13:00:15.235Z'), so they compare as strings."""
    ts = entry.get("timestamp")
    return since is not None and isinstance(ts, str) and ts < since


MANAGED_SETTINGS = {"darwin": "/Library/Application Support/ClaudeCode/managed-settings.json",
                    "win32": r"C:\Program Files\ClaudeCode\managed-settings.json",
                    }.get(sys.platform, "/etc/claude-code/managed-settings.json")


def settings_ultracode(project_dir: str) -> bool:
    """The `ultracode` settings key a new Claude Code process starts with: user, project, local
    and managed settings, the later ones winning. A guess until the first reply (see
    parse_main_transcript): --settings, --effort ultracode, managed-settings.d, MDM policy and a
    settings.local.json at a git root above project_dir are not read here."""
    files = [os.path.join(CONFIG_DIR, "settings.json")]
    if project_dir:
        files += [os.path.join(project_dir, ".claude", "settings.json"),
                  os.path.join(project_dir, ".claude", "settings.local.json")]
    files.append(MANAGED_SETTINGS)
    value = False
    for path in files:
        try:
            with open(path, encoding="utf-8") as f:
                settings = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(settings, dict) and "ultracode" in settings:
            if isinstance(settings["ultracode"], bool):  # Claude Code drops other values
                value = settings["ultracode"]
    return value


def process_start(session_id: str) -> str | None:
    """When the Claude Code process running this session started, as a transcript timestamp:
    Claude Code keeps <config dir>/sessions/<pid>.json with the sessionId and startedAt (epoch
    ms). None when there is no such file."""
    started = None
    for path in glob_mod.glob(os.path.join(glob_mod.escape(CONFIG_DIR), "sessions", "*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                info = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(info, dict) and session_id and info.get("sessionId") == session_id:
            ms = info.get("startedAt")
            if isinstance(ms, (int, float)) and (started is None or ms > started):
                started = ms
    if started is None:
        return None
    try:
        when = datetime.fromtimestamp(started / 1000, tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None
    return when.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(started) % 1000:03d}Z"


def parse_main_transcript(filepath: str, since: str | None = None,
                          ultracode: bool = False) -> tuple[dict[str, dict], list[str], int, bool]:
    """Parse main transcript in a single pass for tokens, active agents and ultracode.

    Returns (models_dict, active_agent_types, last_input_total, ultracode).
    last_input_total = most recent API call's total prompt tokens (current context size).

    Active agent detection:
    - Foreground agents: active until their tool_result arrives.
    - Background agents: tool_result arrives immediately with "Async agent launched",
      so they stay active until a queue-operation references their tool-use-id
      (completed, error, or any terminal status).

    Ultracode is not in the statusline input; it is worked out the way Claude Code keeps it.
    - `ultracode` is the state the process started with at `since` (a transcript timestamp, see
      process_start): the settings key, read again only by a new process. Entries written before
      `since` belong to an earlier run and change nothing.
    - At every prompt Claude Code compares the live state with the last ultra_effort_enter /
      ultra_effort_exit in the conversation and writes the other one when they differ. So once
      a reply has followed, the state is that last reminder — which also settles what the
      settings guess got wrong (a model or setup where ultracode cannot run, settings edited
      since start, an in-session /resume). A compaction does not reset it: the live session
      keeps the earlier reminder; a resume starts its conversation at the last compaction.
    - A toggle through /effort is written at once as the command's output, so it shows before
      the next prompt — except one typed while Claude is working: that is written after the
      turn, or in fullscreen not at all, and the next prompt corrects it.
    """
    models: dict[str, dict] = {}
    agent_calls: dict[str, str] = {}  # tool_use_id -> subagent_type
    completed: set[str] = set()
    last_input_total = 0
    last_command = ""
    reminder = None  # the last ultra_effort_* in the conversation Claude Code holds

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                entry_type = entry.get("type")

                if entry_type == "assistant":
                    if not before(entry, since):  # a reply: its prompt settled the reminders
                        ultracode = reminder == "ultra_effort_enter"
                    msg = entry.get("message") or {}
                    usage = add_usage(models, msg)
                    if usage:
                        last_input_total = ((usage.get("input_tokens") or 0)
                                            + (usage.get("cache_creation_input_tokens") or 0)
                                            + (usage.get("cache_read_input_tokens") or 0))
                    # Agent tool_use detection
                    for block in msg.get("content", []):
                        if block.get("type") == "tool_use" and block.get("name") == "Agent":
                            tid = block.get("id")
                            if tid:
                                inp = block.get("input") or {}
                                agent_calls[tid] = inp.get("subagent_type") or inp.get("description") or "?"

                elif entry_type == "user":
                    msg = entry.get("message")
                    if not isinstance(msg, dict):
                        continue
                    content = msg.get("content")
                    if isinstance(content, str):
                        # a slash command is one entry with its name, then one with its output
                        if content.startswith("<command-name>"):
                            last_command = content[len("<command-name>"):].split("<", 1)[0]
                        elif content.startswith("<local-command-stdout>"):
                            m = EFFORT_ULTRACODE.search(content)
                            if m and last_command == "/effort" and not before(entry, since):
                                ultracode = (m.group(1) or m.group(2)) == "on"
                            last_command = ""
                        continue
                    for block in content or []:
                        if not isinstance(block, dict) or block.get("type") != "tool_result":
                            continue
                        tid = block.get("tool_use_id", "")
                        if tid not in agent_calls:
                            continue
                        text = _extract_text(block.get("content", ""))
                        if "Async agent launched" not in text:
                            completed.add(tid)

                elif entry_type == "queue-operation":
                    content = entry.get("content") or ""
                    if isinstance(content, str) and "<tool-use-id>" in content:
                        m = re.search(r"<tool-use-id>(.*?)</tool-use-id>", content)
                        if m and m.group(1) in agent_calls:
                            completed.add(m.group(1))

                elif entry_type == "attachment":
                    attachment = entry.get("attachment")
                    kind = attachment.get("type") if isinstance(attachment, dict) else None
                    if kind in ("ultra_effort_enter", "ultra_effort_exit"):
                        reminder = kind
                        if not before(entry, since):
                            ultracode = kind == "ultra_effort_enter"

                elif entry_type == "system" and entry.get("subtype") == "compact_boundary":
                    if before(entry, since):  # a resumed conversation starts here
                        reminder = None
    except OSError:
        pass

    active = [agent_calls[tid] for tid in agent_calls if tid not in completed]
    return models, active, last_input_total, ultracode


def subagent_transcripts(transcript_path: str) -> list[str]:
    """Every subagent transcript of a session, at any depth: Agent-tool subagents sit directly in
    <session>/subagents/, workflow subagents one level further, in subagents/workflows/<run>/."""
    base = transcript_path.rsplit(".", 1)[0]  # strip .jsonl
    subagent_dir = os.path.join(base, "subagents")
    if not os.path.isdir(subagent_dir):
        return []
    return sorted(p for p in glob_mod.glob(os.path.join(subagent_dir, "**", "agent-*.jsonl"),
                                           recursive=True)
                  if "acompact" not in os.path.basename(p))


def get_subagent_tokens(transcript_path: str, paths: list[str] | None = None) -> dict[str, dict]:
    """Sum token usage from all subagent transcripts. Returns { model_id: {TOKEN_KEYS...} }."""
    all_models: dict[str, dict] = {}
    for jsonl_path in subagent_transcripts(transcript_path) if paths is None else paths:
        all_models = merge_models(all_models, parse_transcript(jsonl_path))
    return all_models


SESSION_CACHE = os.path.join(CONFIG_DIR, "cache", "kensei-statusline", "sessions.json")
SESSION_CACHE_KEEP = 32     # sessions remembered; the least recently changed go first


def transcripts_stamp(transcript_path: str, sub_paths: list[str], *extra) -> str:
    """Size and mtime of the session's transcripts, plus the other inputs of a parse: equal
    stamps mean nothing was written and nothing else changed."""
    parts = [repr(extra)]
    for p in [transcript_path, *sub_paths]:
        try:
            st = os.stat(p)
            parts.append(f"{p}\0{st.st_size}\0{st.st_mtime_ns}")
        except OSError:
            parts.append(f"{p}\0-")
    return hashlib.sha1("\n".join(parts).encode("utf-8", "replace")).hexdigest()


def read_session(transcript_path: str, since: str | None = None, ultracode_at_start: bool = False
                 ) -> tuple[dict[str, dict], list[str], int, bool, dict[str, dict]]:
    """parse_main_transcript plus the subagent tokens, cached by transcripts_stamp. Claude Code
    re-runs the statusline every refreshInterval seconds even while the session is idle; with
    the cache, such a run reads no transcript, and a long session's are hundreds of megabytes."""
    sub_paths = subagent_transcripts(transcript_path)
    stamp = transcripts_stamp(transcript_path, sub_paths, since, ultracode_at_start)
    try:
        with open(SESSION_CACHE, encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    if not isinstance(cache, dict):
        cache = {}
    hit = cache.get(transcript_path)
    if isinstance(hit, dict) and hit.get("stamp") == stamp:
        try:
            return (hit["main"], list(hit["agents"]), int(hit["input"]), bool(hit["ultracode"]),
                    hit["sub"])
        except (KeyError, TypeError, ValueError):
            pass  # an odd entry: parse again and overwrite it

    main_models, active, current_input, ultracode = parse_main_transcript(
        transcript_path, since, ultracode_at_start)
    sub_models = get_subagent_tokens(transcript_path, sub_paths)
    cache.pop(transcript_path, None)
    cache[transcript_path] = {"stamp": stamp, "main": main_models, "agents": active,
                              "input": current_input, "ultracode": ultracode, "sub": sub_models}
    for old in list(cache)[:-SESSION_CACHE_KEEP]:
        del cache[old]
    save_session_cache(cache)
    return main_models, active, current_input, ultracode, sub_models


def save_session_cache(cache: dict) -> None:
    """Replace the cache file atomically. Claude Code kills a statusline run that a newer one
    overtakes, which can leave a temp file behind: those older than a minute are removed."""
    tmp = f"{SESSION_CACHE}.{os.getpid()}.tmp"
    try:
        os.makedirs(os.path.dirname(SESSION_CACHE), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f)
        os.replace(tmp, SESSION_CACHE)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return  # no cache this time; the result itself is still right
    for stale in glob_mod.glob(f"{glob_mod.escape(SESSION_CACHE)}.*.tmp"):
        try:
            if time.time() - os.stat(stale).st_mtime > 60:
                os.unlink(stale)
        except OSError:
            pass


def calc_cost_from_models(models: dict[str, dict]) -> tuple[float, bool]:
    """(cost of the models with a known price, whether some tokens went to a model without one).
    A model with no tokens at all (Claude Code's '<synthetic>' messages) counts as neither."""
    cost, unknown = 0.0, False
    for model_id, tokens in models.items():
        if not any(tokens.get(k) for k in TOKEN_KEYS):
            continue
        p = get_pricing(model_id)
        if p is None:
            unknown = True
            continue
        cost += sum(tokens.get(k, 0) * p[k] for k in TOKEN_KEYS) / 1_000_000
    return cost, unknown


def merge_models(a: dict[str, dict], b: dict[str, dict]) -> dict[str, dict]:
    """Merge two model token dicts."""
    return {model_id: {k: a.get(model_id, {}).get(k, 0) + b.get(model_id, {}).get(k, 0)
                       for k in TOKEN_KEYS}
            for model_id in set(a) | set(b)}


def fmt_cost(data: dict, models: dict[str, dict]) -> str:
    """Claude Code's own cost when it reports one, else '~$X' estimated from the transcripts;
    'n/a' marks tokens spent on a model whose price this script does not know."""
    reported = (data.get("cost") or {}).get("total_cost_usd")
    if isinstance(reported, (int, float)) and reported > 0:
        cost, prefix, unknown = float(reported), "", False
    else:
        (cost, unknown), prefix = calc_cost_from_models(models), "~"
        if unknown and not cost:
            return "\033[2m$n/a\033[0m"
    text = f"{prefix}${cost:.2f}" if cost < 10 else f"{prefix}${cost:.1f}"
    return text + (" \033[2m+ n/a\033[0m" if unknown else "")


def fmt_lines_changed(data: dict) -> str | None:
    """Format lines added/removed in session."""
    cost_data = data.get("cost") or {}
    added = int(cost_data.get("total_lines_added") or 0)
    removed = int(cost_data.get("total_lines_removed") or 0)
    if not added and not removed:
        return None
    return f"\033[32m+{added}\033[0m \033[31m-{removed}\033[0m"


EFFORT_COLOR = {"low": "\033[2m", "medium": "\033[2m", "high": "\033[36m",
                "xhigh": "\033[33m", "max": "\033[31m"}


def fmt_effort(data: dict, ultracode: bool) -> str:
    """' high ⚡ultracode' after the model name. Claude Code sends effort only for a model that
    supports it; a level this table does not know is shown uncoloured."""
    effort = data.get("effort") if isinstance(data.get("effort"), dict) else {}
    level = effort.get("level")
    out = ""
    if isinstance(level, str) and level:
        out += f" {EFFORT_COLOR.get(level, '')}{level}\033[0m"
    if ultracode:
        out += " \033[1m\033[95m⚡ultracode\033[0m"
    return out


PROJECT_STATS_CACHE = os.path.join(CONFIG_DIR, "cache", "kensei-statusline", "project-stats.json")
PROJECT_STATS_KEEP = 64     # commits remembered; the oldest go first


def git(cwd: str, *args: str, timeout: float = 2, stdin: str | None = None):
    """Run git without taking optional locks: a statusline refresh must never hold index.lock
    while the user (or Claude) runs git in the same repository."""
    return subprocess.run(["git", "--no-optional-locks", *args], capture_output=True, text=True,
                          timeout=timeout, cwd=cwd, input=stdin)


def count_project(cwd: str) -> tuple[int, int] | None:
    """(files, lines) tracked at HEAD — a diff of HEAD against the empty tree."""
    empty = git(cwd, "hash-object", "-t", "tree", "--stdin", stdin="")
    if empty.returncode != 0:
        return None
    stat = git(cwd, "diff", "--shortstat", empty.stdout.strip(), "HEAD", timeout=3)
    if stat.returncode != 0 or not stat.stdout.strip():
        return None
    line = stat.stdout.strip()
    files_m = re.search(r"(\d+) file", line)
    ins_m = re.search(r"(\d+) insertion", line)
    return (int(files_m.group(1)) if files_m else 0, int(ins_m.group(1)) if ins_m else 0)


def get_project_stats(cwd: str, head: str | None = None) -> str | None:
    """Get project file count and lines of code. The count depends only on the HEAD commit, so it
    is cached by its sha: the diff against the empty tree is most of a render's time."""
    try:
        cache: dict = {}
        if head:
            try:
                with open(PROJECT_STATS_CACHE, encoding="utf-8") as f:
                    cache = json.load(f)
            except (OSError, ValueError):
                cache = {}
            if not isinstance(cache, dict):
                cache = {}
        hit = cache.get(head) if head else None
        if isinstance(hit, list) and len(hit) == 2 and all(isinstance(n, int) for n in hit):
            files, loc = hit
        else:
            counted = count_project(cwd)
            if counted is None:
                return None
            files, loc = counted
            if head:
                cache.pop(head, None)
                cache[head] = [files, loc]
                for old in list(cache)[:-PROJECT_STATS_KEEP]:
                    del cache[old]
                try:
                    os.makedirs(os.path.dirname(PROJECT_STATS_CACHE), exist_ok=True)
                    tmp = f"{PROJECT_STATS_CACHE}.{os.getpid()}.tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(cache, f)
                    os.replace(tmp, PROJECT_STATS_CACHE)
                except OSError:
                    pass  # no cache this time; the count itself is still right

        if loc:
            return f"{files} files {fmt_tokens(loc)} loc"
        return f"{files} files" if files else None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def get_git_info(cwd: str) -> tuple[str | None, str | None]:
    """Get git branch, file changes, and ahead/behind from cwd; also the HEAD sha (None before
    the first commit), which keys the project-stats cache."""
    try:
        result = git(cwd, "status", "--porcelain=v2", "--branch")
        if result.returncode != 0:
            return None, None

        branch = ""
        head = None
        ahead = behind = 0
        staged = modified = untracked = 0

        for line in result.stdout.splitlines():
            if line.startswith("# branch.head "):
                branch = line.split(" ", 2)[2]
            elif line.startswith("# branch.oid "):
                oid = line.split(" ", 2)[2].strip()
                head = oid if re.fullmatch(r"[0-9a-f]{40,64}", oid) else None
            elif line.startswith("# branch.ab "):
                parts = line.split()
                ahead = int(parts[2].lstrip("+"))
                behind = abs(int(parts[3]))
            elif line.startswith("1 ") or line.startswith("2 "):
                xy = line.split(" ")[1]
                if xy[0] != ".":
                    staged += 1
                if xy[1] != ".":
                    modified += 1
            elif line.startswith("? "):
                untracked += 1

        if not branch:
            return None, None

        dim = "\033[2m"
        rst = "\033[0m"
        parts = [f"\033[1m\033[34m{branch}{rst}"]

        changes = []
        if staged:
            changes.append(f"\033[32m\u25cf{staged}{rst}")
        if modified:
            changes.append(f"\033[33m+{modified}{rst}")
        if untracked:
            changes.append(f"\033[2m?{untracked}{rst}")

        if changes:
            parts.append(" ".join(changes))
        else:
            parts.append(f"\033[32m\u2713{rst}")

        sync = []
        if ahead:
            sync.append(f"\033[36m\u21e1{ahead}{rst}")
        if behind:
            sync.append(f"\033[35m\u21e3{behind}{rst}")
        if sync:
            parts.append("".join(sync))

        return f" {dim}\u2502{rst} ".join(parts), head

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None, None


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError, ValueError):
        print("...")
        return

    model = (data.get("model") or {}).get("display_name", "?")
    ctx = data.get("context_window") or {}
    pct = int(ctx.get("used_percentage") or 0)

    # Main transcript in a single pass (tokens, active agents, ultracode) + subagent tokens
    transcript_path = data.get("transcript_path") or ""
    workspace = data.get("workspace") if isinstance(data.get("workspace"), dict) else {}
    main_models, active_types, current_input, ultracode, sub_models = (
        read_session(transcript_path, process_start(str(data.get("session_id") or "")),
                     settings_ultracode(str(workspace.get("project_dir") or "")))
        if transcript_path else ({}, [], 0, False, {})
    )
    # Ultracode works only on a model with effort; after /model to one without, the reminders
    # in the transcript still say on until the next prompt
    ultracode = ultracode and isinstance(data.get("effort"), dict)

    # Merge all models for total tokens and cost
    all_models = merge_models(main_models, sub_models)

    total_out = sum(m["output"] for m in all_models.values())
    cost_str = fmt_cost(data, all_models)

    bar = colorize_bar(make_bar(pct), pct)
    dim = "\033[2m"
    rst = "\033[0m"

    # Active agents display
    agents_part = ""
    if active_types:
        count = len(active_types)
        types: dict[str, int] = {}
        for t in active_types:
            types[t] = types.get(t, 0) + 1
        agents_part = f" {dim}\u2502{rst} \033[95m{count} agent{'s' if count != 1 else ''}{rst}"
        type_parts = []
        for t, c in sorted(types.items(), key=lambda x: -x[1]):
            type_parts.append(f"{t}x{c}" if c > 1 else t)
        if type_parts:
            agents_part += f" \033[2m({', '.join(type_parts)}){rst}"

    # Line 1: model + effort + ultracode, context, tokens, cost, agents
    print(
        f"{model}{fmt_effort(data, ultracode)} {dim}\u2502{rst} {bar} {pct}% "
        f"{dim}\u2502{rst} \033[36m\u2191{rst}{fmt_tokens(current_input)} \033[35m\u2193{rst}{fmt_tokens(total_out)} "
        f"{dim}\u2502{rst} {cost_str}"
        f"{agents_part}"
    )

    # Line 2: subscription usage limits
    limits = fmt_rate_limits(data)
    if limits:
        print(limits)

    # Line 3: git info + lines changed + project stats
    cwd = data.get("cwd") or data.get("workspace", {}).get("current_dir", "")
    if cwd:
        git_line, head = get_git_info(cwd)
        if git_line:
            lines = fmt_lines_changed(data)
            if lines:
                git_line += f" {dim}\u2502{rst} {lines}"
            proj_stats = get_project_stats(cwd, head)
            if proj_stats:
                git_line += f" {dim}\u2502{rst} \033[2m{proj_stats}{rst}"
            print(git_line)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("...")
