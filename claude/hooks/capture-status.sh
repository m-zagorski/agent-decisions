#!/usr/bin/env bash
# agent-decisions: at session start, tell Claude which decision records are waiting for the user.
# Registered for SessionStart; its output is added to Claude's context.
[ "${DECISIONS_CAPTURE_RUNNING:-}" = "1" ] && exit 0

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
DIR="$PROJECT_DIR/.context/decisions"
STATE="$PROJECT_DIR/.context/.state"
[ -d "$DIR" ] || exit 0

list_status() {
  grep -l "^status: $1\$" "$DIR"/*.md 2>/dev/null | while read -r f; do basename "$f" .md; done | paste -sd ',' - | sed 's/,/, /g'
}
drafts="$(list_status draft)"
needs="$(list_status needs-reason)"
failed=""
[ -f "$STATE/last-failed" ] && failed=1

[ -z "$drafts$needs$failed" ] && exit 0

echo "[decisions] Decision records are waiting for the user:"
[ -n "$drafts" ] && echo "- Drafts to review and mark status: active: $drafts"
[ -n "$needs" ] && echo "- Missing a reason (ask the user why, then fill in Why and set status: draft): $needs"
[ -n "$failed" ] && echo "- The last background capture failed; details in .context/.state/capture.log"
echo "Mention this to the user in one or two lines at the start of your first reply, then continue with their request."
exit 0
