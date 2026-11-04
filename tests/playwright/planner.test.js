/** Browser coverage for the production React planner with network-only mocks. */
const { test, expect } = require('@playwright/test');

const threadId = 'user_playwright_thread_1';
const agents = ['flight_agent', 'hotel_agent', 'weather_agent', 'budget_agent', 'itinerary_agent'];
const draft = {
  success: true,
  thread_id: threadId,
  answer: 'Draft itinerary for Japan.',
  itinerary: '# Japan Draft\n\n- Day 1: Arrive in Tokyo',
  requires_approval: true,
  approval_request: 'Review this draft before finalizing.',
  selected_agents: agents,
  supervisor_reasoning: 'Flights, lodging, weather, and budget need review.',
  guardrail_allowed: true,
  guardrail_reason: '',
};
const finalPlan = {
  ...draft,
  answer: '# Japan Final Plan\n\n- Day 1: Explore Tokyo',
  itinerary: '',
  requires_approval: false,
};
const revisedDraft = {
  ...draft,
  itinerary: '# Revised Japan Draft\n\n- Day 1: Free day in Tokyo',
  approval_request: 'Review the revised draft.',
};

function clerkFixtures() {
  const now = Math.floor(Date.now() / 1000);
  const sessionId = 'sess_playwright_1';
  const userId = 'user_playwright_1';
  const jwt = `e30.${Buffer.from(JSON.stringify({ sub: userId, sid: sessionId, status: 'active', iat: now, exp: now + 3600 })).toString('base64url')}.signature`;
  const user = {
    object: 'user', id: userId, first_name: 'Playwright', last_name: 'Tester',
    image_url: '', has_image: false, primary_email_address_id: 'email_playwright_1',
    email_addresses: [{ object: 'email_address', id: 'email_playwright_1', email_address: 'planner@example.test', verification: { status: 'verified', strategy: 'email_code' } }],
    created_at: now, updated_at: now, username: null, public_metadata: {}, private_metadata: {}, unsafe_metadata: {},
  };
  const session = {
    object: 'session', id: sessionId, user_id: userId, client_id: 'client_playwright_1', status: 'active',
    last_active_at: now, expire_at: now + 3600, abandon_at: now + 3600, updated_at: now, created_at: now,
    actor: null, last_active_organization_id: null, tasks: [], user,
    last_active_token: { object: 'token', jwt },
  };
  return { jwt, client: { object: 'client', id: 'client_playwright_1', sessions: [session], last_active_session_id: sessionId, sign_in: null, sign_up: null } };
}

async function openPlanner(page, context) {
  const { client, jwt } = clerkFixtures();
  await page.route('**/v1/**', route => {
    const url = route.request().url();
    if (url.includes('/v1/client?')) return route.fulfill({ json: client });
    if (url.includes('/tokens')) return route.fulfill({ json: { object: 'token', jwt } });
    return route.fulfill({ json: {} });
  });
  await page.route('**/api/config', route => route.fulfill({ json: { clerk_publishable_key: 'pk_test_dHJpcC1tYXRlLTEuY2xlcmsuYWNjb3VudHMuZGV2JA', clerk_jwt_template: 'VoyageCrew' } }));
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Go somewhere' })).toBeVisible();
}

async function submitDraft(page, response = draft) {
  await page.getByLabel('Travel request').fill('Plan a week in Japan under 2 lakhs.');
  await page.route('**/api/travel', route => route.fulfill({ json: response }));
  await page.getByRole('button', { name: /Build my itinerary/ }).click();
  await expect(page.getByRole('heading', { name: 'Draft Travel Plan' })).toBeVisible();
}

test.describe('React production planner', () => {
  test('keeps the planner usable on desktop and a narrow mobile viewport', async ({ page, context }) => {
    await openPlanner(page, context);
    await expect(page.getByRole('link', { name: 'Planner' })).toBeVisible();
    await expect(page.getByLabel('Travel request')).toBeVisible();
    const desktop = await page.evaluate(() => ({ viewport: document.documentElement.clientWidth, content: document.documentElement.scrollWidth }));
    expect(desktop.content).toBeLessThanOrEqual(desktop.viewport);
    await page.setViewportSize({ width: 375, height: 812 });
    await expect(page.getByLabel('Travel request')).toBeVisible();
    await expect(page.getByRole('button', { name: /Build my itinerary/ })).toBeVisible();
    const mobile = await page.evaluate(() => ({ viewport: document.documentElement.clientWidth, content: document.documentElement.scrollWidth }));
    expect(mobile.content).toBeLessThanOrEqual(mobile.viewport);
  });

  test('submits a request, displays supervisor routing and draft, then approves the final itinerary', async ({ page, context }) => {
    await openPlanner(page, context);
    await page.getByLabel('Travel request').fill('Plan a week in Japan under 2 lakhs.');
    await page.route('**/api/travel', async route => {
      expect(route.request().postDataJSON()).toEqual({ message: 'Plan a week in Japan under 2 lakhs.', thread_id: null });
      await route.fulfill({ json: draft });
    });
    await page.getByRole('button', { name: /Build my itinerary/ }).click();

    await expect(page.getByText('Flights, lodging, weather, and budget need review.')).toBeVisible();
    for (const agent of ['Flight Agent', 'Hotel Agent', 'Weather Agent', 'Budget Agent', 'Itinerary Agent'])
      await expect(page.getByText(new RegExp(agent))).toBeVisible();
    await expect(page.locator('.result-box')).toContainText('Day 1: Arrive in Tokyo');
    await expect(page.getByText('Review this draft before finalizing.')).toBeVisible();

    let finishApproval;
    const approvalResponse = new Promise(resolve => { finishApproval = resolve; });
    await page.route('**/api/travel/approve', async route => {
      expect(route.request().postDataJSON()).toEqual({ thread_id: threadId, approved: true, feedback: '' });
      await approvalResponse;
      await route.fulfill({ json: finalPlan });
    });
    await page.getByRole('button', { name: 'Approve & Generate Final' }).click();
    await expect(page.getByRole('status')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Approve & Generate Final' })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Revise Using Feedback' })).toBeDisabled();
    finishApproval();
    await expect(page.getByRole('heading', { name: 'Your Final AI Travel Plan' })).toBeVisible();
    await expect(page.locator('.result-box')).toContainText('Day 1: Explore Tokyo');
    await expect(page.getByRole('heading', { name: 'Review the draft itinerary' })).toHaveCount(0);
  });

  test('revises a draft with feedback and sends the existing thread id', async ({ page, context }) => {
    await openPlanner(page, context);
    await submitDraft(page);
    await page.getByLabel('Revision feedback').fill('Add a free day and lower hotel costs.');
    await page.route('**/api/travel/approve', async route => {
      expect(route.request().postDataJSON()).toEqual({ thread_id: threadId, approved: false, feedback: 'Add a free day and lower hotel costs.' });
      await route.fulfill({ json: revisedDraft });
    });
    await page.getByRole('button', { name: 'Revise Using Feedback' }).click();
    await expect(page.locator('.result-box')).toContainText('Free day in Tokyo');
    await expect(page.getByLabel('Revision feedback')).toHaveValue('');
    await expect(page.getByText('Review the revised draft.')).toBeVisible();
  });

  test('validates empty travel requests and empty revision feedback', async ({ page, context }) => {
    await openPlanner(page, context);
    await page.getByRole('button', { name: /Build my itinerary/ }).click();
    await expect(page.getByRole('alert')).toHaveText('Please enter your travel request first.');

    await submitDraft(page);
    await page.getByRole('button', { name: 'Revise Using Feedback' }).click();
    await expect(page.getByRole('alert')).toHaveText('Please enter revision feedback before requesting changes.');
  });

  test('shows API error responses and failed network requests', async ({ page, context }) => {
    await openPlanner(page, context);
    await page.getByLabel('Travel request').fill('Plan a Japan trip.');
    await page.route('**/api/travel', route => route.fulfill({ status: 503, json: { detail: 'Planner service is unavailable.' } }));
    await page.getByRole('button', { name: /Build my itinerary/ }).click();
    await expect(page.getByRole('alert')).toHaveText('Planner service is unavailable.');

    await page.unroute('**/api/travel');
    await page.route('**/api/travel', route => route.abort('failed'));
    await page.getByRole('button', { name: /Build my itinerary/ }).click();
    await expect(page.getByRole('alert')).toHaveText('Network Error');
  });

  test('shows approval API errors and keeps the draft available to retry', async ({ page, context }) => {
    await openPlanner(page, context);
    await submitDraft(page);
    await page.route('**/api/travel/approve', route => route.fulfill({ status: 500, json: { error: 'Could not approve this draft.' } }));
    await page.getByRole('button', { name: 'Approve & Generate Final' }).click();
    await expect(page.getByRole('alert')).toHaveText('Could not approve this draft.');
    await expect(page.getByRole('heading', { name: 'Draft Travel Plan' })).toBeVisible();
  });

  test('prevents duplicate submissions while the request is pending', async ({ page, context }) => {
    await openPlanner(page, context);
    await page.getByLabel('Travel request').fill('Plan a Japan trip.');
    let requestCount = 0;
    let releaseResponse;
    const responseGate = new Promise(resolve => { releaseResponse = resolve; });
    await page.route('**/api/travel', async route => {
      requestCount++;
      await responseGate;
      await route.fulfill({ json: draft });
    });
    await page.getByRole('button', { name: /Build my itinerary/ }).click();
    const generateButton = page.getByRole('button', { name: /Planning your trip/ });
    await expect(generateButton).toBeDisabled();
    await expect(page.getByRole('status')).toBeVisible();
    await page.keyboard.press('Control+Enter');
    await page.waitForTimeout(100);
    expect(requestCount).toBe(1);
    releaseResponse();
    await expect(page.getByRole('heading', { name: 'Draft Travel Plan' })).toBeVisible();
    expect(requestCount).toBe(1);
  });

  test('copies itinerary text and downloads a PDF with the expected filename', async ({ page, context }) => {
    await openPlanner(page, context);
    await submitDraft(page);
    await page.getByRole('button', { name: 'Copy' }).click();
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toContain('Day 1: Arrive in Tokyo');

    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Download PDF' }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe('ai-travel-plan.pdf');
  });
});
