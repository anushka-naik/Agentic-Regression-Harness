from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from agentcheck.dataset import Dataset, load_dataset
from agentcheck.schemas.cases import LoadedCase
from agentcheck.schemas.config import AgentConfig

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data" / "retail_v1"
PROMPT = (ROOT / "prompts" / "support_v1.txt").read_text()


@pytest.fixture(scope="session")
def dataset() -> Dataset:
    return load_dataset(DATASET)


@pytest.fixture(scope="session")
def case(dataset: Dataset) -> Any:
    def get(case_id: str) -> LoadedCase:
        return dataset.select([case_id])[0]
    return get


def scripted_config(**overrides: Any) -> AgentConfig:
    base: dict[str, Any] = {"agent_id": "test", "provider": "scripted", "model": "reference-policy",
                            "prompt_path": "prompts/support_v1.txt"}
    base.update(overrides)
    return AgentConfig.model_validate(base)


def action(**response: Any) -> str:
    """A raw final action with sensible nulls."""
    body = {"outcome": "unsupported", "order_id": None, "order_status": None, "eligible": None,
            "refund_amount_cents": None, "currency": None, "reason_code": None, "refund_issued": False,
            "requested_field": None, "evidence_event_ids": [], "message": "ok"}
    body.update(response)
    return json.dumps({"action": "final", "tool_name": None, "arguments": None, "response": body})


def tool(name: str, **arguments: Any) -> str:
    return json.dumps({"action": "tool_call", "tool_name": name, "arguments": arguments, "response": None})


def _db_url() -> str | None:
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        return url
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("TEST_DATABASE_URL="):
                return line.split("=", 1)[1].strip()
    return None


@pytest.fixture()
def repo() -> Iterator[Any]:
    url = _db_url()
    if not url:
        pytest.skip("TEST_DATABASE_URL not configured")
    from agentcheck.errors import InfrastructureError
    from agentcheck.storage.models import Base
    from agentcheck.storage.repository import Repository, make_engine

    try:
        engine = make_engine(url)
    except InfrastructureError:
        pytest.skip("test database unreachable")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield Repository(engine)
    Base.metadata.drop_all(engine)
    engine.dispose()
