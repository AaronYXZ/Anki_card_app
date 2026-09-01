from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from anki_card_app.database import Base
from anki_card_app.export_service import EXPORT_FORMAT_VERSION
from anki_card_app.models import (
    Card,
    CardVersion,
    GenerationChunkRun,
    GenerationRun,
    ReviewLog,
    ReviewSession,
    ReviewSessionCard,
    SchedulingState,
    SourceChunk,
    SourceDocument,
    UserAccount,
)


class RestoreValidationError(ValueError):
    """An exported backup cannot be restored safely."""


@dataclass(frozen=True, slots=True)
class RestoreResult:
    counts: dict[str, int]

    @property
    def total_rows(self) -> int:
        return sum(self.counts.values())


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RestoreValidationError(f"Backup contains a duplicate JSON key: {key}.")
        result[key] = value
    return result


def _reject_nonfinite_number(value: str) -> None:
    raise RestoreValidationError(f"Backup contains a non-finite number: {value}.")


def parse_backup_json(data: bytes) -> object:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise RestoreValidationError("Backup must be UTF-8 encoded JSON.") from error
    try:
        return json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_nonfinite_number,
        )
    except json.JSONDecodeError as error:
        raise RestoreValidationError("Backup contains invalid JSON.") from error


@dataclass(frozen=True, slots=True)
class _TableSpec:
    name: str
    model: type[Base]
    foreign_keys: dict[str, str]
    owns_user_id: bool = False


_TABLE_SPECS = (
    _TableSpec("source_documents", SourceDocument, {}, True),
    _TableSpec("source_chunks", SourceChunk, {"source_document_id": "source_documents"}),
    _TableSpec(
        "generation_runs",
        GenerationRun,
        {"source_document_id": "source_documents"},
        True,
    ),
    _TableSpec(
        "generation_chunk_runs",
        GenerationChunkRun,
        {
            "generation_run_id": "generation_runs",
            "source_chunk_id": "source_chunks",
        },
    ),
    _TableSpec(
        "cards",
        Card,
        {
            "source_document_id": "source_documents",
            "source_chunk_id": "source_chunks",
            "generation_run_id": "generation_runs",
            "current_version_id": "card_versions",
        },
        True,
    ),
    _TableSpec("card_versions", CardVersion, {"card_id": "cards"}),
    _TableSpec("scheduling_states", SchedulingState, {"card_id": "cards"}),
    _TableSpec("review_sessions", ReviewSession, {}, True),
    _TableSpec(
        "review_session_cards",
        ReviewSessionCard,
        {"review_session_id": "review_sessions", "card_id": "cards"},
    ),
    _TableSpec(
        "review_logs",
        ReviewLog,
        {"card_id": "cards", "review_session_id": "review_sessions"},
        True,
    ),
)
_TABLES_BY_NAME = {spec.name: spec for spec in _TABLE_SPECS}
_OWNED_ROOT_MODELS = (SourceDocument, GenerationRun, Card, ReviewSession, ReviewLog)
_OPTIONAL_COLUMN_DEFAULTS: dict[str, dict[str, Any]] = {
    "cards": {"is_favorite": False, "favorited_at": None},
}


def _parse_uuid(value: object, *, location: str) -> uuid.UUID:
    if not isinstance(value, str):
        raise RestoreValidationError(f"{location} must be a UUID string.")
    try:
        return uuid.UUID(value)
    except ValueError as error:
        raise RestoreValidationError(f"{location} must be a valid UUID.") from error


def _parse_datetime(value: object, *, location: str) -> datetime:
    if not isinstance(value, str):
        raise RestoreValidationError(f"{location} must be an ISO 8601 timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RestoreValidationError(f"{location} must be an ISO 8601 timestamp.") from error
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _validate_user_settings(user_data: dict[str, Any]) -> tuple[str, int, float]:
    timezone = user_data.get("timezone")
    daily_limit = user_data.get("daily_limit")
    desired_retention = user_data.get("desired_retention")
    if not isinstance(timezone, str) or not timezone or len(timezone) > 64:
        raise RestoreValidationError("user.timezone is invalid.")
    if isinstance(daily_limit, bool) or not isinstance(daily_limit, int) or daily_limit <= 0:
        raise RestoreValidationError("user.daily_limit must be a positive integer.")
    if (
        isinstance(desired_retention, bool)
        or not isinstance(desired_retention, (int, float))
        or not 0 < float(desired_retention) <= 1
    ):
        raise RestoreValidationError("user.desired_retention must be greater than 0 and at most 1.")
    return timezone, daily_limit, float(desired_retention)


def _validate_payload(
    payload: object,
) -> tuple[uuid.UUID, tuple[str, int, float], dict[str, list[dict[str, Any]]]]:
    if not isinstance(payload, dict):
        raise RestoreValidationError("Backup root must be a JSON object.")
    document = cast(dict[str, Any], payload)
    if document.get("format") != "anki-card-app-backup":
        raise RestoreValidationError("File is not an Anki Card App backup.")
    if document.get("format_version") != EXPORT_FORMAT_VERSION:
        raise RestoreValidationError(
            f"Unsupported backup format version. Expected {EXPORT_FORMAT_VERSION}."
        )
    user_data = document.get("user")
    data = document.get("data")
    if not isinstance(user_data, dict):
        raise RestoreValidationError("Backup user metadata is missing.")
    if not isinstance(data, dict):
        raise RestoreValidationError("Backup data is missing.")
    typed_user_data = cast(dict[str, Any], user_data)
    export_user_id = _parse_uuid(typed_user_data.get("id"), location="user.id")
    user_settings = _validate_user_settings(typed_user_data)

    expected_tables = set(_TABLES_BY_NAME)
    actual_tables = set(data)
    if actual_tables != expected_tables:
        missing = sorted(expected_tables - actual_tables)
        unexpected = sorted(actual_tables - expected_tables)
        details: list[str] = []
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if unexpected:
            details.append(f"unexpected: {', '.join(unexpected)}")
        raise RestoreValidationError(
            f"Backup tables do not match format version 1 ({'; '.join(details)})."
        )

    validated: dict[str, list[dict[str, Any]]] = {}
    for spec in _TABLE_SPECS:
        raw_rows = data[spec.name]
        if not isinstance(raw_rows, list):
            raise RestoreValidationError(f"data.{spec.name} must be a list.")
        expected_columns = {column.key for column in spec.model.__table__.columns}
        rows: list[dict[str, Any]] = []
        for index, raw_row in enumerate(raw_rows):
            location = f"data.{spec.name}[{index}]"
            if not isinstance(raw_row, dict):
                raise RestoreValidationError(f"{location} must be an object.")
            row = cast(dict[str, Any], raw_row)
            actual_columns = set(row)
            optional_defaults = _OPTIONAL_COLUMN_DEFAULTS.get(spec.name, {})
            required_columns = expected_columns - set(optional_defaults)
            if not required_columns <= actual_columns <= expected_columns:
                raise RestoreValidationError(f"{location} fields do not match the backup schema.")
            normalized_row = {**optional_defaults, **row}
            if (
                spec.name == "cards"
                and normalized_row["is_favorite"] is True
                and normalized_row["favorited_at"] is None
            ):
                normalized_row["favorited_at"] = normalized_row.get("updated_at")
            if (
                spec.owns_user_id
                and _parse_uuid(normalized_row["user_id"], location=f"{location}.user_id")
                != export_user_id
            ):
                raise RestoreValidationError(f"{location} is not owned by the exported user.")
            rows.append(normalized_row)
        validated[spec.name] = rows
    return export_user_id, user_settings, validated


def _coerce_value(spec: _TableSpec, field: str, value: object, *, location: str) -> Any:
    column = spec.model.__table__.columns[field]
    if value is None:
        if not column.nullable:
            raise RestoreValidationError(f"{location} cannot be null.")
        return None
    enum_class = getattr(column.type, "enum_class", None)
    if enum_class is not None:
        try:
            return enum_class(value)
        except (TypeError, ValueError) as error:
            raise RestoreValidationError(f"{location} has an unsupported value.") from error
    python_type = column.type.python_type
    if python_type is uuid.UUID:
        return _parse_uuid(value, location=location)
    if python_type is datetime:
        return _parse_datetime(value, location=location)
    if python_type is bool:
        if type(value) is not bool:
            raise RestoreValidationError(f"{location} must be true or false.")
        return value
    if python_type is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise RestoreValidationError(f"{location} must be an integer.")
        return value
    if python_type is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RestoreValidationError(f"{location} must be a number.")
        return float(value)
    if python_type is str:
        if not isinstance(value, str):
            raise RestoreValidationError(f"{location} must be text.")
        return value
    return value


def _build_id_maps(
    rows_by_table: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[uuid.UUID, uuid.UUID]]:
    id_maps: dict[str, dict[uuid.UUID, uuid.UUID]] = {}
    for spec in _TABLE_SPECS:
        if "id" not in spec.model.__table__.columns:
            continue
        table_map: dict[uuid.UUID, uuid.UUID] = {}
        for index, row in enumerate(rows_by_table[spec.name]):
            old_id = _parse_uuid(row["id"], location=f"data.{spec.name}[{index}].id")
            if old_id in table_map:
                raise RestoreValidationError(f"data.{spec.name} contains a duplicate id.")
            table_map[old_id] = uuid.uuid4()
        id_maps[spec.name] = table_map
    return id_maps


def _remap_rows(
    rows_by_table: dict[str, list[dict[str, Any]]],
    *,
    user_id: uuid.UUID,
    id_maps: dict[str, dict[uuid.UUID, uuid.UUID]],
) -> dict[str, list[dict[str, Any]]]:
    remapped: dict[str, list[dict[str, Any]]] = {}
    for spec in _TABLE_SPECS:
        table_rows: list[dict[str, Any]] = []
        for index, row in enumerate(rows_by_table[spec.name]):
            converted = {
                field: _coerce_value(
                    spec,
                    field,
                    value,
                    location=f"data.{spec.name}[{index}].{field}",
                )
                for field, value in row.items()
            }
            if "id" in converted:
                converted["id"] = id_maps[spec.name][converted["id"]]
            if spec.owns_user_id:
                converted["user_id"] = user_id
            for field, target_table in spec.foreign_keys.items():
                old_foreign_id = converted[field]
                if old_foreign_id is None:
                    continue
                new_foreign_id = id_maps[target_table].get(old_foreign_id)
                if new_foreign_id is None:
                    raise RestoreValidationError(
                        f"data.{spec.name}[{index}].{field} references a missing row."
                    )
                converted[field] = new_foreign_id
            if spec.name == "review_logs":
                converted["attempt_id"] = uuid.uuid4()
            table_rows.append(converted)
        remapped[spec.name] = table_rows
    return remapped


def _validate_current_versions(rows_by_table: dict[str, list[dict[str, Any]]]) -> None:
    version_cards = {
        _parse_uuid(row["id"], location="card_versions.id"): _parse_uuid(
            row["card_id"], location="card_versions.card_id"
        )
        for row in rows_by_table["card_versions"]
    }
    for row in rows_by_table["cards"]:
        current_version = row["current_version_id"]
        if current_version is None:
            continue
        card_id = _parse_uuid(row["id"], location="cards.id")
        version_id = _parse_uuid(current_version, location="cards.current_version_id")
        if version_cards.get(version_id) != card_id:
            raise RestoreValidationError("A card current version does not belong to that card.")


def _ensure_empty_account(session: Session, *, user_id: uuid.UUID) -> None:
    for model in _OWNED_ROOT_MODELS:
        existing_id = session.scalar(select(model.id).where(model.user_id == user_id).limit(1))
        if existing_id is not None:
            raise RestoreValidationError(
                "Restore requires an empty account. Export the current account before "
                "using a new account."
            )


def restore_user_export(
    session: Session,
    *,
    user_id: uuid.UUID,
    payload: object,
) -> RestoreResult:
    user = session.get(UserAccount, user_id)
    if user is None:
        raise RestoreValidationError("Target user does not exist.")
    _, user_settings, rows_by_table = _validate_payload(payload)
    _validate_current_versions(rows_by_table)
    _ensure_empty_account(session, user_id=user_id)
    id_maps = _build_id_maps(rows_by_table)
    remapped = _remap_rows(rows_by_table, user_id=user_id, id_maps=id_maps)

    with session.begin_nested():
        user.timezone, user.daily_limit, user.desired_retention = user_settings
        card_current_versions: dict[uuid.UUID, uuid.UUID | None] = {}
        restored_objects: dict[str, list[Base]] = {}
        for spec in _TABLE_SPECS:
            objects: list[Base] = []
            for values in remapped[spec.name]:
                if spec.name == "cards":
                    card_current_versions[values["id"]] = values["current_version_id"]
                    values = {**values, "current_version_id": None}
                model = cast(Any, spec.model)
                objects.append(model(**values))
            session.add_all(objects)
            session.flush()
            restored_objects[spec.name] = objects
            if spec.name == "card_versions":
                for card in restored_objects["cards"]:
                    typed_card = cast(Card, card)
                    typed_card.current_version_id = card_current_versions[typed_card.id]
                session.flush()

    return RestoreResult(counts={spec.name: len(remapped[spec.name]) for spec in _TABLE_SPECS})
