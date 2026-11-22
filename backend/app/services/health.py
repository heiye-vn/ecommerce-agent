from typing import Literal


HealthStatus = Literal["ok"]


def get_health() -> dict[str, HealthStatus | str | list[str]]:
    return {
        "status": "ok",
        "service": "commerce-agent-backend",
        "dependencies": ["postgresql", "redis", "rabbitmq"],
    }
