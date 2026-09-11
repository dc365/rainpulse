import { useCallback, useEffect, useReducer, useState } from 'react'
import type { CycleSummary } from './model'
import { assertCycleDetail, initialWorkspaceState, workspaceReducer } from './workspaceState'
import { readCycleCatalog } from './readCycleCatalog'

export function useWorkspaceData() {
  const [state, dispatch] = useReducer(workspaceReducer, initialWorkspaceState)
  const [revision, invalidate] = useReducer((n: number) => n + 1, 0)
  const [connection, setConnection] = useState<'connecting' | 'connected' | 'polling'>('connecting')
  const [now, setNow] = useState(() => Date.now())
  const refresh = useCallback(() => invalidate(), [])

  useEffect(() => {
    const controller = new AbortController()
    void readCycleCatalog(controller.signal)
      .then(payload => {
        if (controller.signal.aborted) return
        dispatch({ type: 'catalog', payload })
      }).catch((error: unknown) => {
        if (!controller.signal.aborted) dispatch({ type: 'catalog-error', message: errorMessage(error) })
      })
    return () => controller.abort()
  }, [revision])

  useEffect(() => {
    if (!state.requestedID) return
    const id = state.requestedID
    const controller = new AbortController()
    void readJSON(`/api/v1/workspace/cycles/${encodeURIComponent(id)}`, controller.signal)
      .then(({ payload, stale }) => {
        if (controller.signal.aborted) return
        assertCycleDetail(payload, id)
        dispatch({ type: 'loaded', id, detail: payload, stale })
      }).catch((error: unknown) => {
        if (!controller.signal.aborted) dispatch({ type: 'failed', id, message: errorMessage(error) })
      })
    return () => controller.abort()
  }, [revision, state.requestedID])

  useEffect(() => {
    let lastRevision = ''
    let debounce: number | undefined
    const revalidate = () => {
      window.clearTimeout(debounce)
      debounce = window.setTimeout(refresh, 100)
    }
    const onVisible = () => { if (document.visibilityState === 'visible') revalidate() }
    const source = typeof EventSource === 'undefined' ? null : new EventSource('/api/v1/workspace/events')
    if (source) {
      source.onopen = () => { setConnection('connected'); revalidate() }
      source.onerror = () => setConnection('polling')
      source.addEventListener('workspace.changed', event => {
        try {
          const payload = JSON.parse((event as MessageEvent<string>).data) as { revision?: unknown; etag?: unknown }
          const next = payload.revision ?? payload.etag
          if (typeof next === 'string' && next !== lastRevision) {
            lastRevision = next
            revalidate()
          }
        } catch { /* A malformed notification never destroys a usable snapshot. */ }
      })
    }
    const timer = window.setInterval(refresh, 30_000)
    const clock = window.setInterval(() => setNow(Date.now()), 15_000)
    window.addEventListener('online', revalidate)
    window.addEventListener('focus', revalidate)
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      source?.close()
      window.clearTimeout(debounce)
      window.clearInterval(timer)
      window.clearInterval(clock)
      window.removeEventListener('online', revalidate)
      window.removeEventListener('focus', revalidate)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [refresh])

  return {
    state, now, connection, refresh,
    requestCycle: useCallback((cycle: CycleSummary, time?: string) => dispatch({ type: 'pick', cycle, time }), []),
    setTime: useCallback((time: string | null) => dispatch({ type: 'time', time }), []),
    follow: useCallback(() => { dispatch({ type: 'follow' }); invalidate() }, []),
    pin: useCallback(() => dispatch({ type: 'pin' }), []),
  }
}

async function readJSON(path: string, signal: AbortSignal) {
  const response = await fetch(path, { signal })
  if (!response.ok) throw new Error(`数据服务暂不可用（${response.status}）`)
  return { payload: await response.json() as unknown,
    stale: response.headers.has('X-RainPulse-Stale-Seconds') }
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : '读取数据失败'
}
