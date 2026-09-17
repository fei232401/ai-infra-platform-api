from fastapi import APIRouter, status

from ..deps import (
    BackendServiceDep,
    RequireAdmin,
    RequireInfer,
)
from ..pagination import PageDep
from ..schemas import (
    BackendBulkCreate,
    BackendCreate,
    BackendOut,
    BackendStateChange,
    BackendUpdate,
    HealthCheckCreate,
    HealthCheckOut,
    ModelIdsPayload,
    ModelOut,
    Page,
)

router = APIRouter(prefix="/api/v1/backends", tags=["backends"])


@router.get("", response_model=Page[BackendOut], summary="后端实例列表")
async def list_backends(
    service: BackendServiceDep,
    page: PageDep,
    _auth: RequireInfer,
    state: str | None = None,
    engine: str | None = None,
    name_like: str | None = None,
    with_models: bool = False,
) -> Page[BackendOut]:
    items, total = await service.list(
        state=state,
        engine=engine,
        name_like=name_like,
        limit=page.limit,
        offset=page.offset,
    )
    outputs = [BackendOut.model_validate(item) for item in items]
    if with_models and outputs:
        mapping = await service.models_by_backend([item.id for item in items])
        for output in outputs:
            output.models = [
                ModelOut.model_validate(row) for row in mapping.get(output.id, [])
            ]
    return page.build(outputs, total)


@router.post(
    "",
    response_model=BackendOut,
    status_code=status.HTTP_201_CREATED,
    summary="注册后端实例",
)
async def register_backend(
    payload: BackendCreate,
    service: BackendServiceDep,
    _auth: RequireAdmin,
) -> BackendOut:
    entity = await service.register(
        name=payload.name,
        url=payload.url,
        engine=payload.engine,
        weight=payload.weight,
        max_concurrency=payload.max_concurrency,
        cost_per_token=payload.cost_per_token,
        model_ids=payload.model_ids,
    )
    return BackendOut.model_validate(entity)


@router.post("/bulk", summary="批量幂等注册后端实例")
async def bulk_register_backends(
    payload: BackendBulkCreate,
    service: BackendServiceDep,
    _auth: RequireAdmin,
) -> dict[str, int]:
    items = [item.model_dump() for item in payload.items]
    return await service.bulk_register(items)


@router.get("/{backend_id}", response_model=BackendOut, summary="后端实例详情")
async def get_backend(
    backend_id: int,
    service: BackendServiceDep,
    _auth: RequireInfer,
    with_models: bool = False,
) -> BackendOut:
    entity = await service.get(backend_id)
    output = BackendOut.model_validate(entity)
    if with_models:
        models = await service.list_models(backend_id)
        output.models = [ModelOut.model_validate(row) for row in models]
    return output


@router.patch("/{backend_id}", response_model=BackendOut, summary="更新后端实例")
async def update_backend(
    backend_id: int,
    payload: BackendUpdate,
    service: BackendServiceDep,
    _auth: RequireAdmin,
) -> BackendOut:
    changes = payload.model_dump(exclude_unset=True)
    entity = await service.update(backend_id, changes)
    return BackendOut.model_validate(entity)


@router.post("/{backend_id}/state", response_model=BackendOut, summary="变更实例状态")
async def change_backend_state(
    backend_id: int,
    payload: BackendStateChange,
    service: BackendServiceDep,
    _auth: RequireAdmin,
) -> BackendOut:
    entity = await service.change_state(backend_id, payload.state)
    return BackendOut.model_validate(entity)


@router.delete("/{backend_id}", response_model=BackendOut, summary="下线后端实例")
async def disable_backend(
    backend_id: int,
    service: BackendServiceDep,
    _auth: RequireAdmin,
) -> BackendOut:
    entity = await service.change_state(backend_id, "disabled")
    return BackendOut.model_validate(entity)


@router.get("/{backend_id}/models", response_model=list[ModelOut], summary="实例已挂载模型")
async def list_backend_models(
    backend_id: int,
    service: BackendServiceDep,
    _auth: RequireInfer,
) -> list[ModelOut]:
    entities = await service.list_models(backend_id)
    return [ModelOut.model_validate(entity) for entity in entities]


@router.post("/{backend_id}/models", summary="挂载模型")
async def bind_backend_models(
    backend_id: int,
    payload: ModelIdsPayload,
    service: BackendServiceDep,
    _auth: RequireAdmin,
) -> dict[str, object]:
    return await service.bind_models(backend_id, payload.model_ids)


@router.put("/{backend_id}/models", summary="全量替换挂载模型")
async def replace_backend_models(
    backend_id: int,
    payload: ModelIdsPayload,
    service: BackendServiceDep,
    _auth: RequireAdmin,
) -> dict[str, object]:
    return await service.replace_models(backend_id, payload.model_ids)


@router.delete("/{backend_id}/models", summary="卸载模型")
async def unbind_backend_models(
    backend_id: int,
    payload: ModelIdsPayload,
    service: BackendServiceDep,
    _auth: RequireAdmin,
) -> dict[str, object]:
    return await service.unbind_models(backend_id, payload.model_ids)


@router.get(
    "/{backend_id}/health",
    response_model=list[HealthCheckOut],
    summary="实例健康检查历史",
)
async def backend_health_history(
    backend_id: int,
    service: BackendServiceDep,
    _auth: RequireInfer,
    limit: int = 20,
) -> list[HealthCheckOut]:
    entities = await service.health_history(backend_id, limit=limit)
    return [HealthCheckOut.model_validate(entity) for entity in entities]


@router.post(
    "/{backend_id}/health",
    response_model=HealthCheckOut,
    status_code=status.HTTP_201_CREATED,
    summary="上报健康检查结果",
)
async def record_backend_health(
    backend_id: int,
    payload: HealthCheckCreate,
    service: BackendServiceDep,
    _auth: RequireAdmin,
) -> HealthCheckOut:
    entity = await service.record_health(
        backend_id,
        healthy=payload.healthy,
        latency_ms=payload.latency_ms,
        error=payload.error,
        checked_at=payload.checked_at,
    )
    return HealthCheckOut.model_validate(entity)
