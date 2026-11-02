# Clerk authentication and thread ownership

The API requires a Clerk session bearer token. Configure these environment
values before starting the application:

- `CLERK_ISSUER`: exact HTTPS `iss` value emitted by the Clerk session token.
- `CLERK_AUDIENCE`: audience configured for that token and required by the API.
- `CLERK_PUBLISHABLE_KEY`: Clerk frontend publishable key.
- `CLERK_FRONTEND_API_URL`: HTTPS Clerk Frontend API origin, for example
  `https://example.clerk.accounts.dev`.

The browser obtains a short-lived session token from ClerkJS and sends it in the
`Authorization: Bearer` header. The backend verifies its RS256 signature using
Clerk's JWKS, then maps `(issuer, subject)` to an internal UUID.

Startup applies the additive migration in
`tripmate/persistence/migrations/0001_thread_ownership.sql`. It creates only
`tripmate_users` and `tripmate_threads`; LangGraph checkpoint tables are
unchanged. Threads without an ownership record remain inaccessible through the
travel API. Unknown and foreign thread IDs both return 404.
