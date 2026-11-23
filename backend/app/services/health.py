import asyncio
from typing import Literal
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

HealthStatus = Literal["ok", "degraded"]


def get_health() -> dict[str, str | list[str]]:
    return {
        "status": "ok",
        "service": "commerce-agent-backend",
        "dependencies": ["postgresql", "redis", "rabbitmq"],
    }


async def _probe_tcp(url: str | None, default_port: int, timeout: float = 1.5) -> str:
    if not url:
        return "unconfigured"
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or default_port
    if not host:
        return "invalid_url"
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.close()
        await writer.wait_closed()
        return "ok"
    except Exception:
        return "unreachable"


async def check_readiness(
    engine: AsyncEngine | None,
    *,
    redis_url: str | None = None,
    rabbitmq_url: str | None = None,
) -> tuple[bool, dict[str, object]]:
    checks: dict[str, str] = {}
    if engine is None:
        checks["postgresql"] = "unconfigured"
    else:
        try:
            async with engine.connect() as conn:
                await asyncio.wait_for(conn.execute(text("SELECT 1")), timeout=2.0)
            checks["postgresql"] = "ok"
        except Exception:
            checks["postgresql"] = "unreachable"

    checks["redis"] = await _probe_tcp(redis_url, 6379)
    checks["rabbitmq"] = await _probe_tcp(rabbitmq_url, 5672)

    # PostgreSQL 为必需依赖；Redis/RabbitMQ 若已配置且在就绪检查阶段探测，全部 ok 才判定就绪
    ready = checks["postgresql"] == "ok" and all(
        checks[name] in {"ok", "unconfigured"} for name in ("redis", "rabbitmq")
    )
    return ready, {
        "status": "ok" if ready else "degraded",
        "service": "commerce-agent-backend",
        "checks": checks,
    }
