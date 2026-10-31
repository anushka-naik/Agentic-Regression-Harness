# samples/retail (facade)

The Northstar Demo Store tools are installed with the package so a clean checkout works
after `uv sync`. The real files are:

| What | Where |
| --- | --- |
| Tool implementation under test | `src/agentcheck/samples/retail/tools.py` |
| Canonical policy (human-readable) | `src/agentcheck/samples/retail/policy.md` |
| Controlled mutations | `src/agentcheck/samples/retail/mutations/` |

The reference oracle lives elsewhere on purpose (`src/agentcheck/evaluators/retail_oracle.py`)
and shares no decision code with the tools.
