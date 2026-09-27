import { expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { CompositeCoverage } from './CompositeCoverage'
it('shows frozen contributors, their observation age and absent inputs',()=>{
  render(<CompositeCoverage manifest={{analysis_time:'2026-08-28T00:12:00Z',sources:[{radar_id:'zf101',band:'X',volume_end:'2026-08-28T00:07:00Z'}],skipped:[{radar_id:'zf703',reason:'no_usable_causal_input'}]}}/> )
  expect(screen.getByText('本帧资料 · 参与 1 · 未参与 1')).toBeTruthy()
  expect(screen.getByText(/08:07:00 北京时 · 年龄 5.0 分钟/)).toBeTruthy()
  expect(screen.getByText('ZF703')).toBeTruthy()
  expect(screen.getByText('无可用输入（缺测、未就绪或超龄）')).toBeTruthy()
})
