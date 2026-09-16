from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, status

from ..deps import RequestLogServiceDep, RequireAdmin, RequireInfer
from ..pagination import PageDep
from ..schemas import (
    Page,
    PurgeIn,
    PurgeOut,
    RequestLogDetailOut,
    RequestLogIn,
    RequestLogOut,
    RoutingDecisionOut,
    SummaryOut,
)

router = APIRouter(prefix="/api/v1/requests", tags=["requests"])


@router.get("/summary", response_model=SummaryOut, summary="请求聚合统计")
async def request_summary(
    service: RequestLogServiceDep,
    _auth: RequireInfer,
    started_after: datetime | None = None,
    started_before: datetime | None = None,
    backend_id: int | None = None,
) -> SummaryOut:
    data = await service.summary(
        started_after=started_after,
        started_before=started_before,
        backend_id=backend_id,
    )
    return SummaryOut(**data)


@router.post("/purge", response_model=PurgeOut, summary="按时间窗口清理历史请求")
async def purge_requests(
    payload: PurgeIn,
    service: RequestLogServiceDep,
    _auth: RequireAdmin,
) -> PurgeOut:
    deleted = await service.purge_finished_before(payload.cutoff, batch_size=payload.batch_size)
    return PurgeOut(deleted=deleted, cutoff=payload.cutoff, batch_size=payload.batch_size)


@router.get("", response_model=Page[RequestLogOut], summary="请求记录列表")
async def list_requests(
    service: RequestLogServiceDep,
    page: PageDep,
    _auth: RequireInfer,
    backend_id: int | None = None,
    api_key_id: int | None = None,
    status_value: str | None = None,
    model_name: str | None = None,
    session_id: str | None = None,
    started_after: datetime | None = None,
    started_before: datetime | None = None,
    failures_only: bool = False,
) -> Page[RequestLogOut]:
    items, total = await service.list(
        backend_id=backend_id,
        api_key_id=api_key_id,
        status=status_value,
        model_name=model_name,
        session_id=session_id,
        started_after=started_after,
        started_before=started_before,
        failures_only=failures_only,
        limit=page.limit,
        offset=page.offset,
    )
    return page.build([RequestLogOut.model_validate(item) for item in items], total)


@router.post(
    "",
    response_model=RequestLogDetailOut,
    status_code=status.HTTP_201_CREATED,
    summary="写入请求记录",
)
async def record_request(
    payload: RequestLogIn,
    service: RequestLogServiceDep,
    _auth: RequireInfer,
) -> RequestLogDetailOut:
    entity, decision = await service.record(
        model_name=payload.model_name,
        status=payload.status,
        started_at=payload.started_at,
        request_id=payload.request_id,
        api_key_id=payload.api_key_id,
        session_id=payload.session_id,
        backend_id=payload.backend_id,
        prompt_tokens=payload.prompt_tokens,
        completion_tokens=payload.completion_tokens,
        ttft_ms=payload.ttft_ms,
        total_ms=payload.total_ms,
        error_code=payload.error_code,
        finished_at=payload.finished_at,
        decision=payload.decision.model_dump() if payload.decision is not None else None,
    )
    output = RequestLogDetailOut.model_validate(entity)
    if decision is not None:
        output.decision = RoutingDecisionOut.model_validate(decision)
    return output


@router.get("/{request_id}", response_model=RequestLogDetailOut, summary="请求记录详情")
async def get_request(
    request_id: UUID,
    service: RequestLogServiceDep,
    _auth: RequireInfer,
) -> RequestLogDetailOut:
    entity, decision = await service.get(request_id)
    output = RequestLogDetailOut.model_validate(entity)
    if decision is not None:
        output.decision = RoutingDecisionOut.model_validate(decision)
    return output
