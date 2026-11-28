import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import create_database_engine
from app.services.seed_stage4 import seed_stage4_sandbox
from scripts.seed import local_sandbox_url


async def main() -> None:
    database_url = local_sandbox_url()
    engine = create_database_engine(database_url)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions.begin() as session:
            created = await seed_stage4_sandbox(session)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    finally:
        await engine.dispose()

    print(f"Stage 4 sandbox seed result: {created}")


if __name__ == "__main__":
    asyncio.run(main())
