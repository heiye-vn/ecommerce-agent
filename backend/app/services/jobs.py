import inspect
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, cast
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import DEFAULT_SHOP_TIMEZONE, BusinessClock
from app.core.errors import AppError
from app.models import Job, OutboxEvent, Promotion, Role, Shop
from app.schemas.jobs import JobDetail, JobStatus, JobSummary, JobTab, JobType
from app.services.audit import list_resource_audit_logs, write_audit_log
from app.services.notifications import create_notification, notify_shop_roles
from app.services.promotions import (
    activate_scheduled_promotion,
    complete_active_promotion,
    schedule_approved_promotion,
)

JOB_TYPE_TO_EVENT_TYPE: dict[str, str] = {
    "promotion_schedule": "promotion.schedule.requested",
    "promotion_activate": "promotion.activate.requested",
    "promotion_complete": "promotion.complete.requested",
}


class TransientJobError(RuntimeError):
    """表示依赖暂时不可用或网络抖动等可安全退避重试的暂态错误。"""


FaultInjector = Callable[[Job], Any]


async def next_job_no(session: AsyncSession, shop_id: int, business_day: date) -> str:
    await session.execute(text("SELECT pg_advisory_xact_lock(726300, :shop_id)"), {"shop_id": shop_id})
    prefix = f"JOB-{business_day:%Y%m%d}-"
    existing_count = await session.scalar(
        select(func.count(Job.id)).where(
            Job.shop_id == shop_id,
            Job.job_no.like(f"{prefix}%"),
        )
    )
    return f"{prefix}{(existing_count or 0) + 1:02d}"


def _build_outbox_event(
    *,
    organization_id: int,
    shop_id: int,
    job: Job,
    available_at: datetime | None = None,
    attempt: int | None = None,
) -> OutboxEvent:
    event_type = JOB_TYPE_TO_EVENT_TYPE[job.job_type]
    payload: dict[str, Any] = {
        "job_id": job.id,
        "job_no": job.job_no,
        "job_type": job.job_type,
        "resource_type": job.resource_type,
        "resource_id": job.resource_id,
        "resource_version": job.resource_version,
    }
    if attempt is not None:
        payload["attempt"] = attempt
    return OutboxEvent(
        event_id=f"evt-{uuid4().hex}",
        organization_id=organization_id,
        shop_id=shop_id,
        event_type=event_type,
        aggregate_type=job.resource_type,
        aggregate_id=job.resource_id,
        payload=payload,
        status="pending",
        available_at=available_at or datetime.now(UTC),
    )


async def enqueue_job_with_outbox(
    session: AsyncSession,
    *,
    organization_id: int,
    shop_id: int,
    job_type: JobType,
    resource_type: str,
    resource_id: int,
    resource_version: int,
    idempotency_key: str,
    payload: dict[str, Any],
    business_day: date,
    max_attempts: int = 3,
    emit_outbox: bool = True,
) -> tuple[Job, bool]:
    existing = await session.scalar(
        select(Job).where(
            Job.organization_id == organization_id,
            Job.job_type == job_type,
            Job.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        return existing, False

    job_no = await next_job_no(session, shop_id, business_day)
    inserted_id = await session.scalar(
        insert(Job)
        .values(
            organization_id=organization_id,
            shop_id=shop_id,
            job_no=job_no,
            job_type=job_type,
            status="pending",
            resource_type=resource_type,
            resource_id=resource_id,
            resource_version=resource_version,
            idempotency_key=idempotency_key,
            payload=payload,
            max_attempts=max_attempts,
        )
        .on_conflict_do_nothing(constraint="uq_jobs_idempotency")
        .returning(Job.id)
    )
    if inserted_id is None:
        concurrent_existing = (
            await session.scalars(
                select(Job).where(
                    Job.organization_id == organization_id,
                    Job.job_type == job_type,
                    Job.idempotency_key == idempotency_key,
                )
            )
        ).one()
        return concurrent_existing, False

    job = (await session.scalars(select(Job).where(Job.id == inserted_id))).one()
    if emit_outbox:
        session.add(_build_outbox_event(organization_id=organization_id, shop_id=shop_id, job=job))
    await session.flush()
    return job, True


async def _run_fault_injector(fault_injector: FaultInjector | None, job: Job) -> None:
    if fault_injector is None:
        return
    outcome = fault_injector(job)
    if inspect.isawaitable(outcome):
        await outcome


async def run_loaded_job(
    session: AsyncSession,
    *,
    job: Job,
    business_now: datetime,
    fault_injector: FaultInjector | None = None,
) -> tuple[str, str]:
    """执行已锁定的 Job（核心消费幂等实现：终态作业直接跳过，绝不重复执行）。"""
    if job.status in ("succeeded", "failed"):
        return job.status, "重复投递已幂等忽略，未重复执行业务状态变更。"

    job.status = "running"
    job.started_at = datetime.now(UTC)
    await session.flush()

    await _run_fault_injector(fault_injector, job)

    if job.resource_type != "promotion":
        raise AppError(
            400,
            "UNSUPPORTED_JOB_RESOURCE",
            f"暂不支持作业资源类型 {job.resource_type}。",
            detail="Unsupported job resource type",
        )

    promotion = await session.scalar(
        select(Promotion).where(Promotion.shop_id == job.shop_id, Promotion.id == job.resource_id)
    )
    if promotion is None:
        raise AppError(404, "PROMOTION_NOT_FOUND", "关联促销活动不存在。", detail="Promotion not found")

    actor_id = int(job.payload.get("actor_id") or promotion.created_by)
    if job.job_type == "promotion_schedule":
        schedule_status, msg = await schedule_approved_promotion(
            session,
            organization_id=job.organization_id,
            shop_id=job.shop_id,
            promotion_id=job.resource_id,
            expected_version=job.resource_version,
            actor_id=actor_id,
            business_now=business_now,
        )
        if schedule_status == "scheduled":
            job.status = "succeeded"
            job.last_error = None
            await create_notification(
                session,
                organization_id=job.organization_id,
                shop_id=job.shop_id,
                user_id=promotion.created_by,
                category="promotion",
                title=f"促销活动 {promotion.promotion_no} 已成功排期",
                content=msg,
                resource_type="promotion",
                resource_id=promotion.id,
            )
        else:
            job.status = "failed"
            job.last_error = msg
            await create_notification(
                session,
                organization_id=job.organization_id,
                shop_id=job.shop_id,
                user_id=promotion.created_by,
                category="promotion",
                title=f"促销活动 {promotion.promotion_no} 复核失败",
                content=f"{msg} 请前往促销详情修订方案并重新送审。",
                resource_type="promotion",
                resource_id=promotion.id,
            )
        job.result_json = {"execution_status": schedule_status, "message": msg}
        job.finished_at = datetime.now(UTC)
        return schedule_status, msg

    if job.job_type == "promotion_activate":
        activate_status, msg = await activate_scheduled_promotion(
            session,
            organization_id=job.organization_id,
            shop_id=job.shop_id,
            promotion_id=job.resource_id,
            actor_id=actor_id,
            business_now=business_now,
        )
        job.status = "succeeded"
        job.last_error = None
        job.result_json = {"execution_status": activate_status, "message": msg}
        job.finished_at = datetime.now(UTC)
        return activate_status, msg

    if job.job_type == "promotion_complete":
        complete_status, msg = await complete_active_promotion(
            session,
            organization_id=job.organization_id,
            shop_id=job.shop_id,
            promotion_id=job.resource_id,
            actor_id=actor_id,
            business_now=business_now,
        )
        job.status = "succeeded"
        job.last_error = None
        job.result_json = {"execution_status": complete_status, "message": msg}
        job.finished_at = datetime.now(UTC)
        return complete_status, msg

    raise AppError(400, "UNSUPPORTED_JOB_TYPE", f"不支持的任务类型 {job.job_type}。")


async def execute_job_in_transaction(
    session: AsyncSession,
    *,
    job_id: int,
    business_now: datetime | None = None,
    clock: BusinessClock | None = None,
    fault_injector: FaultInjector | None = None,
) -> tuple[str, str]:
    job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job is None:
        raise AppError(404, "JOB_NOT_FOUND", "未找到后台任务。", detail=f"Job {job_id} not found")

    if business_now is not None:
        resolved_now = business_now
    else:
        shop_tz = (
            await session.scalar(select(Shop.timezone).where(Shop.id == job.shop_id))
        ) or DEFAULT_SHOP_TIMEZONE
        resolved_now = (clock or BusinessClock()).now(shop_tz)

    return await run_loaded_job(
        session,
        job=job,
        business_now=resolved_now,
        fault_injector=fault_injector,
    )


async def record_job_transient_failure(
    session: AsyncSession,
    *,
    job_id: int,
    error_message: str,
    backoff_base_seconds: int = 5,
) -> Literal["retrying", "dead_lettered"]:
    job = (await session.scalars(select(Job).where(Job.id == job_id).with_for_update())).one()
    if job.status in ("succeeded", "failed"):
        return "dead_lettered" if job.status == "failed" else "retrying"
    if job.status == "dead_lettered":
        return "dead_lettered"

    now = datetime.now(UTC)
    previous_attempts = job.attempts
    job.attempts = previous_attempts + 1
    job.last_error = error_message

    if job.attempts < job.max_attempts:
        delay_seconds = backoff_base_seconds * (2 ** (job.attempts - 1))
        next_at = now + timedelta(seconds=delay_seconds)
        job.status = "retrying"
        job.available_at = next_at
        session.add(
            _build_outbox_event(
                organization_id=job.organization_id,
                shop_id=job.shop_id,
                job=job,
                available_at=next_at,
                attempt=job.attempts + 1,
            )
        )
        await session.flush()
        return "retrying"

    job.status = "dead_lettered"
    job.finished_at = now
    actor_id = int(job.payload.get("actor_id") or job.payload.get("requester_id") or 1)
    await write_audit_log(
        session,
        organization_id=job.organization_id,
        shop_id=job.shop_id,
        actor_id=actor_id,
        action="job.dead_lettered",
        resource_type=job.resource_type,
        resource_id=job.resource_id,
        before_json={"job_no": job.job_no, "attempts": previous_attempts},
        after_json={
            "job_no": job.job_no,
            "status": "dead_lettered",
            "attempts": job.attempts,
            "last_error": error_message,
        },
        metadata_json={"job_id": job.id, "job_no": job.job_no},
    )
    await notify_shop_roles(
        session,
        organization_id=job.organization_id,
        shop_id=job.shop_id,
        roles=(Role.OPERATOR, Role.ADMIN),
        category="job",
        title=f"任务 {job.job_no} 已转入死信队列",
        content=(
            f"连续自动重试 {job.attempts}/{job.max_attempts} 次均失败（{error_message}），"
            "请确认系统依赖恢复后在任务中心人工重试。"
        ),
        resource_type="job",
        resource_id=job.id,
    )
    await session.flush()
    return "dead_lettered"


async def retry_dead_letter_job(
    session: AsyncSession,
    *,
    organization_id: int,
    shop_id: int,
    job_id: int,
    actor_id: int,
    business_now: datetime,
) -> JobDetail:
    job = await session.scalar(
        select(Job).where(Job.shop_id == shop_id, Job.id == job_id).with_for_update()
    )
    if job is None:
        raise AppError(404, "JOB_NOT_FOUND", "当前店铺没有找到该任务。", detail="Job not found")
    if job.status == "failed":
        raise AppError(
            409,
            "JOB_BUSINESS_FAILURE_NOT_RETRYABLE",
            "业务复核失败不可直接重试，请在促销详情修订方案并重新送审。",
            detail="Business validation failure cannot be retried directly",
        )
    if job.status != "dead_lettered":
        raise AppError(
            409,
            "INVALID_JOB_STATE",
            "仅待人工处理（死信）状态的任务支持按原操作重试。",
            detail=f"Cannot retry job in status {job.status}",
        )

    now = datetime.now(UTC)
    before_state = {"status": job.status, "attempts": job.attempts, "last_error": job.last_error}
    job.status = "pending"
    job.attempts = 0
    job.last_error = None
    job.started_at = None
    job.finished_at = None
    job.available_at = now
    job.result_json = None
    session.add(_build_outbox_event(organization_id=organization_id, shop_id=shop_id, job=job))

    await write_audit_log(
        session,
        organization_id=organization_id,
        shop_id=shop_id,
        actor_id=actor_id,
        action="job.manual_retry",
        resource_type=job.resource_type,
        resource_id=job.resource_id,
        before_json=before_state,
        after_json={
            "job_no": job.job_no,
            "status": "pending",
            "attempts": 0,
            "retried_at": business_now.isoformat(),
        },
        metadata_json={"job_id": job.id, "job_no": job.job_no},
    )
    await session.flush()
    detail = await get_job_detail(session, shop_id=shop_id, job_id=job.id)
    assert detail is not None
    return detail


def _job_retry_meta(status: str) -> tuple[bool, str | None]:
    if status == "dead_lettered":
        return True, "系统暂时性故障已超出自动重试上限，确认依赖恢复后可按原操作重试，不会重复建单。"
    if status == "failed":
        return False, "SKU 下架、价格变化或时段冲突属于业务复核失败，不可自动或直接重试，请修订方案并重新审批。"
    return False, None


async def list_jobs(
    session: AsyncSession,
    *,
    shop_id: int,
    tab: JobTab = "all",
) -> list[JobSummary]:
    query = (
        select(Job, Promotion.promotion_no, Promotion.name)
        .outerjoin(
            Promotion,
            (Promotion.shop_id == Job.shop_id) & (Promotion.id == Job.resource_id),
        )
        .where(Job.shop_id == shop_id)
        .order_by(Job.updated_at.desc(), Job.id.desc())
    )
    if tab == "needs_attention":
        query = query.where(Job.status.in_(("failed", "dead_lettered", "retrying")))
    elif tab == "completed":
        query = query.where(Job.status == "succeeded")

    rows = (await session.execute(query)).all()
    summaries: list[JobSummary] = []
    for job, promo_no, promo_name in rows:
        payload = job.payload or {}
        summaries.append(
            JobSummary(
                id=job.id,
                job_no=job.job_no,
                job_type=cast(JobType, job.job_type),
                status=cast(JobStatus, job.status),
                resource_type=job.resource_type,
                resource_id=job.resource_id,
                resource_no=promo_no or str(payload.get("promotion_no") or job.resource_id),
                resource_name=promo_name or str(payload.get("promotion_name") or ""),
                resource_version=job.resource_version,
                attempts=job.attempts,
                max_attempts=job.max_attempts,
                available_at=job.available_at,
                last_error=job.last_error,
                started_at=job.started_at,
                finished_at=job.finished_at,
                created_at=job.created_at,
                updated_at=job.updated_at,
            )
        )
    return summaries


async def get_job_detail(
    session: AsyncSession,
    *,
    shop_id: int,
    job_id: int,
) -> JobDetail | None:
    row = (
        await session.execute(
            select(Job, Promotion.promotion_no, Promotion.name, Promotion.status)
            .outerjoin(
                Promotion,
                (Promotion.shop_id == Job.shop_id) & (Promotion.id == Job.resource_id),
            )
            .where(Job.shop_id == shop_id, Job.id == job_id)
        )
    ).one_or_none()
    if row is None:
        return None
    job, promo_no, promo_name, promo_status = row
    payload = job.payload or {}
    audit_logs = await list_resource_audit_logs(
        session,
        shop_id=shop_id,
        resource_type=job.resource_type,
        resource_id=job.resource_id,
    )
    can_retry, retry_hint = _job_retry_meta(job.status)
    return JobDetail(
        id=job.id,
        job_no=job.job_no,
        job_type=cast(JobType, job.job_type),
        status=cast(JobStatus, job.status),
        resource_type=job.resource_type,
        resource_id=job.resource_id,
        resource_no=promo_no or str(payload.get("promotion_no") or job.resource_id),
        resource_name=promo_name or str(payload.get("promotion_name") or ""),
        resource_version=job.resource_version,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        available_at=job.available_at,
        last_error=job.last_error,
        started_at=job.started_at,
        finished_at=job.finished_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
        idempotency_key=job.idempotency_key,
        payload=payload,
        result_json=job.result_json,
        resource_status=promo_status,
        can_retry=can_retry,
        retry_hint=retry_hint,
        audit_logs=audit_logs,
    )
