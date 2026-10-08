import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { DataflowWorkspace } from './DataflowWorkspace'
import type { DataflowSnapshot } from './types'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

const NOW = Date.parse('2026-10-08T04:30:00Z')

function minutesAgo(minutes: number): string {
  return new Date(NOW - minutes * 60_000).toISOString()
}

const fixture: DataflowSnapshot = {
  schema_version: '1.0',
  generated_at: new Date(NOW).toISOString(),
  window_start: minutesAgo(60),
  window_minutes: 60,
  stages: [
    { key: 'ingest', label: '数据到达', completed: 2, running: 1, queued: 0, failed: 0, p50_ms: 0 },
    { key: 'decode', label: '解码', completed: 2, running: 0, queued: 0, failed: 0, p50_ms: 9000 },
    { key: 'qc', label: '极坐标质控', completed: 1, running: 1, queued: 0, failed: 0, p50_ms: 41000 },
    { key: 'grid', label: '格点化', completed: 1, running: 0, queued: 1, failed: 0, p50_ms: 22000 },
    { key: 'mosaic_qpe', label: '拼图 · QPE', completed: 9, running: 0, queued: 0, failed: 0, p50_ms: 31000 },
    { key: 'nowcast', label: '临近预报', completed: 9, running: 0, queued: 0, failed: 0, p50_ms: 65000 },
    { key: 'products', label: '产品发布', completed: 9, running: 0, queued: 0, failed: 0, p50_ms: 12000 },
  ],
  radar_lanes: [{
    radar_id: 'z9591',
    blocks: [
      {
        scan_id: 'scan-old', run_id: 'run-old', volume_start: minutesAgo(12), volume_end: minutesAgo(7),
        received_at: minutesAgo(7), status: 'RADAR_GRID_READY',
        scan_completeness: 0.99, mean_quality_index: 0.93,
        stages: [
          { stage: 'decode', job_id: 'j1', status: 'SUCCEEDED', created_at: minutesAgo(7), started_at: minutesAgo(7), finished_at: minutesAgo(6.9), runtime_ms: 6000 },
          { stage: 'qc', job_id: 'j2', status: 'SUCCEEDED', created_at: minutesAgo(6.9), started_at: minutesAgo(6.9), finished_at: minutesAgo(6.2), runtime_ms: 42000 },
          { stage: 'grid', job_id: 'j3', status: 'SUCCEEDED', created_at: minutesAgo(6.2), started_at: minutesAgo(6.2), finished_at: minutesAgo(6), runtime_ms: 12000 },
        ],
      },
      {
        scan_id: 'scan-live', run_id: 'run-live', volume_start: minutesAgo(6), volume_end: minutesAgo(1),
        received_at: minutesAgo(1), status: 'QC_RUNNING',
        stages: [
          { stage: 'decode', job_id: 'j4', status: 'SUCCEEDED', created_at: minutesAgo(1), started_at: minutesAgo(1), finished_at: minutesAgo(0.9), runtime_ms: 6000 },
          { stage: 'qc', job_id: 'j5', status: 'RUNNING', created_at: minutesAgo(0.9), started_at: minutesAgo(0.9) },
        ],
      },
    ],
  }],
  analysis_blocks: [{
    analysis_id: 'a1', run_id: 'ra1', analysis_time: minutesAgo(6), grid_id: 'fuzhou',
    status: 'ANALYSIS_READY', radar_count: 4, coverage_ratio: 0.86, created_at: minutesAgo(6),
    stages: [
      { stage: 'mosaic', job_id: 'm1', status: 'SUCCEEDED', created_at: minutesAgo(6), started_at: minutesAgo(6), finished_at: minutesAgo(5.4), runtime_ms: 36000 },
      { stage: 'qpe', job_id: 'q1', status: 'SUCCEEDED', created_at: minutesAgo(5.4), started_at: minutesAgo(5.4), finished_at: minutesAgo(5.1), runtime_ms: 18000 },
    ],
  }],
  forecast_blocks: [{
    run_id: 'rf1', issue_time: minutesAgo(6), grid_id: 'fuzhou', status: 'PUBLISHED',
    created_at: minutesAgo(6), updated_at: minutesAgo(4),
    stages: [
      { stage: 'nowcast_input', job_id: 'n1', status: 'SUCCEEDED', created_at: minutesAgo(6), started_at: minutesAgo(6), finished_at: minutesAgo(5.8), runtime_ms: 12000 },
      { stage: 'pysteps_lk', job_id: 'p1', status: 'SUCCEEDED', created_at: minutesAgo(5.8), started_at: minutesAgo(5.8), finished_at: minutesAgo(5), runtime_ms: 48000 },
    ],
  }],
  radar_statuses: [{
    radar_id: 'z9591', display_name: '福州长乐', health: 'HEALTHY',
    latest_scan_time: minutesAgo(1), scan_status: 'QC_RUNNING',
    scan_completeness: 0.99, mean_quality_index: 0.93, data_delay_seconds: 64,
    participating_in_latest_analysis: true, qc_duration_ms: 42000,
  }],
  events: [
    { time: minutesAgo(0.5), kind: 'job.succeeded', label: '质控 · Z9591', radar_id: 'z9591', detail: '42000 ms' },
    { time: minutesAgo(6), kind: 'run.published', label: '预报发布 12:24' },
  ],
}

function stubFetch(payload: unknown) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.startsWith('/api/v1/workspace/dataflow')) {
      const requested = Number(new URL(url, 'http://test.local').searchParams.get('window') ?? '60')
      const body = { ...(payload as object), window_minutes: requested }
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }
    if (url.startsWith('/api/v1/workspace/ingest-status')) {
      return new Response(JSON.stringify({ sources: [{ radar_id: 'z9591', last_error: '' }] }), { status: 200 })
    }
    return new Response('{}', { status: 200 })
  }))
}

it('renders the chain strip, lanes, radar status and events from one snapshot', async () => {
  stubFetch(fixture)
  render(<DataflowWorkspace />)
  expect(await screen.findByText('链路节拍')).toBeTruthy()
  expect(screen.getByText('极坐标质控')).toBeTruthy()
  expect(screen.getAllByText(/1 执行/).length).toBeGreaterThanOrEqual(1)
  expect(screen.getAllByText('福州长乐').length).toBeGreaterThanOrEqual(2) // lane label + status card
  expect(screen.getByText('分析周期')).toBeTruthy()
  expect(screen.getByText('预报与发布')).toBeTruthy()
  expect(screen.getByText('质控 · Z9591')).toBeTruthy()
  expect(screen.getByText('参与拼图')).toBeTruthy()
  expect(screen.getByText(/当前分析周期/)).toBeTruthy()
})

async function dataflowBlocks() {
  await waitFor(() => {
    if (!screen.queryAllByRole('button').some(button => button.className.includes('df-block'))) {
      throw new Error('lane blocks have not rendered yet')
    }
  })
  return screen.getAllByRole('button').filter(button => button.className.includes('df-block'))
}

it('opens the evidence drawer with per-stage timings when a block is clicked', async () => {
  stubFetch(fixture)
  render(<DataflowWorkspace />)
  const blocks = await dataflowBlocks()
  expect(blocks.length).toBeGreaterThanOrEqual(2)
  fireEvent.click(blocks[0])
  await waitFor(() => expect(screen.getByRole('dialog', { name: '数据块详情' })).toBeTruthy())
  expect(screen.getByText('Z9591 体扫链路')).toBeTruthy()
  expect(screen.getByText('链路完成')).toBeTruthy()
  expect(screen.getByText(/scan scan-old/)).toBeTruthy()
  expect(screen.getAllByText('已完成').length).toBeGreaterThanOrEqual(3)
})

it('carries a hover tooltip with radar, volume time and status on each block', async () => {
  stubFetch(fixture)
  render(<DataflowWorkspace />)
  const blocks = await dataflowBlocks()
  const title = blocks[0].getAttribute('title') ?? ''
  expect(title).toContain('福州长乐')
  expect(title).toContain('链路完成')
})

it('switches the wall-clock window from the picker', async () => {
  stubFetch(fixture)
  render(<DataflowWorkspace />)
  await screen.findByText('雷达泳道 · 近 60 分钟')
  fireEvent.click(screen.getByRole('button', { name: '近 30 分钟' }))
  await screen.findByText('雷达泳道 · 近 30 分钟')
})

it('states the polling fallback while SSE is unavailable in tests', async () => {
  stubFetch(fixture)
  render(<DataflowWorkspace />)
  expect(await screen.findByText('轮询降级')).toBeTruthy()
})
