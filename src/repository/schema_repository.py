from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .tables import SchemaMigrationRow


class SchemaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def current_version(self) -> int | None:
        stmt = select(func.max(SchemaMigrationRow.version))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def applied_versions(self) -> list[int]:
        stmt = select(SchemaMigrationRow.version).order_by(SchemaMigrationRow.version)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
