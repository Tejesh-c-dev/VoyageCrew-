"""Evidence contract for the travel planning workflow.

Each specialist agent records *where* its information came from, *when* it was
retrieved, and *whether* it should be treated as a live verified fact or as a
model estimate.  This module is intentionally free of business logic; it only
defines the schema, a factory, and the LangGraph state reducer.

Design constraints
------------------
- All types must be JSON-serialisable so they pass through the
  ``PostgresSaver`` JSONB checkpoint codec without custom registration.
- ``datetime`` objects are **not** stored; timestamps are ISO-8601 UTC strings
  produced by ``make_evidence``.
- ``EvidenceItem`` and ``AgentEvidence`` are ``TypedDict`` (not Pydantic) so
  they are structurally transparent to the LangGraph codec.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal, TypedDict

# ---------------------------------------------------------------------------
# Source labels
# ---------------------------------------------------------------------------

#: Machine-readable tag describing how data was obtained.
#:
#: ``aviationstack_mcp``
#:     Structured data returned directly by the AviationStack MCP server
#:     (airport and airline records).  No LLM synthesis involved in the raw
#:     records, but the flight agent combines them with LLM output.
#:
#: ``aviationstack_mcp+llm``
#:     AviationStack data used as *input* to an LLM that then produced a
#:     synthesised text result (flight recommendations, estimated pricing).
#:
#: ``tavily_mcp``
#:     Results returned by the Tavily web-search MCP.  These are web-sourced
#:     snippets, **not** independently verified by TripMate.  The hotel agent
#:     passes results through without further synthesis.
#:
#: ``openweather_mcp``
#:     Live weather data from the OpenWeather MCP server.  Temperature,
#:     humidity, wind, and forecast data are real API responses.
#:
#: ``llm``
#:     Entirely LLM-generated output with no live external data.  Budget
#:     estimates are the primary example.
#:
#: ``unavailable``
#:     The data source was unreachable or returned no usable result; the
#:     content field will describe the fallback or error.
SourceLabel = Literal[
    "aviationstack_mcp",
    "aviationstack_mcp+llm",
    "tavily_mcp",
    "openweather_mcp",
    "llm",
    "unavailable",
]


# ---------------------------------------------------------------------------
# Evidence item schema
# ---------------------------------------------------------------------------

class EvidenceItem(TypedDict, total=False):
    """Provenance record for a single specialist agent's output.

    All fields are optional (``total=False``) so partial records can be stored
    safely; consumers must use ``.get()`` with sensible defaults.

    Fields
    ------
    source : SourceLabel
        Machine-readable tag for the data origin.
    source_url : str
        Primary URL for web-sourced results (Tavily).  Empty string for
        non-web sources.
    retrieved_at : str
        ISO-8601 UTC timestamp string set at agent execution time.
        Always produced by :func:`make_evidence`; never a ``datetime`` object.
    is_estimate : bool
        ``True`` when the content was synthesised by the LLM and does **not**
        reflect a live price, measurement, or API response.
    is_verified : bool
        ``True`` when the content originates from a live external API call.
        Mutually exclusive pattern: ``is_verified=True, is_estimate=False``
        means live data; ``is_verified=False, is_estimate=True`` means LLM
        estimate.  The ``aviationstack_mcp+llm`` source sets both ``True``
        because it has a live-data foundation but includes synthesised estimates.
    content : str
        Full text result from the agent (mirrors the corresponding ``*_results``
        state field for downstream consumers that need structured access).
    records : list[dict]
        Optional list of structured records when the source returns multiple
        items (e.g. multiple Tavily search results or weather forecast entries).
        Each dict is provider-specific and must be JSON-serialisable.
    """

    source: SourceLabel
    source_url: str
    retrieved_at: str
    is_estimate: bool
    is_verified: bool
    content: str
    records: list[dict]


# ---------------------------------------------------------------------------
# Aggregate evidence for a single workflow run
# ---------------------------------------------------------------------------

class AgentEvidence(TypedDict, total=False):
    """Collected evidence from all specialist agents in one workflow run.

    Each key matches a specialist agent name (minus the ``_agent`` suffix).
    Missing keys mean the agent was not selected or did not run.
    """

    flight: EvidenceItem
    hotel: EvidenceItem
    weather: EvidenceItem
    budget: EvidenceItem


# ---------------------------------------------------------------------------
# LangGraph merge reducer
# ---------------------------------------------------------------------------

def merge_evidence(
    existing: AgentEvidence,
    update: AgentEvidence,
) -> AgentEvidence:
    """LangGraph state reducer for the ``evidence`` field.

    LangGraph's default reducer for non-annotated dict fields is
    *last-write-wins* at the top level, meaning a later agent would silently
    erase evidence written by earlier agents.  This reducer instead performs a
    **shallow merge** so each agent's sub-key is preserved independently.

    Usage in ``TravelState``::

        evidence: Annotated[AgentEvidence, merge_evidence]

    Parameters
    ----------
    existing:
        Evidence accumulated so far (may be empty dict on first agent).
    update:
        Partial evidence dict returned by the current agent node.

    Returns
    -------
    AgentEvidence
        Merged dict containing all keys from both inputs.  If the same key
        appears in both (e.g. an agent runs twice), the newer value wins.
    """
    return {**existing, **update}  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Evidence factory
# ---------------------------------------------------------------------------

def make_evidence(
    *,
    source: SourceLabel,
    content: str,
    is_estimate: bool,
    is_verified: bool,
    source_url: str = "",
    records: list[dict] | None = None,
) -> EvidenceItem:
    """Create an :class:`EvidenceItem` with a guaranteed ISO-8601 UTC timestamp.

    This factory is the **only** intended way to create evidence items inside
    agent code.  It enforces that ``retrieved_at`` is always an ISO-8601 string
    rather than a bare ``datetime`` object, which would break the LangGraph
    JSONB checkpoint codec.

    Parameters
    ----------
    source:
        Machine-readable origin tag (:data:`SourceLabel`).
    content:
        Full text result from the agent.
    is_estimate:
        Whether the content was LLM-synthesised without live data backing.
    is_verified:
        Whether the content came from a live external API call.
    source_url:
        Primary result URL for web-sourced content.  Leave empty for non-web.
    records:
        Optional list of structured provider records (JSON-serialisable dicts).

    Returns
    -------
    EvidenceItem
        A fully populated evidence item ready to be stored in ``TravelState``.
    """
    item: EvidenceItem = {
        "source": source,
        "source_url": source_url,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "is_estimate": is_estimate,
        "is_verified": is_verified,
        "content": content,
    }
    if records is not None:
        item["records"] = records
    return item
