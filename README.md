# VoyageCrew

VoyageCrew is a FastAPI travel planner backed by a LangGraph workflow. A
supervisor selects travel specialists, MCP integrations retrieve external
information, and the itinerary is paused for human approval or revision before
the final response is produced.

## Architecture

```text
Browser UI
  → FastAPI routes and schemas
  → TravelService
  → LangGraph guardrail and supervisor
  → Selected specialists (fixed order)
  → Draft itinerary
  → PostgreSQL-backed human approval interrupt
  → Final response
```

The application is a modular monolith. It retains the existing frontend and
HTTP contracts; external providers are integrated through MCP.

| Directory or file | Responsibility |
|---|---|
| `main.py` | FastAPI app, static assets, and startup/shutdown lifecycle |
| `tripmate/api/` | Request schemas, dependencies, and page/travel/health routes |
| `tripmate/agents/` | Guardrail, supervisor, and specialist workflow nodes |
| `tripmate/graph/` | Travel state, routing, and graph assembly |
| `tripmate/integrations/mcp/` | Shared MCP configuration and provider adapters |
| `tripmate/services/travel_service.py` | Start/resume workflow and serialize graph results |
| `tripmate/persistence/checkpointer.py` | PostgreSQL LangGraph checkpointer lifecycle |
| `tripmate/core/` | Settings, Groq model construction, logging, and exceptions |
| `mcp_servers/weather_server.py` | Local OpenWeather MCP server |
| `templates/`, `static/` | Existing browser UI |
| `tests/` | Offline API, workflow, and infrastructure regression tests |

`app.py`, `backend.py`, `mcp_client.py`, and `custom_weather_mcp_server.py` are
thin compatibility entry points for the previous root-module layout.

## Requirements

- Python 3.11
- PostgreSQL with a connection URL
- Groq API key
- Tavily API key
- AviationStack MCP API key
- OpenWeather API key
- Docker (optional)

The AviationStack integration launches `aviationstack-mcp` using `uvx`; `uv` is
installed from `requirements.txt`. The local weather MCP server is launched
with the active Python interpreter.

## Local setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Set the following values in `.env`:

```dotenv
DATABASE_URL=postgresql://...
GROQ_API_KEY=...
GROQ_MODEL=openai/gpt-oss-120b
TAVILY_API_KEY=...
AVIATION_STACK_API_KEY=...
OPENWEATHER_API_KEY=...
```

`GROQ_MODEL` selects a model available to the configured Groq account. The
default is `openai/gpt-oss-120b`; set it explicitly when using another model
from Groq's current model catalog. Requests use Groq's standard
`https://api.groq.com` endpoint.

`AVIATIONSTACK_API_KEY` is also accepted as an alias.

### PostgreSQL and Neon

`DATABASE_URL` accepts the standard PostgreSQL URI supplied by Neon. The
application uses Psycopg and LangGraph's `PostgresSaver` with a bounded client
pool (1-5 connections). SSL settings already present in the URI are preserved,
including `sslmode` and `channel_binding`; if `sslmode` is absent, the
application adds `sslmode=require`. Automatic prepared statements are disabled
for compatibility with Neon pooled endpoints.

Use the **direct** Neon endpoint for schema setup, `pg_dump`, and `pg_restore`.
The **pooled** endpoint (hostname containing `-pooler`) is suitable for normal
application traffic and can be copied from the Neon connection details. Both
are PostgreSQL connection URLs and are configured only through
`DATABASE_URL`; never commit the URL or put credentials in source files.

Local PostgreSQL remains available for development. Set a local URL in `.env`
and explicitly disable SSL for a non-TLS local server, for example:

```dotenv
DATABASE_URL=postgresql://<user>:<password>@localhost:5432/tripmate?sslmode=disable
```

#### Preserving historical checkpoints

The application calls `PostgresSaver.setup()` at startup. It creates or
upgrades LangGraph's checkpoint schema without dropping or resetting tables.
If you have checkpoint history in another PostgreSQL database that must be
retained, restore it into a **new, empty Neon database before the first
application startup**. Do not use `pg_restore --clean`, and do not restore
over an initialized database. Use direct connection URLs (not `-pooler`) for
both source and target:

```powershell
$env:SOURCE_DIRECT_DATABASE_URL = "postgresql://..."
$env:NEON_DIRECT_DATABASE_URL = "postgresql://..."
pg_dump --format=custom --no-owner --no-acl `
  --table=public.checkpoints `
  --table=public.checkpoint_blobs `
  --table=public.checkpoint_writes `
  --table=public.checkpoint_migrations `
  --dbname $env:SOURCE_DIRECT_DATABASE_URL `
  --file .\tripmate-checkpoints.dump
pg_restore --list .\tripmate-checkpoints.dump
pg_restore --no-owner --no-acl `
  --dbname $env:NEON_DIRECT_DATABASE_URL `
  .\tripmate-checkpoints.dump
```

Before starting VoyageCrew, compare row counts for the four checkpoint tables on
the source and target with `psql`. Keep the dump until the target data and
application resume behavior are verified. If the target is not empty, stop
and inspect it rather than overwriting existing tables or checkpoint data.
When no historical data needs migration, configure the fresh Neon database
and let startup initialize it.

Start the application:

```powershell
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. The previous `python app.py` and
`uvicorn app:app` entry points remain available for local compatibility.

## API

- `GET /` — browser interface
- `GET /health` — basic application health and feature list
- `GET /favicon.ico` — empty JSON response
- `POST /api/travel` — accepts `{"message": "...", "thread_id": null}` and
  returns the draft or blocked result
- `POST /api/travel/approve` — accepts
  `{"thread_id": "...", "approved": true, "feedback": ""}` to approve or revise
  a paused draft; rejection requires nonblank feedback

The response fields and endpoint paths remain compatible with the existing
frontend in `static/script.js`.

## Workflow and integrations

The input guardrail runs before the supervisor. For allowed requests, the
supervisor selects from flights, hotels, weather, budget, and itinerary agents.
Selected specialists run in the fixed order flight, hotel, weather, budget,
itinerary; the itinerary agent always runs. LangGraph then interrupts at human
approval. Approval or revision resumes the same `thread_id`, and PostgreSQL
checkpoints preserve workflow state.

MCP provider modules use the existing server names and tools:

- Tavily: `tavily_search`
- AviationStack: `list_airports`, `list_airlines`, and other configured tools
- Weather: `get_current_weather`, `get_forecast` from
  `mcp_servers/weather_server.py`

MCP subprocesses receive API keys through their child environment. The
`MultiServerMCPClient` is configured during application startup; provider
sessions are scoped to MCP tool operations. Live provider calls require valid
credentials and network access.

Workflow nodes remain synchronous and use `asyncio.run()` for async MCP calls.
`nest_asyncio` is retained while this execution model is in use; the refactor
does not convert the workflow to async.

The current guardrail intentionally preserves its existing fail-open fallback
when model or JSON parsing fails. Changing that reliability/security behavior
is a separate decision and is not part of this structural refactor.

## Docker

The build context excludes `.env`, `.git`, local environments, caches, and
generated artifacts. Provide secrets at container startup, not during build:

```powershell
docker build -t voyagecrew .
docker run --env-file .env -p 8000:8000 voyagecrew
```

The image starts `main:app` and installs `uv`, which provides `uvx`
for the AviationStack MCP server.

## Tests

Run the regression suite with Python 3.11 and the project dependencies
installed (the application imports FastAPI, LangGraph, and Psycopg):

```powershell
.venv\Scripts\python.exe -m tests.run_tests
```

The runner exits nonzero if discovery finds zero tests. Offline tests use mocks
and cover FastAPI startup/shutdown, health and readiness, checkpointer setup and
cleanup, supervisor routing, request validation and sanitized errors, and
approval/revision resume behavior. No API or live MCP credentials are required.

One real PostgreSQL/Neon integration test initializes the LangGraph schema and
checks database readiness. It runs when `DATABASE_URL` is set in the process
environment or root `.env`; otherwise that single integration test is skipped.
The integration test creates no workflow thread and deletes no records.