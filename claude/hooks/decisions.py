#!/usr/bin/env python3
"""agent-decisions lookup: give Claude only the decisions that matter for what it is touching.

Commands:
    decisions.py context        Hook mode (PostToolUse on Read/Edit/Write). Reads the hook
                                 JSON on stdin and injects the decisions anchored to that file.
    decisions.py show <path>    Print every decision anchored to a file (for Claude or you).
    decisions.py search <words> Rank decisions by keyword match, top 8 (for "why do we ...?").

Scale: records are never loaded wholesale. An index (file path -> records) is cached in
.context/.state/index.json and rebuilt only when a record changes, so a lookup reads one
small JSON file plus at most a handful of records, whether there are 50 or 5000.
Each record is injected at most once per session, and at most MAX_PER_FILE per file.
Standard library only.
"""
import fnmatch
import json
import os
import re
import sys
from pathlib import Path

MAX_PER_FILE = 5
MAX_SEARCH = 8
DECISION_CHARS = 220
STATUS_ORDER = {"active": 0, "draft": 1, "needs-reason": 2}

PROJECT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()
DECISIONS = PROJECT / ".context" / "decisions"
STATE = PROJECT / ".context" / ".state"
INDEX = STATE / "index.json"


# ---------- reading records ----------

def parse_record(path):
    """Pull the few fields we need from a record without a YAML dependency."""
    text = path.read_text(encoding="utf-8", errors="replace")
    front, body = "", text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            front, body = parts[1], parts[2]
    field = lambda name: (re.search(rf"^{name}:\s*\"?([^\"\n]*)\"?\s*$", front, re.M) or [None, ""])[1].strip()
    anchors = [a.strip().strip("\"'") for a in re.findall(r"^\s*-\s*symbol:\s*(.+)$", front, re.M)]
    title = (re.search(r"^#\s+(.+)$", body, re.M) or [None, path.stem])[1].strip()
    decision = (re.search(r"\*\*Decision:\*\*\s*(.+)", body) or [None, ""])[1].strip()
    if len(decision) > DECISION_CHARS:
        decision = decision[:DECISION_CHARS].rstrip() + "…"
    return {
        "id": field("id") or path.stem.split("-")[0],
        "status": field("status") or "draft",
        "title": title,
        "decision": decision,
        "file": str(path.relative_to(PROJECT)),
        "paths": sorted({a.split("#", 1)[0] for a in anchors if a}),
        "text": (title + " " + body).lower(),
    }


def load_index():
    """Return the cached index, rebuilding it if any record changed since it was written."""
    records = sorted(DECISIONS.glob("*.md")) if DECISIONS.is_dir() else []
    stamp = [len(records), max((p.stat().st_mtime for p in records), default=0)]
    try:
        cached = json.loads(INDEX.read_text())
        if cached.get("stamp") == stamp:
            return cached
    except (OSError, ValueError):
        pass
    exact, globs, entries = {}, [], {}
    for p in records:
        r = parse_record(p)
        if r["status"] == "superseded":
            continue
        entries[r["file"]] = {k: r[k] for k in ("id", "status", "title", "decision", "file")}
        for path in r["paths"]:
            if any(ch in path for ch in "*?["):
                globs.append([path, r["file"]])
            else:
                exact.setdefault(path, []).append(r["file"])
    index = {"stamp": stamp, "exact": exact, "globs": globs, "entries": entries}
    STATE.mkdir(parents=True, exist_ok=True)
    try:
        INDEX.write_text(json.dumps(index))
    except OSError:
        pass
    return index


def decisions_for(rel_path, index):
    files = list(index["exact"].get(rel_path, []))
    files += [f for pattern, f in index["globs"] if fnmatch.fnmatch(rel_path, pattern)]
    found = {f: index["entries"][f] for f in files if f in index["entries"]}
    return sorted(found.values(), key=lambda e: (STATUS_ORDER.get(e["status"], 3), e["id"]))


def relative(path_str):
    p = Path(path_str)
    if not p.is_absolute():
        p = PROJECT / p
    try:
        return str(p.resolve().relative_to(PROJECT))
    except ValueError:
        return None  # outside the project


def line(e):
    tag = "ask before changing: reason unknown" if e["status"] == "needs-reason" else e["status"]
    summary = f": {e['decision']}" if e["decision"] else ""
    return f"- {e['id']} [{tag}] {e['title']}{summary} (full record: {e['file']})"


# ---------- commands ----------

def cmd_context():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    tool_input = data.get("tool_input") or {}
    rel = relative(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    if not rel or rel.startswith(".context/"):
        return 0
    found = decisions_for(rel, load_index())
    if not found:
        return 0

    # Each record at most once per session, so repeated reads cost nothing.
    session = re.sub(r"[^A-Za-z0-9_-]", "", data.get("session_id") or "nosession")
    shown_file = STATE / f"shown-{session}.txt"
    shown = set(shown_file.read_text().split()) if shown_file.exists() else set()
    new = [e for e in found if e["file"] not in shown]
    if not new:
        return 0
    batch = new[:MAX_PER_FILE]
    try:
        with shown_file.open("a") as f:
            f.write("".join(e["file"] + "\n" for e in batch))
    except OSError:
        pass

    lines = [f"[decisions] Recorded decisions that govern {rel}:"] + [line(e) for e in batch]
    if len(new) > len(batch):
        lines.append(f"- …and {len(new) - len(batch)} more: run python3 .claude/hooks/decisions.py show {rel}")
    lines.append("Read the full record before changing code it covers. If the change you are about to make "
                 "contradicts one, stop and tell the user instead of proceeding. For 'needs-reason' records, "
                 "ask the user why before changing that behaviour.")
    event = data.get("hook_event_name") or "PostToolUse"
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": "\n".join(lines)}}))
    return 0


def cmd_show(path_arg):
    rel = relative(path_arg) or path_arg
    found = decisions_for(rel, load_index())
    if not found:
        print(f"No decisions anchored to {rel}.")
    for e in found:
        print(line(e))
    return 0


def cmd_search(words):
    terms = [w.lower() for w in words if len(w) > 2]
    if not terms:
        print("Give a few keywords, e.g. search polling websocket")
        return 1
    scored = []
    for p in (sorted(DECISIONS.glob("*.md")) if DECISIONS.is_dir() else []):
        r = parse_record(p)
        if r["status"] == "superseded":
            continue
        title = r["title"].lower()
        score = sum(r["text"].count(t) + 3 * title.count(t) for t in terms)
        if score:
            scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        print("No matching decisions.")
    for _, r in scored[:MAX_SEARCH]:
        print(line(r))
    return 0


def main(argv):
    if len(argv) >= 2 and argv[1] == "context":
        return cmd_context()
    if len(argv) == 3 and argv[1] == "show":
        return cmd_show(argv[2])
    if len(argv) >= 3 and argv[1] == "search":
        return cmd_search(argv[2:])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
