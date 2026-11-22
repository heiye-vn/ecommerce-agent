import os
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def read_setting(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        env_file = Path(__file__).resolve().parents[3] / ".env"
        value = dotenv_values(env_file).get(name)
    return value


def load_database_url() -> str:
    database_url = read_setting("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL must be set in the environment or the project .env")
    return database_url


def create_database_engine(database_url: str | None = None) -> AsyncEngine:
    return create_async_engine(database_url if database_url is not None else load_database_url())
