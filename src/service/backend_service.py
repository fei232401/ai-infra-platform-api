from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import (
    Backend,
    BackendHealthCheck,
    ConflictError,
    ModelInfo,
    NotFoundError,
    ValidationError,
)
from ..repository import BackendRepository, ModelRepository
from .scoring import CandidateScore, score_candidates

STATE_TRANSITIONS: dict[str, set[str]] = {
    "active": {"draining", "disabled"},
    "draining": {"active", "disabled"},
    "disabled": {"active"},
}

ENGINES = {"ollama", "openai"}


def _as_decimal(value: Any, field: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise ValidationError(f"{field} 不是合法数值", detail={field: str(value)}) from exc


class BackendService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._backends = BackendRepository(session)
        self._models = ModelRepository(session)

    async def list(
        self,
        *,
        state: str | None = None,
        engine: str | None = None,
        name_like: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Backend], int]:
        await self._validate_engine(engine)
        rows = await self._backends.list(
            state=state,
            engine=engine,
            name_like=name_like,
            limit=limit,
            offset=offset,
        )
        total = await self._backends.count(state=state, engine=engine, name_like=name_like)
        return [Backend.model_validate(row) for row in rows], total

    async def get(self, backend_id: int) -> Backend:
        row = await self._backends.get(backend_id)
        if row is None:
            raise NotFoundError("后端实例不存在", detail={"backend_id": backend_id})
        return Backend.model_validate(row)

    async def register(
        self,
        *,
        name: str,
        url: str,
        engine: str,
        weight: Any = 1,
        max_concurrency: int = 1,
        cost_per_token: Any = 0,
        model_ids: list[int] | None = None,
    ) -> Backend:
        await self._validate_engine(engine)
        parsed_weight = _as_decimal(weight, "weight")
        parsed_cost = _as_decimal(cost_per_token, "cost_per_token")
        self._validate_weight(parsed_weight)
        self._validate_concurrency(max_concurrency)
        self._validate_cost(parsed_cost)

        if await self._backends.find_by_name(name) is not None:
            raise ConflictError("后端名称已存在", detail={"name": name})
        if await self._backends.find_by_url(url) is not None:
            raise ConflictError("后端地址已被注册", detail={"url": url})

        resolved_models = await self._resolve_model_ids(model_ids)

        row = await self._backends.create(
            {
                "name": name,
                "url": url,
                "engine": engine,
                "weight": parsed_weight,
                "max_concurrency": max_concurrency,
                "cost_per_token": parsed_cost,
            }
        )
        if resolved_models:
            await self._backends.bind_models(row.id, resolved_models)
        return await self.get(row.id)

    async def bulk_register(self, items: list[dict[str, Any]]) -> dict[str, int]:
        accepted: list[dict[str, Any]] = []
        for index, item in enumerate(items):
            engine = str(item.get("engine", ""))
            if engine not in ENGINES:
                raise ValidationError(
                    "engine 取值非法",
                    detail={"index": index, "engine": engine, "allowed": sorted(ENGINES)},
                )
            weight = _as_decimal(item.get("weight", 1), "weight")
            cost = _as_decimal(item.get("cost_per_token", 0), "cost_per_token")
            self._validate_weight(weight)
            accepted.append(
                {
                    "name": str(item["name"]),
                    "url": str(item["url"]),
                    "engine": engine,
                    "weight": weight,
                    "max_concurrency": int(item.get("max_concurrency", 1)),
                    "cost_per_token": cost,
                }
            )
        inserted = await self._backends.upsert_many(accepted)
        return {"submitted": len(accepted), "inserted": inserted, "skipped": len(accepted) - inserted}

    async def update(self, backend_id: int, changes: dict[str, Any]) -> Backend:
        current = await self._backends.get(backend_id)
        if current is None:
            raise NotFoundError("后端实例不存在", detail={"backend_id": backend_id})

        payload: dict[str, Any] = {}
        if "url" in changes and changes["url"] is not None:
            url = str(changes["url"])
            existing = await self._backends.find_by_url(url)
            if existing is not None and existing.id != backend_id:
                raise ConflictError("后端地址已被注册", detail={"url": url})
            payload["url"] = url
        if "weight" in changes and changes["weight"] is not None:
            weight = _as_decimal(changes["weight"], "weight")
            self._validate_weight(weight)
            payload["weight"] = weight
        if "max_concurrency" in changes and changes["max_concurrency"] is not None:
            concurrency = int(changes["max_concurrency"])
            self._validate_concurrency(concurrency)
            payload["max_concurrency"] = concurrency
        if "cost_per_token" in changes and changes["cost_per_token"] is not None:
            cost = _as_decimal(changes["cost_per_token"], "cost_per_token")
            self._validate_cost(cost)
            payload["cost_per_token"] = cost

        if not payload:
            raise ValidationError("没有可更新的字段", detail={"allowed": ["url", "weight", "max_concurrency", "cost_per_token"]})

        row = await self._backends.apply_update(backend_id, payload)
        if row is None:
            raise NotFoundError("后端实例不存在", detail={"backend_id": backend_id})
        return Backend.model_validate(row)

    async def change_state(self, backend_id: int, target: str) -> Backend:
        current = await self._backends.get(backend_id)
        if current is None:
            raise NotFoundError("后端实例不存在", detail={"backend_id": backend_id})

        allowed = STATE_TRANSITIONS.get(current.state, set())
        if target not in allowed:
            raise ConflictError(
                "状态流转不被允许",
                detail={"from": current.state, "to": target, "allowed": sorted(allowed)},
            )

        row = await self._backends.apply_update(backend_id, {"state": target})
        if row is None:
            raise NotFoundError("后端实例不存在", detail={"backend_id": backend_id})
        return Backend.model_validate(row)

    async def bind_models(self, backend_id: int, model_ids: list[int]) -> dict[str, Any]:
        if await self._backends.get(backend_id) is None:
            raise NotFoundError("后端实例不存在", detail={"backend_id": backend_id})
        resolved = await self._resolve_model_ids(model_ids)
        inserted = await self._backends.bind_models(backend_id, resolved)
        bound = await self._backends.list_models(backend_id)
        return {
            "backend_id": backend_id,
            "inserted": inserted,
            "model_ids": [row.id for row in bound],
        }

    async def replace_models(self, backend_id: int, model_ids: list[int]) -> dict[str, Any]:
        if await self._backends.get(backend_id) is None:
            raise NotFoundError("后端实例不存在", detail={"backend_id": backend_id})
        resolved = set(await self._resolve_model_ids(model_ids))
        bound = await self._backends.bound_model_ids(backend_id)
        to_add = sorted(resolved - bound)
        to_remove = sorted(bound - resolved)
        inserted = await self._backends.bind_models(backend_id, to_add)
        removed = await self._backends.unbind_models(backend_id, to_remove)
        return {
            "backend_id": backend_id,
            "inserted": inserted,
            "removed": removed,
            "model_ids": sorted(resolved),
        }

    async def unbind_models(self, backend_id: int, model_ids: list[int]) -> dict[str, Any]:
        if await self._backends.get(backend_id) is None:
            raise NotFoundError("后端实例不存在", detail={"backend_id": backend_id})
        resolved = await self._resolve_model_ids(model_ids)
        removed = await self._backends.unbind_models(backend_id, resolved)
        bound = await self._backends.list_models(backend_id)
        return {
            "backend_id": backend_id,
            "removed": removed,
            "model_ids": [row.id for row in bound],
        }

    async def record_health(
        self,
        backend_id: int,
        *,
        healthy: bool,
        latency_ms: int | None = None,
        error: str | None = None,
        checked_at: datetime | None = None,
    ) -> BackendHealthCheck:
        if await self._backends.get(backend_id) is None:
            raise NotFoundError("后端实例不存在", detail={"backend_id": backend_id})
        if latency_ms is not None and latency_ms < 0:
            raise ValidationError("latency_ms 不能为负", detail={"latency_ms": latency_ms})
        if not healthy and latency_ms is not None:
            latency_ms = None
        row = await self._backends.record_health_check(
            backend_id,
            healthy=healthy,
            latency_ms=latency_ms,
            error=error,
            checked_at=checked_at,
        )
        return BackendHealthCheck.model_validate(row)

    async def health_history(self, backend_id: int, *, limit: int = 20) -> list[BackendHealthCheck]:
        if await self._backends.get(backend_id) is None:
            raise NotFoundError("后端实例不存在", detail={"backend_id": backend_id})
        rows = await self._backends.health_history(backend_id, limit=limit)
        return [BackendHealthCheck.model_validate(row) for row in rows]

    async def list_models(self, backend_id: int) -> list[ModelInfo]:
        if await self._backends.get(backend_id) is None:
            raise NotFoundError("后端实例不存在", detail={"backend_id": backend_id})
        rows = await self._backends.list_models(backend_id)
        return [ModelInfo.model_validate(row) for row in rows]

    async def models_by_backend(self, backend_ids: list[int]) -> dict[int, list[ModelInfo]]:
        grouped = await self._backends.models_by_backend(backend_ids)
        return {
            backend_id: [ModelInfo.model_validate(row) for row in rows]
            for backend_id, rows in grouped.items()
        }

    async def rank_candidates(self, model_name: str, policy: str) -> list[CandidateScore]:
        if policy not in POLICY_FACTORS:
            raise ValidationError(
                "policy 取值非法",
                detail={"policy": policy, "allowed": sorted(POLICY_FACTORS)},
            )
        candidates = await self._backends.list_candidates(model_name)
        if not candidates:
            return []
        health_map = await self._backends.latest_health_map([row.id for row in candidates])
        payload = [
            {
                "backend_id": row.id,
                "name": row.name,
                "weight": row.weight,
                "healthy": health_map[row.id].healthy if row.id in health_map else None,
                "latency_ms": health_map[row.id].latency_ms if row.id in health_map else None,
                "cost_per_token": row.cost_per_token,
            }
            for row in candidates
        ]
        return score_candidates(policy, payload)

    async def _validate_engine(self, engine: str | None) -> None:
        if engine is not None and engine not in ENGINES:
            raise ValidationError(
                "engine 取值非法",
                detail={"engine": engine, "allowed": sorted(ENGINES)},
            )

    def _validate_weight(self, weight: Decimal) -> None:
        if weight <= 0:
            raise ValidationError("weight 必须大于 0", detail={"weight": str(weight)})

    def _validate_concurrency(self, concurrency: int) -> None:
        if concurrency <= 0:
            raise ValidationError(
                "max_concurrency 必须大于 0",
                detail={"max_concurrency": concurrency},
            )

    def _validate_cost(self, cost: Decimal) -> None:
        if cost < 0:
            raise ValidationError("cost_per_token 不能为负", detail={"cost_per_token": str(cost)})

    async def _resolve_model_ids(self, model_ids: list[int] | None) -> list[int]:
        if not model_ids:
            return []
        unique = sorted({int(model_id) for model_id in model_ids})
        missing = await self._models.missing_ids(unique)
        if missing:
            raise NotFoundError("模型不存在", detail={"missing_model_ids": missing})
        return unique
