<!-- agent-decisions:start -->
## Recorded decisions (.context/decisions/)

This repo records design decisions as short files in `.context/decisions/`. Do not read that folder wholesale; it can be large.

- In Claude Code, when you read or edit a file, decisions anchored to it are added to your context automatically (`[decisions] Recorded decisions that govern …`). Other agents: run `show <path>` before editing a file. Respect them either way. If a change you are about to make contradicts one, stop and tell the user before proceeding. For records marked "reason unknown", ask the user why before changing that behaviour.
- Before planning a change, or when the user asks why something works the way it does, search first:
  `python3 .claude/hooks/decisions.py search <a few keywords>`
- To list every decision for one file: `python3 .claude/hooks/decisions.py show <path>`
- Open a full record only when its one-line summary is relevant to the task.
<!-- agent-decisions:end -->
