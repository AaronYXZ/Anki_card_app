# Anki Card App

An AI-assisted spaced-repetition learning system that turns Obsidian notes into high-quality flashcards and schedules reviews with FSRS.

## Planned workflow

1. Import Markdown notes from Obsidian.
2. Generate question-and-answer and cloze cards.
3. Review due cards in a PWA.
4. Persist review history and schedule future reviews with FSRS.
5. Send daily review reminders and generate learning reports.

## Status

Milestone 1 is complete. The application supports manual Normal and Cloze drafts, immutable card versions, approval and rejection, and a due-card review preview.

## Local development

Requirements:

- `uv`
- Docker with Compose, for PostgreSQL

Set up the project:

```bash
cp .env.example .env
docker compose up -d postgres
uv sync --all-groups
uv run alembic upgrade head
uv run uvicorn anki_card_app.main:app --reload
```

Open [http://localhost:8000/health](http://localhost:8000/health) to verify the application.

The local product workflow is available at [http://localhost:8000](http://localhost:8000):

1. Create a Normal or Cloze draft.
2. Review, edit, approve, or reject the draft.
3. Open Review to preview approved due cards and reveal their answers.

The local development build uses a fixed development user. Authentication is added before the private alpha.

Run quality checks:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest --cov --cov-report=term-missing
```

## Product documents

- [Product requirements](docs/PRD.md)
- [Development plan](docs/DEVELOPMENT_PLAN.md)
