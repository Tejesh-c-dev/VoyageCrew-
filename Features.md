# Project report

## 1. Overview

This is a Python web application called **VoyageCrew**. It accepts a travel-planning request, uses a LangGraph workflow to select specialist agents, gathers information through MCP tools, creates a draft itinerary, pauses for human review, and then generates a final response.

The main flow is:

```text
Browser UI
  → FastAPI
  → LangGraph supervisor and input guardrail
  → Selected travel specialists
  → Draft itinerary
  → Human approval or revision feedback
  → Final response
```

The project contains **14 tracked files**. The local `.venv` is ignored environment content, not project-authored source. No code was changed to prepare this report.

## 2. File inventory

| File | Purpose |
|---|---|
| [app.py](app.py) | FastAPI application, request models, page/API routes, and calls into the backend. |
| [backend.py](backend.py) | Main application logic: LangGraph state, supervisor, guardrail, specialist agents, graph routing, approval pause/resume, and PostgreSQL checkpointing. |
| [mcp_client.py](mcp_client.py) | Configures MCP connections and provides async wrappers for Tavily, AviationStack, and the local weather server. |
| [custom_weather_mcp_server.py](custom_weather_mcp_server.py) | Local MCP server exposing current weather and forecast tools backed by OpenWeather. |
| [templates/index.html](templates/index.html) | Single-page web interface: prompt entry, workflow status, result display, and approval/revision controls. |
| [static/script.js](static/script.js) | Browser behavior, API calls, result rendering, approval handling, copy/PDF actions, and keyboard shortcut. |
| [static/style.css](static/style.css) | Page layout and styling, including workflow/result cards, responsive rules, print styles, and loading animation. |
| [requirements.txt](requirements.txt) | Pinned Python dependencies. |
| [Dockerfile](Dockerfile) | Builds a Python 3.11 image, installs dependencies, and starts Uvicorn. |
| [.dockerignore](.dockerignore) | Excludes virtual-environment directories from Docker build context. |
| [.gitignore](.gitignore) | Excludes local environments, secrets, caches, and generated files from Git. |
| [README.md](README.md) | Project description, setup instructions, API overview, and development notes. |
| [LICENSE](LICENSE) | Apache License 2.0. |
| [demo.excalidraw](demo.excalidraw) | Excalidraw diagram with three embedded images and labels for the project's no-MCP, MCP, and supervisor/guardrail/HITL stages. |

## 3. Main workflow and how the files connect

### A. Web entry and API

[app.py](app.py) serves the HTML page and exposes the API:

- `home` serves `index.html`.
- `travel_planner` validates and trims a travel request, calls `run_travel_agent`, and returns its result. Empty messages receive HTTP 400; exceptions receive HTTP 500.
- `approve_travel_plan` validates approval or revision feedback, calls `resume_travel_agent`, and returns the resumed workflow result.
- `health_check` returns a status message and feature names. It does not check database or external-service availability.
- `favicon` returns an empty JSON response for `/favicon.ico`.

The request models are `TravelRequest` (`message`, optional `thread_id`) and `ApprovalRequest` (`thread_id`, `approved`, optional `feedback`).

### B. Supervisor, specialists, and approval

[backend.py](backend.py) defines the workflow state and the nodes that LangGraph executes.

- `TravelState` holds the request, agent selections, travel constraints, specialist results, approval state, final response, messages, and an LLM-call counter.
- `supervisor_agent` first asks the LLM whether the request is travel-related, then chooses relevant agents and extracts constraints such as destination, origin, duration, and budget. The itinerary agent is always included. If guardrail parsing/model work fails, the request is allowed; if supervisor parsing fails, the full specialist sequence is selected.
- `guardrail_blocked_agent` returns the guardrail's blocked-request explanation.
- `flight_agent` requests airport and airline data from AviationStack, then asks the LLM to produce route, duration, airfare-estimate, season, and booking guidance.
- `hotel_agent` searches Tavily for hotel information. If the search fails, it supplies a fallback instruction to give clearly labeled general accommodation guidance.
- `weather_agent` extracts the destination, then requests current weather and a forecast from the weather MCP server. On MCP errors, it returns a general seasonal-guidance fallback.
- `budget_agent` asks the LLM to estimate cost categories, risks, savings, and feasibility using the request and available specialist results.
- `itinerary_agent` combines the request, constraints, and collected results into a draft, then prepares the approval prompt.
- `human_approval_agent` uses LangGraph's `interrupt` to pause the graph and return the draft, selected agents, and supervisor reasoning for review.
- `final_agent` generates the final formatted travel response and incorporates approval or revision feedback.

The routing helpers `_selected_agents`, `route_from_supervisor`, and `route_after_agent` route execution. Although the supervisor chooses agents, the execution order is fixed by `AGENT_ORDER`: flight, hotel, weather, budget, itinerary. Unselected specialists are skipped.

### C. MCP integrations

[mcp_client.py](mcp_client.py) configures the three MCP servers and provides the helpers used by backend agents.

- `_require_env` checks that a required key exists.
- `_subprocess_env` builds a child-process environment for local MCP servers.
- `_get_server_tool` loads a named tool from one server and reports missing keys, missing `uvx`, missing weather-server files, or unavailable tool names.
- `get_all_tools` tests each server independently and prints its available tools or failure. It is a diagnostic helper.
- `tavily_mcp_search` calls Tavily's `tavily_search`; used by the hotel agent.
- `aviation_mcp_call` invokes a named AviationStack tool; used by the flight agent for airport and airline data.
- `weather_mcp_search` calls `get_current_weather`.
- `forecast_mcp_search` calls `get_forecast`.
- `extract_destination` asks the LLM to extract a destination name; used by the weather agent.

The local server in [custom_weather_mcp_server.py](custom_weather_mcp_server.py) exposes:

- `_get_api_key` retrieves the OpenWeather key.
- `_request_json` sends a timed HTTP request, checks its status, and converts request failures into runtime errors.
- `get_current_weather` returns city, temperature, feels-like temperature, humidity, condition, and wind speed.
- `get_forecast` returns up to the first five three-hour forecast entries.

### D. Graph persistence and API response shaping

The backend builds a `StateGraph`, registers the supervisor, specialist, approval, final, and blocked-request nodes, and connects them with conditional routing. The compiled graph uses a PostgreSQL `PostgresSaver`.

- `get_database_url` requires `DATABASE_URL` and adds `sslmode=require` if it is absent.
- `_interrupt_payload` extracts the approval pause payload from a graph result.
- `_serialize_result` converts graph state into the API response: answer, thread ID, approval status, specialist results, constraints, selected agents, guardrail status, feedback, and LLM-call count.
- `run_travel_agent` starts a graph invocation, creating a thread ID if none was provided.
- `resume_travel_agent` resumes the paused graph using the user's approval and feedback.

## 4. Browser interface

[templates/index.html](templates/index.html) defines the page areas: travel prompt and quick prompts, supervisor reasoning and selected-agent chips, draft/final result, approval feedback, error display, copy and PDF buttons. It loads `marked` and `html2pdf.js` from CDNs.

[static/script.js](static/script.js) connects those controls to the API:

- `setPrompt` fills the prompt field; `setLoading` toggles loading/disabled states.
- `showError` and `hideError` manage the error box.
- `renderMarkdown`, `showWorkflow`, and `showResult` render the plan and supervisor output.
- `showApproval` and `hideApproval` control the human-review UI.
- `sendMessage` posts to `/api/travel`, stores the returned thread ID in `localStorage`, and shows either the draft/approval controls or a completed result.
- `submitApproval` posts to `/api/travel/approve`; revision requests require feedback.
- `copyResult` copies the rendered plan; `downloadPDF` exports it as an A4 PDF.
- A Ctrl+Enter listener submits the travel request.

[static/style.css](static/style.css) gives the UI its dark gradient/card design, styles agent chips and guardrail status, formats the rendered plan for PDF/print, and includes a small-screen layout.

## 5. Dependencies and configuration

[requirements.txt](requirements.txt) pins the project's Python packages. The main runtime groups are:

- **Web:** FastAPI, Uvicorn, Jinja2.
- **Agent/LLM:** LangGraph, LangChain, and `langchain-groq`.
- **MCP:** `langchain-mcp-adapters` and `mcp`.
- **Checkpointing/database:** Psycopg and `langgraph-checkpoint-postgres`.
- **External services/utilities:** Requests, `python-dotenv`, and Certifi.
- **Other declared packages:** LangChain community/Tavily integrations, `tavily-python`, `psycopg_pool`, `airportsdata`, and `pycountry`.

Runtime configuration is read from `.env` or environment variables:

- `GROQ_API_KEY` — required for the LLM.
- `DATABASE_URL` — required for PostgreSQL checkpointing.
- `TAVILY_API_KEY` — required when Tavily search is used.
- `AVIATION_STACK_API_KEY` or `AVIATIONSTACK_API_KEY` — accepted for AviationStack.
- `OPENWEATHER_API_KEY` — required by the local weather MCP server.
- `uvx` — required to launch the AviationStack MCP package.

A notable startup detail: `backend.py` checks the Groq key, obtains the database URL, connects to PostgreSQL, and calls checkpoint setup while the module is being imported. As a result, starting the app—including reaching its health route—depends on the required configuration and database being available.

## 6. Documentation, deployment, and testing

[README.md](README.md) documents a Windows virtual-environment setup, dependency installation, local startup, API routes, and the weather server. It describes the app as a demo and says tests are not included.

[Dockerfile](Dockerfile) installs `requirements.txt` into a Python 3.11 slim image and starts Uvicorn on port 8000. It does not install `uvx`; the AviationStack integration therefore needs that executable to be provided separately if used in the container.

The repository's tracked inventory contains no test files. This report is based on reading the project files; no tests were run.
