import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from './App'

describe('RainPulse application shell', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('shows the control-plane status returned by the public Go API', async () => {
    const fetchStatus = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        service: 'rainpulse-control',
        status: 'ready',
        version: 'test-version',
      }),
    })
    vi.stubGlobal('fetch', fetchStatus)

    render(<App />)

    expect(screen.getByRole('heading', { name: 'RainPulse' })).toBeTruthy()
    expect(await screen.findByText('控制面状态：ready')).toBeTruthy()
    expect(screen.getByText('版本：test-version')).toBeTruthy()
    expect(fetchStatus).toHaveBeenCalledWith('/api/v1/system/status')
  })
})
