from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .tables import ApiKeyRow


class ApiKeyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, values: dict[str, Any]) -> ApiKeyRow:
        row = ApiKeyRow(**values)
        self._session.add(row)
        await self._session.flush()
        return row

    async def get(self, key_id: int) -> ApiKeyRow | None:
        result = await self._session.execute(select(ApiKeyRow).where(ApiKeyRow.id == key_id))
        return result.scalar_one_or_none()

    async def find_by_hash(self, key_hash: str) -> ApiKeyRow | None:
        result = await self._session.execute(
            select(ApiKeyRow).where(ApiKeyRow.key_hash == key_hash)
        )
        return result.scalar_one_or_none()

    async def list(
        self,
        *,
        active_only: bool = False,
        name_like: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ApiKeyRow]:
        stmt = select(ApiKeyRow)
        if active_only:
            stmt = stmt.where(ApiKeyRow.revoked_at.is_(None))
        if name_like is not None:
            stmt = stmt.where(ApiKeyRow.name.ilike(f"%{name_like}%"))
        stmt = stmt.order_by(ApiKeyRow.id).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count(self, *, active_only: bool = False, name_like: str | None = None) -> int:
        stmt = select(func.count()).select_from(ApiKeyRow)
        if active_only:
            stmt = stmt.where(ApiKeyRow.revoked_at.is_(None))
        if name_like is not None:
            stmt = stmt.where(ApiKeyRow.name.ilike(f"%{name_like}%"))
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def revoke(self, key_id: int, when: datetime) -> bool:
        stmt = (
            update(ApiKeyRow)
            .where(ApiKeyRow.id == key_id)
            .where(ApiKeyRow.revoked_at.is_(None))
            .values(revoked_at=when)
            .returning(ApiKeyRow.id)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def touch_last_used(self, key_id: int, when: datetime) -> None:
        stmt = update(ApiKeyRow).where(ApiKeyRow.id == key_id).values(last_used_at=when)
        await self._session.execute(stmt)

    async def prefix_taken(self, key_prefix: str) -> bool:
        stmt = select(func.count()).select_from(ApiKeyRow).where(ApiKeyRow.key_prefix == key_prefix)
        result = await self._session.execute(stmt)
        return int(result.scalar_one()) > 0
