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
it('counts stations once while retaining sweep QC versions',()=>{
  render(<CompositeCoverage manifest={{analysis_time:'2026-08-28T00:12:00Z',sources:[
    {radar_id:'z9591',band:'S',volume_end:'2026-08-28T00:07:00Z',qc_version:'s-qc-v1'},
    {radar_id:'z9591',band:'S',volume_end:'2026-08-28T00:07:00Z',qc_version:'s-qc-v1'},
    {radar_id:'zf101',band:'X',volume_end:'2026-08-28T00:07:00Z',qc_version:'x-qc-v2'},
  ]}}/>)
  expect(screen.getByText('本帧资料 · 参与 2 · 未参与 0')).toBeTruthy()
  expect(screen.getAllByText(/Z9591/)).toHaveLength(1)
  expect(screen.getByText(/QC s-qc-v1/)).toBeTruthy()
})
it('shows actual parameter and producer identities, including an unknown historical revision',()=>{
  render(<CompositeCoverage manifest={{analysis_time:'2026-08-28T00:12:00Z',sources:[
    {radar_id:'z9591',band:'S',volume_end:'2026-08-28T00:07:00Z',qc_identity:{parameters_sha256:'a'.repeat(64),implementation_revision:null}},
    {radar_id:'zf101',band:'X',volume_end:'2026-08-28T00:07:00Z',qc_identity:{parameters_sha256:'b'.repeat(64),base_parameters_sha256:'c'.repeat(64),implementation_revision:'actual-x-producer'}},
  ]}}/>)
  expect(screen.getByText('参数 aaaaaaaaaaaa · 实现未记录')).toBeTruthy()
  expect(screen.getByText('参数 bbbbbbbbbbbb · 实现 actual-x-producer')).toBeTruthy()
  expect(screen.getByText('基础参数 cccccccccccc')).toBeTruthy()
})
