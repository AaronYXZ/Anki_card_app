import io
import uuid
import zipfile

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from anki_card_app.import_service import (
    ImportLimits,
    ImportValidationError,
    MarkdownSource,
    chunk_markdown,
    import_markdown,
    read_upload,
)
from anki_card_app.models import SourceChunk
from anki_card_app.user_service import ensure_user


def make_zip(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return output.getvalue()


def test_read_markdown_and_safe_zip() -> None:
    markdown = read_upload("note.md", b"# Topic\nFact", ImportLimits())
    archive = read_upload(
        "notes.zip",
        make_zip({"Offers/a.md": b"# A", "Offers/b.MD": b"# B", "image.png": b"x"}),
        ImportLimits(),
    )

    assert markdown[0].content == "# Topic\nFact"
    assert [item.relative_path for item in archive] == ["Offers/a.md", "Offers/b.MD"]
    assert archive[0].modified_at is not None


@pytest.mark.parametrize(
    ("filename", "data", "message"),
    [
        ("", b"", "Choose"),
        ("note.txt", b"text", "Only .md"),
        ("note.md", b"\xff", "UTF-8"),
        ("note.md", b"a\x00b", "Binary"),
        ("notes.zip", b"invalid", "invalid"),
        ("notes.zip", make_zip({"image.png": b"x"}), "no Markdown"),
        ("notes.zip", make_zip({"../secret.md": b"x"}), "Unsafe"),
    ],
)
def test_read_upload_rejects_invalid_inputs(filename: str, data: bytes, message: str) -> None:
    with pytest.raises(ImportValidationError, match=message):
        read_upload(filename, data, ImportLimits())


def test_read_upload_enforces_all_limits() -> None:
    with pytest.raises(ImportValidationError, match="size limit"):
        read_upload("large.md", b"1234", ImportLimits(max_upload_bytes=3))
    with pytest.raises(ImportValidationError, match="too many"):
        read_upload(
            "many.zip",
            make_zip({"a.md": b"a", "b.md": b"b"}),
            ImportLimits(max_archive_files=1),
        )
    with pytest.raises(ImportValidationError, match="expands"):
        read_upload(
            "large.zip",
            make_zip({"a.md": b"1234"}),
            ImportLimits(max_archive_uncompressed_bytes=3),
        )


def test_chunk_markdown_tracks_heading_order_and_fenced_code() -> None:
    content = """Preamble.
# Statistics
Power definition.
```python
# not a heading
print('ok')
```
## Tests
A paragraph that is deliberately long.

Another paragraph.
"""
    chunks = chunk_markdown(content, max_chars=55)

    assert chunks[0].heading_path is None
    assert chunks[1].heading_path == "Statistics"
    assert "# not a heading" in chunks[1].text
    assert chunks[-1].heading_path == "Statistics > Tests"
    assert [chunk.sequence for chunk in chunks] == list(range(len(chunks)))


def test_chunk_markdown_splits_long_unbroken_text_and_empty() -> None:
    chunks = chunk_markdown("x" * 25, max_chars=10)
    assert [len(chunk.text) for chunk in chunks] == [10, 10, 5]
    assert chunk_markdown("   ") == []


def test_import_markdown_is_idempotent_and_persists_chunks(db_session: Session) -> None:
    user = ensure_user(db_session, user_id=uuid.uuid4(), email="import@example.com")
    original_markdown = "# A\n**Fact**\n\n- one\n- two\n## B\n`More`"
    source = MarkdownSource("Offers/test.md", original_markdown)

    first = import_markdown(db_session, user_id=user.id, source=source)
    second = import_markdown(db_session, user_id=user.id, source=source)
    db_session.commit()

    count = db_session.scalar(select(func.count()).select_from(SourceChunk))
    assert first.created is True
    assert second.created is False
    assert first.document.id == second.document.id
    assert first.document.raw_content == original_markdown
    persisted_chunks = db_session.scalars(select(SourceChunk).order_by(SourceChunk.sequence)).all()
    assert "**Fact**" in persisted_chunks[0].text
    assert "- one\n- two" in persisted_chunks[0].text
    assert "`More`" in persisted_chunks[1].text
    assert count == 2


def test_import_rejects_empty_markdown(db_session: Session) -> None:
    user = ensure_user(db_session, user_id=uuid.uuid4(), email="empty@example.com")
    with pytest.raises(ImportValidationError, match="empty"):
        import_markdown(
            db_session,
            user_id=user.id,
            source=MarkdownSource("empty.md", "  "),
        )
