from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import (
    ConflictError,
    NotFoundError,
    RequestLog,
    RequestStatus,
    RoutingDecision,
    RoutingPolicy,
    ValidationError,
)
from ..repository import RequestLogRepository

FAILED_STATUSES = {"error", "timeout", "rejected"}


class RequestLogService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._logs = RequestLogRepository(session)

    async def record(
        self,
        *,
        model_name: str,
        status: str,
        started_at: datetime,
        request_id: UUID | None = None,
        api_key_id: int | None = None,
        session_id: str | None = None,
        backend_id: int | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        ttft_ms: Any = None,
        total_ms: Any = None,
        error_code: str | None = None,
        finished_at: datetime | None = None,
        decision: dict[str, Any] | None = None,
    ) -> tuple[RequestLog, RoutingDecision | None]:
        self._validate_status(status)
        resolved_id = request_id or uuid4()
        if await self._logs.get(resolved_id) is not None:
            raise ConflictError("该 request_id 已存在", detail={"request_id": str(resolved_id)})

        prompt = self._validate_tokens(prompt_tokens, "prompt_tokens")
        completion = self._validate_tokens(completion_tokens, "completion_tokens")
        ttft = self._validate_millis(ttft_ms, "ttft_ms")
        total = self._validate_millis(total_ms, "total_ms")
        finished = self._validate_timeline(started_at, finished_at, status)
        resolved_error = self._validate_error_code(status, error_code)

        row = await self._logs.create(
            {
                "request_id": resolved_id,
                "api_key_id": api_key_id,
                "session_id": session_id,
                "model_name": model_name,
                "backend_id": backend_id,
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "ttft_ms": ttft,
                "total_ms": total,
                "status": status,
                "error_code": resolved_error,
                "started_at": started_at,
                "finished_at": finished,
            }
        )

        decision_entity: RoutingDecision | None = None
        if decision is not None:
            decision_entity = await self._record_decision(resolved_id, decision)

        return RequestLog.model_validate(row), decision_entity

    async def get(self, request_id: UUID) -> tuple[RequestLog, RoutingDecision | None]:
        row = await self._logs.get(request_id, with_decision=True)
        if row is None:
            raise NotFoundError("请求记录不存在", detail={"request_id": str(request_id)})
        decision = (
            RoutingDecision.model_validate(row.decision) if row.decision is not None else None
        )
        return RequestLog.model_validate(row), decision

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
    ) -> tuple[list[RequestLog], int]:
        if status is not None:
            self._validate_status(status)
        rows = await self._logs.list(
            backend_id=backend_id,
            api_key_id=api_key_id,
            status=status,
            model_name=model_name,
            session_id=session_id,
            started_after=started_after,
            started_before=started_before,
            failures_only=failures_only,
            limit=limit,
            offset=offset,
        )
        total = await self._logs.count(
            backend_id=backend_id,
            api_key_id=api_key_id,
            status=status,
            model_name=model_name,
            session_id=session_id,
            started_after=started_after,
            started_before=started_before,
            failures_only=failures_only,
        )
        return [RequestLog.model_validate(row) for row in rows], total

    async def summary(
        self,
        *,
        started_after: datetime | None = None,
        started_before: datetime | None = None,
        backend_id: int | None = None,
    ) -> dict[str, Any]:
        by_backend = await self._logs.backend_stats(
            started_after=started_after,
            started_before=started_before,
        )
        by_status = await self._logs.status_breakdown(started_after=started_after)
        percentiles = await self._logs.latency_percentiles(
            backend_id=backend_id,
            started_after=started_after,
        )
        return {
            "by_backend": [self._normalize_row(item) for item in by_backend],
            "by_status": [self._normalize_row(item) for item in by_status],
            "latency": self._normalize_row(percentiles),
        }

    async def purge_finished_before(self, cutoff: datetime, *, batch_size: int = 5000) -> int:
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)
        return await self._logs.delete_finished_before(cutoff, batch_size=batch_size)

    async def _record_decision(
        self,
        request_id: UUID,
        payload: dict[str, Any],
    ) -> RoutingDecision:
        policy = str(payload.get("policy", ""))
        if policy not in set(RoutingPolicy):
            raise ValidationError(
                "policy 取值非法",
                detail={"policy": policy, "allowed": [item.value for item in RoutingPolicy]},
            )

        raw_candidates = payload.get("candidate_ids") or []
        candidate_ids = [int(item) for item in raw_candidates]
        chosen_raw = payload.get("chosen_id")
        chosen_id = int(chosen_raw) if chosen_raw is not None else None
        if chosen_id is not None and chosen_id not in candidate_ids:
            raise ValidationError(
                "chosen_id 必须来自 candidate_ids",
                detail={"chosen_id": chosen_id, "candidate_ids": candidate_ids},
            )

        snapshot = payload.get("score_snapshot")
        if not isinstance(snapshot, dict):
            raise ValidationError("score_snapshot 必须是对象")

        row = await self._logs.create_decision(
            {
                "request_id": request_id,
                "policy": policy,
                "candidate_ids": candidate_ids,
                "chosen_id": chosen_id,
                "score_snapshot": snapshot,
                "fallback_reason": payload.get("fallback_reason"),
            }
        )
        return RoutingDecision.model_validate(row)

    def _validate_status(self, status: str) -> None:
        if status not in set(RequestStatus):
            raise ValidationError(
                "status 取值非法",
                detail={"status": status, "allowed": [item.value for item in RequestStatus]},
            )

    def _validate_tokens(self, value: int | None, field: str) -> int | None:
        if value is None:
            return None
        parsed = int(value)
        if parsed < 0:
            raise ValidationError(f"{field} 不能为负", detail={field: parsed})
        return parsed

    def _validate_millis(self, value: Any, field: str) -> Decimal | None:
        if value is None:
            return None
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError) as exc:
            raise ValidationError(f"{field} 不是合法数值", detail={field: str(value)}) from exc
        if parsed < 0:
            raise ValidationError(f"{field} 不能为负", detail={field: str(parsed)})
        return parsed

    def _validate_timeline(
        self,
        started_at: datetime,
        finished_at: datetime | None,
        status: str,
    ) -> datetime | None:
        if finished_at is None:
            if status in {"success"} | FAILED_STATUSES:
                raise ValidationError("已结束的请求必须提供 finished_at", detail={"status": status})
            return None
        if finished_at < started_at:
            raise ValidationError(
                "finished_at 不能早于 started_at",
                detail={
                    "started_at": started_at.isoformat(),
                    "finished_at": finished_at.isoformat(),
                },
            )
        return finished_at

    def _validate_error_code(self, status: str, error_code: str | None) -> str | None:
        if status == RequestStatus.SUCCESS and error_code:
            raise ValidationError("成功请求不应携带 error_code", detail={"error_code": error_code})
        if status in FAILED_STATUSES and not error_code:
            raise ValidationError("失败请求必须携带 error_code", detail={"status": status})
        return error_code

    def _normalize_row(self, row: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, Decimal):
                normalized[key] = float(value)
            elif isinstance(value, datetime):
                normalized[key] = value.isoformat()
            else:
                normalized[key] = value
        return normalized
