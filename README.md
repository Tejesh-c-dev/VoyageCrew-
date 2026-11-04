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

The application is a modular monolith. FastAPI serves a React frontend while
preserving its HTTP contracts; external providers are integrated through MCP.

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
| `frontend/` | React and TypeScript browser interface |
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

The application uses one bounded pool for runtime queries, ownership checks,
and startup checkpoint schema setup. Keep the Neon pooled endpoint in
`DATABASE_URL` for runtime. Use the **direct** endpoint for `pg_dump` and
`pg_restore`, which are separate administrative operations. There is no
separate migration URL in the application; use a direct endpoint for another
operation only if that operation is shown to require it. Never commit a
connection URL or put credentials in source files.

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

The response fields and endpoint paths remain compatible with the React
frontend.

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

### Production readiness configuration notes

`load_settings()` reads `.env` without overriding variables already present in
the process environment, so the process environment takes precedence. The
application consumes `DATABASE_URL`, `GROQ_API_KEY`, `GROQ_MODEL`,
`TAVILY_API_KEY`, `AVIATION_STACK_API_KEY` (also accepts
`AVIATIONSTACK_API_KEY`), `OPENWEATHER_API_KEY`, and the Clerk issuer, audience,
publishable key, and frontend API URL. Provider credentials are needed for the
corresponding live integrations; Clerk configuration is required at startup.

In the local configuration reviewed for the 2026-11-04 readiness audit, the
application settings above were present. `TRIPMATE_TEST_DATABASE_URL` was not
set, so PostgreSQL integration tests were not run against a disposable test
database. `MIGRATION_DATABASE_URL` is not read by the application. No database
values are recorded here. The configured Neon runtime URL is pooled, and a
read-only connectivity check succeeded; schema-writing startup, checkpoint
resume, and ownership migration were not re-run against the runtime database.

The application does not implement deployment-wide rate limiting. Configure
request limits at the ingress or hosting layer before exposing the API to
untrusted traffic; an in-process limit would not cover multiple workers.

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

PostgreSQL integration tests cover database readiness, checkpoint schema setup, and thread ownership/migration behavior. They require a disposable local PostgreSQL database configured through `TRIPMATE_TEST_DATABASE_URL`; tests are skipped when that database is unavailable. The release verification on 2026-11-03 ran these tests successfully against a disposable database.

## React frontend

The production React frontend is built from `frontend/` and served by FastAPI. For local UI development, run:

```powershell
npm install --prefix frontend
npm run dev --prefix frontend
```

The Vite development server proxies `/api` to FastAPI. Create the production bundle with `npm run build --prefix frontend`. Docker builds the bundle in its Node stage and packages it with the API. Clerk's publishable key is returned at runtime by `/api/config`; no Clerk key is embedded in the frontend build.

### Release verification

- `npm run build --prefix frontend` — passed. The largest initial JavaScript chunk is 428.31 kB, below Vite's 500 kB warning threshold. PDF export code is loaded only when requested.
- `npm test --prefix frontend` — passed (2 tests).
- `npm run test:playwright` — passed (17 tests) against the production React bundle served through FastAPI. Playwright mocks Clerk and planner API calls; Python tests cover FastAPI route and ownership behavior.
- `python tests/run_tests.py` - 250 discovered and passed with no skips. Output includes a Pydantic `IncompleteFieldDefinitionWarning` from a dependency's unresolved `lifespan` annotation; the warning remains visible for investigation.
- Disposable PostgreSQL migration and ownership checks passed, including cross-user isolation. The production Docker image built and its container passed health/readiness, React asset, and API smoke checks. See the migration report for exact results.

See [the React migration and release readiness report](docs/REACT_MIGRATION_PROGRESS.md) for scope and details.
