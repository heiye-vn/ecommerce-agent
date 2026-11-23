import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from testcontainers.community.postgres import PostgresContainer

from alembic import command
from app.core.db import create_database_engine
from app.models import Organization, Shop
from app.services.seed import seed_demo_operator

BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture
def migrated_database(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(BACKEND / "alembic.ini"))
    command.upgrade(config, "head")
    try:
        yield
    finally:
        command.downgrade(config, "base")


@pytest_asyncio.fixture
async def database(
    migrated_database: None, database_url: str
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_database_engine(database_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_demo_data_matches_promotion_baseline(database: async_sessionmaker[AsyncSession]) -> None:
    from app.services.seed_catalog import seed_catalog

    async with database.begin() as session:
        await seed_demo_operator(session, "test-password")
        other_org = Organization(name="外部组织")
        session.add(other_org)
        await session.flush()
        other_shop = Shop(organization_id=other_org.id, name="轻食旗舰店", platform="sandbox")
        session.add(other_shop)
        await session.flush()

    async with database.begin() as session:
        assert await seed_catalog(session) is True

    async with database() as session:
        sku = (await session.execute(text("""
            SELECT p.shop_id, s.current_price, i.available_qty
            FROM skus s JOIN products p ON p.id = s.product_id
            JOIN inventory i ON i.sku_id = s.id
            WHERE s.sku_code = 'SKU-001'
        """))).one()
        assert sku.shop_id != other_shop.id
        assert (sku.current_price, sku.available_qty) == (Decimal("199.00"), 300)
        sales = await session.scalar(text("""
            SELECT sum(oi.quantity)
            FROM order_items oi JOIN orders o ON o.id = oi.order_id
            JOIN skus s ON s.id = oi.sku_id
            WHERE o.shop_id = :shop_id AND s.sku_code = 'SKU-001' AND o.status = 'paid'
                AND o.paid_at >= TIMESTAMPTZ '2026-11-14 00:00:00+08'
                AND o.paid_at < TIMESTAMPTZ '2026-11-21 00:00:00+08'
        """), {"shop_id": sku.shop_id})
        assert sales == 120
        assert await session.scalar(text("SELECT count(*) FROM orders")) == 7
        assert await session.scalar(text("SELECT count(*) FROM orders WHERE shop_id = :shop_id"),
                                    {"shop_id": other_shop.id}) == 0
        assert await session.scalar(text("SELECT count(*) FROM users")) == 1


@pytest.mark.asyncio
async def test_reseeding_does_not_duplicate_or_overwrite_data(database: async_sessionmaker[AsyncSession]) -> None:
    from app.services.seed_catalog import seed_catalog

    async with database.begin() as session:
        await seed_demo_operator(session, "test-password")
    async with database.begin() as session:
        assert await seed_catalog(session) is True
        await session.execute(text("UPDATE inventory SET available_qty = 250"))
    async with database.begin() as session:
        assert await seed_catalog(session) is False
        assert await session.scalar(text("SELECT available_qty FROM inventory")) == 250
        assert await session.scalar(text("SELECT count(*) FROM orders")) == 7
        assert await session.scalar(text("SELECT count(*) FROM order_items")) == 7
        assert await session.scalar(text("SELECT count(*) FROM inventory_movements")) == 1
        assert await session.scalar(text("SELECT count(*) FROM price_histories")) == 1


@pytest.mark.asyncio
async def test_seed_requires_existing_demo_shop(database: async_sessionmaker[AsyncSession]) -> None:
    from app.services.seed_catalog import seed_catalog

    async with database.begin() as session:
        with pytest.raises(ValueError, match="demo sandbox shop"):
            await seed_catalog(session)
        assert await session.scalar(text("SELECT count(*) FROM products")) == 0


def test_cli_refuses_nonlocal_database() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "scripts.seed_catalog"], cwd=BACKEND,
        env={**os.environ, "DATABASE_URL": "postgresql+asyncpg://sandbox.invalid:7600/commerce_agent"},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 1
    assert "restricted to the local Compose sandbox" in result.stderr
