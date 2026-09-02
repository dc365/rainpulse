export function focusedPanelFromSearch(search: string) {
  const params = new URLSearchParams(search)
  if (params.get('layout') !== 'single') return null
  return params.get('panel') || null
}

export function workspaceLayoutSearch(search: string, panelID: string | null) {
  const params = new URLSearchParams(search)
  if (panelID) {
    params.set('layout', 'single')
    params.set('panel', panelID)
  } else {
    params.delete('layout')
    params.delete('panel')
  }
  return params.toString()
}
