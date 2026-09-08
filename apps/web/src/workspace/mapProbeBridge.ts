import type { GISMapProbe } from '../RasterGISMap'

export type MapProbeDetail = GISMapProbe & {
  assetUrl: string
  panelLabel: string
}

export function dispatchMapProbe(detail: MapProbeDetail) {
  window.dispatchEvent(new CustomEvent<MapProbeDetail>('rainpulse:map-probe', { detail }))
}

export function clearMapProbe() {
  window.dispatchEvent(new CustomEvent('rainpulse:map-probe-clear'))
}
