#!/usr/bin/env bash
# Install ctx into a project that uses Claude Code.
#
# Usage:  ./install.sh /path/to/your/project [--shared] [--with-ci]
#   --shared   write hooks and permissions to .claude/settings.json (committed, for the team)
#              instead of .claude/settings.local.json (personal, the default)
#   --with-ci  also add .github/workflows/decisions-check.yml
#
# Safe to run again: it updates ctx's own files, merges settings without duplicating
# entries, and replaces the AGENTS.md block between its ctx markers.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${1:-}"
[ -n "$TARGET" ] && [ -d "$TARGET" ] || { echo "usage: $0 /path/to/project [--shared] [--with-ci]" >&2; exit 2; }
TARGET="$(cd "$TARGET" && pwd)"
shift
SETTINGS_FILE="settings.local.json"
WITH_CI=""
for arg in "$@"; do
  case "$arg" in
    --shared) SETTINGS_FILE="settings.json" ;;
    --with-ci) WITH_CI=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }

echo "Installing ctx into $TARGET"

# 1. Skills and hooks
mkdir -p "$TARGET/.claude/skills" "$TARGET/.claude/hooks" "$TARGET/.context/decisions"
for skill in capture-decisions review-decisions; do
  mkdir -p "$TARGET/.claude/skills/$skill"
  cp "$SRC/claude/skills/$skill/SKILL.md" "$TARGET/.claude/skills/$skill/SKILL.md"
done
cp "$SRC"/claude/hooks/* "$TARGET/.claude/hooks/"
chmod +x "$TARGET"/.claude/hooks/*.sh "$TARGET"/.claude/hooks/*.py
echo "  skills and hooks -> .claude/"

# 2. Merge hooks and permissions into the settings file
python3 - "$SRC/claude/settings.hooks.json" "$TARGET/.claude/$SETTINGS_FILE" <<'PY'
import json, sys
from pathlib import Path
src, dst = Path(sys.argv[1]), Path(sys.argv[2])
new = json.loads(src.read_text())
cur = json.loads(dst.read_text()) if dst.exists() and dst.read_text().strip() else {}

allow = cur.setdefault("permissions", {}).setdefault("allow", [])
for rule in new["permissions"]["allow"]:
    if rule not in allow:
        allow.append(rule)

hooks = cur.setdefault("hooks", {})
for event, groups in new["hooks"].items():
    existing = hooks.setdefault(event, [])
    have = {h.get("command") for g in existing for h in g.get("hooks", [])}
    for g in groups:
        if not any(h.get("command") in have for h in g["hooks"]):
            existing.append(g)

dst.write_text(json.dumps(cur, indent=2) + "\n")
PY
echo "  hooks and permissions merged -> .claude/$SETTINGS_FILE"

# 3. Agent instructions: replace the ctx block in AGENTS.md, or append it
python3 - "$SRC/templates/AGENTS.decisions.md" "$TARGET/AGENTS.md" <<'PY'
import re, sys
from pathlib import Path
block = Path(sys.argv[1]).read_text().strip() + "\n"
dst = Path(sys.argv[2])
text = dst.read_text() if dst.exists() else ""
pattern = re.compile(r"<!-- ctx:start -->.*?<!-- ctx:end -->\n?", re.S)
text = pattern.sub(lambda _: block, text) if pattern.search(text) else (text.rstrip() + "\n\n" + block if text.strip() else block)
dst.write_text(text)
PY
echo "  agent instructions -> AGENTS.md (make sure CLAUDE.md imports it with @AGENTS.md)"

# 4. Keep ctx's working files out of git
GI="$TARGET/.gitignore"
grep -qxF ".context/.state/" "$GI" 2>/dev/null || { echo ".context/.state/" >> "$GI"; echo "  .context/.state/ -> .gitignore"; }

# 5. Optional CI check
if [ -n "$WITH_CI" ]; then
  mkdir -p "$TARGET/.github/workflows"
  cp "$SRC/templates/decisions-check.yml" "$TARGET/.github/workflows/decisions-check.yml"
  echo "  CI check -> .github/workflows/decisions-check.yml (adjust the branches, make it required)"
fi

echo
echo "Done. Restart Claude Code in that project and run /hooks to confirm the hooks are listed."
