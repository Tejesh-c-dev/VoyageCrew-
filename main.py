"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from time import perf_counter
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from tripmate.api.routes.health import router as health_router
from tripmate.api.routes.accounts import router as accounts_router
from tripmate.api.routes.pages import router as pages_router
from tripmate.api.routes.travel import router as travel_router
from tripmate.api.exception_handlers import install_exception_handlers
from tripmate.api.request_size import TravelRequestSizeLimitMiddleware
from tripmate.core.config import (
    PROJECT_ROOT,
    configure_ssl_certificates,
    load_settings,
)
from tripmate.core.logging import (
    configure_logging,
    get_logger,
    reset_correlation_id,
    set_correlation_id,
    valid_correlation_id,
)
from tripmate.core.llm import create_llm
from tripmate.graph.builder import build_travel_graph
from tripmate.integrations.mcp.client import create_mcp_integration
from tripmate.persistence.checkpointer import (
    CheckpointerResources,
    create_checkpointer,
)
from tripmate.services.travel_service import TravelService

configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    resources: CheckpointerResources | None = None
    application.state.is_ready = False
    application.state.checkpointer_resources = None
    application.state.travel_service = None
    application.state.settings = None
    try:
        settings = load_settings(PROJECT_ROOT)
        application.state.settings = settings
        configure_ssl_certificates()
        resources = create_checkpointer(settings)
        integration = create_mcp_integration(settings)
        llm = create_llm(settings)
        travel_graph = build_travel_graph(
            resources.saver,
            llm,
            integration,
        )
        application.state.travel_service = TravelService(travel_graph)
        application.state.checkpointer_resources = resources
        application.state.is_ready = True
        yield
    finally:
        application.state.is_ready = False
        application.state.travel_service = None
        application.state.checkpointer_resources = None
        application.state.settings = None
        if resources is not None:
            resources.close()


app = FastAPI(
    title="VoyageCrew",
    description=(
        "LangGraph Multi-Agent Travel Planner with Supervisor, Guardrails, "
        "Human-in-the-Loop, and FastAPI Frontend"
    ),
    version="2.0.0",
    lifespan=lifespan,
)
install_exception_handlers(app)
app.add_middleware(TravelRequestSizeLimitMiddleware)


@app.middleware("http")
async def request_observability(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        fetch_site = request.headers.get("sec-fetch-site", "").lower()
        origin = request.headers.get("origin")
        if fetch_site == "cross-site" or (origin and origin.rstrip("/") != str(request.base_url).rstrip("/")):
            return JSONResponse(status_code=403, content={"detail": "Cross-site requests are not allowed."})
    correlation_id = valid_correlation_id(
        request.headers.get("X-Request-ID")
    ) or str(uuid.uuid4())
    request.state.correlation_id = correlation_id
    token = set_correlation_id(correlation_id)
    started_at = perf_counter()
    try:
        response = await call_next(request)
    except Exception as exception:
        logger.error(
            "HTTP request failed.",
            extra={
                "event": "request.failed",
                "method": request.method,
                "path": request.url.path,
                "status_code": 500,
                "duration_ms": round(
                    (perf_counter() - started_at) * 1000,
                    2,
                ),
                "error_type": type(exception).__name__,
                "outcome": "failure",
            },
        )
        raise
    else:
        response.headers["X-Request-ID"] = correlation_id
        logger.info(
            "HTTP request completed.",
            extra={
                "event": "request.completed",
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round(
                    (perf_counter() - started_at) * 1000,
                    2,
                ),
                "outcome": (
                    "success"
                    if response.status_code < 400
                    else "failure"
                ),
            },
        )
        return response
    finally:
        reset_correlation_id(token)


frontend_assets = PROJECT_ROOT / "frontend" / "dist" / "assets"
if frontend_assets.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=str(frontend_assets)),
        name="frontend-assets",
    )
frontend_images = PROJECT_ROOT / "frontend" / "dist" / "images"
if frontend_images.is_dir():
    app.mount(
        "/images",
        StaticFiles(directory=str(frontend_images)),
        name="frontend-images",
    )
app.include_router(pages_router)
app.include_router(accounts_router)
app.include_router(travel_router)
app.include_router(health_router)


@app.get("/{spa_path:path}")
async def spa_fallback(spa_path: str):
    """Serve the React entry point after API and health routes."""
    if (
        spa_path == "api"
        or spa_path.startswith(("api/", "health"))
        or "." in spa_path.rsplit("/", 1)[-1]
    ):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    index = PROJECT_ROOT / "frontend" / "dist" / "index.html"
    if index.is_file():
        return FileResponse(index, media_type="text/html")
    return JSONResponse(status_code=404, content={"detail": "Not Found"})
