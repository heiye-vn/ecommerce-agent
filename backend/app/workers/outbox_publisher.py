import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

from aio_pika import DeliveryMode, Message
from aio_pika.abc import AbstractExchange
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import AppSettings
from app.core.db import create_database_engine, create_session_factory
from app.models import OutboxEvent
from app.workers.topology import TopologyConfig, connect_rabbitmq, declare_topology

logger = logging.getLogger(__name__)


async def publish_pending_batch(
    session_factory: async_sessionmaker[AsyncSession],
    exchange: AbstractExchange,
    *,
    batch_size: int = 50,
    backoff_base_seconds: int = 5,
) -> int:
    published_count = 0
    async with session_factory.begin() as session:
        events = list(
            (
                await session.scalars(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.status == "pending",
                        OutboxEvent.available_at <= func.now(),
                    )
                    .order_by(OutboxEvent.id.asc())
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        for event in events:
            body = json.dumps(event.payload, ensure_ascii=False).encode("utf-8")
            message = Message(
                body=body,
                delivery_mode=DeliveryMode.PERSISTENT,
                message_id=event.event_id,
                content_type="application/json",
                headers={"event_type": event.event_type, "shop_id": event.shop_id},
            )
            now = datetime.now(UTC)
            try:
                await exchange.publish(message, routing_key=event.event_type, mandatory=True)
            except Exception as exc:
                event.retry_count += 1
                event.last_error = f"{type(exc).__name__}: {exc}"
                delay = min(300, backoff_base_seconds * (2 ** min(event.retry_count - 1, 6)))
                event.available_at = now + timedelta(seconds=delay)
                logger.warning(
                    "Outbox publish failed: event_id=%s retry=%d error=%s",
                    event.event_id,
                    event.retry_count,
                    type(exc).__name__,
                )
            else:
                event.status = "published"
                event.published_at = now
                event.last_error = None
                published_count += 1
    return published_count


async def run_outbox_publisher(poll_interval_seconds: float = 1.0) -> None:
    settings = AppSettings.load()
    engine = create_database_engine(settings.require_database_url())
    session_factory = create_session_factory(engine)
    connection = await connect_rabbitmq(settings.require_rabbitmq_url())
    try:
        async with connection:
            channel = await connection.channel(publisher_confirms=True)
            topology = await declare_topology(channel, TopologyConfig())
            logger.info("Outbox publisher started: exchange=%s", topology.exchange.name)
            while True:
                published = await publish_pending_batch(session_factory, topology.exchange)
                if published == 0:
                    await asyncio.sleep(poll_interval_seconds)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(run_outbox_publisher())
    except KeyboardInterrupt:
        pass
