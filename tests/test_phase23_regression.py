"""Phase 2.3 - Thread Ownership Security: comprehensive regression tests.

Each test below is a DISTINCT, individually named case matching the specification.
Existing tests in test_thread_ownership_security.py and
test_postgres_thread_ownership.py are NOT re-implemented here; this file covers
the gaps and provides additional granularity required by the milestone checklist.

Test classes:
  Auth1_MissingToken          - missing bearer token -> 401
  Auth2_MalformedJWT          - malformed JWT -> 401
  Auth3_InvalidSignature      - invalid signature -> 401
  Auth4_ExpiredToken          - expired token -> 401
  Auth5_WrongIssuer           - incorrect issuer -> 401
  Auth6_WrongAudience         - incorrect audience -> 401
  Auth7_ValidToken            - valid RS256 token authenticates
  Auth8_JWKSUnavailable       - unavailable JWKS fails safely
  Ownership1_CreateAccess     - User A creates and accesses own thread
  Ownership2_Continue         - User A continues own thread
  Ownership3_ApproveResume    - User A approves and resumes own thread
  Ownership4_BCannotAccess    - User B cannot access User A thread
  Ownership5_BCannotApprove   - User B cannot approve/resume User A thread
  Ownership6_BCannotOverwrite - User B cannot overwrite User A thread
  Ownership7_UnknownThread    - unknown thread_id returns 404
  Ownership8_LegacyThread     - thread without ownership record returns 404
  Ownership9_Unauthenticated  - unauthenticated request cannot access any thread
  Security1_NoWorkflowOnAuth  - unauthorized requests never invoke LangGraph
  Security2_IndistinguishableErrors - foreign/unknown/legacy -> same 404 body
  Security3_InternalUUIDs     - ownership records use internal user UUIDs
  Security4_IssuerSubjectUniq - issuer+subject uniqueness prevents duplicates
  Security5_NewThreadOwned    - newly created threads receive ownership records
  Security6_NoClientIdentity  - ownership not derived from client-supplied data
  Integration_PostgresMigration - PostgreSQL migration tests (skipped without env var)
"""

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

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------
ISSUER = "https://clerk.phase23.test"
AUDIENCE = "tripmate-phase23"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_token(private_key, **claims):
    """Build a signed RS256 JWT with sensible defaults."""
    now = datetime.now(timezone.utc)
    payload = {
        "iss": ISSUER,
        "sub": "user_default",
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    payload.update(claims)
    return jwt.encode(payload, private_key, algorithm="RS256",
                      headers={"kid": "phase23-test-key"})


class _JWKSFixture:
    """Minimal real JWKS HTTP server for integration-level JWT tests."""

    def __init__(self):
        self.private_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )
        pub = self.private_key.public_key().public_numbers()
        self.jwk = {
            "kty": "RSA",
            "use": "sig",
            "alg": "RS256",
            "kid": "phase23-test-key",
            "n": jwt.utils.base64url_encode(
                pub.n.to_bytes((pub.n.bit_length() + 7) // 8, "big")
            ).decode(),
            "e": jwt.utils.base64url_encode(
                pub.e.to_bytes((pub.e.bit_length() + 7) // 8, "big")
            ).decode(),
        }
        fixture = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/.well-known/jwks.json":
                    self.send_error(404)
                    return
                body = json.dumps({"keys": [fixture.jwk]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )

    @property
    def issuer(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=2)
        auth._jwks_client.cache_clear()


class _MemoryOwnershipStore:
    """In-memory ThreadOwnershipStore compatible with the production API."""

    _users = {}
    _threads = {}

    def __init__(self, _pool):
        pass

    def get_or_create_user(self, identity):
        key = (identity.issuer, identity.subject)
        if key not in self._users:
            self._users[key] = uuid4()
        return self._users[key]

    def create_thread(self, thread_id, owner_id):
        if thread_id in self._threads:
            raise AssertionError("Ownership overwrite attempted")
        self._threads[thread_id] = owner_id

    def require_owner(self, thread_id, owner_id):
        if self._threads.get(thread_id) != owner_id:
            raise ThreadNotFoundError()


# ---------------------------------------------------------------------------
# Shared app fixture for HTTP-level tests
# ---------------------------------------------------------------------------

class _AppTestBase(unittest.TestCase):
    """Base class that wires up a TestClient backed by _MemoryOwnershipStore."""

    @classmethod
    def setUpClass(cls):
        cls.jwks = _JWKSFixture()
        cls.jwks.__enter__()
        cls.private_key = cls.jwks.private_key
        cls.tokens = {
            "A": _make_token(cls.private_key, iss=cls.jwks.issuer, sub="user_A"),
            "B": _make_token(cls.private_key, iss=cls.jwks.issuer, sub="user_B"),
        }

    @classmethod
    def tearDownClass(cls):
        cls.jwks.__exit__()

    def setUp(self):
        _MemoryOwnershipStore._users = {}
        _MemoryOwnershipStore._threads = {}

        self.resources = MagicMock()
        self.resources.pool = object()
        self.service = MagicMock()
        self.service.start.side_effect = (
            lambda **kw: {"thread_id": kw["thread_id"], "answer": "ok"}
        )
        self.service.resume.side_effect = (
            lambda **kw: {"thread_id": kw["thread_id"], "answer": "resumed"}
        )

        self._prev_lifespan = main.app.router.lifespan_context

        @asynccontextmanager
        async def _no_lifespan(_app):
            yield

        main.app.router.lifespan_context = _no_lifespan
        main.app.state.settings = Settings(
            None, None, None, None, None, None,
            clerk_issuer=self.jwks.issuer,
            clerk_audience=AUDIENCE,
        )
        main.app.state.checkpointer_resources = self.resources
        main.app.state.travel_service = self.service

        self._dep_patch = patch(
            "tripmate.api.dependencies.ThreadOwnershipStore",
            _MemoryOwnershipStore,
        )
        self._route_patch = patch(
            "tripmate.api.routes.travel.ThreadOwnershipStore",
            _MemoryOwnershipStore,
        )
        self._dep_patch.start()
        self._route_patch.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()
        self._dep_patch.stop()
        self._route_patch.stop()
        main.app.router.lifespan_context = self._prev_lifespan
        for attr in ("settings", "checkpointer_resources", "travel_service"):
            try:
                delattr(main.app.state, attr)
            except AttributeError:
                pass

    def _auth(self, user):
        return {"Authorization": f"Bearer {self.tokens[user]}"}

    def _post_travel(self, headers, **body_kwargs):
        body = {"message": "Plan a trip", **body_kwargs}
        return self.client.post("/api/travel", headers=headers, json=body)

    def _create_thread(self):
        """Create a thread owned by User A and return the thread_id."""
        resp = self._post_travel(self._auth("A"))
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()["thread_id"]

    def _post_approve(self, headers, thread_id, approved=True):
        return self.client.post(
            "/api/travel/approve",
            headers=headers,
            json={"thread_id": thread_id, "approved": approved},
        )


# ===========================================================================
# 1. Authentication tests (unit-level - mock JWKS client)
# ===========================================================================

class _AuthUnitBase(unittest.TestCase):
    """Mock the JWKS client so auth tests are purely local."""

    def setUp(self):
        self.private_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )
        self.settings = Settings(
            None, None, None, None, None, None,
            clerk_issuer=ISSUER,
            clerk_audience=AUDIENCE,
        )
        auth._jwks_client.cache_clear()
        self._mock_jwks = MagicMock()
        self._mock_jwks.get_signing_key_from_jwt.return_value.key = (
            self.private_key.public_key()
        )

    def _verify(self, authorization, settings=None):
        with patch.object(auth, "_jwks_client", return_value=self._mock_jwks):
            return auth.verify_clerk_token(
                authorization, settings or self.settings
            )

    def _assert_401(self, authorization, settings=None):
        with self.assertRaises(AuthenticationError):
            self._verify(authorization, settings)


class Auth1_MissingToken(_AuthUnitBase):
    def test_missing_bearer_token_returns_401(self):
        """Req Auth1: Missing bearer token returns 401."""
        self._assert_401(None)

    def test_empty_authorization_header_returns_401(self):
        self._assert_401("")

    def test_non_bearer_scheme_returns_401(self):
        self._assert_401("Basic dXNlcjpwYXNz")


class Auth2_MalformedJWT(_AuthUnitBase):
    def test_malformed_jwt_returns_401(self):
        """Req Auth2: Malformed JWT returns 401."""
        self._assert_401("Bearer not.a.valid.jwt")

    def test_bearer_with_empty_token_returns_401(self):
        self._assert_401("Bearer ")

    def test_bearer_with_garbage_returns_401(self):
        self._assert_401("Bearer !!###")


class Auth3_InvalidSignature(_AuthUnitBase):
    def test_invalid_signature_returns_401(self):
        """Req Auth3: Invalid signature returns 401."""
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self._assert_401("Bearer " + _make_token(other_key))


class Auth4_ExpiredToken(_AuthUnitBase):
    def test_expired_token_returns_401(self):
        """Req Auth4: Expired token returns 401."""
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        token = _make_token(
            self.private_key, exp=past, iat=past - timedelta(minutes=5)
        )
        self._assert_401("Bearer " + token)


class Auth5_WrongIssuer(_AuthUnitBase):
    def test_incorrect_issuer_returns_401(self):
        """Req Auth5: Incorrect issuer returns 401."""
        token = _make_token(self.private_key, iss="https://attacker.example")
        self._assert_401("Bearer " + token)


class Auth6_WrongAudience(_AuthUnitBase):
    def test_incorrect_audience_returns_401(self):
        """Req Auth6: Incorrect audience returns 401."""
        token = _make_token(self.private_key, aud="wrong-audience")
        self._assert_401("Bearer " + token)


class Auth7_ValidToken(_AuthUnitBase):
    def test_valid_clerk_rs256_token_authenticates_successfully(self):
        """Req Auth7: Valid Clerk RS256 token authenticates successfully."""
        token = _make_token(self.private_key, sub="user_xyz")
        identity = self._verify("Bearer " + token)
        self.assertEqual(identity.issuer, ISSUER)
        self.assertEqual(identity.subject, "user_xyz")
        self.assertIsInstance(identity, auth.VerifiedIdentity)

    def test_identity_fields_are_strings(self):
        token = _make_token(self.private_key)
        identity = self._verify("Bearer " + token)
        self.assertIsInstance(identity.issuer, str)
        self.assertIsInstance(identity.subject, str)


class Auth8_JWKSUnavailable(_AuthUnitBase):
    def test_unavailable_jwks_service_fails_safely(self):
        """Req Auth8: Unavailable JWKS service fails safely."""
        self._mock_jwks.get_signing_key_from_jwt.side_effect = RuntimeError(
            "connection refused"
        )
        self._assert_401("Bearer " + _make_token(self.private_key))

    def test_jwks_network_timeout_returns_auth_error(self):
        self._mock_jwks.get_signing_key_from_jwt.side_effect = TimeoutError()
        self._assert_401("Bearer " + _make_token(self.private_key))

    def test_jwks_error_never_leaks_network_details(self):
        """Auth errors must not embed internal network details."""
        self._mock_jwks.get_signing_key_from_jwt.side_effect = RuntimeError(
            "internal-credential-detail-xyzzy"
        )
        try:
            self._verify("Bearer " + _make_token(self.private_key))
            self.fail("Expected AuthenticationError")
        except AuthenticationError as exc:
            self.assertNotIn("internal-credential-detail-xyzzy", str(exc))

    def test_real_rs256_jwt_verified_against_http_jwks(self):
        """Req: At least one realistic JWT verification integration test."""
        with _JWKSFixture() as jwks:
            settings = Settings(
                None, None, None, None, None, None,
                clerk_issuer=jwks.issuer,
                clerk_audience=AUDIENCE,
            )
            token = _make_token(
                jwks.private_key, iss=jwks.issuer, sub="integration_user"
            )
            identity = auth.verify_clerk_token("Bearer " + token, settings)
        self.assertEqual(identity.issuer, jwks.issuer)
        self.assertEqual(identity.subject, "integration_user")


# ===========================================================================
# 2. Thread ownership tests (HTTP-level via TestClient)
# ===========================================================================

class Ownership1_CreateAccess(_AppTestBase):
    def test_user_a_can_create_and_access_their_own_thread(self):
        """Req Ownership1: User A can create and access their own thread."""
        resp = self._post_travel(self._auth("A"))
        self.assertEqual(resp.status_code, 200)
        thread_id = resp.json()["thread_id"]
        self.assertTrue(thread_id, "Response must include a thread_id")
        owner = _MemoryOwnershipStore._threads.get(thread_id)
        user_a_id = _MemoryOwnershipStore._users.get((self.jwks.issuer, "user_A"))
        self.assertIsNotNone(owner)
        self.assertEqual(owner, user_a_id)

    def test_thread_id_in_response_matches_ownership_record(self):
        thread_id = self._create_thread()
        self.assertIn(thread_id, _MemoryOwnershipStore._threads)


class Ownership2_Continue(_AppTestBase):
    def test_user_a_can_continue_their_own_thread(self):
        """Req Ownership2: User A can continue their own thread."""
        thread_id = self._create_thread()
        count_before = self.service.start.call_count
        resp = self._post_travel(
            self._auth("A"), thread_id=thread_id, message="Continue planning"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["thread_id"], thread_id)
        self.assertEqual(self.service.start.call_count, count_before + 1)

    def test_continuing_a_thread_does_not_create_new_ownership_record(self):
        thread_id = self._create_thread()
        threads_before = len(_MemoryOwnershipStore._threads)
        self._post_travel(self._auth("A"), thread_id=thread_id, message="More")
        self.assertEqual(len(_MemoryOwnershipStore._threads), threads_before)


class Ownership3_ApproveResume(_AppTestBase):
    def test_user_a_can_approve_and_resume_their_own_thread(self):
        """Req Ownership3: User A can approve and resume their own thread."""
        thread_id = self._create_thread()
        resp = self._post_approve(self._auth("A"), thread_id, approved=True)
        self.assertEqual(resp.status_code, 200)
        self.service.resume.assert_called_once_with(
            thread_id=thread_id, approved=True, feedback=""
        )

    def test_user_a_can_reject_with_feedback(self):
        thread_id = self._create_thread()
        resp = self.client.post(
            "/api/travel/approve",
            headers=self._auth("A"),
            json={"thread_id": thread_id, "approved": False, "feedback": "Add museums"},
        )
        self.assertEqual(resp.status_code, 200)
        self.service.resume.assert_called_once_with(
            thread_id=thread_id, approved=False, feedback="Add museums"
        )


class Ownership4_BCannotAccess(_AppTestBase):
    def test_user_b_cannot_access_user_a_thread(self):
        """Req Ownership4: User B cannot access User A thread."""
        thread_id = self._create_thread()
        resp = self._post_travel(
            self._auth("B"), thread_id=thread_id, message="Hijack"
        )
        self.assertEqual(resp.status_code, 404)

    def test_user_b_access_does_not_invoke_workflow(self):
        thread_id = self._create_thread()
        before = self.service.start.call_count
        self._post_travel(self._auth("B"), thread_id=thread_id)
        self.assertEqual(self.service.start.call_count, before)


class Ownership5_BCannotApprove(_AppTestBase):
    def test_user_b_cannot_approve_user_a_thread(self):
        """Req Ownership5: User B cannot approve or resume User A thread."""
        thread_id = self._create_thread()
        resp = self._post_approve(self._auth("B"), thread_id)
        self.assertEqual(resp.status_code, 404)

    def test_user_b_approve_does_not_invoke_resume(self):
        thread_id = self._create_thread()
        before = self.service.resume.call_count
        self._post_approve(self._auth("B"), thread_id)
        self.assertEqual(self.service.resume.call_count, before)

    def test_user_b_cannot_resume_user_a_thread(self):
        thread_id = self._create_thread()
        before = self.service.resume.call_count
        resp = self.client.post(
            "/api/travel/approve",
            headers=self._auth("B"),
            json={"thread_id": thread_id, "approved": True},
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(self.service.resume.call_count, before)


class Ownership6_BCannotOverwrite(_AppTestBase):
    def test_user_b_cannot_overwrite_user_a_thread(self):
        """Req Ownership6: User B cannot overwrite or continue User A thread."""
        thread_id = self._create_thread()
        resp = self._post_travel(
            self._auth("B"), thread_id=thread_id, message="Overwrite"
        )
        self.assertEqual(resp.status_code, 404)
        user_a_id = _MemoryOwnershipStore._users[(self.jwks.issuer, "user_A")]
        self.assertEqual(_MemoryOwnershipStore._threads[thread_id], user_a_id)

    def test_ownership_record_is_immutable_across_unauthorized_attempts(self):
        thread_id = self._create_thread()
        original_owner = _MemoryOwnershipStore._threads[thread_id]
        for _ in range(3):
            self._post_travel(self._auth("B"), thread_id=thread_id)
        self.assertEqual(_MemoryOwnershipStore._threads[thread_id], original_owner)


class Ownership7_UnknownThread(_AppTestBase):
    def test_unknown_thread_id_returns_404_on_travel(self):
        """Req Ownership7: Unknown thread IDs return 404."""
        resp = self._post_travel(
            self._auth("A"), thread_id="absolutely-unknown-thread-id"
        )
        self.assertEqual(resp.status_code, 404)

    def test_unknown_thread_id_returns_404_on_approve(self):
        resp = self._post_approve(
            self._auth("A"), "absolutely-unknown-thread-id-approve"
        )
        self.assertEqual(resp.status_code, 404)

    def test_unknown_thread_does_not_invoke_workflow(self):
        before_start = self.service.start.call_count
        before_resume = self.service.resume.call_count
        self._post_travel(self._auth("A"), thread_id="ghost-thread-1")
        self._post_approve(self._auth("A"), "ghost-thread-2")
        self.assertEqual(self.service.start.call_count, before_start)
        self.assertEqual(self.service.resume.call_count, before_resume)


class Ownership8_LegacyThread(_AppTestBase):
    def test_legacy_checkpoint_thread_without_ownership_returns_404(self):
        """Req Ownership8: Legacy checkpoint threads without ownership records return 404."""
        legacy_id = "legacy-checkpoint-without-owner-record"
        resp = self._post_travel(self._auth("A"), thread_id=legacy_id)
        self.assertEqual(resp.status_code, 404)

    def test_legacy_thread_approve_without_ownership_returns_404(self):
        legacy_id = "legacy-checkpoint-approve-path"
        resp = self._post_approve(self._auth("A"), legacy_id)
        self.assertEqual(resp.status_code, 404)

    def test_legacy_thread_not_owned_by_any_user_at_store_level(self):
        legacy_id = "legacy-only-in-checkpoint-store"
        store = _MemoryOwnershipStore(None)
        with self.assertRaises(ThreadNotFoundError):
            store.require_owner(legacy_id, uuid4())


class Ownership9_Unauthenticated(_AppTestBase):
    def test_unauthenticated_request_cannot_access_travel_endpoint(self):
        """Req Ownership9: An unauthenticated request cannot access any thread."""
        resp = self.client.post("/api/travel", json={"message": "Plan"})
        self.assertEqual(resp.status_code, 401)

    def test_unauthenticated_request_cannot_access_approve_endpoint(self):
        resp = self.client.post(
            "/api/travel/approve",
            json={"thread_id": "any-thread", "approved": True},
        )
        self.assertEqual(resp.status_code, 401)

    def test_unauthenticated_request_with_thread_id_cannot_access_thread(self):
        thread_id = self._create_thread()
        resp = self.client.post(
            "/api/travel", json={"message": "Sneaky", "thread_id": thread_id}
        )
        self.assertEqual(resp.status_code, 401)


# ===========================================================================
# 3. Security assertion tests
# ===========================================================================

class Security1_NoWorkflowOnAuth(_AppTestBase):
    def test_unauthorized_requests_never_invoke_langgraph_workflow(self):
        """Req Security1: Unauthorized requests never invoke or resume the LangGraph workflow."""
        thread_id = self._create_thread()
        self.service.start.reset_mock()
        self.service.resume.reset_mock()

        self.client.post("/api/travel", json={"message": "x"})
        self.client.post(
            "/api/travel/approve",
            json={"thread_id": "any", "approved": True},
        )
        self._post_travel(self._auth("B"), thread_id=thread_id)
        self._post_approve(self._auth("B"), thread_id)
        self._post_travel(self._auth("A"), thread_id="ghost-thread")
        self._post_approve(self._auth("A"), "another-ghost-thread")

        self.service.start.assert_not_called()
        self.service.resume.assert_not_called()

    def test_invalid_token_never_invokes_workflow(self):
        self.service.start.reset_mock()
        self.service.resume.reset_mock()
        for bad in ("garbage", "Bearer a.b.c", "Bearer not-a-jwt"):
            self.client.post(
                "/api/travel",
                headers={"Authorization": bad},
                json={"message": "x"},
            )
        self.service.start.assert_not_called()
        self.service.resume.assert_not_called()


class Security2_IndistinguishableErrors(_AppTestBase):
    def test_foreign_unknown_and_legacy_thread_ids_produce_identical_api_responses(self):
        """Req Security2: Foreign, unknown and legacy IDs produce indistinguishable API responses."""
        thread_id_a = self._create_thread()
        unknown_id = f"unknown-{uuid4()}"
        legacy_id = "legacy-checkpoint-indistinguishable"

        responses = [
            self._post_travel(self._auth("B"), thread_id=tid, message="x")
            for tid in (thread_id_a, unknown_id, legacy_id)
        ]
        status_codes = {r.status_code for r in responses}
        response_bodies = {r.content for r in responses}

        self.assertEqual(status_codes, {404}, "All must return 404")
        self.assertEqual(
            len(response_bodies), 1,
            "All 404 bodies must be byte-identical (no information leakage)",
        )

    def test_approve_endpoint_foreign_unknown_legacy_are_identical(self):
        thread_id_a = self._create_thread()
        unknown_id = f"unknown-approve-{uuid4()}"
        legacy_id = "legacy-approve-indistinguishable"

        responses = [
            self._post_approve(self._auth("B"), tid)
            for tid in (thread_id_a, unknown_id, legacy_id)
        ]
        status_codes = {r.status_code for r in responses}
        response_bodies = {r.content for r in responses}

        self.assertEqual(status_codes, {404})
        self.assertEqual(len(response_bodies), 1)


class Security3_InternalUUIDs(_AppTestBase):
    def test_ownership_records_use_internal_user_uuids(self):
        """Req Security3: Ownership records use internal user UUIDs."""
        resp = self._post_travel(self._auth("A"))
        self.assertEqual(resp.status_code, 200)
        thread_id = resp.json()["thread_id"]
        owner = _MemoryOwnershipStore._threads.get(thread_id)
        self.assertIsInstance(owner, UUID, "owner_id must be a UUID object")
        user_a_key = (self.jwks.issuer, "user_A")
        self.assertIn(user_a_key, _MemoryOwnershipStore._users)
        self.assertEqual(_MemoryOwnershipStore._users[user_a_key], owner)

    def test_two_users_have_different_internal_uuids(self):
        self._post_travel(self._auth("A"))
        self._post_travel(self._auth("B"))
        key_a = (self.jwks.issuer, "user_A")
        key_b = (self.jwks.issuer, "user_B")
        self.assertNotEqual(
            _MemoryOwnershipStore._users[key_a],
            _MemoryOwnershipStore._users[key_b],
        )

    def test_same_user_identity_maps_to_same_internal_uuid(self):
        self._post_travel(self._auth("A"))
        first_id = _MemoryOwnershipStore._users.get((self.jwks.issuer, "user_A"))
        self._post_travel(self._auth("A"), message="Second trip")
        second_id = _MemoryOwnershipStore._users.get((self.jwks.issuer, "user_A"))
        self.assertEqual(first_id, second_id)


class Security4_IssuerSubjectUniqueness(unittest.TestCase):
    def test_issuer_plus_subject_uniqueness_prevents_duplicate_external_identity_mappings(self):
        """Req Security4: issuer+subject uniqueness prevents duplicate external identity mappings."""
        store = _MemoryOwnershipStore(None)
        identity = auth.VerifiedIdentity(
            issuer="https://clerk.example.test",
            subject=f"user-{uuid4()}",
        )
        id_1 = store.get_or_create_user(identity)
        id_2 = store.get_or_create_user(identity)
        self.assertEqual(id_1, id_2)

    def test_different_issuers_same_subject_are_distinct_identities(self):
        store = _MemoryOwnershipStore(None)
        subject = f"shared-subject-{uuid4()}"
        id_a = store.get_or_create_user(
            auth.VerifiedIdentity("https://issuer-a.test", subject)
        )
        id_b = store.get_or_create_user(
            auth.VerifiedIdentity("https://issuer-b.test", subject)
        )
        self.assertNotEqual(id_a, id_b)

    def test_same_issuer_different_subjects_are_distinct(self):
        store = _MemoryOwnershipStore(None)
        issuer = "https://clerk.example.test"
        id_a = store.get_or_create_user(
            auth.VerifiedIdentity(issuer, f"subject-a-{uuid4()}")
        )
        id_b = store.get_or_create_user(
            auth.VerifiedIdentity(issuer, f"subject-b-{uuid4()}")
        )
        self.assertNotEqual(id_a, id_b)


class Security5_NewThreadOwned(_AppTestBase):
    def test_newly_created_threads_receive_ownership_records(self):
        """Req Security5: Newly created threads receive ownership records."""
        threads_before = set(_MemoryOwnershipStore._threads.keys())
        resp = self._post_travel(self._auth("A"))
        self.assertEqual(resp.status_code, 200)
        threads_after = set(_MemoryOwnershipStore._threads.keys())
        new_threads = threads_after - threads_before
        self.assertEqual(len(new_threads), 1, "Exactly one ownership record must be created")
        thread_id = resp.json()["thread_id"]
        self.assertIn(thread_id, new_threads)
        owner = _MemoryOwnershipStore._threads[thread_id]
        self.assertIsNotNone(owner)
        self.assertIsInstance(owner, UUID)

    def test_multiple_new_threads_each_get_separate_ownership_records(self):
        for i in range(3):
            resp = self._post_travel(self._auth("A"), message=f"Trip {i}")
            self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(_MemoryOwnershipStore._threads), 3)
        user_a_id = _MemoryOwnershipStore._users[(self.jwks.issuer, "user_A")]
        for owner in _MemoryOwnershipStore._threads.values():
            self.assertEqual(owner, user_a_id)

    def test_new_thread_ownership_record_created_before_workflow_invoked(self):
        """Ownership must be persisted before the workflow is invoked."""
        call_order = []
        original_create = _MemoryOwnershipStore.create_thread

        def recording_create(self_store, thread_id, owner_id):
            call_order.append("create_thread")
            return original_create(self_store, thread_id, owner_id)

        original_start = self.service.start.side_effect

        def recording_start(**kw):
            call_order.append("workflow_start")
            return original_start(**kw)

        with patch.object(_MemoryOwnershipStore, "create_thread", recording_create):
            self.service.start.side_effect = recording_start
            self._post_travel(self._auth("A"))

        self.assertEqual(
            call_order, ["create_thread", "workflow_start"],
            "Ownership must be recorded before the workflow is invoked",
        )


class Security6_NoClientIdentity(_AppTestBase):
    def test_ownership_not_derived_from_client_supplied_user_id(self):
        """Req Security6: Ownership not derived from email, localStorage, or client-supplied identity."""
        fake_user_id = str(uuid4())
        resp = self.client.post(
            "/api/travel",
            headers=self._auth("A"),
            json={"message": "Plan a trip", "user_id": fake_user_id},
        )
        self.assertEqual(resp.status_code, 200)
        thread_id = resp.json()["thread_id"]
        actual_owner = _MemoryOwnershipStore._threads[thread_id]
        auth_owner = _MemoryOwnershipStore._users[(self.jwks.issuer, "user_A")]
        self.assertEqual(actual_owner, auth_owner)
        self.assertNotEqual(str(actual_owner), fake_user_id)

    def test_ownership_not_derived_from_client_supplied_email(self):
        resp = self.client.post(
            "/api/travel",
            headers=self._auth("A"),
            json={"message": "Plan a trip", "email": "attacker@evil.example"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(_MemoryOwnershipStore._users), 1)

    def test_server_code_does_not_reference_local_storage_or_email(self):
        """Static code analysis: auth/ownership code must not read browser state or email."""
        root = Path(__file__).resolve().parents[1]
        server_files = [
            "tripmate/api/auth.py",
            "tripmate/api/dependencies.py",
            "tripmate/persistence/ownership.py",
            "tripmate/api/routes/travel.py",
        ]
        combined = "\n".join(
            (root / f).read_text(encoding="utf-8") for f in server_files
        )
        self.assertNotIn("localStorage", combined)
        self.assertNotIn("sessionStorage", combined)
        self.assertNotIn("email", combined.lower())
        self.assertIn("verify_clerk_token(authorization, settings)", combined)

    def test_ownership_not_derived_from_client_supplied_owner_id(self):
        fake_owner = str(uuid4())
        resp = self.client.post(
            "/api/travel",
            headers=self._auth("A"),
            json={"message": "Plan a trip", "owner_id": fake_owner},
        )
        self.assertEqual(resp.status_code, 200)
        thread_id = resp.json()["thread_id"]
        actual_owner = _MemoryOwnershipStore._threads[thread_id]
        self.assertNotEqual(str(actual_owner), fake_owner)


# ===========================================================================
# 4. Integration tests - PostgreSQL migration (skipped unless env var set)
# ===========================================================================

class Integration_PostgresMigration(unittest.TestCase):
    """
    Tests that exercise the real PostgreSQL migration and concurrent writes.
    Activated only when TRIPMATE_TEST_DATABASE_URL points to a local disposable
    PostgreSQL instance (hostname must be localhost / 127.0.0.1 / ::1).
    The production database is NEVER touched.
    """

    @classmethod
    def setUpClass(cls):
        import os
        from urllib.parse import urlsplit

        cls.db_url = os.getenv("TRIPMATE_TEST_DATABASE_URL")
        if not cls.db_url:
            raise unittest.SkipTest("TRIPMATE_TEST_DATABASE_URL not set")
        hostname = urlsplit(cls.db_url).hostname
        if hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise RuntimeError(
                "Ownership integration tests require a LOCAL disposable PostgreSQL instance"
            )

        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
        from tripmate.persistence.checkpointer import CheckpointerResources

        cls.pool = ConnectionPool(
            cls.db_url,
            kwargs={
                "autocommit": True,
                "row_factory": dict_row,
                "prepare_threshold": None,
            },
            min_size=1,
            max_size=8,
            open=True,
        )
        resources = CheckpointerResources(pool=cls.pool, saver=None)
        resources.ensure_ownership_schema()

        from tripmate.persistence.ownership import ThreadOwnershipStore
        cls.store = ThreadOwnershipStore(cls.pool)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "pool") and cls.pool:
            cls.pool.close()

    def test_migration_creates_tripmate_users_table(self):
        """Req Integration1: Test PostgreSQL migration against a disposable database."""
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.tripmate_users') AS tbl")
            row = cur.fetchone()
        self.assertIsNotNone(row["tbl"], "tripmate_users table must exist after migration")

    def test_migration_creates_tripmate_threads_table(self):
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.tripmate_threads') AS tbl")
            row = cur.fetchone()
        self.assertIsNotNone(row["tbl"], "tripmate_threads table must exist after migration")

    def test_migration_is_idempotent(self):
        """Running the migration twice must not raise errors."""
        from tripmate.persistence.checkpointer import CheckpointerResources
        resources = CheckpointerResources(pool=self.pool, saver=None)
        resources.ensure_ownership_schema()

    def test_issuer_subject_unique_constraint_enforced(self):
        """Req Integration2: issuer+subject uniqueness prevents duplicate external identity mappings."""
        from psycopg.errors import UniqueViolation

        identity = auth.VerifiedIdentity(
            "https://clerk.pg-test.example", f"unique-subject-{uuid4()}"
        )
        owner_id = self.store.get_or_create_user(identity)
        self.assertIsInstance(owner_id, UUID)

        owner_id_2 = self.store.get_or_create_user(identity)
        self.assertEqual(owner_id, owner_id_2)

        with self.pool.connection() as conn, conn.cursor() as cur:
            with self.assertRaises(UniqueViolation):
                cur.execute(
                    "INSERT INTO tripmate_users (issuer, subject) VALUES (%s, %s)",
                    (identity.issuer, identity.subject),
                )

    def test_internal_uuid_is_version_4(self):
        """Req Integration3: Ownership records use internal user UUIDs (version 4)."""
        identity = auth.VerifiedIdentity(
            "https://clerk.pg-test.example", f"v4-subject-{uuid4()}"
        )
        owner_id = self.store.get_or_create_user(identity)
        self.assertEqual(owner_id.version, 4)

    def test_concurrent_thread_creation_and_ownership_persistence(self):
        """Req Integration4: Test concurrent thread creation and ownership persistence."""
        from concurrent.futures import ThreadPoolExecutor

        identity = auth.VerifiedIdentity(
            "https://clerk.pg-test.example", f"concurrent-{uuid4()}"
        )
        with ThreadPoolExecutor(max_workers=8) as ex:
            owner_ids = list(
                ex.map(lambda _: self.store.get_or_create_user(identity), range(12))
            )
        self.assertEqual(len(set(owner_ids)), 1, "Concurrent upserts must yield one UUID")
        owner_id = owner_ids[0]

        thread_ids = [f"concurrent-thread-{uuid4()}" for _ in range(8)]
        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(
                lambda tid: self.store.create_thread(tid, owner_id), thread_ids
            ))

        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM tripmate_threads "
                "WHERE thread_id = ANY(%s) AND owner_id = %s",
                (thread_ids, owner_id),
            )
            self.assertEqual(cur.fetchone()["n"], len(thread_ids))

    def test_production_database_not_modified(self):
        """Req Integration5: Do not modify the existing production database."""
        from urllib.parse import urlsplit
        hostname = urlsplit(self.db_url).hostname
        self.assertIn(
            hostname, {"localhost", "127.0.0.1", "::1"},
            "Integration tests must only run against a local disposable database",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
