import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psycopg

from tripmate.api.exception_handlers import handle_database_error
from tripmate.core.logging import StructuredLogFormatter


class FakeOperationalError(psycopg.OperationalError):
    @property
    def sqlstate(self):
        return "23505"

    @property
    def diag(self):
        return SimpleNamespace(
            message_primary="duplicate key value violates unique constraint",
            table_name="tripmate_users",
            constraint_name="tripmate_users_issuer_subject_key",
            message_detail="sensitive subject value",
        )


class DatabaseDiagnosticTests(unittest.TestCase):
    def test_handler_logs_safe_postgres_diagnostics_and_preserves_response(self):
        request = MagicMock()
        request.state.correlation_id = "4cecbee6-bb29-494c-bcc2-daa1b983982d"
        exception = FakeOperationalError(
            "sensitive token, connection password, and SQL parameters"
        )
        exception._tripmate_operation = "identity_user_upsert"
        exception._tripmate_phase = "clerk_identity_lookup"
        captured = {}

        def capture(_message, *, extra):
            captured.update(extra)

        with patch("tripmate.api.exception_handlers.logger.error", side_effect=capture):
            response = asyncio.run(handle_database_error(request, exception))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            json.loads(response.body),
            {
                "success": False,
                "error": "The database is temporarily unavailable.",
            },
        )
        self.assertEqual(response.headers["X-Request-ID"], request.state.correlation_id)

        record = MagicMock()
        record.created = 0
        record.levelname = "ERROR"
        record.name = "tripmate.api.exception_handlers"
        for key, value in captured.items():
            setattr(record, key, value)
        # The handler establishes correlation context while logging; the
        # structured formatter serializes only its allowlisted fields.
        from tripmate.core.logging import correlation_context

        with correlation_context(request.state.correlation_id):
            payload = json.loads(StructuredLogFormatter().format(record))

        self.assertEqual(payload["correlation_id"], request.state.correlation_id)
        self.assertEqual(payload["operation"], "identity_user_upsert")
        self.assertEqual(payload["phase"], "clerk_identity_lookup")
        self.assertEqual(payload["error_type"], "FakeOperationalError")
        self.assertEqual(payload["sqlstate"], "23505")
        self.assertEqual(
            payload["message_primary"],
            "duplicate key value violates unique constraint",
        )
        self.assertEqual(payload["table_name"], "tripmate_users")
        self.assertEqual(
            payload["constraint_name"], "tripmate_users_issuer_subject_key"
        )
        serialized = json.dumps(payload)
        for sensitive in (
            "sensitive subject value",
            "sensitive token",
            "connection password",
            "SQL parameters",
            "message_detail",
        ):
            self.assertNotIn(sensitive, serialized)

    def test_connection_error_diagnostic_fields_are_null(self):
        request = MagicMock()
        request.state.correlation_id = None
        exception = psycopg.OperationalError("connection failed")
        captured = {}

        with patch(
            "tripmate.api.exception_handlers.logger.error",
            side_effect=lambda _message, *, extra: captured.update(extra),
        ):
            asyncio.run(handle_database_error(request, exception))

        self.assertIsNone(captured["sqlstate"])
        self.assertIsNone(captured["message_primary"])
        self.assertIsNone(captured["table_name"])
        self.assertIsNone(captured["constraint_name"])


if __name__ == "__main__":
    unittest.main()
