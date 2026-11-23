from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.org import User
from app.models.reliability import AuditLog
from app.schemas.promotions import AuditLogEntry


async def write_audit_log(
    session: AsyncSession,
    *,
    organization_id: int,
    shop_id: int,
    actor_id: int,
    action: str,
    resource_type: str,
    resource_id: int,
    before_json: dict[str, Any] | None = None,
    after_json: dict[str, Any] | None = None,
    metadata_json: dict[str, Any] | None = None,
) -> AuditLog:
    entry = AuditLog(
        organization_id=organization_id,
        shop_id=shop_id,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        before_json=before_json,
        after_json=after_json,
        metadata_json=metadata_json,
    )
    session.add(entry)
    await session.flush()
    return entry


async def list_resource_audit_logs(
    session: AsyncSession,
    *,
    shop_id: int,
    resource_type: str,
    resource_id: int,
) -> list[AuditLogEntry]:
    query = (
        select(
            AuditLog.id,
            AuditLog.action,
            AuditLog.actor_id,
            User.name.label("actor_name"),
            AuditLog.before_json,
            AuditLog.after_json,
            AuditLog.metadata_json,
            AuditLog.created_at,
        )
        .join(User, User.id == AuditLog.actor_id)
        .where(
            AuditLog.shop_id == shop_id,
            AuditLog.resource_type == resource_type,
            AuditLog.resource_id == resource_id,
        )
        .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
    )
    return [AuditLogEntry.model_validate(row) for row in (await session.execute(query)).mappings()]
