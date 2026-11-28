import asyncio

from app.core.config import AppSettings
from app.core.db import create_database_engine, create_session_factory
from app.services.knowledge import build_embedding_client
from app.services.seed_stage5b import seed_stage5b_sandbox


async def _main() -> None:
    settings = AppSettings.load()
    engine = create_database_engine(settings.require_database_url())
    session_factory = create_session_factory(engine)
    embedding_client = build_embedding_client(settings) if settings.dashscope_api_key else None
    try:
        outcome = await seed_stage5b_sandbox(session_factory, embedding_client=embedding_client)
        print(f"Stage 5B seed outcome: {outcome}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
