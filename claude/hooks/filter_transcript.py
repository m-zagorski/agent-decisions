#!/usr/bin/env python3
"""Condense a Claude Code transcript (JSONL) into a short text file for decision capture.

Usage:
    filter_transcript.py <transcript.jsonl> <session_id> <state_dir> <out_file>

What it keeps:
    [you]     your messages
    [claude]  Claude's prose (not its private reasoning)
    [edit]    file edits, clipped
    [write]   new files, clipped
    [bash]    commands that were run
    [error]   failed tool results (often the "tried X, it failed" part of a decision)

What it drops: file reads, successful tool output, Claude's thinking, subagent chatter
(subagent file edits are kept), and edits inside .context/ (the records themselves).

Incremental: only the part of the session not captured yet is written.
    - <state_dir>/<session_id>.done holds how many entries were already captured.
    - A manual /capture-decisions run in the session also counts as captured up to that point.
    - On success it writes <state_dir>/<session_id>.pending; the caller renames it to .done
      once the capture actually succeeded.

Exit codes: 0 = wrote <out_file>, 3 = nothing new worth capturing, 2 = bad usage.
Standard library only.
"""
import json
import re
import sys
from pathlib import Path

EDIT_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
TEXT_LIMIT = 4000      # per message
SNIPPET_LIMIT = 600    # per edited region
CMD_LIMIT = 300
ERROR_LIMIT = 500
MAX_MULTIEDITS = 5
SKILL_NAME = "capture-decisions"
SKILL_COMMAND = re.compile(r"(^|[\s>])/" + SKILL_NAME + r"\b")


def clip(text, limit):
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f" …[{len(text) - limit} chars cut]"


def indent(text, prefix="    "):
    return "\n".join(prefix + line for line in text.splitlines()) or prefix


def blocks(entry):
    content = (entry.get("message") or {}).get("content", "")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def load(path):
    entries = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a half-written last line is normal while a session is live
    return entries


def in_context_dir(path):
    return "/.context/" in path.replace("\\", "/") or path.startswith(".context/")


def is_capture_invocation(entry):
    """True if this entry is the user running the skill, or Claude invoking it."""
    kind = entry.get("type")
    for b in blocks(entry):
        if kind == "user" and b.get("type") == "text" and SKILL_COMMAND.search(b.get("text", "")):
            return True
        if (kind == "assistant" and b.get("type") == "tool_use" and b.get("name") == "Skill"
                and SKILL_NAME in json.dumps(b.get("input") or {})):
            return True
    return False


def tool_result_text(content):
    if isinstance(content, list):
        return " ".join(x.get("text", "") for x in content if isinstance(x, dict))
    return str(content or "")


def render(entry):
    """Return (lines, made_a_real_edit) for one transcript entry."""
    kind = entry.get("type")
    if kind not in ("user", "assistant") or entry.get("isMeta"):
        return [], False
    sidechain = bool(entry.get("isSidechain"))
    speaker = "[you]" if kind == "user" else "[claude]"
    lines, edited = [], False

    for b in blocks(entry):
        btype = b.get("type")

        if btype == "text" and not sidechain:
            text = b.get("text", "")
            if text.strip() and "<local-command-stdout>" not in text:
                lines.append(f"{speaker} {clip(text, TEXT_LIMIT)}")

        elif btype == "tool_use":
            name, inp = b.get("name", ""), b.get("input") or {}
            if name in EDIT_TOOLS:
                path = inp.get("file_path") or inp.get("notebook_path") or "?"
                if in_context_dir(path):
                    continue
                edited = True
                if name == "Edit":
                    lines.append(f"[edit] {path}\n  before:\n{indent(clip(inp.get('old_string'), SNIPPET_LIMIT))}"
                                 f"\n  after:\n{indent(clip(inp.get('new_string'), SNIPPET_LIMIT))}")
                elif name == "MultiEdit":
                    edits = inp.get("edits") or []
                    lines.append(f"[edit] {path} ({len(edits)} changes)")
                    for e in edits[:MAX_MULTIEDITS]:
                        lines.append(f"  after:\n{indent(clip(e.get('new_string'), SNIPPET_LIMIT))}")
                elif name == "Write":
                    lines.append(f"[write] {path}\n{indent(clip(inp.get('content'), SNIPPET_LIMIT))}")
                else:
                    lines.append(f"[edit] {path}")
            elif name == "Bash" and not sidechain:
                lines.append(f"[bash] {clip(inp.get('command'), CMD_LIMIT)}")

        elif btype == "tool_result" and b.get("is_error") and not sidechain:
            lines.append(f"[error] {clip(tool_result_text(b.get('content')), ERROR_LIMIT)}")

    return lines, edited


def main(argv):
    if len(argv) != 5:
        print(__doc__, file=sys.stderr)
        return 2
    transcript, session_id, state_dir, out_file = argv[1:5]
    state = Path(state_dir)
    state.mkdir(parents=True, exist_ok=True)

    entries = load(transcript)
    done_file = state / f"{session_id}.done"
    start = 0
    if done_file.exists():
        try:
            start = int(done_file.read_text().strip() or 0)
        except ValueError:
            start = 0

    # A manual /capture-decisions run already covered everything up to it.
    for i in range(start, len(entries)):
        if is_capture_invocation(entries[i]):
            start = i + 1

    lines, edited = [], False
    for entry in entries[start:]:
        entry_lines, entry_edited = render(entry)
        lines.extend(entry_lines)
        edited = edited or entry_edited

    if not edited:
        # Nothing was built since the last capture: mark it as captured and stop.
        done_file.write_text(str(len(entries)))
        return 3

    header = [
        "# Filtered Claude Code transcript for decision capture",
        f"session: {session_id}",
        f"covers: entries {start + 1}-{len(entries)} of {len(entries)}"
        + (" (earlier parts were already captured)" if start else ""),
        "legend: [you] user · [claude] assistant prose · [edit]/[write] file changes (clipped) · "
        "[bash] commands run · [error] failed tool results",
        "",
    ]
    Path(out_file).write_text("\n".join(header + lines) + "\n", encoding="utf-8")
    (state / f"{session_id}.pending").write_text(str(len(entries)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
