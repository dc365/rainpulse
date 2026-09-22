const prefix = '/api/v1/admin/ops';
export class AdminError extends Error {
    constructor(public status: number, public code: string, message: string) { super(message); }
}
async function response(token: string, path: string, options: RequestInit = {}) { const r = await fetch(prefix + path, { ...options, cache: 'no-store', headers: { 'Authorization': `Bearer ${token}`, ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } }); if (!r.ok) {
    const v = await r.json().catch(() => ({}));
    throw new AdminError(r.status, v.code ?? 'request_failed', v.message ?? `请求失败（${r.status}）`);
} return r; }
export async function read<T>(token: string, path: string, signal?: AbortSignal): Promise<T> { return (await response(token, path, { signal })).json() as Promise<T>; }
export async function post<T>(token: string, path: string, value: unknown): Promise<T> { return (await response(token, path, { method: 'POST', body: JSON.stringify(value) })).json() as Promise<T>; }
export async function asset(token: string, task: string, key: string, signal?: AbortSignal) { return (await response(token, `/tasks/${task}/asset?key=${encodeURIComponent(key)}`, { signal })).blob(); }
export function failure(error: unknown) { return error instanceof Error ? error.message : '读取失败，请重试'; }
