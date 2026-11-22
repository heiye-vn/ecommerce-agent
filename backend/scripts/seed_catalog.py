import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import create_database_engine
from app.services.seed_catalog import SKU_CODE, seed_catalog
from scripts.seed import local_sandbox_url


async def main() -> None:
    engine = create_database_engine(local_sandbox_url())
    try:
        sessions = async_sessionmaker(engine)
        async with sessions.begin() as session:
            created = await seed_catalog(session)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    finally:
        await engine.dispose()

    if created:
        print(f"Seeded local catalog and orders for {SKU_CODE}")
    else:
        print(f"{SKU_CODE} already exists in the demo shop; no changes made")


if __name__ == "__main__":
    asyncio.run(main())
