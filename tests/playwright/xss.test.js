/**
 * XSS Protection — Playwright browser tests
 *
 * These tests load the REAL production page (served by serve_static.py) and
 * call the renderMarkdown() function defined by /static/script.js.
 * No inline re-implementation — the exact same sanitisation path as production.
 *
 * Library versions pinned in templates/index.html:
 *   marked    4.3.0  (cdn.jsdelivr.net/npm/marked@4.3.0)
 *   DOMPurify 3.0.5  (cdnjs, SRI-verified sha512-KqUc2WMPF/…)
 *
 * CDN requests are intercepted and served from local node_modules to make
 * tests fast and network-independent.
 */

const { test, expect } = require('@playwright/test');
const path = require('path');
const fs = require('fs');

// Paths to locally installed library files (installed in package.json devDeps)
const PROJECT_ROOT = path.resolve(__dirname, '..', '..');
const LOCAL_MARKED = path.join(PROJECT_ROOT, 'node_modules', 'marked', 'marked.min.js');
const LOCAL_PURIFY = path.join(PROJECT_ROOT, 'node_modules', 'dompurify', 'dist', 'purify.min.js');

/**
 * Navigate to the production page.
 *
 * Before navigating, set up route interceptions so that:
 *   1. CDN requests for marked and DOMPurify are served from local node_modules
 *      (fast, no internet required, same pinned versions as production)
 *   2. Clerk JS requests (pointing to clerk.invalid) are aborted immediately
 *      so they don't consume timeout
 */
async function loadProductionPage(page) {
  // Serve marked locally (avoids CDN round-trip / potential 404)
  await page.route('**/npm/marked@*/**', async (route) => {
    const body = fs.readFileSync(LOCAL_MARKED);
    await route.fulfill({ status: 200, contentType: 'application/javascript', body });
  });

  // Serve DOMPurify locally (avoids CDN round-trip)
  await page.route('**/dompurify**/purify.min.js', async (route) => {
    const body = fs.readFileSync(LOCAL_PURIFY);
    await route.fulfill({ status: 200, contentType: 'application/javascript', body });
  });

  // Abort Clerk requests (dummy domain in test server config)
  await page.route('**clerk.invalid**', (route) => route.abort());
  await page.route('**/npm/@clerk/**', (route) => route.abort());

  // Navigate; don't wait for networkidle (Clerk requests are aborted, not idle)
  await page.goto('/', { waitUntil: 'domcontentloaded' });

  // Wait for CDN libraries to be available in the window
  await page.waitForFunction(
    () => typeof marked !== 'undefined' && typeof DOMPurify !== 'undefined',
    { timeout: 10000 }
  );

  // Wait for renderMarkdown defined by /static/script.js (deferred load)
  await page.waitForFunction(
    () => typeof renderMarkdown === 'function',
    { timeout: 10000 }
  );
}

/**
 * Invoke the real production renderMarkdown() from /static/script.js.
 * Returns the innerHTML produced after sanitisation.
 */
async function renderAndGetHtml(page, markdown) {
  return await page.evaluate((md) => {
    const el = document.createElement('div');
    // renderMarkdown is the global from /static/script.js — NOT reimplemented here
    renderMarkdown(el, md);
    return el.innerHTML;
  }, markdown);
}

// ---------------------------------------------------------------------------

test.describe('XSS Protection — production renderMarkdown()', () => {
  const maliciousPayloads = [
    '<script>alert(1)</script>',
    '<img src="x" onerror="alert(2)"/>',
    '[click me](javascript:alert(3))',
    '<svg><circle onload="alert(4)"/></svg>',
    '<iframe src="javascript:alert(5)"></iframe>',
  ];

  test.beforeEach(async ({ page }) => {
    await loadProductionPage(page);
  });

  // --- Malicious payload tests --------------------------------------------

  for (const payload of maliciousPayloads) {
    test(`sanitizes payload: ${payload}`, async ({ page }) => {
      const html = await renderAndGetHtml(page, payload);

      expect(html).not.toContain('<script');
      expect(html).not.toMatch(/\bonerror\b/i);
      expect(html).not.toMatch(/\bonload\b/i);
      expect(html).not.toMatch(/\bonclick\b/i);
      expect(html).not.toMatch(/javascript:/i);
      expect(html).not.toContain('<iframe');
    });
  }

  // --- Safe markdown rendering --------------------------------------------

  test('renders valid markdown correctly', async ({ page }) => {
    const markdown = [
      '# Heading',
      '',
      '- Item 1',
      '- Item 2',
      '',
      '[Link](https://example.com)',
      '',
      '```js',
      'console.log("test");',
      '```',
    ].join('\n');

    const html = await renderAndGetHtml(page, markdown);

    expect(html).toContain('<h1');
    expect(html).toContain('<li>Item 1</li>');
    expect(html).toContain('<a href="https://example.com"');
    expect(html).toContain('<code');
  });
});
