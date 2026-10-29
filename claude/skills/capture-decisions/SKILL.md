---
name: capture-decisions
description: Capture the design decisions made in a Claude Code session as draft decision records in .context/decisions/. Use when the user runs /capture-decisions or asks to record, capture or save the decisions from this session, or when a prompt asks to run capture-decisions in transcript mode on a transcript file.
---

# Capture decisions

Turn the real decisions made in a session into short draft records that live next to the code, so whoever picks up this work later (a person or an agent) knows what was decided and why.

## 0. Pick the mode

- **Live mode** (default): the user ran `/capture-decisions` in this session. The source is this conversation. You can ask the user questions.
- **Transcript mode**: the prompt says "transcript mode" and gives the path of a filtered transcript file (written by the agent-decisions hooks). Read that whole file; it is the source. Its header names the session ID and says which part of the session it covers; earlier parts were already captured, so the existing records already reflect them. **Nobody is there to answer questions**: never wait for input, and turn every open question into a `needs-reason` record (step 4).

In the filtered transcript, `[you]` lines are the user, `[claude]` lines are the assistant's prose, `[edit]`/`[write]` are file changes (clipped), `[bash]` are commands run, and `[error]` are failed tool results, which often show an approach that was tried and abandoned.

Reasons must come from what the user said, or from what the session actually showed (an error, a measurement, a failed attempt). A reason the assistant proposed and the user never confirmed or acted on is not established; treat it as unknown.

## 1. Find the real decisions

From the source, list candidate **decisions**: a choice between alternatives, made for a reason, that should shape how this code is written or changed from now on.

Candidates include, for example:
- Choosing a library, pattern, or architecture (e.g. Paging 3 over manual pagination).
- A behaviour rule (retry 3 times with backoff, never block the main thread on X).
- A constraint discovered while working ("must stay on minSdk 24 because ...").
- An approach that was tried and abandoned, with the reason.
- A product behaviour or an accepted limitation.

Exclude:
- Bug fixes with no real choice involved.
- Renames, formatting, moving code around.
- Anything the code already makes obvious.
- Temporary hacks, unless the user explicitly decided to keep them.
- Tooling or config choices that are better explained by a one-line comment in that config file.

### Apply the value filter

Keep a candidate only if the answer to this question is yes:

> **Would a competent developer plausibly break, undo, or "simplify" this without knowing about it?**

Signals that the answer is yes: the code looks like a mistake or an unnecessary workaround (overriding a library's activity, a detached coroutine instead of a plain timeout), a simpler approach was tried and failed, it must match another platform, or it encodes a product rule that is not visible in the code.

If a candidate fails the filter, drop it. Do not list dropped candidates in the report.

### Merge related decisions

Decisions that belong to the same flow or feature, share most of their anchors, and would be read together become **one record** with several points. For example, five rules that all make the hosted-UI sign-out flow survive a library's quirks are one record ("How hosted-UI sign-out works and why"), not five. Split only when the parts could change independently for different reasons.

As a rough guide, a typical task produces 0–4 records. If you have more than 6, merge harder.

If nothing passes, say so in one sentence and stop. An empty result is a good result; never invent decisions to have something to write.

## 2. Check what already exists (and running again)

Create `.context/decisions/` if it does not exist, then read the existing records whose anchors point at files the session touched.

Running this skill more than once, on the same session or later ones, is safe and must **converge, never duplicate**. For each candidate decision:

| An existing record covers it, and its status is | Do this |
| --- | --- |
| `draft` or `needs-reason` | **Update that record in place**, keeping its ID: add new points, anchors, or a reason that is now known (then `needs-reason` becomes `draft`). |
| `active` (a human approved it) | **Never rewrite it.** If the session reversed or replaced the decision, write a new record with `supersedes: "NNNN"` and set the old one's `status: superseded`. If the session only adds detail, leave it and mention in the report that it may need an update. |
| `superseded` | Ignore it; compare against the record that superseded it. |
| No record covers it | Create a new record. |

Always:
- Treat the current text of every record as authoritative: a person may have edited it. You may **add** to a draft, but never remove or reword lines that are already there.
- Never delete a record. Deleting is the user's call.
- If nothing is new since the last capture, report "No new decisions since the last capture" and stop.

## 3. Anchor each decision to real code

Anchors say which code a decision governs. Use these ID formats:

| What | Format | Example |
| --- | --- | --- |
| Class / object / interface | `path#Name` | `app/src/main/java/com/acme/sync/SyncRepository.kt#SyncRepository` |
| Method | `path#Class.method(ParamTypes)` | `...SyncRepository.kt#SyncRepository.fetch(String,Int)` |
| Top-level function | `path#function(ParamTypes)` | `...Formatters.kt#formatPrice(Long,Currency)` |
| Property | `path#Class.property` | `...SyncRepository.kt#SyncRepository.pageSize` |
| Companion member | `path#Class.Companion.member(...)` | `...Api.kt#Api.Companion.create(OkHttpClient)` |
| Gradle setting | `path#dotted.path` | `app/build.gradle.kts#android.defaultConfig.minSdk` |
| Gradle dependency | `path#dependencies[group:artifact]` | `app/build.gradle.kts#dependencies[androidx.paging:paging-runtime]` |
| Manifest entry | `path#element[name]` | `app/src/main/AndroidManifest.xml#uses-permission[android.permission.CAMERA]` |
| Whole file or folder | `path` (a folder covers everything under it) | `feature/checkout` |
| Files matching a glob | `path/*.kt`, `path/**` | `feature/checkout/**/*ViewModel.kt` |

Rules for parameter types: use simple type names in declaration order, keep `?` for nullable types, and drop generic arguments (`List<Order>` becomes `List`). A function with no parameters ends in `()`.

**Verify every anchor against the current code before writing it**, in both modes: open the file and confirm the declaration exists with exactly that name and those parameter types. In transcript mode the edits shown are clipped and may be out of date, so the files on disk are the truth. A snippet in the transcript or an excerpt you saw earlier does not count as verification: open the actual file on disk and find the declaration. If you only saw an excerpt, treat the anchor as unverified and drop it. If you cannot verify a symbol, leave it out and say which one you dropped. Never anchor generated code: anything under `build/`, Hilt/Dagger output (`*_Factory`, `*_MembersInjector`, `Hilt_*`), Room `*_Impl`, or other generated sources.

## 4. Write the records

- Next ID = the highest existing ID + 1, four digits (`0001` if none exist).
- Filename: `.context/decisions/NNNN-short-kebab-title.md`.
- Get the branch with `git branch --show-current`.
- `captured_from`: live mode → `"session <date>, branch <branch>"`; transcript mode → `"session <session id>, <date>, branch <branch>"`.

Use exactly this format and keep each record under about 25 lines:

```markdown
---
id: "0007"
status: draft
platforms: [android]
captured_from: "session 2026-10-27, branch feature/offline-sync"
anchors:
  - symbol: app/src/main/java/com/acme/sync/SyncRepository.kt#SyncRepository.fetch(String,Int)
---
# Use Paging 3 instead of manual pagination for the orders list

**Decision:** The orders list loads through a Paging 3 `PagingSource`; no manual page tracking in the ViewModel.

**Why:** Manual paging duplicated loading and error state across three screens and broke on process death.

**Rejected:**
- Manual offset paging in the ViewModel: duplicated state, no built-in retry.
- Loading everything at once: some accounts have 10k+ orders.

**Revisit if:** the API moves to cursor-only pagination without stable keys.
```

For a **merged record**, make **Decision** a short list of rules, each followed by its own reason, and list the anchors of all of them:

```markdown
**Decision:** Sign-out through the hosted UI works as follows:
- The app builds the sign-out redirect itself instead of passing the activity's Intent. Why: Amplify's handling drops the session when the activity is recreated.
- The browser step starts only after the post-logout screen has resumed (2 s cap). Why: starting earlier raced the screen transition.
```

`Revisit if` is optional; include it only when the session made a condition clear. Write concrete reasons, with numbers or symptoms the session actually mentioned. Never invent figures.

### When the reason is unknown

If a decision passes the value filter but the source never establishes **why**, do not invent a reason and do not skip it silently. Write the record with:

- `status: needs-reason` in the frontmatter.
- **Decision** filled in as usual.
- **Why:** `Unknown: ask before changing.`
- **Question:** one direct question for the user, e.g. `Why does sign-in keep CUSTOM_AUTH_WITHOUT_SRP instead of SRP?`

When the reason becomes known (the user answers, or a later session explains it), fill in **Why**, remove the **Question** line, and set `status: draft`.

## 5. Safety

Never put secrets or personal data in a record: no API keys, tokens, passwords, URLs with credentials, or customer data, even if they appeared in the session.

Only create or edit files inside `.context/`. Never change code, never commit, never push.

## 6. Report back

Keep it short:
- Which records you created or updated (ID + title, one line each).
- Which records have `status: needs-reason`. In live mode, ask their questions directly, one per record. In transcript mode just list them; the questions are in the records and the next session will raise them.
- Any anchors you dropped because you could not verify them.
- Any `active` records that may need an update.

In live mode, end with one line reminding the user to review the drafts in their next commit and set `status: active` on the ones they keep. If the session produced more than 6 records, also mention in one line that capturing after each finished task keeps batches smaller.
