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

it('switches candidate fields without confusing quarantine with rejection', async () => {
  render(<QCReviewWorkspace />)
  const extended = structuredClone(report) as typeof report & { cases: unknown[] }
  const ray = extended.cases[0].sweeps[0].radials[0] as typeof report.cases[0]['sweeps'][0]['radials'][0] & { variants?: Record<string, unknown> }
  ray.variants = { open_source_fusion: ray.fields, rfi_objects_v2: { ...ray.fields, QC_ACTION: [1, 3], DBZH_USABLE: [null, null], RFI_RISK_STATE: [2, 0], QC_DECISION_REASON: [1024, 1], RFI_OBJECT_ID: [1, 0] } }
  upload(extended)
  await waitFor(() => expect(screen.getByText('疑似隔离 · 不用于 QPE')).toBeTruthy())
  expect(screen.getByText('高风险隔离')).toBeTruthy()
  fireEvent.change(screen.getByLabelText('检查算法'), { target: { value: 'open_source_fusion' } })
  expect(screen.queryByText('疑似隔离 · 不用于 QPE')).toBeNull()
})

it('shows missing RDD as not executed and keeps AFL candidates separate from final actions', async () => {
  render(<QCReviewWorkspace />)
  const sample = structuredClone(report)
  const sweep = sample.cases[0].sweeps[0]
  Object.assign(sweep.methods, {
    rdd_reference: { status: 'not_executed_no_verified_reference', marked_observed_gates: null, measurement_metrics: null, mask_semantics: 'raw_candidate_not_final_rejection' },
    afl_parameterized: { status: 'computed', marked_observed_gates: 1, mask_semantics: 'raw_candidate_not_final_rejection' },
  })
  Object.assign(sweep.radials[0], { variants: {
    paper_fusion_v4: sweep.radials[0].fields,
    afl_parameterized: { DBZH_RAW: [20, null], CANDIDATE_PREVIEW_DBZH: [null, null], CANDIDATE_MASK: [1, 0] },
  } })
  upload(sample)
  await waitFor(() => expect(screen.getByText('未运行，不评分')).toBeTruthy())
  expect(screen.getByText('未运行 · 无核验参考')).toBeTruthy()
  fireEvent.change(screen.getByLabelText('检查算法'), { target: { value: 'afl_parameterized' } })
  expect(screen.getAllByText('候选，不生成最终动作')).toHaveLength(2)
  expect(screen.getByText('候选掩码预览', { exact: false })).toBeTruthy()
  expect(screen.queryByText('原始缺测')).toBeNull()
})
