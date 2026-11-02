"""Regression coverage for Clerk authentication and API thread isolation."""

import json
import threading
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

import main
from tripmate.api import auth
from tripmate.core.config import Settings
from tripmate.core.exceptions import AuthenticationError, ThreadNotFoundError


ISSUER = "https://clerk.tripmate.test"
AUDIENCE = "tripmate-api"


def make_token(private_key, **claims):
    now = datetime.now(timezone.utc)
    token_claims = {
        "iss": ISSUER,
        "sub": "user_clerk_a",
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    token_claims.update(claims)
    return jwt.encode(
        token_claims,
        private_key,
        algorithm="RS256",
        headers={"kid": "tripmate-test-key"},
    )


class JWKSFixture:
    """Serve a real RSA public key over HTTP for JWT integration tests."""

    def __init__(self):
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_numbers = self.private_key.public_key().public_numbers()
        self.jwk = {
            "kty": "RSA",
            "use": "sig",
            "alg": "RS256",
            "kid": "tripmate-test-key",
            "n": jwt.utils.base64url_encode(
                public_numbers.n.to_bytes((public_numbers.n.bit_length() + 7) // 8, "big")
            ).decode(),
            "e": jwt.utils.base64url_encode(
                public_numbers.e.to_bytes((public_numbers.e.bit_length() + 7) // 8, "big")
            ).decode(),
        }
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/.well-known/jwks.json":
                    self.send_error(404)
                    return
                payload = json.dumps({"keys": [fixture.jwk]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def issuer(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        auth._jwks_client.cache_clear()


class AuthenticationVerificationTests(unittest.TestCase):
    def setUp(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.settings = Settings(None, None, None, None, None, None, clerk_issuer=ISSUER, clerk_audience=AUDIENCE)
        auth._jwks_client.cache_clear()
        self.client = MagicMock()
        self.client.get_signing_key_from_jwt.return_value.key = self.key.public_key()

    def verify(self, token, settings=None):
        with patch.object(auth, "_jwks_client", return_value=self.client):
            return auth.verify_clerk_token(token, settings or self.settings)

    def assert_rejected(self, authorization, settings=None):
        with self.assertRaises(AuthenticationError):
            self.verify(authorization, settings)

    def test_missing_bearer_token_is_rejected(self):
        self.assert_rejected(None)

    def test_malformed_jwt_is_rejected(self):
        self.assert_rejected("Bearer not.a.jwt")

    def test_invalid_signature_is_rejected(self):
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.assert_rejected("Bearer " + make_token(other_key))

    def test_expired_token_is_rejected(self):
        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        self.assert_rejected("Bearer " + make_token(self.key, exp=past, iat=past - timedelta(minutes=1)))

    def test_incorrect_issuer_is_rejected(self):
        self.assert_rejected("Bearer " + make_token(self.key, iss="https://wrong.test"))

    def test_incorrect_audience_is_rejected(self):
        self.assert_rejected("Bearer " + make_token(self.key, aud="wrong-audience"))

    def test_valid_clerk_rs256_token_authenticates(self):
        identity = self.verify("Bearer " + make_token(self.key))
        self.assertEqual(identity.issuer, ISSUER)
        self.assertEqual(identity.subject, "user_clerk_a")

    def test_unavailable_jwks_fails_safely(self):
        self.client.get_signing_key_from_jwt.side_effect = RuntimeError("network details")
        with self.assertRaises(AuthenticationError):
            self.verify("Bearer " + make_token(self.key))

    def test_real_rs256_jwt_is_verified_against_http_jwks(self):
        with JWKSFixture() as jwks:
            settings = Settings(None, None, None, None, None, None, clerk_issuer=jwks.issuer, clerk_audience=AUDIENCE)
            # The fixture's issuer is the JWKS URL issuer; sign matching claims.
            token = make_token(jwks.private_key, iss=jwks.issuer)
            identity = auth.verify_clerk_token("Bearer " + token, settings)
            self.assertEqual(identity, auth.VerifiedIdentity(jwks.issuer, "user_clerk_a"))


class MemoryOwnership:
    """Small ownership store for exercising HTTP authorization ordering."""

    users = {}
    threads = {}

    def __init__(self, _pool):
        pass

    def get_or_create_user(self, identity):
        return self.users.setdefault((identity.issuer, identity.subject), uuid4())

    def create_thread(self, thread_id, owner_id):
        if thread_id in self.threads:
            raise AssertionError("thread ownership overwrite attempted")
        self.threads[thread_id] = owner_id

    def require_owner(self, thread_id, owner_id):
        if self.threads.get(thread_id) != owner_id:
            raise ThreadNotFoundError()


class ThreadOwnershipApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jwks = JWKSFixture()
        cls.jwks.__enter__()
        cls.tokens = {
            "A": make_token(cls.jwks.private_key, iss=cls.jwks.issuer, sub="user_A"),
            "B": make_token(cls.jwks.private_key, iss=cls.jwks.issuer, sub="user_B"),
        }

    @classmethod
    def tearDownClass(cls):
        cls.jwks.__exit__()

    def setUp(self):
        MemoryOwnership.users = {}
        MemoryOwnership.threads = {}
        self.resources = MagicMock()
        self.resources.pool = object()
        self.service = MagicMock()
        self.service.start.side_effect = lambda **kwargs: {"thread_id": kwargs["thread_id"], "answer": "created"}
        self.service.resume.side_effect = lambda **kwargs: {"thread_id": kwargs["thread_id"], "answer": "resumed"}
        self.previous_lifespan = main.app.router.lifespan_context

        @asynccontextmanager
        async def no_lifespan(_app):
            yield

        main.app.router.lifespan_context = no_lifespan
        main.app.state.settings = Settings(
            None, None, None, None, None, None,
            clerk_issuer=self.jwks.issuer,
            clerk_audience=AUDIENCE,
        )
        main.app.state.checkpointer_resources = self.resources
        main.app.state.travel_service = self.service
        self.store_patch = patch("tripmate.api.dependencies.ThreadOwnershipStore", MemoryOwnership)
        self.route_patch = patch("tripmate.api.routes.travel.ThreadOwnershipStore", MemoryOwnership)
        self.store_patch.start()
        self.route_patch.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()
        self.store_patch.stop()
        self.route_patch.stop()
        main.app.router.lifespan_context = self.previous_lifespan
        for name in ("settings", "checkpointer_resources", "travel_service"):
            if hasattr(main.app.state, name):
                delattr(main.app.state, name)

    def headers(self, user):
        return {"Authorization": f"Bearer {self.tokens[user]}"}

    def create_thread(self):
        response = self.client.post("/api/travel", headers=self.headers("A"), json={"message": "Plan a trip"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["thread_id"]

    def test_user_can_create_access_continue_approve_and_resume_own_thread(self):
        thread_id = self.create_thread()
        owner_id = MemoryOwnership.users[(self.jwks.issuer, "user_A")]
        self.assertIsInstance(owner_id, UUID)
        self.assertEqual(MemoryOwnership.threads[thread_id], owner_id)

        continued = self.client.post("/api/travel", headers=self.headers("A"), json={"message": "Continue", "thread_id": thread_id})
        approved = self.client.post("/api/travel/approve", headers=self.headers("A"), json={"thread_id": thread_id, "approved": True})
        self.assertEqual(continued.status_code, 200)
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(self.service.start.call_count, 2)
        self.assertEqual(self.service.start.call_args_list[1].kwargs["thread_id"], thread_id)
        self.service.resume.assert_called_once_with(thread_id=thread_id, approved=True, feedback="")

    def test_user_b_cannot_access_approve_or_resume_user_a_thread(self):
        thread_id = self.create_thread()
        calls_before = (self.service.start.call_count, self.service.resume.call_count)
        access = self.client.post("/api/travel", headers=self.headers("B"), json={"message": "take over", "thread_id": thread_id})
        approve = self.client.post("/api/travel/approve", headers=self.headers("B"), json={"thread_id": thread_id, "approved": True})
        self.assertEqual((access.status_code, approve.status_code), (404, 404))
        self.assertEqual((self.service.start.call_count, self.service.resume.call_count), calls_before)
        self.assertEqual(MemoryOwnership.threads[thread_id], MemoryOwnership.users[(self.jwks.issuer, "user_A")])

    def test_foreign_unknown_and_legacy_threads_have_identical_responses(self):
        foreign_id = self.create_thread()
        legacy_id = "legacy-checkpoint-without-owner"
        unknown_id = "unknown-thread"
        # These checkpoint-only IDs intentionally have no ownership row.
        responses = [
            self.client.post("/api/travel", headers=self.headers("B"), json={"message": "x", "thread_id": thread_id})
            for thread_id in (foreign_id, unknown_id, legacy_id)
        ]
        self.assertEqual(len({(r.status_code, r.content) for r in responses}), 1)
        self.assertEqual(responses[0].status_code, 404)
        self.assertEqual(self.service.start.call_count, 1)

    def test_user_b_cannot_approve_or_resume_foreign_thread(self):
        thread_id = self.create_thread()
        before = self.service.resume.call_count
        response = self.client.post("/api/travel/approve", headers=self.headers("B"), json={"thread_id": thread_id, "approved": True})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.service.resume.call_count, before)

    def test_unauthenticated_requests_never_invoke_workflow(self):
        for response in (
            self.client.post("/api/travel", json={"message": "x", "thread_id": "anything"}),
            self.client.post("/api/travel/approve", json={"thread_id": "anything", "approved": True}),
        ):
            self.assertEqual(response.status_code, 401)
        self.service.start.assert_not_called()
        self.service.resume.assert_not_called()

    def test_client_supplied_user_email_and_local_identity_do_not_set_ownership(self):
        response = self.client.post(
            "/api/travel",
            headers=self.headers("A"),
            json={"message": "Plan", "user_id": str(uuid4()), "email": "attacker@example.test", "owner_id": str(uuid4())},
        )
        self.assertEqual(response.status_code, 200)
        thread_id = response.json()["thread_id"]
        authenticated_id = MemoryOwnership.users[(self.jwks.issuer, "user_A")]
        self.assertEqual(MemoryOwnership.threads[thread_id], authenticated_id)
        self.assertEqual(len(MemoryOwnership.users), 1)

    def test_server_identity_code_does_not_read_browser_storage_or_email(self):
        root = Path(__file__).resolve().parents[1]
        server_auth = "\n".join(
            (root / relative).read_text(encoding="utf-8")
            for relative in (
                "tripmate/api/auth.py",
                "tripmate/api/dependencies.py",
                "tripmate/persistence/ownership.py",
                "tripmate/api/routes/travel.py",
            )
        )
        self.assertNotIn("localStorage", server_auth)
        self.assertNotIn("email", server_auth.lower())
        self.assertIn("verify_clerk_token(authorization, settings)", server_auth)

    def test_malformed_or_invalid_tokens_return_401_without_workflow(self):
        for token in ("garbage", "Bearer a.b.c"):
            response = self.client.post("/api/travel", headers={"Authorization": token}, json={"message": "x"})
            self.assertEqual(response.status_code, 401)
        self.service.start.assert_not_called()
        self.service.resume.assert_not_called()


if __name__ == "__main__":
    unittest.main()
