from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..domain import ApiKey, UnauthorizedError
from ..repository import Database
from ..service import ApiKeyService, BackendService, ModelService, RequestLogService

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_database(request: Request) -> Database:
    return request.app.state.database


def get_app_settings() -> Settings:
    return get_settings()


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    database: Database = request.app.state.database
    async with database.transaction() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]
DatabaseDep = Annotated[Database, Depends(get_database)]


def get_backend_service(session: SessionDep) -> BackendService:
    return BackendService(session)


def get_model_service(session: SessionDep) -> ModelService:
    return ModelService(session)


def get_request_log_service(session: SessionDep) -> RequestLogService:
    return RequestLogService(session)


def get_api_key_service(session: SessionDep, settings: SettingsDep) -> ApiKeyService:
    return ApiKeyService(session, settings)


BackendServiceDep = Annotated[BackendService, Depends(get_backend_service)]
ModelServiceDep = Annotated[ModelService, Depends(get_model_service)]
RequestLogServiceDep = Annotated[RequestLogService, Depends(get_request_log_service)]
ApiKeyServiceDep = Annotated[ApiKeyService, Depends(get_api_key_service)]


def require_scope(scope: str):
    async def dependency(
        session: SessionDep,
        settings: SettingsDep,
        raw_key: Annotated[str | None, Security(api_key_header)] = None,
    ) -> ApiKey | None:
        if not settings.auth_required:
            return None
        if not raw_key:
            raise UnauthorizedError("缺少 X-API-Key 请求头")
        service = ApiKeyService(session, settings)
        return await service.verify(raw_key, required_scope=scope)

    return dependency


RequireInfer = Annotated[ApiKey | None, Depends(require_scope("infer"))]
RequireAdmin = Annotated[ApiKey | None, Depends(require_scope("admin"))]
