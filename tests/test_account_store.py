"""Focused account/session behavior checks using a small SQL-shaped fake."""

import unittest
from uuid import uuid4

from tripmate.api.auth import AccountStore, _token_hash
from tripmate.core.exceptions import AuthenticationError, BadRequestError


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.row = None

    def __enter__(self): return self
    def __exit__(self, *_): return False

    def execute(self, query, params=()):
        if query.startswith("INSERT INTO tripmate_users"):
            self.row = {"id": uuid4()}
        elif query.startswith("INSERT INTO tripmate_accounts"):
            user_id, email, password_hash = params
            if email in self.db.accounts:
                self.row = None
            else:
                self.db.accounts[email] = (user_id, password_hash)
                self.row = {"user_id": user_id}
        elif query.startswith("SELECT user_id AS id"):
            account = self.db.accounts.get(params[0])
            self.row = {"id": account[0], "password_hash": account[1]} if account else None
        elif query.startswith("INSERT INTO tripmate_sessions"):
            digest, user_id, _expiry = params
            self.db.sessions[digest] = user_id
            self.row = None
        elif query.startswith("SELECT user_id FROM tripmate_sessions"):
            self.row = {"user_id": self.db.sessions[params[0]]} if params[0] in self.db.sessions else None
        elif query.startswith("DELETE FROM tripmate_sessions"):
            self.db.sessions.pop(params[0], None)

    def fetchone(self): return self.row


class FakeConnection:
    def __init__(self, db): self.db = db
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def transaction(self): return self
    def cursor(self): return FakeCursor(self.db)


class FakePool:
    def __init__(self): self.accounts = {}; self.sessions = {}
    def connection(self): return FakeConnection(self)


class AccountStoreTests(unittest.TestCase):
    def setUp(self):
        self.pool = FakePool()
        self.store = AccountStore(self.pool)

    def test_signup_duplicate_login_and_logout(self):
        user_id, token = self.store.signup(" PERSON@example.com ", "long-password")
        self.assertEqual(self.store.user_for_session(token), user_id)
        self.assertIn(_token_hash(token), self.pool.sessions)
        with self.assertRaises(BadRequestError):
            self.store.signup("person@example.com", "long-password")
        login_id, login_token = self.store.login("person@example.com", "long-password")
        self.assertEqual(login_id, user_id)
        with self.assertRaises(AuthenticationError):
            self.store.login("person@example.com", "incorrect-password")
        self.store.revoke(login_token)
        with self.assertRaises(AuthenticationError):
            self.store.user_for_session(login_token)

    def test_expired_or_unknown_session_is_rejected(self):
        with self.assertRaises(AuthenticationError):
            self.store.user_for_session("unknown-session")

    def test_signup_rejects_invalid_email_and_short_password(self):
        with self.assertRaises(BadRequestError):
            self.store.signup("invalid", "long-password")
        with self.assertRaises(BadRequestError):
            self.store.signup("person@example.com", "short")


if __name__ == "__main__":
    unittest.main()
