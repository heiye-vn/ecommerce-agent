import asyncio
import os
from pathlib import Path
from secrets import token_urlsafe

from dotenv import dotenv_values
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import create_database_engine, load_database_url
from app.services.seed import DEMO_USERNAME, seed_demo_operator


def local_sandbox_url() -> str:
    configured_url = load_database_url()
    database_url = make_url(configured_url)
    if (
        database_url.drivername != "postgresql+asyncpg"
        or database_url.host not in {"localhost", "127.0.0.1", "::1"}
        or database_url.port != 7600
        or database_url.database != "commerce_agent"
        or database_url.query
    ):
        raise SystemExit("Seed is restricted to the local Compose sandbox on port 7600")
    return configured_url


async def main() -> None:
    database_url = local_sandbox_url()
    credentials = Path(__file__).resolve().parents[2] / ".env.demo-login"
    engine = create_database_engine(database_url)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions.begin() as session:
            has_credentials = credentials.exists()
            password = dotenv_values(credentials).get("DEMO_LOGIN_PASSWORD") if has_credentials else token_urlsafe(24)
            if not password:
                raise SystemExit(f"{credentials.name} is missing DEMO_LOGIN_PASSWORD; no changes made")
            created = await seed_demo_operator(session, password)
            if created and not has_credentials:
                descriptor = os.open(credentials, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w") as file:
                    file.write(f"DEMO_LOGIN_USERNAME={DEMO_USERNAME}\nDEMO_LOGIN_PASSWORD={password}\n")
        if created:
            print(f"Created {DEMO_USERNAME}. Credentials: {credentials.name}")
        else:
            print(f"{DEMO_USERNAME} already exists; no changes made")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
