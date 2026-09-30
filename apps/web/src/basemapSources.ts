import { useSyncExternalStore } from 'react'

// Runtime basemap sources served by the control plane. Clients only see
// key/label and request tiles same-origin through the basemap-tiles proxy;
// upstream URLs (and the Tianditu token) stay server-side. Falls back to the
// build-time env basemap when the config endpoint is unreachable.

export type BasemapOption = { key: string; label: string; hasOverlay: boolean }
export type BasemapState = {
  status: 'loading' | 'ready' | 'failed'
  options: BasemapOption[]
  defaultKey: string
  selected: string
}

const STORAGE_KEY = 'rainpulse.basemap.key'
const CONFIG_URL = '/api/v1/workspace/basemap-config'

let state: BasemapState = { status: 'loading', options: [], defaultKey: '', selected: '' }
let snapshot: BasemapState = state
const listeners = new Set<() => void>()
let started = false

function emit() {
  snapshot = { ...state }
  listeners.forEach(listener => listener())
}

async function load() {
  try {
    const response = await fetch(CONFIG_URL, { cache: 'no-store' })
    if (!response.ok) throw new Error(`basemap config ${response.status}`)
    const data = await response.json() as { default: string; sources: BasemapOption[] }
    if (!Array.isArray(data.sources) || !data.sources.length) throw new Error('basemap config empty')
    const stored = window.localStorage.getItem(STORAGE_KEY) ?? ''
    const selected = data.sources.some(option => option.key === stored) ? stored : data.default
    state = { status: 'ready', options: data.sources, defaultKey: data.default, selected }
  } catch {
    state = { status: 'failed', options: [], defaultKey: '', selected: '' }
  }
  emit()
}

export function subscribeBasemap(listener: () => void) {
  listeners.add(listener)
  if (!started && typeof fetch === 'function') {
    started = true
    void load()
  }
  return () => { listeners.delete(listener) }
}

export function getBasemapSnapshot() {
  return snapshot
}

export function selectBasemap(key: string) {
  if (!state.options.some(option => option.key === key)) return
  window.localStorage.setItem(STORAGE_KEY, key)
  state = { ...state, selected: key }
  emit()
}

export function basemapTileURL(key: string, annotation = false) {
  return `/api/v1/workspace/basemap-tiles/${key}${annotation ? '-annot' : ''}/{z}/{x}/{y}.png`
}

export function useBasemap() {
  return useSyncExternalStore(subscribeBasemap, getBasemapSnapshot)
}
