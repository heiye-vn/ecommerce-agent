import asyncio
import inspect
import json
import logging
from collections.abc import Callable
from typing import Any

from aio_pika.abc import AbstractIncomingMessage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import BusinessClock
from app.core.config import AppSettings
from app.core.db import create_database_engine, create_session_factory
from app.services.jobs import (
    FaultInjector,
    TransientJobError,
    execute_job_in_transaction,
    record_job_transient_failure,
)
from app.workers.topology import TopologyConfig, connect_rabbitmq, declare_topology

logger = logging.getLogger(__name__)

AfterCommitHook = Callable[[AbstractIncomingMessage], Any]


async def handle_incoming_message(
    message: AbstractIncomingMessage,
    session_factory: async_sessionmaker[AsyncSession],
    clock: BusinessClock,
    *,
    fault_injector: FaultInjector | None = None,
    after_commit_hook: AfterCommitHook | None = None,
    backoff_base_seconds: int = 5,
) -> str:
    try:
        payload = json.loads(message.body.decode("utf-8"))
        job_id = int(payload["job_id"])
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        logger.warning("Rejecting malformed message to DLQ: message_id=%s", message.message_id)
        await message.reject(requeue=False)
        return "malformed"

    try:
        async with session_factory.begin() as session:
            status, _msg = await execute_job_in_transaction(
                session,
                job_id=job_id,
                clock=clock,
                fault_injector=fault_injector,
            )
    except TransientJobError as exc:
        async with session_factory.begin() as failure_session:
            outcome = await record_job_transient_failure(
                failure_session,
                job_id=job_id,
                error_message=str(exc) or type(exc).__name__,
                backoff_base_seconds=backoff_base_seconds,
            )
        if outcome == "retrying":
            await message.ack()
            return "retrying"
        await message.reject(requeue=False)
        return "dead_lettered"

    if after_commit_hook is not None:
        hook_result = after_commit_hook(message)
        if inspect.isawaitable(hook_result):
            await hook_result
        return "after_commit_hook"

    await message.ack()
    return status


async def run_consumer(prefetch_count: int = 10) -> None:
    settings = AppSettings.load()
    clock = BusinessClock(settings.sandbox_business_date)
    engine = create_database_engine(settings.require_database_url())
    session_factory = create_session_factory(engine)
    connection = await connect_rabbitmq(settings.require_rabbitmq_url())
    try:
        async with connection:
            channel = await connection.channel()
            await channel.set_qos(prefetch_count=prefetch_count)
            topology = await declare_topology(channel, TopologyConfig())
            logger.info("Consumer worker started: queue=%s", topology.queue.name)

            async with topology.queue.iterator() as queue_iter:
                async for incoming in queue_iter:
                    await handle_incoming_message(incoming, session_factory, clock)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(run_consumer())
    except KeyboardInterrupt:
        pass
