"""SQLAlchemy models. Identifiers, statuses, timestamps and joins are typed
columns; variable payloads are JSONB."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

JsonB = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    schema_version: Mapped[int] = mapped_column(Integer)
    content_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    manifest: Mapped[dict[str, Any]] = mapped_column(JsonB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentConfigRow(Base):
    __tablename__ = "agent_configs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    config_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    config: Mapped[dict[str, Any]] = mapped_column(JsonB)
    prompt_sha256: Mapped[str] = mapped_column(String(64))
    tool_revision: Mapped[str] = mapped_column(String(200))


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    dataset_version_id: Mapped[int] = mapped_column(ForeignKey("dataset_versions.id"))
    agent_config_id: Mapped[int] = mapped_column(ForeignKey("agent_configs.id"))
    side: Mapped[str] = mapped_column(String(20))
    mode: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    manifest: Mapped[dict[str, Any]] = mapped_column(JsonB)
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    attempts: Mapped[list[CaseAttempt]] = relationship(back_populates="run")


class CaseAttempt(Base):
    __tablename__ = "case_attempts"
    __table_args__ = (
        UniqueConstraint("run_id", "case_id", "repeat_index", "attempt_index"),
        Index("ix_case_attempts_run_status", "run_id", "status"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    case_id: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(100))
    repeat_index: Mapped[int] = mapped_column(Integer)
    attempt_index: Mapped[int] = mapped_column(Integer)
    seed: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30))
    rerun_reason: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    result: Mapped[dict[str, Any] | None] = mapped_column(JsonB)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped[Run] = relationship(back_populates="attempts")


class TraceEventRow(Base):
    __tablename__ = "trace_events"
    __table_args__ = (
        UniqueConstraint("case_attempt_id", "sequence"),
        Index("ix_trace_events_attempt_sequence", "case_attempt_id", "sequence"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_attempt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_attempts.id", ondelete="CASCADE"))
    sequence: Mapped[int] = mapped_column(Integer)
    event_id: Mapped[str] = mapped_column(String(40))
    event_type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JsonB)
    wall_time: Mapped[str] = mapped_column(String(40))
    elapsed_ms: Mapped[float] = mapped_column(Float)


class CheckResultRow(Base):
    __tablename__ = "check_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_attempt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_attempts.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20))
    severity: Mapped[str] = mapped_column(String(20))
    expected: Mapped[Any] = mapped_column(JsonB, nullable=True)
    actual: Mapped[Any] = mapped_column(JsonB, nullable=True)
    evidence: Mapped[Any] = mapped_column(JsonB, nullable=True)


class Comparison(Base):
    __tablename__ = "comparisons"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    baseline_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"))
    candidate_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"))
    gate_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    report: Mapped[dict[str, Any]] = mapped_column(JsonB)
    report_markdown: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JudgeReview(Base):
    __tablename__ = "judge_reviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_attempt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_attempts.id", ondelete="CASCADE"), index=True)
    judge_config_sha256: Mapped[str] = mapped_column(String(64))
    rubric_sha256: Mapped[str] = mapped_column(String(64))
    verdict: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict[str, Any]] = mapped_column(JsonB)
    human_review_status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
