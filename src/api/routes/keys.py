from fastapi import APIRouter, status

from ...domain import UnauthorizedError
from ..deps import ApiKeyServiceDep, RequireAdmin
from ..pagination import PageDep
from ..schemas import (
    ApiKeyCreate,
    ApiKeyCreatedOut,
    ApiKeyOut,
    ApiKeyVerifyIn,
    ApiKeyVerifyOut,
    Page,
)

router = APIRouter(prefix="/api/v1/keys", tags=["keys"])


@router.get("", response_model=Page[ApiKeyOut], summary="密钥列表")
async def list_keys(
    service: ApiKeyServiceDep,
    page: PageDep,
    _auth: RequireAdmin,
    active_only: bool = False,
    name_like: str | None = None,
) -> Page[ApiKeyOut]:
    items, total = await service.list(
        active_only=active_only,
        name_like=name_like,
        limit=page.limit,
        offset=page.offset,
    )
    return page.build([ApiKeyOut.model_validate(item) for item in items], total)


@router.post(
    "",
    response_model=ApiKeyCreatedOut,
    status_code=status.HTTP_201_CREATED,
    summary="签发密钥",
)
async def create_key(
    payload: ApiKeyCreate,
    service: ApiKeyServiceDep,
    _auth: RequireAdmin,
) -> ApiKeyCreatedOut:
    entity, plaintext = await service.create(
        name=payload.name,
        scopes=payload.scopes,
        rate_limit_per_second=payload.rate_limit_per_second,
        ttl_days=payload.ttl_days,
        expires_at=payload.expires_at,
    )
    return ApiKeyCreatedOut(**ApiKeyOut.model_validate(entity).model_dump(), key=plaintext)


@router.post("/verify", response_model=ApiKeyVerifyOut, summary="校验密钥")
async def verify_key(
    payload: ApiKeyVerifyIn,
    service: ApiKeyServiceDep,
    _auth: RequireAdmin,
    scope: str = "infer",
) -> ApiKeyVerifyOut:
    try:
        entity = await service.verify(payload.key, required_scope=scope)
    except UnauthorizedError:
        return ApiKeyVerifyOut(valid=False, api_key=None)
    return ApiKeyVerifyOut(valid=True, api_key=ApiKeyOut.model_validate(entity))


@router.get("/{key_id}", response_model=ApiKeyOut, summary="密钥详情")
async def get_key(key_id: int, service: ApiKeyServiceDep, _auth: RequireAdmin) -> ApiKeyOut:
    return ApiKeyOut.model_validate(await service.get(key_id))


@router.post("/{key_id}/revoke", response_model=ApiKeyOut, summary="吊销密钥")
async def revoke_key(key_id: int, service: ApiKeyServiceDep, _auth: RequireAdmin) -> ApiKeyOut:
    return ApiKeyOut.model_validate(await service.revoke(key_id))
