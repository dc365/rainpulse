import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { WorkspacePresets } from './WorkspacePresets'

afterEach(cleanup)

it('renders the shared topbar preset tabs with one active', () => {
  const onSelect = vi.fn()
  render(<WorkspacePresets active="qc" onSelect={onSelect} />)
  expect(screen.getByRole('tab', { name: '质控排查' }).getAttribute('aria-selected')).toBe('true')
  expect(screen.getByRole('tab', { name: '预报对比' }).getAttribute('aria-selected')).toBe('false')
  fireEvent.click(screen.getByRole('tab', { name: '预报对比' }))
  expect(onSelect).toHaveBeenCalledExactlyOnceWith('forecast')
  fireEvent.click(screen.getByRole('tab', { name: '质控排查' }))
  expect(onSelect).not.toHaveBeenCalledWith('qc')
})
