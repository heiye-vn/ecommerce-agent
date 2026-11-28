from dataclasses import dataclass

import aio_pika
from aio_pika import ExchangeType
from aio_pika.abc import (
    AbstractChannel,
    AbstractExchange,
    AbstractQueue,
    AbstractRobustConnection,
)


@dataclass(frozen=True)
class TopologyConfig:
    exchange_name: str = "commerce.events"
    dlx_name: str = "commerce.dlx"
    queue_name: str = "commerce.jobs.promotion"
    dlq_name: str = "commerce.jobs.promotion.dlq"
    dlq_routing_key: str = "promotion.dlq"
    routing_keys: tuple[str, ...] = (
        "promotion.schedule.requested",
        "promotion.activate.requested",
        "promotion.complete.requested",
    )
    refund_queue_name: str | None = None
    refund_dlq_name: str | None = None
    refund_dlq_routing_key: str = "refund.dlq"
    refund_routing_keys: tuple[str, ...] = ("refund.execute.requested",)

    @classmethod
    def production(cls) -> "TopologyConfig":
        return cls(
            refund_queue_name="commerce.jobs.refund",
            refund_dlq_name="commerce.jobs.refund.dlq",
        )


@dataclass(frozen=True)
class DeclaredTopology:
    exchange: AbstractExchange
    dlx: AbstractExchange
    queue: AbstractQueue
    dlq: AbstractQueue
    refund_queue: AbstractQueue | None = None
    refund_dlq: AbstractQueue | None = None


async def connect_rabbitmq(rabbitmq_url: str) -> AbstractRobustConnection:
    return await aio_pika.connect_robust(rabbitmq_url)


async def declare_topology(
    channel: AbstractChannel,
    config: TopologyConfig | None = None,
) -> DeclaredTopology:
    resolved = config or TopologyConfig()
    exchange = await channel.declare_exchange(
        resolved.exchange_name,
        ExchangeType.DIRECT,
        durable=True,
    )
    dlx = await channel.declare_exchange(
        resolved.dlx_name,
        ExchangeType.DIRECT,
        durable=True,
    )
    dlq = await channel.declare_queue(resolved.dlq_name, durable=True)
    await dlq.bind(dlx, routing_key=resolved.dlq_routing_key)

    queue = await channel.declare_queue(
        resolved.queue_name,
        durable=True,
        arguments={
            "x-dead-letter-exchange": resolved.dlx_name,
            "x-dead-letter-routing-key": resolved.dlq_routing_key,
        },
    )
    for routing_key in resolved.routing_keys:
        await queue.bind(exchange, routing_key=routing_key)

    refund_queue: AbstractQueue | None = None
    refund_dlq: AbstractQueue | None = None
    if resolved.refund_queue_name:
        dlq_name = resolved.refund_dlq_name or f"{resolved.refund_queue_name}.dlq"
        refund_dlq = await channel.declare_queue(dlq_name, durable=True)
        await refund_dlq.bind(dlx, routing_key=resolved.refund_dlq_routing_key)
        refund_queue = await channel.declare_queue(
            resolved.refund_queue_name,
            durable=True,
            arguments={
                "x-dead-letter-exchange": resolved.dlx_name,
                "x-dead-letter-routing-key": resolved.refund_dlq_routing_key,
            },
        )
        for routing_key in resolved.refund_routing_keys:
            await refund_queue.bind(exchange, routing_key=routing_key)

    return DeclaredTopology(
        exchange=exchange,
        dlx=dlx,
        queue=queue,
        dlq=dlq,
        refund_queue=refund_queue,
        refund_dlq=refund_dlq,
    )
