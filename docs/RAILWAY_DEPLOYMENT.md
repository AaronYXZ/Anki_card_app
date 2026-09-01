# Railway Deployment and Sync Acceptance

## Current production deployment

The production service is available at
<https://web-production-a42e0.up.railway.app>. The deployment from commit
`b4b3f29` passed the Alembic migration gate through `20260827_0007`, the
`/ready` PostgreSQL check, and Railway's service health check on 2026-08-27.
The authenticated restore route and persistent review-card favorites are live;
anonymous learning-data requests redirect to login.

The first deployment was uploaded with `railway up`. GitHub autodeploy remains
pending until the Railway GitHub App is granted access to
`AaronYXZ/Anki_card_app` and the web service source is reconnected to `dev`.

## Scope

This runbook deploys one FastAPI web replica and one Railway PostgreSQL service.
PostgreSQL is the shared source of truth for Mac, iPhone, and browser sessions.
The PWA remains online-only for writes. IndexedDB and offline conflict resolution
are not part of this deployment.

Keep one web replica while generation still uses FastAPI in-process background
tasks. Moving generation to a durable queue is required before multi-replica use.

## Repository and service setup

1. Push the `dev` branch to GitHub.
2. Create a Railway project and add a PostgreSQL service.
3. Add a web service from the GitHub repository and select the `dev` branch.
4. Generate a public domain for the web service.
5. Keep the PostgreSQL service private. The application uses its reference
   variable and does not need the database TCP proxy.

`railway.json` configures Railpack, runs `alembic upgrade head` before each
deployment, starts Uvicorn on Railway's injected `PORT`, and uses `/ready` as the
deployment health check. `/ready` returns `200` only after `SELECT 1` succeeds.

## Web service variables

Set these variables on the web service:

```dotenv
APP_ENV=production
APP_DEBUG=false
AUTH_MODE=password
SESSION_COOKIE_SECURE=true
SESSION_COOKIE_NAME=anki_session
CSRF_COOKIE_NAME=anki_csrf
SESSION_LIFETIME_DAYS=30
DATABASE_URL=${{Postgres.DATABASE_URL}}
OPENAI_API_KEY=replace_in_railway_secret_store
OPENAI_MODEL=gpt-5.6-terra
OPENAI_TIMEOUT_SECONDS=90
OPENAI_MAX_RETRIES=0
MAX_UPLOAD_BYTES=10000000
MAX_ARCHIVE_FILES=250
MAX_ARCHIVE_UNCOMPRESSED_BYTES=50000000
```

If the PostgreSQL service has a different name, replace `Postgres` in the
reference expression. Do not paste a public database URL when a private reference
is available. Do not commit production values to `.env`.

The application accepts Railway's `postgres://` and `postgresql://` values and
normalizes them to SQLAlchemy's psycopg 3 driver.

## First account

After the first healthy deployment, connect to the running web service:

```bash
railway ssh
```

Inside the service container, create the private account:

```bash
anki-card-admin create-user --email you@example.com
```

Enter a unique password of at least 12 characters. Do not pass the password as a
command-line argument or store it in Railway variables. Use `set-password` through
the same SSH flow to rotate it. Rotation revokes all existing sessions.

## Database backups

Enable Railway's native backups for the PostgreSQL service before importing
valuable data. Keep at least a daily schedule and lock a known-good recovery point
before schema changes when the plan supports it.

The authenticated `Export` navigation action downloads a user-owned JSON backup.
It includes source snapshots, generation records, cards and versions, scheduling
state, review sessions, and review logs. It excludes password hashes and session
tokens. Treat the file as sensitive because it contains the original note text.

Before private alpha, perform one restoration exercise into a separate database:

1. Restore a Railway database backup or snapshot into a non-production service.
2. point a temporary web service at that database;
3. run `alembic upgrade head`;
4. sign in and verify notes, cards, due state, and review history;
5. remove the temporary service after recording the result.

The authenticated `/restore` page imports a version 1 JSON export into an empty
account. It restores source notes, generation records, drafts, cards and versions,
scheduling state, review sessions, and review logs atomically. It remaps IDs and
review attempt identifiers, preserves the signed-in account credentials and
sessions, and refuses to merge into a non-empty account.

Before private alpha, test JSON recovery with a new account or a separate
environment. Keep the original export until restored card counts, drafts,
scheduling state, and review history have been verified.

## Mac and iPhone sync acceptance

Use the same production account on both devices:

1. Sign in on Mac Safari and create or approve one uniquely named card.
2. Open the app on iPhone Safari and confirm the card appears without local data
   transfer.
3. Review and rate the card on iPhone.
4. Refresh the Mac dashboard and confirm completed count and due state changed.
5. Sign out on one device and confirm the other device remains signed in because
   sessions are independently revocable.
6. Download `Export` and confirm the card and review event are present.
7. Disable the network on iPhone and confirm the app explains that writes require
   a connection instead of pretending a review was saved.

This is the V1 synchronization milestone. Both devices read and write the same
PostgreSQL state through the authenticated FastAPI service.

## Operational checks

- `/health` proves that the process is running.
- `/ready` proves that the process can query PostgreSQL.
- Railway deployment logs should show a successful Alembic pre-deploy command.
- A failed `/ready` check must block the new deployment from receiving traffic.
- Generation jobs can still be interrupted by a restart. Resume them from the run
  page after the two-minute safety window.

Railway references:

- <https://docs.railway.com/config-as-code/reference>
- <https://docs.railway.com/deployments/pre-deploy-command>
- <https://docs.railway.com/deployments/healthchecks>
- <https://docs.railway.com/databases/postgresql>
- <https://docs.railway.com/cli/ssh>
