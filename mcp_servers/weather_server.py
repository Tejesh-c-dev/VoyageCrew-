"""Local OpenWeather-backed weather MCP server.

Launched as a stdio MCP server by ``tripmate.integrations.mcp.client``, which
points it at ``mcp_servers/weather_server.py`` and injects
``OPENWEATHER_API_KEY`` into the child-process environment.

Tools
-----
- ``get_current_weather(city)`` returns ``city``, ``temperature``,
  ``feels_like``, ``humidity``, ``condition``, and ``wind_speed``.
- ``get_forecast(city)`` returns up to the first five three-hour forecast
  entries with the same core fields plus ``observed_at`` and
  ``precipitation_probability``.

Failures (invalid locations, rejected API keys, exhausted transient retries,
missing or malformed payloads) raise ``ValueError`` carrying safe,
client-facing messages; the MCP protocol frames these as error results and
the application sanitizes them for the end user.

Every HTTP request runs under bounded per-attempt timeouts with a small
number of jittered retries for transient failures, sized so the worst-case
tool call stays well inside the MCP stdio read timeout configured by the
client (20 s). Logging goes to stderr only, because stdout carries the stdio
MCP protocol.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import random
import sys
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, ValidationError

SERVER_NAME = "tripmate-weather"
API_BASE_URL = (
    os.getenv("OPENWEATHER_BASE_URL")
    or "https://api.openweathermap.org/data/2.5"
).rstrip("/")

MAX_CITY_LENGTH = 100
MAX_FORECAST_ENTRIES = 5

HTTP_ATTEMPTS = 3
HTTP_CONNECT_TIMEOUT_SECONDS = 2.0
HTTP_READ_TIMEOUT_SECONDS = 3.0
RETRY_BASE_DELAY_SECONDS = 0.25
RETRY_MAX_DELAY_SECONDS = 1.0

TRANSIENT_STATUS_CODES = {408, 409, 425, 429} | set(
    code for code in range(500, 600) if code not in {501, 505}
)

MSG_LOCATION_NOT_FOUND = (
    "Weather information for '{city}' is unavailable: the location was not "
    "found. Check the destination name and try again."
)
MSG_API_KEY_REJECTED = (
    "The weather service rejected the configured OPENWEATHER_API_KEY."
)
MSG_SERVICE_UNAVAILABLE = (
    "The weather service is temporarily unavailable. Please try again shortly."
)
MSG_UNEXPECTED_RESPONSE = "Unexpected response from the weather service."
MSG_FORECAST_UNAVAILABLE = "The forecast is currently unavailable for '{city}'."

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP(SERVER_NAME)


class CurrentWeather(BaseModel):
    """Validated current-weather response returned by ``get_current_weather``."""

    model_config = ConfigDict(allow_inf_nan=False)

    city: str = Field(min_length=1)
    temperature: float
    feels_like: float
    humidity: int = Field(ge=0, le=100)
    condition: str = Field(min_length=1)
    wind_speed: float = Field(ge=0)


class ForecastEntry(BaseModel):
    """Validated single three-hour forecast entry."""

    model_config = ConfigDict(allow_inf_nan=False)

    observed_at: str = Field(min_length=1)
    temperature: float
    feels_like: float
    humidity: int = Field(ge=0, le=100)
    condition: str = Field(min_length=1)
    wind_speed: float = Field(ge=0)
    precipitation_probability: int = Field(ge=0, le=100)


def _is_transient_status(status_code: int) -> bool:
    return status_code in TRANSIENT_STATUS_CODES


def _http_timeout() -> httpx.Timeout:
    return httpx.Timeout(
        connect=HTTP_CONNECT_TIMEOUT_SECONDS,
        read=HTTP_READ_TIMEOUT_SECONDS,
        write=HTTP_CONNECT_TIMEOUT_SECONDS,
        pool=HTTP_CONNECT_TIMEOUT_SECONDS,
    )


def _retry_delay(retry_index: int) -> float:
    exponential_cap = min(
        RETRY_BASE_DELAY_SECONDS * 2**retry_index,
        RETRY_MAX_DELAY_SECONDS,
    )
    return random.uniform(0.0, exponential_cap)


def _unexpected_response(location: str) -> ValueError:
    return ValueError(f"{MSG_UNEXPECTED_RESPONSE} ({location})")


def _mapping(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _unexpected_response(location)
    return value


def _number(source: dict[str, Any], key: str, location: str) -> int | float:
    value = source.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _unexpected_response(location)
    if not math.isfinite(value):
        raise _unexpected_response(location)
    return value


def _text(source: dict[str, Any], key: str, location: str) -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _unexpected_response(location)
    return value


def _condition_from(payload: dict[str, Any], location: str) -> str:
    weather_entries = payload.get("weather")
    entries = weather_entries if isinstance(weather_entries, list) else []
    entry = entries[0] if entries else None
    if not isinstance(entry, dict):
        raise _unexpected_response(location)
    condition = entry.get("description") or entry.get("main")
    if not isinstance(condition, str) or not condition.strip():
        raise _unexpected_response(location)
    return condition.strip()


def _city_name(requested_city: str, names: Any, location: str) -> str:
    """Prefer the resolved location name from the response, fall back to request."""
    if isinstance(names, str) and names.strip():
        return names.strip()
    if isinstance(names, dict):
        name = names.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    if requested_city:
        return requested_city
    raise _unexpected_response(location)


def current_weather_payload(city: str, payload: dict[str, Any]) -> dict:
    """Validate a current-conditions payload into its structured response."""
    location = "current weather payload"
    main = _mapping(payload.get("main"), location)
    resolved_city = _city_name(city, payload.get("name"), location)
    try:
        return CurrentWeather(
            city=resolved_city,
            temperature=_number(main, "temp", location),
            feels_like=_number(main, "feels_like", location),
            humidity=_number(main, "humidity", location),
            condition=_condition_from(payload, location),
            wind_speed=_number(
                _mapping(payload.get("wind"), location),
                "speed",
                location,
            ),
        ).model_dump()
    except ValidationError as error:
        raise _unexpected_response(location) from error


def forecast_payload(city: str, payload: dict[str, Any]) -> dict:
    """Validate a forecast payload into up to five structured entries."""
    location = "forecast payload"
    periods = payload.get("list")
    if not isinstance(periods, list) or not periods:
        raise ValueError(MSG_FORECAST_UNAVAILABLE.format(city=city))
    resolved_city = _city_name(
        city,
        _mapping(payload.get("city"), location).get("name"),
        location,
    )
    entries: list[ForecastEntry] = []
    for period in periods[:MAX_FORECAST_ENTRIES]:
        single = "forecast entry"
        main = _mapping(period.get("main"), single)
        wind = _mapping(period.get("wind"), single)
        pop = period.get("pop", 0.0)
        if isinstance(pop, bool) or not isinstance(pop, (int, float)):
            pop = 0.0
        try:
            entries.append(
                ForecastEntry(
                    observed_at=_text(period, "dt_txt", single),
                    temperature=_number(main, "temp", single),
                    feels_like=_number(main, "feels_like", single),
                    humidity=_number(main, "humidity", single),
                    condition=_condition_from(period, single),
                    wind_speed=_number(wind, "speed", single),
                    precipitation_probability=round(pop * 100),
                )
            )
        except ValidationError as error:
            raise _unexpected_response(single) from error
    return {
        "city": resolved_city,
        "forecast": [entry.model_dump() for entry in entries],
    }


def _get_api_key() -> str:
    api_key = os.getenv("OPENWEATHER_API_KEY")
    if not api_key:
        raise ValueError(
            "OPENWEATHER_API_KEY is missing. "
            "Add OPENWEATHER_API_KEY=your_key to the project .env file."
        )
    return api_key


def _validate_city(city: str) -> str:
    cleaned = (city or "").strip()
    if not cleaned:
        raise ValueError("A city name is required to look up weather.")
    if len(cleaned) > MAX_CITY_LENGTH:
        raise ValueError(
            f"City name must be {MAX_CITY_LENGTH} characters or fewer."
        )
    return cleaned


async def _request_json(
    endpoint: str,
    params: dict[str, Any],
    city: str,
) -> dict[str, Any]:
    """Send a timed HTTP request with bounded retries for transient failures."""
    url = f"{API_BASE_URL}/{endpoint}"
    for attempt in range(HTTP_ATTEMPTS):
        if attempt:
            await asyncio.sleep(_retry_delay(attempt - 1))
        try:
            async with httpx.AsyncClient(timeout=_http_timeout()) as client:
                response = await client.get(url, params=params)
        except (httpx.TimeoutException, httpx.TransportError) as error:
            logger.warning(
                "OpenWeather request attempt %d/%d failed: %s (%s)",
                attempt + 1,
                HTTP_ATTEMPTS,
                endpoint,
                type(error).__name__,
            )
            continue

        status = response.status_code
        if 200 <= status < 300:
            try:
                payload = response.json()
            except ValueError as error:
                raise _unexpected_response(endpoint) from error
            if not isinstance(payload, dict):
                raise _unexpected_response(endpoint)
            return payload

        if status in {400, 404}:
            raise ValueError(
                MSG_LOCATION_NOT_FOUND.format(city=city)
            )
        if status in {401, 403}:
            raise ValueError(MSG_API_KEY_REJECTED)
        if _is_transient_status(status) and attempt < HTTP_ATTEMPTS - 1:
            logger.warning(
                "OpenWeather request attempt %d/%d got transient status %d "
                "for %s.",
                attempt + 1,
                HTTP_ATTEMPTS,
                status,
                endpoint,
            )
            continue
        logger.warning(
            "OpenWeather request for %s got status %d.",
            endpoint,
            status,
        )
        break

    raise ValueError(MSG_SERVICE_UNAVAILABLE)


async def fetch_current_weather(city: str) -> dict:
    """Fetch and validate current weather for a city (OpenWeather metric units)."""
    cleaned_city = _validate_city(city)
    payload = await _request_json(
        "weather",
        {"q": cleaned_city, "appid": _get_api_key(), "units": "metric"},
        cleaned_city,
    )
    return current_weather_payload(cleaned_city, payload)


async def fetch_forecast(city: str) -> dict:
    """Fetch and validate up to five upcoming three-hour forecast entries."""
    cleaned_city = _validate_city(city)
    payload = await _request_json(
        "forecast",
        {"q": cleaned_city, "appid": _get_api_key(), "units": "metric"},
        cleaned_city,
    )
    return forecast_payload(cleaned_city, payload)


@mcp.tool()
async def get_current_weather(city: str) -> dict[str, Any]:
    """Return current weather for a city: temperature, feels-like, humidity, condition, and wind speed."""
    return await fetch_current_weather(city)


@mcp.tool()
async def get_forecast(city: str) -> dict[str, Any]:
    """Return up to the first five three-hour forecast entries for a city."""
    return await fetch_forecast(city)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
