from __future__ import annotations

import uuid
from typing import Literal, Protocol

from openai import (
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)
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

PROMPT_VERSION = "anki-v7-authored-answers"
CARD_GENERATION_PROMPT = """
You create durable interview-preparation flashcards for machine learning engineers.
Extract the source's key concepts, facts, decisions, equations, code behavior, and
behavioral interview lessons in source order. Each card must be atomic, self-contained,
and faithful to the source.

Choose the card type deliberately:
- Use normal for a question that needs a concise explanatory answer.
- Use cloze for one precise term, relationship, formula, or short fact. Use Anki syntax
  such as {{c1::answer}}.
- Use skeleton_recall only when the learner should reconstruct a complete framework,
  process, or story from a minimal outline. Good candidates include behavioral or STAR
  stories, project walkthroughs, system designs, debugging processes, analytical
  frameworks, decision processes, multi-step workflows, root cause analyses, and
  experimentation processes. Do not use skeleton_recall for vocabulary, definitions,
  simple facts, formulas, or short lists.

First detect whether the source contains an explicitly authored question-and-answer block.
Recognize case-insensitive standalone labels or Markdown headings such as `Question:`, `Q:`,
`Prompt:`, `Answer:`, `A:`, `Response:`, and `Solution:`. Treat them as structural labels only
when they occur on their own line or as a heading, not when the words appear inside prose.
When an explicit pair exists:
- Create a normal card by default. Use the content after the question label and before the
  answer label as the front. If there is only an answer label, use the enclosing heading or
  clearly stated question as the front.
- Use the complete coherent content after the answer label, stopping at the next question or
  topic boundary, as the back. Remove only the structural label and unnecessary surrounding
  whitespace. Preserve the author's wording, Markdown, order, examples, formulas, lists, and
  explanations. Do not summarize, paraphrase, shorten, or move parts only to ai_enrichment.
- Do not generate duplicate cards from individual details inside that same answer block.
These authored answer rules take priority over the general synthesis rules below.

For skeleton_recall, put a descriptive title and only major section headers or a numbered
outline in front. Do not reveal supporting details in front. Put the same sections in back
and fill each with compact bullets that preserve logical or chronological order, causal
reasoning, evidence, and examples. Include only enough detail to trigger reconstruction.
Never write a long essay. One skeleton_recall card covers one complete framework or story.

Treat examples, cases, scenarios, analogies, anecdotes, sample calculations, and
illustrative code as supporting context rather than separate facts to memorize. Do not turn
names, numbers, outcomes, steps, claims, or conclusions that are true only inside one
example or case into standalone cards. Never generalize a rule from a single example.

For a normal card that asks about a concept, term, or metric, make the back independently
useful for understanding, not just a one-line definition:
- Start with a direct, concise answer to the question.
- When the source contains an example, scenario, analogy, or concrete interpretation that
  materially clarifies the concept, include the most useful one or a compact representative
  set in the back under an **Example:** or **Interpretation:** label. Keep it faithful to the
  source. Preserve multiple examples when each shows a distinct mechanism and the set stays
  compact. Do not move essential explanatory examples only to ai_enrichment.
- For a metric, statistical quantity, or mathematical relationship, include the relevant
  source-supported formula when it helps understanding. Define its symbols and briefly state
  how to interpret changes in the metric. Preserve the source's math notation and delimiters.
- Do not invent an example, formula, threshold, or interpretation that the source does not
  support. If the source has no useful example or formula, a concise explanation is enough.

For example, if the source defines contamination and then gives control/treatment exposure
scenarios, a card asking "What is contamination?" should answer with the definition and
retain one or more compact exposure scenarios on the same card. The scenarios support the
concept; they should not become independent cards.

A complete case or story may become one skeleton_recall card only when the source clearly
presents that case or story itself as something the learner should rehearse, such as their
own project walkthrough or behavioral interview story. Never atomize its incidental details
into cards.

Card content supports Markdown. Preserve useful Markdown from the source when carrying
material into front, back, cloze_text, back_extra, or ai_enrichment. In particular, keep
lists, emphasis, links, inline code, fenced code blocks, and math notation instead of
flattening them into plain text. Do not add Markdown decoration that changes the source's
meaning. The source_excerpt must remain an exact substring, including its original Markdown.
Wrap generated inline LaTeX in `$...$` and standalone equations in `$$...$$` so the card
renderer typesets them. Preserve math delimiters that already exist in the source.

Preserve useful code and math. Do not invent unsupported claims. Put optional context or
pitfalls in ai_enrichment, never in the tested prompt. Quote a short, exact source_excerpt
that supports each card. Return no more than 20 cards for this chunk.
""".strip()


class GenerationProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool, abort_run: bool) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.abort_run = abort_run


class GeneratedCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    card_type: Literal["normal", "cloze", "skeleton_recall"]
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

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 90.0,
        max_retries: int = 0,
    ) -> None:
        self.model = model
        self._client = OpenAI(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    def generate(self, chunk: SourceChunk) -> GenerationResult:
        heading = chunk.heading_path or "Untitled section"
        try:
            response = self._client.responses.parse(
                model=self.model,
                input=f"{CARD_GENERATION_PROMPT}\n\nHeading: {heading}\n\nSOURCE:\n{chunk.text}",
                text_format=GeneratedCardBatch,
                reasoning={"effort": "low"},
            )
        except RateLimitError as exc:
            error_code = getattr(exc, "code", None)
            if error_code is None and isinstance(exc.body, dict):
                error_body = exc.body.get("error", exc.body)
                if isinstance(error_body, dict):
                    error_code = error_body.get("code")
            quota_exhausted = error_code in {
                "credit_balance_exhausted",
                "insufficient_quota",
            }
            message = (
                "OpenAI API credits are exhausted. Add credits in OpenAI billing, then resume "
                "generation."
                if quota_exhausted
                else "OpenAI rate limit reached. Try generation again later."
            )
            raise GenerationProviderError(
                message,
                retryable=not quota_exhausted,
                abort_run=quota_exhausted,
            ) from exc
        except AuthenticationError as exc:
            raise GenerationProviderError(
                "OpenAI rejected the API key. Check OPENAI_API_KEY and restart the app.",
                retryable=False,
                abort_run=True,
            ) from exc
        except (PermissionDeniedError, NotFoundError) as exc:
            raise GenerationProviderError(
                f"OpenAI cannot access model {self.model}. Choose an available model and resume.",
                retryable=False,
                abort_run=True,
            ) from exc
        except BadRequestError as exc:
            raise GenerationProviderError(
                "OpenAI rejected the card-generation request. Check the selected model and "
                "structured-output settings.",
                retryable=False,
                abort_run=True,
            ) from exc
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
    run.completed_at = None
    session.commit()
    abort_error: str | None = None

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
            chunk_run.completed_at = utc_now()
            session.commit()
            continue
        chunk_run.status = ChunkGenerationStatus.RUNNING
        chunk_run.started_at = utc_now()
        while chunk_run.attempt_count < max_attempts:
            chunk_run.attempt_count += 1
            chunk_run.completed_at = None
            session.commit()
            try:
                result = generator.generate(chunk)
                chunk_run.generated_count = _save_candidates(
                    session, run=run, chunk=chunk, candidates=result.cards
                )
                chunk_run.request_id = result.request_id
                chunk_run.status = ChunkGenerationStatus.COMPLETED
                chunk_run.error = None
                chunk_run.completed_at = utc_now()
                session.commit()
                break
            except Exception as exc:
                session.rollback()
                recovered_chunk_run = session.get(GenerationChunkRun, chunk_run.id)
                if recovered_chunk_run is None:
                    raise RuntimeError("Generation chunk run disappeared.") from exc
                chunk_run = recovered_chunk_run
                chunk_run.error = str(exc)[:2_000]
                chunk_run.completed_at = utc_now()
                session.commit()
                if isinstance(exc, GenerationProviderError):
                    if exc.abort_run:
                        abort_error = str(exc)
                    if not exc.retryable:
                        break
        if chunk_run.status is not ChunkGenerationStatus.COMPLETED:
            chunk_run.status = ChunkGenerationStatus.FAILED
            chunk_run.completed_at = utc_now()
            session.commit()
        if abort_error:
            for pending_chunk_run in chunk_runs:
                if pending_chunk_run.status is ChunkGenerationStatus.PENDING:
                    pending_chunk_run.status = ChunkGenerationStatus.FAILED
                    pending_chunk_run.error = f"Skipped: {abort_error}"
                    pending_chunk_run.completed_at = utc_now()
            session.commit()
            break

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
        run.error_summary = abort_error or (
            f"{run.failed_chunks} source chunks failed after one retry."
        )
    else:
        run.status = GenerationStatus.FAILED
        run.error_summary = abort_error or "All source chunks failed after one retry."
    session.commit()
    return run
