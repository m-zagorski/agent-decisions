# agent-decisions

Decisions that stay with the code.

When you build with an AI coding agent, the *why* behind your code lives in the session: what you tried, what failed, which approach you picked and for what reason. Then the session ends and it's gone. The next person, or the next agent, sees only the code, and "simplifies" the workaround that was there for a reason.

**agent-decisions** keeps those decisions in the repo, next to the code they govern, and puts them in front of the agent at the moment it's about to touch that code.

- **Capture**: decisions are drafted automatically from Claude Code sessions. Nobody has to remember to write docs.
- **Review**: you approve, fix, merge or delete the drafts in a quick guided pass.
- **Read**: when Claude reads or edits a file, the decisions anchored to it are injected into its context. Only those, never the whole folder.
- **Respect**: if a change contradicts a decision, Claude stops and asks instead of proceeding. Overriding a decision means superseding it with a new record and a reason.

> **Status: v0, experimental.** Built for Claude Code, used on an Android (Kotlin) project. Anchors are checked by the agent, not yet by a parser, and there is no drift check on code changes yet.

## What it looks like

A decision record, in `.context/decisions/0008-detached-amplify-timeout.md`:

```markdown
---
id: "0008"
status: active
anchors:
  - symbol: app/src/main/java/.../AmplifyTimeout.kt#withAmplifyTimeoutOrNull(Long,suspend () -> T)
---
# Time out Amplify calls by awaiting a detached coroutine

**Decision:** The Amplify call runs in `async` without the caller's Job; only `await()` is wrapped in `withTimeoutOrNull`.

**Why:** Amplify's Kotlin facade uses `suspendCoroutine`, which ignores cancellation, so a plain timeout never fires.

**Rejected:**
- Plain `withTimeoutOrNull` around the call: the timeout never takes effect.
- `GlobalScope`: loses the caller's dispatcher and tracing context.
```

Then, when someone asks Claude to "simplify this to a plain `withTimeoutOrNull`", Claude gets the decision in its context the moment it reads the file, and answers:

> I haven't changed anything yet, because this goes against decision 0008 … How do you want to go ahead? 1. Keep it as is (recommended) 2. … 3. …

## Requirements

- [Claude Code](https://claude.com/claude-code), with the `claude` command on your PATH
- `python3` (standard library only, nothing to install)
- `bash`, `git`

## Install

```bash
git clone https://github.com/m-zagorski/agent-decisions.git
./agent-decisions/install.sh /path/to/your/project
```

This copies the skills and hooks into the project's `.claude/`, merges the hook and permission settings into `.claude/settings.local.json` (personal, not committed), adds a short instruction block to `AGENTS.md`, and adds `.context/.state/` to `.gitignore`. Running it again updates agent-decisions without duplicating anything.

Options:
- `--shared`: write settings to `.claude/settings.json` instead, so the whole team gets the hooks.
- `--with-ci`: also add the GitHub check that blocks merging unreviewed drafts and warns about broken anchors. It calls `.claude/hooks/decisions.py`, so commit `.claude/hooks/`.

Then:
1. Make sure your `CLAUDE.md` contains `@AGENTS.md` (or paste [the block](templates/AGENTS.decisions.md) into `CLAUDE.md` directly).
2. Restart Claude Code in the project and run `/hooks`. You should see `SessionEnd`, `PreCompact`, `SessionStart` and `PostToolUse`.

## Usage

### Capturing decisions

**Automatically.** Just work. When you `/clear`, exit, or the session gets compacted, a background run reads the new part of the session and writes drafts to `.context/decisions/`. Sessions with no code edits are skipped. The next session starts with a note like:

> [decisions] Decision records are waiting for the user: Drafts to review: 0014, 0015 …

**Manually.** Run `/capture-decisions` at the end of a task. It uses the live conversation and asks you directly when the reason behind a decision is unknown.

Capture keeps only decisions a competent developer could plausibly break without knowing about them. It merges related ones into a single record, never invents a reason, and verifies every anchor against the actual code. Running it again updates drafts instead of duplicating them.

### Reviewing

```
/review-decisions                 # all drafts and needs-reason records
/review-decisions app/.../auth/   # only records for one area
/review-decisions 0014 0015       # specific records
```

Claude checks every anchor, proposes merges, deletions and conflicts, then asks you in rounds of up to 4 multiple-choice questions. Approved records become `status: active`, with `reviewed_by` and `reviewed_at` added.

### Reading

This happens on its own. When Claude reads or edits a file, the decisions anchored to it are added to its context as one-line summaries, at most 5 per file and each record at most once per session. For "why does this work like this?" questions, Claude searches:

```bash
python3 .claude/hooks/decisions.py search sign-out timeout
python3 .claude/hooks/decisions.py show app/src/main/java/.../AmplifyTokenManager.kt
```

You can run those yourself too.

### Keeping anchors healthy

Anchors are paths, so renaming or deleting a file silently disconnects its decisions. `doctor` finds them:

```bash
python3 .claude/hooks/decisions.py doctor
```

```
0008 Time out Amplify calls by awaiting a detached coroutine  [active]  (.context/decisions/0008-…md)
  missing    app/…/AmplifyTimeout.kt#withAmplifyTimeoutOrNull(Long,suspend () -> T)
             renamed → core/net/…/AmplifyTimeout.kt (e86516f, 2026-10-28)
```

It reports anchors whose file is `missing` (and, from git, where it went: renamed, deleted, or not committed yet), globs that match nothing (`no match`), symbols whose name no longer appears in the file (`symbol`; a name check only, parameter types aren't verified), and records with `no anchors`. Superseded records are skipped. It exits with 1 when something is broken. The CI check (`--with-ci`) runs it on every PR as a warning, so the PR that renames a file is the one that hears about it.

### Record lifecycle

| Status | Meaning | Shown to agents |
| --- | --- | --- |
| `draft` | Captured, not reviewed yet | Yes |
| `needs-reason` | Intentional, but nobody knows why yet: "ask before changing" | Yes |
| `active` | Reviewed and approved | Yes |
| `superseded` | Replaced by a newer record (`supersedes: "NNNN"`) | No |

Drafts are fine on feature branches: they're the handoff notes when someone else picks up your work. The optional CI check (`--with-ci`) blocks merging any `draft` into `main`, and warns about broken anchors. `needs-reason` records are allowed but listed.

## What ends up in your project

```
your-project/
├── AGENTS.md                         # + the agent decisions instruction block
├── .claude/
│   ├── settings.local.json           # + hooks and two pre-approved lookup commands
│   ├── skills/
│   │   ├── capture-decisions/SKILL.md
│   │   └── review-decisions/SKILL.md
│   └── hooks/
│       ├── capture.sh                # SessionEnd + PreCompact: start a background capture
│       ├── filter_transcript.py      # condense the new part of a session transcript
│       ├── capture-status.sh         # SessionStart: report drafts waiting for review
│       └── decisions.py             # PostToolUse: inject decisions; show / search
├── .context/
│   ├── decisions/                    # the records, committed with the code
│   └── .state/                       # logs, snapshots, index (gitignored)
└── .github/workflows/decisions-check.yml   # optional
```

## Checking that it works

1. **The hook, without Claude:**
   ```bash
   echo '{"session_id":"test","tool_input":{"file_path":"path/to/AnchoredFile.kt"}}' \
     | python3 .claude/hooks/decisions.py context && rm -f .context/.state/shown-test.txt
   ```
   You should see JSON with `[decisions] Recorded decisions that govern …`.
2. **In a session:** ask Claude to read an anchored file. A new `.context/.state/shown-<session>.txt` should appear.
3. **Respect:** ask Claude to make a change that contradicts a decision. It should stop and ask.
4. **No noise:** ask Claude to read a file with no decisions. It shouldn't mention any.

## Troubleshooting

- **Nothing happened after `/clear`.** Capture runs in the background and takes a minute or two. Check `tail -20 .context/.state/capture.log`; it should end with `--- ok`.
- **The log says `FAILED` or shows permission errors.** The background run may only read files, write inside `.context/`, and run `git branch/log/diff`. The log names what was denied.
- **Hooks don't run at all.** Hooks load when a session starts: restart Claude Code, then check `/hooks`.
- **A decision isn't injected.** Its anchor path must be relative to the repo root and be the file, a folder above it, or a glob like `feature/auth/**` that matches it. Check with `decisions.py show <path>`, and run `decisions.py doctor` to find anchors that no longer point at anything.

## Development

```bash
python3 -m unittest discover tests -v
```

## License

MIT