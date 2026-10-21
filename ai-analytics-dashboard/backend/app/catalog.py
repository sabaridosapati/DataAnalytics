from dataclasses import dataclass, field

from sqlalchemy import select

from app.db.models import Dataset, DatasetColumn
from app.db.versions import get_catalog_version


@dataclass
class ColumnInfo:
    name: str
    pg_type: str
    samples: list = field(default_factory=list)
    description: str = ""


@dataclass
class DatasetInfo:
    id: int
    slug: str
    name: str
    kind: str  # table | document
    columns: list[ColumnInfo] = field(default_factory=list)
    row_count: int = 0
    chunk_count: int = 0


@dataclass
class Catalog:
    version: int
    datasets: list[DatasetInfo] = field(default_factory=list)

    def get(self, slug: str) -> DatasetInfo | None:
        return next((d for d in self.datasets if d.slug == slug), None)

    def tables(self) -> list[DatasetInfo]:
        return [d for d in self.datasets if d.kind == "table"]

    def documents(self) -> list[DatasetInfo]:
        return [d for d in self.datasets if d.kind == "document"]

    def sql_schema(self, slugs: list[str] | None = None) -> dict[str, dict[str, str]]:
        return {
            d.slug: {c.name: c.pg_type for c in d.columns}
            for d in self.tables()
            if slugs is None or d.slug in slugs
        }

    def summary(self) -> str:
        lines = []
        for d in self.tables():
            cols = ", ".join(f"{c.name} ({c.pg_type})" for c in d.columns[:40])
            more = f", … {len(d.columns) - 40} more" if len(d.columns) > 40 else ""
            lines.append(f"- {d.slug} [table, {d.row_count} rows] from '{d.name}': {cols}{more}")
        for d in self.documents():
            lines.append(f"- {d.slug} [document, {d.chunk_count} text chunks] from '{d.name}'")
        return "\n".join(lines) if lines else "(no datasets)"


async def load_catalog(state) -> Catalog:
    async with state.sessionmaker() as session:
        version = await get_catalog_version(session)
        cached = getattr(state, "catalog_cache", None)
        if cached is not None and cached.version == version:
            return cached
        datasets = (
            await session.scalars(select(Dataset).where(Dataset.status == "ready").order_by(Dataset.id))
        ).all()
        columns = (await session.scalars(select(DatasetColumn).order_by(DatasetColumn.id))).all()
    by_dataset: dict[int, list[ColumnInfo]] = {}
    for c in columns:
        by_dataset.setdefault(c.dataset_id, []).append(ColumnInfo(c.column_name, c.pg_type, c.sample_values or [], c.description or ""))
    catalog = Catalog(
        version=version,
        datasets=[
            DatasetInfo(d.id, d.slug, d.name, d.kind, by_dataset.get(d.id, []), d.row_count, d.chunk_count)
            for d in datasets
        ],
    )
    state.catalog_cache = catalog
    return catalog
