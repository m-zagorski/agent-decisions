#!/usr/bin/env bash
# agent-decisions: capture decisions when a session ends (/clear, exit) or is about to be compacted.
# Registered for SessionEnd and PreCompact. It must return quickly, so the slow part
# (a headless Claude Code run of the capture-decisions skill) runs in the background.
set -uo pipefail

# The background capture is itself a Claude Code session: never let it capture itself.
[ "${DECISIONS_CAPTURE_RUNNING:-}" = "1" ] && exit 0

INPUT="$(cat)"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
HOOKS_DIR="$PROJECT_DIR/.claude/hooks"
STATE="$PROJECT_DIR/.context/.state"
LOG="$STATE/capture.log"
MODEL="${DECISIONS_CAPTURE_MODEL:-sonnet}"
mkdir -p "$STATE"

field() {
  python3 -c 'import json,sys; print(json.load(sys.stdin).get(sys.argv[1], ""))' "$1" <<<"$INPUT"
}
TRANSCRIPT="$(field transcript_path)"
SESSION="$(field session_id)"
EVENT="$(field hook_event_name)"
[ -n "$SESSION" ] && [ -f "$TRANSCRIPT" ] || exit 0

# Fast and synchronous: condense the new part of the transcript into a snapshot file.
# Exits non-zero when there is nothing new worth capturing.
FILTERED="$STATE/$SESSION.transcript.md"
python3 "$HOOKS_DIR/filter_transcript.py" "$TRANSCRIPT" "$SESSION" "$STATE" "$FILTERED" || exit 0

PROMPT="Use the capture-decisions skill in transcript mode. The transcript to capture is $FILTERED (session $SESSION, trigger: $EVENT). You cannot ask the user anything; follow the skill's transcript-mode rules."

(
  export DECISIONS_CAPTURE_RUNNING=1
  cd "$PROJECT_DIR" || exit 1

  # One capture at a time. mkdir is atomic, so it works as a lock (macOS has no flock).
  got_lock=""
  for _ in $(seq 1 120); do
    if mkdir "$STATE/lock" 2>/dev/null; then got_lock=1; break; fi
    sleep 5
  done
  if [ -z "$got_lock" ]; then
    echo "$(date '+%F %T') $SESSION: another capture held the lock for 10 min, skipped" >> "$LOG"
    exit 1
  fi
  trap 'rmdir "$STATE/lock" 2>/dev/null' EXIT

  echo "=== $(date '+%F %T') $EVENT session $SESSION" >> "$LOG"
  if claude -p "$PROMPT" --model "$MODEL" \
       --allowedTools "Read" "Glob" "Grep" "Edit(.context/**)" \
                      "Bash(git branch:*)" "Bash(git log:*)" "Bash(git diff:*)" \
       >> "$LOG" 2>&1; then
    mv -f "$STATE/$SESSION.pending" "$STATE/$SESSION.done"
    rm -f "$STATE/last-failed"
    echo "--- ok" >> "$LOG"
  else
    # .pending stays, .done is untouched: the next capture of this session retries this part.
    echo "--- FAILED" >> "$LOG"
    touch "$STATE/last-failed"
  fi
) </dev/null >/dev/null 2>&1 &
disown 2>/dev/null || true
exit 0
