import asyncio
import unittest
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import main
from tripmate.api.dependencies import get_application_readiness, get_checkpointer_resources
from tripmate.api.dependencies import get_current_user_id, get_travel_service
from tripmate.api.exception_handlers import install_exception_handlers
from tripmate.agents.approval import human_approval_agent
from tripmate.core.config import Settings
from tripmate.core.exceptions import BadRequestError
from tripmate.graph.routing import (
    AGENT_ORDER,
    route_after_agent,
    route_from_guardrail,
    route_from_supervisor,
    selected_agents,
)
from tripmate.persistence.checkpointer import CheckpointerResources, create_checkpointer
from tripmate.services.travel_service import TravelService


class LifespanTests(unittest.TestCase):
    def test_startup_ready_and_shutdown_closes_resources(self):
        resources = MagicMock()
        settings = Settings(
            None, "unused", None, None, None, None,
            clerk_issuer="https://clerk.example.test",
            clerk_audience="tripmate-api",
            clerk_publishable_key="pk_test_placeholder",
            clerk_frontend_api_url="clerk.example.test",
        )
        with patch.object(main, "load_settings", return_value=settings), \
             patch.object(main, "configure_ssl_certificates"), \
             patch.object(main, "create_checkpointer", return_value=resources), \
             patch.object(main, "create_mcp_integration", return_value=object()), \
             patch.object(main, "create_llm", return_value=object()), \
             patch.object(main, "build_travel_graph", return_value=object()):
            async def run():
                async with main.lifespan(main.app):
                    self.assertTrue(main.app.state.is_ready)
                    self.assertIsNotNone(main.app.state.travel_service)
                    self.assertIs(main.app.state.checkpointer_resources, resources)
                self.assertFalse(main.app.state.is_ready)
                self.assertIsNone(main.app.state.travel_service)
                self.assertIsNone(main.app.state.checkpointer_resources)
            asyncio.run(run())
        resources.close.assert_called_once_with()

    def test_startup_failure_closes_created_checkpointer(self):
        resources = MagicMock()
        settings = Settings(
            None, "unused", None, None, None, None,
            clerk_issuer="https://clerk.example.test",
            clerk_audience="tripmate-api",
            clerk_publishable_key="pk_test_placeholder",
            clerk_frontend_api_url="clerk.example.test",
        )
        with patch.object(main, "load_settings", return_value=settings), \
             patch.object(main, "configure_ssl_certificates"), \
             patch.object(main, "create_checkpointer", return_value=resources), \
             patch.object(main, "create_mcp_integration", side_effect=RuntimeError("offline")):
            async def run():
                with self.assertRaises(RuntimeError):
                    async with main.lifespan(main.app):
                        pass
                self.assertFalse(main.app.state.is_ready)
            asyncio.run(run())
        resources.close.assert_called_once_with()


class HealthAndApiTests(unittest.TestCase):
    def setUp(self):
        @asynccontextmanager
        async def no_startup(application):
            yield
        self.original_lifespan = main.app.router.lifespan_context
        main.app.router.lifespan_context = no_startup

    def tearDown(self):
        main.app.router.lifespan_context = self.original_lifespan
        main.app.dependency_overrides.clear()

    def test_health_live_and_ready_endpoints(self):
        resources = MagicMock()
        resources.is_available.return_value = True
        main.app.dependency_overrides[get_application_readiness] = lambda: True
        main.app.dependency_overrides[get_checkpointer_resources] = lambda: resources
        try:
            with TestClient(main.app) as client:
                self.assertEqual(client.get("/health").status_code, 200)
                self.assertEqual(client.get("/health/live").json(), {"status": "ok"})
                self.assertEqual(client.get("/health/ready").json(), {"status": "ok"})
                resources.is_available.assert_called_once_with()
        finally:
            main.app.dependency_overrides.clear()

    def test_not_ready_when_startup_or_database_probe_fails(self):
        main.app.dependency_overrides[get_application_readiness] = lambda: False
        main.app.dependency_overrides[get_checkpointer_resources] = lambda: None
        try:
            with TestClient(main.app) as client:
                response = client.get("/health/ready")
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {"status": "not_ready"})
        finally:
            main.app.dependency_overrides.clear()

    def test_validation_and_public_application_errors(self):
        main.app.dependency_overrides[get_travel_service] = lambda: MagicMock()
        main.app.dependency_overrides[get_current_user_id] = lambda: "00000000-0000-0000-0000-000000000001"
        with TestClient(main.app) as client:
            invalid = client.post("/api/travel/approve", json={"thread_id": "thread-1"})
            self.assertEqual(invalid.status_code, 422)
            self.assertEqual(invalid.json(), {"success": False, "error": "The request is invalid."})
            self.assertTrue(invalid.headers.get("x-request-id"))

    def test_unexpected_error_is_sanitized(self):
        from fastapi import FastAPI
        app = FastAPI()
        install_exception_handlers(app)
        @app.get("/")
        async def fail():
            raise RuntimeError("private detail")
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"success": False, "error": "An unexpected error occurred."})
        self.assertNotIn("private detail", response.text)


class CheckpointerTests(unittest.TestCase):
    def test_initialize_saver_and_close_pool(self):
        settings = Settings(None, "postgresql://example/db", None, None, None, None)
        pool = MagicMock()
        saver = MagicMock()
        with patch("tripmate.persistence.checkpointer.ConnectionPool", return_value=pool) as pool_cls, \
             patch("tripmate.persistence.checkpointer.DiagnosticPostgresSaver", return_value=saver) as saver_cls:
            resources = create_checkpointer(settings)
        pool.open.assert_called_once()
        saver_cls.assert_called_once_with(pool)
        saver.setup.assert_called_once_with()
        self.assertIs(resources.saver, saver)
        resources.close()
        pool.close.assert_called_once_with()
        self.assertEqual(pool_cls.call_args.kwargs["open"], False)

    def test_setup_failure_closes_pool(self):
        pool = MagicMock()
        with patch("tripmate.persistence.checkpointer.ConnectionPool", return_value=pool), \
             patch("tripmate.persistence.checkpointer.DiagnosticPostgresSaver") as saver_cls:
            saver_cls.return_value.setup.side_effect = RuntimeError("setup failed")
            with self.assertRaisesRegex(RuntimeError, "setup failed"):
                create_checkpointer(Settings(None, "postgresql://x/db", None, None, None, None))
        pool.close.assert_called_once_with()

    def test_availability_reports_closed_pool(self):
        pool = MagicMock(closed=True)
        resources = CheckpointerResources(pool, MagicMock())
        self.assertFalse(resources.is_available())


class RoutingTests(unittest.TestCase):
    def test_selection_uses_canonical_order_and_drops_unknowns(self):
        selected = ["weather_agent", "bad_agent", "flight_agent"]
        self.assertEqual(selected_agents({"selected_agents": selected}), ["flight_agent", "weather_agent"])
        self.assertEqual(route_from_supervisor({"selected_agents": selected}), "flight_agent")
        self.assertEqual(route_after_agent("flight_agent")({"selected_agents": selected}), "weather_agent")
        self.assertEqual(route_after_agent("weather_agent")({"selected_agents": selected}), "itinerary_agent")
        self.assertEqual(len(AGENT_ORDER), 5)

    def test_guardrail_and_empty_selection_routes(self):
        self.assertEqual(route_from_guardrail({"guardrail_allowed": False}), "guardrail_blocked")
        self.assertEqual(route_from_guardrail({}), "supervisor")
        self.assertEqual(route_from_supervisor({"selected_agents": []}), "itinerary_agent")


class TravelServiceTests(unittest.TestCase):
    def test_human_approval_node_records_approval_and_revision_feedback(self):
        state = {"itinerary": "Draft", "approval_request": "Review", "selected_agents": ["flight_agent"]}
        with patch("tripmate.agents.approval.interrupt", return_value={"approved": False, "feedback": "Add museums"}) as interrupt:
            update = human_approval_agent(state)
        payload = interrupt.call_args.args[0]
        self.assertEqual(payload["draft_itinerary"], "Draft")
        self.assertEqual(update["approved"], False)
        self.assertEqual(update["human_feedback"], "Add museums")
        self.assertEqual(update["messages"][0].content, "Human approval step completed.")

    def test_resume_reuses_thread_and_passes_approval_or_revision(self):
        graph = MagicMock()
        graph.get_state.return_value.values = {"llm_calls": 4}
        graph.invoke.return_value = {"final_response": "done", "approved": True}
        service = TravelService(graph)
        result = service.resume("thread-1", approved=False, feedback="  Add museums  ")
        graph.get_state.assert_called_once_with({"configurable": {"thread_id": "thread-1"}})
        invocation = graph.invoke.call_args.args[0]
        self.assertEqual(invocation.resume, {"approved": False, "feedback": "Add museums"})
        self.assertEqual(graph.invoke.call_args.kwargs["config"]["configurable"]["thread_id"], "thread-1")
        self.assertEqual(result["thread_id"], "thread-1")
        self.assertEqual(result["llm_calls"], 4)

    def test_start_generates_thread_when_missing(self):
        graph = MagicMock()
        graph.invoke.return_value = {"final_response": "ready"}
        result = TravelService(graph).start("plan a trip")
        self.assertTrue(result["thread_id"].startswith("user_"))
        self.assertEqual(result["answer"], "ready")
        invocation = graph.invoke.call_args.args[0]
        self.assertEqual(
            invocation["evidence"],
            {"flight": {}, "hotel": {}, "weather": {}, "budget": {}},
        )
        self.assertEqual(len(invocation["thread_id_hash"]), 16)

    def test_resume_requires_thread_id(self):
        with self.assertRaises(BadRequestError):
            TravelService(MagicMock()).resume("", True)


class NeonIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tripmate.core.config import load_settings, PROJECT_ROOT
        cls.settings = load_settings(PROJECT_ROOT)

    def test_neon_or_postgresql_checkpointer_initializes_and_probes(self):
        if not self.settings.database_url:
            self.skipTest("DATABASE_URL unavailable")
        resources = create_checkpointer(self.settings)
        try:
            self.assertTrue(resources.is_available())
        finally:
            resources.close()


if __name__ == "__main__":
    unittest.main()
