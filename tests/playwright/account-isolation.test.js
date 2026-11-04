/** Account lifecycle coverage against the production React bundle.
 * Clerk and planner API traffic are mocked at the browser network boundary.
 */
const { test, expect } = require('@playwright/test');

const userA = { id: 'user_playwright_a', sessionId: 'sess_playwright_a', name: 'User A', email: 'user-a@example.test', jwt: token('user_playwright_a', 'sess_playwright_a') };
const userB = { id: 'user_playwright_b', sessionId: 'sess_playwright_b', name: 'User B', email: 'user-b@example.test', jwt: token('user_playwright_b', 'sess_playwright_b') };
const threadA = 'thread_owned_by_user_a';

function token(userId, sessionId) {
  const now = Math.floor(Date.now() / 1000);
  return `e30.${Buffer.from(JSON.stringify({ sub: userId, sid: sessionId, status: 'active', iat: now, exp: now + 3600 })).toString('base64url')}.signature`;
}

function clerkUser(account) {
  const now = Math.floor(Date.now() / 1000);
  const emailId = `email_${account.id}`;
  return {
    object: 'user', id: account.id, first_name: account.name, last_name: '', image_url: '', has_image: false,
    primary_email_address_id: emailId,
    email_addresses: [{ object: 'email_address', id: emailId, email_address: account.email, verification: { status: 'verified', strategy: 'email_code' } }],
    created_at: now, updated_at: now, username: null, public_metadata: {}, private_metadata: {}, unsafe_metadata: {},
  };
}

function clerkSession(account) {
  const now = Math.floor(Date.now() / 1000);
  return {
    object: 'session', id: account.sessionId, user_id: account.id, client_id: 'client_playwright_accounts', status: 'active',
    last_active_at: now, expire_at: now + 3600, abandon_at: now + 3600, updated_at: now, created_at: now,
    actor: null, last_active_organization_id: null, tasks: [], user: clerkUser(account),
    last_active_token: { object: 'token', jwt: account.jwt },
  };
}

function clerkClient(activeAccount) {
  return {
    object: 'client', id: 'client_playwright_accounts', sessions: [clerkSession(userA), clerkSession(userB)],
    last_active_session_id: activeAccount.sessionId, sign_in: null, sign_up: null,
  };
}

async function openAs(page, account, { bothSessions = true } = {}) {
  let active = account;
  let signedOut = false;
  const seenClerkRequests = [];
  await page.route('**/v1/**', async route => {
    const request = route.request();
    const url = request.url();
    seenClerkRequests.push({ url, method: request.method(), body: request.postData() });

    if (url.includes('/v1/client?')) {
      const client = clerkClient(active);
      if (!bothSessions) client.sessions = [clerkSession(active)];
      if (signedOut) client.sessions = [];
      return route.fulfill({ json: client });
    }
    if (url.includes('/tokens')) {
      return route.fulfill({ json: { object: 'token', jwt: signedOut ? '' : active.jwt } });
    }
    if (url.includes(userB.sessionId)) {
      active = userB;
      signedOut = false;
      return route.fulfill({ json: { object: 'session', id: userB.sessionId, status: 'active', user: clerkUser(userB), last_active_token: { object: 'token', jwt: userB.jwt } } });
    }
    if (request.method() === 'DELETE' || /\/end(?:\?|$)/.test(url) || (request.method() === 'POST' && (url.includes('/client/sessions?') && url.includes('_method=DELETE')))) {
      signedOut = true;
      return route.fulfill({ json: { object: 'session', id: active.sessionId, status: 'ended' } });
    }
    return route.fulfill({ json: {} });
  });
  await page.route('**/api/config', route => route.fulfill({ json: { clerk_publishable_key: 'pk_test_dHJpcC1tYXRlLTEuY2xlcmsuYWNjb3VudHMuZGV2JA', clerk_jwt_template: 'VoyageCrew' } }));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Go somewhere' })).toBeVisible();
  return { seenClerkRequests };
}

const responseFor = (threadId, label) => ({
  success: true, thread_id: threadId, answer: `${label} itinerary`, itinerary: `# ${label} itinerary`,
  requires_approval: false, approval_request: '', selected_agents: [], supervisor_reasoning: `${label} private routing`,
  guardrail_allowed: true, guardrail_reason: '',
});

test.describe('React production account and thread isolation', () => {
  test('account switch clears User A thread and sends User B JWT on subsequent requests', async ({ page }) => {
    const auth = await openAs(page, userA);
    await page.route('**/api/travel', async route => {
      expect(route.request().headers().authorization).toBe(`Bearer ${userA.jwt}`);
      expect(route.request().postDataJSON()).toEqual({ message: 'Plan a trip for User A.', thread_id: null });
      await route.fulfill({ json: responseFor(threadA, 'User A') });
    });
    await page.getByLabel('Travel request').fill('Plan a trip for User A.');
    await page.getByRole('button', { name: /Build my itinerary/ }).click();
    await expect(page.getByText(`Thread ID: ${threadA}`)).toBeVisible();
    await expect(page.getByText('User A private routing')).toBeVisible();
    expect(await page.evaluate(() => sessionStorage.getItem('travel_thread_id'))).toBe(threadA);

    // Clerk's UserButton exposes its native account-switch menu when multiple sessions exist.
    await page.getByRole('button', { name: 'Open user menu' }).click();
    await expect(page.getByRole('dialog', { name: 'Account panel' })).toBeVisible();
    await page.getByRole('button', { name: /user b user-b@example\.test/i }).click();

    await expect(page.getByRole('heading', { name: 'Go somewhere' })).toBeVisible();
    await expect(page.getByText(`Thread ID: ${threadA}`)).toHaveCount(0);
    await expect(page.getByText('User A private routing')).toHaveCount(0);
    expect(await page.evaluate(() => sessionStorage.getItem('travel_thread_id'))).not.toBe(threadA);

    let call = 0;
    await page.unroute('**/api/travel');
    await page.route('**/api/travel', async route => {
      const request = route.request();
      expect(request.headers().authorization).toBe(`Bearer ${userB.jwt}`);
      if (call === 0) expect(request.postDataJSON()).toEqual({ message: 'Plan a trip for User B.', thread_id: null });
      else expect(request.postDataJSON().thread_id).toBe('thread_owned_by_user_b');
      call++;
      await route.fulfill({ json: responseFor('thread_owned_by_user_b', 'User B') });
    });
    await page.getByLabel('Travel request').fill('Plan a trip for User B.');
    await page.getByRole('button', { name: /Build my itinerary/ }).click();
    await expect(page.getByText('Thread ID: thread_owned_by_user_b')).toBeVisible();
    await page.getByLabel('Travel request').fill('Continue User B plan.');
    await page.getByRole('button', { name: /Build my itinerary/ }).click();
    await expect.poll(() => call).toBe(2);
    expect(await page.evaluate(() => sessionStorage.getItem('travel_thread_id'))).toBe('thread_owned_by_user_b');
    expect(auth.seenClerkRequests.length).toBeGreaterThan(0);
  });

  test('logout clears planner UI and refresh restores authenticated identity without foreign thread data', async ({ page }) => {
    await openAs(page, userA, { bothSessions: false });
    await page.route('**/api/travel', route => route.fulfill({ json: responseFor(threadA, 'User A') }));
    await page.getByLabel('Travel request').fill('Plan a private trip.');
    await page.getByRole('button', { name: /Build my itinerary/ }).click();
    await expect(page.getByText('User A private routing')).toBeVisible();

    await page.reload();
    await expect(page.getByRole('heading', { name: 'Go somewhere' })).toBeVisible();
    await expect(page.getByText(`Thread ID: ${threadA}`)).toHaveCount(0);
    await page.getByLabel('Travel request').fill('Continue my saved trip.');
    let restoredRequest;
    await page.unroute('**/api/travel');
    await page.route('**/api/travel', async route => {
      restoredRequest = { authorization: route.request().headers().authorization, body: route.request().postDataJSON() };
      await route.fulfill({ json: responseFor('thread_after_refresh', 'Restored User A') });
    });
    const refreshRequest = page.waitForRequest(request => request.url().endsWith('/api/travel'));
    await page.getByRole('button', { name: /Build my itinerary/ }).click();
    await refreshRequest;
    await expect(page.getByText('Restored User A private routing')).toBeVisible();
    expect(restoredRequest).toEqual({
      authorization: `Bearer ${userA.jwt}`,
      body: { message: 'Continue my saved trip.', thread_id: threadA },
    });

    await page.getByRole('button', { name: 'Open user menu' }).click();
    await page.getByRole('button', { name: 'Sign out' }).click();
    await expect(page.getByRole('heading', { name: 'Your next great trip starts here.' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Go somewhere' })).toHaveCount(0);
    await expect(page.getByText(`Thread ID: ${threadA}`)).toHaveCount(0);
    await expect(page.getByText('User A private routing')).toHaveCount(0);
  });

  test('unauthorized and foreign thread API failures are shown without exposing prior plan content', async ({ page }) => {
    await openAs(page, userB, { bothSessions: false });
    await page.route('**/api/travel', async route => {
      expect(route.request().headers().authorization).toBe(`Bearer ${userB.jwt}`);
      await route.fulfill({ status: 404, json: { detail: 'Not Found' } });
    });
    await page.evaluate(() => sessionStorage.setItem('travel_thread_id', 'thread_owned_by_user_a'));
    await page.evaluate(() => sessionStorage.setItem('travel_thread_owner', 'user_playwright_a'));
    await page.getByLabel('Travel request').fill('Try to continue another user thread.');
    await page.getByRole('button', { name: /Build my itinerary/ }).click();
    await expect(page.getByRole('alert')).toHaveText('Not Found');
    await expect(page.getByText(/Thread ID:/)).toHaveCount(0);
    await expect(page.getByText(/User A private routing/)).toHaveCount(0);
  });
});
