# Anki Card App

An AI-assisted spaced-repetition learning system that turns Obsidian notes into high-quality flashcards and schedules reviews with FSRS.

## Planned workflow

1. Import Markdown notes from Obsidian.
2. Generate Normal, Cloze, and Skeleton Recall cards.
3. Review due cards in an installable PWA.
4. Persist review history and schedule future reviews with FSRS.
5. Send daily review reminders and generate learning reports.

## Status

Milestone 3's local implementation is complete. The application can safely import notes, generate and review draft cards, build a due-first daily queue, and persist Again, Hard, Good, or Easy ratings through FSRS 6. Every review stores the prior and updated scheduling state and is protected by an idempotent attempt identifier. The web app is installable from a supported browser and caches its static shell for a clear offline fallback. Review and database writes still require a network connection. A live OpenAI provider smoke test still requires an `OPENAI_API_KEY`.

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

1. Create a Normal, Cloze, or Skeleton Recall draft.
2. Review, edit, approve, or reject the draft.
3. Open Review, recall the answer, reveal it, and choose Again, Hard, Good, or Easy.
4. Complete the session and inspect the rating summary. FSRS schedules each next review automatically.

Open [http://localhost:8000/install](http://localhost:8000/install) for iPhone, iPad, and desktop installation instructions. During review, press `Space` to reveal the answer, then use `1`, `2`, `3`, or `4` for Again, Hard, Good, or Easy.

To generate drafts from notes, set `OPENAI_API_KEY` in `.env`, open `/imports`, choose Terra or Luna, and upload a Markdown file or a ZIP of Markdown files. Skeleton Recall is selected for complete stories, frameworks, designs, and multi-step processes. Normal and Cloze remain preferred for explanations and atomic facts. `OPENAI_MODEL` controls the default selection. Without an API key, imports are still validated and stored, and the run shows a clear configuration error.

Generation progress is committed after every chunk and active run pages refresh every five seconds. Each OpenAI request times out after 90 seconds and is retried once by the generation workflow. Authentication, model-access, and exhausted-credit errors stop the run immediately. After correcting the configuration or adding API credits, use **Resume generation** to create a fresh run without duplicating successful cards.

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
