import { describe, expect, it } from 'vitest'

import {
  rasterCellAtCoordinate,
  rasterValueAtCell,
  rasterValueAtCoordinate,
  type RasterPixels,
} from './rasterSampling'

const pixels: RasterPixels = {
  width: 2,
  height: 2,
  data: new Uint8ClampedArray([
    255, 0, 0, 220, 0, 0, 0, 0,
    0, 0, 255, 220, 0, 255, 0, 220,
  ]),
}

const legend = [
  { label: '1', color: '#f00', minimum: 1 },
  { label: '5', color: '#0000ff', minimum: 5 },
  { label: '10', color: '#00ff00', minimum: 10 },
]

describe('raster sampling', () => {
  it('maps WGS84 coordinates to north-first PNG rows and cell centres', () => {
    expect(rasterCellAtCoordinate([118.25, 26.75], [118, 25, 119, 27], 2, 2)).toEqual({
      column: 0,
      row: 0,
      longitude: 118.25,
      latitude: 26.5,
    })
    expect(rasterCellAtCoordinate([117.9, 26], [118, 25, 119, 27], 2, 2)).toBeNull()
  })

  it('returns one direct palette value and suppresses transparent cells', () => {
    expect(rasterValueAtCell(pixels, 0, 0, legend)).toMatchObject({
      compactLabel: '1',
      label: '1',
    })
    expect(rasterValueAtCell(pixels, 1, 0, legend)).toBeNull()
    expect(rasterValueAtCell(pixels, 1, 1, legend)).toMatchObject({
      label: '10',
    })
  })

  it('samples the matching cell for pointer coordinates', () => {
    expect(rasterValueAtCoordinate(
      pixels,
      [118.2, 25.2],
      [118, 25, 119, 27],
      legend,
    )).toMatchObject({
      cell: { column: 0, row: 1 },
      value: { label: '5' },
    })
  })
})
