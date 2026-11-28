from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.auth import AuthorizedShop, Clock, Database
from app.schemas.analytics import (
    AnalyticsOverviewResponse,
    DailyReportResponse,
    InventoryTurnoverResponse,
)
from app.services import analytics as analytics_service

router = APIRouter(prefix="/api/shops/{shop_id}/analytics", tags=["analytics"])


@router.get("/overview")
async def analytics_overview(
    shop: AuthorizedShop,
    session: Database,
    clock: Clock,
    days: Annotated[int, Query(ge=1, le=30)] = 7,
    as_of: Annotated[date | None, Query()] = None,
) -> AnalyticsOverviewResponse:
    target_date = as_of or clock.business_date(shop.timezone)
    return await analytics_service.get_analytics_overview(
        session, shop_id=shop.id, as_of=target_date, days=days
    )


@router.get("/daily-report")
async def analytics_daily_report(
    shop: AuthorizedShop,
    session: Database,
    clock: Clock,
    report_date: Annotated[date | None, Query()] = None,
) -> DailyReportResponse:
    default_date = clock.business_date(shop.timezone) - timedelta(days=1)
    return await analytics_service.get_daily_report(
        session, shop_id=shop.id, report_date=report_date or default_date
    )


@router.get("/inventory-turnover")
async def analytics_inventory_turnover(
    shop: AuthorizedShop,
    session: Database,
    clock: Clock,
    days: Annotated[int, Query(ge=1, le=30)] = 7,
    as_of: Annotated[date | None, Query()] = None,
) -> InventoryTurnoverResponse:
    target_date = as_of or clock.business_date(shop.timezone)
    return await analytics_service.get_inventory_turnover(
        session, shop_id=shop.id, as_of=target_date, days=days
    )
