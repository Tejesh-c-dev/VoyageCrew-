/** XSS tests exercise the sanitizer bundled with the production React app. */
const { test, expect } = require('@playwright/test');

test.describe('React production Markdown sanitizer', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**clerk.**', route => route.abort());
    await page.route('**/api/config', route => route.fulfill({ json: { clerk_publishable_key: 'pk_test_dummy' } }));
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => typeof window.tripmateRenderMarkdown === 'function', { timeout: 15000 });
  });
  const payloads = ['<script>alert(1)</script>', '<img src="x" onerror="alert(2)"/>', '[click](javascript:alert(3))', '<svg><circle onload="alert(4)"/></svg>', '<iframe src="javascript:alert(5)"></iframe>'];
  for (const payload of payloads) test(`sanitizes ${payload}`, async ({ page }) => {
    const html = await page.evaluate(md => window.tripmateRenderMarkdown(md), payload);
    expect(html).not.toMatch(/<script|onerror|onload|onclick|javascript:|<iframe/i);
  });
  test('renders safe Markdown', async ({ page }) => {
    const html = await page.evaluate(() => window.tripmateRenderMarkdown('# Heading\n\n- Item 1\n\n[Link](https://example.com)\n\n```js\nconsole.log("test")\n```'));
    expect(html).toContain('<h1'); expect(html).toContain('<li>Item 1</li>'); expect(html).toContain('href="https://example.com"'); expect(html).toContain('<code');
  });
});
