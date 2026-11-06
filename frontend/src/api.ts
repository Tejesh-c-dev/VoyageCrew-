import axios from 'axios';
export const api = axios.create();
api.defaults.withCredentials = true;
export function apiMessage(error: unknown): string {
  if (axios.isAxiosError(error)) return error.response?.data?.error || error.response?.data?.detail || error.message;
  return error instanceof Error ? error.message : 'Something went wrong.';
}
