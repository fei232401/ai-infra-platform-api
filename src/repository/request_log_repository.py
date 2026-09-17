from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .tables import RequestLogRow, RoutingDecisionRow


class RequestLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _filters(
        *,
        backend_id: int | None = None,
        api_key_id: int | None = None,
        status: str | None = None,
        model_name: str | None = None,
        session_id: str | None = None,
        started_after: datetime | None = None,
        started_before: datetime | None = None,
        failures_only: bool = False,
    ) -> list[Any]:
        clauses: list[Any] = []
        if backend_id is not None:
            clauses.append(RequestLogRow.backend_id == backend_id)
        if api_key_id is not None:
            clauses.append(RequestLogRow.api_key_id == api_key_id)
        if status is not None:
            clauses.append(RequestLogRow.status == status)
        if model_name is not None:
            clauses.append(RequestLogRow.model_name == model_name)
        if session_id is not None:
            clauses.append(RequestLogRow.session_id == session_id)
        if started_after is not None:
            clauses.append(RequestLogRow.started_at >= started_after)
        if started_before is not None:
            clauses.append(RequestLogRow.started_at < started_before)
        if failures_only:
            clauses.append(RequestLogRow.status != "success")
        return clauses

    async def create(self, values: dict[str, Any]) -> RequestLogRow:
        row = RequestLogRow(**values)
        self._session.add(row)
        await self._session.flush()
        return row

    async def get(self, request_id: UUID, *, with_decision: bool = False) -> RequestLogRow | None:
        stmt = select(RequestLogRow).where(RequestLogRow.request_id == request_id)
        if with_decision:
            stmt = stmt.options(selectinload(RequestLogRow.decision))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self,
        *,
        backend_id: int | None = None,
        api_key_id: int | None = None,
        status: str | None = None,
        model_name: str | None = None,
        session_id: str | None = None,
        started_after: datetime | None = None,
        started_before: datetime | None = None,
        failures_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[RequestLogRow]:
        stmt = select(RequestLogRow)
        clauses = self._filters(
            backend_id=backend_id,
            api_key_id=api_key_id,
            status=status,
            model_name=model_name,
            session_id=session_id,
            started_after=started_after,
            started_before=started_before,
            failures_only=failures_only,
        )
        if clauses:
            stmt = stmt.where(*clauses)
        stmt = stmt.order_by(RequestLogRow.started_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count(
        self,
        *,
        backend_id: int | None = None,
        api_key_id: int | None = None,
        status: str | None = None,
        model_name: str | None = None,
        session_id: str | None = None,
        started_after: datetime | None = None,
        started_before: datetime | None = None,
        failures_only: bool = False,
    ) -> int:
        stmt = select(func.count()).select_from(RequestLogRow)
        clauses = self._filters(
            backend_id=backend_id,
            api_key_id=api_key_id,
            status=status,
            model_name=model_name,
            session_id=session_id,
            started_after=started_after,
            started_before=started_before,
            failures_only=failures_only,
        )
        if clauses:
            stmt = stmt.where(*clauses)
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def create_decision(self, values: dict[str, Any]) -> RoutingDecisionRow:
        row = RoutingDecisionRow(**values)
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_decision(self, request_id: UUID) -> RoutingDecisionRow | None:
        result = await self._session.execute(
            select(RoutingDecisionRow).where(RoutingDecisionRow.request_id == request_id)
        )
        return result.scalar_one_or_none()

    async def backend_stats(
        self,
        *,
        started_after: datetime | None = None,
        started_before: datetime | None = None,
    ) -> list[dict[str, Any]]:
        stmt = (
            select(
                RequestLogRow.backend_id.label("backend_id"),
                func.count().label("total"),
                func.count().filter(RequestLogRow.status == "success").label("successes"),
                func.count().filter(RequestLogRow.status != "success").label("failures"),
                func.avg(RequestLogRow.total_ms).label("avg_total_ms"),
                func.avg(RequestLogRow.ttft_ms).label("avg_ttft_ms"),
            )
            .group_by(RequestLogRow.backend_id)
            .order_by(func.count().desc())
        )
        if started_after is not None:
            stmt = stmt.where(RequestLogRow.started_at >= started_after)
        if started_before is not None:
            stmt = stmt.where(RequestLogRow.started_at < started_before)
        result = await self._session.execute(stmt)
        return [dict(row._mapping) for row in result.all()]

    async def latency_percentiles(
        self,
        *,
        backend_id: int | None = None,
        started_after: datetime | None = None,
    ) -> dict[str, Any]:
        stmt = select(
            func.count().label("samples"),
            func.percentile_cont(0.50).within_group(RequestLogRow.total_ms.asc()).label("p50_ms"),
            func.percentile_cont(0.95).within_group(RequestLogRow.total_ms.asc()).label("p95_ms"),
            func.percentile_cont(0.99).within_group(RequestLogRow.total_ms.asc()).label("p99_ms"),
            func.max(RequestLogRow.total_ms).label("max_ms"),
        ).where(RequestLogRow.total_ms.is_not(None))
        if backend_id is not None:
            stmt = stmt.where(RequestLogRow.backend_id == backend_id)
        if started_after is not None:
            stmt = stmt.where(RequestLogRow.started_at >= started_after)
        result = await self._session.execute(stmt)
        return dict(result.one()._mapping)

    async def status_breakdown(
        self,
        *,
        started_after: datetime | None = None,
    ) -> list[dict[str, Any]]:
        stmt = (
            select(RequestLogRow.status.label("status"), func.count().label("total"))
            .group_by(RequestLogRow.status)
            .order_by(func.count().desc())
        )
        if started_after is not None:
            stmt = stmt.where(RequestLogRow.started_at >= started_after)
        result = await self._session.execute(stmt)
        return [dict(row._mapping) for row in result.all()]

    async def delete_finished_before(self, cutoff: datetime, *, batch_size: int = 5000) -> int:
        subquery = (
            select(RequestLogRow.request_id)
            .where(RequestLogRow.finished_at.is_not(None))
            .where(RequestLogRow.finished_at < cutoff)
            .limit(batch_size)
            .scalar_subquery()
        )
        stmt = delete(RequestLogRow).where(RequestLogRow.request_id.in_(subquery))
        result = await self._session.execute(stmt)
        return int(result.rowcount or 0)
