# TripMate features

TripMate accepts travel-planning requests, selects specialist agents, gathers
provider information, and prepares an itinerary for human review before
producing a final response.

## User experience

- Sign-in through Clerk with account-scoped planner threads.
- Responsive React planner with suggested prompts, workflow status, draft
  review and revision, and final itinerary display.
- Markdown output is sanitized with DOMPurify. Users can copy a plan or export
  it as a PDF; PDF dependencies load only when export is requested.
- FastAPI serves the production Vite bundle, its assets, runtime Clerk
  configuration, and client-side routes.

## Planning workflow

- An input guardrail and supervisor route allowed requests to flight, hotel,
  weather, budget, and itinerary specialists.
- MCP integrations connect AviationStack, Tavily, and the local OpenWeather
  server. Provider results and generated estimates are tracked as evidence.
- The itinerary pauses for human approval or revision. PostgreSQL-backed
  LangGraph checkpoints preserve workflow state between requests.
- API thread ownership is stored in PostgreSQL and checked on travel and
  approval routes.

## Project structure

| Location | Responsibility |
|---|---|
| `main.py` | FastAPI application and lifecycle |
| `tripmate/api/` | Authentication, schemas, route handlers, and error responses |
| `tripmate/agents/` | Guardrail, supervisor, specialist, approval, and final workflow nodes |
| `tripmate/graph/` | LangGraph state, routing, evidence, and graph construction |
| `tripmate/integrations/mcp/` | MCP providers and client setup |
| `tripmate/persistence/` | PostgreSQL checkpointer, thread ownership, and SQL migrations |
| `frontend/src/` | React planner, API client, and styles |
| `tests/` | Python regression tests and Playwright browser tests |
| `Dockerfile` | Multi-stage frontend build and Python application image |

## Release verification status

- `npm run build --prefix frontend` — passed without a Vite chunk size warning.
  The largest initial JavaScript chunk is 428.31 kB, under the 500 kB threshold.
- `npm test --prefix frontend` — passed (2 tests).
- `npm run test:playwright` — passed (17 tests) against the production bundle
  served through FastAPI. Clerk and planner API calls are mocked in Playwright;
  Python tests cover FastAPI route and ownership behavior.
- `python tests/run_tests.py` — 250 discovered; 240 passed and 2 PostgreSQL
  integration tests skipped. The run retains a Pydantic
  `IncompleteFieldDefinitionWarning` from a dependency's unresolved `lifespan`
  annotation. No unawaited-coroutine warning remains.
- PostgreSQL-backed ownership and migration verification is pending until the
  integration tests run against a disposable local database.
- Docker image verification is pending because the Docker Desktop engine was
  unavailable during the audit.

See [the React migration and release readiness report](docs/REACT_MIGRATION_PROGRESS.md)
for verification scope and limitations.
