# VoyageCrew Refactoring Progress

This document tracks the staged modularization requested for VoyageCrew.
Statuses reflect inspected or executed evidence only; no stage is marked PASS
without supporting evidence.

## Stage status

| Stage | Status | Evidence |
|---|---|---|
| 0 — Repository audit | PASS | Audited all four root Python modules, the frontend request/response flow, Docker configuration, dependencies, README, and tracked-file inventory. Migration map and risks are below. No application code was changed. |
| 1 — Establish baseline | PARTIAL | Added an initial 10-test offline `unittest` suite and later expanded it to 16 passing tests. The initial import required `GROQ_API_KEY`; after lifecycle refactoring, module import succeeds without credentials. `.env` and local `uvx` are absent, so live PostgreSQL/MCP startup could not be baselined. |
| 2 — Create package structure | PASS | Added package initializers and verified all package namespaces import; compileall and the 10 baseline regressions pass. |
| 3 — Extract core and infrastructure | PASS | Centralized settings, Groq construction, logging/exception helpers, and PostgreSQL resource management. Removed database connection and graph compilation from module import and wired FastAPI startup/shutdown. `app` imports without credentials; all 12 offline regressions pass, including resource closure. |
| 4 — Extract integrations | PASS | Added provider MCP modules and shared client configuration, retained the existing `mcp_client.py` function facade during graph extraction, relocated the weather server with a compatibility entry point, and verified its stdio initialize handshake. The 14 offline regressions pass; credentialed provider calls remain unavailable. |
| 5 — Extract agents and graph | PASS | Extracted all agent nodes, shared state, routing, and graph assembly. Graph dependencies are injected; the 15 offline regressions pass, including full in-memory draft, interrupt, revision, and final-response execution. Pylance reports no workspace diagnostics or unresolved imports. |
| 6 — Extract services and API | PASS | Added `TravelService`, schemas, dependencies, and page/travel/health routers; `main:app` now owns startup/shutdown. Kept root `app.py` and `backend.py` compatibility facades. API, homepage, favicon, health, lifecycle, graph, and service regressions pass (16 tests); workspace diagnostics and import analysis are clean. |
| 7 — Verification | PARTIAL | `compileall`, all 16 offline tests, Pylance workspace diagnostics/import analysis, app compatibility import, and the relocated weather-server stdio initialize handshake pass. Live PostgreSQL/provider calls remain unverified without credentials. `docker build` was attempted but could not connect to the Docker Desktop Linux Engine named pipe. |
| 8 — Documentation | PASS | Rewrote README for the modular architecture, setup, environment, startup, Docker, tests, workflow, and MCP; added `.env.example` without credentials. |
| Phase 1.1 — Non-blocking graph execution | PASS | Both travel endpoints dispatch synchronous service calls with Starlette `run_in_threadpool`. The 20-test offline suite and `compileall` pass. Live PostgreSQL and external MCP calls remain unavailable for validation. |
| Phase 1.2 — Application lifecycle management | PASS | Added failure-safe, repeatable lifespan cleanup and five lifecycle tests. All 25 offline tests, `compileall`, and application import checks pass. Live PostgreSQL and credentialed MCP calls remain unavailable. |
| Phase 1.3 — Health and readiness checks | PASS | Added liveness and PostgreSQL/checkpointer readiness routes while preserving `/health`. Startup/shutdown state, bounded probes, sanitized 503 responses, 33 offline tests, `compileall`, and application imports pass. Live PostgreSQL integration remains unverified. |
| Phase 1.4 — Centralized exception handling | PASS | Added application-wide sanitized API errors, application/domain failure types, database/MCP/Groq error mapping, and safe server-side trace logging. All 43 offline tests, `compileall`, and imports pass; live provider/database failures were simulated, not exercised against external services. |
| Phase 1.5 — External integration reliability | PASS | Configured bounded Groq/MCP/Weather timeouts and retries; rate limits honor bounded `Retry-After`, permanent errors are not retried, and exhausted MCP failures reach centralized handling. All 59 offline tests, `compileall`, application imports, `git diff --check`, and a whitespace scan pass. External credentials/services were not used. |
| Phase 1.6 — Logging and observability | PASS | Added allowlisted JSON logs, UUID request correlation, workflow/agent/LLM/integration timing and failure events, retry outcome logs, and invocation-based cumulative LLM counts. All 67 offline tests, `compileall`, application/compatibility imports, and `git diff --check` pass. Credentialed external calls were not made. |
| Phase 1.7 — Reliability and failure-injection testing | PASS WITH LIMITATIONS | Added deterministic workflow, concurrency, lifecycle-recovery, correlation, LLM-usage, readiness, and external retry/timeout tests. Fixed normalized/generated request-ID mismatch and preserved correlation IDs on sanitized error responses and logs. 75 offline tests pass; live PostgreSQL/provider integration remains unavailable because `DATABASE_URL` is not configured. |
| Neon PostgreSQL persistence | PASS WITH DATA-MIGRATION LIMITATION | Replaced the single connection with a bounded Psycopg pool and verified the configured Neon endpoint, fresh checkpoint schema setup, durable interrupt/resume on the same thread, four concurrent independent workflows, readiness, and application startup/shutdown. The Neon integration tests pass; no separate legacy PostgreSQL source URL was supplied, so historical data on another database cannot be assessed or migrated. No tables were dropped, overwritten, or reset. |

## Neon PostgreSQL persistence

- `Settings.require_database_url()` accepts standard PostgreSQL URIs, adds
  `sslmode=require` only when needed, preserves supplied SSL and
  `channel_binding` options, and normalizes duplicate `sslmode` entries.
- Startup creates a Psycopg pool bounded to 1-5 connections and at most 10
  queued waiters, then initializes the existing LangGraph `PostgresSaver`.
  Connection setup and application shutdown close the pool, including startup
  failure paths. Prepared statements are disabled for Neon pooled-endpoint
  compatibility.
- A read-only connection to the configured Neon endpoint succeeded. Before
  setup, it contained no LangGraph checkpoint tables. `PostgresSaver.setup()`
  initialized the schema without dropping or resetting tables.
- Five live Neon integration tests passed: schema/readiness, checkpoint
  creation and retrieval, interrupt/resume on the same thread ID, four
  concurrent independent workflows, and real application startup/shutdown.
  The tests clean up only their uniquely named checkpoint records; no existing
  records were deleted.
- The complete offline regression suite passed: **84 tests**, with the five
  live integration tests skipped when `DATABASE_URL` is not exported to that
  test process. A separate run with the configured Neon URL passed all nine
  database URL and live integration tests.
- No legacy/local PostgreSQL source connection was configured, so whether a
  separate source database contains historical checkpoints cannot be
  determined. No dump or restore was attempted. README documents a
  non-destructive direct-connection `pg_dump`/`pg_restore` process for retaining
  such history in a new, empty Neon database.

## Current architecture

- `main.py` creates settings, the checkpointer, shared LLM, MCP
  integration, graph, and service during FastAPI startup; failures and shutdown
  clear the service and close an initialized PostgreSQL connection pool.
- API routers delegate to `TravelService`; agent implementations and graph
  routing are in separate modules under `tripmate/agents/` and
  `tripmate/graph/`.
- The async travel routes run synchronous graph start/resume calls in Starlette
  worker threads so graph execution does not block FastAPI's event loop.
- Application logs use a stdlib JSON formatter with an explicit field
  allowlist; request UUIDs are returned in `X-Request-ID` and propagated as
  LangGraph metadata. Workflow logs include hashed thread IDs rather than
  logging caller-provided thread IDs directly.
- Request, workflow, agent, LLM invocation, MCP operation, and Weather HTTP
  events include duration and safe outcome metadata. Retry logs record attempt,
  integration, operation, and status without logging request bodies, prompts,
  credentials, exception text, or provider responses.
- `/health/live` reports process liveness independently of dependency state;
  `/health/ready` requires completed startup and a successful bounded
  PostgreSQL checkpointer probe. Optional MCP providers are not probed.
- Provider-specific MCP wrappers live under `tripmate/integrations/mcp/`;
  `MultiServerMCPClient` creates and closes a scoped session per tool operation.
- External integrations use bounded request timeouts; retries are limited to
  transient failures from idempotent lookups, with provider `Retry-After`
  respected within a finite wait budget. Exhausted MCP failures are raised to
  the centralized exception handlers.
- Templates and static files remain at the repository root. Root app/backend/
  MCP modules remain small compatibility facades.

### Phase 1.7 — Reliability and failure-injection testing

- Audited the existing Phase 1 tests: they already covered API contracts,
  in-memory approval and revision, provider retry limits, sanitized errors,
  readiness behavior, and resource cleanup. Added coverage for generated
  request-ID propagation, correlation on failed responses/logs, simultaneous
  workflow tracker isolation, transient/exhausted Groq SDK retries, transient
  MCP timeouts, probe-lock release after a database error, and startup recovery
  after a failed initialization.
- Fixed generated IDs to use the canonical UUID form already used by the
  correlation context. Error responses now include `X-Request-ID`, and
  exception-handler logs restore the request correlation context after
  middleware cleanup; existing error bodies and status codes remain unchanged.
- Reworked event-loop and readiness timeout checks to synchronize on events
  rather than asserting elapsed wall-clock thresholds. Timer bounds are used
  only as deadlock safeguards.
- Verification: **75 offline unittest tests pass**; Python compilation,
  application/compatibility import checks, and `git diff --check` pass. Groq
  and MCP behavior is exercised with local mock transports/tools, not live
  providers. `DATABASE_URL` is not configured, so PostgreSQL persistence and
  failure behavior against a real server remain unverified.

### Phase 1.5 — External integration reliability

- Configured Groq with a 5-second connect timeout, 30-second request timeout,
  and two SDK-managed retries. The Groq SDK uses exponential backoff with
  jitter, honors supported `Retry-After` values, and does not retry
  authentication or validation responses.
- Configured Tavily Streamable HTTP with 5-second connect and 20-second
  response/session read timeouts. Configured stdio MCP sessions with a
  20-second response timeout while preserving the adapter's per-operation
  session lifecycle.
- Added at most two retries with exponential jitter for transient failures on
  the read-only Tavily search and AviationStack lookup tools. Permanent HTTP
  responses and unapproved operations are not retried. MCP tool errors are now
  raised instead of returned as success-shaped tool messages, then propagate
  as sanitized `ExternalServiceError` responses after retries are exhausted.
- Configured OpenWeather GETs with separate 5-second connect and 20-second
  response timeouts and at most two transient retries. Retryable rate limits
  respect numeric or HTTP-date `Retry-After` values; excessive requested waits
  stop retries rather than exceed the delay budget. Authentication, validation,
  TLS, and permanent HTTP failures are not retried.
- Added 16 mocked tests covering timeouts, transient failures, retry limits,
  rate-limit delays, permanent failures, MCP propagation, Groq settings, and
  transport configuration. Full offline suite: **59 tests passed**.
- Validation: `python -m compileall -q tripmate mcp_servers tests`,
  application/compatibility imports, `git diff --check`, and a whitespace
  scan of changed Python files passed. Live external calls were not made and
  no credentials were required.

### Phase 1.6 — Logging and observability

- Centralized application output as JSON using the standard-library logging
  package and a fixed allowlist of structured fields. Log message text,
  exception tracebacks, and unapproved fields are excluded from formatted
  output; application failure logs include only safe error type names.
- Added validated UUID request correlation via `X-Request-ID`, returned the ID
  in the response header, and propagated it through request context and
  LangGraph metadata. Workflow/agent events use a stable hash of the thread ID.
- Added request completion, workflow and agent duration/failure, and per-LLM
  invocation outcome/duration events. MCP and OpenWeather operations record
  attempt latency and retry outcomes without logging inputs or results.
- Replaced workflow count estimates with a request-scoped counter incremented
  around each actual model `.invoke()`. Node state preserves the cumulative
  total across approval/resume; search-only hotel work does not increment it,
  and weather destination extraction is counted.
- Added regression tests for request/workflow correlation, HTTP completion,
  model counts across resume and model failure, hotel/weather counting,
  retry/latency records, and sensitive-field/message redaction.
- Validation: **67 tests passed** with
  `python -m unittest discover -s tests -v`;
  `python -m compileall -q tripmate mcp_servers tests`, application and
  compatibility imports (`app`, `backend`, `mcp_client`, `main`,
  logging/observability modules, and the weather server), and `git diff
  --check` passed. Tests ran in the isolated project `.venv` using pinned
  requirements because the global interpreter lacked required packages.
  Live PostgreSQL and provider calls were not made.

## Stage-by-stage report

### Stage 0 — Repository audit

- Created this progress document and recorded the source-based migration map.
- Inspected all original Python files, API/frontend contracts, dependency
  imports, Docker commands, and environment/checkpointer/MCP initialization.
- Tests: none; audit only. No application code was changed.
- Remaining: baseline and staged extraction.

### Stage 1 — Establish baseline

- Added `tests/test_baseline.py` with the initial API, routing, serialization,
  guardrail, and approval/resume checks. The suite now contains 16 checks.
- `compileall` succeeded. The original app import failed because
  `GROQ_API_KEY` was absent; the initial 10 offline tests passed after mocking
  database setup.
- Remaining: real database and external-service startup were not available.

### Stage 2 — Create package structure

- Added initializers for `tripmate/` API, agents, graph, integrations, services,
  persistence, core, and `mcp_servers/`.
- Package namespace imports and the 10-test suite passed.
- No application modules were moved in this stage.

### Stage 3 — Extract core and infrastructure

- Created `tripmate/core/config.py`, `llm.py`, `logging.py`,
  `exceptions.py`, and `tripmate/persistence/checkpointer.py`.
- Modified `app.py`, `backend.py`, and `mcp_client.py` to centralize
  configuration/LLM creation and move PostgreSQL setup into FastAPI lifespan.
  Graph compilation no longer occurs during module import; shutdown closes the
  database connection.
- Tests: 12 passed, including mocked checkpointer setup/closure. App import
  succeeded without database or API credentials.
- Remaining MCP ownership was handled in Stage 4.

### Stage 4 — Extract integrations

- Created the shared MCP client and Tavily, AviationStack, and weather provider
  modules under `tripmate/integrations/mcp/`.
- Moved the weather server to `mcp_servers/weather_server.py`; retained
  `custom_weather_mcp_server.py` and `mcp_client.py` as compatibility facades.
  Updated the local server subprocess path.
- Tests: 14 passed. The relocated weather MCP process returned an initialize
  response over stdio. Live provider calls remain unverified.

### Stage 5 — Extract agents and graph

- Created shared agent helpers and separate guardrail, supervisor, flight,
  hotel, weather, budget, itinerary, approval, and final agent modules.
- Created `graph/state.py`, `routing.py`, and `builder.py`. Reduced the former
  monolithic `backend.py` to a compatibility facade; model and MCP instances
  are injected into graph nodes.
- Tests: 15 passed, including an in-memory draft/approval/revision/final run.
  Pylance diagnostics and import analysis were clean.

### Stage 6 — Extract services and API

- Created `TravelService`, travel schemas, API dependencies, page/travel/health
  routers, and `main.py`.
- Retained root `app.py` and `backend.py` compatibility entry points.
  Updated the Docker Uvicorn module, Docker exclusions, `uv` dependency, and
  `.env.example`.
- Tests: 16 passed, including homepage, favicon, health, request validation,
  API response shape, and lifespan resource closure. `app` and
  `main:app` resolves to the application.

### Stage 7 — Verification

- Passed: `python -m compileall`, all 16 offline tests, Pylance workspace
  diagnostics/import analysis, app compatibility import, ChatGroq construction
  with the original model name, and the relocated weather MCP stdio handshake.
- Blocked: live PostgreSQL and credentialed provider checks due missing `.env`
  and API keys. Docker build was attempted but the Docker Desktop Linux Engine
  named pipe was unavailable, so the image was not built or started.
- Status remains PARTIAL; these unavailable checks are not claimed as passed.

### Stage 8 — Documentation

- Rewrote `README.md` for the package layout, environment, local startup,
  workflow, MCP providers, Docker, and test commands.
- Added `.env.example` with empty values only.
- Validation: documentation inspected; no documentation test runner exists.

### Phase 1.1 — Non-blocking LangGraph execution

- Updated `POST /api/travel` and `POST /api/travel/approve` to await
  `starlette.concurrency.run_in_threadpool` around the existing synchronous
  `TravelService.start` and `TravelService.resume` methods. Service behavior,
  graph invocation, request/response fields, and thread IDs are unchanged.
- Audited `asyncio.run()` use in the flight, hotel, and weather graph nodes.
  These synchronous nodes still invoke async MCP helpers; running graph
  execution in a worker thread gives those helpers a thread with no running
  event loop. Removed `nest_asyncio` from application startup and
  `requirements.txt`; the node-level `asyncio.run()` calls remain.
- Kept PostgreSQL `PostgresSaver` configuration and lifecycle unchanged.
  Existing offline checkpointer setup/closure tests pass, and the new in-memory
  graph test verifies checkpointed approval/resumption plus an async MCP-style
  call from a synchronous node.
- Added four concurrency tests covering event-loop responsiveness for both
  endpoints, parallel initial requests, same-thread approval/revision, MCP
  event-loop bridging, and existing HTTP 500 error shapes.
- Validation: `python -m unittest discover -s tests -v` passed all 20 tests;
  `python -m compileall -q tripmate tests` succeeded. These commands ran with
  the repository `.venv`. Live PostgreSQL checkpointing and credentialed MCP
  providers were not exercised, so those runtime integrations remain
  unverified.

### Phase 1.2 — Application lifecycle management

- Kept PostgreSQL connection and `PostgresSaver.setup()` initialization in the
  FastAPI lifespan. The lifespan now clears stale service state before startup,
  closes any successfully created checkpointer when later initialization
  fails, and clears the service before closing the connection on shutdown.
- Kept one MCP integration, LLM, compiled graph, and `TravelService` per
  lifespan execution; routes continue to retrieve the service through
  `get_travel_service`. A missing service now raises an explicit initialization
  error rather than returning an invalid dependency.
- Verified installed `langchain-mcp-adapters==0.3.0`: `get_tools()` documents
  and implements a new scoped session for each tool invocation, so the client
  does not retain a session requiring application-level close. Verified
  `psycopg.threadsafety == 2`; `PostgresSaver._cursor()` also guards its shared
  connection with its lock while creating per-operation cursors.
- Added lifecycle coverage for schema setup during startup, cleanup after
  startup failure, checkpointer setup failure, repeated lifespan execution,
  and a travel route resolving the initialized service through FastAPI
  dependency injection. Existing workflow/approval/resumption regression tests
  remain unchanged and pass.
- Validation with the repository `.venv`: `python -m unittest discover -s
  tests -v` passed **25 tests**; `python -m compileall -q tripmate tests` and
  imports of `app`, `main`, `tripmate.api.dependencies`, and
  `tripmate.persistence.checkpointer` succeeded. The existing suite's
  intentional route-failure tests log their expected exceptions while passing.
  No live PostgreSQL server or external credentialed MCP provider was
  available, so those integrations were not exercised.

### Phase 1.3 — Health and readiness checks

- Added `GET /health/live` for process liveness and `GET /health/ready` for
  readiness. The existing `GET /health` payload remains unchanged.
- Readiness stays false until application startup has initialized the service
  and checkpointer, and is cleared before shutdown cleanup. It probes the
  existing PostgreSQL checkpointer connection under the saver lock, with a
  transaction-local 1-second PostgreSQL statement timeout and a 1.5-second
  async request timeout. Optional MCP providers are not readiness dependencies.
- Dependency failures and timeouts return HTTP 503 with only
  `{"status": "not_ready"}`; internal exception details and credentials are
  not included in the response.
- Added route and checkpointer tests for liveness, ready/unready states,
  failure sanitization, the bounded SQL probe, and saver-lock contention.
- Validation with the repository `.venv`: `python -m unittest discover -s
  tests -v` passed **33 tests**; `python -m compileall -q tripmate tests` and
  imports of `app`, `main`, `tripmate.api.dependencies`,
  `tripmate.api.routes.health`, and `tripmate.persistence.checkpointer`
  succeeded. Live PostgreSQL connectivity remains unverified because no
  database configuration/server was available.

### Phase 1.4 — Centralized exception handling

- Installed application-wide FastAPI handlers with a consistent
  `{"success": false, "error": "..."}` response. Existing 400/422 validation
  statuses and successful route/health response contracts are preserved;
  validation details and arbitrary HTTP exception details are not echoed.
- Added application errors for invalid requests, configuration/service
  unavailability, database unavailability, and external provider failures.
  PostgreSQL errors map to HTTP 503; MCP and Groq API failures map to HTTP 502;
  unexpected exceptions map to a generic HTTP 500 response.
- Central handlers log full exception tracebacks server-side while returning
  only safe messages. MCP adapters rethrow sanitized application errors with
  original causes; agent-level broad provider fallbacks were removed. The
  established guardrail/supervisor parse and runtime fallbacks remain, are
  narrowed to expected `RuntimeError`/`ValueError`, and log server-side.
- Validation with the repository `.venv`: `python -m unittest discover -s
  tests -v` passed **43 tests**; `python -m compileall -q tripmate tests` and
  imports of `app`, `main`, `tripmate.api.dependencies`,
  `tripmate.api.exception_handlers`, `tripmate.core.exceptions`,
  `tripmate.integrations.mcp.client`, and
  `tripmate.persistence.checkpointer` succeeded. PostgreSQL and provider
  failures were simulated in tests; no live external services were available.

## Stage 0 repository audit (snapshot)

The following inventory records the pre-refactor repository. It is historical
evidence, not a description of the current module layout.

### Pre-refactor source layout

The tracked application had four Python modules:

- `app.py` owns FastAPI construction, Pydantic request models, static/template
  setup, page and API routes, and `nest_asyncio.apply()`.
- `backend.py` owns `TravelState`, shared LLM helpers, supervisor and guardrail
  behavior, all specialist and approval nodes, routing, graph construction,
  PostgreSQL checkpointer setup, thread start/resume, and response
  serialization.
- `mcp_client.py` owns environment loading, Groq LLM construction, the shared
  `MultiServerMCPClient`, tool lookup, provider calls, and destination
  extraction.
- `custom_weather_mcp_server.py` owns the OpenWeather-backed MCP server and its
  two tools.

`templates/` and `static/` contain the existing frontend and should remain at
the repository root unless a later implementation proves relocation necessary.
At the start of Stage 0 there was no tracked `tests/` or `docs/` directory, no
test-runner configuration, and no `.env.example`. The baseline suite added in
Stage 1 uses the standard-library `unittest` runner.

### Pre-refactor dependency and workflow map

```text
app.py
  └── backend.py
        ├── mcp_client.py
        │     ├── Groq ChatGroq (destination extraction)
        │     ├── Tavily MCP (streamable HTTP)
        │     ├── AviationStack MCP (stdio via uvx)
        │     └── local weather MCP (stdio via sys.executable)
        ├── Groq ChatGroq (supervisor and specialist prompts)
        ├── LangGraph StateGraph and interrupt/resume
        └── psycopg + PostgresSaver
```

The original graph started at the supervisor. That node first ran the guardrail and then
specialist selection/constraint extraction. Selected specialists run in the
fixed order flight, hotel, weather, budget, itinerary; itinerary always runs
and then pauses at human approval before final response generation. A blocked
request ends at the blocked-response node. Existing public workflow helpers are
`run_travel_agent` and `resume_travel_agent`.

### API and frontend contracts preserved

- `GET /` serves `templates/index.html`; `/static` serves the root `static/`
  directory; `GET /favicon.ico` returns an empty JSON object.
- `GET /health` returns `status`, `message`, and the existing `features` list.
- `POST /api/travel` accepts `message` and optional `thread_id`. A blank
  trimmed message returns HTTP 400; success includes `success: true` and the
  serialized workflow result.
- `POST /api/travel/approve` accepts `thread_id`, `approved`, and optional
  `feedback`. Rejection without nonblank feedback returns HTTP 400; otherwise
  it resumes the same checkpointed thread.
- The frontend expects the existing result fields, notably `answer`,
  `thread_id`, `requires_approval`, `itinerary`, `approval_request`,
  `selected_agents`, `supervisor_reasoning`, and `guardrail_allowed`. It stores
  the thread ID in local storage and sends it back on approval/revision.

### Pre-refactor lifecycle, async behavior, and configuration findings

- Importing `backend.py` immediately requires `DATABASE_URL`, opens a psycopg
  connection, calls `PostgresSaver.setup()`, compiles the graph, and retains
  the connection globally. No application shutdown closes it.
- Importing `mcp_client.py` loads `.env` relative to that module, configures
  certificate environment variables, requires `GROQ_API_KEY`, constructs a
  second `ChatGroq`, and creates the global MCP client. `backend.py` also
  constructs its own `ChatGroq`; this is the duplicated initialization to
  centralize.
- The graph nodes are synchronous. Flight, hotel, and weather nodes call async
  MCP helpers through `asyncio.run()`. `app.py` applies `nest_asyncio` to
  support those calls from the async FastAPI context. Preserve this execution
  model until a tested replacement is established.
- Weather MCP is launched using `sys.executable` and a path derived from
  `mcp_client.py`; relocating the server requires updating and testing that
  path. AviationStack requires `uvx`, but the Dockerfile does not install it.
- Guardrail/model parsing errors currently fail open and allow the request;
  supervisor parsing errors select all specialists. These are existing
  behaviors to preserve during extraction, not to change as part of this
  refactor.
- Environment names in use include `DATABASE_URL`, `GROQ_API_KEY`,
  `TAVILY_API_KEY`, `AVIATION_STACK_API_KEY` (with the alias
  `AVIATIONSTACK_API_KEY`), and `OPENWEATHER_API_KEY`.

### Pre-refactor deployment and baseline findings

- The original Dockerfile installed `requirements.txt`, copied the repository, and started
  `uvicorn app:app`. The future package entry point must be reflected in the
  Docker command and documented startup instructions.
- The project venv selected by Pylance is Python 3.10.4, while the original
  Dockerfile used Python 3.11. The README now documents Python 3.11; only the
  Python 3.10.4 environment was available for local tests.
- The original `.dockerignore` excluded only virtual-environment directories. It
  does not exclude `.env`, `.git`, Python caches, or generated artifacts.
- README setup still describes the root-module launch commands and says tests
  are not included. No test configuration or explicit test command was found
  during the audit.
- `python -m compileall` succeeds for the four existing Python source files.
  Importing `app` fails during `mcp_client` initialization because
  `GROQ_API_KEY` is not configured. A focused import with fake credentials and
  mocked database setup succeeds; an in-memory LangGraph check confirms the
  current approval node pauses and resumes with revision feedback.
- The project venv reports Python 3.10.4; required application packages resolve
  in that interpreter. Pylance import analysis found no unresolved project
  imports. Docker CLI is present, but `uvx` is not. No live database, external
  MCP, credentialed app-startup, or Docker build checks were run.

### Stage 1 regression checks

`tests/test_baseline.py` currently contains 16 offline checks for HTTP request validation
and response fields, supervisor agent ordering and guardrail fail-open behavior,
fixed-order routing, interrupted-result serialization, and approval/revision
pause/resume. Run with:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Latest result: **16 tests passed** after the core, lifecycle, integration, graph/agent, service, and API changes. `python -m compileall` succeeds. The relocated weather MCP server started and returned an initialize response over stdio. Pylance reports no workspace diagnostics or unresolved imports. Pylance syntax validation also reported no syntax
errors for the test module. These checks do not replace unavailable live
PostgreSQL, MCP, API-key, and Docker startup validation.

The Docker build command was attempted with
`docker build --progress=plain -t voyagecrew:refactor .`; it stopped before
building because the Docker Desktop Linux Engine named pipe was unavailable.

## Stage 0 migration map (implemented)

Keep the frontend directories at the root and use the root entry point
`main:app`. Extract responsibilities as follows, preserving existing
prompts, model settings, node names, routing order, payload shapes, and
environment-variable names:

| Current responsibility | Planned destination |
|---|---|
| FastAPI creation and lifespan | `main.py` |
| Request/approval models | `tripmate/api/schemas/travel.py` |
| Homepage and favicon | `tripmate/api/routes/pages.py` |
| Travel and approval endpoints | `tripmate/api/routes/travel.py` |
| Health endpoint | `tripmate/api/routes/health.py` |
| Initialized service access | `tripmate/api/dependencies.py` |
| Settings, environment/path validation | `tripmate/core/config.py` |
| Shared Groq `ChatGroq` instance | `tripmate/core/llm.py` |
| Logging and application exceptions | `tripmate/core/logging.py`, `tripmate/core/exceptions.py` only where needed |
| Guardrail evaluation and blocked response | `tripmate/agents/guardrail.py` |
| Supervisor decision and constraint extraction | `tripmate/agents/supervisor.py` |
| Flight, hotel, weather, budget, itinerary, approval, final nodes | Corresponding modules under `tripmate/agents/` |
| State, fixed-order routing, graph assembly | `tripmate/graph/state.py`, `tripmate/graph/routing.py`, `tripmate/graph/builder.py` |
| Shared MCP client/tool lifecycle | `tripmate/integrations/mcp/client.py` |
| Provider-specific MCP operations | `tavily.py`, `aviationstack.py`, and `weather.py` under `tripmate/integrations/mcp/` |
| Local OpenWeather MCP server | `mcp_servers/weather_server.py` |
| Start/resume, interrupt extraction, result serialization | `tripmate/services/travel_service.py` |
| PostgreSQL saver and connection lifecycle | `tripmate/persistence/checkpointer.py` |
| Root frontend assets | Keep `templates/` and `static/`; configure paths from the repository root |

Build and inject the graph/service from the FastAPI lifespan after opening and
setting up the PostgreSQL checkpointer; close the database connection during
shutdown. Keep MCP subprocess configuration centralized and ensure its child
environment and weather-server path still work after relocation. Decide whether
to retain a thin root `app.py` compatibility entry point during Stage 6 after
the baseline launch commands are verified.

## Remaining verification

- Start Docker Desktop and rerun the documented Docker build/start commands.
- Configure PostgreSQL and API keys to validate real application lifespan,
  checkpoint persistence/resume, and external MCP tool calls.
- The local editor interpreter is Python 3.10.4; test under Python 3.11 as
  documented when that runtime is available.
