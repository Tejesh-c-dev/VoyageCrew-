// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { api, installTokenProvider } from './api';
import { sanitizeMarkdown } from './Planner';

describe('React API and Markdown boundary', () => {
  it('adds a fresh Clerk token to the request without persisting it', async () => {
    const getToken = vi.fn().mockResolvedValue('session-token');
    installTokenProvider(getToken);
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
  it('removes executable markup while retaining safe Markdown', () => {
    const unsafe = sanitizeMarkdown('<img src="x" onerror="alert(1)"><script>alert(2)</script>[bad](javascript:alert(3))');
    expect(unsafe).not.toMatch(/onerror|<script|javascript:/i);
    expect(sanitizeMarkdown('# Hello')).toContain('<h1');
  });
});

