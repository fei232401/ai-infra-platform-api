from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .tables import ModelRow


class ModelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _filters(family: str | None = None, name_like: str | None = None) -> list[Any]:
        clauses: list[Any] = []
        if family is not None:
            clauses.append(ModelRow.family == family)
        if name_like is not None:
            clauses.append(ModelRow.name.ilike(f"%{name_like}%"))
        return clauses

    async def list(
        self,
        *,
        family: str | None = None,
        name_like: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ModelRow]:
        stmt = select(ModelRow)
        clauses = self._filters(family=family, name_like=name_like)
        if clauses:
            stmt = stmt.where(*clauses)
        stmt = stmt.order_by(ModelRow.id).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count(self, *, family: str | None = None, name_like: str | None = None) -> int:
        stmt = select(func.count()).select_from(ModelRow)
        clauses = self._filters(family=family, name_like=name_like)
        if clauses:
            stmt = stmt.where(*clauses)
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def get(self, model_id: int) -> ModelRow | None:
        result = await self._session.execute(select(ModelRow).where(ModelRow.id == model_id))
        return result.scalar_one_or_none()

    async def find_by_name(self, name: str) -> ModelRow | None:
        result = await self._session.execute(select(ModelRow).where(ModelRow.name == name))
        return result.scalar_one_or_none()

    async def create(self, values: dict[str, Any]) -> ModelRow:
        row = ModelRow(**values)
        self._session.add(row)
        await self._session.flush()
        return row

    async def upsert(self, values: dict[str, Any]) -> ModelRow:
        stmt = (
            pg_insert(ModelRow)
            .values(**values)
            .on_conflict_do_update(
                constraint="model_name_unique",
                set_={
                    "family": values.get("family"),
                    "parameter_billions": values.get("parameter_billions"),
                    "quantization": values.get("quantization"),
                },
            )
            .returning(ModelRow.id)
        )
        result = await self._session.execute(stmt)
        model_id = int(result.scalar_one())
        row = await self.get(model_id)
        assert row is not None
        return row

    async def list_names(self) -> list[str]:
        result = await self._session.execute(select(ModelRow.name).order_by(ModelRow.name))
        return list(result.scalars().all())

    async def missing_ids(self, model_ids: list[int]) -> list[int]:
        if not model_ids:
            return []
        unique = sorted({int(model_id) for model_id in model_ids})
        result = await self._session.execute(
            select(ModelRow.id).where(ModelRow.id.in_(unique))
        )
        found = set(result.scalars().all())
        return [model_id for model_id in unique if model_id not in found]

    async def get_many(self, model_ids: list[int]) -> list[ModelRow]:
        if not model_ids:
            return []
        unique = sorted({int(model_id) for model_id in model_ids})
        result = await self._session.execute(
            select(ModelRow).where(ModelRow.id.in_(unique)).order_by(ModelRow.id)
        )
        return list(result.scalars().all())

    async def families(self) -> list[str]:
        stmt = (
            select(ModelRow.family)
            .where(ModelRow.family.is_not(None))
            .distinct()
            .order_by(ModelRow.family)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
