#!/usr/bin/env python3
"""agent-decisions lookup: give Claude only the decisions that matter for what it is touching.

Commands:
    decisions.py context        Hook mode (PostToolUse on Read/Edit/Write). Reads the hook
                                 JSON on stdin and injects the decisions anchored to that file.
    decisions.py show <path>    Print every decision anchored to a file (for Claude or you).
    decisions.py search <words> Rank decisions by keyword match, top 8 (for "why do we ...?").
    decisions.py doctor [--github]
                                 Check that every anchor still points at existing code, and
                                 suggest where missing files went. Exit 1 if anything is broken.

Anchors: a file (`path` or `path#Symbol`), a folder (`path`, covering everything under it), or
a glob (`path/**`, `path/*.kt`; note that fnmatch's `*` also matches across `/`).

Scale: records are never loaded wholesale. An index (file path -> records) is cached in
.context/.state/index.json and rebuilt only when a record changes, so a lookup reads one
small JSON file plus at most a handful of records, whether there are 50 or 5000.
Each record is injected at most once per session, and at most MAX_PER_FILE per file.
Standard library only.
"""
import fnmatch
import functools
import json
import os
import re
import subprocess
import sys
from pathlib import Path

MAX_PER_FILE = 5
MAX_SEARCH = 8
DECISION_CHARS = 220
STATUS_ORDER = {"active": 0, "draft": 1, "needs-reason": 2}
INDEX_VERSION = 2

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
        "anchors": [a for a in anchors if a],
        "paths": sorted({anchor_path(a) for a in anchors if a}),
        "text": (title + " " + body).lower(),
    }


def anchor_path(anchor):
    return anchor.split("#", 1)[0].strip().rstrip("/")


def is_glob(path):
    return any(ch in path for ch in "*?[")


def load_index():
    """Return the cached index, rebuilding it if any record changed since it was written."""
    records = sorted(DECISIONS.glob("*.md")) if DECISIONS.is_dir() else []
    stamp = [INDEX_VERSION, len(records), max((p.stat().st_mtime for p in records), default=0)]
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
            if is_glob(path):
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
    # Exact anchors match the file itself or any folder above it: `feature/checkout` covers
    # everything under feature/checkout/.
    parts = rel_path.split("/")
    files = [f for i in range(len(parts), 0, -1) for f in index["exact"].get("/".join(parts[:i]), [])]
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


# ---------- doctor ----------

def git(*args):
    try:
        r = subprocess.run(["git", *args], cwd=PROJECT, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


@functools.lru_cache(maxsize=None)
def project_files():
    """Files a glob anchor can match: tracked plus new untracked ones, never ignored build output."""
    out = git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if out is not None:
        return tuple(f for f in out.split("\0") if f)
    skip = {".git", ".gradle", ".idea", "build", "node_modules"}
    files = []
    for root, dirs, names in os.walk(PROJECT):
        dirs[:] = [d for d in dirs if d not in skip]
        files += [str((Path(root) / n).relative_to(PROJECT)) for n in names]
    return tuple(files)


@functools.lru_cache(maxsize=None)
def uncommitted_changes():
    """`git diff HEAD` rows: staged renames show as R, a move with the new file not added yet as D."""
    return tuple(row.split("\t") for row in (git("diff", "-M", "--name-status", "HEAD") or "").splitlines())


def symbol_pattern(symbol):
    """What to look for in the file to believe the symbol is still there. A name check only:
    parameter types and overloads are not verified."""
    bracket = re.search(r"\[([^\]]+)\]\s*$", symbol)
    if bracket:  # dependencies[group:artifact], uses-permission[android.permission.CAMERA]
        name = bracket.group(1).strip()
        alternatives = [re.escape(name)]
        if ":" in name:  # a version catalog writes paging-runtime as libs.androidx.paging.runtime
            artifact = name.rsplit(":", 1)[1]
            alternatives.append(r"[-_.]".join(map(re.escape, re.split(r"[-_.]", artifact))))
        return "|".join(alternatives)
    name = symbol.split("(", 1)[0].rsplit(".", 1)[-1].strip()  # Class.method(Int) -> method
    return re.escape(name) if name else None


def symbol_found(path, symbol):
    pattern = symbol_pattern(symbol)
    if not pattern:
        return True
    text = path.read_text(encoding="utf-8", errors="replace")
    return re.search(rf"(?<![\w$])(?:{pattern})(?![\w$])", text) is not None


def where_did_it_go(path):
    """Explain a missing file from git: renamed (following chains), deleted, or not committed yet.
    Only called for broken anchors, so it reads only the commits that removed them."""
    for cols in uncommitted_changes():
        if cols[0].startswith("R") and cols[1] == path:
            return f"renamed → {cols[2]} (not committed yet)"
        if cols[0] == "D" and cols[1] == path:
            return "deleted or moved, not committed yet"
    seen, hops = set(), []
    while path not in seen:
        seen.add(path)
        last = (git("log", "-1", "--format=%H %h %ad", "--date=short", "--", path) or "").split()
        if not last:
            break
        full, short, date = last
        status = None
        for row in (git("show", "-M", "--name-status", "--format=", full) or "").splitlines():
            cols = row.split("\t")
            if cols[0].startswith("R") and cols[1] == path:
                status = ("R", cols[2])
            elif cols[0] == "D" and cols[1] == path:
                status = ("D", None)
        if not status:
            break
        if status[0] == "D":
            return " → ".join(hops + [f"deleted in {short} ({date})"])
        path = status[1]
        hops.append(f"renamed → {path} ({short}, {date})")
        if (PROJECT / path).is_file():
            return " → ".join(hops)
    return " → ".join(hops) if hops else "not found in git history"


def check_record(r):
    """Problems with one record's anchors, as (kind, anchor, detail) tuples."""
    if not r["anchors"]:
        return [("no anchors", "", "never injected; add an anchor")]
    problems = []
    for anchor in r["anchors"]:
        path, _, symbol = anchor.partition("#")
        path = path.strip().rstrip("/")
        target = PROJECT / path
        if is_glob(path):
            if not any(fnmatch.fnmatch(f, path) for f in project_files()):
                problems.append(("no match", anchor, "the glob matches no file"))
        elif target.is_dir():
            continue
        elif not target.is_file():
            problems.append(("missing", anchor, where_did_it_go(path)))
        elif symbol.strip() and not symbol_found(target, symbol):
            problems.append(("symbol", anchor, "name not found in the file"))
    return problems


def cmd_doctor(github=False):
    records = [parse_record(p) for p in (sorted(DECISIONS.glob("*.md")) if DECISIONS.is_dir() else [])]
    records = [r for r in records if r["status"] != "superseded"]
    broken = [(r, check_record(r)) for r in records]
    broken = [(r, problems) for r, problems in broken if problems]

    for r, problems in broken:
        if github:
            for kind, anchor, detail in problems:
                print(f"::warning file={r['file']}::{r['id']} {kind}: {anchor} ({detail})".replace("\n", " "))
            continue
        print(f"{r['id']} {r['title']}  [{r['status']}]  ({r['file']})")
        for kind, anchor, detail in problems:
            print(f"  {kind:<10} {anchor}")
            print(f"  {'':<10} {detail}")
    if not broken:
        print(f"All anchors of {len(records)} records point at existing code.")
        return 0
    kinds = {}
    for _, problems in broken:
        for kind, _, _ in problems:
            kinds[kind] = kinds.get(kind, 0) + 1
    summary = ", ".join(f"{n} {kind}" for kind, n in kinds.items())
    if not github:
        print()
    print(f"{sum(kinds.values())} problems in {len(broken)} of {len(records)} records ({summary}).")
    return 1


def main(argv):
    if len(argv) >= 2 and argv[1] == "context":
        return cmd_context()
    if len(argv) == 3 and argv[1] == "show":
        return cmd_show(argv[2])
    if len(argv) >= 3 and argv[1] == "search":
        return cmd_search(argv[2:])
    if len(argv) >= 2 and argv[1] == "doctor" and set(argv[2:]) <= {"--github"}:
        return cmd_doctor(github="--github" in argv[2:])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
