import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import type { CycleSummary } from './model'
import { assertCycleDetail, initialWorkspaceState, workspaceReducer } from './workspaceState'
import { readCycleCatalog } from './readCycleCatalog'
import { refreshPlan, selectedCycleRevision, mergeRefreshReason } from './refreshPolicy'
import type { RefreshReason } from './refreshPolicy'

export function useWorkspaceData() {
  const [state, dispatch] = useReducer(workspaceReducer, initialWorkspaceState)
  const [catalogRevision, invalidateCatalog] = useReducer((n: number) => n + 1, 0)
  const [detailRevision, invalidateDetail] = useReducer((n: number) => n + 1, 0)
  const [connection, setConnection] = useState<'connecting' | 'connected' | 'polling'>(
    () => typeof EventSource === 'undefined' ? 'polling' : 'connecting',
  )
  const [now, setNow] = useState(() => Date.now())
  const mode = useRef(state.mode)
  const connected = useRef(false)
  const attempts = useRef({ catalog: 0, detail: 0 })
  useEffect(() => { mode.current = state.mode }, [state.mode])
  const trigger = useCallback((reason: RefreshReason) => {
    const tick = Date.now()
    const plan = refreshPlan(reason, {
      visible: document.visibilityState !== 'hidden', connected: connected.current,
      mode: mode.current, catalogAgeMS: tick - attempts.current.catalog,
      detailAgeMS: tick - attempts.current.detail,
    })
    if (plan.catalog) { attempts.current.catalog = tick; invalidateCatalog() }
    if (plan.detail) { attempts.current.detail = tick; invalidateDetail() }
  }, [])
  const refresh = useCallback(() => trigger('manual'), [trigger])
  const selectedRevision = selectedCycleRevision(state.cycles.find(c => c.cycle_id === state.requestedID))

  useEffect(() => {
    const controller = new AbortController()
    attempts.current.catalog = Date.now()
    void readCycleCatalog(controller.signal)
      .then(payload => {
        if (controller.signal.aborted) return
        dispatch({ type: 'catalog', payload })
      }).catch((error: unknown) => {
        if (!controller.signal.aborted) dispatch({ type: 'catalog-error', message: errorMessage(error) })
      })
    return () => controller.abort()
  }, [catalogRevision])

  useEffect(() => {
    if (!state.requestedID) return
    const id = state.requestedID
    const controller = new AbortController()
    attempts.current.detail = Date.now()
    void readJSON(`/api/v1/workspace/cycles/${encodeURIComponent(id)}`, controller.signal)
      .then(({ payload, stale }) => {
        if (controller.signal.aborted) return
        assertCycleDetail(payload, id)
        dispatch({ type: 'loaded', id, detail: payload, stale })
      }).catch((error: unknown) => {
        if (!controller.signal.aborted) dispatch({ type: 'failed', id, message: errorMessage(error) })
      })
    return () => controller.abort()
  }, [detailRevision, state.requestedID, selectedRevision])

  useEffect(() => {
    let lastRevision = ''
    let debounce: number | undefined
    let pendingReason: RefreshReason | undefined
    const schedule = (reason: RefreshReason) => {
      pendingReason = mergeRefreshReason(pendingReason, reason)
      window.clearTimeout(debounce)
      debounce = window.setTimeout(() => {
        const next = pendingReason
        pendingReason = undefined
        if (next) trigger(next)
      }, 100)
    }
    const onVisible = () => {
      if (document.visibilityState !== 'hidden') schedule('visible')
      else { window.clearTimeout(debounce); pendingReason = undefined }
    }
    const onFocus = () => schedule('visible')
    const onOnline = () => schedule('reconnect')
    const source = typeof EventSource === 'undefined' ? null : new EventSource('/api/v1/workspace/events')
    if (source) {
      source.onopen = () => {
        connected.current = true; setConnection('connected'); schedule('reconnect')
      }
      source.onerror = () => {
        connected.current = false; setConnection('polling')
      }
      source.addEventListener('workspace.changed', event => {
        try {
          const payload = JSON.parse((event as MessageEvent<string>).data) as { revision?: unknown; etag?: unknown }
          const next = payload.revision ?? payload.etag
          if (typeof next === 'string' && next !== lastRevision) {
            lastRevision = next
            schedule('event')
          }
        } catch { /* Notifications cannot destroy a usable committed snapshot. */ }
      })
    } else {
      connected.current = false
    }
    const timer = window.setInterval(() => trigger('timer'), 5_000)
    const clock = window.setInterval(() => {
      if (document.visibilityState !== 'hidden') setNow(Date.now())
    }, 15_000)
    window.addEventListener('online', onOnline)
    window.addEventListener('focus', onFocus)
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      source?.close()
      connected.current = false
      window.clearTimeout(debounce)
      window.clearInterval(timer)
      window.clearInterval(clock)
      window.removeEventListener('online', onOnline)
      window.removeEventListener('focus', onFocus)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [trigger])

  return {
    state, now, connection, refresh,
    requestCycle: useCallback((cycle: CycleSummary, time?: string) => dispatch({ type: 'pick', cycle, time }), []),
    setTime: useCallback((time: string | null) => dispatch({ type: 'time', time }), []),
    follow: useCallback(() => { dispatch({ type: 'follow' }); refresh() }, [refresh]),
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
