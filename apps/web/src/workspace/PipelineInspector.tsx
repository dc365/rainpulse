import { useEffect, useState } from 'react'

type CycleList = { items: Array<{ cycle_id: string, issue_time: string }> }
type PipelineStage = {
  stage_id: string
  display_name: string
  status: string
  radar_id?: string
  runtime_ms?: number
  queue_ms?: number
  config_version?: string
  error_message?: string
}
type PipelineSnapshot = {
  issue_time: string
  stages: PipelineStage[]
  active_regeneration?: {
    request_id: string
    status: string
    reason: string
  }
}

export function PipelineInspector() {
  const [snapshot, setSnapshot] = useState<PipelineSnapshot | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [cancelling, setCancelling] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    const load = async () => {
      try {
        const catalogResponse = await fetch('/api/v1/workspace/cycles?limit=1', { signal: controller.signal })
        if (!catalogResponse.ok) throw new Error(`周期目录响应 ${catalogResponse.status}`)
        const catalog = await catalogResponse.json() as CycleList
        const cycle = catalog.items[0]
        if (!cycle) {
          setSnapshot(null)
          return
        }
        const response = await fetch(
          `/api/v1/workspace/cycles/${encodeURIComponent(cycle.cycle_id)}/stages`,
          { signal: controller.signal },
        )
        if (!response.ok) throw new Error(`流水线响应 ${response.status}`)
        setSnapshot(await response.json() as PipelineSnapshot)
        setError(null)
      } catch (requestError) {
        if (!(requestError instanceof DOMException && requestError.name === 'AbortError')) {
          setError(requestError instanceof Error ? requestError.message : '读取流水线失败')
        }
      }
    }
    void load()
    const timer = window.setInterval(() => void load(), 5000)
    return () => {
      controller.abort()
      window.clearInterval(timer)
    }
  }, [])

  const cancel = async () => {
    const requestID = snapshot?.active_regeneration?.request_id
    if (!requestID || cancelling) return
    if (!window.confirm('确认停止这次历史重算？已经运行的单个任务可能完成，但后续阶段不会继续。')) return
    setCancelling(true)
    try {
      const response = await fetch(`/api/v1/admin/regenerations/${encodeURIComponent(requestID)}/cancel`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: '操作员在后台终止本次历史重算' }),
      })
      if (!response.ok) throw new Error(`取消请求响应 ${response.status}`)
      setSnapshot((current) => current ? { ...current, active_regeneration: undefined } : current)
      setError(null)
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '取消重算失败')
    } finally {
      setCancelling(false)
    }
  }

  return (
    <section className="runtime-pipeline-panel" aria-label="最新周期数据流">
      <header>
        <div><span>Pipeline DAG</span><h2>最新周期数据流</h2></div>
        <small>{snapshot ? formatTime(snapshot.issue_time) : '等待周期'}</small>
      </header>
      {error ? <p className="runtime-pipeline-error">{error}</p> : null}
      <div className="runtime-pipeline-dag">
        {(snapshot?.stages ?? []).map((stage, index) => (
          <div className="runtime-pipeline-stage-wrap" key={stage.stage_id}>
            {index ? <i className="runtime-pipeline-edge" aria-hidden="true" /> : null}
            <article data-status={stage.status.toLowerCase()} title={stage.config_version ?? stage.error_message}>
              <span>{stage.radar_id ? stage.radar_id.toUpperCase() : '系统'}</span>
              <strong>{stage.display_name}</strong>
              <small>{stage.runtime_ms == null ? stage.status : `${formatDuration(stage.runtime_ms)} · ${stage.status}`}</small>
            </article>
          </div>
        ))}
        {snapshot && !snapshot.stages.length ? <p>当前周期尚无任务记录。</p> : null}
      </div>
      {snapshot?.active_regeneration ? (
        <footer>
          <span>历史重算：{snapshot.active_regeneration.status} · {snapshot.active_regeneration.reason}</span>
          <button type="button" onClick={() => void cancel()} disabled={cancelling}>
            {cancelling ? '正在停止…' : '停止重算'}
          </button>
        </footer>
      ) : null}
    </section>
  )
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Taipei', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(value))
}

function formatDuration(value: number) {
  if (value < 1000) return `${value} ms`
  return `${(value / 1000).toFixed(1)} s`
}
