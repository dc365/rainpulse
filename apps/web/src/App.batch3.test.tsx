import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

const loaded = vi.hoisted(() => [] as string[])
vi.mock('./workspace/MainWorkspace', () => { loaded.push('main'); return { MainWorkspace: () => <div>main-ready</div> } })
vi.mock('./workspace/QCReviewWorkspace', () => { loaded.push('qc'); return { QCReviewWorkspace: () => <div>qc-ready</div> } })
vi.mock('./admin/AdminApp', () => { loaded.push('admin'); return { default: () => <div>admin-ready</div> } })
afterEach(() => { cleanup(); window.history.replaceState({}, '', '/') })
it('loads only the selected route and retains all existing route matches', async () => {
  const { default: App } = await import('./App')
  expect(loaded).toEqual([])
  window.history.replaceState({}, '', '/')
  render(<App />)
  await screen.findByText('main-ready')
  expect(loaded).toEqual(['main'])
  cleanup()
  window.history.replaceState({}, '', '/qc-review')
  render(<App />)
  await screen.findByText('qc-ready')
  expect(loaded).toEqual(['main', 'qc'])
  cleanup()
  window.history.replaceState({}, '', '/admin/settings')
  render(<App />)
  await screen.findByText('admin-ready')
  expect(loaded.slice(2)).toEqual(['admin'])
})

it('shows a manual recovery path when a lazy route fails', async () => {
  const { RouteBoundary } = await import('./RouteBoundary')
  const report = vi.spyOn(console, 'error').mockImplementation(() => {})
  function BrokenRoute(): never { throw new Error('chunk unavailable') }
  try {
    render(<RouteBoundary><BrokenRoute /></RouteBoundary>)
    expect(screen.getByRole('alert')).toBeTruthy()
    expect(screen.getByRole('button', { name: '重新加载页面' })).toBeTruthy()
  } finally {
    report.mockRestore()
  }
})
