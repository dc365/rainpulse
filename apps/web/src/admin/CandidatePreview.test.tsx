import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Preview } from './CandidatePreview'

const { asset } = vi.hoisted(() => ({ asset: vi.fn() }))
vi.mock('./api', () => ({ asset: (...args: unknown[]) => asset(...args), failure: (error: unknown) => String(error) }))

describe('X QC candidate preview', () => {
  beforeEach(() => {
    asset.mockReset()
    asset.mockImplementation(async (_token: string, _task: string, key: string) => {
      if (key === 'manifest.json') return new Blob([JSON.stringify({
        layers: [
          { object_path: 'raw.png', title: '原始反射率', field: 'DBZH_RAW', sweep_number: 0 },
          { object_path: 'qc.png', title: '质控后', field: 'DBZH_QC_DISPLAY', sweep_number: 0 },
          { object_path: 'flags.png', title: '质控标记', field: 'QC_ACTION', sweep_number: 0 },
        ],
        comparison: { sweeps: [{ sweep_number: 0, raw: 'raw.png', qc: 'qc.png', flags: 'flags.png', elevation_deg: 0.9 }] },
      })])
      return new Blob(['png'])
    })
    vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:qc-preview'), revokeObjectURL: vi.fn() })
  })
  afterEach(() => { cleanup(); vi.unstubAllGlobals() })

  it('shows raw and QC from the same sweep side by side', async () => {
    render(<Preview token="test" taskID="task-x" />)
    expect(await screen.findByAltText('原始反射率PPI')).toBeTruthy()
    expect(await screen.findByAltText('质控后反射率PPI，琥珀色为未决门')).toBeTruthy()
    await waitFor(() => expect(asset).toHaveBeenCalledWith('test', 'task-x', 'raw.png', expect.any(AbortSignal)))
    expect(asset).toHaveBeenCalledWith('test', 'task-x', 'qc.png', expect.any(AbortSignal))
    expect(screen.getByText(/琥珀色回波保留为待核/)).toBeTruthy()
  })

  it('shows S-only, X-only, fused, and common-valid difference quicklooks', async () => {
    asset.mockImplementation(async (_token: string, _task: string, key: string) => {
      if (key === 'manifest.json') return new Blob([JSON.stringify({
        layers: [{ object_path: 'cr.png', title: '组合反射率' }],
        comparison: { cadence_seconds: 360, products: [
          { product_id: 's_only', label: 'S 单独', status: 'available', object_path: 'comparison/s_only.png', valid_echo_cells: 3 },
          { product_id: 'x_only', label: 'X 单独', status: 'no_qualified_echo', object_path: 'comparison/x_only.png', reason: '无合格回波' },
          { product_id: 'sx_composite', label: 'S/X 融合', status: 'available', object_path: 'comparison/sx_composite.png', valid_echo_cells: 3 },
          { product_id: 'x_minus_s', label: 'X−S 差值', status: 'no_overlap', object_path: 'comparison/x_minus_s.png', valid_cells: 0, reason: '没有共同有效像元' },
        ] },
      })])
      return new Blob(['png'])
    })
    render(<Preview token="test" taskID="task-sx" />)
    expect(await screen.findByAltText('S 单独候选网格快视图')).toBeTruthy()
    expect(await screen.findByAltText('X 单独候选网格快视图')).toBeTruthy()
    expect(await screen.findByAltText('S/X 融合候选网格快视图')).toBeTruthy()
    expect(await screen.findByAltText('X−S 差值候选网格快视图')).toBeTruthy()
    expect(screen.getByText(/差值仅在 S 与 X 均有有效回波/)).toBeTruthy()
  })
})
