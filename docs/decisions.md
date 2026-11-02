## Human Decisions and Assumptions

- Clerk selected as the managed identity provider; internal user UUIDs are the ownership key; legacy unowned threads are denied.
- Human configuration is still required for the Clerk instance’s issuer, accepted audience, and frontend publishable key, plus production domain/CORS settings. The repository contains no existing values or deployment identity configuration to derive these from.
- Assumes authenticated users are provisioned on first request with a valid Clerk token, and that Clerk is the sole identity provider for this rollout.