// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { api, createClerkTokenProvider, installTokenProvider } from './api';
import { sanitizeMarkdown } from './Planner';

describe('React API and Markdown boundary', () => {
  it('adds a fresh Clerk token to the request without persisting it', async () => {
    const getToken = vi.fn(async (options: { template?: string }) => {
      expect(options).toEqual({ template: 'VoyageCrew' });
      return 'session-token';
    });
    installTokenProvider(createClerkTokenProvider(getToken, 'VoyageCrew'));
    let authorization = '';
    const originalAdapter = api.defaults.adapter;
    api.defaults.adapter = async config => {
      authorization = String(config.headers.Authorization || '');
      return { data: { success: true }, status: 200, statusText: 'OK', headers: {}, config };
    };
    try { await api.post('/api/travel', { message: 'plan', thread_id: null }); }
    finally { api.defaults.adapter = originalAdapter!; }
    expect(authorization).toBe('Bearer session-token');
    expect(getToken).toHaveBeenCalledOnce();
    expect(sessionStorage.getItem('session-token')).toBeNull();
    expect(localStorage.getItem('session-token')).toBeNull();
  });
  it('waits for the Clerk token before dispatching the protected request', async () => {
    let finishToken!: (token: string) => void;
    const getToken = vi.fn(() => new Promise<string>(resolve => { finishToken = resolve; }));
    installTokenProvider(getToken);
    let dispatched = false;
    const originalAdapter = api.defaults.adapter;
    api.defaults.adapter = async config => {
      dispatched = true;
      return { data: {}, status: 200, statusText: 'OK', headers: {}, config };
    };
    try {
      const request = api.post('/api/travel', { message: 'plan', thread_id: null });
      await Promise.resolve();
      expect(dispatched).toBe(false);
      finishToken('session-token');
      await request;
      expect(dispatched).toBe(true);
    } finally { api.defaults.adapter = originalAdapter!; }
  });
  it('removes executable markup while retaining safe Markdown', () => {
    const unsafe = sanitizeMarkdown('<img src="x" onerror="alert(1)"><script>alert(2)</script>[bad](javascript:alert(3))');
    expect(unsafe).not.toMatch(/onerror|<script|javascript:/i);
    expect(sanitizeMarkdown('# Hello')).toContain('<h1');
  });
});

