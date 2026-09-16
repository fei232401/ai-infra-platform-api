from datetime import datetime
from decimal import Decimal
from typing import Any, Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class PageMeta(BaseModel):
    limit: int
    offset: int
    total: int
    count: int
    has_more: bool


class Page(BaseModel, Generic[T]):
    items: list[T]
    meta: PageMeta


class ErrorOut(BaseModel):
    code: str
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


class ModelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    family: str | None = None
    parameter_billions: float | None = None
    quantization: str | None = None
    created_at: datetime


class ModelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    family: str | None = Field(default=None, max_length=100)
    parameter_billions: Decimal | None = Field(default=None, gt=0)
    quantization: str | None = Field(default=None, max_length=50)


class BackendOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    url: str
    engine: str
    weight: float
    max_concurrency: int
    cost_per_token: float
    state: str
    created_at: datetime
    updated_at: datetime
    models: list[ModelOut] | None = None


class BackendCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=500)
    engine: str
    weight: Decimal = Field(default=Decimal("1.000"), gt=0)
    max_concurrency: int = Field(default=1, gt=0)
    cost_per_token: Decimal = Field(default=Decimal("0"), ge=0)
    model_ids: list[int] = Field(default_factory=list)


class BackendUpdate(BaseModel):
    url: str | None = Field(default=None, min_length=1, max_length=500)
    weight: Decimal | None = Field(default=None, gt=0)
    max_concurrency: int | None = Field(default=None, gt=0)
    cost_per_token: Decimal | None = Field(default=None, ge=0)


class BackendBulkCreate(BaseModel):
    items: list[BackendCreate] = Field(min_length=1, max_length=500)


class BackendStateChange(BaseModel):
    state: str


class ModelIdsPayload(BaseModel):
    model_ids: list[int] = Field(default_factory=list)


class HealthCheckCreate(BaseModel):
    healthy: bool
    latency_ms: int | None = Field(default=None, ge=0)
    error: str | None = Field(default=None, max_length=500)
    checked_at: datetime | None = None


class HealthCheckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    backend_id: int
    checked_at: datetime
    healthy: bool
    latency_ms: int | None = None
    error: str | None = None


class CandidateOut(BaseModel):
    backend_id: int
    name: str
    weight: float
    healthy: bool | None
    latency_ms: int | None
    cost_per_token: float
    score: float
    excluded: bool
    reasons: list[str]


class CandidateListOut(BaseModel):
    model_name: str
    policy: str
    ranked: list[CandidateOut]


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    key_prefix: str
    name: str
    scopes: list[str]
    rate_limit_per_second: float | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None
    created_at: datetime


class ApiKeyCreatedOut(ApiKeyOut):
    key: str


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    scopes: list[str] = Field(default_factory=lambda: ["infer"])
    rate_limit_per_second: Decimal | None = Field(default=None, gt=0)
    ttl_days: int | None = Field(default=None, gt=0)
    expires_at: datetime | None = None


class ApiKeyVerifyIn(BaseModel):
    key: str = Field(min_length=8)


class ApiKeyVerifyOut(BaseModel):
    valid: bool
    api_key: ApiKeyOut | None = None


class RoutingDecisionIn(BaseModel):
    policy: str
    candidate_ids: list[int] = Field(default_factory=list)
    chosen_id: int | None = None
    score_snapshot: dict[str, Any] = Field(default_factory=dict)
    fallback_reason: str | None = Field(default=None, max_length=500)


class RoutingDecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    request_id: UUID
    policy: str
    candidate_ids: list[int]
    chosen_id: int | None = None
    score_snapshot: dict[str, Any]
    fallback_reason: str | None = None
    decided_at: datetime


class RequestLogIn(BaseModel):
    model_name: str = Field(min_length=1, max_length=200)
    status: str
    started_at: datetime
    request_id: UUID | None = None
    api_key_id: int | None = None
    session_id: str | None = Field(default=None, max_length=200)
    backend_id: int | None = None
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    ttft_ms: Decimal | None = Field(default=None, ge=0)
    total_ms: Decimal | None = Field(default=None, ge=0)
    error_code: str | None = Field(default=None, max_length=100)
    finished_at: datetime | None = None
    decision: RoutingDecisionIn | None = None


class RequestLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    request_id: UUID
    api_key_id: int | None = None
    session_id: str | None = None
    model_name: str
    backend_id: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    ttft_ms: float | None = None
    total_ms: float | None = None
    status: str
    error_code: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class RequestLogDetailOut(RequestLogOut):
    decision: RoutingDecisionOut | None = None


class BackendStatOut(BaseModel):
    backend_id: int | None = None
    total: int
    successes: int
    failures: int
    avg_total_ms: float | None = None
    avg_ttft_ms: float | None = None


class StatusStatOut(BaseModel):
    status: str
    total: int


class LatencyStatOut(BaseModel):
    samples: int
    p50_ms: float | None = None
    p95_ms: float | None = None
    p99_ms: float | None = None
    max_ms: float | None = None


class SummaryOut(BaseModel):
    by_backend: list[BackendStatOut]
    by_status: list[StatusStatOut]
    latency: LatencyStatOut


class PurgeIn(BaseModel):
    cutoff: datetime
    batch_size: int = Field(default=5000, ge=1, le=50000)


class PurgeOut(BaseModel):
    deleted: int
    cutoff: datetime
    batch_size: int


class PoolStatusOut(BaseModel):
    size: int
    checked_in: int
    checked_out: int
    overflow: int


class ReadyOut(BaseModel):
    status: str
    database: str
    schema_version: int | None = None
    pool: PoolStatusOut | None = None
    failed: list[str] = Field(default_factory=list)
