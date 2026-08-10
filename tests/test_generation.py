import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from anki_card_app.card_service import CardContent, create_draft
from anki_card_app.generation import (
    GeneratedCard,
    GeneratedCardBatch,
    GenerationResult,
    OpenAICardGenerator,
    create_generation_run,
    process_generation_run,
)
from anki_card_app.import_service import MarkdownSource, import_markdown
from anki_card_app.models import (
    Card,
    CardType,
    CardVersion,
    ChunkGenerationStatus,
    GenerationChunkRun,
    GenerationStatus,
    SourceChunk,
)
from anki_card_app.user_service import ensure_user


class FakeGenerator:
    provider = "fake"
    model = "test-model"

    def __init__(self, *, fail_sequences: set[int] | None = None) -> None:
        self.fail_sequences = fail_sequences or set()
        self.calls: dict[int, int] = {}

    def generate(self, chunk: SourceChunk) -> GenerationResult:
        self.calls[chunk.sequence] = self.calls.get(chunk.sequence, 0) + 1
        if chunk.sequence in self.fail_sequences:
            raise RuntimeError(f"failure for chunk {chunk.sequence}")
        if chunk.sequence == 0:
            card = GeneratedCard(
                card_type="normal",
                front="What is statistical power?",
                back="The probability of detecting a real effect.",
                source_excerpt="Power is the probability of detecting a real effect.",
                ai_enrichment="It equals one minus beta.",
            )
        else:
            card = GeneratedCard(
                card_type="cloze",
                cloze_text="Power equals {{c1::one minus beta}}.",
                source_excerpt="Power equals one minus beta.",
            )
        return GenerationResult(cards=[card], request_id=f"request-{chunk.sequence}")


def setup_run(db_session: Session) -> tuple[uuid.UUID, uuid.UUID]:
    user_identifier = uuid.uuid4()
    user = ensure_user(
        db_session,
        user_id=user_identifier,
        email=f"{user_identifier}@example.com",
    )
    imported = import_markdown(
        db_session,
        user_id=user.id,
        source=MarkdownSource(
            "stats.md",
            "# Power\nPower is the probability of detecting a real effect."
            "\n## Formula\nPower equals one minus beta.",
        ),
    )
    run = create_generation_run(
        db_session,
        user_id=user.id,
        source_document_id=imported.document.id,
        provider="fake",
        model="test-model",
        input_hash=imported.document.content_hash,
    )
    db_session.commit()
    return user.id, run.id


def test_generated_card_schema_rejects_invalid_content() -> None:
    with pytest.raises(ValueError, match="question and an answer"):
        GeneratedCard(card_type="normal", front="Question", source_excerpt="Source")


def test_process_generation_creates_provenanced_drafts(db_session: Session) -> None:
    user_id, run_id = setup_run(db_session)

    run = process_generation_run(db_session, run_id=run_id, generator=FakeGenerator())
    db_session.commit()

    cards = db_session.scalars(select(Card).order_by(Card.created_at)).all()
    versions = db_session.scalars(select(CardVersion).order_by(CardVersion.created_at)).all()
    assert run.status is GenerationStatus.COMPLETED
    assert run.generated_cards == 2
    assert [card.card_type for card in cards] == [CardType.NORMAL, CardType.CLOZE]
    assert all(card.user_id == user_id and card.source_chunk_id for card in cards)
    assert versions[0].source_excerpt is not None
    assert versions[0].source_excerpt.startswith("Power")
    assert versions[0].ai_enrichment == "It equals one minus beta."


def test_generation_retries_once_and_reports_partial_failure(db_session: Session) -> None:
    _, run_id = setup_run(db_session)
    generator = FakeGenerator(fail_sequences={1})

    run = process_generation_run(db_session, run_id=run_id, generator=generator)
    db_session.commit()
    results = db_session.scalars(
        select(GenerationChunkRun).order_by(GenerationChunkRun.started_at)
    ).all()

    assert run.status is GenerationStatus.PARTIAL
    assert run.completed_chunks == 1
    assert run.failed_chunks == 1
    assert generator.calls[1] == 2
    assert results[1].status is ChunkGenerationStatus.FAILED
    assert "failure for chunk 1" in (results[1].error or "")


def test_generation_reports_total_failure_and_missing_run(db_session: Session) -> None:
    _, run_id = setup_run(db_session)
    run = process_generation_run(
        db_session,
        run_id=run_id,
        generator=FakeGenerator(fail_sequences={0, 1}),
    )
    assert run.status is GenerationStatus.FAILED
    assert "All source chunks" in (run.error_summary or "")
    with pytest.raises(ValueError, match="not found"):
        process_generation_run(db_session, run_id=uuid.uuid4(), generator=FakeGenerator())


def test_generation_skips_exact_duplicate_cards(db_session: Session) -> None:
    user_id, run_id = setup_run(db_session)
    create_draft(
        db_session,
        user_id=user_id,
        card_type=CardType.NORMAL,
        content=CardContent(
            front="What is statistical power?",
            back="The probability of detecting a real effect.",
        ),
    )
    run = process_generation_run(db_session, run_id=run_id, generator=FakeGenerator())
    db_session.commit()

    assert run.generated_cards == 1
    assert db_session.scalar(select(func.count()).select_from(Card)) == 2


def test_generation_skips_unsupported_source_excerpt(db_session: Session) -> None:
    _, run_id = setup_run(db_session)

    class UnsupportedExcerptGenerator(FakeGenerator):
        def generate(self, chunk: SourceChunk) -> GenerationResult:
            return GenerationResult(
                cards=[
                    GeneratedCard(
                        card_type="normal",
                        front="Unsupported question",
                        back="Unsupported answer",
                        source_excerpt="This text is absent from the source.",
                    )
                ]
            )

    run = process_generation_run(
        db_session,
        run_id=run_id,
        generator=UnsupportedExcerptGenerator(),
    )
    assert run.status is GenerationStatus.COMPLETED
    assert run.generated_cards == 0
    assert db_session.scalar(select(func.count()).select_from(Card)) == 0


def test_completed_chunk_is_not_generated_twice(db_session: Session) -> None:
    _, run_id = setup_run(db_session)
    generator = FakeGenerator()
    process_generation_run(db_session, run_id=run_id, generator=generator)
    process_generation_run(db_session, run_id=run_id, generator=generator)
    assert generator.calls == {0: 1, 1: 1}


def test_openai_adapter_uses_structured_response(monkeypatch: pytest.MonkeyPatch) -> None:
    card = GeneratedCard(
        card_type="normal",
        front="Question",
        back="Answer",
        source_excerpt="Source",
    )

    class Response:
        output_parsed = type("Batch", (), {"cards": [card]})()
        _request_id = "req_123"

    class Responses:
        def parse(self, **kwargs: object) -> Response:
            assert kwargs["text_format"] is GeneratedCardBatch
            return Response()

    class Client:
        responses = Responses()

    generator = OpenAICardGenerator.__new__(OpenAICardGenerator)
    generator.model = "test-model"
    generator._client = Client()  # type: ignore[assignment]
    chunk = SourceChunk(source_document_id=uuid.uuid4(), sequence=0, text="Source")
    result = generator.generate(chunk)
    assert result.request_id == "req_123"
    assert result.cards == [card]


def test_openai_adapter_rejects_missing_parsed_output() -> None:
    class Responses:
        def parse(self, **kwargs: object) -> object:
            return type("Response", (), {"output_parsed": None})()

    generator = OpenAICardGenerator.__new__(OpenAICardGenerator)
    generator.model = "test-model"
    generator._client = type("Client", (), {"responses": Responses()})()
    chunk = SourceChunk(source_document_id=uuid.uuid4(), sequence=0, text="Source")
    with pytest.raises(RuntimeError, match="no structured"):
        generator.generate(chunk)
