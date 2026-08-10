from __future__ import annotations

import hashlib
import io
import re
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from anki_card_app.models import SourceChunk, SourceDocument

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")


class ImportValidationError(ValueError):
    """An uploaded Markdown file or archive is unsafe or unsupported."""


@dataclass(frozen=True, slots=True)
class ImportLimits:
    max_upload_bytes: int = 10_000_000
    max_archive_files: int = 250
    max_archive_uncompressed_bytes: int = 50_000_000
    max_chunk_chars: int = 6_000


@dataclass(frozen=True, slots=True)
class MarkdownSource:
    relative_path: str
    content: str
    modified_at: datetime | None = None

    @property
    def filename(self) -> str:
        return PurePosixPath(self.relative_path).name


@dataclass(frozen=True, slots=True)
class MarkdownChunk:
    sequence: int
    heading_path: str | None
    text: str


@dataclass(frozen=True, slots=True)
class ImportResult:
    document: SourceDocument
    created: bool


def _safe_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ImportValidationError(f"Unsafe archive path: {value}")
    return path.as_posix()


def _decode_markdown(data: bytes, path: str) -> str:
    if b"\x00" in data:
        raise ImportValidationError(f"Binary content is not supported: {path}")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportValidationError(f"Markdown must be UTF-8 encoded: {path}") from exc


def read_upload(filename: str, data: bytes, limits: ImportLimits) -> list[MarkdownSource]:
    if not filename:
        raise ImportValidationError("Choose a Markdown or ZIP file.")
    if len(data) > limits.max_upload_bytes:
        raise ImportValidationError("Upload exceeds the configured size limit.")

    suffix = PurePosixPath(filename).suffix.casefold()
    if suffix == ".md":
        path = _safe_relative_path(PurePosixPath(filename).name)
        return [MarkdownSource(path, _decode_markdown(data, path))]
    if suffix != ".zip":
        raise ImportValidationError("Only .md and .zip files are supported.")

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ImportValidationError("The ZIP archive is invalid.") from exc

    sources: list[MarkdownSource] = []
    total_size = 0
    seen_paths: set[str] = set()
    with archive:
        files = [info for info in archive.infolist() if not info.is_dir()]
        if len(files) > limits.max_archive_files:
            raise ImportValidationError("ZIP archive contains too many files.")
        for info in files:
            path = _safe_relative_path(info.filename)
            total_size += info.file_size
            if total_size > limits.max_archive_uncompressed_bytes:
                raise ImportValidationError("ZIP archive expands beyond the configured size limit.")
            if PurePosixPath(path).suffix.casefold() != ".md":
                continue
            if path in seen_paths:
                raise ImportValidationError(f"ZIP archive contains a duplicate path: {path}")
            seen_paths.add(path)
            try:
                modified_at = datetime(*info.date_time, tzinfo=UTC)
                content = _decode_markdown(archive.read(info), path)
            except (RuntimeError, zipfile.BadZipFile) as exc:
                raise ImportValidationError(f"Could not read archive member: {path}") from exc
            sources.append(MarkdownSource(path, content, modified_at))
    if not sources:
        raise ImportValidationError("The ZIP archive contains no Markdown files.")
    return sources


def _split_oversized(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    paragraphs = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph.strip()
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = paragraph.strip()
        else:
            current = candidate
        while len(current) > max_chars:
            if current.count("```") >= 2 or current.count("~~~") >= 2:
                break
            boundary = current.rfind("\n", 0, max_chars)
            boundary = boundary if boundary > max_chars // 2 else max_chars
            chunks.append(current[:boundary].strip())
            current = current[boundary:].strip()
    if current:
        chunks.append(current)
    return chunks


def chunk_markdown(content: str, *, max_chars: int = 6_000) -> list[MarkdownChunk]:
    headings: list[tuple[int, str]] = []
    sections: list[tuple[str | None, str]] = []
    section_lines: list[str] = []
    section_heading: str | None = None
    fence: str | None = None

    def flush() -> None:
        nonlocal section_lines
        text = "\n".join(section_lines).strip()
        if text:
            sections.append((section_heading, text))
        section_lines = []

    for line in content.splitlines():
        fence_match = FENCE_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            fence = None if fence == marker else marker if fence is None else fence
            section_lines.append(line)
            continue
        heading_match = HEADING_RE.match(line) if fence is None else None
        if heading_match:
            flush()
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            headings = [item for item in headings if item[0] < level]
            headings.append((level, title))
            section_heading = " > ".join(item[1] for item in headings)
        section_lines.append(line)
    flush()

    chunks: list[MarkdownChunk] = []
    for heading_path, section in sections:
        for part in _split_oversized(section, max_chars):
            chunks.append(MarkdownChunk(len(chunks), heading_path, part))
    if not chunks and content.strip():
        chunks.append(MarkdownChunk(0, None, content.strip()))
    return chunks


def import_markdown(
    session: Session,
    *,
    user_id: uuid.UUID,
    source: MarkdownSource,
    max_chunk_chars: int = 6_000,
) -> ImportResult:
    digest = hashlib.sha256(source.content.encode()).hexdigest()
    existing = session.scalar(
        select(SourceDocument).where(
            SourceDocument.user_id == user_id,
            SourceDocument.content_hash == digest,
        )
    )
    if existing is not None:
        return ImportResult(existing, False)

    chunks = chunk_markdown(source.content, max_chars=max_chunk_chars)
    if not chunks:
        raise ImportValidationError(f"Markdown file is empty: {source.relative_path}")
    document = SourceDocument(
        user_id=user_id,
        relative_path=source.relative_path,
        filename=source.filename,
        content_hash=digest,
        raw_content=source.content,
        source_modified_at=source.modified_at,
    )
    session.add(document)
    session.flush()
    session.add_all(
        SourceChunk(
            source_document_id=document.id,
            sequence=chunk.sequence,
            heading_path=chunk.heading_path,
            text=chunk.text,
            token_estimate=max(1, len(chunk.text) // 4),
        )
        for chunk in chunks
    )
    session.flush()
    return ImportResult(document, True)
