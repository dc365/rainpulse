import { render, screen, cleanup } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { XQCStatus } from './XQCStatus'

describe('XQC completion status', () => {
  afterEach(cleanup)
  it('warns on incomplete resource evaluation despite successful publication', () => {
    render(<XQCStatus completion={{ status: 'DEGRADED_SOURCE_RESOURCE_LIMIT', mode: 'quarantine' }}/>)
    expect(screen.getByRole('alert').textContent).toContain('未完整完成')
  })
  it('does not label an audit artifact as cleaned', () => {
    render(<XQCStatus completion={{ status: 'EVALUATED', mode: 'audit' }}/>)
    expect(screen.getByRole('alert').textContent).toContain('审计模式')
  })
  it('accepts a complete candidate and remains compatible with old results', () => {
    const view=render(<XQCStatus completion={{ status: 'EVALUATED', mode: 'quarantine' }}/>)
    expect(screen.queryByRole('alert')).toBeNull()
    view.rerender(<XQCStatus/>)
    expect(screen.queryByRole('alert')).toBeNull()
  })
})
