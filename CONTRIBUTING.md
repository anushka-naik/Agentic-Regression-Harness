# Contributing

Thanks for helping. AgentCheck is small on purpose; please keep changes focused.

## Setup

```bash
uv sync --dev
cp .env.example .env
docker compose up -d --wait db      # or any local PostgreSQL 16
uv run alembic upgrade head
make check                          # ruff, mypy, dataset validation, tests, mutation suite
```

No model download is needed for anything except `-m live` tests and live experiments.

## Ground rules

1. **Scoring stays independent of candidate behaviour.** Never derive expected values from
   the tool implementation under test, and never share decision helpers between
   `samples/retail/tools.py` and `evaluators/retail_oracle.py`.
2. **Don't weaken a gate or change an expectation to make a run pass.** If an expectation
   is wrong, fix it in `scripts/generate_dataset.py`, regenerate, explain why in the PR,
   and expect the dataset hash to change.
3. **Keep modes separate.** Fixture, replay and live results must stay labelled. Don't
   present scripted output as model behaviour.
4. **Never fabricate** runs, model digests, metrics or human reviews.
5. Add a test for every new failure mode a check is meant to catch.

## Dataset contributions

- Edit `scripts/generate_dataset.py` only; generated files are committed and CI fails if
  they differ from the generator output (`git diff --exit-code data/retail_v1`).
- New families need two wording variants that differ in phrasing, not just names, and a
  `semantic_parent_id` that keeps near-duplicates in the same split.
- All data must be synthetic. No real names, addresses or customer text.
- `review_status` may be `machine_checked` after automated validation. Only a person may
  mark `human_reviewed` and fill reviewer fields in `review.csv`.

## Commit and PR checklist

- [ ] `make check` passes locally
- [ ] Reports or screenshots in the PR say which mode produced them
- [ ] New behaviour is documented in `docs/`
