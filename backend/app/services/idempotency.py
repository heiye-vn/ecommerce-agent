import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.reliability import OperationRequest


def compute_request_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IdempotencyClaim:
    record: OperationRequest
    replayed_response: dict[str, Any] | None


async def claim_idempotency(
    session: AsyncSession,
    *,
    organization_id: int,
    shop_id: int,
    operation_type: str,
    idempotency_key: str,
    request_payload: dict[str, Any],
) -> IdempotencyClaim:
    request_hash = compute_request_hash(request_payload)
    stmt = (
        insert(OperationRequest)
        .values(
            organization_id=organization_id,
            shop_id=shop_id,
            operation_type=operation_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            status="processing",
        )
        .on_conflict_do_nothing(constraint="uq_operation_requests_idempotency")
    )
    await session.execute(stmt)

    record = (
        await session.scalars(
            select(OperationRequest)
            .where(
                OperationRequest.organization_id == organization_id,
                OperationRequest.operation_type == operation_type,
                OperationRequest.idempotency_key == idempotency_key,
            )
            .with_for_update()
        )
    ).one()

    if record.request_hash != request_hash or record.shop_id != shop_id:
        raise AppError(
            409,
            "IDEMPOTENCY_CONFLICT",
            "相同的幂等键已被用于不同的请求参数，请勿复用旧幂等键。",
            detail="Idempotency key reused with a different payload",
        )

    if record.status == "succeeded" and record.response_json is not None:
        return IdempotencyClaim(record=record, replayed_response=dict(record.response_json))

    return IdempotencyClaim(record=record, replayed_response=None)


def complete_idempotency(
    record: OperationRequest,
    *,
    resource_type: str,
    resource_id: int,
    response_payload: dict[str, Any],
) -> None:
    record.status = "succeeded"
    record.resource_type = resource_type
    record.resource_id = resource_id
    record.response_json = response_payload
