import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import BusinessClock
from app.core.config import AppSettings
from app.core.db import create_database_engine, create_session_factory
from app.models import Promotion, Shop
from app.services.jobs import enqueue_job_with_outbox

logger = logging.getLogger(__name__)


async def scan_due_promotions(
    session_factory: async_sessionmaker[AsyncSession],
    clock: BusinessClock,
) -> int:
    """
    扫描到期促销活动并幂等写入 jobs + outbox_events。
    不依赖 RabbitMQ 延迟队列替代长期业务事实，停机恢复后可补发错过窗口的结束事件。
    """
    created_jobs = 0
    async with session_factory.begin() as session:
        rows = (
            await session.execute(
                select(Promotion, Shop.organization_id, Shop.timezone)
                .join(Shop, Shop.id == Promotion.shop_id)
                .where(Promotion.status.in_(("scheduled", "active")))
                .order_by(Promotion.id.asc())
            )
        ).all()

        for promotion, organization_id, timezone_name in rows:
            business_now = clock.now(timezone_name)
            business_day = business_now.date()

            if promotion.status == "scheduled" and promotion.starts_at <= business_now:
                if promotion.ends_at <= business_now:
                    _, created = await enqueue_job_with_outbox(
                        session,
                        organization_id=organization_id,
                        shop_id=promotion.shop_id,
                        job_type="promotion_complete",
                        resource_type="promotion",
                        resource_id=promotion.id,
                        resource_version=promotion.version,
                        idempotency_key=f"promotion-complete-{promotion.id}-v{promotion.version}",
                        payload={
                            "promotion_id": promotion.id,
                            "promotion_no": promotion.promotion_no,
                            "promotion_name": promotion.name,
                            "actor_id": promotion.created_by,
                            "missed_window": True,
                        },
                        business_day=business_day,
                    )
                else:
                    _, created = await enqueue_job_with_outbox(
                        session,
                        organization_id=organization_id,
                        shop_id=promotion.shop_id,
                        job_type="promotion_activate",
                        resource_type="promotion",
                        resource_id=promotion.id,
                        resource_version=promotion.version,
                        idempotency_key=f"promotion-activate-{promotion.id}-v{promotion.version}",
                        payload={
                            "promotion_id": promotion.id,
                            "promotion_no": promotion.promotion_no,
                            "promotion_name": promotion.name,
                            "actor_id": promotion.created_by,
                        },
                        business_day=business_day,
                    )
                if created:
                    created_jobs += 1
            elif promotion.status == "active" and promotion.ends_at <= business_now:
                _, created = await enqueue_job_with_outbox(
                    session,
                    organization_id=organization_id,
                    shop_id=promotion.shop_id,
                    job_type="promotion_complete",
                    resource_type="promotion",
                    resource_id=promotion.id,
                    resource_version=promotion.version,
                    idempotency_key=f"promotion-complete-{promotion.id}-v{promotion.version}",
                    payload={
                        "promotion_id": promotion.id,
                        "promotion_no": promotion.promotion_no,
                        "promotion_name": promotion.name,
                        "actor_id": promotion.created_by,
                    },
                    business_day=business_day,
                )
                if created:
                    created_jobs += 1
    return created_jobs


async def run_scheduler(poll_interval_seconds: float = 5.0) -> None:
    settings = AppSettings.load()
    clock = BusinessClock(settings.sandbox_business_date)
    engine = create_database_engine(settings.require_database_url())
    session_factory = create_session_factory(engine)
    try:
        logger.info("Promotion lifecycle scheduler started")
        while True:
            enqueued = await scan_due_promotions(session_factory, clock)
            if enqueued > 0:
                logger.info("Scheduler enqueued %d due promotion jobs", enqueued)
            await asyncio.sleep(poll_interval_seconds)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(run_scheduler())
    except KeyboardInterrupt:
        pass
