"""Evaluation case schema (``northstar-retail-v1``).

The full :class:`EvaluationCase` is grading-side data. The actor only ever
receives :class:`AgentCaseInput`, which carries user messages (through the
scripted user) and permitted session context, never expectations, labels,
category names or identifiers.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentcheck.schemas.domain import World
from agentcheck.schemas.protocol import FinalResponse, Outcome, RequestedField

Category = Literal[
    "order_status",
    "eligible_refund",
    "ineligible_refund",
    "time_boundary",
    "missing_order_id",
    "missing_reason",
    "access_and_not_found",
    "transient_tool_failure",
    "persistent_tool_failure",
    "untrusted_tool_note",
    "unsupported_action",
    "multi_turn_correction",
]
CATEGORIES: tuple[str, ...] = Category.__args__  # type: ignore[attr-defined]
Split = Literal["dev", "validation", "heldout"]
ReviewStatus = Literal["unreviewed", "machine_checked", "human_reviewed"]
TransientCode = Literal["timeout", "temporarily_unavailable"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Session(Strict):
    customer_id: str = Field(pattern=r"^C-\d{3}$")


class ResponsePredicate(Strict):
    outcome: Outcome
    requested_field: RequestedField | None = None

    def matches(self, response: FinalResponse) -> bool:
        if response.outcome != self.outcome:
            return False
        return self.requested_field is None or response.requested_field == self.requested_field


class DialogueStep(Strict):
    user: str = Field(min_length=1)
    after_response: Literal["stop"] | ResponsePredicate


class FaultError(Strict):
    code: TransientCode
    retryable: bool = True


class FaultAttempt(Strict):
    index: int = Field(ge=1)
    error: FaultError


class FaultPlanEntry(Strict):
    tool_name: str
    arguments: dict[str, Any]
    attempts: list[FaultAttempt]
    otherwise: Literal["normal"] = "normal"


class ExpectationBranch(Strict):
    outcomes: list[Outcome] = Field(min_length=1)
    fields: dict[str, Any] = Field(default_factory=dict)
    required_tools_in_order: list[str] = Field(default_factory=list)
    expected_arguments: dict[str, dict[str, Any]] = Field(default_factory=dict)
    forbidden_tools: list[str] = Field(default_factory=list)
    must_not_call: list[str] = Field(default_factory=list)
    required_attempts: dict[str, int] = Field(default_factory=dict)
    max_tool_attempts: int = Field(default=2, ge=1)
    evidence_required: bool = True

    @field_validator("fields")
    @classmethod
    def _known_fields(cls, fields: dict[str, Any]) -> dict[str, Any]:
        unknown = set(fields) - set(FinalResponse.model_fields)
        if unknown:
            raise ValueError(f"unknown expected fields: {sorted(unknown)}")
        return fields


class AnyOfExpectation(Strict):
    any_of: list[ExpectationBranch] = Field(min_length=2)


ExpectedTurn = ExpectationBranch | AnyOfExpectation


def branches(turn: ExpectedTurn) -> list[ExpectationBranch]:
    return list(turn.any_of) if isinstance(turn, AnyOfExpectation) else [turn]


class EvaluationCase(Strict):
    schema_version: Literal[1] = 1
    case_id: str = Field(pattern=r"^[a-z0-9_]+$")
    scenario_family_id: str
    semantic_parent_id: str
    split: Split
    category: Category
    severity: Literal["normal", "critical"] = "normal"
    world_file: str
    clock_utc: str
    session: Session
    dialogue: list[DialogueStep] = Field(min_length=1, max_length=3)
    fault_plan: list[FaultPlanEntry] = Field(default_factory=list)
    expected_turns: list[ExpectedTurn]
    review_status: ReviewStatus = "unreviewed"
    notes: str = ""

    @field_validator("world_file")
    @classmethod
    def _safe_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or "\\" in value:
            raise ValueError("world_file must be a relative path beneath the dataset directory")
        return value

    @model_validator(mode="after")
    def _dialogue_consistency(self) -> EvaluationCase:
        if len(self.expected_turns) != len(self.dialogue):
            raise ValueError("expected_turns must have one entry per dialogue step")
        for step in self.dialogue[:-1]:
            if step.after_response == "stop":
                raise ValueError("only the final dialogue step may stop")
        if self.dialogue[-1].after_response != "stop":
            raise ValueError("the final dialogue step must stop")
        return self


class LoadedCase(BaseModel):
    """A case joined with its resolved world. Grading-side only."""

    model_config = ConfigDict(frozen=True)

    case: EvaluationCase
    world: World
    world_sha256: str


class UserTurnSource(Protocol):
    """What the actor may see of the scripted user: messages, one at a time."""

    def first_message(self) -> str: ...

    def next_message(self, response: FinalResponse) -> str | None: ...


class AgentCaseInput(BaseModel):
    """The only case information that crosses into the actor."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    session_context: dict[str, Any]
    user: Any  # UserTurnSource; typed loosely so pydantic does not inspect it
