import { useEffect, useState } from 'react'

export function WorkspaceLiveBridge() {
  const [connected, setConnected] = useState(false)
  useEffect(() => {
    const source = new EventSource('/api/v1/workspace/events')
    source.onopen = () => setConnected(true)
    source.onerror = () => setConnected(false)
    source.addEventListener('workspace.changed', (event) => {
      try {
        const detail = JSON.parse((event as MessageEvent<string>).data) as unknown
        window.dispatchEvent(new CustomEvent('rainpulse:workspace-changed', { detail }))
      } catch {
        // A malformed event must not break the live bridge. The 30-second
        // catalog fallback remains authoritative and the EventSource reconnects
        // independently of this browser-side notification.
      }
    })
    return () => source.close()
  }, [])
  return <span className="workspace-live-bridge" data-connected={connected} aria-label={connected ? '实时事件已连接' : '实时事件正在重连'} />
}
