// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { api } from './api';
import { sanitizeMarkdown } from './Planner';

describe('session API and Markdown boundary', () => {
  it('sends browser requests with cookie credentials enabled', () => {
    expect(api.defaults.withCredentials).toBe(true);
  });
  it('removes executable markup while retaining safe Markdown', () => {
    const unsafe = sanitizeMarkdown('<img src="x" onerror="alert(1)"><script>alert(2)</script>[bad](javascript:alert(3))');
    expect(unsafe).not.toMatch(/onerror|<script|javascript:/i);
    expect(sanitizeMarkdown('# Hello')).toContain('<h1');
  });
});
