from sqlalchemy import select, text

from app.db.models import Dataset, DatasetColumn, User
from app.db.versions import get_catalog_version
from app.maintenance import purge_structured_data


async def seed(db):
    async with db.engine.begin() as conn:
        await conn.execute(text('CREATE TABLE data."old_sales" (id int)'))
        await conn.execute(text('CREATE TABLE data."old_hr" (id int)'))
    async with db.sessionmaker() as s:
        s.add(User(email="keep@example.com", password_hash="x", role="user", is_verified=True))
        table = Dataset(name="old.csv", slug="old_sales", kind="table", source_filename="old.csv", file_type="csv", status="ready")
        doc = Dataset(name="notes.txt", slug="notes", kind="document", source_filename="notes.txt", file_type="txt", status="ready")
        s.add_all([table, doc])
        await s.flush()
        s.add(DatasetColumn(dataset_id=table.id, column_name="id", pg_type="bigint"))
        await s.commit()


async def data_tables(db):
    async with db.engine.connect() as c:
        return set((await c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'data'"))).scalars())


async def test_dry_run_changes_nothing(db):
    await seed(db)
    out = await purge_structured_data(db.engine, apply=False)
    assert out == {"applied": False, "dropped_tables": ["old_hr", "old_sales"], "deleted_dataset_rows": 1}
    assert await data_tables(db) == {"old_hr", "old_sales"}
    async with db.sessionmaker() as s:
        assert len((await s.scalars(select(Dataset))).all()) == 2


async def test_apply_drops_tables_and_table_rows_only(db):
    await seed(db)
    async with db.sessionmaker() as s:
        before = await get_catalog_version(s)
    out = await purge_structured_data(db.engine, apply=True)
    assert out["applied"] is True and out["deleted_dataset_rows"] == 1
    assert await data_tables(db) == set()
    async with db.sessionmaker() as s:
        assert [d.slug for d in (await s.scalars(select(Dataset))).all()] == ["notes"]
        assert (await s.scalars(select(DatasetColumn))).all() == []
        assert (await s.scalar(select(User).where(User.email == "keep@example.com"))) is not None
        assert await get_catalog_version(s) == before + 1


async def test_apply_is_idempotent(db):
    await seed(db)
    await purge_structured_data(db.engine, apply=True)
    out = await purge_structured_data(db.engine, apply=True)
    assert out["dropped_tables"] == [] and out["deleted_dataset_rows"] == 0
