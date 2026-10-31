# Third-party notices

AgentCheck depends on the packages below. Each keeps its own license; AgentCheck's Apache-2.0
license does not replace them. License identifiers were read from each installed package's
metadata at the versions pinned in `uv.lock`; re-check them when upgrading.

| Package | Version tested | License (from package metadata) | Source | Use |
| --- | --- | --- | --- | --- |
| fastapi | 0.162.0 | MIT | https://github.com/fastapi/fastapi | runtime |
| uvicorn | 0.54.0 | BSD-3-Clause | https://uvicorn.dev/ | runtime |
| Jinja2 | 3.1.6 | BSD License | https://github.com/pallets/jinja/ | runtime |
| httpx | 0.28.1 | BSD License | https://github.com/encode/httpx | runtime |
| pydantic | 2.14.2 | MIT | https://github.com/pydantic/pydantic | runtime |
| pydantic-settings | 2.16.2 | MIT | https://github.com/pydantic/pydantic-settings | runtime |
| SQLAlchemy | 2.1.6 | MIT | https://www.sqlalchemy.org | runtime |
| psycopg | 3.3.6 | LGPL-3.0-only (used unmodified, as a separately installed library) | https://github.com/psycopg/psycopg | runtime |
| psycopg-binary | 3.3.6 | LGPL-3.0-only | https://psycopg.org/ | runtime |
| alembic | 1.20.1 | MIT | https://alembic.sqlalchemy.org | runtime |
| typer | 0.27.3 | MIT | https://github.com/fastapi/typer | runtime |
| PyYAML | 6.0.3 | MIT License | https://github.com/yaml/pyyaml | runtime |
| starlette | 1.10.0 | BSD-3-Clause | https://github.com/Kludex/starlette | runtime |
| pytest | 9.2.0 | MIT | https://docs.pytest.org/en/latest/ | dev |
| pytest-asyncio | 1.4.1 | Apache-2.0 | https://github.com/pytest-dev/pytest-asyncio | dev |
| hypothesis | 6.170.14 | MPL-2.0 | https://hypothesis.works | dev |
| ruff | 0.17.3 | MIT | https://github.com/astral-sh/ruff | dev |
| mypy | 2.4.1 | MIT | https://www.mypy-lang.org/ | dev |
| types-PyYAML | 6.0.12.20261030 | Apache-2.0 | https://github.com/python/typeshed | dev |

## Not distributed, downloaded by the user

| Component | License | Source |
| --- | --- | --- |
| Ollama (runtime) | MIT | https://github.com/ollama/ollama |
| Qwen3-4B / Qwen3-8B weights | Apache-2.0 (per the official model cards) | https://huggingface.co/Qwen/Qwen3-4B, https://huggingface.co/Qwen/Qwen3-8B |
| PostgreSQL 16 (Docker image) | PostgreSQL License | https://www.postgresql.org/about/licence/ |

## Design references (no code or data included)

- tau2-bench, Sierra Research: https://github.com/sierra-research/tau2-bench
- Promptfoo: https://github.com/promptfoo/promptfoo
