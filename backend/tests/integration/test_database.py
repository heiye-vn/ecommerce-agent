import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker
from testcontainers.community.postgres import PostgresContainer

from alembic import command


async def table_names(database_url: str) -> set[str]:
    from app.core.db import create_database_engine

    engine = create_database_engine(database_url)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
    finally:
        await engine.dispose()


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture
def migrated_database(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[Config]:
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    command.upgrade(config, "head")
    try:
        yield config
    finally:
        command.downgrade(config, "base")


def test_initial_migration_can_be_rolled_back_and_reapplied(
    migrated_database: Config, database_url: str
) -> None:
    expected_tables = {
        "organizations",
        "users",
        "shops",
        "memberships",
        "products",
        "skus",
        "inventory",
        "inventory_movements",
        "price_histories",
        "orders",
        "order_items",
        "agent_sessions",
        "agent_messages",
        "agent_tasks",
        "agent_tool_calls",
        "promotions",
        "promotion_items",
        "approvals",
        "operation_requests",
        "audit_logs",
    }
    command.downgrade(migrated_database, "base")
    assert expected_tables.isdisjoint(asyncio.run(table_names(database_url)))
    command.upgrade(migrated_database, "head")
    assert expected_tables <= asyncio.run(table_names(database_url))
    command.check(migrated_database)


@pytest.mark.asyncio
async def test_memberships_enforce_organization_and_scope(
    migrated_database: Config, database_url: str
) -> None:
    from app.core.db import create_database_engine
    from app.models.org import Membership, Organization, Role, Shop, User

    engine = create_database_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            organization = Organization(name="本店组织")
            other_organization = Organization(name="外店组织")
            operator = User(name="测试运营")
            session.add_all([organization, other_organization, operator])
            await session.flush()

            shop = Shop(organization_id=organization.id, name="本店", platform="sandbox")
            other_shop = Shop(
                organization_id=other_organization.id, name="外店", platform="sandbox"
            )
            session.add_all([shop, other_shop])
            await session.flush()

            for shop_id in (None, shop.id):
                session.add(
                    Membership(
                        user_id=operator.id,
                        organization_id=organization.id,
                        shop_id=shop_id,
                        role=Role.OPERATOR,
                    )
                )

        async with sessions() as session:
            scopes = await session.scalars(select(Membership.shop_id))
            assert set(scopes) == {None, shop.id}

        for rejected_scope in (other_shop.id, None, shop.id):
            with pytest.raises(IntegrityError):
                async with sessions.begin() as session:
                    session.add(
                        Membership(
                            user_id=operator.id,
                            organization_id=organization.id,
                            shop_id=rejected_scope,
                            role=Role.OPERATOR,
                        )
                    )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_shop_timezone_default_and_order_status_constraint(
    migrated_database: Config, database_url: str
) -> None:
    from decimal import Decimal

    from app.core.db import create_database_engine
    from app.models.catalog import Order
    from app.models.org import Organization, Shop

    engine = create_database_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            org = Organization(name="约束测试组织")
            session.add(org)
            await session.flush()
            shop = Shop(organization_id=org.id, name="默认时区店", platform="sandbox")
            session.add(shop)
            await session.flush()
            await session.refresh(shop)
            assert shop.timezone == "Asia/Shanghai"
            shop_id = shop.id

        with pytest.raises(IntegrityError):
            async with sessions.begin() as session:
                session.add(
                    Order(
                        shop_id=shop_id,
                        order_no="BAD-STATUS-1",
                        status="invalid_status",
                        paid_amount=Decimal("10.00"),
                        paid_at=None,
                    )
                )
    finally:
        await engine.dispose()
