import { useMemo, useState } from 'react'
import './dataflow.css'
import { StageStrip } from './StageStrip'
import { Swimlanes } from './Swimlanes'
import { RadarStatusStrip } from './RadarStatusStrip'
import { EventTicker } from './EventTicker'
import { BlockDrawer, type SelectedBlock } from './BlockDrawer'
import { useDataflow } from './useDataflow'
import { formatClock, formatClockSeconds } from './layout'

const WINDOW_CHOICES = [30, 60, 180] as const

// DataflowWorkspace is the realtime radar-processing dataflow screen: chain
// beat strip, wall-clock swimlanes, per-radar status strip and the terminal
// event ticker. It reads only the workspace projection; no admin credentials.
// The optional end anchor replays a recorded historical case instead of
// following the live clock.
export function DataflowWorkspace() {
  const [windowMinutes, setWindowMinutes] = useState<number>(60)
  const [selected, setSelected] = useState<SelectedBlock | null>(null)
  const [anchorDraft, setAnchorDraft] = useState('')
  const [anchorISO, setAnchorISO] = useState<string | null>(null)
  const { snapshot, ingest, error, connection, now, refresh } = useDataflow(windowMinutes, anchorISO)

  const verdict = useMemo(() => verdictOf(snapshot, anchorISO), [snapshot, anchorISO])
  const anyRunning = useMemo(() =>
    (snapshot?.stages ?? []).some(stage => stage.running > 0 || stage.queued > 0), [snapshot])

  const applyAnchor = () => {
    const trimmed = anchorDraft.trim()
    if (!trimmed) {
      setAnchorISO(null)
      return
    }
    // datetime-local carries no timezone; the operator-facing clock is CST.
    const parsed = Date.parse(`${trimmed}+08:00`)
    if (Number.isNaN(parsed)) return
    setAnchorISO(new Date(parsed).toISOString())
  }

  return <main className="df-root">
    <header className="df-header">
      <div className="df-header-title">
        <h1>数据流 · 雷达处理链路</h1>
        <p>数据到达 → 解码 → 极坐标质控 → 格点化 → 拼图 · QPE → 临近预报 → 产品发布</p>
      </div>
      <div className="df-header-tools">
        <ConnectionBadge state={connection}/>
        <div className="df-window-picker" role="group" aria-label="时间窗口">
          {WINDOW_CHOICES.map(choice => <button
            type="button"
            key={choice}
            className={choice === windowMinutes ? 'df-window df-window-on' : 'df-window'}
            onClick={() => setWindowMinutes(choice)}
          >近 {choice} 分钟</button>)}
        </div>
        <div className="df-anchor-picker" role="group" aria-label="历史锚点">
          <input
            type="datetime-local"
            value={anchorDraft}
            onChange={event => setAnchorDraft(event.target.value)}
            aria-label="历史锚点时刻（北京时间）"
          />
          <button type="button" onClick={applyAnchor}>回放</button>
          {anchorISO && <button type="button" className="df-anchor-live" onClick={() => {
            setAnchorDraft('')
            setAnchorISO(null)
          }}>回到实时</button>}
        </div>
        <button type="button" className="df-refresh" onClick={refresh}>刷新</button>
        <a className="df-home" href="/">返回工作台</a>
      </div>
    </header>

    {error && <div className="df-notice df-notice-error" role="alert">{error}</div>}
    {!snapshot && !error && <div className="df-notice" role="status">正在读取链路快照…</div>}

    {snapshot && <>
      <section className={`df-verdict df-verdict-${verdict.tone}`} role="status">
        <span className="df-verdict-dot" aria-hidden="true"/>
        <div>
          <h2>{verdict.title}</h2>
          <p>
            {verdict.detail}
            {snapshot.warnings && snapshot.warnings.length > 0 && ` · 部分源降级（${snapshot.warnings.join('、')}）`}
          </p>
        </div>
        <small>
          {anchorISO ? `回放锚点 ${formatClock(anchorISO)} CST` : '快照'}
          {' '}{formatClockSeconds(snapshot.generated_at)} CST
        </small>
      </section>

      <section className="df-panel">
        <div className="df-panel-head">
          <h2>链路节拍</h2>
          <small>窗口内完成 / 执行 / 排队 / 失败 · 中位耗时</small>
        </div>
        <StageStrip stages={snapshot.stages} nowActive={anyRunning}/>
      </section>

      <section className="df-panel">
        <div className="df-panel-head">
          <h2>雷达泳道{anchorISO ? ` · ${formatClock(snapshot.window_start)} – ${formatClock(now)}` : ` · 近 ${snapshot.window_minutes} 分钟`}</h2>
          <small>每块 = 一次体扫 · 内部分段 = 解码 | 质控 | 格点化 · 点击查看证据</small>
        </div>
        <Swimlanes
          snapshot={snapshot}
          now={now}
          onSelect={setSelected}
          selected={selected}
          nowLabel={anchorISO ? formatClock(anchorISO) : '现在'}
        />
        <p className="df-legend">
          <span className="df-seg df-seg-done"/> 已完成
          <span className="df-seg df-seg-running"/> 执行中
          <span className="df-seg df-seg-failed"/> 失败
          <span className="df-seg df-seg-queued"/> 排队
          <span className="df-gap-sample"/> 断流
        </p>
      </section>

      <div className="df-lower">
        <section className="df-panel">
          <div className="df-panel-head">
            <h2>雷达状态</h2>
            <small>按波段分组 · 每部雷达的当前质控进程与采集状况</small>
          </div>
          <RadarStatusStrip statuses={snapshot.radar_statuses} ingest={ingest}/>
        </section>
        <section className="df-panel">
          <div className="df-panel-head">
            <h2>事件流</h2>
            <small>{anchorISO ? '锚点窗口内终结事件' : `最近终结事件 · ${formatClock(now)} CST`}</small>
          </div>
          <EventTicker events={snapshot.events}/>
        </section>
      </div>
    </>}
    <BlockDrawer selected={selected} onClose={() => setSelected(null)}/>
  </main>
}

function ConnectionBadge({ state }: { state: 'connecting' | 'connected' | 'polling' | 'historical' }) {
  const labels = {
    connected: '实时已连接', polling: '轮询降级', connecting: '连接中…', historical: '历史回放',
  } as const
  return <span className={`df-conn df-conn-${state}`} role="status">
    <span className="df-conn-dot" aria-hidden="true"/>{labels[state]}
  </span>
}

type Verdict = { tone: 'ok' | 'warn' | 'risk'; title: string; detail: string }

function verdictOf(snapshot: import('./types').DataflowSnapshot | null, anchorISO: string | null): Verdict {
  if (!snapshot) return { tone: 'ok', title: '', detail: '' }
  const total = snapshot.radar_statuses.length
  const participating = snapshot.radar_statuses.filter(status => status.participating_in_latest_analysis).length
  const unavailable = snapshot.radar_statuses.filter(status => status.health === 'UNAVAILABLE').length
  const failed = snapshot.stages.reduce((sum, stage) => sum + stage.failed, 0)
  const running = snapshot.stages.reduce((sum, stage) => sum + stage.running, 0)
  const latestAnalysis = snapshot.analysis_blocks[snapshot.analysis_blocks.length - 1]
  const cycleText = latestAnalysis
    ? `${anchorISO ? '锚点周期' : '当前分析周期'} ${formatClock(latestAnalysis.analysis_time)} · ${latestAnalysis.status === 'ANALYSIS_READY' ? '已就绪' : latestAnalysis.status}`
    : '暂无分析周期'
  const detail = `${participating}/${total} 雷达参与 · ${cycleText}`
  if (failed > 0 || unavailable > 0) {
    return {
      tone: 'risk',
      title: `链路需要关注：${failed} 个阶段作业失败${unavailable > 0 ? ` · ${unavailable} 部雷达不可用` : ''}`,
      detail,
    }
  }
  if (running > 0) return { tone: 'ok', title: '链路正常运行', detail: `${detail} · ${running} 个阶段作业执行中` }
  return { tone: 'ok', title: anchorISO ? '历史窗口回放' : '链路空闲', detail }
}
