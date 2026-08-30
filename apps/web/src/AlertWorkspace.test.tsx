import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AlertWorkspace } from './AlertWorkspace'

const snapshot = {
  status: 'ready',
  sources: { prometheus: 'ready', alertmanager: 'ready' },
  counts: { total: 3, pending: 1, firing: 1, silenced: 1, inhibited: 0 },
  observed_at: '2026-08-30T07:00:00Z',
  items: [
    {
      alert_id: 'critical-alert',
      name: 'RainPulseJobStuck',
      severity: 'critical',
      state: 'firing',
      summary: '任务持续阻塞超过工程门槛',
      active_at: '2026-08-30T06:50:00Z',
      value: '1200',
      labels: { alertname: 'RainPulseJobStuck', severity: 'critical', job_id: '11111111-1111-4111-8111-111111111111', run_id: '22222222-2222-4222-8222-222222222222', job_type: 'analysis.qpe' },
      annotations: { summary: '任务持续阻塞超过工程门槛' },
    },
    {
      alert_id: 'pending-alert',
      name: 'RainPulseRadarDataStale',
      severity: 'warning',
      state: 'pending',
      summary: '业务雷达数据超过十分钟未更新',
      active_at: '2026-08-30T06:59:00Z',
      value: '901',
      labels: { alertname: 'RainPulseRadarDataStale', severity: 'warning', radar_id: 'z9598' },
      annotations: { summary: '业务雷达数据超过十分钟未更新' },
    },
    {
      alert_id: 'silenced-alert',
      name: 'RainPulseOutboxFailure',
      severity: 'warning',
      state: 'silenced',
      summary: 'Outbox 重试事件仍未发布',
      active_at: '2026-08-30T06:40:00Z',
      value: '1',
      labels: { alertname: 'RainPulseOutboxFailure', severity: 'warning' },
      annotations: { summary: 'Outbox 重试事件仍未发布' },
    },
  ],
} as const

const issueSnapshot = {
  counts: { total: 2, failed_jobs: 1, stuck_jobs: 0, outbox_events: 1 },
  observed_at: '2026-08-30T07:00:00Z',
  items: [
    {
      issue_id: 'job:11111111-1111-4111-8111-111111111111',
      kind: 'job',
      status: 'FAILED',
      summary: 'analysis.qpe 执行失败',
      run_id: '22222222-2222-4222-8222-222222222222',
      job_id: '11111111-1111-4111-8111-111111111111',
      job_type: 'analysis.qpe',
      error_code: 'BAD_INPUT',
      error_message: '输入网格与目标范围不一致',
      attempt_count: 2,
      age_seconds: 93,
      created_at: '2026-08-30T06:58:00Z',
      updated_at: '2026-08-30T06:58:27Z',
    },
    {
      issue_id: 'outbox:33333333-3333-4333-8333-333333333333',
      kind: 'outbox',
      status: 'failed',
      summary: 'job.requested.v1 尚未发布',
      event_id: '33333333-3333-4333-8333-333333333333',
      aggregate_id: '44444444-4444-4444-8444-444444444444',
      event_type: 'job.requested.v1',
      attempt_count: 4,
      age_seconds: 88,
      created_at: '2026-08-30T06:58:32Z',
      updated_at: '2026-08-30T07:00:00Z',
    },
  ],
} as const

describe('RainPulse alert workspace', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('shows source health, state counts, evidence, and local filters', async () => {
    const fetchAlerts = vi.fn().mockImplementation(async (url: string) => ({
      ok: true, status: 200,
      json: async () => url.includes('/operations/issues') ? issueSnapshot : snapshot,
    }))
    vi.stubGlobal('fetch', fetchAlerts)

    render(<AlertWorkspace refreshToken={0} />)

    expect(await screen.findByRole('heading', { name: '告警中心' })).toBeTruthy()
    expect(screen.getByText('Prometheus 正常')).toBeTruthy()
    expect(screen.getByText('Alertmanager 正常')).toBeTruthy()
    expect(screen.getByText('任务持续阻塞超过工程门槛')).toBeTruthy()
    expect(screen.getByText('业务雷达数据超过十分钟未更新')).toBeTruthy()
    expect(screen.getByText('Outbox 重试事件仍未发布')).toBeTruthy()
    expect(screen.getByText('analysis.qpe 执行失败')).toBeTruthy()
    expect(screen.getByText('输入网格与目标范围不一致')).toBeTruthy()
    expect(screen.getByText('已关联 告警中')).toBeTruthy()
    expect(screen.getByText('job.requested.v1 尚未发布')).toBeTruthy()
    expect(fetchAlerts).toHaveBeenCalledWith('/api/v1/alerts', expect.objectContaining({ signal: expect.any(AbortSignal) }))
    expect(fetchAlerts).toHaveBeenCalledWith('/api/v1/operations/issues', expect.objectContaining({ signal: expect.any(AbortSignal) }))

    fireEvent.click(screen.getByRole('button', { name: '待生效 1' }))
    expect(screen.getByText('业务雷达数据超过十分钟未更新')).toBeTruthy()
    expect(screen.queryByText('任务持续阻塞超过工程门槛')).toBeNull()
  })

  it('does not report a healthy empty state when an upstream is unavailable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) => ({
      ok: true,
      status: 200,
      json: async () => url.includes('/operations/issues') ? {
        counts: { total: 0, failed_jobs: 0, stuck_jobs: 0, outbox_events: 0 },
        items: [],
        observed_at: '2026-08-30T07:00:00Z',
      } : {
        status: 'degraded',
        sources: { prometheus: 'ready', alertmanager: 'unavailable' },
        counts: { total: 0, pending: 0, firing: 0, silenced: 0, inhibited: 0 },
        items: [],
        observed_at: '2026-08-30T07:00:00Z',
      },
    })))

    render(<AlertWorkspace refreshToken={0} />)

    expect(await screen.findByText('告警状态不完整')).toBeTruthy()
    expect(screen.getByText('Alertmanager 不可用')).toBeTruthy()
    expect(screen.queryByText('当前没有活动告警')).toBeNull()
    expect(screen.getByText('当前没有失败、阻塞或滞留的运行记录')).toBeTruthy()
  })
})
