import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AdminWorkspace } from './AdminWorkspace'

const sourceRunID = '0b390d5f-33e7-4ed8-aab9-8568063dc18c'
const regeneratedRunID = 'a21e5143-ad87-4a1b-8111-bd24873cd5b1'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('AdminWorkspace regeneration control', () => {
  it('requires confirmation and sends the operator token only with the bounded rerun request', async () => {
    let regenerationRequest: RequestInit | undefined
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path.includes('/api/v1/admin/runs/')) {
        regenerationRequest = init
        return jsonResponse({
          run_id: regeneratedRunID,
          rerun_of: sourceRunID,
          status: 'PREPROCESSING',
        }, 202)
      }
      if (path.includes('/api/v1/workspace/cycles')) {
        return jsonResponse({
          schema_version: '1.0',
          generated_at: '2026-09-03T00:00:00Z',
          items: [{
            cycle_id: 'cycle-1030',
            issue_time: '2026-08-28T02:30:00Z',
            grid_id: 'fuzhou_118_123_25_27_0p01deg_v1',
            execution_mode: 'realtime_shadow',
            freshness_seconds: 0,
            run_id: sourceRunID,
            capabilities: { radar: true, lk: true, steps: true, nowcastnet: true },
          }],
        })
      }
      if (path.endsWith('/radars/status')) return jsonResponse([])
      if (path.endsWith('/system/status')) return jsonResponse({ status: 'ready', version: 'test' })
      return jsonResponse({ items: [], sources: [] })
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<AdminWorkspace />)

    expect(await screen.findByRole('heading', { name: '数据重算' })).toBeTruthy()
    fireEvent.change(screen.getByLabelText('管理令牌'), { target: { value: 'operator-secret' } })
    fireEvent.click(screen.getByRole('button', { name: '准备重算' }))

    expect(screen.getByText('确认提交这次重算？')).toBeTruthy()
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes('/api/v1/admin/runs/'))).toBe(false)

    fireEvent.click(screen.getByRole('button', { name: '确认执行' }))

    expect(await screen.findByText(/已受理：a21e5143…d5b1/)).toBeTruthy()
    await waitFor(() => {
      const request = fetchMock.mock.calls.find(([input]) => String(input).includes('/api/v1/admin/runs/'))
      expect(request).toBeTruthy()
      expect(String(request?.[0])).toBe(`/api/v1/admin/runs/${sourceRunID}/rerun`)
      expect(new Headers(regenerationRequest?.headers).get('Authorization')).toBe('Bearer operator-secret')
      expect(JSON.parse(String(regenerationRequest?.body))).toEqual({
        preset: 'forecast_all',
        reason: '验证更新后的算法配置',
      })
    })
    expect(screen.getByLabelText('管理令牌')).toHaveProperty('value', '')
  })
})

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}
