import type { GISLegendEntry, GISMapExtent } from './RasterGISMap'

export type RasterPixels = {
  width: number
  height: number
  data: Uint8ClampedArray
}

export type RasterCell = {
  column: number
  row: number
  longitude: number
  latitude: number
}

export type RasterValue = {
  color: string
  compactLabel: string
  label: string
}

export function rasterCellAtCoordinate(
  coordinate: readonly number[],
  extent: GISMapExtent,
  width: number,
  height: number,
): RasterCell | null {
  const [longitude, latitude] = coordinate
  const [west, south, east, north] = extent
  if (
    width < 1
    || height < 1
    || longitude < west
    || longitude > east
    || latitude < south
    || latitude > north
  ) return null

  const column = Math.min(width - 1, Math.floor(((longitude - west) / (east - west)) * width))
  const row = Math.min(height - 1, Math.floor(((north - latitude) / (north - south)) * height))
  return {
    column,
    row,
    longitude: west + ((column + 0.5) / width) * (east - west),
    latitude: north - ((row + 0.5) / height) * (north - south),
  }
}

export function rasterValueAtCell(
  pixels: RasterPixels,
  column: number,
  row: number,
  legend: readonly GISLegendEntry[],
): RasterValue | null {
  if (column < 0 || row < 0 || column >= pixels.width || row >= pixels.height) return null
  const offset = (row * pixels.width + column) * 4
  const alpha = pixels.data[offset + 3]
  if (alpha === 0) return null

  const red = pixels.data[offset]
  const green = pixels.data[offset + 1]
  const blue = pixels.data[offset + 2]
  const matchIndex = legend.findIndex((entry) => {
    const color = parseHexColor(entry.color)
    return color?.[0] === red && color[1] === green && color[2] === blue
  })
  if (matchIndex < 0) return null

  const entry = legend[matchIndex]
  if (entry.minimum != null) {
    const label = formatNumber(entry.minimum)
    return { color: entry.color, compactLabel: label, label }
  }

  const label = entry.label || '有值'
  return {
    color: entry.color,
    compactLabel: label.length > 6 ? `${label.slice(0, 6)}…` : label,
    label,
  }
}

export function rasterValueAtCoordinate(
  pixels: RasterPixels,
  coordinate: readonly number[],
  extent: GISMapExtent,
  legend: readonly GISLegendEntry[],
) {
  const cell = rasterCellAtCoordinate(coordinate, extent, pixels.width, pixels.height)
  if (!cell) return null
  const value = rasterValueAtCell(pixels, cell.column, cell.row, legend)
  return value ? { cell, value } : null
}

function parseHexColor(value: string): readonly [number, number, number] | null {
  const normalized = value.trim().replace(/^#/, '')
  const expanded = normalized.length === 3
    ? normalized.split('').map((character) => `${character}${character}`).join('')
    : normalized
  if (!/^[0-9a-f]{6}$/i.test(expanded)) return null
  return [
    Number.parseInt(expanded.slice(0, 2), 16),
    Number.parseInt(expanded.slice(2, 4), 16),
    Number.parseInt(expanded.slice(4, 6), 16),
  ]
}

function formatNumber(value: number) {
  if (Math.abs(value) >= 100) return value.toFixed(0)
  if (Math.abs(value) >= 10) return Number.isInteger(value) ? value.toFixed(0) : value.toFixed(1)
  return Number.isInteger(value) ? value.toFixed(0) : value.toFixed(2).replace(/0+$/, '')
}
