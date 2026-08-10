# Anki Card App

An AI-assisted spaced-repetition platform for machine learning interview preparation. It turns Markdown and Obsidian notes into reviewable flashcard drafts, keeps the learner in control of approval and editing, and schedules approved cards with FSRS-6.

## Product status

The local MVP core loop is working:

```text
Markdown note
    -> AI-generated drafts
    -> human approval or editing
    -> daily review queue
    -> Again, Hard, Good, or Easy rating
    -> persistent FSRS schedule and review history
```

Implemented capabilities:

- import one Markdown file or a ZIP containing Markdown files;
- track imported note snapshots and their generated cards;
- split notes into heading-aware source chunks;
- generate Normal, Cloze, and Skeleton Recall cards;
- choose Terra or Luna for each import;
- show per-chunk generation progress and actionable provider errors;
- resume failed or stalled generation without duplicating successful cards;
- review, edit, approve, or reject every draft before it enters learning;
- create a due-first daily queue with a stored per-user limit;
- persist idempotent FSRS-6 reviews and complete scheduling history;
- show daily counts and a 30-day first-attempt recall metric;
- install the interface as a PWA on supported mobile and desktop browsers;
- use `Space` to reveal an answer and `1` through `4` to rate it.

This is not yet private-alpha ready. The current build uses one fixed development user. Authentication, durable background jobs, production deployment, content sanitization, notification delivery, backups, and full mobile or accessibility acceptance testing remain open.

## Technology

| Layer | Choice |
|---|---|
| Application | Python 3.12, FastAPI |
| UI | Server-rendered Jinja, CSS, targeted JavaScript |
| Database | PostgreSQL, SQLAlchemy 2, Alembic |
| Scheduling | Py-FSRS, FSRS-6 |
| AI generation | OpenAI Responses API with structured output |
| Packaging | `uv` and `pyproject.toml` |
| Testing | Pytest, Ruff, Mypy, coverage |

## Local setup

Requirements:

- `uv`
- Docker with Compose
- an OpenAI API key for AI card generation

```bash
cp .env.example .env
docker compose up -d postgres
uv sync --all-groups
uv run alembic upgrade head
uv run uvicorn anki_card_app.main:app --reload
```

Add the API key to `.env` without quotes:

```dotenv
OPENAI_API_KEY=your_api_key_here
```

Do not commit `.env`. Restart the application after changing environment variables.

Open these local pages:

- App: [http://localhost:8000](http://localhost:8000)
- Import notes: [http://localhost:8000/imports/new](http://localhost:8000/imports/new)
- Imported notes ledger: [http://localhost:8000/notes](http://localhost:8000/notes)
- Draft review: [http://localhost:8000/cards/drafts](http://localhost:8000/cards/drafts)
- Daily review: [http://localhost:8000/review](http://localhost:8000/review)
- PWA installation: [http://localhost:8000/install](http://localhost:8000/install)
- Health check: [http://localhost:8000/health](http://localhost:8000/health)

## Use the product

### Generate cards from a note

1. Set `OPENAI_API_KEY` and restart the service.
2. Open `/imports/new`.
3. Choose Terra or Luna.
4. Upload a `.md` file or a ZIP archive.
5. Watch each source chunk move from pending to completed or failed.
6. Open Drafts and approve, edit, or reject the generated cards.

Generation progress is committed after every chunk. The run page refreshes every five seconds. Each provider request has a 90-second timeout, and the workflow retries a recoverable failed chunk once. Authentication, unavailable-model, and exhausted-credit errors stop the run. After fixing the problem, select **Resume generation**. The new run reuses the imported source and avoids duplicate cards through content fingerprints.

### Review cards

1. Approve at least one draft.
2. Open `/review`.
3. Attempt recall before revealing the answer.
4. Select Again, Hard, Good, or Easy.
5. Complete the queue and inspect the session summary.

The rating mapping is standard FSRS: Again `1`, Hard `2`, Good `3`, Easy `4`. Review submission uses a unique attempt identifier so a retry cannot create a second review event.

### Install the PWA

Open `/install` for platform-specific instructions. The application shell and static assets are cached. Imports, approvals, ratings, and all other data writes require a connection to the server. Offline review writes are intentionally unsupported.

## Configuration

See [.env.example](.env.example) for every setting. The most important values are:

| Variable | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | PostgreSQL connection | local Compose database on port 5433 |
| `OPENAI_API_KEY` | Enables card generation | empty |
| `OPENAI_MODEL` | Default import model | `gpt-5.6-terra` |
| `OPENAI_TIMEOUT_SECONDS` | Provider request timeout | `90` |
| `MAX_UPLOAD_BYTES` | Uploaded file limit | `10000000` |
| `MAX_ARCHIVE_FILES` | ZIP entry limit | `250` |
| `MAX_ARCHIVE_UNCOMPRESSED_BYTES` | Expanded ZIP limit | `50000000` |

Terra and Luna are the only models exposed by the current import form. Confirm API access and pricing before changing this allowlist.

## Quality checks

```bash
uv run ruff format --check src tests migrations
uv run ruff check src tests migrations
uv run mypy src
uv run pytest --cov=anki_card_app --cov-report=term-missing
```

Current verified baseline at commit `6d7f7b8`:

- 71 tests passing;
- 95.48 percent total coverage;
- Ruff, Mypy, JavaScript syntax, manifest parsing, and live PWA endpoint checks passing.

## Important constraints

- Imported notes are immutable snapshots. Source-file change detection and automatic card reconciliation are deferred.
- AI output is always a draft. It never enters the review queue without user approval.
- Generation currently runs in FastAPI in-process background tasks. A process restart can interrupt it.
- One fixed development account owns all local data. Do not deploy this build for multiple users.
- Static assets work offline, but database writes do not.
- The application does not synchronize with Anki or scan an arbitrary Obsidian directory.

## Documentation

- [Product requirements](docs/PRD.md)
- [Development plan](docs/DEVELOPMENT_PLAN.md)
- [Session handoff specification](docs/SESSION_HANDOFF.md)

The handoff specification is the canonical starting point for the next development session. It records the current architecture, invariants, known gaps, validation state, and recommended next task.
