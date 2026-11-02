import axios from 'axios';
export const api = axios.create();
export function installTokenProvider(provider: () => Promise<string | null>) {
  api.interceptors.request.clear();
  api.interceptors.request.use(async config => {
    const token = await provider();
    if (!token) throw new Error('Your session expired. Please sign in again.');
    config.headers.Authorization = `Bearer ${token}`;
    return config;
  });
}
export function apiMessage(error: unknown): string {
  if (axios.isAxiosError(error)) return error.response?.data?.error || error.response?.data?.detail || error.message;
  return error instanceof Error ? error.message : 'Something went wrong.';
}
