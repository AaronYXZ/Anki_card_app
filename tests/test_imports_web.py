import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import anki_card_app.imports_web as imports_web
from anki_card_app.config import get_settings
from anki_card_app.models import (
    ChunkGenerationStatus,
    GenerationChunkRun,
    GenerationRun,
    GenerationStatus,
    SourceDocument,
)


def test_import_pages_and_markdown_upload(client: TestClient, db_session: Session) -> None:
    empty = client.get("/imports")
    form = client.get("/imports/new")
    uploaded = client.post(
        "/imports/new",
        files={"upload": ("power.md", b"# Power\nPower is one minus beta.", "text/markdown")},
        follow_redirects=False,
    )

    assert empty.status_code == 200
    assert "No imported notes" in empty.text
    assert "Import notes" in form.text
    assert "gpt-5.6-terra" in form.text
    assert "gpt-5.6-luna" in form.text
    assert uploaded.status_code == 303
    run = db_session.scalar(select(GenerationRun))
    assert run is not None
    assert uploaded.headers["location"] == f"/imports/{run.id}"
    assert run.status is GenerationStatus.FAILED
    chunk_runs = db_session.scalars(select(GenerationChunkRun)).all()
    assert all(item.status is ChunkGenerationStatus.FAILED for item in chunk_runs)
    assert run.failed_chunks == len(chunk_runs)

    detail = client.get(f"/imports/{run.id}")
    listing = client.get("/imports")
    assert "OPENAI_API_KEY is not configured" in detail.text
    assert "power.md" in detail.text
    assert "power.md" in listing.text


def test_duplicate_upload_reuses_existing_run(client: TestClient, db_session: Session) -> None:
    file = {"upload": ("same.md", b"# Same\nContent", "text/markdown")}
    first = client.post("/imports/new", files=file, follow_redirects=False)
    second = client.post("/imports/new", files=file, follow_redirects=False)

    assert first.headers["location"] == second.headers["location"]
    assert len(db_session.scalars(select(SourceDocument)).all()) == 1
    assert len(db_session.scalars(select(GenerationRun)).all()) == 1


def test_same_note_can_run_with_a_different_model(client: TestClient, db_session: Session) -> None:
    file = {"upload": ("same.md", b"# Same\nContent", "text/markdown")}
    first = client.post(
        "/imports/new",
        data={"model": "gpt-5.6-terra"},
        files=file,
        follow_redirects=False,
    )
    second = client.post(
        "/imports/new",
        data={"model": "gpt-5.6-luna"},
        files=file,
        follow_redirects=False,
    )

    runs = db_session.scalars(select(GenerationRun).order_by(GenerationRun.created_at)).all()
    assert first.headers["location"] != second.headers["location"]
    assert [run.model for run in runs] == ["gpt-5.6-terra", "gpt-5.6-luna"]
    assert len(db_session.scalars(select(SourceDocument)).all()) == 1


def test_import_upload_validation_and_missing_detail(client: TestClient) -> None:
    invalid = client.post(
        "/imports/new",
        files={"upload": ("notes.txt", b"not markdown", "text/plain")},
    )
    missing = client.get(f"/imports/{uuid.uuid4()}")

    assert invalid.status_code == 422
    assert "Only .md and .zip" in invalid.text
    assert missing.status_code == 404

    invalid_model = client.post(
        "/imports/new",
        data={"model": "untrusted-model"},
        files={"upload": ("notes.md", b"# Notes", "text/markdown")},
    )
    assert invalid_model.status_code == 422
    assert "Choose Terra or Luna" in invalid_model.text


def test_configured_upload_schedules_generation(
    client: TestClient,
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    original = get_settings()
    settings = SimpleNamespace(
        development_user_id=original.development_user_id,
        development_user_email=original.development_user_email,
        max_upload_bytes=10_000,
        max_archive_files=10,
        max_archive_uncompressed_bytes=20_000,
        openai_api_key="test-key",
        openai_model="gpt-5.6-terra",
        openai_timeout_seconds=12.0,
        openai_max_retries=0,
    )
    called: list[tuple[uuid.UUID, str]] = []
    monkeypatch.setattr(imports_web, "get_settings", lambda: settings)
    monkeypatch.setattr(
        imports_web,
        "_process_in_background",
        lambda run_id, api_key, model, timeout_seconds, max_retries: called.append((run_id, model)),
    )

    response = client.post(
        "/imports/new",
        data={"model": "gpt-5.6-luna"},
        files={"upload": ("configured.md", b"# Topic\nFact", "text/markdown")},
        follow_redirects=False,
    )
    run = db_session.scalar(select(GenerationRun))
    assert response.status_code == 303
    assert run is not None
    assert run.model == "gpt-5.6-luna"
    assert called == [(run.id, "gpt-5.6-luna")]


def test_background_generation_commits_and_records_outer_failure(
    client: TestClient,
    db_session: Session,
    test_engine: Engine,
    monkeypatch: MonkeyPatch,
) -> None:
    client.post(
        "/imports/new",
        files={"upload": ("background.md", b"# Topic\nFact", "text/markdown")},
    )
    run = db_session.scalar(select(GenerationRun))
    assert run is not None
    monkeypatch.setattr(imports_web, "get_engine", lambda: test_engine)

    def complete(session: Session, **kwargs: object) -> None:
        stored = session.get(GenerationRun, run.id)
        assert stored is not None
        stored.status = GenerationStatus.COMPLETED

    monkeypatch.setattr(imports_web, "process_generation_run", complete)
    imports_web._process_in_background(run.id, "key", "model")
    db_session.expire_all()
    assert db_session.get(GenerationRun, run.id).status is GenerationStatus.COMPLETED  # type: ignore[union-attr]

    def fail(session: Session, **kwargs: object) -> None:
        raise RuntimeError("outer failure")

    monkeypatch.setattr(imports_web, "process_generation_run", fail)
    imports_web._process_in_background(run.id, "key", "model")
    db_session.expire_all()
    failed = db_session.get(GenerationRun, run.id)
    assert failed is not None
    assert failed.status is GenerationStatus.FAILED
    assert failed.error_summary == "outer failure"


def test_retry_creates_a_fresh_run_and_handles_conflicts(
    client: TestClient,
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    client.post(
        "/imports/new",
        files={"upload": ("retry.md", b"# Topic\nFact", "text/markdown")},
    )
    original_run = db_session.scalar(select(GenerationRun))
    assert original_run is not None
    original = get_settings()
    settings = SimpleNamespace(
        development_user_id=original.development_user_id,
        development_user_email=original.development_user_email,
        openai_api_key="test-key",
        openai_model="gpt-5.6-terra",
        openai_timeout_seconds=12.0,
        openai_max_retries=0,
    )
    called: list[tuple[uuid.UUID, str]] = []
    monkeypatch.setattr(imports_web, "get_settings", lambda: settings)
    monkeypatch.setattr(
        imports_web,
        "_process_in_background",
        lambda run_id, api_key, model, timeout_seconds, max_retries: called.append((run_id, model)),
    )

    retried = client.post(f"/imports/{original_run.id}/retry", follow_redirects=False)
    runs = db_session.scalars(select(GenerationRun).order_by(GenerationRun.created_at)).all()
    assert retried.status_code == 303
    assert len(runs) == 2
    assert retried.headers["location"] == f"/imports/{runs[1].id}"
    assert runs[1].model == original_run.model
    assert called == [(runs[1].id, original_run.model)]

    runs[1].status = GenerationStatus.RUNNING
    db_session.commit()
    running = client.post(f"/imports/{runs[1].id}/retry")
    assert running.status_code == 409
    missing = client.post(f"/imports/{uuid.uuid4()}/retry")
    assert missing.status_code == 404

    original_run.status = GenerationStatus.COMPLETED
    db_session.commit()
    completed = client.post(f"/imports/{original_run.id}/retry", follow_redirects=False)
    assert completed.status_code == 303
    assert completed.headers["location"] == f"/imports/{original_run.id}"
