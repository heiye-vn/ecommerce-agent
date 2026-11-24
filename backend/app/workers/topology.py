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


@dataclass(frozen=True)
class DeclaredTopology:
    exchange: AbstractExchange
    dlx: AbstractExchange
    queue: AbstractQueue
    dlq: AbstractQueue


async def connect_rabbitmq(rabbitmq_url: str) -> AbstractRobustConnection:
    return await aio_pika.connect_robust(rabbitmq_url)


async def declare_topology(
    channel: AbstractChannel,
    config: TopologyConfig = TopologyConfig(),
) -> DeclaredTopology:
    exchange = await channel.declare_exchange(
        config.exchange_name,
        ExchangeType.DIRECT,
        durable=True,
    )
    dlx = await channel.declare_exchange(
        config.dlx_name,
        ExchangeType.DIRECT,
        durable=True,
    )
    dlq = await channel.declare_queue(config.dlq_name, durable=True)
    await dlq.bind(dlx, routing_key=config.dlq_routing_key)

    queue = await channel.declare_queue(
        config.queue_name,
        durable=True,
        arguments={
            "x-dead-letter-exchange": config.dlx_name,
            "x-dead-letter-routing-key": config.dlq_routing_key,
        },
    )
    for routing_key in config.routing_keys:
        await queue.bind(exchange, routing_key=routing_key)

    return DeclaredTopology(exchange=exchange, dlx=dlx, queue=queue, dlq=dlq)
