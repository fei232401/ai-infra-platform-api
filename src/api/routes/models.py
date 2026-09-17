from fastapi import APIRouter, status

from ..deps import ModelServiceDep, RequireAdmin, RequireInfer
from ..pagination import PageDep
from ..schemas import ModelCreate, ModelOut, Page

router = APIRouter(prefix="/api/v1/models", tags=["models"])


@router.get("/names", response_model=list[str], summary="模型名称列表")
async def list_model_names(service: ModelServiceDep, _auth: RequireInfer) -> list[str]:
    return await service.list_names()


@router.get("/families", response_model=list[str], summary="模型族列表")
async def list_model_families(service: ModelServiceDep, _auth: RequireInfer) -> list[str]:
    return await service.families()


@router.get("", response_model=Page[ModelOut], summary="模型列表")
async def list_models(
    service: ModelServiceDep,
    page: PageDep,
    _auth: RequireInfer,
    family: str | None = None,
    name_like: str | None = None,
) -> Page[ModelOut]:
    items, total = await service.list(
        family=family,
        name_like=name_like,
        limit=page.limit,
        offset=page.offset,
    )
    return page.build([ModelOut.model_validate(item) for item in items], total)


@router.post("", response_model=ModelOut, status_code=status.HTTP_201_CREATED, summary="注册模型")
async def create_model(
    payload: ModelCreate,
    service: ModelServiceDep,
    _auth: RequireAdmin,
) -> ModelOut:
    entity = await service.create(
        name=payload.name,
        family=payload.family,
        parameter_billions=payload.parameter_billions,
        quantization=payload.quantization,
    )
    return ModelOut.model_validate(entity)


@router.post("/upsert", response_model=ModelOut, summary="幂等注册模型")
async def upsert_model(
    payload: ModelCreate,
    service: ModelServiceDep,
    _auth: RequireAdmin,
) -> ModelOut:
    entity = await service.upsert(
        name=payload.name,
        family=payload.family,
        parameter_billions=payload.parameter_billions,
        quantization=payload.quantization,
    )
    return ModelOut.model_validate(entity)


@router.get("/{model_id}", response_model=ModelOut, summary="模型详情")
async def get_model(model_id: int, service: ModelServiceDep, _auth: RequireInfer) -> ModelOut:
    return ModelOut.model_validate(await service.get(model_id))
