// Run against the repository's real React/Vitest dependencies. These are
// integration UI cases, not replacements for the pure-model Node regression.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { DataInventory } from './DataInventory'
import { Workers } from './Workers'
import { Journal } from './LogViews'
import { Performance } from './Performance'
import { NewRun } from './NewRun'

const id = '10000000-0000-0000-0000-000000000001'
const at = '2026-09-23T00:00:00Z'
const pool = { kind: 'qc', mode: 'ACCEPTING', revision: 4, updated_at: at, actor: 'administrator', reason: '', registered: 1, fresh: 1, ready: 1, active: 1, stalled: 0, queued: 2, oldest_queued_at: at }
const response = (value: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(value), { status }))
afterEach(() => { cleanup(); sessionStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('does not enumerate data until a bounded query is explicitly submitted', async () => {
  const fetcher = vi.fn((url: string) => response(url.includes('/summary?') ? { items: [], sampled_at: at } : { items: [], next_cursor: '', sampled_at: at }))
  vi.stubGlobal('fetch', fetcher)
  render(<DataInventory token="test" navigate={vi.fn()} />)
  expect(fetcher).not.toHaveBeenCalled()
  fireEvent.change(screen.getByLabelText('站号'), { target: { value: 'z9591' } })
  fireEvent.change(screen.getByLabelText('阶段'), { target: { value: 'qc_missing' } })
  fireEvent.click(screen.getByRole('button', { name: '查询' }))
  await waitFor(() => expect(fetcher.mock.calls.length).toBeGreaterThanOrEqual(2))
  const url = new URL(fetcher.mock.calls.find(([u]) => u.includes('/data/scans?'))![0], 'http://local')
  expect(url.searchParams.get('stage')).toBe('qc_missing')
  expect(url.searchParams.get('radar')).toBe('z9591')
  expect(Date.parse(url.searchParams.get('end')!) - Date.parse(url.searchParams.get('start')!)).toBeLessThanOrEqual(86400000)
})

it('links a scan to preflight without submitting a task or using a product date', async () => {
  const navigate = vi.fn()
  const fetcher = vi.fn(() => response({ scan: { id, radar_id: 'z9591', observed_at: '2026-09-23T00:01:35Z', received_at: at, state: 'QC_READY', normalized_uri: 's3://b/normalized', qc_uri: 's3://b/qc', grid_uri: null }, automatic_tasks: [], candidate_tasks: [], downstream_analyses: [], checks: [], truncated: {}, sampled_at: at }))
  vi.stubGlobal('fetch', fetcher)
  render(<DataInventory token="test" scanID={id} navigate={navigate} />)
  fireEvent.click(await screen.findByRole('button', { name: '预检查 QC＋对照图' }))
  expect(navigate).toHaveBeenCalledWith({ page: 'new', preset: 'qc_preview', radar: 'z9591', start: '2026-09-23T00:01:00.000Z', end: '2026-09-23T00:02:00.000Z' })
  expect(fetcher.mock.calls).toHaveLength(1)
})

it('uses optimistic revision and a reason when pausing a pool', async () => {
  const writes: unknown[] = []
  vi.stubGlobal('fetch', vi.fn((url: string, options: RequestInit) => {
    if (url.endsWith('/action')) { writes.push(JSON.parse(String(options.body))); return response({items: []}) }
    if (url.endsWith('/pools')) return response({ items: [pool], sampled_at: at })
    return response({items: [], next_before: 0, sampled_at: at})
  }))
  render(<Workers token="test" />)
  fireEvent.click(await screen.findByRole('button', { name: '暂停接单并排空' }))
  const dialog = screen.getByRole('dialog', { name: '修改执行池接单策略' })
  expect((within(dialog).getByRole('button', {name:'确认'}) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.change(within(dialog).getByLabelText('操作原因'), {target:{value:'更新管理镜像前排空'}})
  fireEvent.click(within(dialog).getByRole('button', {name:'确认'}))
  await waitFor(() => expect(writes).toEqual([{ action: 'drain', expected_revision: 4, reason: '更新管理镜像前排空' }]))
})

it('shows a pool conflict without silently retrying against a newer revision', async () => {
  const fetcher = vi.fn((url: string) => url.endsWith('/action') ? response({ message: '策略版本已变化，请刷新' }, 409) : response({items:url.endsWith('/pools') ? [pool] : [], next_before:0}))
  vi.stubGlobal('fetch', fetcher)
  render(<Workers token="test" />)
  fireEvent.click(await screen.findByRole('button', {name:'暂停接单并排空'}))
  fireEvent.change(screen.getByLabelText('操作原因'), {target:{value:'维护'}})
  fireEvent.click(screen.getByRole('button', {name:'确认'}))
  await screen.findAllByText('策略版本已变化，请刷新')
  expect(fetcher.mock.calls.filter(([u]) => u.endsWith('/action'))).toHaveLength(1)
  expect(screen.getByRole('dialog')).toBeTruthy()
})

it('filters before historical pagination and switches scope safely', async () => {
  // JSDOM has no scrolling implementation; this stubs only scrolling, not data.
  Object.defineProperty(HTMLElement.prototype, 'scrollTo', {value:vi.fn(), configurable:true})
  const fetcher = vi.fn((url: string) => {
    const q = new URL(url, 'http://local').searchParams
    return response({items:[{id: q.get('before') === '10' ? 9 : 10, level:'error',event:'failed',message:'超时',at}],has_more:q.get('before')!=='10',next_before:10,next_after:10,direction:'backward'})
  })
  vi.stubGlobal('fetch', fetcher)
  render(<Journal token="test" scope={`tasks/${id}`} />)
  await screen.findByText('超时')
  fireEvent.change(screen.getByLabelText('级别'), {target:{value:'error'}})
  fireEvent.change(screen.getByLabelText('日志关键字'), {target:{value:'%超时_'}})
  fireEvent.click(screen.getByRole('button', {name:'应用过滤'}))
  await waitFor(() => expect(fetcher.mock.calls.some(([u]) => new URL(u, 'http://local').searchParams.get('q') === '%超时_')).toBe(true))
  await waitFor(() => expect((screen.getByRole('button', {name:'更早记录'}) as HTMLButtonElement).disabled).toBe(false))
  fireEvent.click(screen.getByRole('button', {name:'更早记录'}))
  await waitFor(() => expect(fetcher.mock.calls.some(([u]) => { const q = new URL(u, 'http://local').searchParams; return q.get('before') === '10' && q.get('level') === 'error' && q.get('q') === '%超时_' })).toBe(true))
  expect(screen.getByRole('button', {name:'返回最新并跟随'})).toBeTruthy()
})

it('does not restore an unrelated old plan over a data-driven selection', async () => {
  sessionStorage.setItem('rainpulse.ops.pendingPlan', id)
  const fetcher = vi.fn(() => response({}))
  vi.stubGlobal('fetch', fetcher)
  render(<NewRun token="test" navigate={vi.fn()} initial={{radar:'z9598',preset:'render_only',start:at,end:'2026-09-23T00:01:00Z'}} />)
  await act(async () => {})
  expect(fetcher).not.toHaveBeenCalled()
  expect((screen.getByLabelText('雷达站号') as HTMLInputElement).value).toBe('z9598')
  expect((screen.getByLabelText('重算预设') as HTMLSelectElement).value).toBe('render_only')
  expect(screen.queryByRole('button', {name:'3. 确认并提交此计划'})).toBeNull()
})

it('renders unavailable performance separately from measured zero', async () => {
  vi.stubGlobal('fetch', vi.fn(() => response({start:at,end:at,sampled_at:at,scope:'management_attempts_started_in_window',quantile_basis:'successful_attempts_only_nearest_rank',attempts:1,slow:[],groups:[{kind:'qc',fingerprint:'a'.repeat(64),workers:['w'],attempts:1,states:{SUCCEEDED:1},metrics:{queue_ms:{samples:1,p50:0,p95:0,maximum:0},compute_wall_ms:{samples:0,p50:null,p95:null,maximum:null}}}]})))
  render(<Performance token="test" navigate={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', {name:'查询'}))
  await screen.findAllByText('0 ms')
  expect(screen.getAllByText('未采集').length).toBeGreaterThan(0)
})
