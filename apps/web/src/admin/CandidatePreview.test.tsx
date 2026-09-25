import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
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

  it('defaults to the lowest elevation while retaining duplicate-elevation sweeps', async () => {
    asset.mockImplementation(async (_token: string, _task: string, key: string) => {
      if (key === 'manifest.json') return new Blob([JSON.stringify({
        layers: [{ object_path: 'raw.png', title: '原始反射率' }],
        comparison: { sweeps: [
          { sweep_number: 0, sequence: 1, raw: 'high.png', qc: 'high-qc.png', flags: 'high-flags.png', elevation_deg: 2.0 },
          { sweep_number: 5, sequence: 2, raw: 'low.png', qc: 'low-qc.png', flags: 'low-flags.png', elevation_deg: 0.5 },
          { sweep_number: 7, sequence: 3, raw: 'low-duplicate.png', qc: 'low-duplicate-qc.png', flags: 'low-duplicate-flags.png', elevation_deg: 0.5 },
        ] },
      })])
      return new Blob(['png'])
    })
    render(<Preview token="test" taskID="task-x-lowest" />)
    const select = await screen.findByRole('combobox', { name: '仰角层' }) as HTMLSelectElement
    await waitFor(() => expect(select.value).toBe('5'))
    expect(select.options).toHaveLength(3)
    expect(asset).toHaveBeenCalledWith('test', 'task-x-lowest', 'low.png', expect.any(AbortSignal))
  })

  it('shows S-only, X-only, fused, and common-valid difference quicklooks', async () => {
    asset.mockImplementation(async (_token: string, _task: string, key: string) => {
      if (key === 'manifest.json') return new Blob([JSON.stringify({
        layers: [{ object_path: 'cr.png', title: '组合反射率' }],
        comparison: { cadence_seconds: 360, products: [
          { product_id: 's_only', label: 'S 单独', status: 'available', object_path: 'comparison/s_only.png', valid_echo_cells: 3 },
          { product_id: 'x_only', label: 'X 单独', status: 'no_qualified_echo', object_path: 'comparison/x_only.png', reason: '无合格回波' },
          { product_id: 'sx_composite', label: '仅S贡献（未形成 S/X 融合）', status: 'available', object_path: 'comparison/sx_composite.png', valid_echo_cells: 3, contributing_bands: ['S'], reason: 'X 波段没有合格贡献；结果仅由 S 波段贡献，未形成双波段融合' },
          { product_id: 'x_minus_s', label: 'X−S 差值', status: 'available', object_path: 'comparison/x_minus_s.png', valid_cells: 2 },
        ] },
      })])
      return new Blob(['png'])
    })
    render(<Preview token="test" taskID="task-sx" />)
    expect(await screen.findByAltText('S 单独候选网格快视图')).toBeTruthy()
    expect(await screen.findByAltText('X 单独候选网格快视图')).toBeTruthy()
    expect(await screen.findByAltText('仅S贡献（未形成 S/X 融合）候选网格快视图')).toBeTruthy()
    expect(await screen.findByAltText('X−S 差值候选网格快视图')).toBeTruthy()
    expect(screen.getByText(/X 波段没有合格贡献/)).toBeTruthy()
    expect(screen.getAllByText(/3 个有效回波像元/)).toHaveLength(2)
    expect(screen.getByText(/2 个共同有效回波像元/)).toBeTruthy()
    expect(screen.queryByText('本时次没有有效回波')).toBeNull()
    expect(screen.getByText(/差值仅在 S 与 X 均有有效回波/)).toBeTruthy()
  })

  it('shows valid no-echo coverage separately from qualified echoes', async () => {
    asset.mockImplementation(async (_token: string, _task: string, key: string) => {
      if (key === 'manifest.json') return new Blob([JSON.stringify({
        layers: [{ object_path: 'cr.png', title: '组合反射率' }],
        comparison: { products: [{
          product_id: 'sx_composite',
          label: 'S/X 融合（仅有效无回波像元）',
          status: 'no_echo',
          object_path: 'comparison/sx_composite.png',
          valid_echo_cells: 0,
          valid_no_echo_cells: 4,
          contributing_bands: ['S', 'X'],
          echo_contributing_bands: [],
          reason: 'S 与 X 均有有效无回波覆盖，本时次没有有效回波',
        }] },
      })])
      return new Blob(['png'])
    })
    render(<Preview token="test" taskID="task-sx-noecho" />)
    expect(await screen.findByAltText('S/X 融合（仅有效无回波像元）候选网格快视图')).toBeTruthy()
    expect(screen.getByText(/0 个有效回波像元 · 4 个有效无回波像元/)).toBeTruthy()
    expect(screen.getByText(/本时次没有有效回波/)).toBeTruthy()
    expect(screen.queryByText('本时次无合格像元')).toBeNull()
  })

  it('shows regional image errors and retries the failed quicklook', async () => {
    let attempts = 0
    asset.mockImplementation(async (_token: string, _task: string, key: string) => {
      if (key === 'manifest.json') return new Blob([JSON.stringify({
        layers: [{ object_path: 'cr.png', title: '组合反射率' }],
        comparison: { products: [
          { product_id: 's_only', label: 'S 单独', status: 'available', object_path: 'comparison/s_only.png' },
        ] },
      })])
      if (key === 'comparison/s_only.png' && attempts++ === 0) throw new Error('503 asset unavailable')
      return new Blob(['png'])
    })
    render(<Preview token="test" taskID="task-sx-failure" />)
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('503 asset unavailable')
    fireEvent.click(screen.getByRole('button', { name: '重试区域对照图' }))
    expect(await screen.findByAltText('S 单独候选网格快视图')).toBeTruthy()
  })
})
