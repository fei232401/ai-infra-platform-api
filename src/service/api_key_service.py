from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..domain import ApiKey, ConflictError, NotFoundError, UnauthorizedError, ValidationError
from ..repository import ApiKeyRepository

ALLOWED_SCOPES = {"infer", "admin"}

TOKEN_BYTES = 32

LAST_USED_THROTTLE = timedelta(seconds=60)


class ApiKeyService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._keys = ApiKeyRepository(session)

    def generate_key(self) -> str:
        return f"aip_{secrets.token_urlsafe(TOKEN_BYTES)}"

    def key_prefix(self, key: str) -> str:
        return key[: self._settings.api_key_prefix_length]

    def hash_key(self, key: str) -> str:
        material = f"{self._settings.secret_pepper}{key}".encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    async def create(
        self,
        *,
        name: str,
        scopes: list[str] | None = None,
        rate_limit_per_second: Any = None,
        ttl_days: int | None = None,
        expires_at: datetime | None = None,
    ) -> tuple[ApiKey, str]:
        resolved_scopes = self._validate_scopes(scopes)
        rate_limit = self._validate_rate_limit(rate_limit_per_second)
        expiry = self._resolve_expiry(ttl_days=ttl_days, expires_at=expires_at)

        for _ in range(5):
            plaintext = self.generate_key()
            prefix = self.key_prefix(plaintext)
            key_hash = self.hash_key(plaintext)
            if await self._keys.find_by_hash(key_hash) is not None:
                continue
            if await self._keys.prefix_taken(prefix):
                continue
            row = await self._keys.create(
                {
                    "key_prefix": prefix,
                    "key_hash": key_hash,
                    "name": name,
                    "scopes": resolved_scopes,
                    "rate_limit_per_second": rate_limit,
                    "expires_at": expiry,
                }
            )
            return ApiKey.model_validate(row), plaintext

        raise ConflictError("密钥生成冲突，请重试", detail={"attempts": 5})

    async def verify(self, plaintext: str, *, required_scope: str = "infer") -> ApiKey:
        key_hash = self.hash_key(plaintext)
        row = await self._keys.find_by_hash(key_hash)
        if row is None:
            raise UnauthorizedError("密钥无效")
        if row.revoked_at is not None:
            raise UnauthorizedError("密钥已吊销", detail={"key_id": row.id})
        now = datetime.now(timezone.utc)
        if row.expires_at is not None and row.expires_at <= now:
            raise UnauthorizedError("密钥已过期", detail={"key_id": row.id})
        if required_scope not in row.scopes:
            raise UnauthorizedError(
                "密钥权限不足",
                detail={"key_id": row.id, "required_scope": required_scope, "scopes": row.scopes},
            )
        if row.last_used_at is None or now - row.last_used_at > LAST_USED_THROTTLE:
            await self._keys.touch_last_used(row.id, now)
            row.last_used_at = now
        return ApiKey.model_validate(row)

    async def list(
        self,
        *,
        active_only: bool = False,
        name_like: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ApiKey], int]:
        rows = await self._keys.list(
            active_only=active_only,
            name_like=name_like,
            limit=limit,
            offset=offset,
        )
        total = await self._keys.count(active_only=active_only, name_like=name_like)
        return [ApiKey.model_validate(row) for row in rows], total

    async def get(self, key_id: int) -> ApiKey:
        row = await self._keys.get(key_id)
        if row is None:
            raise NotFoundError("密钥不存在", detail={"key_id": key_id})
        return ApiKey.model_validate(row)

    async def revoke(self, key_id: int) -> ApiKey:
        row = await self._keys.get(key_id)
        if row is None:
            raise NotFoundError("密钥不存在", detail={"key_id": key_id})
        if row.revoked_at is None:
            revoked = await self._keys.revoke(key_id, datetime.now(timezone.utc))
            if not revoked:
                raise ConflictError("密钥状态已变更", detail={"key_id": key_id})
        return await self.get(key_id)

    def _validate_scopes(self, scopes: list[str] | None) -> list[str]:
        if not scopes:
            return ["infer"]
        unknown = sorted({scope for scope in scopes if scope not in ALLOWED_SCOPES})
        if unknown:
            raise ValidationError(
                "scope 取值非法",
                detail={"unknown": unknown, "allowed": sorted(ALLOWED_SCOPES)},
            )
        return sorted(set(scopes))

    def _validate_rate_limit(self, value: Any) -> Decimal | None:
        if value is None:
            return None
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError) as exc:
            raise ValidationError(
                "rate_limit_per_second 不是合法数值",
                detail={"rate_limit_per_second": str(value)},
            ) from exc
        if parsed <= 0:
            raise ValidationError(
                "rate_limit_per_second 必须大于 0",
                detail={"rate_limit_per_second": str(parsed)},
            )
        return parsed

    def _resolve_expiry(
        self,
        *,
        ttl_days: int | None,
        expires_at: datetime | None,
    ) -> datetime | None:
        if ttl_days is not None:
            if ttl_days <= 0:
                raise ValidationError("ttl_days 必须大于 0", detail={"ttl_days": ttl_days})
            return datetime.now(timezone.utc) + timedelta(days=ttl_days)
        if expires_at is not None:
            if expires_at <= datetime.now(timezone.utc):
                raise ValidationError("expires_at 必须晚于当前时间")
            return expires_at
        return None
