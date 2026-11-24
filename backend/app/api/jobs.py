from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.api.auth import AuthorizedShop, Clock, Database, OperatorShop, reject_cross_origin
from app.core.errors import AppError
from app.schemas.jobs import JobDetail, JobRetryResponse, JobSummary, JobTab
from app.services import jobs as job_service

router = APIRouter(prefix="/api/shops/{shop_id}/jobs", tags=["jobs"])


@router.get("")
async def get_jobs(
    shop: AuthorizedShop,
    session: Database,
    tab: Annotated[JobTab, Query()] = "all",
) -> list[JobSummary]:
    return await job_service.list_jobs(session, shop_id=shop.id, tab=tab)


@router.get("/{job_id}")
async def get_job(
    shop: AuthorizedShop,
    session: Database,
    job_id: Annotated[int, Path(gt=0, le=2_147_483_647)],
) -> JobDetail:
    detail = await job_service.get_job_detail(session, shop_id=shop.id, job_id=job_id)
    if detail is None:
        raise AppError(404, "JOB_NOT_FOUND", "当前店铺没有找到该任务。", detail="Job not found")
    return detail


@router.post("/{job_id}/retry", dependencies=[Depends(reject_cross_origin)])
async def retry_job(
    shop: OperatorShop,
    session: Database,
    clock: Clock,
    job_id: Annotated[int, Path(gt=0, le=2_147_483_647)],
) -> JobRetryResponse:
    detail = await job_service.retry_dead_letter_job(
        session,
        organization_id=shop.organization_id,
        shop_id=shop.id,
        job_id=job_id,
        actor_id=shop.user_id,
        business_now=clock.now(shop.timezone),
    )
    return JobRetryResponse(
        job=detail,
        message=f"已按原操作重新投递任务 {detail.job_no}，不会重复创建活动或审批单。",
    )
