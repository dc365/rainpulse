import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { MainWorkspace } from './MainWorkspace'

const { pin, follow, state } = vi.hoisted(() => ({
  pin: vi.fn(), follow: vi.fn(),
  state: { cycles: [], detail: null, selectedTime: null, loading: false, mode: 'follow',
    catalogError: null, detailError: null, stale: false },
}))
vi.mock('./useWorkspaceData', () => ({ useWorkspaceData: () => ({ state, now: 0,
  connection: 'connected', refresh: vi.fn(), requestCycle: vi.fn(), setTime: vi.fn(), pin, follow }) }))
afterEach(() => { cleanup(); vi.clearAllMocks() })
it('pins the displayed cycle on entering verification and exits verification when following live again', () => {
  render(<MainWorkspace />)
  fireEvent.click(screen.getByRole('tab', { name: '检验回放' }))
  expect(pin).toHaveBeenCalledOnce()
  expect(screen.getByRole('tab', { name: '检验回放' }).getAttribute('aria-selected')).toBe('true')
  fireEvent.click(screen.getByRole('button', { name: '实时监测' }))
  expect(follow).toHaveBeenCalledOnce()
  expect(screen.getByRole('tab', { name: '预报对比' }).getAttribute('aria-selected')).toBe('true')
})
