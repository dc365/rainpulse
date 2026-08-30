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
      labels: { alertname: 'RainPulseJobStuck', severity: 'critical', job: 'rainpulse-api' },
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

describe('RainPulse alert workspace', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('shows source health, state counts, evidence, and local filters', async () => {
    const fetchAlerts = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => snapshot,
    })
    vi.stubGlobal('fetch', fetchAlerts)

    render(<AlertWorkspace refreshToken={0} />)

    expect(await screen.findByRole('heading', { name: '告警中心' })).toBeTruthy()
    expect(screen.getByText('Prometheus 正常')).toBeTruthy()
    expect(screen.getByText('Alertmanager 正常')).toBeTruthy()
    expect(screen.getByText('任务持续阻塞超过工程门槛')).toBeTruthy()
    expect(screen.getByText('业务雷达数据超过十分钟未更新')).toBeTruthy()
    expect(screen.getByText('Outbox 重试事件仍未发布')).toBeTruthy()
    expect(fetchAlerts).toHaveBeenCalledWith('/api/v1/alerts', expect.objectContaining({ signal: expect.any(AbortSignal) }))

    fireEvent.click(screen.getByRole('button', { name: '待生效 1' }))
    expect(screen.getByText('业务雷达数据超过十分钟未更新')).toBeTruthy()
    expect(screen.queryByText('任务持续阻塞超过工程门槛')).toBeNull()
  })

  it('does not report a healthy empty state when an upstream is unavailable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        status: 'degraded',
        sources: { prometheus: 'ready', alertmanager: 'unavailable' },
        counts: { total: 0, pending: 0, firing: 0, silenced: 0, inhibited: 0 },
        items: [],
        observed_at: '2026-08-30T07:00:00Z',
      }),
    }))

    render(<AlertWorkspace refreshToken={0} />)

    expect(await screen.findByText('告警状态不完整')).toBeTruthy()
    expect(screen.getByText('Alertmanager 不可用')).toBeTruthy()
    expect(screen.queryByText('当前没有活动告警')).toBeNull()
  })
})
