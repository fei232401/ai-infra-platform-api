from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import ConflictError, ModelInfo, NotFoundError, ValidationError
from ..repository import ModelRepository


class ModelService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._models = ModelRepository(session)

    async def list(
        self,
        *,
        family: str | None = None,
        name_like: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ModelInfo], int]:
        rows = await self._models.list(
            family=family,
            name_like=name_like,
            limit=limit,
            offset=offset,
        )
        total = await self._models.count(family=family, name_like=name_like)
        return [ModelInfo.model_validate(row) for row in rows], total

    async def get(self, model_id: int) -> ModelInfo:
        row = await self._models.get(model_id)
        if row is None:
            raise NotFoundError("模型不存在", detail={"model_id": model_id})
        return ModelInfo.model_validate(row)

    async def find_by_name(self, name: str) -> ModelInfo | None:
        row = await self._models.find_by_name(name)
        return ModelInfo.model_validate(row) if row is not None else None

    async def list_names(self) -> list[str]:
        return await self._models.list_names()

    async def families(self) -> list[str]:
        return await self._models.families()

    async def create(
        self,
        *,
        name: str,
        family: str | None = None,
        parameter_billions: Any = None,
        quantization: str | None = None,
    ) -> ModelInfo:
        if await self._models.find_by_name(name) is not None:
            raise ConflictError("模型名称已存在", detail={"name": name})
        row = await self._models.create(
            {
                "name": name,
                "family": family,
                "parameter_billions": self._parse_parameters(parameter_billions),
                "quantization": quantization,
            }
        )
        return ModelInfo.model_validate(row)

    async def upsert(
        self,
        *,
        name: str,
        family: str | None = None,
        parameter_billions: Any = None,
        quantization: str | None = None,
    ) -> ModelInfo:
        row = await self._models.upsert(
            {
                "name": name,
                "family": family,
                "parameter_billions": self._parse_parameters(parameter_billions),
                "quantization": quantization,
            }
        )
        return ModelInfo.model_validate(row)

    def _parse_parameters(self, value: Any) -> Decimal | None:
        if value is None:
            return None
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError) as exc:
            raise ValidationError(
                "parameter_billions 不是合法数值",
                detail={"parameter_billions": str(value)},
            ) from exc
        if parsed <= 0:
            raise ValidationError(
                "parameter_billions 必须大于 0",
                detail={"parameter_billions": str(parsed)},
            )
        return parsed
