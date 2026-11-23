import asyncio
from pathlib import Path
from secrets import token_urlsafe

from dotenv import dotenv_values
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import create_database_engine
from app.services.seed_stage2 import APPROVER_USERNAME, seed_stage2_sandbox
from scripts.seed import local_sandbox_url


async def main() -> None:
    database_url = local_sandbox_url()
    credentials = Path(__file__).resolve().parents[2] / ".env.demo-login"
    existing = dotenv_values(credentials) if credentials.exists() else {}
    approver_password = existing.get("DEMO_APPROVER_PASSWORD") or token_urlsafe(24)

    engine = create_database_engine(database_url)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions.begin() as session:
            created = await seed_stage2_sandbox(session, approver_password)
            if created["approver"] and "DEMO_APPROVER_PASSWORD" not in existing:
                with credentials.open("a", encoding="utf-8") as file:
                    file.write(
                        f"DEMO_APPROVER_USERNAME={APPROVER_USERNAME}\n"
                        f"DEMO_APPROVER_PASSWORD={approver_password}\n"
                    )
    except ValueError as error:
        raise SystemExit(str(error)) from None
    finally:
        await engine.dispose()

    print(f"Stage 2 sandbox seed result: {created}")


if __name__ == "__main__":
    asyncio.run(main())
