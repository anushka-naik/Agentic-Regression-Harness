# Dependency verification

Recorded on 2026-10-31 on the development machine (Apple M4 Pro, 24 GB RAM, macOS 15.7.4).
Exact versions are pinned in `uv.lock`; `uv sync --frozen` reproduces them.

| Component | Version tested | How verified |
| --- | --- | --- |
| Python | 3.12.13 | `python --version` |
| uv | 0.13.6 | `uv --version` |
| PostgreSQL | 16.15 (Homebrew; `postgres:16` image in Compose and CI) | `select version()` |
| Ollama | 0.35.1 | `GET /api/version` via `agentcheck doctor` |
| qwen3:4b-q4_K_M | digest `2bfd38a7daaf4b1037efe517ccb73d1a3bbd4822cf89f1a82be1569050a114e0` | `GET /api/tags` (full digest, not the web short ID) |
| qwen3:8b-q4_K_M | see `artifacts/judge-*/judge_manifest.json` when run | `GET /api/tags` |
| fastapi / pydantic / sqlalchemy / httpx | see `THIRD_PARTY_NOTICES.md` | installed package metadata |

Ollama interfaces used: `POST /api/chat` with `stream=false`, `think=false`, `format`
(JSON schema) and `options.{temperature,num_ctx,num_predict,seed}`; `GET /api/tags`;
`GET /api/version`. `agentcheck doctor` checks each one, including that no `thinking`
field comes back when `think=false`.

Licenses were read from installed package metadata (see `THIRD_PARTY_NOTICES.md`). The
Apache-2.0 `LICENSE` file is the canonical text from apache.org
(SHA-256 `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`); the CC0
text in `DATA_LICENSE` is the Creative Commons legal code.
