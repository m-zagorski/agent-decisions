---
id: "0001"
status: active
platforms: [android]
captured_from: "session 2026-10-27, branch feature/offline-sync"
reviewed_by: "Jane Doe"
reviewed_at: "2026-10-28"
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
