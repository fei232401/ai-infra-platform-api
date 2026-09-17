from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .tables import (
    BackendHealthCheckRow,
    BackendRow,
    ModelRow,
    backend_model_table,
)


class BackendRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _filters(
        state: str | None = None,
        engine: str | None = None,
        name_like: str | None = None,
    ) -> list[Any]:
        clauses: list[Any] = []
        if state is not None:
            clauses.append(BackendRow.state == state)
        if engine is not None:
            clauses.append(BackendRow.engine == engine)
        if name_like is not None:
            clauses.append(BackendRow.name.ilike(f"%{name_like}%"))
        return clauses

    async def list(
        self,
        *,
        state: str | None = None,
        engine: str | None = None,
        name_like: str | None = None,
        with_models: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[BackendRow]:
        stmt = select(BackendRow)
        clauses = self._filters(state=state, engine=engine, name_like=name_like)
        if clauses:
            stmt = stmt.where(*clauses)
        if with_models:
            stmt = stmt.options(selectinload(BackendRow.models))
        stmt = stmt.order_by(BackendRow.id).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count(
        self,
        *,
        state: str | None = None,
        engine: str | None = None,
        name_like: str | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(BackendRow)
        clauses = self._filters(state=state, engine=engine, name_like=name_like)
        if clauses:
            stmt = stmt.where(*clauses)
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def get(self, backend_id: int) -> BackendRow | None:
        stmt = select(BackendRow).where(BackendRow.id == backend_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_by_name(self, name: str) -> BackendRow | None:
        stmt = select(BackendRow).where(BackendRow.name == name)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_by_url(self, url: str) -> BackendRow | None:
        stmt = select(BackendRow).where(BackendRow.url == url)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(self, values: dict[str, Any]) -> BackendRow:
        row = BackendRow(**values)
        self._session.add(row)
        await self._session.flush()
        return row

    async def apply_update(self, backend_id: int, changes: dict[str, Any]) -> BackendRow | None:
        if not changes:
            return await self.get(backend_id)
        stmt = (
            update(BackendRow)
            .where(BackendRow.id == backend_id)
            .values(**changes, updated_at=func.now())
            .returning(BackendRow.id)
        )
        result = await self._session.execute(stmt)
        if result.scalar_one_or_none() is None:
            return None
        return await self.get(backend_id)

    async def list_models(self, backend_id: int) -> list[ModelRow]:
        stmt = (
            select(ModelRow)
            .join(backend_model_table, backend_model_table.c.model_id == ModelRow.id)
            .where(backend_model_table.c.backend_id == backend_id)
            .order_by(ModelRow.id)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def bind_models(self, backend_id: int, model_ids: list[int]) -> int:
        if not model_ids:
            return 0
        values = [{"backend_id": backend_id, "model_id": model_id} for model_id in model_ids]
        stmt = (
            pg_insert(backend_model_table)
            .values(values)
            .on_conflict_do_nothing(index_elements=["backend_id", "model_id"])
        )
        result = await self._session.execute(stmt)
        return int(result.rowcount or 0)

    async def unbind_models(self, backend_id: int, model_ids: list[int]) -> int:
        if not model_ids:
            return 0
        stmt = (
            delete(backend_model_table)
            .where(backend_model_table.c.backend_id == backend_id)
            .where(backend_model_table.c.model_id.in_(model_ids))
        )
        result = await self._session.execute(stmt)
        return int(result.rowcount or 0)

    async def bound_model_ids(self, backend_id: int) -> set[int]:
        stmt = select(backend_model_table.c.model_id).where(
            backend_model_table.c.backend_id == backend_id
        )
        result = await self._session.execute(stmt)
        return set(result.scalars().all())

    async def models_by_backend(self, backend_ids: list[int]) -> dict[int, list[ModelRow]]:
        if not backend_ids:
            return {}
        stmt = (
            select(backend_model_table.c.backend_id, ModelRow)
            .join(ModelRow, ModelRow.id == backend_model_table.c.model_id)
            .where(backend_model_table.c.backend_id.in_(backend_ids))
            .order_by(backend_model_table.c.backend_id, ModelRow.id)
        )
        result = await self._session.execute(stmt)
        grouped: dict[int, list[ModelRow]] = {}
        for backend_id, model in result.all():
            grouped.setdefault(backend_id, []).append(model)
        return grouped

    async def record_health_check(
        self,
        backend_id: int,
        *,
        healthy: bool,
        latency_ms: int | None = None,
        error: str | None = None,
        checked_at: datetime | None = None,
    ) -> BackendHealthCheckRow:
        values: dict[str, Any] = {
            "backend_id": backend_id,
            "healthy": healthy,
            "latency_ms": latency_ms,
            "error": error,
        }
        if checked_at is not None:
            values["checked_at"] = checked_at
        row = BackendHealthCheckRow(**values)
        self._session.add(row)
        await self._session.flush()
        return row

    async def latest_health_map(
        self,
        backend_ids: list[int],
    ) -> dict[int, BackendHealthCheckRow]:
        if not backend_ids:
            return {}
        stmt = (
            select(BackendHealthCheckRow)
            .where(BackendHealthCheckRow.backend_id.in_(backend_ids))
            .distinct(BackendHealthCheckRow.backend_id)
            .order_by(BackendHealthCheckRow.backend_id, BackendHealthCheckRow.checked_at.desc())
        )
        result = await self._session.execute(stmt)
        return {row.backend_id: row for row in result.scalars().all()}

    async def health_history(
        self,
        backend_id: int,
        *,
        limit: int = 20,
    ) -> list[BackendHealthCheckRow]:
        stmt = (
            select(BackendHealthCheckRow)
            .where(BackendHealthCheckRow.backend_id == backend_id)
            .order_by(BackendHealthCheckRow.checked_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_candidates(self, model_name: str) -> list[BackendRow]:
        stmt = (
            select(BackendRow)
            .join(backend_model_table, backend_model_table.c.backend_id == BackendRow.id)
            .join(ModelRow, ModelRow.id == backend_model_table.c.model_id)
            .where(ModelRow.name == model_name)
            .where(BackendRow.state == "active")
            .order_by(BackendRow.id)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def upsert_many(self, values: list[dict[str, Any]]) -> int:
        if not values:
            return 0
        stmt = (
            pg_insert(BackendRow)
            .values(values)
            .on_conflict_do_nothing(constraint="backend_name_unique")
        )
        result = await self._session.execute(stmt)
        return int(result.rowcount or 0)

    async def bulk_insert_health(self, values: list[dict[str, Any]]) -> int:
        if not values:
            return 0
        result = await self._session.execute(insert(BackendHealthCheckRow).values(values))
        return int(result.rowcount or 0)
