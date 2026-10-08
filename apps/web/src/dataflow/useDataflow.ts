import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import type { DataflowSnapshot, IngestStatus } from './types'

export type DataflowConnection = 'connecting' | 'connected' | 'polling' | 'historical'

// anchorISO pins the window's right edge at a past moment (historical replay
// of a recorded case); null follows the live clock with SSE push.
export function useDataflow(windowMinutes: number, anchorISO: string | null) {
  const [snapshot, setSnapshot] = useState<DataflowSnapshot | null>(null)
  const [ingest, setIngest] = useState<IngestStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [connection, setConnection] = useState<DataflowConnection>(
    () => anchorISO ? 'historical'
      : typeof EventSource === 'undefined' ? 'polling' : 'connecting',
  )
  const [now, setNow] = useState(() => anchorISO ? Date.parse(anchorISO) : Date.now())
  const [reloadKey, invalidate] = useReducer((n: number) => n + 1, 0)
  const connected = useRef(false)

  const load = useCallback((signal: AbortSignal) => {
    const anchor = anchorISO ? `&end=${encodeURIComponent(anchorISO)}` : ''
    void fetch(`/api/v1/workspace/dataflow?window=${windowMinutes}${anchor}`, { signal })
      .then(response => {
        if (!response.ok) throw new Error(`数据流服务暂不可用（${response.status}）`)
        return response.json() as Promise<DataflowSnapshot>
      })
      .then(payload => {
        if (signal.aborted) return
        setSnapshot(payload)
        setError(null)
      })
      .catch((cause: unknown) => {
        if (signal.aborted) return
        setError(cause instanceof Error ? cause.message : '读取数据流失败')
      })
  }, [windowMinutes, anchorISO])

  useEffect(() => {
    const controller = new AbortController()
    load(controller.signal)
    return () => controller.abort()
  }, [load, reloadKey])

  useEffect(() => {
    if (anchorISO) return
    const controller = new AbortController()
    void fetch('/api/v1/workspace/ingest-status', { signal: controller.signal })
      .then(response => (response.ok ? response.json() : null))
      .then(payload => {
        if (!controller.signal.aborted && payload) setIngest(payload as IngestStatus)
      })
      .catch(() => { /* Ingest status is auxiliary; never block the screen. */ })
    return () => controller.abort()
  }, [reloadKey, anchorISO])

  useEffect(() => {
    setNow(anchorISO ? Date.parse(anchorISO) : Date.now())
    if (anchorISO) {
      connected.current = false
      setConnection('historical')
      return
    }
    setConnection(typeof EventSource === 'undefined' ? 'polling' : 'connecting')
    let debounce: number | undefined
    const schedule = () => {
      window.clearTimeout(debounce)
      debounce = window.setTimeout(invalidate, 150)
    }
    const onVisible = () => {
      if (document.visibilityState !== 'hidden') schedule()
    }
    const source = typeof EventSource === 'undefined' ? null : new EventSource('/api/v1/workspace/dataflow/events')
    if (source) {
      source.onopen = () => {
        connected.current = true
        setConnection('connected')
        schedule()
      }
      source.onerror = () => {
        connected.current = false
        setConnection('polling')
      }
      source.addEventListener('dataflow.changed', event => {
        try {
          const payload = JSON.parse((event as MessageEvent<string>).data) as { revision?: unknown }
          if (typeof payload.revision === 'string') schedule()
        } catch { /* A malformed ping never destroys a usable snapshot. */ }
      })
    } else {
      connected.current = false
    }
    const timer = window.setInterval(() => {
      if (!connected.current || document.visibilityState === 'hidden') return
      invalidate()
    }, 5_000)
    const clock = window.setInterval(() => {
      if (document.visibilityState !== 'hidden') setNow(Date.now())
    }, 1_000)
    window.addEventListener('online', schedule)
    window.addEventListener('focus', onVisible)
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      source?.close()
      connected.current = false
      window.clearTimeout(debounce)
      window.clearInterval(timer)
      window.clearInterval(clock)
      window.removeEventListener('online', schedule)
      window.removeEventListener('focus', onVisible)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [anchorISO])

  return { snapshot, ingest, error, connection, now, refresh: invalidate }
}
