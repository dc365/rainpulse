export function focusedPanelFromSearch(search: string) {
  const params = new URLSearchParams(search)
  if (params.get('layout') !== 'single') return null
  return params.get('panel') || null
}

const presetValues = ['forecast', 'qc', 'verification'] as const

export type WorkspaceViewState = {
  panelID: string | null
  preset: (typeof presetValues)[number] | null
  cycleID: string | null
  time: string | null
}

// The URL carries the shareable view identity (preset/cycle/valid time/single
// panel) so an operator can hand over exactly the screen they are looking at.
export function workspaceViewFromSearch(search: string): WorkspaceViewState {
  const params = new URLSearchParams(search)
  const preset = params.get('preset')
  return {
    panelID: focusedPanelFromSearch(search),
    preset: presetValues.includes(preset as (typeof presetValues)[number]) ? preset as (typeof presetValues)[number] : null,
    cycleID: params.get('cycle'),
    time: params.get('time'),
  }
}

export function workspaceLayoutSearch(search: string, view: WorkspaceViewState) {
  const params = new URLSearchParams(search)
  if (view.panelID) {
    params.set('layout', 'single')
    params.set('panel', view.panelID)
  } else {
    params.delete('layout')
    params.delete('panel')
  }
  if (view.preset) params.set('preset', view.preset)
  else params.delete('preset')
  // A null cycle/time means "not part of this view update": keep whatever the
  // URL already carries so a half-initialized workspace never wipes a shared link.
  if (view.cycleID) params.set('cycle', view.cycleID)
  if (view.time) params.set('time', view.time)
  return params.toString()
}
