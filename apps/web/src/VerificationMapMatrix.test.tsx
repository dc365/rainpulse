import { act, cleanup, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { VerificationMapMatrix } from './VerificationMapMatrix'

type LayerErrorHandler = (failed: boolean) => void

const handlerHistory = new Map<string, LayerErrorHandler[]>()

vi.mock('./RasterGISMap', () => ({
  RasterGISMap: ({ mapLabel, onLayerError }: { mapLabel: string, onLayerError: LayerErrorHandler }) => {
    const history = handlerHistory.get(mapLabel) ?? []
    history.push(onLayerError)
    handlerHistory.set(mapLabel, history)
    return <div data-testid={mapLabel} />
  },
}))

const frame = {
  case_id: 'midwest_convection_20210810',
  issue_time: '2021-08-10T17:00:00Z',
  valid_time: '2021-08-10T17:10:00Z',
  lead_minutes: 10,
  fit_bounds: [-94.99, 39, -90, 41],
  pixel_edge_bounds: [-94.995, 38.995, -89.995, 41.005],
  width: 501,
  height: 201,
  rain_threshold_mm_h: 0.1,
  valid_no_rain_color: '#dce6e2',
  palette_version: 'rainfall-operational-v1',
  legend: [{ minimum_mm_h: 0.1, color: '#9dd9ff' }],
  layers: [
    { role: 'truth', model: null, image_url: '/maps/truth.png', valid_cell_count: 100, missing_cell_count: 0, rain_cell_count: 20 },
    { role: 'forecast', model: 'lk', image_url: '/maps/lk.png', valid_cell_count: 100, missing_cell_count: 0, rain_cell_count: 20 },
    { role: 'forecast', model: 'persistence', image_url: '/maps/persistence.png', valid_cell_count: 100, missing_cell_count: 0, rain_cell_count: 20 },
  ],
  motion: { vectors: [], fallback_used: false, fallback_reason: null, feature_count: 0, trackable_rain_pixel_count: 0 },
}

describe('VerificationMapMatrix', () => {
  beforeEach(() => handlerHistory.clear())
  afterEach(cleanup)

  it('keeps layer error callbacks stable and ignores repeated status reports', () => {
    render(
      <VerificationMapMatrix
        frame={frame as never}
        baseline="persistence"
        loading={false}
        error={null}
        mapsAvailable
      />,
    )

    const label = 'MRMS 实况验证地图，共享视野，EPSG:4326'
    const initialHistory = handlerHistory.get(label) ?? []
    const initialHandler = initialHistory.at(-1)
    const initialRenderCount = initialHistory.length
    expect(initialHandler).toBeTypeOf('function')

    act(() => initialHandler?.(false))
    expect(handlerHistory.get(label)).toHaveLength(initialRenderCount)

    act(() => initialHandler?.(true))
    const afterFailure = handlerHistory.get(label)?.at(-1)
    const failureRenderCount = handlerHistory.get(label)?.length ?? initialRenderCount
    expect(afterFailure).toBe(initialHandler)

    act(() => afterFailure?.(true))
    expect(handlerHistory.get(label)).toHaveLength(failureRenderCount)
  })
})
