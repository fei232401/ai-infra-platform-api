from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Table,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

backend_model_table = Table(
    "backend_model",
    Base.metadata,
    Column("backend_id", BigInteger, ForeignKey("backend.id", ondelete="CASCADE"), primary_key=True),
    Column("model_id", BigInteger, ForeignKey("model.id", ondelete="CASCADE"), primary_key=True),
    Column("loaded_at", DateTime(timezone=True)),
)


class SchemaMigrationRow(Base):
    __tablename__ = "schema_migration"

    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModelRow(Base):
    __tablename__ = "model"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    family: Mapped[str | None] = mapped_column(Text)
    parameter_billions: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    quantization: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    backends: Mapped[list["BackendRow"]] = relationship(
        secondary=backend_model_table,
        back_populates="models",
        lazy="raise",
    )


class BackendRow(Base):
    __tablename__ = "backend"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    engine: Mapped[str] = mapped_column(Text, nullable=False)
    weight: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False, server_default="1.000")
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    cost_per_token: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False, server_default="0")
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    models: Mapped[list[ModelRow]] = relationship(
        secondary=backend_model_table,
        back_populates="backends",
        lazy="raise",
    )
    health_checks: Mapped[list["BackendHealthCheckRow"]] = relationship(
        back_populates="backend",
        lazy="raise",
        cascade="all, delete-orphan",
    )


class BackendHealthCheckRow(Base):
    __tablename__ = "backend_health_check"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    backend_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("backend.id", ondelete="CASCADE"),
        nullable=False,
    )
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    healthy: Mapped[bool] = mapped_column(Boolean, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)

    backend: Mapped[BackendRow] = relationship(back_populates="health_checks", lazy="raise")


class ApiKeyRow(Base):
    __tablename__ = "api_key"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    key_prefix: Mapped[str] = mapped_column(Text, nullable=False)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{infer}")
    rate_limit_per_second: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RequestLogRow(Base):
    __tablename__ = "request_log"

    request_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    api_key_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("api_key.id", ondelete="SET NULL"),
    )
    session_id: Mapped[str | None] = mapped_column(Text)
    model_name: Mapped[str] = mapped_column(Text, nullable=False)
    backend_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("backend.id", ondelete="SET NULL"),
    )
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    ttft_ms: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    total_ms: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    decision: Mapped["RoutingDecisionRow | None"] = relationship(
        back_populates="request",
        lazy="raise",
        cascade="all, delete-orphan",
        uselist=False,
    )


class RoutingDecisionRow(Base):
    __tablename__ = "routing_decision"

    request_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("request_log.request_id", ondelete="CASCADE"),
        primary_key=True,
    )
    policy: Mapped[str] = mapped_column(Text, nullable=False)
    candidate_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    chosen_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("backend.id", ondelete="SET NULL"),
    )
    score_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    fallback_reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    request: Mapped[RequestLogRow] = relationship(back_populates="decision", lazy="raise")
