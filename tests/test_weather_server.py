"""Offline and credentialed tests for the restored weather MCP server.

Offline tests use mocked HTTP responses; the live smoke tests run only when
OPENWEATHER_API_KEY is available and are skipped otherwise.
"""

import asyncio
import os
import sys
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

import httpx

from mcp_servers import weather_server

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CURRENT_FIXTURE = {
    "name": "London",
    "main": {"temp": 12.3, "feels_like": 11.8, "humidity": 72},
    "weather": [{"description": "light rain", "main": "Rain"}],
    "wind": {"speed": 4.5},
}


def _forecast_fixture(period_count: int = 7) -> dict[str, Any]:
    periods = []
    for index in range(period_count):
        periods.append(
            {
                "dt_txt": f"2026-11-02 12:0{index}:00",
                "main": {"temp": 12.0 + index, "feels_like": 11.5, "humidity": 70},
                "weather": [{"description": "light rain"}],
                "wind": {"speed": 4.0},
                "pop": round(0.1 * index, 1),
            }
        )
    return {"city": {"name": "London"}, "list": periods}


def _response(status_code: int, **kwargs: Any) -> httpx.Response:
    return httpx.Response(status_code, **kwargs)


def _http_mock(results: list[Any]) -> tuple[MagicMock, list[Any]]:
    """Build an httpx.AsyncClient stand-in serving scripted get() results."""
    calls: list[tuple[str, dict[str, Any] | None]] = []
    remaining = list(results)

    async def get(url: str, params: Any = None) -> Any:
        calls.append((url, params))
        item = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        if isinstance(item, Exception):
            raise item
        return item

    client = MagicMock()
    client.get = get
    client.__aenter__.return_value = client
    return client, calls


class AsyncTestCase(unittest.TestCase):
    def run_async(self, coroutine: Any) -> Any:
        return asyncio.run(coroutine)


class CurrentWeatherPayloadTests(unittest.TestCase):
    def test_builds_structured_current_weather(self):
        payload = weather_server.current_weather_payload("London", CURRENT_FIXTURE)
        self.assertEqual(
            payload,
            {
                "city": "London",
                "temperature": 12.3,
                "feels_like": 11.8,
                "humidity": 72,
                "condition": "light rain",
                "wind_speed": 4.5,
            },
        )

    def test_missing_temperature_raises_unexpected_response(self):
        broken = {**CURRENT_FIXTURE, "main": {"feels_like": 11.8, "humidity": 72}}
        with self.assertRaisesRegex(ValueError, "Unexpected response"):
            weather_server.current_weather_payload("London", broken)

    def test_non_numeric_temperature_raises_unexpected_response(self):
        broken = {**CURRENT_FIXTURE, "main": {**CURRENT_FIXTURE["main"], "temp": "warm"}}
        with self.assertRaisesRegex(ValueError, "Unexpected response"):
            weather_server.current_weather_payload("London", broken)

    def test_infinite_temperature_raises_unexpected_response(self):
        broken = {**CURRENT_FIXTURE, "main": {**CURRENT_FIXTURE["main"], "temp": float("inf")}}
        with self.assertRaisesRegex(ValueError, "Unexpected response"):
            weather_server.current_weather_payload("London", broken)

    def test_missing_weather_entry_raises_unexpected_response(self):
        broken = {key: value for key, value in CURRENT_FIXTURE.items() if key != "weather"}
        with self.assertRaisesRegex(ValueError, "Unexpected response"):
            weather_server.current_weather_payload("London", broken)

    def test_missing_city_name_falls_back_to_request(self):
        nameless = {**CURRENT_FIXTURE, "name": ""}
        payload = weather_server.current_weather_payload("Paris", nameless)
        self.assertEqual(payload["city"], "Paris")

    def test_out_of_range_humidity_raises_unexpected_response(self):
        broken = {**CURRENT_FIXTURE, "main": {**CURRENT_FIXTURE["main"], "humidity": 500}}
        with self.assertRaisesRegex(ValueError, "Unexpected response"):
            weather_server.current_weather_payload("London", broken)


class ForecastPayloadTests(unittest.TestCase):
    def test_returns_at_most_five_validated_entries(self):
        payload = weather_server.forecast_payload("London", _forecast_fixture(7))
        self.assertEqual(payload["city"], "London")
        self.assertEqual(len(payload["forecast"]), 5)
        first = payload["forecast"][0]
        self.assertEqual(first["observed_at"], "2026-11-02 12:00:00")
        self.assertEqual(first["precipitation_probability"], 0)
        self.assertEqual(first["temperature"], 12.0)
        self.assertEqual(first["wind_speed"], 4.0)
        self.assertEqual(payload["forecast"][2]["precipitation_probability"], 20)

    def test_empty_forecast_list_raises_unavailable(self):
        empty = {"city": {"name": "London"}, "list": []}
        with self.assertRaisesRegex(ValueError, "forecast is currently unavailable"):
            weather_server.forecast_payload("London", empty)
        with self.assertRaisesRegex(ValueError, "forecast is currently unavailable"):
            weather_server.forecast_payload("London", {"city": {"name": "London"}})

    def test_invalid_entry_raises_unexpected_response(self):
        broken = _forecast_fixture(1)
        del broken["list"][0]["dt_txt"]
        with self.assertRaisesRegex(ValueError, "Unexpected response"):
            weather_server.forecast_payload("London", broken)

    def test_bad_pop_value_defaults_to_zero(self):
        fixture = _forecast_fixture(1)
        fixture["list"][0]["pop"] = "storm"
        payload = weather_server.forecast_payload("London", fixture)
        self.assertEqual(payload["forecast"][0]["precipitation_probability"], 0)


class CityAndApiKeyValidationTests(AsyncTestCase):
    def test_empty_city_is_rejected_before_any_http_call(self):
        with patch.object(weather_server.httpx, "AsyncClient") as client_cls:
            with self.assertRaisesRegex(ValueError, "city name is required"):
                self.run_async(weather_server.fetch_current_weather("   "))
            with self.assertRaisesRegex(ValueError, "city name is required"):
                self.run_async(weather_server.fetch_forecast(""))
        client_cls.assert_not_called()

    def test_overlong_city_is_rejected_before_any_http_call(self):
        with patch.object(weather_server.httpx, "AsyncClient") as client_cls:
            with self.assertRaisesRegex(ValueError, "characters or fewer"):
                self.run_async(weather_server.fetch_current_weather("a" * 101))
        client_cls.assert_not_called()

    def test_missing_api_key_raises_safe_error(self):
        environment = {k: v for k, v in os.environ.items() if k != "OPENWEATHER_API_KEY"}
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(ValueError, "OPENWEATHER_API_KEY is missing"):
                self.run_async(weather_server.fetch_current_weather("Paris"))


class WeatherHttpRequestTests(AsyncTestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {"OPENWEATHER_API_KEY": "test-key"},
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def _run_with_responses(self, results: list[Any], fetch: Any) -> tuple[Any, list[Any]]:
        client, calls = _http_mock(results)
        with patch.object(weather_server.httpx, "AsyncClient", return_value=client):
            return self.run_async(fetch("London")), calls

    def _call_count_despite_error(self, results: list[Any], fetch: Any) -> int:
        client, calls = _http_mock(results)
        with patch.object(weather_server.httpx, "AsyncClient", return_value=client):
            with self.assertRaises(ValueError):
                self.run_async(fetch("London"))
        return len(calls)

    def test_transient_500_retries_then_succeeds(self):
        result, calls = self._run_with_responses(
            [
                _response(500, json={"cod": 500}),
                _response(200, json=CURRENT_FIXTURE),
            ],
            weather_server.fetch_current_weather,
        )
        self.assertEqual(result["city"], "London")
        self.assertEqual(len(calls), 2)
        url, params = calls[0]
        self.assertEqual(url, "https://api.openweathermap.org/data/2.5/weather")
        self.assertEqual(params["appid"], "test-key")
        self.assertEqual(params["units"], "metric")
        self.assertEqual(params["q"], "London")

    def test_transient_429_retries_then_succeeds(self):
        result, calls = self._run_with_responses(
            [
                _response(429, json={"cod": 429}),
                _response(200, json=CURRENT_FIXTURE),
            ],
            weather_server.fetch_current_weather,
        )
        self.assertEqual(result["humidity"], 72)
        self.assertEqual(len(calls), 2)

    def test_timeout_retries_then_succeeds(self):
        result, calls = self._run_with_responses(
            [
                httpx.ConnectTimeout("timed out"),
                _response(200, json=CURRENT_FIXTURE),
            ],
            weather_server.fetch_current_weather,
        )
        self.assertEqual(result["temperature"], 12.3)
        self.assertEqual(len(calls), 2)

    def test_persistent_transient_statuses_return_unavailable_error(self):
        with self.assertRaisesRegex(ValueError, "temporarily unavailable"):
            self._run_with_responses(
                [
                    _response(503, json={}),
                    _response(503, json={}),
                    _response(503, json={}),
                ],
                weather_server.fetch_current_weather,
            )

    def test_persistent_timeouts_return_unavailable_error(self):
        with self.assertRaisesRegex(ValueError, "temporarily unavailable"):
            self._run_with_responses(
                [httpx.ReadTimeout("timed out")] * 3,
                weather_server.fetch_current_weather,
            )

    def test_invalid_location_returns_not_found_without_retries(self):
        with self.assertRaisesRegex(ValueError, "location was not found"):
            self._run_with_responses(
                [_response(404, json={"cod": "404", "message": "city not found"})],
                weather_server.fetch_current_weather,
            )
        self.assertEqual(
            self._call_count_despite_error(
                [_response(404, json={"cod": "404"})],
                weather_server.fetch_forecast,
            ),
            1,
        )

    def test_forecast_url_and_structured_result(self):
        result, calls = self._run_with_responses(
            [_response(200, json=_forecast_fixture(2))],
            weather_server.fetch_forecast,
        )
        url, _ = calls[0]
        self.assertEqual(url, "https://api.openweathermap.org/data/2.5/forecast")
        self.assertEqual(len(result["forecast"]), 2)
        self.assertEqual(result["city"], "London")

    def test_rejected_api_key_raises_safe_error_without_retries(self):
        with self.assertRaisesRegex(ValueError, "rejected the configured OPENWEATHER_API_KEY"):
            _, calls = self._run_with_responses(
                [_response(401, json={"cod": 401})],
                weather_server.fetch_current_weather,
            )
            self.assertEqual(len(calls), 1)

    def test_non_json_body_raises_unexpected_response(self):
        with self.assertRaisesRegex(ValueError, "Unexpected response"):
            self._run_with_responses(
                [_response(200, content=b"<html>gateway</html>")],
                weather_server.fetch_current_weather,
            )

    def test_non_dict_json_raises_unexpected_response(self):
        with self.assertRaisesRegex(ValueError, "Unexpected response"):
            self._run_with_responses(
                [_response(200, json=[1, 2, 3])],
                weather_server.fetch_current_weather,
            )


class WeatherClientConfigurationTests(unittest.TestCase):
    """The shared MCP client must point the weather server at this file."""

    def test_client_launches_restored_weather_server_with_key(self):
        from pathlib import Path

        from tripmate.core.config import Settings
        from tripmate.integrations.mcp.client import create_mcp_integration

        settings = Settings(
            Path(PROJECT_ROOT),
            "unused",
            None,
            None,
            None,
            "test-openweather-key",
        )
        integration = create_mcp_integration(settings)
        connection = integration.client.connections["weather"]
        self.assertEqual(connection["args"][0], str(Path(PROJECT_ROOT) / "mcp_servers" / "weather_server.py"))
        self.assertIn("OPENWEATHER_API_KEY", connection["env"])
        self.assertEqual(connection["env"]["OPENWEATHER_API_KEY"], "test-openweather-key")


class WeatherMcpStdioTests(unittest.TestCase):
    """Offline stdio handshake: the server boots and exposes both tools."""

    def test_stdio_initialize_and_tool_discovery(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        server_path = os.path.join(PROJECT_ROOT, "mcp_servers", "weather_server.py")
        self.assertTrue(os.path.isfile(server_path))
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[server_path],
            cwd=PROJECT_ROOT,
        )

        async def run():
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    names = {tool.name for tool in tools.tools}
                    self.assertIn("get_current_weather", names)
                    self.assertIn("get_forecast", names)
                    schema = next(
                        tool for tool in tools.tools if tool.name == "get_current_weather"
                    ).inputSchema
                    self.assertIn("city", schema.get("properties", {}))
                    self.assertEqual(schema.get("required"), ["city"])

        asyncio.run(run())


class WeatherLiveSmokeTests(unittest.TestCase):
    """Live smoke tests: skipped unless OPENWEATHER_API_KEY is configured."""

    @classmethod
    def setUpClass(cls):
        if os.getenv("TRIPMATE_SKIP_LIVE_TESTS") == "1":
            cls.skip_live = True
            return
        from tripmate.core.config import PROJECT_ROOT, load_settings

        cls.settings = load_settings(PROJECT_ROOT)
        cls.skip_live = not bool(cls.settings.openweather_api_key)

    def test_live_current_weather_smoke(self):
        if self.skip_live:
            self.skipTest("live provider tests disabled or OPENWEATHER_API_KEY unavailable")
        result = asyncio.run(weather_server.fetch_current_weather("London"))
        self.assertIn("city", result)
        self.assertTrue(result["condition"])
        self.assertGreaterEqual(result["humidity"], 0)
        self.assertLessEqual(result["humidity"], 100)
        self.assertGreaterEqual(result["wind_speed"], 0)

    def test_live_forecast_smoke(self):
        if self.skip_live:
            self.skipTest("live provider tests disabled or OPENWEATHER_API_KEY unavailable")
        result = asyncio.run(weather_server.fetch_forecast("London"))
        entries = result["forecast"]
        self.assertGreaterEqual(len(entries), 1)
        self.assertLessEqual(len(entries), 5)
        for entry in entries:
            self.assertTrue(entry["observed_at"])
            self.assertTrue(entry["condition"])


if __name__ == "__main__":
    unittest.main()
