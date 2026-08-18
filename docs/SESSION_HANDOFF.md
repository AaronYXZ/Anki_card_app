# Session Handoff Specification

## 1. Handoff snapshot

| Field | Value |
|---|---|
| Project | Anki Card App |
| Snapshot date | 2026-08-17 |
| Branch | `dev` |
| Live deployment source commit | `b902160 Preview cards after approved edits` |
| Local URL | `http://127.0.0.1:8000` |
| Production URL | `https://web-production-a42e0.up.railway.app` |
| Database | PostgreSQL through Docker Compose, host port `5433` |
| Schema head | `20260810_0006` |
| Test baseline | 110 passing, 94.03 percent coverage |
| Product stage | Mobile draft layout completed locally, deployment and first-account device sync acceptance pending |

Start every continuation by running `git status --short`. Preserve any user changes that appeared after this snapshot.

## 2. Product intent

The primary user is a machine learning engineer preparing across software engineering, ML breadth, ML system design, projects, and behavioral interviews. The product solves two linked problems:

1. Convert mixed Markdown knowledge into atomic recall material without requiring manual card authoring.
2. Decide what the learner should review today and preserve enough history to measure retained knowledge.

The product contract is human-controlled generation. AI produces drafts. The learner must approve or edit a draft before it can enter an FSRS review queue.

## 3. Implemented user journey

```text
Upload Markdown or ZIP
        |
        v
Validate and persist immutable source snapshot
        |
        v
Split source by heading and size
        |
        v
Generate structured card candidates per chunk
        |
        v
Validate source excerpt, schema, and duplicate fingerprint
        |
        v
Draft inbox: approve, edit, or reject
        |
        v
Approval initializes FSRS scheduling state
        |
        v
Due-first daily queue and answer reveal
        |
        v
Atomic rating, review log, and next due date
```

The current card types are:

- `normal`: explanatory question and answer;
- `cloze`: one or more Anki cloze deletions;
- `skeleton_recall`: a title and minimal outline on the front, with compact reconstruction cues on the back.

Skeleton Recall is intended for stories, designs, investigations, decisions, and multi-step frameworks. It should not replace atomic Normal or Cloze cards.

## 4. Architecture

The project deliberately uses a server-rendered Python stack:

```text
Jinja pages and targeted browser JavaScript
        |
        v
FastAPI routers
        |
        +-- card_service.py
        +-- import_service.py
        +-- generation.py
        +-- review_service.py
        +-- analytics_service.py
        |
        v
SQLAlchemy models and PostgreSQL
        |
        +-- Alembic migrations
        +-- Py-FSRS serialized scheduling state
        +-- OpenAI structured card generation
```

Important modules:

| Path | Responsibility |
|---|---|
| `src/anki_card_app/app.py` | FastAPI application, static resources, PWA root routes |
| `src/anki_card_app/auth.py` | Request-scoped identity and explicit development bypass |
| `src/anki_card_app/auth_service.py` | Password credentials and server-side session lifecycle |
| `src/anki_card_app/auth_web.py` | Login and logout routes |
| `src/anki_card_app/security.py` | Session-bound CSRF, CSP, and browser security headers |
| `src/anki_card_app/export_service.py` | User-owned portable JSON backup construction |
| `src/anki_card_app/exports_web.py` | Authenticated backup download route |
| `src/anki_card_app/database.py` | Engine configuration, Railway URL normalization, readiness check |
| `src/anki_card_app/web.py` | Dashboard, manual cards, draft lifecycle, review pages |
| `src/anki_card_app/imports_web.py` | Upload flow, model choice, generation background tasks and retries |
| `src/anki_card_app/notes_web.py` | Imported-note ledger and note-to-card traceability |
| `src/anki_card_app/import_service.py` | Safe upload parsing, ZIP limits, Markdown chunking, content hashing |
| `src/anki_card_app/generation.py` | Prompt, structured OpenAI output, validation, deduplication, progress persistence |
| `src/anki_card_app/markdown.py` | CommonMark rendering with embedded HTML disabled and Pygments fenced-code highlighting |
| `src/anki_card_app/card_service.py` | Card validation, immutable versions, lifecycle transitions |
| `src/anki_card_app/review_service.py` | Daily queue, reveal rules, idempotent rating transaction |
| `src/anki_card_app/fsrs_adapter.py` | FSRS state creation, restoration, application, and snapshots |
| `src/anki_card_app/analytics_service.py` | Dashboard and first-attempt recall metrics |
| `src/anki_card_app/models.py` | SQLAlchemy domain model and database invariants |
| `src/anki_card_app/static/` | CSS, keyboard behavior, PWA manifest, icons, service worker |
| `migrations/versions/` | Ordered schema history through authentication support |

## 5. Runtime and configuration

Bootstrap a fresh local environment:

```bash
cp .env.example .env
docker compose up -d postgres
uv sync --all-groups
uv run alembic upgrade head
uv run uvicorn anki_card_app.main:app --reload
```

The `.env` file is intentionally untracked. `OPENAI_API_KEY` may be pasted into it. Restart Uvicorn after changing the key because settings are cached in-process.

Current generation choices are hard-coded in `imports_web.py`:

| UI name | Model identifier |
|---|---|
| Terra | `gpt-5.6-terra` |
| Luna | `gpt-5.6-luna` |

`OPENAI_MODEL` chooses the default radio selection, but it must be in the same allowlist. The provider uses the Responses API with Pydantic structured output and low reasoning effort.

Authentication defaults to the explicit `AUTH_MODE=development` bypass locally.
Password mode uses email credentials and opaque server-side sessions. Create or
rotate accounts with `uv run anki-card-admin create-user --email ...` and
`uv run anki-card-admin set-password --email ...`. Production configuration is
rejected unless `AUTH_MODE=password` and `SESSION_COOKIE_SECURE=true`. See
[Authentication Decision and Threat Model](AUTHENTICATION.md).

## 6. Routes and interfaces

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Dashboard and learning metrics |
| `/login` | GET, POST | Password authentication and session creation |
| `/logout` | POST | Revoke the current session and delete its cookie |
| `/imports` | GET | Import and generation history |
| `/imports/new` | GET, POST | Upload note and choose generation model |
| `/imports/{run_id}` | GET | Per-chunk run status and generated-card count |
| `/imports/{run_id}/retry` | POST | Create a fresh generation run for the same source |
| `/notes` | GET | Imported-note ledger |
| `/notes/{document_id}` | GET | Source metadata and extracted cards |
| `/cards/new` | GET, POST | Manual card creation |
| `/cards` | GET | Browse and edit approved active cards |
| `/cards/{card_id}` | GET | Review-format card preview without scheduling or history side effects |
| `/cards/drafts` | GET | Draft review inbox with a floating back-to-top control |
| `/cards/{card_id}/edit` | GET, POST | Versioned content editing |
| `/cards/{card_id}/approve` | POST | Activate card and initialize scheduling |
| `/cards/{card_id}/reject` | POST | Exclude draft from learning |
| `/review` | GET | Create or continue today's queue |
| `/review/{session_id}/{card_id}/reveal` | POST | Record answer reveal |
| `/review/{session_id}/{card_id}/rate` | POST | Persist rating and FSRS transition |
| `/review/sessions/{session_id}` | GET | Completed-session summary |
| `/exports/backup.json` | GET | Download all owned learning data without credentials or sessions |
| `/restore` | GET, POST | Atomically restore a version 1 JSON export into an empty account |
| `/install` | GET | PWA installation and keyboard guidance |
| `/manifest.webmanifest` | GET | Root-scoped PWA manifest |
| `/service-worker.js` | GET | Root-scoped static-shell service worker |
| `/health` | GET | Process health response |
| `/ready` | GET | PostgreSQL readiness response used by Railway deployment gating |

There is no public JSON product API yet. The current product surface is server-rendered HTML.

## 7. Data model and invariants

Core tables:

- `user_accounts`
- `auth_sessions`
- `source_documents`
- `source_chunks`
- `generation_runs`
- `generation_chunk_runs`
- `cards`
- `card_versions`
- `scheduling_states`
- `review_sessions`
- `review_session_cards`
- `review_logs`

Do not violate these invariants:

1. Source documents are immutable imported snapshots identified by content hash.
2. Card content edits append `CardVersion` rows and advance `current_version_id`.
3. AI-generated cards keep their source document, source chunk, generation run, and exact supporting excerpt.
4. A card fingerprint is unique per user and prevents duplicate tested content.
5. Draft and rejected cards never enter the review queue.
6. Approval creates exactly one scheduling state for the card.
7. Review rating is invalid until the answer has been revealed.
8. Scheduling-state update and review-log insertion occur in one transaction.
9. `ReviewLog.attempt_id` is globally unique and makes submission idempotent.
10. Review logs and prior or new FSRS snapshots are append-only history.
11. Stored timestamps are UTC. User timezone affects local-day boundaries and presentation.
12. Password mode never falls back to the fixed development identity.
13. Raw authentication session tokens are never persisted. Only SHA-256 digests are stored.
14. Every POST validates a CSRF token derived from the current Session after login.
15. Ordinary dynamic content is Jinja-autoescaped. Card Markdown is converted by the shared renderer with embedded HTML and unsafe links disabled before templates receive safe markup. Recognized fenced-code languages are highlighted by Pygments.

## 8. Generation behavior

The active prompt version is `anki-v4-markdown-preservation`. It treats examples,
cases, scenarios, analogies, anecdotes, sample calculations, and illustrative
code as supporting context instead of standalone card material. It may test an
explicitly stated reusable principle, while keeping the example as optional
context. A complete user project or behavioral story may become one Skeleton
Recall card when the source clearly presents the full story as rehearsal material;
incidental details are not atomized into cards. Every source chunk may return at
most 20 candidates. The prompt also requires generated fields to retain useful
source Markdown such as lists, emphasis, links, inline code, fenced code, and math
notation. Exact source excerpts retain their original Markdown. A candidate is
accepted only when:

- its Pydantic schema is valid;
- its card content passes type-specific domain validation;
- its `source_excerpt` is an exact substring of the source chunk;
- its user-level content fingerprint does not already exist.

Progress is saved after every chunk. Chunk failures are tracked separately, which allows a partial run to preserve successful work.

Provider error behavior:

| Failure | User-facing behavior |
|---|---|
| Missing API key | Source remains imported, generation run fails with configuration guidance |
| Exhausted credits | Run aborts with billing guidance |
| Invalid API key | Run aborts and asks for key correction plus restart |
| Model unavailable | Run aborts and asks the user to choose an accessible model |
| Ordinary rate limit | Chunk is considered retryable |
| Request timeout or unexpected error | Retry once in the workflow, then expose failure |

The OpenAI client has `max_retries=0` by default. Workflow retry logic remains under application control. Do not enable both layers casually because that can multiply cost and latency.

Generation uses FastAPI `BackgroundTasks`. This is acceptable for local MVP use only. It is not durable. If the process terminates during a run, wait two minutes and use Resume generation. Production work should move this function to a durable queue with leases, heartbeats, and retry limits.

## 9. Review and metrics behavior

The default user has a daily capacity stored on `UserAccount`. The queue targets
at least 10 Normal and 3 Skeleton Recall reviews per local day when the daily
limit is at least 13 and enough due cards of each type exist. A lower limit is
always respected. Missing quota cards are never fabricated, and unused capacity
is filled with other due card types. Queue selection does the following:

1. Reuse an unfinished session from the same local day. Close stale sessions and rebuild the queue after the local day changes.
2. Subtract reviews already completed during the user's local day.
3. Reserve the missing Normal and Skeleton Recall targets.
4. Fill unused capacity with any other due cards.
5. Order previously reviewed cards before new cards, then order by due time.
6. Store the fixed session order in `review_session_cards`.

Rating mapping:

| Keyboard | Rating | Meaning |
|---:|---:|---|
| `1` | Again | Could not recall |
| `2` | Hard | Recalled with serious difficulty |
| `3` | Good | Recalled after hesitation |
| `4` | Easy | Recalled immediately |

The north-star metric implemented on the dashboard is 30-day first-attempt recall for due reviews. Hard, Good, and Easy count as successful recall. Again counts as failure. Same-day attempts after the first attempt for the same card are excluded.

## 10. PWA behavior

The app provides a manifest, application icons, standalone display mode, installation help, and static-shell caching. The service worker intentionally follows these rules:

- cache only application CSS, JavaScript, icons, manifest, and offline page;
- use the network for navigations;
- return the offline explanation if navigation fails;
- never intercept or cache POST requests;
- never promise offline ratings or imports.

Draft cards constrain Markdown content to the phone viewport. Long links,
identifiers, fenced code, and table cells wrap rather than creating horizontal
page or card scrolling. The shell cache is `anki-shell-v7` so installed PWAs
refresh the updated stylesheet.

Any future offline-write feature requires a synchronization protocol, conflict rules, idempotency, and user-visible pending state. Do not extend the current service worker into offline database writes without that design.

## 11. Verification baseline

Run before and after meaningful changes:

```bash
uv run ruff format --check src tests migrations
uv run ruff check src tests migrations
uv run mypy src
uv run pytest --cov=anki_card_app --cov-report=term-missing
node --check src/anki_card_app/static/app.js
node --check src/anki_card_app/static/service-worker.js
```

At the handoff snapshot:

- 110 tests pass;
- total branch-aware coverage is 94.03 percent;
- coverage threshold is 90 percent;
- both PWA icons are valid PNG files at 192 by 192 and 512 by 512;
- live manifest response type is `application/manifest+json`;
- live service-worker response includes `Cache-Control: no-cache` and `Service-Worker-Allowed: /`;
- Git working tree was clean after commit `6d7f7b8`.

Tests use an isolated SQLite database through fixtures. Production-like PostgreSQL constraints and deployment behavior still need dedicated acceptance testing.

## 12. Known gaps and risks

### Release blockers

- No single-use invite acceptance, password recovery, or account deletion lifecycle.
- The initial authorization matrix is implemented, but it must remain current as routes are added.
- No durable job queue. In-process generation can be interrupted.
- Railway config-as-code, migration gating, readiness, export, and the first live
  deployment are complete. Native backup enablement and a restoration exercise remain open.
- The initial deployment used `railway up`. Railway's GitHub App still needs repository
  access before `dev` pushes can trigger automatic deployments.
- Content is safely rendered as escaped text with a restrictive CSP. A future
  Markdown-to-HTML renderer would still require an allowlist sanitizer.
- No rate, token, or cost budget per import.
- No standalone product-event instrumentation beyond the persisted domain records.
- No implemented 30-day card-frequency metric for cards reviewed at least five times.
- No source and card deletion workflow.
- No provider privacy notice or data-retention disclosure.
- No automated mobile browser end-to-end test or formal accessibility pass. A
  manual 402 by 874 CSS-pixel draft-page check passed with long identifiers,
  URLs, Markdown code, and table-like content, with page and card scroll widths
  equal to their visible widths.

### Deliberately deferred product scope

- automatic Obsidian folder scanning;
- source-file modification reconciliation;
- Anki import or export synchronization;
- notifications, email, or ChatGPT Scheduled Tasks;
- weekly quiz and learning report;
- personalized FSRS parameter optimization;
- AI grading of free-form learner answers;
- semantic duplicate detection.

### Product and technical cautions

- Re-uploading changed content creates a new immutable snapshot. It does not update existing cards.
- The note ledger provides traceability, not live filesystem synchronization.
- JSON restore is deliberately replace-only for an empty account. It does not merge
  records into an account that already contains sources, cards, or review history.
- Terra and Luna model availability depends on the OpenAI project and can change.
- Adding API billing credit may take a short time to propagate. Restarting the local application refreshes cached configuration, but it does not change provider billing state.
- A permanently pending run usually means the process stopped before its background task began or completed. Resume creates a fresh run after the two-minute safety window.

## 13. Recommended next milestone

Milestone 5 is in progress. Authentication, authorization, CSRF, CSP, and the
current text-rendering boundary are complete. Do not start notifications or
source synchronization first.

Recommended order:

1. Implement single-use invite acceptance and administrative account lifecycle.
2. Add login and import rate limits plus observable generation cost.
3. Add source and card deletion with tested review-history behavior.
4. Create the first production account and complete the Mac/iPhone sync acceptance runbook.
5. Grant Railway's GitHub App access to the repository, then verify a `dev` push triggers deployment.
6. Enable native PostgreSQL backups and exercise restoration into a temporary service.
7. Move generation to a durable worker queue before multi-instance deployment.
8. Run Playwright flows at mobile and desktop sizes.
9. Perform an accessibility pass for focus, labels, contrast, and keyboard behavior.

The next-session deliverable should be the first-account and Mac/iPhone sync
acceptance run, or single-use invite acceptance if device testing is unavailable.
Preserve the authorization, CSRF, and export ownership matrices.

## 14. Definition of a safe handoff continuation

Before implementing a new feature, the next session should:

1. Read this document, the PRD, and the relevant milestone in the development plan.
2. Run `git status --short` and inspect recent commits.
3. Confirm PostgreSQL is running and apply `alembic upgrade head`.
4. Run the current test suite once to establish the local baseline.
5. State the exact scope and non-goals of the next change.
6. Preserve existing user data and unrelated worktree changes.
7. Update this handoff document whenever architecture, invariants, configuration, test baseline, or next priority changes.

The session is fully handed off when the code, README, this specification, migration state, tests, and Git history agree about what is implemented.
