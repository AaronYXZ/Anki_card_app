from __future__ import annotations

import uuid
from typing import Literal, Protocol

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from anki_card_app.card_service import (
    CardContent,
    CardValidationError,
    content_fingerprint,
    create_draft,
    validate_content,
)
from anki_card_app.models import (
    Card,
    CardType,
    ChunkGenerationStatus,
    GenerationChunkRun,
    GenerationRun,
    GenerationStatus,
    SourceChunk,
    utc_now,
)

PROMPT_VERSION = "anki-v1"
CARD_GENERATION_PROMPT = """
You create durable interview-preparation flashcards for machine learning engineers.
Extract the source's key concepts, facts, decisions, equations, code behavior, and
behavioral interview lessons in source order. Each card must be atomic, self-contained,
and faithful to the source. Prefer a normal question and answer when recall needs
explanation. Prefer cloze when one precise term, relationship, or short expression should
be recalled. Use Anki cloze syntax such as {{c1::answer}}. Preserve useful code and math.
Do not invent unsupported claims. Put optional context or pitfalls in ai_enrichment, never
in the tested prompt. Quote a short, exact source_excerpt that supports each card. Return
no more than 20 cards for this chunk.
""".strip()


class GeneratedCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    card_type: Literal["normal", "cloze"]
    front: str | None = None
    back: str | None = None
    cloze_text: str | None = None
    back_extra: str | None = None
    source_excerpt: str = Field(min_length=1, max_length=1_500)
    ai_enrichment: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def validate_card_content(self) -> GeneratedCard:
        validate_content(CardType(self.card_type), self.as_card_content())
        return self

    def as_card_content(self) -> CardContent:
        return CardContent(
            front=self.front,
            back=self.back,
            cloze_text=self.cloze_text,
            back_extra=self.back_extra,
        )


class GeneratedCardBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cards: list[GeneratedCard] = Field(max_length=20)


class GenerationResult(BaseModel):
    cards: list[GeneratedCard]
    request_id: str | None = None


class CardGenerator(Protocol):
    provider: str
    model: str

    def generate(self, chunk: SourceChunk) -> GenerationResult: ...


class OpenAICardGenerator:
    provider = "openai"

    def __init__(self, *, api_key: str, model: str) -> None:
        self.model = model
        self._client = OpenAI(api_key=api_key)

    def generate(self, chunk: SourceChunk) -> GenerationResult:
        heading = chunk.heading_path or "Untitled section"
        response = self._client.responses.parse(
            model=self.model,
            input=f"{CARD_GENERATION_PROMPT}\n\nHeading: {heading}\n\nSOURCE:\n{chunk.text}",
            text_format=GeneratedCardBatch,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise RuntimeError("The model returned no structured card batch.")
        return GenerationResult(cards=parsed.cards, request_id=response._request_id)


def create_generation_run(
    session: Session,
    *,
    user_id: uuid.UUID,
    source_document_id: uuid.UUID,
    provider: str,
    model: str,
    input_hash: str,
) -> GenerationRun:
    chunks = session.scalars(
        select(SourceChunk)
        .where(SourceChunk.source_document_id == source_document_id)
        .order_by(SourceChunk.sequence)
    ).all()
    run = GenerationRun(
        user_id=user_id,
        source_document_id=source_document_id,
        prompt_version=PROMPT_VERSION,
        provider=provider,
        model=model,
        input_hash=input_hash,
        total_chunks=len(chunks),
    )
    session.add(run)
    session.flush()
    session.add_all(
        GenerationChunkRun(generation_run_id=run.id, source_chunk_id=chunk.id) for chunk in chunks
    )
    session.flush()
    return run


def _save_candidates(
    session: Session,
    *,
    run: GenerationRun,
    chunk: SourceChunk,
    candidates: list[GeneratedCard],
) -> int:
    created = 0
    for candidate in candidates:
        if candidate.source_excerpt not in chunk.text:
            continue
        card_type = CardType(candidate.card_type)
        content = candidate.as_card_content()
        fingerprint = content_fingerprint(card_type, content)
        if session.scalar(
            select(Card.id).where(
                Card.user_id == run.user_id,
                Card.content_fingerprint == fingerprint,
            )
        ):
            continue
        try:
            create_draft(
                session,
                user_id=run.user_id,
                card_type=card_type,
                content=content,
                created_by="ai",
                source_document_id=run.source_document_id,
                source_chunk_id=chunk.id,
                generation_run_id=run.id,
                source_excerpt=candidate.source_excerpt,
                ai_enrichment=candidate.ai_enrichment,
            )
        except CardValidationError:
            continue
        created += 1
    return created


def process_generation_run(
    session: Session,
    *,
    run_id: uuid.UUID,
    generator: CardGenerator,
    max_attempts: int = 2,
) -> GenerationRun:
    run = session.get(GenerationRun, run_id)
    if run is None:
        raise ValueError("Generation run not found.")
    run.status = GenerationStatus.RUNNING
    run.started_at = run.started_at or utc_now()
    session.flush()

    chunk_runs = session.scalars(
        select(GenerationChunkRun)
        .join(SourceChunk, SourceChunk.id == GenerationChunkRun.source_chunk_id)
        .where(GenerationChunkRun.generation_run_id == run.id)
        .order_by(SourceChunk.sequence)
    ).all()
    for chunk_run in chunk_runs:
        if chunk_run.status is ChunkGenerationStatus.COMPLETED:
            continue
        chunk = session.get(SourceChunk, chunk_run.source_chunk_id)
        if chunk is None:
            chunk_run.status = ChunkGenerationStatus.FAILED
            chunk_run.error = "Source chunk is missing."
            continue
        chunk_run.status = ChunkGenerationStatus.RUNNING
        chunk_run.started_at = utc_now()
        while chunk_run.attempt_count < max_attempts:
            chunk_run.attempt_count += 1
            try:
                result = generator.generate(chunk)
                chunk_run.generated_count = _save_candidates(
                    session, run=run, chunk=chunk, candidates=result.cards
                )
                chunk_run.request_id = result.request_id
                chunk_run.status = ChunkGenerationStatus.COMPLETED
                chunk_run.error = None
                break
            except Exception as exc:
                chunk_run.error = str(exc)[:2_000]
        if chunk_run.status is not ChunkGenerationStatus.COMPLETED:
            chunk_run.status = ChunkGenerationStatus.FAILED
        chunk_run.completed_at = utc_now()
        session.flush()

    run.completed_chunks = sum(
        item.status is ChunkGenerationStatus.COMPLETED for item in chunk_runs
    )
    run.failed_chunks = sum(item.status is ChunkGenerationStatus.FAILED for item in chunk_runs)
    run.generated_cards = sum(item.generated_count for item in chunk_runs)
    run.completed_at = utc_now()
    if run.failed_chunks == 0:
        run.status = GenerationStatus.COMPLETED
        run.error_summary = None
    elif run.completed_chunks:
        run.status = GenerationStatus.PARTIAL
        run.error_summary = f"{run.failed_chunks} source chunks failed after one retry."
    else:
        run.status = GenerationStatus.FAILED
        run.error_summary = "All source chunks failed after one retry."
    session.flush()
    return run
