from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from .enums import BackendEngine, BackendState, RequestStatus, RoutingPolicy


class DomainBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ModelInfo(DomainBase):
    id: int
    name: str
    family: str | None = None
    parameter_billions: Decimal | None = None
    quantization: str | None = None
    created_at: datetime


class Backend(DomainBase):
    id: int
    name: str
    url: str
    engine: BackendEngine
    weight: Decimal
    max_concurrency: int
    cost_per_token: Decimal
    state: BackendState
    created_at: datetime
    updated_at: datetime


class BackendHealthCheck(DomainBase):
    id: int
    backend_id: int
    checked_at: datetime
    healthy: bool
    latency_ms: int | None = None
    error: str | None = None


class ApiKey(DomainBase):
    id: int
    key_prefix: str
    name: str
    scopes: list[str]
    rate_limit_per_second: Decimal | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None
    created_at: datetime


class RequestLog(DomainBase):
    request_id: UUID
    api_key_id: int | None = None
    session_id: str | None = None
    model_name: str
    backend_id: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    ttft_ms: Decimal | None = None
    total_ms: Decimal | None = None
    status: RequestStatus
    error_code: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class RoutingDecision(DomainBase):
    request_id: UUID
    policy: RoutingPolicy
    candidate_ids: list[int]
    chosen_id: int | None = None
    score_snapshot: dict[str, object]
    fallback_reason: str | None = None
    decided_at: datetime
