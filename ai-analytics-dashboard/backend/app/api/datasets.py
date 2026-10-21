import asyncio
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import admin_user, current_user, get_session
from app.api.errors import api_error
from app.api.ratelimit import limiter
from app.db.models import Dataset, DatasetColumn, User
from app.db.versions import bump_catalog_version
from app.duckdb_store import DuckDBError, preview_table, remove_upload_file, upload_db_path
from app.ingestion.detect import UnsupportedFileError, detect_kind
from app.ingestion.pipeline import process_upload
from app.util import to_jsonable

router = APIRouter(prefix="/api/datasets", tags=["datasets"])
MAX_FILES = 10


class DatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    slug: str
    kind: str
    source_filename: str
    file_type: str
    parent_upload_id: int | None
    row_count: int
    chunk_count: int
    status: str
    error: str | None
    created_at: datetime


class ColumnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    column_name: str
    pg_type: str
    sample_values: list
    description: str


class DatasetDetail(DatasetOut):
    columns: list[ColumnOut]
    preview: dict


@router.post("/upload", status_code=202, response_model=list[DatasetOut])
@limiter.limit("10/minute")
async def upload(
    request: Request,
    background: BackgroundTasks,
    files: list[UploadFile] = File(...),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    settings = request.app.state.settings
    if not files:
        raise api_error(400, "no_files", "No files were uploaded.")
    if len(files) > MAX_FILES:
        raise api_error(400, "too_many_files", f"Upload at most {MAX_FILES} files at a time.")
    limit = settings.max_upload_mb * 1024 * 1024
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved: list[tuple[str, Path, str]] = []  # (original name, stored path, file kind)
    written: list[Path] = []
    try:
        for f in files:
            name = Path(f.filename or "upload").name[:200]
            dest = upload_dir / f"{uuid4().hex}{Path(name).suffix.lower()}"
            written.append(dest)
            size, head = 0, b""
            with dest.open("wb") as out:
                while chunk := await f.read(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise api_error(413, "file_too_large", f"'{name}' exceeds the {settings.max_upload_mb} MB limit.")
                    if len(head) < 8192:
                        head += chunk[: 8192 - len(head)]
                    out.write(chunk)
            if size == 0:
                raise api_error(400, "empty_file", f"'{name}' is empty.")
            try:
                kind = detect_kind(name, head)
            except UnsupportedFileError as exc:
                raise api_error(415, "unsupported_file", f"'{name}': {exc}")
            saved.append((name, dest, kind.value))
    except Exception:
        for path in written:
            path.unlink(missing_ok=True)
        raise

    created: list[Dataset] = []
    for name, path, kind in saved:
        ds = Dataset(
            name=name,
            slug=f"pending_{uuid4().hex[:16]}",
            kind="pending",
            source_filename=name,
            file_type=kind,
            stored_path=str(path),
            uploaded_by=user.id,
            status="pending",
        )
        session.add(ds)
        created.append(ds)
    await session.commit()
    for ds, (name, path, _) in zip(created, saved):
        background.add_task(process_upload, request.app.state, ds.id, path, name)
    return created


@router.get("", response_model=list[DatasetOut])
async def list_datasets(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    return (await session.scalars(select(Dataset).order_by(Dataset.created_at.desc(), Dataset.id.desc()))).all()


@router.get("/{dataset_id}", response_model=DatasetDetail)
async def get_dataset(
    dataset_id: int, request: Request, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    ds = await session.get(Dataset, dataset_id)
    if ds is None:
        raise api_error(404, "not_found", "Dataset not found.")
    columns = (
        await session.scalars(select(DatasetColumn).where(DatasetColumn.dataset_id == ds.id).order_by(DatasetColumn.id))
    ).all()
    preview: dict = {}
    if ds.status == "ready" and ds.kind == "table":
        path = upload_db_path(request.app.state.settings.duckdb_path, ds.parent_upload_id or ds.id)
        try:
            cols, rows = await asyncio.to_thread(preview_table, path, ds.slug, 20)
            preview = {"columns": cols, "rows": [[to_jsonable(v) for v in r] for r in rows]}
        except DuckDBError:
            preview = {}  # file missing or unreadable: show the schema without a preview
    elif ds.status == "ready" and ds.kind == "document":
        rows = await session.execute(
            text(
                "SELECT chunk_index, content, metadata FROM vector.chunks WHERE dataset_id = :id "
                "ORDER BY chunk_index LIMIT 20"
            ),
            {"id": ds.id},
        )
        preview = {"chunks": [{"index": r.chunk_index, "content": r.content, "metadata": r.metadata} for r in rows]}
    base = DatasetOut.model_validate(ds).model_dump()
    return DatasetDetail(**base, columns=[ColumnOut.model_validate(c) for c in columns], preview=preview)


@router.delete("/{dataset_id}", status_code=204)
async def delete_dataset(
    dataset_id: int, request: Request, admin: User = Depends(admin_user), session: AsyncSession = Depends(get_session)
):
    ds = await session.get(Dataset, dataset_id)
    if ds is None:
        raise api_error(404, "not_found", "Dataset not found.")
    if ds.status in ("pending", "processing"):
        raise api_error(409, "busy", "This dataset is still being processed. Try again shortly.")
    if ds.kind == "table":
        upload_id = ds.parent_upload_id or ds.id
        remaining = await session.scalar(
            select(func.count()).select_from(Dataset).where(
                Dataset.kind == "table", Dataset.id != ds.id, (Dataset.id == upload_id) | (Dataset.parent_upload_id == upload_id)
            )
        )
        if not remaining:
            try:
                remove_upload_file(upload_db_path(request.app.state.settings.duckdb_path, upload_id))
            except PermissionError:
                raise api_error(409, "busy", "The data file is in use by a running query. Try again shortly.")
    stored = ds.stored_path
    await session.delete(ds)
    await bump_catalog_version(session)
    await session.commit()
    if stored:
        remaining_refs = await session.scalar(select(func.count()).select_from(Dataset).where(Dataset.stored_path == stored))
        if not remaining_refs:
            Path(stored).unlink(missing_ok=True)
    return Response(status_code=204)