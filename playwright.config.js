const { defineConfig } = require('@playwright/test');

/** Build and serve the production React bundle through the FastAPI application. */
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
    command: 'npm run build --prefix frontend && python -m uvicorn main:app --lifespan off --host 127.0.0.1 --port 8123',
    port: 8123,
    reuseExistingServer: false,
    timeout: 30000,
    env: {
      PLAYWRIGHT_PORT: '8123',
    },
  },
});
