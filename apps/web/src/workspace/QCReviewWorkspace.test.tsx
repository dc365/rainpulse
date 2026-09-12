import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { QCReviewWorkspace } from './QCReviewWorkspace'

afterEach(cleanup)
const report = { schema_version: 'rainpulse.qc-review.v1', operational_eligible: false, limitations: [], cases: [{ case_id: 'test-case', radar_id: 'TEST', input_sha256: 'a'.repeat(64), partition: 'development', process_id: 'synthetic', elapsed_ms: {}, sweeps: [{ sweep: 'sweep_000', shape: [2, 2], observed_gates: 4, label_status: 'unlabeled', methods: { open_source_fusion: { mask_semantics: 'final_reject', marked_observed_gates: 0 } }, radials: [{ ray_index: 0, azimuth_deg: 0, range_m: [250, 500], fields: { DBZH_RAW: [20, null], DBZH_USABLE: [20, null], QC_ACTION: [0, 3] } }] }] }] }
const upload = (value: unknown) => fireEvent.change(screen.getByLabelText('打开 QC 对照报告'), { target: { files: [{ size: 2048, text: async () => JSON.stringify(value) }] } })

describe('QC local review', () => {
  it('does not claim live readiness before a report is loaded', () => {
    render(<QCReviewWorkspace />)
    expect(screen.getByText('先生成同输入对照报告')).toBeTruthy()
    expect(screen.queryByRole('table')).toBeNull()
  })
  it('shows exact missing states without inventing unlabeled skill', async () => {
    render(<QCReviewWorkspace />); upload(report)
    await waitFor(() => expect(screen.getByText('同一输入的候选与决策')).toBeTruthy())
    expect(screen.getAllByText('无标签样本')).toHaveLength(3)
    expect(screen.getByText('原始缺测')).toBeTruthy()
    expect(screen.getByText('没有原生执行参考，不视为已运行', { exact: false })).toBeTruthy()
  })
  it('rejects mismatched radial arrays', async () => {
    render(<QCReviewWorkspace />)
    const bad = structuredClone(report); bad.cases[0].sweeps[0].radials[0].fields.DBZH_RAW = [1]
    upload(bad)
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('不一致'))
    expect(screen.queryByRole('table')).toBeNull()
  })
})
