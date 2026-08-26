# Anki Card App

[简体中文](README-CN.md)

An AI-assisted spaced-repetition system for machine learning interview preparation. It
turns Markdown or Obsidian notes into card drafts, keeps approval under user control, and
schedules approved cards with FSRS-6.

Production: <https://web-production-a42e0.up.railway.app>

## Current status

The core workflow is deployed and has passed Mac and iPhone synchronization acceptance:

```text
Markdown notes
    -> AI-generated drafts
    -> human approval, editing, or rejection
    -> daily review queue
    -> Again, Hard, Good, or Easy
    -> PostgreSQL review history and FSRS scheduling
```

The current version supports Markdown and ZIP imports, Normal/Cloze/Skeleton Recall cards,
syntax highlighting, LaTeX formula rendering, source-ordered draft review, approved-card
editing, FSRS-6 scheduling, complete JSON backup and restore, and PWA installation. All
learning-data routes require authentication, and every write is protected by CSRF validation.

## Installation and use

End users do not need to download the source code. The website and installed PWA use the
same cloud account and database.

### iPhone

1. Open the production URL in Safari and sign in.
2. Tap Safari's Share button.
3. Select **Add to Home Screen**.
4. Open Anki Card App from the Home Screen.

### Mac

Use the app directly in Safari or another browser, or install it as a PWA:

1. Open the production URL in Safari and sign in.
2. Select **File > Add to Dock**.
3. Open the app from the Dock or Applications.

The Mac website, Mac PWA, and iPhone PWA have the same features. The current version needs
a network connection to import, approve, edit, or review. Static pages may be cached, but
offline review writes are not implemented.

### First use

1. Open **Create > Import** and upload a Markdown file or a ZIP containing Markdown files.
2. Wait for AI card-draft generation to finish.
3. Open **Modify > Drafts** and approve, edit, or reject the drafts.
4. Open **Review** and complete the day's queue.
5. Open **Utils > Export** and download the first JSON backup.

## Local development setup

### Requirements

| Tool | Purpose |
|---|---|
| Python 3.12+ | Application runtime |
| `uv` | Python dependency and command management |
| Docker Compose | Local PostgreSQL |
| OpenAI API key | Required only for AI card generation |

### Start the application

```bash
cp .env.example .env
docker compose up -d postgres
uv sync --all-groups
uv run alembic upgrade head
uv run uvicorn anki_card_app.main:app --reload
```

Open <http://localhost:8000>.

To enable AI generation, add the key to `.env` without quotes:

```dotenv
OPENAI_API_KEY=your_api_key_here
```

Restart the application after changing `.env`. The file contains secrets and must never be
committed. Manual card creation, review, export, and restore remain available without an API
key.

Local development defaults to `AUTH_MODE=development`. To test password authentication:

```bash
uv run anki-card-admin create-user --email you@example.com
```

Then update `.env` and restart the application:

```dotenv
AUTH_MODE=password
SESSION_COOKIE_SECURE=false
```

Passwords must contain at least 12 characters. Change a password with:

```bash
uv run anki-card-admin set-password --email you@example.com
```

## Project structure

```text
Anki_card_app/
├── src/anki_card_app/
│   ├── app.py                 # FastAPI app, static resources, and PWA routes
│   ├── main.py                # ASGI entry point
│   ├── models.py              # SQLAlchemy data model
│   ├── database.py            # Database connection and readiness check
│   ├── web.py                 # Card, draft, and review pages
│   ├── imports_web.py         # Note upload and generation tasks
│   ├── notes_web.py           # Imported Notes pages
│   ├── generation.py          # Structured OpenAI card generation
│   ├── import_service.py      # Markdown/ZIP parsing and chunking
│   ├── card_service.py        # Card versions and lifecycle transitions
│   ├── review_service.py      # Daily queue and atomic review writes
│   ├── fsrs_adapter.py        # FSRS-6 state transitions
│   ├── export_service.py      # JSON backup creation
│   ├── restore_service.py     # JSON validation and atomic restore
│   ├── auth*.py               # Login, sessions, and identity isolation
│   ├── security.py            # CSRF, CSP, and security headers
│   ├── templates/             # Jinja pages
│   └── static/                # CSS, JavaScript, PWA resources, and icons
├── migrations/                # Alembic database migrations
├── tests/                     # Unit, service, and web tests
├── docs/                      # PRD, development plan, and operations guides
├── compose.yaml               # Local PostgreSQL
├── railway.json               # Railway build, migration, and health checks
└── pyproject.toml             # Dependencies, tests, and quality configuration
```

Core layers:

```text
Jinja pages and targeted JavaScript
                |
                v
           FastAPI routes
                |
                v
 Services: import / generation / card / review / restore
                |
                v
 SQLAlchemy 2 + PostgreSQL + FSRS state
```

The UI never accesses the database directly. Scheduling runs on the server, so Mac, iPhone,
and future clients share the same learning rules.

## Technology stack

| Layer | Technology | Responsibility |
|---|---|---|
| Backend | Python 3.12, FastAPI, Uvicorn | Pages, authentication, and business routes |
| Frontend | Jinja, CSS, targeted JavaScript | Lightweight server-rendered PWA |
| Data | PostgreSQL, SQLAlchemy 2, Alembic | Cloud state and database migrations |
| Scheduling | Py-FSRS, FSRS-6 | Next-review calculation |
| AI | OpenAI Responses API, Pydantic structured output | Structured drafts from notes |
| Content | markdown-it-py, Pygments, latex2mathml | Safe Markdown, syntax highlighting, and MathML formulas |
| Engineering | uv, Pytest, Ruff, Mypy, coverage | Dependencies, tests, and static checks |
| Deployment | Railway, Railpack, GitHub | Web service, PostgreSQL, and releases |

## How Mac and iPhone share data

```text
Mac Browser / PWA ----\
                       \
iPhone Safari / PWA ---- HTTPS ---> FastAPI ---> Railway PostgreSQL
                       /
Other Browser --------/
```

Synchronization has four essential properties:

1. Both devices sign in to the same production account.
2. PostgreSQL stores every card, draft, review event, and schedule.
3. Every approval, edit, and rating is sent to FastAPI and written on the server.
4. The other device reads the latest state from the same PostgreSQL database after refresh.

Devices do not exchange files directly and do not depend on iCloud for application-state
synchronization. Each device has an independent login session, so signing out on one device
does not automatically sign out the other.

A review transaction stores both the review log and the updated FSRS schedule atomically.
An `attempt_id` prevents a network retry from recording the same rating twice. The current
version does not have IndexedDB offline writes, conflict resolution, or background sync. Do
not continue reviewing while offline.

Daily review totals reset at midnight in `America/Los_Angeles`. The IANA timezone handles
Pacific Daylight Time, UTC-7, and Pacific Standard Time, UTC-8, automatically. Database
timestamps remain stored in UTC.

See [Railway Deployment and Sync Acceptance](docs/RAILWAY_DEPLOYMENT.md) for the deployment
and acceptance runbook.

## Data backup and restore

### Recommended low-cost approach

The current Railway dashboard exposes native Backups and PITR only on the Pro plan. Users on
other plans should use the application's portable JSON backup. Do not delete, recreate, or
overwrite the production database to test a restore.

Create a backup:

1. Sign in to production.
2. Open **Utils > Export**.
3. Download a file such as `anki-card-app-2026-08-17.json`.
4. Store it in private iCloud Drive, an encrypted disk, or another protected location.
5. Export weekly and after large imports or editing sessions.

The JSON backup contains:

| Data | Included |
|---|---|
| Imported notes and chunks | Yes |
| Generation records, drafts, cards, and every card version | Yes |
| FSRS schedules, review sessions, and review logs | Yes |
| User timezone and learning preferences | Yes |
| Password hash, session token, and OpenAI API key | No |

The file still contains the account email, source notes, and learning content. Treat it as
private data and never commit it to GitHub.

### Restore a JSON backup

1. Sign in to an empty account with no learning data.
2. Open **Utils > Restore**.
3. Select a version 1 JSON file exported by this application.
4. Confirm the empty-account warning and start the restore.
5. Verify notes, drafts, cards, due state, and review history.

Restore fully validates the file before writing everything in one database transaction. An
error rolls back the transaction, so a partial restore is never retained. Restore keeps the
signed-in account's email, password, and sessions while importing learning data and
preferences.

Important limitation: restore accepts only an empty account. Merge, selective restore, and
overwrite restore are not implemented.

### Railway Pro

If the project later upgrades to Railway Pro, enable native volume snapshots and PITR from
the PostgreSQL service's **Backups** page. Keep portable JSON exports even with native
backups because JSON is easier to migrate to another provider.

## Core product rules

| Rule | Current behavior |
|---|---|
| AI generation | Produces drafts only and never enters review automatically |
| Review history | Append-only, with no overwritten historical ratings |
| Card edits | Append an immutable CardVersion |
| Daily queue | Targets at least 10 Normal and 3 Skeleton Recall when possible |
| Skeleton prompt | Bold only when explicitly marked with Markdown |
| Markdown | Rejects raw HTML and unsafe links |
| Math | Converts LaTeX to allowlisted MathML on the server |
| Offline | Caches static resources but rejects offline writes |

## Markdown and formulas

Drafts, approved cards, and review cards use the same renderer. Use `$...$` for an inline
formula and `$$...$$` for a standalone formula:

```text
The adjusted value is $Y_{adj}$.

$$
Y_{adj} = Y - \theta \cdot (X - \bar{X})
$$
```

The `\(...\)` and `\[...\]` delimiters are also supported. A standalone line containing
LaTeX commands is recognized without delimiters, so this line also renders as a formula:

```text
Y_{adj} = Y - \theta \cdot (X - \bar{X})
```

Imported notes retain their existing delimiters. AI-generated cards are instructed to wrap
new formulas in delimiters. Code spans and fenced code blocks remain code and are never
interpreted as formulas.

## Configuration

See [.env.example](.env.example) for every setting. Production requires at least:

```dotenv
APP_ENV=production
APP_DEBUG=false
AUTH_MODE=password
SESSION_COOKIE_SECURE=true
DATABASE_URL=${{Postgres.DATABASE_URL}}
OPENAI_API_KEY=stored_in_railway_secrets
OPENAI_MODEL=gpt-5.6-terra
```

Railway runs `alembic upgrade head` before deployment and uses `/ready` to verify
PostgreSQL. A new release receives traffic only after the database is reachable. PostgreSQL
remains private, and the web service obtains its connection string through a Railway
reference variable.

## Tests

```bash
uv run ruff format --check src tests migrations
uv run ruff check src tests migrations
uv run mypy src
uv run pytest --cov=anki_card_app --cov-report=term-missing
node --check src/anki_card_app/static/app.js
node --check src/anki_card_app/static/service-worker.js
```

Current baseline: 115 tests passing with 93.25 percent total coverage.

## Known limitations

| Area | Limitation |
|---|---|
| AI tasks | FastAPI in-process background tasks can be interrupted by deployment |
| Imports | Notes are immutable snapshots, with no automatic Obsidian folder scanning |
| Restore | Empty accounts only, with no merge restore |
| Sync | Network connection required, with no offline writes or conflict handling |
| Accounts | No self-service registration, password recovery, or account deletion yet |

## Documentation

- [Product requirements](docs/PRD.md)
- [Development plan](docs/DEVELOPMENT_PLAN.md)
- [Session handoff specification](docs/SESSION_HANDOFF.md)
- [Authentication decision and threat model](docs/AUTHENTICATION.md)
- [Railway deployment and sync acceptance](docs/RAILWAY_DEPLOYMENT.md)

Read `docs/SESSION_HANDOFF.md` before continuing development. It records the current
deployment, data invariants, test baseline, and next task.
