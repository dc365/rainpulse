import OLMap from 'ol/Map.js'

export type MapProbeDetail = {
  longitude: number
  latitude: number
  xRatio: number
  yRatio: number
  assetUrl: string
  panelLabel: string
}

type DispatchEvent = (this: OLMap, event: string | object) => boolean | undefined

type PointerMapEvent = {
  type?: string
  coordinate?: number[]
  pixel?: number[]
  dragging?: boolean
}

const installKey = Symbol.for('rainpulse.workspace.map-probe-bridge')
const bridgedTargets = new WeakSet<Element>()

export function installMapProbeBridge() {
  const globalState = globalThis as typeof globalThis & Record<PropertyKey, unknown>
  if (globalState[installKey]) return
  globalState[installKey] = true

  const prototype = OLMap.prototype as unknown as { dispatchEvent: DispatchEvent }
  const original = prototype.dispatchEvent
  prototype.dispatchEvent = function dispatchWithWorkspaceProbe(event) {
    const result = original.call(this, event)
    if (typeof event === 'string') return result
    const pointer = event as PointerMapEvent
    if (pointer.type !== 'pointermove' || pointer.dragging
      || !pointer.coordinate || !pointer.pixel) return result

    const target = this.getTargetElement()
    const size = this.getSize()
    if (!target || !size || size[0] <= 0 || size[1] <= 0) return result
    if (!bridgedTargets.has(target)) {
      bridgedTargets.add(target)
      target.addEventListener('pointerleave', () => {
        window.dispatchEvent(new CustomEvent('rainpulse:map-probe-clear'))
      })
    }
    const panel = target.closest('.workspace-map-panel')
    const source = panel?.querySelector<HTMLElement>('[data-source]')?.dataset.source ?? ''
    const panelLabel = panel?.querySelector<HTMLElement>('.workspace-map-caption strong')?.textContent?.trim() ?? ''
    const longitude = Number(pointer.coordinate[0])
    const latitude = Number(pointer.coordinate[1])
    if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) return result
    const detail: MapProbeDetail = {
      longitude,
      latitude,
      xRatio: Math.min(1, Math.max(0, Number(pointer.pixel[0]) / size[0])),
      yRatio: Math.min(1, Math.max(0, Number(pointer.pixel[1]) / size[1])),
      assetUrl: source,
      panelLabel,
    }
    window.dispatchEvent(new CustomEvent<MapProbeDetail>('rainpulse:map-probe', { detail }))
    return result
  }
}
