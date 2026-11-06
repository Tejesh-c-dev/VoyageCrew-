# VoyageCrew Features

VoyageCrew is an AI travel planner that turns a traveler’s request into a tailored itinerary. It combines specialist agents for travel research with a review step, so users can refine a draft before receiving the final plan.

## Traveler experience

- Sign in with an email and password and plan trips in a responsive React interface.
- Describe the destination, origin, dates or duration, budget, travel style, and preferences. Suggested prompts help users get started.
- See whether a request passed the input guardrail, which specialists were selected, and the supervisor’s routing summary.
- Review the draft itinerary before completion. Approve it to generate the final response, or provide feedback to revise it.
- Copy the displayed plan or export it as a PDF. Markdown is sanitized before rendering in the browser.
- Keep a planner thread in the signed-in session. The backend checks thread ownership on both planning and approval requests.

## Planning capabilities

- An input guardrail checks the request before planning. The supervisor routes allowed requests to the relevant flight, hotel, weather, budget, and itinerary specialists; the itinerary specialist is always included.
- Specialists run in a fixed order and contribute research or estimates to the trip plan. Provider integrations use AviationStack for flight-related information, Tavily for web search, and a local OpenWeather MCP server for current weather and forecasts.
- The workflow gathers evidence from specialist results, assembles a draft, and pauses for human approval. Feedback resumes the same workflow thread for revision; approval continues to the final response.
- LangGraph checkpoints in PostgreSQL preserve workflow state across requests. PostgreSQL also stores thread ownership so one account cannot continue another account’s planner thread.

## Application architecture

FastAPI serves the production React frontend and provides authenticated travel and approval endpoints, runtime frontend configuration, and health and readiness checks. LangGraph coordinates the stateful planning workflow. MCP adapters manage connections to external travel and search providers, and PostgreSQL backs checkpoints and thread ownership. The application also includes request-size limits, structured request logging, correlation IDs, and centralized API error handling.

| Location | Responsibility |
|---|---|
| `main.py` | FastAPI application and lifecycle |
| `tripmate/api/` | Authentication, request schemas, and routes |
| `tripmate/agents/` and `tripmate/graph/` | Specialist agents, workflow state, routing, and approvals |
| `tripmate/integrations/mcp/` | External provider connections |
| `tripmate/persistence/` | PostgreSQL checkpoints and thread ownership |
| `frontend/src/` | React planner interface and API client |
| `tests/` | Backend and browser regression coverage |

## Runtime dependencies

The application uses built-in email/password accounts, Groq for language-model requests, PostgreSQL for durable workflow state and account-scoped threads, and MCP integrations for provider information. Provider credentials are required for their corresponding live data. The frontend is built with Vite and served by FastAPI in production; Docker packages the frontend build and Python application together.

For installation, configuration, API details, and verification status, see [README.md](README.md).
