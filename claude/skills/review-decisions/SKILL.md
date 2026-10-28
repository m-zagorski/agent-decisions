---
name: review-decisions
description: Walk the user through draft and needs-reason decision records in .context/decisions/ so they can approve, fix, merge or delete each one. Use when the user runs /review-decisions or asks to review, clean up, approve or go through the decision drafts.
---

# Review decisions

Drafts in `.context/decisions/` are already shown to agents as decisions, so an unreviewed or wrong draft misleads them as much as a good one helps. This skill turns drafts into `active` records the user stands behind, and removes the rest. The user decides; you prepare each decision so it takes them seconds.

Arguments (optional): record IDs (`0003 0007`), a path or folder (only records anchored there), or `all` to include `active` records too. With no argument, review every `draft` and `needs-reason` record.

## 1. Take inventory (no questions yet)

Read every record in scope, plus the `active` records anchored to the same files. For each record in scope, check:

- **Anchors**: open each anchored file and confirm the declaration still exists with that name and those parameter types. Mark each anchor `ok`, `moved` (you found it under a different name or file: note where), or `gone`.
- **Duplicates and overlaps**: records that describe the same decision, or the same flow with shared anchors, are merge candidates. Prefer merging into the lowest ID.
- **Conflicts**: a draft that contradicts an `active` record, or another draft.
- **Value**: would a competent developer plausibly break or "simplify" this without knowing? If not, it is a delete candidate.
- **Reason**: is the *Why* concrete and plausible? A vague or generic reason ("for better maintainability") counts as missing.

Then give the user one compact overview, for example:

> 17 records to review. 3 groups to merge (sign-out flow: 0001, 0002, 0004, 0005; …), 2 delete candidates (0011, 0013), 1 conflict (0009 vs active 0003), 2 need a reason, 4 anchors gone.

Order the review: conflicts first, then needs-reason, then merge groups, then the remaining drafts grouped by area (records anchored near each other are reviewed together, so context carries over).

## 2. Review in rounds

Go through the records in that order, **up to 4 per round**, asking with AskUserQuestion: one question per record. For each, put a short header in the question (ID and title), then one or two sentences: what it says, anchor status, and your recommendation with the reason. Make your recommendation the first option and mark it "(Recommended)".

Options, as they apply:
- **Approve**: becomes `active` as written.
- **Merge into NNNN**: for merge candidates.
- **Delete**: not worth keeping, or wrong.
- **Fix**: the user will say what to change (they can type it via "Other").

A merge group can be one question ("Merge 0001, 0002, 0004, 0005 into one sign-out record?") rather than four.

For `needs-reason` records, ask for the reason directly as an open question in plain text instead of AskUserQuestion, one record at a time. If the user does not know, offer to keep it as `needs-reason` (it still warns agents to ask before changing that code) or delete it.

If AskUserQuestion is not available, ask the same thing in plain text, one record at a time.

Keep going round after round until no records in scope are left or the user says to stop. Do not end your turn between rounds. After each round, say in one line how many records are left.

## 3. Apply each answer immediately

Apply answers right after each round, so stopping halfway loses nothing.

- **Approve**: set `status: active`, add `reviewed_by:` (from `git config user.name`) and `reviewed_at:` (today, YYYY-MM-DD) to the frontmatter. Fix `moved` anchors to their new location; remove `gone` anchors and say so. If every anchor is gone, ask before approving: a decision anchored to nothing is never shown to agents.
- **Merge**: rewrite the target record as a merged record (a short list of rules under **Decision**, each with its own reason, all anchors combined, all rejected alternatives kept). Show the user the merged text and get a yes before writing. Then set the target `status: active` with the review fields, and delete the merged drafts. Deleting is fine here because drafts were never approved and the user chose the merge.
- **Delete**: delete the file.
- **Fix**: apply the user's change, show the result, then approve it unless they say otherwise.
- **Reason given**: write it into **Why**, remove the **Question** line, and set `status: active` with the review fields (the user just stated the reason, which is approval).
- **Conflict with an active record**: ask which one holds. If the draft wins, approve it with `supersedes: "NNNN"` and set the old record's `status: superseded`. If the active one wins, delete or fix the draft.

Never change an `active` record's text without the user saying so, and never delete an `active` record; superseding is the only way an active record goes away.

## 4. Rules

- Only touch files in `.context/decisions/`. Never change code, never commit.
- Do not invent reasons or details while fixing or merging; use only what the records and the user say.
- Keep your messages short: the user is deciding, not reading essays.
- The user can stop at any time ("stop", "later"). Then report progress and what is left.

## 5. Finish

Report in a few lines: how many approved, merged, deleted, fixed, still `needs-reason`, and still unreviewed. Then suggest a commit, for example:

```
git add .context/decisions && git commit -m "Review decision records: 9 approved, 4 merged, 2 deleted"
```
