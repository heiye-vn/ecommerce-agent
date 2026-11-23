import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


def load_env_file() -> Mapping[str, str | None]:
    if not ENV_FILE.exists():
        return {}
    return dotenv_values(ENV_FILE)


def read_setting(name: str, env_file_values: Mapping[str, str | None] | None = None) -> str | None:
    value = os.environ.get(name)
    if value is not None:
        return value
    values = env_file_values if env_file_values is not None else load_env_file()
    return values.get(name)


def load_database_url() -> str:
    database_url = read_setting("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL must be set in the environment or the project .env")
    return database_url


def create_database_engine(database_url: str | None = None) -> AsyncEngine:
    return create_async_engine(database_url if database_url is not None else load_database_url())


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
