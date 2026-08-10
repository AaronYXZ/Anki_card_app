# AI Spaced Repetition Platform PRD

## 1. Document information

| Field | Value |
|---|---|
| Product | Anki Card App |
| Version | 0.1 |
| Status | Draft for MVP implementation |
| Initial audience | Machine learning engineers preparing for interviews |
| Initial release | Invite-only private alpha |

## 2. Product summary

Anki Card App turns interview preparation notes into reviewable flashcards, asks the user to approve or edit AI-generated content, and schedules each approved card with FSRS. The product is designed for machine learning engineers who need to retain material across coding, machine learning breadth, ML system design, project stories, and behavioral interviews.

The core loop is:

```text
Markdown notes
    -> AI card generation
    -> human review and editing
    -> daily due queue
    -> active recall and rating
    -> FSRS scheduling
    -> measurable long-term retention
```

## 3. Problem

Machine learning interview preparation spans multiple knowledge types and source formats. Candidates often collect Markdown notes, code snippets, equations, interview questions, and project stories, but they lack a reliable process for:

1. Extracting atomic, self-contained knowledge units.
2. Turning those units into effective question-answer or cloze cards.
3. Reviewing each card at an appropriate time.
4. Measuring retained knowledge rather than reading activity.

Existing solutions leave a gap. Coding platforms cover practice problems but not the full interview surface. General note tools help users collect information but do not enforce active recall. Traditional flashcard tools support scheduling but make card creation and maintenance expensive.

## 4. Target user

### Primary persona

A machine learning engineer in a 4 to 12 week interview preparation cycle who:

- keeps technical and behavioral notes in Obsidian or Markdown;
- prepares across coding, ML fundamentals, ML system design, projects, and behavioral questions;
- can commit 15 to 30 minutes per day to review;
- values control over AI output and will review generated cards;
- wants a mobile-friendly daily review experience.

### Jobs to be done

When I capture useful interview knowledge, help me convert it into trustworthy atomic cards so I can recall it under interview pressure.

When I have limited study time, show me the cards most at risk of being forgotten so I do not have to plan the schedule manually.

When I prepare over several weeks, show whether my retained knowledge is improving, not merely how many cards I opened.

## 5. Product goals

### MVP goals

1. Reduce the time required to turn Markdown notes into usable flashcards.
2. Prevent unreviewed AI output from entering the learning queue.
3. Deliver a complete daily review loop with persistent history.
4. Schedule cards with FSRS-6 using standard rating semantics.
5. Measure first-attempt recall on due cards.
6. Work well on desktop and mobile browsers and be installable as a PWA.

### Non-goals for MVP

- Continuous or bidirectional Obsidian synchronization.
- Automatic reconciliation after a source note changes.
- Anki collection synchronization.
- Offline review writes and conflict resolution.
- AI grading of open-ended answers.
- Personalized FSRS parameter optimization.
- Team workspaces or shared decks.
- Weekly generated reports and adaptive quizzes.
- Native iOS or Android applications.

## 6. Product principles

1. Trust before automation. AI creates drafts; the user controls what becomes active.
2. Recall before rereading. The answer stays hidden until the user attempts retrieval.
3. Quality before volume. A small set of atomic cards is better than exhaustive duplication.
4. History is append-only. Editing content never rewrites past review events.
5. The source remains traceable. Every generated card links to its document and source excerpt.
6. Scheduling semantics remain standard. The UI may use friendly language, but backend ratings map exactly to FSRS.

## 7. MVP scope and priority

### P0. Required for private alpha

| Capability | Description |
|---|---|
| Invite-only account | Keep each user's notes, cards, and history isolated |
| Markdown import | Upload one or more `.md` files or a ZIP archive |
| Import status | Show parsing, generation, completion, and failure states |
| AI card generation | Produce Normal and Cloze draft cards as structured data |
| Source traceability | Store source file, heading, excerpt, tags, and generation version |
| Draft review | Approve, reject, edit, and bulk-approve selected cards |
| Daily queue | Select due cards first, then fill remaining capacity with new cards |
| Review interface | Show prompt, reveal answer, rate recall, and advance to the next card |
| FSRS scheduling | Update scheduling state and append a review event atomically |
| Review history | Preserve every rating and scheduling result |
| Dashboard | Show due count, completed count, first-attempt recall rate, and draft count |
| Installable PWA | Provide manifest, icons, and standalone display behavior |

### P1. After the core loop is stable

- Daily email or web push reminders.
- Search, filters, tags, and topic-level performance.
- Suspend, retire, and restore cards.
- Anki-compatible text export.
- Re-run generation for a selected document or section.
- Duplicate detection using semantic similarity.
- Weekly diagnostic quiz.

### P2. Later product expansion

- Obsidian plugin or local sync agent.
- Source change detection and card reconciliation.
- Personalized FSRS optimization.
- AI-generated weak-area practice questions.
- Weekly learning report.
- Interview plan and readiness forecast.

## 8. Primary user flows

### 8.1 Import and generation

1. User uploads Markdown files or a ZIP archive.
2. System validates file type, size, encoding, and archive safety.
3. System parses front matter, headings, prose, code blocks, lists, and equations.
4. System divides the document into coherent source chunks.
5. Generation runs asynchronously and returns structured card candidates.
6. System validates, normalizes, and deduplicates candidates.
7. Valid candidates enter the `draft` state.
8. User sees generation counts, warnings, and failures.

### 8.2 Draft review

1. User opens the draft inbox.
2. Each draft shows card type, prompt, answer or cloze, source excerpt, and tags.
3. User approves, rejects, or edits the draft.
4. Editing creates a card version and preserves the original generated payload.
5. Approval creates an FSRS scheduling state and makes the card eligible as new.

### 8.3 Daily review

1. Dashboard shows today's due count and estimated completion time.
2. User starts a session.
3. System shows one prompt without the answer.
4. User attempts recall and reveals the answer.
5. User selects Again, Hard, Good, or Easy.
6. Backend updates the FSRS card and inserts the review log in one transaction.
7. Interface shows the next card.
8. Session summary reports completed cards and first-attempt recall rate.

### 8.4 Source changes in MVP

MVP imports are immutable snapshots. Re-uploading a changed file creates a new source document version and does not update active cards automatically. The system warns about a matching path or filename. Automatic reconciliation is a P2 feature.

## 9. Functional requirements

### FR-1. Import Markdown

- Accept `.md` files and ZIP archives containing `.md` files.
- Reject executable files, path traversal, encrypted archives, and unsupported encodings.
- Preserve relative path, filename, content hash, detected timestamps, headings, and tags.
- Present imported documents by source modification time, with filename as the stable fallback when timestamp metadata is unavailable.
- Treat file timestamps as metadata only. The content hash determines whether two snapshots are identical.

Acceptance criteria:

- A valid Markdown file produces one source document and at least one source chunk.
- Uploading the same content twice does not create a second generation run unless the user explicitly requests it.
- A failed file does not prevent other valid files in the same upload from processing.
- Missing or unreliable creation timestamps do not block import.

### FR-2. Generate draft cards

- Support `normal` and `cloze` card types.
- Use concepts and explanations for Normal cards.
- Use terms, formulas, ordered steps, and precise facts for Cloze cards.
- Generate no more than one default card for the same atomic fact.
- Require structured JSON output from the model.
- Store model identifier, prompt version, input hash, latency, and validation result.
- Label any material not supported by the source as `ai_enrichment`.

Acceptance criteria:

- Every draft validates against the card schema.
- Every draft has at least one source excerpt.
- Invalid generations are retried at most once, then surfaced as a recoverable failure.
- No unapproved card is eligible for daily review.

### FR-3. Review and edit drafts

- Allow approve, reject, edit, and undo for the current session.
- Normal cards require non-empty `front` and `back`.
- Cloze cards require at least one valid `{{c1::...}}` deletion.
- Preserve original generated content after edits.
- Allow preview using the same renderer as the review screen.

Acceptance criteria:

- Approval produces an active card and initial scheduling state.
- Rejection excludes the draft from future queues without deleting provenance.
- Editing creates a new immutable card version.

### FR-4. Build the daily queue

- Default daily capacity is 25 cards and is user-configurable.
- Include all due active cards before adding new cards.
- If due cards exceed the limit, show the backlog instead of silently dropping cards.
- Fill remaining capacity with approved new cards in approval order.
- Do not include suspended, retired, draft, or rejected cards.

Acceptance criteria:

- The same card does not appear twice in one queue unless FSRS schedules an explicit same-day learning step.
- Queue construction is deterministic for the same user, time, and scheduling state, except for FSRS fuzzing.

### FR-5. Record a review

The UI uses four actions with the following backend mapping:

| UI label | Meaning | FSRS rating |
|---|---|---:|
| Again | Could not recall | 1 |
| Hard | Recalled with serious difficulty | 2 |
| Good | Recalled after hesitation | 3 |
| Easy | Recalled immediately | 4 |

- Apply the rating only after the answer is revealed.
- Store timestamps in UTC and render them in the user's timezone.
- Make repeated submission idempotent using a review attempt identifier.
- Update scheduling state and insert the review log atomically.

Acceptance criteria:

- Refreshing or retrying a request does not create duplicate review logs.
- A successful review updates the due date and session progress.
- A failed transaction changes neither the card state nor the review history.

### FR-6. Dashboard and measurement

- Show due today, overdue, new available, drafts awaiting review, and completed today.
- Show 7-day and 30-day first-attempt recall rates.
- Show review time and cards completed per minute as secondary metrics.
- Do not present total review count as evidence of learning success.

### FR-7. Installable PWA

- Include a web app manifest, application icons, theme color, and standalone display mode.
- Provide an in-product installation guide for iOS and desktop.
- Cache the application shell and static assets.
- Do not claim offline review support in MVP.

## 10. Card generation policy

### Normal card

Use when the learner must explain a concept, causal relationship, decision, trade-off, or procedure.

Example:

```text
Front: What is statistical power, and why does it matter in an A/B test?
Back: Statistical power is the probability of detecting a real effect of a specified size. Low power makes a non-significant result inconclusive because the experiment may not have collected enough data.
```

### Cloze card

Use when the learner must recall a precise term, formula component, ordered step, or compact list.

Example:

```text
Statistical power equals {{c1::1 minus the Type II error rate}}.
```

### Quality checks

Each draft should be:

- atomic;
- answerable without the full source;
- faithful to the source;
- concise enough for active recall;
- free of duplicate knowledge targets;
- rendered correctly for Markdown, HTML, code, and MathJax;
- explicit about AI-added context.

The application stores structured cards internally. Pipe-delimited text is generated only during Anki export.

## 11. Scheduling policy

- Use FSRS-6 through the maintained Python implementation.
- Default desired retention is 0.90.
- Use standard Again, Hard, Good, and Easy meanings.
- Store the FSRS algorithm version and parameters used for each scheduling update.
- Keep scheduler state separate from editorial card content.
- Do not store or expose SM-2 Ease Factor as an FSRS concept.
- Start with default FSRS parameters. Personalized optimization is out of scope until sufficient review history exists.

## 12. Data model

### Core entities

| Entity | Purpose | Important fields |
|---|---|---|
| User | Ownership and preferences | timezone, daily limit, desired retention |
| SourceDocument | Imported source snapshot | path, filename, hash, raw content, imported time |
| SourceChunk | Traceable generation unit | heading path, sequence, text, token estimate |
| GenerationRun | AI execution record | prompt version, model, status, input hash, errors |
| Card | Stable card identity | type, lifecycle state, source document, current version |
| CardVersion | Immutable editorial content | front, back, cloze text, extra, editor, created time |
| SchedulingState | Current FSRS state | due, stability, difficulty, state, step |
| ReviewLog | Append-only review event | rating, review time, elapsed days, prior and new state |
| ReviewSession | Daily session summary | started time, completed time, queue size |

### Card lifecycle

```text
draft -> approved -> active -> suspended -> active
   |                    |
   -> rejected          -> retired
```

`approved` represents the transition that initializes scheduling. The steady learning state is `active`.

### Data invariants

- Every row containing user data has a `user_id` or inherits ownership through a constrained parent.
- Review logs are append-only.
- One card has one current version and may have many historical versions.
- One active card has exactly one current scheduling state.
- All application timestamps are timezone-aware UTC.
- Source content and generated content are never silently overwritten.

## 13. Metrics and analytics

### North-star metric

**30-day due-review first-attempt recall rate**

```text
successful first attempts on due reviews during the last 30 days
divided by
all first attempts on due reviews during the last 30 days
```

Hard, Good, and Easy count as successful recall. Again counts as failure. Same-day relearning attempts do not enter this metric.

### Supporting product metrics

| Funnel stage | Metric |
|---|---|
| Import | Percentage of uploaded documents parsed successfully |
| Generation | Valid draft cards per document and generation failure rate |
| Trust | Draft approval rate and edit rate |
| Activation | Users completing one approved card and one review session within 24 hours |
| Habit | Percentage of active days with all due cards completed |
| Efficiency | Successful recalls per minute |
| Retention | 7-day and 30-day active learner retention |
| Quality | Card suspension and retirement rate after first review |

### Required events

- `source_uploaded`
- `source_processed`
- `generation_completed`
- `draft_approved`
- `draft_edited`
- `draft_rejected`
- `review_started`
- `answer_revealed`
- `card_rated`
- `review_session_completed`
- `card_suspended`

## 14. Weekly diagnostic quiz proposal

The weekly quiz is P1 and optional. It samples mature cards that have not been reviewed recently and records first-attempt performance. It should not affect scheduling until its predictive value is validated.

Initial constraints:

- sample 10 to 15 mature cards;
- exclude cards reviewed in the previous 48 hours;
- show no hints before the first response;
- keep quiz results separate from FSRS review logs;
- compare quiz recall with predicted retrievability;
- allow the user to skip the quiz without penalty.

## 15. Privacy, security, and reliability

- Treat imported notes as private user data.
- Encrypt network traffic and use a managed secret store in production.
- Never place source content, prompts, or model responses in application logs by default.
- Make AI provider retention behavior visible before first import.
- Provide source and card deletion with clearly defined cascading behavior.
- Validate archive extraction paths and enforce upload size limits.
- Sanitize rendered Markdown and model-generated HTML.
- Back up the production database and test restoration.
- Record generation and scheduling errors with identifiers that do not expose note content.

## 16. Main product trade-offs

| Decision | MVP choice | Accepted cost |
|---|---|---|
| AI publishing | Human approval required | More user effort, higher trust |
| Obsidian access | Manual file or ZIP upload | No automatic freshness |
| Review ratings | Standard four-button FSRS scale | Slightly more choice for users |
| Queue priority | Due cards before new cards | Slower intake of new material |
| PWA offline behavior | Installable, online writes only | No offline review sessions |
| Source updates | Immutable import snapshots | Duplicate review work after major edits |
| AI breadth | Quality-limited candidates | Less exhaustive coverage |
| Scheduling | Default FSRS parameters | No early personalization |

## 17. Release criteria

Private alpha can launch when:

1. A user can upload a Markdown file, approve one generated card, and complete a scheduled review.
2. Normal and Cloze rendering pass desktop and mobile checks.
3. Review writes are atomic and idempotent.
4. All cards and review logs are isolated by user.
5. At least 90 percent of candidates in the evaluation set pass schema validation.
6. No critical security finding remains open for uploads or rendered content.
7. Database backup and restoration have been exercised once.
8. Product events support the north-star metric.

## 18. Open decisions

- Exact identity provider for invite-only authentication.
- AI model provider, cost ceiling, and data retention requirements.
- Maximum file, archive, and generation batch sizes.
- Whether code-heavy notes should produce code-execution questions in a later version.
- Hosting environment and background job infrastructure.
- Retention and deletion policy for original source documents.

## 19. Technical references

- [Py-FSRS](https://github.com/open-spaced-repetition/py-fsrs)
- [FastAPI background tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/)
- [MDN guide to installable PWAs](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Making_PWAs_installable)
