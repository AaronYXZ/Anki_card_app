# Development Plan

## 1. Delivery strategy

Build the smallest complete vertical slice first:

```text
one Markdown file
    -> one generated draft
    -> one approved card
    -> one review
    -> one persisted FSRS update
```

Do not begin automated Obsidian synchronization, weekly reports, or advanced analytics before this path is reliable.

For a single builder, the private-alpha scope is approximately 5 to 7 focused development weeks. This is a planning range, not a commitment. AI provider integration, authentication, and deployment constraints can change it substantially.

## 2. Proposed technical baseline

| Layer | Initial choice | Rationale |
|---|---|---|
| Language | Python 3.12+ | Strong parsing, AI, and backend ecosystem |
| Web framework | FastAPI | Typed HTTP APIs and simple background processing |
| UI | Jinja templates, HTMX, small targeted JavaScript | Fast server-rendered MVP with limited client complexity |
| Database | PostgreSQL | Multi-user constraints and production-safe transactions from day one |
| ORM and migrations | SQLAlchemy 2 and Alembic | Explicit models and reversible schema evolution |
| Scheduler | Py-FSRS, FSRS-6 | Maintained Python scheduler with review logs and serialization |
| Markdown | Python Markdown parser plus HTML sanitizer | Preserve structured source while rendering safely |
| Tests | Pytest and Playwright | Unit, integration, and mobile-sized browser flows |
| Packaging | `uv` and `pyproject.toml` | Reproducible Python environment and fast workflows |
| Deployment | Docker-compatible web and worker services | Avoid coupling the MVP to one hosting vendor |

### Architecture boundary

```text
Browser or installed PWA
        |
        v
FastAPI web application
        |
        +-- PostgreSQL
        +-- object or file storage for source snapshots
        +-- AI provider adapter
        +-- background generation worker
        +-- Py-FSRS scheduling service
```

FastAPI `BackgroundTasks` is acceptable for early local development and small private-alpha jobs. Before multi-instance production deployment, generation should move to a durable queue because in-process tasks can be lost when a process restarts.

## 3. Engineering rules

1. Build multi-user ownership into the schema even if the first tester is the owner.
2. Keep domain logic outside HTTP handlers.
3. Treat generation output as untrusted input and validate it with typed schemas.
4. Keep editorial card content separate from scheduling state.
5. Append review logs and card versions. Never update historical rows in place.
6. Make review submission idempotent.
7. Store UTC in the database and convert only at the presentation boundary.
8. Keep external AI and notification providers behind interfaces.
9. Add product events in the same milestone as the feature they measure.
10. Do not depend on a local absolute Obsidian path in application code.

## 4. Milestones

### Implementation status on 2026-08-10

| Milestone | Status | Remaining work |
|---|---|---|
| 0. Foundation | Complete locally | Production CI and deployment validation |
| 1. Manual vertical slice | Complete locally | Authorization matrix must remain current as routes are added |
| 2. Import and generation | Complete locally | Durable queue and formal AI evaluation corpus |
| 3. FSRS daily review | Complete locally | Production PostgreSQL concurrency exercise |
| 4. Dashboard and PWA | Core implementation complete | Product-event instrumentation, mobile browser flow, accessibility pass |
| 5. Private alpha | In progress | Invite acceptance, rate limits, operations, privacy, and deployment |

The canonical continuation context is [Session Handoff Specification](SESSION_HANDOFF.md).

## Milestone 0. Foundation

Estimated duration: 2 to 3 days.

### Deliverables

- Python project with `pyproject.toml` and locked dependencies.
- FastAPI application factory and configuration management.
- PostgreSQL development environment.
- SQLAlchemy session management and Alembic.
- Formatting, linting, typing, and test commands.
- CI running unit tests and static checks.
- `.env.example` with no secrets.
- Basic application health endpoint.

### Exit criteria

- A new contributor can start the application from the README.
- Database migrations run from an empty database.
- CI passes on `main`.
- No production secret is committed.

## Milestone 1. Domain model and manual vertical slice

Estimated duration: 4 to 5 days.

### Deliverables

- User, SourceDocument, SourceChunk, Card, CardVersion, SchedulingState, ReviewLog, and ReviewSession tables.
- Card lifecycle transitions with domain-level validation.
- Manual card creation for Normal and Cloze cards.
- Draft inbox with approve, edit, and reject.
- Basic review screen using a fixed test queue.
- Audit timestamps and user ownership constraints.

### Tests

- Lifecycle transition unit tests.
- Cross-user access denial integration tests.
- Card version immutability tests.
- Cloze syntax validation tests.

### Exit criteria

- A manually created draft can be approved and rendered in a review session.
- Editing creates a new version and preserves the old one.
- Rejected cards never enter the review queue.

## Milestone 2. Markdown import and AI generation

Estimated duration: 5 to 7 days.

### Deliverables

- Single-file and ZIP upload.
- Safe archive extraction with size and path limits.
- Markdown parser preserving headings, code blocks, equations, and tags.
- Content hashing and idempotent import behavior.
- Source chunking by heading and size.
- Versioned Normal and Cloze generation prompts.
- AI provider interface and one initial adapter.
- Structured response schema and validation.
- Generation run status UI.
- Exact-text duplicate detection.
- Source excerpt shown beside every draft.

### Tests

- Fixture corpus containing prose, code, tables, equations, Unicode, and malformed Markdown.
- Archive path traversal and oversized upload tests.
- Golden generation tests with recorded model responses.
- Retry and partial-failure integration tests.
- Prompt and output schema snapshot tests.

### Exit criteria

- The A/B testing sample note can be imported successfully.
- Valid drafts preserve source order and provenance.
- A failed chunk can be retried without duplicating successful drafts.
- At least 90 percent of evaluation candidates pass schema validation.

## Milestone 3. FSRS scheduling and daily review

Estimated duration: 5 to 7 days.

### Deliverables

- Py-FSRS scheduling adapter.
- Initial scheduling state on approval.
- Daily queue query with due-first and new-card fill behavior.
- Reveal-answer interaction.
- Again, Hard, Good, and Easy controls.
- Atomic scheduling update and review-log insert.
- Idempotency key for review submissions.
- Learning and relearning steps.
- Session completion summary.
- Timezone-aware due-date presentation.

### Tests

- Known rating-sequence tests against Py-FSRS outputs.
- Due, overdue, new, suspended, and retired queue tests.
- Concurrent and repeated submission tests.
- Transaction rollback test.
- UTC boundary and daylight-saving presentation tests.

### Exit criteria

- A complete rating sequence produces expected state transitions.
- Duplicate HTTP requests create one review event.
- Due cards are never displaced by new cards within the same daily limit.
- Review history can reconstruct every scheduling change.

## Milestone 4. Dashboard, metrics, and installable PWA

Estimated duration: 4 to 5 days.

### Deliverables

- Dashboard counts for due, overdue, new, drafts, and completed.
- Review duration and first-attempt recall metrics.
- Required product event instrumentation.
- Web app manifest, icons, and standalone mode.
- Static application-shell caching.
- Responsive review interface for phone and desktop.
- Keyboard shortcuts with visible button alternatives.
- iOS and desktop installation instructions.

### Tests

- Metric calculation tests excluding same-day relearning attempts.
- Mobile viewport end-to-end review flow.
- Manifest and icon checks.
- Accessibility checks for focus order, contrast, labels, and keyboard use.

### Exit criteria

- The north-star metric can be computed from production events and review logs.
- The application is installable on a supported phone and desktop browser.
- No screen claims that offline review writes are supported.

## Milestone 5. Authentication, hardening, and private alpha

Estimated duration: 5 to 7 days.

### Deliverables

- Invite-only authentication and account lifecycle.
- Authorization review for every user-owned route.
- HTML sanitization and content security policy.
- Rate and cost limits for import and generation.
- Structured error reporting without source content.
- Database backup and restoration procedure.
- Source and card deletion flow.
- Privacy notice for AI processing.
- Operational dashboard for failed generation jobs.
- Private-alpha onboarding and feedback form.

### Tests

- Authorization matrix tests.
- Upload and rendered-content security tests.
- Backup restoration exercise.
- End-to-end test from account creation through first completed review.
- Manual browser checks on iPhone and desktop.

### Exit criteria

- All PRD release criteria pass.
- Five invited users can complete onboarding without developer intervention.
- Critical and high-severity security defects are closed.
- Generation cost per imported document is observable.

## 5. First implementation backlog

Complete these issues in order:

1. Create Python package, FastAPI app, and test harness.
2. Add PostgreSQL development configuration.
3. Add SQLAlchemy models and initial Alembic migration.
4. Implement Normal and Cloze domain validation.
5. Implement card lifecycle transitions.
6. Build manual draft inbox and editor.
7. Build static review screen for an approved card.
8. Add Markdown upload and source snapshot persistence.
9. Add parser and chunker with fixtures.
10. Add structured AI generation interface.
11. Add generation status and draft provenance UI.
12. Add Py-FSRS adapter and scheduling persistence.
13. Add daily queue and atomic review endpoint.
14. Add dashboard metrics.
15. Add manifest, icons, and responsive UI.
16. Add authentication and private-alpha hardening.

## 6. Test strategy

### Unit tests

- Card schema and lifecycle.
- Markdown chunk boundaries.
- Cloze validation.
- Rating mapping.
- Metric calculations.
- Queue selection.

### Integration tests

- Database constraints and transactions.
- Import idempotency.
- AI response validation and retry.
- FSRS state serialization and restoration.
- Authorization boundaries.

### End-to-end tests

- Upload to approved card.
- Approved card to completed review.
- Failed generation to retry.
- Mobile review session.
- Duplicate review submission.

### AI quality evaluation

Maintain a small, versioned evaluation corpus with representative MLE content:

- ML concepts;
- code blocks;
- system design trade-offs;
- equations;
- behavioral stories;
- tables and lists;
- mixed Chinese and English notes.

Score generations for source faithfulness, atomicity, self-containment, duplication, card-type choice, and rendering validity. Prompt changes must be evaluated against the corpus before release.

## 7. Product validation plan

### Before private alpha

- Interview 5 to 10 machine learning engineers in active preparation.
- Observe at least three real note-to-study workflows.
- Measure current weekly time spent creating study material.
- Test whether users understand the four ratings consistently.
- Run a moderated prototype test for import, approval, and review.

### Private-alpha hypotheses

| Hypothesis | Signal |
|---|---|
| AI reduces card creation effort | Median time from import to 10 approved cards |
| Human review creates sufficient trust | Draft approval and edit rates |
| Users can sustain the daily loop | Due-card completion on active days |
| Scheduling improves retained knowledge | 30-day due-review first-attempt recall rate |
| The workflow fits interview preparation | 7-day and 30-day learner retention |

Do not set target thresholds until baseline data from the first cohort exists. Establish the baseline, inspect failure modes, then set a target for the next cohort.

## 8. Deferred design work

### Obsidian synchronization

The hosted application cannot scan an arbitrary local folder. A later version must choose one of:

- an Obsidian plugin that pushes selected notes;
- a local sync agent;
- Git-backed note synchronization;
- a supported cloud-drive connector;
- a desktop application.

Source reconciliation should combine document hashes, heading paths, chunk hashes, and explicit user approval. It must never overwrite active card content or review history silently.

### Weekly quiz

Implement only after normal review metrics are trustworthy. Quiz results remain separate from FSRS logs until an experiment shows that they improve learning measurement or scheduling.

### Personalized scheduling

Consider parameter optimization only after each user has enough review history for stable estimation. Preserve the parameter version used for every scheduling event.

## 9. Definition of done

A feature is done when:

- acceptance criteria pass;
- unit and integration tests cover the main success and failure paths;
- user ownership and authorization are enforced;
- analytics events are emitted and documented;
- mobile and keyboard interaction have been checked;
- errors are actionable and do not leak private note content;
- schema changes include a migration and rollback plan;
- relevant PRD and README sections are updated.

## 10. Immediate next action

The authentication, authorization, Session-bound CSRF, restrictive CSP, and
escaped text-rendering slices are complete. Continue Milestone 5 with the next
private-alpha operations slice:

- implement single-use invite acceptance instead of administrator-shared passwords;
- add login rate limits and security event logging without private content;
- resolve card deletion semantics so review history cannot be silently cascaded away.
- document and exercise database backup restoration;
- prepare Railway deployment configuration and production acceptance checks.

Do not add notifications, weekly quizzes, or automatic Obsidian synchronization before the ownership boundary is verified.
