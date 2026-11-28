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
    price_change_queue_name: str | None = None
    price_change_dlq_name: str | None = None
    price_change_dlq_routing_key: str = "price_change.dlq"
    price_change_routing_keys: tuple[str, ...] = ("price_change.execute.requested",)

    @classmethod
    def production(cls) -> "TopologyConfig":
        return cls(
            refund_queue_name="commerce.jobs.refund",
            refund_dlq_name="commerce.jobs.refund.dlq",
            price_change_queue_name="commerce.jobs.price_change",
            price_change_dlq_name="commerce.jobs.price_change.dlq",
        )


@dataclass(frozen=True)
class DeclaredTopology:
    exchange: AbstractExchange
    dlx: AbstractExchange
    queue: AbstractQueue
    dlq: AbstractQueue
    refund_queue: AbstractQueue | None = None
    refund_dlq: AbstractQueue | None = None
    price_change_queue: AbstractQueue | None = None
    price_change_dlq: AbstractQueue | None = None


async def connect_rabbitmq(rabbitmq_url: str) -> AbstractRobustConnection:
    return await aio_pika.connect_robust(rabbitmq_url)


async def _declare_queue_with_dlq(
    channel: AbstractChannel,
    *,
    exchange: AbstractExchange,
    dlx: AbstractExchange,
    dlx_name: str,
    queue_name: str,
    dlq_name: str,
    dlq_routing_key: str,
    routing_keys: tuple[str, ...],
) -> tuple[AbstractQueue, AbstractQueue]:
    dlq = await channel.declare_queue(dlq_name, durable=True)
    await dlq.bind(dlx, routing_key=dlq_routing_key)
    queue = await channel.declare_queue(
        queue_name,
        durable=True,
        arguments={
            "x-dead-letter-exchange": dlx_name,
            "x-dead-letter-routing-key": dlq_routing_key,
        },
    )
    for routing_key in routing_keys:
        await queue.bind(exchange, routing_key=routing_key)
    return queue, dlq


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
    queue, dlq = await _declare_queue_with_dlq(
        channel,
        exchange=exchange,
        dlx=dlx,
        dlx_name=resolved.dlx_name,
        queue_name=resolved.queue_name,
        dlq_name=resolved.dlq_name,
        dlq_routing_key=resolved.dlq_routing_key,
        routing_keys=resolved.routing_keys,
    )

    refund_queue: AbstractQueue | None = None
    refund_dlq: AbstractQueue | None = None
    if resolved.refund_queue_name:
        refund_queue, refund_dlq = await _declare_queue_with_dlq(
            channel,
            exchange=exchange,
            dlx=dlx,
            dlx_name=resolved.dlx_name,
            queue_name=resolved.refund_queue_name,
            dlq_name=resolved.refund_dlq_name or f"{resolved.refund_queue_name}.dlq",
            dlq_routing_key=resolved.refund_dlq_routing_key,
            routing_keys=resolved.refund_routing_keys,
        )

    price_change_queue: AbstractQueue | None = None
    price_change_dlq: AbstractQueue | None = None
    if resolved.price_change_queue_name:
        price_change_queue, price_change_dlq = await _declare_queue_with_dlq(
            channel,
            exchange=exchange,
            dlx=dlx,
            dlx_name=resolved.dlx_name,
            queue_name=resolved.price_change_queue_name,
            dlq_name=resolved.price_change_dlq_name or f"{resolved.price_change_queue_name}.dlq",
            dlq_routing_key=resolved.price_change_dlq_routing_key,
            routing_keys=resolved.price_change_routing_keys,
        )

    return DeclaredTopology(
        exchange=exchange,
        dlx=dlx,
        queue=queue,
        dlq=dlq,
        refund_queue=refund_queue,
        refund_dlq=refund_dlq,
        price_change_queue=price_change_queue,
        price_change_dlq=price_change_dlq,
    )
