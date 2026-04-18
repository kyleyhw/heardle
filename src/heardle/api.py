"""FastAPI application — routes + htmx partials.

Phase 5 implementation target. This stub exposes an ``app`` attribute so ``uvicorn
heardle.api:app`` can at least start the server during Phase 1 scaffolding checks.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="Heardle", version="0.1.0")


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    """Placeholder landing page. Phase 5 replaces this with a Jinja-rendered selector."""
    return (
        "<!doctype html><html><body>"
        "<h1>Heardle</h1>"
        "<p>Scaffolding in place. See PROJECT_PLAN.md for phase status.</p>"
        "</body></html>"
    )
