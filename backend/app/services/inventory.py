from typing import cast

from sqlalchemy import and_, case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import Inventory, InventoryMovement, Product, Sku, User
from app.schemas.inventory import (
    InventoryMovementCreateRequest,
    InventoryMovementItem,
    InventoryMovementMutationResponse,
    InventoryMovementType,
    InventoryOverviewResponse,
    InventorySnapshot,
)
from app.services.audit import write_audit_log
from app.services.idempotency import claim_idempotency, complete_idempotency

LOW_STOCK_THRESHOLD = 30


def _to_snapshot(
    *,
    sku: Sku,
    product_name: str,
    category: str,
    effective_status: str,
    inventory: Inventory | None,
) -> InventorySnapshot:
    available = inventory.available_qty if inventory is not None else None
    reserved = inventory.reserved_qty if inventory is not None else None
    total = (available + reserved) if (available is not None and reserved is not None) else None
    version = inventory.version if inventory is not None else None
    low_stock = (
        effective_status == "active"
        and available is not None
        and available < LOW_STOCK_THRESHOLD
    )
    return InventorySnapshot(
        sku_id=sku.id,
        sku_code=sku.sku_code,
        product_name=product_name,
        category=category,
        status=effective_status,
        available_qty=available,
        reserved_qty=reserved,
        total_qty=total,
        version=version,
        low_stock=low_stock,
        low_stock_threshold=LOW_STOCK_THRESHOLD,
    )


async def get_inventory_overview(session: AsyncSession, shop_id: int) -> InventoryOverviewResponse:
    query = (
        select(
            Sku,
            Product.name,
            Product.category,
            case((Product.status != "active", Product.status), else_=Sku.status),
            Inventory,
        )
        .join(Product, and_(Sku.shop_id == Product.shop_id, Sku.product_id == Product.id))
        .outerjoin(Inventory, Inventory.sku_id == Sku.id)
        .where(Sku.shop_id == shop_id)
        .order_by(Sku.sku_code.asc())
    )
    rows = (await session.execute(query)).all()
    items = [
        _to_snapshot(
            sku=sku,
            product_name=product_name,
            category=category,
            effective_status=effective_status,
            inventory=inventory,
        )
        for sku, product_name, category, effective_status, inventory in rows
    ]
    return InventoryOverviewResponse(
        low_stock_threshold=LOW_STOCK_THRESHOLD,
        low_stock_count=sum(1 for item in items if item.low_stock),
        items=items,
    )


async def list_inventory_movements(
    session: AsyncSession,
    shop_id: int,
    *,
    sku_code: str | None = None,
    movement_type: InventoryMovementType | None = None,
    limit: int = 50,
) -> list[InventoryMovementItem]:
    query = (
        select(InventoryMovement, Sku.sku_code, Product.name, User.name)
        .join(Sku, Sku.id == InventoryMovement.sku_id)
        .join(Product, and_(Product.shop_id == Sku.shop_id, Product.id == Sku.product_id))
        .outerjoin(User, User.id == InventoryMovement.actor_id)
        .where(Sku.shop_id == shop_id)
        .order_by(InventoryMovement.created_at.desc(), InventoryMovement.id.desc())
        .limit(limit)
    )
    if sku_code:
        query = query.where(Sku.sku_code == sku_code.upper())
    if movement_type:
        query = query.where(InventoryMovement.movement_type == movement_type)

    rows = (await session.execute(query)).all()
    return [
        InventoryMovementItem(
            id=mv.id,
            sku_id=mv.sku_id,
            sku_code=code,
            product_name=product_name,
            movement_type=cast(InventoryMovementType, mv.movement_type),
            quantity=mv.quantity,
            available_delta=mv.available_delta,
            reserved_delta=mv.reserved_delta,
            before_available=mv.before_available,
            after_available=mv.after_available,
            before_reserved=mv.before_reserved,
            after_reserved=mv.after_reserved,
            reference_id=mv.reference_id,
            note=mv.note,
            actor_id=mv.actor_id,
            actor_name=actor_name,
            created_at=mv.created_at,
        )
        for mv, code, product_name, actor_name in rows
    ]


async def apply_inventory_movement(
    session: AsyncSession,
    *,
    organization_id: int,
    shop_id: int,
    sku_code: str,
    actor_id: int,
    payload: InventoryMovementCreateRequest,
    idempotency_key: str | None = None,
) -> InventoryMovementMutationResponse:
    normalized_code = sku_code.upper()
    canonical_request = {
        "sku_code": normalized_code,
        "movement_type": payload.movement_type,
        "quantity": payload.quantity,
        "reference_id": payload.reference_id,
        "expected_version": payload.expected_version,
        "note": payload.note,
    }

    claim = None
    if idempotency_key:
        claim = await claim_idempotency(
            session,
            organization_id=organization_id,
            shop_id=shop_id,
            operation_type="inventory_movement",
            idempotency_key=idempotency_key,
            request_payload=canonical_request,
        )
        if claim.replayed_response is not None:
            replayed = InventoryMovementMutationResponse.model_validate(claim.replayed_response)
            replayed.replayed = True
            return replayed

    sku_row = (
        await session.execute(
            select(Sku, Product)
            .join(Product, and_(Product.shop_id == Sku.shop_id, Product.id == Sku.product_id))
            .where(Sku.shop_id == shop_id, Sku.sku_code == normalized_code)
        )
    ).one_or_none()
    if sku_row is None:
        raise AppError(404, "SKU_NOT_FOUND", f"当前店铺没有找到 {normalized_code}。", detail="SKU not found")
    sku, product = sku_row

    inventory = await session.scalar(
        select(Inventory).where(Inventory.sku_id == sku.id).with_for_update()
    )
    if inventory is None:
        raise AppError(
            404,
            "INVENTORY_NOT_FOUND",
            f"{normalized_code} 暂无库存快照记录，无法执行库存变更。",
            detail="Inventory snapshot not found",
        )

    if payload.expected_version is not None and inventory.version != payload.expected_version:
        raise AppError(
            409,
            "INVENTORY_VERSION_CONFLICT",
            (
                f"库存快照版本冲突：请求期望版本为 V{payload.expected_version}，"
                f"当前最新快照为 V{inventory.version}，请刷新后重试。"
            ),
            detail=(
                f"Expected inventory version {payload.expected_version}, "
                f"actual version {inventory.version}"
            ),
        )

    before_available = inventory.available_qty
    before_reserved = inventory.reserved_qty
    qty = payload.quantity

    if payload.movement_type == "reserve":
        if before_available < qty:
            raise AppError(
                409,
                "INSUFFICIENT_INVENTORY",
                f"可售库存不足：当前可售 {before_available} 件，无法预占 {qty} 件。",
                detail=f"Available inventory {before_available} < {qty}",
            )
        available_delta = -qty
        reserved_delta = qty
        action_label = "库存预占"
    elif payload.movement_type == "release":
        if before_reserved < qty:
            raise AppError(
                409,
                "INSUFFICIENT_RESERVED_INVENTORY",
                f"预占库存不足：当前已预占 {before_reserved} 件，无法释放 {qty} 件。",
                detail=f"Reserved inventory {before_reserved} < {qty}",
            )
        available_delta = qty
        reserved_delta = -qty
        action_label = "预占释放"
    elif payload.movement_type == "fulfill":
        if before_reserved < qty:
            raise AppError(
                409,
                "INSUFFICIENT_RESERVED_INVENTORY",
                f"预占库存不足：当前已预占 {before_reserved} 件，无法扣减履约 {qty} 件。",
                detail=f"Reserved inventory {before_reserved} < {qty}",
            )
        available_delta = 0
        reserved_delta = -qty
        action_label = "履约扣减"
    else:
        available_delta = qty
        reserved_delta = 0
        action_label = "补货入库"

    after_available = before_available + available_delta
    after_reserved = before_reserved + reserved_delta

    inventory.available_qty = after_available
    inventory.reserved_qty = after_reserved
    inventory.version += 1

    movement = InventoryMovement(
        shop_id=shop_id,
        sku_id=sku.id,
        movement_type=payload.movement_type,
        quantity=qty,
        available_delta=available_delta,
        reserved_delta=reserved_delta,
        before_available=before_available,
        after_available=after_available,
        before_reserved=before_reserved,
        after_reserved=after_reserved,
        reference_id=payload.reference_id,
        actor_id=actor_id,
        note=payload.note,
    )
    session.add(movement)
    await session.flush()

    await write_audit_log(
        session,
        organization_id=organization_id,
        shop_id=shop_id,
        actor_id=actor_id,
        action=f"inventory.{payload.movement_type}",
        resource_type="inventory",
        resource_id=inventory.id,
        before_json={
            "sku_code": sku.sku_code,
            "available_qty": before_available,
            "reserved_qty": before_reserved,
            "version": inventory.version - 1,
        },
        after_json={
            "sku_code": sku.sku_code,
            "available_qty": after_available,
            "reserved_qty": after_reserved,
            "version": inventory.version,
        },
        metadata_json={"movement_id": movement.id, "reference_id": payload.reference_id},
    )

    actor_name = await session.scalar(select(User.name).where(User.id == actor_id))
    effective_status = product.status if product.status != "active" else sku.status
    snapshot = _to_snapshot(
        sku=sku,
        product_name=product.name,
        category=product.category,
        effective_status=effective_status,
        inventory=inventory,
    )
    movement_item = InventoryMovementItem(
        id=movement.id,
        sku_id=sku.id,
        sku_code=sku.sku_code,
        product_name=product.name,
        movement_type=cast(InventoryMovementType, movement.movement_type),
        quantity=movement.quantity,
        available_delta=movement.available_delta,
        reserved_delta=movement.reserved_delta,
        before_available=movement.before_available,
        after_available=movement.after_available,
        before_reserved=movement.before_reserved,
        after_reserved=movement.after_reserved,
        reference_id=movement.reference_id,
        note=movement.note,
        actor_id=movement.actor_id,
        actor_name=actor_name,
        created_at=movement.created_at,
    )
    response = InventoryMovementMutationResponse(
        snapshot=snapshot,
        movement=movement_item,
        replayed=False,
        message=(
            f"{sku.sku_code} {action_label} {qty} 件成功（快照 V{inventory.version}："
            f"可售 {after_available} 件，预占 {after_reserved} 件）。"
        ),
    )
    if claim is not None:
        complete_idempotency(
            claim.record,
            resource_type="inventory",
            resource_id=inventory.id,
            response_payload=response.model_dump(mode="json"),
        )
    return response
