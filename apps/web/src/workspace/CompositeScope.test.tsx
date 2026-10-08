import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { CompositeScope } from './CompositeScope'

afterEach(cleanup)
const stations = ['z9591','z9593','z9598','z9599','zf101']
it('marks a partial request and deduplicates sweep records', () => {
  render(<CompositeScope registered={stations} requested={['z9591','zf101']} sources={[
    {radar_id:'z9591',band:'S'},{radar_id:'z9591',band:'S'},{radar_id:'zf101',band:'X'},
  ]}/>)
  expect(screen.getByRole('status').textContent).toContain('局部组合：请求 2/5 个登记站')
  expect(screen.getByText(/实际输入 1 S \+ 1 X/)).toBeTruthy()
})
it('distinguishes missing inputs from a deliberately partial request', () => {
  render(<CompositeScope registered={stations} requested={stations} sources={[{radar_id:'z9591',band:'S'}]}/>)
  expect(screen.getByRole('status').textContent).toContain('请求全部 5 个登记站，实际输入 1 站')
  expect(screen.queryByText(/局部组合/)).toBeNull()
})
it('does not assume an unknown historical request covered all registered stations', () => {
  render(<CompositeScope registered={stations} sources={[{radar_id:'z9591',band:'S'}]}/>)
  expect(screen.getByRole('status').textContent).toContain('历史请求范围未记录')
})
