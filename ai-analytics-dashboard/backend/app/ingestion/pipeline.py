import asyncio
import json
import logging
from pathlib import Path
from uuid import uuid4

import pandas as pd
from sqlalchemy import select, text, update

from app.db.models import Dataset, DatasetColumn
from app.db.versions import bump_catalog_version
from app.ingestion.chunker import chunk_text
from app.ingestion.detect import UnsupportedFileError, detect_kind
from app.ingestion.parsers import ParsedTable, ParseError, ParseResult, TextSegment, parse_file
from app.ingestion.tabular import clean_column_names, create_and_load_table, infer_types, to_identifier
from app.util import to_jsonable, vector_literal

log = logging.getLogger(__name__)

DESCRIBE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["descriptions"],
    "properties": {
        "descriptions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["column", "description"],
                "properties": {"column": {"type": "string"}, "description": {"type": "string"}},
            },
        }
    },
}


def _samples(series: pd.Series, n: int = 5) -> list:
    return [to_jsonable(v) for v in pd.unique(series.dropna())[:n]]


async def _set_status(state, dataset_id: int, status: str, error: str | None = None) -> None:
    async with state.sessionmaker() as s:
        ds = await s.get(Dataset, dataset_id)
        if ds is not None:
            ds.status = status
            ds.error = error
            await s.commit()


async def unique_slug(session, base: str) -> str:
    base = to_identifier(base, "dataset")[:50]
    taken = set((await session.scalars(select(Dataset.slug).where(Dataset.slug.like(f"{base}%")))).all())
    taken |= set(
        (await session.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'data'"))).scalars().all()
    )
    slug, k = base, 2
    while slug in taken:
        slug = f"{base}_{k}"
        k += 1
    return slug


async def _target(session, reuse_id: int | None, parent_id: int) -> Dataset:
    if reuse_id is not None:
        return await session.get(Dataset, reuse_id)
    parent = await session.get(Dataset, parent_id)
    ds = Dataset(
        name=parent.source_filename,
        slug=f"pending_child_{parent_id}",
        source_filename=parent.source_filename,
        file_type=parent.file_type,
        stored_path=parent.stored_path,
        uploaded_by=parent.uploaded_by,
        parent_upload_id=parent_id,
        status="processing",
    )
    session.add(ds)
    return ds


async def _describe_columns(state, hint: str, df: pd.DataFrame, types: dict[str, str]) -> dict[str, str]:
    lines = [
        f"- {c} ({types[c]}): e.g. {', '.join(str(v) for v in _samples(df[c], 3))}" for c in list(df.columns)[:60]
    ]
    try:
        out = await state.llm.chat_json(
            purpose="describe_columns",
            system="Write one short, factual description (max 12 words) for each column of a dataset, "
            "based only on its name, type and sample values.",
            user=f"Dataset: {hint}\nColumns:\n" + "\n".join(lines),
            schema=DESCRIBE_SCHEMA,
        )
        return {
            d["column"]: d["description"][:200]
            for d in out.get("descriptions", [])
            if isinstance(d, dict) and d.get("column") in df.columns
        }
    except Exception as exc:  # noqa: BLE001 - descriptions are optional
        log.warning("Column description failed for %s: %s", hint, exc)
        return {}


def _prepare_table(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    df = df.copy()
    df.columns = clean_column_names(df.columns)
    return infer_types(df)


async def _store_table(state, reuse_id: int | None, parent_id: int, original_name: str, table: ParsedTable) -> None:
    # CPU-heavy pandas work runs in a thread so large uploads don't freeze the API
    df, types = await asyncio.to_thread(_prepare_table, table.df)
    descriptions = await _describe_columns(state, table.name_hint, df, types)
    async with state.sessionmaker() as s:
        slug = await unique_slug(s, table.name_hint)
        ds = await _target(s, reuse_id, parent_id)
        is_main = table.name_hint == Path(original_name).stem
        ds.name = original_name if is_main else f"{original_name} ({table.name_hint})"
        ds.slug = slug
        ds.kind = "table"
        await s.flush()
        conn = await s.connection()
        ds.row_count = await create_and_load_table(conn, slug, df, types)
        for col in df.columns:
            s.add(
                DatasetColumn(
                    dataset_id=ds.id,
                    column_name=col,
                    pg_type=types[col],
                    sample_values=_samples(df[col]),
                    description=descriptions.get(col, ""),
                )
            )
        ds.status, ds.error = "ready", None
        await bump_catalog_version(s)  # each committed part becomes visible immediately
        await s.commit()


async def _store_document(
    state, reuse_id: int | None, parent_id: int, original_name: str, segments: list[TextSegment]
) -> None:
    chunks: list[tuple[str, dict]] = []
    for seg in segments:
        for piece in chunk_text(seg.text):
            chunks.append((piece, {"file": original_name, "page": seg.page}))
    if not chunks:
        if reuse_id is not None:
            raise ParseError("The file contains no readable text.")
        return
    embeddings: list[list[float]] = []
    for i in range(0, len(chunks), 100):
        embeddings.extend(await state.llm.embed([c[0] for c in chunks[i : i + 100]]))
    async with state.sessionmaker() as s:
        slug = await unique_slug(s, Path(original_name).stem)
        ds = await _target(s, reuse_id, parent_id)
        ds.name, ds.slug, ds.kind = original_name, slug, "document"
        await s.flush()
        await s.execute(
            text(
                "INSERT INTO vector.chunks (dataset_id, chunk_index, content, metadata, embedding) "
                "VALUES (:dataset_id, :idx, :content, CAST(CAST(:meta AS text) AS jsonb), "
                "CAST(CAST(:emb AS text) AS vector))"
            ),
            [
                {"dataset_id": ds.id, "idx": i, "content": c, "meta": json.dumps(m), "emb": vector_literal(e)}
                for i, ((c, m), e) in enumerate(zip(chunks, embeddings))
            ],
        )
        ds.chunk_count = len(chunks)
        ds.status, ds.error = "ready", None
        await bump_catalog_version(s)
        await s.commit()


async def _store(state, dataset_id: int, original_name: str, result: ParseResult) -> None:
    reuse: int | None = dataset_id
    for table in result.tables:
        await _store_table(state, reuse, dataset_id, original_name, table)
        reuse = None
    if result.segments:
        await _store_document(state, reuse, dataset_id, original_name, result.segments)


async def _record_failure(state, dataset_id: int, message: str) -> None:
    """Mark the upload failed — or, if an earlier part already loaded, add a failed part row."""
    async with state.sessionmaker() as s:
        ds = await s.get(Dataset, dataset_id)
        if ds is None:
            return
        if ds.status == "ready":
            s.add(
                Dataset(
                    name=f"{ds.source_filename} (part failed)",
                    slug=f"failed_{uuid4().hex[:16]}",
                    kind="pending",
                    source_filename=ds.source_filename,
                    file_type=ds.file_type,
                    stored_path=ds.stored_path,
                    uploaded_by=ds.uploaded_by,
                    parent_upload_id=ds.id,
                    status="failed",
                    error=message,
                )
            )
        else:
            ds.status, ds.error = "failed", message
        await s.commit()


async def recover_interrupted(sessionmaker) -> int:
    """At startup: uploads left pending/processing by a restart can never finish — mark them failed."""
    async with sessionmaker() as s:
        result = await s.execute(
            update(Dataset)
            .where(Dataset.status.in_(("pending", "processing")))
            .values(status="failed", error="Processing was interrupted by a server restart. Please upload the file again.")
        )
        await s.commit()
        return result.rowcount or 0


async def process_upload(state, dataset_id: int, path: Path, original_name: str) -> None:
    async with state.ingest_lock:
        try:
            await _set_status(state, dataset_id, "processing")
            kind = detect_kind(original_name, Path(path).read_bytes()[:8192])
            result = await asyncio.to_thread(parse_file, Path(path), kind, original_name)
            await _store(state, dataset_id, original_name, result)
        except Exception as exc:  # noqa: BLE001
            log.exception("Ingestion failed for dataset %s", dataset_id)
            if isinstance(exc, (ParseError, UnsupportedFileError)):
                message = str(exc)
            else:
                message = f"Processing failed: {type(exc).__name__}: {exc}"
            await _record_failure(state, dataset_id, message[:1000])
