"""
Minimal static server for Playwright XSS tests.

Serves:
  GET /        → templates/index.html with dummy Clerk placeholders
  GET /static/ → static/ directory (script.js, style.css, …)
  GET /_test/marked.min.js    → node_modules/marked/marked.min.js (local)
  GET /_test/purify.min.js    → node_modules/dompurify/dist/purify.min.js (local)

The Playwright test intercepts CDN requests for marked and DOMPurify and
redirects them to these local endpoints so the tests work without internet.

No database, no auth, no LangGraph required.

Usage:
    python tests/playwright/serve_static.py
"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import uvicorn
from fastapi import FastAPI
from fastapi.requests import Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI(title="playwright-static-server")
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))

NODE_MODULES = PROJECT_ROOT / "node_modules"
MARKED_JS = NODE_MODULES / "marked" / "marked.min.js"
PURIFY_JS = NODE_MODULES / "dompurify" / "dist" / "purify.min.js"


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            # Clerk JS will fail to auth but CDN libs + script.js still load.
            "clerk_publishable_key": "pk_test_dummy",
            "clerk_frontend_api_url": "https://clerk.invalid",
        },
    )


@app.get("/_test/marked.min.js")
async def local_marked():
    """Serve locally installed marked@4.3.0 to avoid external CDN dependency."""
    return FileResponse(str(MARKED_JS), media_type="application/javascript")


@app.get("/_test/purify.min.js")
async def local_purify():
    """Serve locally installed DOMPurify@3.0.5 to avoid external CDN dependency."""
    return FileResponse(str(PURIFY_JS), media_type="application/javascript")


app.mount(
    "/static",
    StaticFiles(directory=str(PROJECT_ROOT / "static")),
    name="static",
)

if __name__ == "__main__":
    port = int(os.environ.get("PLAYWRIGHT_PORT", "8123"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
