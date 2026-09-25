import palette from '../../../algorithms/rainpulse_algo/reflectivity_palette.json'
import type { GISLegendEntry } from './RasterGISMap'

export const REFLECTIVITY_STOPS = palette as [number, string][]
export const REFLECTIVITY_LEGEND: GISLegendEntry[] = REFLECTIVITY_STOPS.map(([minimum, color]) => ({
  minimum,
  label: String(minimum),
  color,
}))

