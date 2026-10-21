"""One-off clean-up of structured data left in Postgres by the pre-DuckDB version.

Usage (from the backend folder):
    python scripts/purge_postgres_data.py          # dry run: shows what would be removed
    python scripts/purge_postgres_data.py --yes    # actually drop data.* tables and 'table' dataset rows

Users must re-upload their structured files afterwards (they are stored as DuckDB files now).
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.db.session import create_engine_and_sessionmaker  # noqa: E402
from app.maintenance import purge_structured_data  # noqa: E402


async def main(apply: bool) -> None:
    engine, _ = create_engine_and_sessionmaker(get_settings().database_url)
    try:
        out = await purge_structured_data(engine, apply)
    finally:
        await engine.dispose()
    verb = "Dropped" if apply else "Would drop"
    print(f"{verb} {len(out['dropped_tables'])} Postgres table(s): {', '.join(out['dropped_tables']) or '-'}")
    print(f"{'Deleted' if apply else 'Would delete'} {out['deleted_dataset_rows']} dataset row(s) of kind 'table'.")
    if not apply:
        print("Dry run only. Re-run with --yes to apply.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", action="store_true", help="apply the changes (default is a dry run)")
    asyncio.run(main(parser.parse_args().yes))
