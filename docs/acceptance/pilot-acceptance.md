# Pilot acceptance report

Date: 2026-09-21. Scope: local code acceptance with fixtures; production was not changed.
The first delivery is a **pilot**, not 50/50 live source coverage.

## Original ten readiness criteria

| # | Status | Evidence |
|---|---|---|
| 1 | verified | `PilotJourneyAcceptanceTests`: confirmed editable profile and exact registry of 9 sites + 41 Telegram sources. |
| 2 | verified | Registry test requires a concrete reason for every unverified source and forbids a seeded source from claiming `ready`. |
| 3 | mock-only | Fixture collection creates one exact-link vacancy; a second cycle keeps one vacancy. Live provider repeat remains separate. |
| 4 | verified | Existing matching/vacancy suites cover missing salary, country restrictions, and duty-first role rejection. |
| 5 | mock-only | Fixture journey creates both drafts; real Chrome DOM acceptance saves an edit and verifies the exact clipboard text. |
| 6 | mock-only | Full fixture journey sends one owner digest; monitoring suites prove one source failure does not stop the cycle. |
| 7 | verified | Snapshot/restore contract preserves profile/status/checkpoint/CV and TTL cleanup; actual restart/restore drill is blocked pending deployment gate. |
| 8 | mock-only | Research and both drafts share provenance-backed research; source URLs and checked times are asserted by research suites. |
| 9 | verified | Research/draft suites cover unavailable search, conflicts, budget exhaustion, partial drafts, and no invented facts. |
| 10 | blocked | Himalayas/Jobicy contracts and fixture repeats are verified; live API repeat is not run in this acceptance task. CryptoJobsList remains `needs_access`. |

## Source coverage

- Catalog: **50/50 registered** (9 sites, 41 Telegram), verified automatically.
- Live working coverage: **not claimed**. No live API, Telegram session, paid call, or delivery was used.
- Himalayas/Jobicy: adapter behavior is fixture-verified; live repeat and exact-link smoke are blocked until production acceptance.
- Telegram: 41/41 registered with an access reason; live coverage is blocked until `API_ID` and `API_HASH` are supplied later.
- Keyed/paid sources remain honestly `needs_access` or `not_ready`.

## End-to-end evidence

`tests.acceptance.test_pilot_acceptance.PilotJourneyAcceptanceTests` covers:
CV upload → DOCX extraction → structured profile extraction → explicit confirmation →
collection → match → web research → cover letter + recruiter message → owner edit →
applied status → Telegram digest,
with every external provider replaced by a deterministic fixture.

## Design and browser acceptance

Static verification covers semantic tokens, both theme definitions, system-theme CSS,
local persistence/storage sync, 16rem/4.25rem shell dimensions, 62rem/42rem breakpoints,
drawer Escape/focus contracts, skip link, ARIA state, focus-visible, page overflow guard,
table-local overflow, and >=4.5:1 core text token contrast.

The reproducible Playwright-core/Axe harness uses the locally installed Chrome and checks
five authenticated pages at 1920/1280/992/768/390 in both themes: 50 scans total.
Every scan has Axe serious/critical=0, clean console, no page overflow at normal or 200%
root text size. Drawer keyboard open, focus entry, Escape close and focus return are
checked at every viewport up to 992px. A real DOM flow saves edited draft text, copies it
through navigator.clipboard, reads it back, and verifies the rendered success state.

## Security and resilience

The full Django suite supplies negative evidence for authentication/403, CSRF,
optimistic conflicts, SSRF including private/reserved IPs and redirects, exact LLM
permission/hash, prompt-injection isolation, atomic budget reservation, TTL cleanup,
restart/checkpoint recovery, duplicate prevention, and error sanitization.
Snapshot verification rejects traversal, unknown formats, undeclared files (including
nested private/**/manifest.json), missing files, checksum drift, and non-resumable ORM state.
Monitoring exposes a safe exception without upstream cause/context. Restore rollback
is fault-injected at the second and fourth move in Linux; both restore old data/private.

## Remaining external acceptance

Production deploy/HTTPS, VPS restart/restore drill, real Remote OK/Himalayas/Jobicy
repeat, real Telegram reader/bot delivery, real OpenAI/Tavily/Brave calls, and keyed
providers require the separate owner-approved production gate and credentials.

## Reproducible local results

- Full Django suite → 355 passed, 1 skipped (before T12: 345 passed, 1 skipped).
- T12 acceptance module → 4 passed.
- Node UI suite → 8 passed (before T12: 8).
- Migration drift check → no changes detected.
- Dependency consistency check → no broken requirements.
- Browser matrix → 50 scans, 5 viewports, 2 themes, Axe serious/critical=0, console clean, DOM save/copy passed.
- Linux container restore harness → bash syntax passed; second/fourth move rollback passed.
