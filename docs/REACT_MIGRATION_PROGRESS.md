# React frontend migration progress

## Delivered

- Added a standalone React 19 and TypeScript app in `frontend/`, built with Vite.
- Integrated Clerk React sign-in and user controls. The browser fetches only the publishable key from `/api/config`; session tokens are requested for each API call and kept in memory.
- Recreated trip prompts, loading/errors, workflow routing and guardrail state, draft approval/revision, final results, Markdown rendering, copy, and A4 PDF export.
- Kept backend endpoints and request/response contracts unchanged. FastAPI serves `frontend/dist` at `/`, serves its assets at `/assets`, and applies an SPA fallback after API and health routes.
- Added a Node build stage to Docker and tests that exercise the production bundle's Markdown sanitizer.
- Retained `templates/index.html`, `static/script.js`, and `static/style.css` during migration.

## Verification

- `npm run build --prefix frontend` — passed.
- `npm test --prefix frontend` — passed (2 tests: request token handling and Markdown sanitization).
- `npm run test:playwright` — passed (6 tests against the production React bundle, including unsafe and safe Markdown).
- `python -m unittest discover -s tests` — passed (239 tests, 2 skipped).
- Clerk sign-in, database, and live travel-provider flows were not exercised; Playwright uses a dummy Clerk publishable key and tests only the browser sanitizer.

## Follow-up

Expand Playwright coverage for mocked sign-in, planner submission, approval/revision, copy/PDF, and account switching before removing the legacy page and static assets. Build and smoke-test the Docker image in a container runtime before deployment.
