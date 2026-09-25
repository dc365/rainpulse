import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import AdminApp from './AdminApp'
import { NewRun } from './NewRun'
import type { Plan } from './model'

const id = 'd4aeb65c-b54f-4cfb-879c-4415a9999991'
const health = { database_ready: true, worker_auth_configured: true, workers: [], counts: {}, sampled_at: '2026-09-22T15:00:00Z' }
afterEach(() => { cleanup(); sessionStorage.clear(); vi.unstubAllGlobals(); window.history.replaceState({}, '', '/admin') })
function respond(value: unknown, status = 200) { return Promise.resolve(new Response(JSON.stringify(value), { status })) }
it('requires a real credential check and displays authentication failure', async () => {
  vi.stubGlobal('fetch', vi.fn(() => respond({ code: 'unauthorized', message: '管理凭据不正确' }, 401)))
  render(<AdminApp />)
  await screen.findByLabelText('管理凭据')
  fireEvent.change(screen.getByLabelText('管理凭据'), { target: { value: 'wrong' } })
  fireEvent.click(screen.getByRole('button', { name: '进入后台' }))
  await screen.findByText('管理凭据不正确')
  expect(sessionStorage.getItem('rainpulse.ops.adminToken')).toBeNull()
})
it('enters the backend without a credential when validation mode is enabled', async () => {
  const calls: {url: string; authorization?: string}[] = []
  vi.stubGlobal('fetch', vi.fn((url: string, options: RequestInit = {}) => {
    calls.push({url, authorization: new Headers(options.headers).get('Authorization') ?? undefined})
    if (url.endsWith('/auth-mode')) return respond({mode: 'validation'})
    if (url.endsWith('/status')) return respond(health)
    return respond({items: [], next_cursor: ''})
  }))
  render(<AdminApp />)
  await screen.findByText(/验证模式：/)
  await waitFor(() => expect(calls.some(call => call.url.endsWith('/status') && call.authorization === undefined)).toBe(true))
  expect(screen.queryByLabelText('管理凭据')).toBeNull()
  expect(sessionStorage.getItem('rainpulse.ops.adminToken')).toBeNull()
})
it('shows query failure rather than presenting an empty healthy task list', async () => {
  sessionStorage.setItem('rainpulse.ops.adminToken', 'test')
  vi.stubGlobal('fetch', vi.fn((url: string) => url.endsWith('/status') ? respond(health) : respond({message: '数据库不可用'}, 503)))
  render(<AdminApp />)
  await screen.findByText(/数据库不可用/)
  expect(screen.queryByText('没有符合条件的管理作业')).toBeNull()
})
it('submits the frozen plan ID with a stable idempotency key', async () => {
  const plan: Plan = { id, run_id: id, digest: 'a'.repeat(64), created_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 900000).toISOString(), submittable: true,
    impact: 'candidate only', checks: [{code: 'ready',state: 'PASS',message: '已核对'}], tasks: [],
    selection: {preset: 'qc_preview',start: new Date(Date.now()-600000).toISOString(),end: new Date().toISOString(),radar_ids:['z9591'],name:'test'} }
  const calls: {url: string; body: unknown}[] = []
  vi.stubGlobal('fetch', vi.fn((url: string, options: RequestInit) => {
    calls.push({url, body: options.body ? JSON.parse(String(options.body)) : null})
    return url.endsWith('/submit') ? respond({run_id:id}) : respond(plan)
  }))
  const navigate = vi.fn()
  render(<NewRun token="test" navigate={navigate} />)
  fireEvent.change(screen.getByLabelText('雷达站号'), { target: {value:'z9591'} })
  fireEvent.click(screen.getByRole('button', {name:'2. 生成预检查计划'}))
  await screen.findByText('已核对')
  fireEvent.click(screen.getByRole('button', {name:'3. 确认并提交此计划'}))
  await waitFor(() => expect(navigate).toHaveBeenCalledWith({page:'tasks',run:id}))
  expect(calls[1]).toEqual({url:`/api/v1/admin/ops/plans/${id}/submit`,body:{idempotency_key:`ops-plan-${id}`}})
})
it('does not install an old preflight response after the form changes', async () => {
  let reply!: (r: Response) => void
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => { reply = resolve })))
  render(<NewRun token="test" navigate={vi.fn()} />)
  fireEvent.change(screen.getByLabelText('雷达站号'), {target:{value:'z9591'}})
  fireEvent.click(screen.getByRole('button', {name:'2. 生成预检查计划'}))
  fireEvent.change(screen.getByLabelText('雷达站号'), {target:{value:'z9598'}})
  await act(async () => reply(new Response(JSON.stringify({id,submittable:true}))))
  await screen.findByText('检查期间选择范围发生变化，请重新生成计划。')
  expect(screen.queryByRole('button', {name:'3. 确认并提交此计划'})).toBeNull()
})
