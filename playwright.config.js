const { defineConfig } = require('@playwright/test');

/**
 * Playwright configuration.
 *
 * webServer: starts a minimal static server (no DB, no auth) that serves
 *   - GET /        → templates/index.html with dummy Clerk placeholders
 *   - GET /static/ → /static/ directory (includes script.js, style.css)
 *
 * This ensures tests load the REAL production JS (marked 4.5.0, DOMPurify
 * 3.0.5, and renderMarkdown from script.js) without requiring PostgreSQL.
 *
 * Pinned library versions (verified in templates/index.html):
 *   marked    4.5.0  cdn.jsdelivr.net/npm/marked@4.5.0/marked.min.js
 *   DOMPurify 3.0.5  cdnjs (SRI: sha512-KqUc2WMPF/…)
 */
module.exports = defineConfig({
  testDir: './tests/playwright',
  timeout: 30000,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:8123',
    headless: true,
    ignoreHTTPSErrors: true,
  },
  webServer: {
    command: 'python tests/playwright/serve_static.py',
    port: 8123,
    reuseExistingServer: false,
    timeout: 30000,
    env: {
      PLAYWRIGHT_PORT: '8123',
    },
  },
});
