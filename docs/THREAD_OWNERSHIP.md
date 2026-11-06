# Accounts, sessions, and thread ownership

TripMate provides open email/password signup and login. Passwords are stored as Argon2id hashes. The API issues random opaque session tokens in an `HttpOnly`, `SameSite=Lax` cookie; PostgreSQL stores only a SHA-256 digest of each token and its expiry. Sessions expire after 14 days and are revoked on logout. State-changing requests reject cross-site browser submissions.

The account and session tables are created by the additive startup migration in `tripmate/persistence/migrations/0001_thread_ownership.sql`. Each account has an internal UUID, which is also the owner key in `tripmate_threads`. Both planning and approval requests require a valid session and verify ownership before invoking the workflow. Unknown and foreign thread IDs both return 404.

Existing Clerk identities are not imported. Existing ownership rows remain intact, but those old accounts cannot sign in and their threads are not attached to new accounts.
