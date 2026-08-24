import { useEffect, useState } from 'react'

import type { components } from './api/generated/schema'
import './styles.css'

type SystemStatus = components['schemas']['SystemStatus']

export default function App() {
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true

    fetch('/api/v1/system/status')
      .then((response) => {
        if (!response.ok) {
          throw new Error(`status request failed with ${response.status}`)
        }
        return response.json() as Promise<SystemStatus>
      })
      .then((status) => {
        if (active) {
          setSystemStatus(status)
        }
      })
      .catch((requestError: unknown) => {
        if (active) {
          setError(requestError instanceof Error ? requestError.message : 'unknown error')
        }
      })

    return () => {
      active = false
    }
  }, [])

  return (
    <main className="app-shell">
      <section className="hero" aria-labelledby="rainpulse-title">
        <p className="eyebrow">0–2 小时短临降水预报</p>
        <h1 id="rainpulse-title">RainPulse</h1>
        <p className="summary">可靠基线优先，数据、模型和实况检验形成闭环。</p>

        <div className="status-panel" aria-live="polite">
          {error ? <p>控制面不可用：{error}</p> : null}
          {!error && !systemStatus ? <p>正在连接控制面…</p> : null}
          {systemStatus ? (
            <>
              <p>控制面状态：{systemStatus.status}</p>
              <p>版本：{systemStatus.version}</p>
            </>
          ) : null}
        </div>
      </section>
    </main>
  )
}
