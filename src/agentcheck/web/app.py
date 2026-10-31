"""Read-only dashboard and JSON API.

Binds to localhost; there is no authentication in version 1. Every model output,
tool note and imported trace is rendered through Jinja2 autoescaping; nothing is
marked safe.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from agentcheck import __version__
from agentcheck.errors import InfrastructureError
from agentcheck.settings import Settings
from agentcheck.storage.repository import MAX_PAGE_SIZE, Repository, make_engine

HERE = Path(__file__).parent


def _uuid(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid UUID") from None


def _pretty(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True, default=str)


def _short(value: Any, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def create_app(repo: Repository | None = None) -> FastAPI:
    app = FastAPI(title="AgentCheck", version=__version__, docs_url="/api/docs", redoc_url=None)
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.filters["pretty"] = _pretty
    templates.env.filters["short"] = _short
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    db_error: str | None = None
    if repo is None:
        settings = Settings()
        if settings.database_url:
            try:
                repo = Repository(make_engine(settings.database_url))
            except InfrastructureError as exc:
                db_error = str(exc)
        else:
            db_error = "DATABASE_URL is not set"

    def need_repo() -> Repository:
        if repo is None:
            raise HTTPException(status_code=503, detail=db_error or "database unavailable")
        return repo

    def page(request: Request, name: str, **context: Any) -> HTMLResponse:
        return templates.TemplateResponse(request, name, {"version": __version__, **context})

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"status": "ok" if repo is not None else "degraded", "database": repo is not None,
                "detail": db_error, "version": __version__}

    @app.get("/api/runs")
    def api_runs(page_no: int = Query(1, alias="page", ge=1),
                 size: int = Query(25, ge=1, le=MAX_PAGE_SIZE)) -> dict[str, Any]:
        runs, total = need_repo().list_runs(page_no, size)
        return {"items": runs, "page": page_no, "size": size, "total": total}

    @app.get("/api/runs/{run_id}")
    def api_run(run_id: str) -> dict[str, Any]:
        run = need_repo().get_run(_uuid(run_id))
        if run is None:
            raise HTTPException(404, "run not found")
        return {"run_id": str(run.id), "side": run.side, "mode": run.mode, "status": run.status,
                "manifest": run.manifest, "started_at": run.started_at.isoformat(),
                "finished_at": run.finished_at.isoformat() if run.finished_at else None}

    @app.get("/api/runs/{run_id}/cases")
    def api_run_cases(run_id: str, page_no: int = Query(1, alias="page", ge=1),
                      size: int = Query(50, ge=1, le=MAX_PAGE_SIZE)) -> dict[str, Any]:
        items, total = need_repo().run_cases(_uuid(run_id), page_no, size)
        return {"items": items, "page": page_no, "size": size, "total": total}

    @app.get("/api/case-attempts/{attempt_id}/trace")
    def api_trace(attempt_id: str) -> dict[str, Any]:
        attempt = need_repo().attempt(_uuid(attempt_id))
        if attempt is None:
            raise HTTPException(404, "attempt not found")
        return attempt

    @app.get("/api/comparisons/{comparison_id}")
    def api_comparison(comparison_id: str) -> dict[str, Any]:
        comp = need_repo().get_comparison(_uuid(comparison_id))
        if comp is None:
            raise HTTPException(404, "comparison not found")
        return {"comparison_id": str(comp.id), "baseline_run_id": str(comp.baseline_run_id),
                "candidate_run_id": str(comp.candidate_run_id), "status": comp.status,
                "gate_sha256": comp.gate_sha256, "report": comp.report}

    @app.get("/api/comparisons/{comparison_id}/report", response_class=PlainTextResponse)
    def api_comparison_report(comparison_id: str) -> PlainTextResponse:
        comp = need_repo().get_comparison(_uuid(comparison_id))
        if comp is None:
            raise HTTPException(404, "comparison not found")
        return PlainTextResponse(comp.report_markdown, media_type="text/markdown; charset=utf-8",
                                 headers={"Content-Disposition": f'attachment; filename="report-{comp.id}.md"'})

    @app.get("/", response_class=HTMLResponse)
    def runs_page(request: Request, page_no: int = Query(1, alias="page", ge=1)) -> HTMLResponse:
        if repo is None:
            return page(request, "error.html", message=db_error)
        runs, total = repo.list_runs(page_no, 25)
        comparisons = repo.list_comparisons(10)
        return page(request, "runs.html", runs=runs, total=total, page_no=page_no,
                    comparisons=comparisons, pages=max(1, -(-total // 25)))

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_page(request: Request, run_id: str, page_no: int = Query(1, alias="page", ge=1)) -> HTMLResponse:
        r = need_repo()
        run = r.get_run(_uuid(run_id))
        if run is None:
            raise HTTPException(404, "run not found")
        cases, total = r.run_cases(str(run.id), page_no, 100)
        return page(request, "run.html", run=run, cases=cases, total=total, page_no=page_no,
                    pages=max(1, -(-total // 100)))

    @app.get("/comparisons/{comparison_id}", response_class=HTMLResponse)
    def comparison_page(request: Request, comparison_id: str) -> HTMLResponse:
        r = need_repo()
        comp = r.get_comparison(_uuid(comparison_id))
        if comp is None:
            raise HTTPException(404, "comparison not found")
        attempts = r.attempt_ids(str(comp.candidate_run_id))
        baseline_attempts = r.attempt_ids(str(comp.baseline_run_id))
        baseline = r.get_run(str(comp.baseline_run_id))
        candidate = r.get_run(str(comp.candidate_run_id))
        return page(request, "comparison.html", comp=comp, report=comp.report,
                    attempts={f"{k[0]}:{k[1]}": v for k, v in attempts.items()},
                    baseline_attempts={f"{k[0]}:{k[1]}": v for k, v in baseline_attempts.items()},
                    baseline=baseline, candidate=candidate)

    @app.get("/attempts/{attempt_id}", response_class=HTMLResponse)
    def trace_page(request: Request, attempt_id: str) -> HTMLResponse:
        attempt = need_repo().attempt(_uuid(attempt_id))
        if attempt is None:
            raise HTTPException(404, "attempt not found")
        cited = set()
        for e in attempt["events"]:
            if e["event_type"] == "assistant_final":
                cited |= set(e["payload"]["response"].get("evidence_event_ids", []))
        return page(request, "trace.html", a=attempt, cited=cited)

    @app.get("/review", response_class=HTMLResponse)
    def review_page(request: Request) -> HTMLResponse:
        return page(request, "review.html", rows=need_repo().review_queue())

    return app
