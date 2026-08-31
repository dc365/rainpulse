import { useCallback, useEffect, useRef, useState } from 'react'

export interface TimelineAsset {
  asset_id: string
  lead_time_minutes?: number | null
  valid_time?: string | null
}

interface NowcastTimelineProps {
  assets: TimelineAsset[]
  selectedAsset: TimelineAsset | null
  issueTime?: string | null
  fixedWindow: boolean
  productLabel: string
  mode?: 'forecast' | 'analysis'
  onSelect: (asset: TimelineAsset) => void
}

function formatUtc(value?: string | null) {
  if (!value) return '暂无'
  return `${new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'UTC',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value))} UTC`
}

function formatCst(value?: string | null, includeDate = false) {
  if (!value) return '暂无'
  const options: Intl.DateTimeFormatOptions = {
    timeZone: 'Asia/Taipei',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }
  if (includeDate) {
    options.month = '2-digit'
    options.day = '2-digit'
  }
  return `${new Intl.DateTimeFormat('zh-CN', options).format(new Date(value))} CST`
}

export function NowcastTimeline({
  assets,
  selectedAsset,
  issueTime,
  fixedWindow,
  productLabel,
  mode = 'forecast',
  onSelect,
}: NowcastTimelineProps) {
  const [playing, setPlaying] = useState(false)
  const railRef = useRef<HTMLDivElement>(null)
  const activeIndex = Math.max(0, assets.findIndex((asset) => asset.asset_id === selectedAsset?.asset_id))
  const activeLead = selectedAsset?.lead_time_minutes ?? 0
  const analysisMode = mode === 'analysis'
  const lastAsset = assets.at(-1) ?? null

  useEffect(() => {
    if (!playing || fixedWindow || assets.length < 2) return
    const timer = window.setInterval(() => {
      onSelect(assets[activeIndex >= assets.length - 1 ? 0 : activeIndex + 1])
    }, 900)
    return () => window.clearInterval(timer)
  }, [activeIndex, assets, fixedWindow, onSelect, playing])

  useEffect(() => {
    const rail = railRef.current
    const active = rail?.querySelector<HTMLElement>('[aria-current="step"]')
    if (!rail || !active) return
    const left = active.offsetLeft - (rail.clientWidth - active.clientWidth) / 2
    if (typeof rail.scrollTo === 'function') rail.scrollTo({ left, behavior: 'smooth' })
    else rail.scrollLeft = left
  }, [activeIndex])

  const scrubbingRef = useRef(false)

  const scrubTo = useCallback((clientX: number) => {
    const rail = railRef.current
    if (!rail || fixedWindow || assets.length < 2) return
    const rect = rail.getBoundingClientRect()
    if (rect.width <= 0) return
    const x = clientX - rect.left + rail.scrollLeft
    const ratio = Math.min(1, Math.max(0, x / rail.scrollWidth))
    const index = Math.round(ratio * (assets.length - 1))
    const asset = assets[index]
    if (asset) onSelect(asset)
  }, [assets, fixedWindow, onSelect])

  const endScrub = useCallback(() => {
    scrubbingRef.current = false
  }, [])

  const move = (delta: number) => {
    if (fixedWindow || !assets.length) return
    const nextIndex = Math.min(assets.length - 1, Math.max(0, activeIndex + delta))
    onSelect(assets[nextIndex])
  }

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'ArrowLeft') {
      event.preventDefault()
      move(-1)
    } else if (event.key === 'ArrowRight') {
      event.preventDefault()
      move(1)
    } else if (event.key === 'Home' && !fixedWindow && assets.length) {
      event.preventDefault()
      onSelect(assets[0])
    } else if (event.key === 'End' && !fixedWindow && assets.length) {
      event.preventDefault()
      onSelect(assets[assets.length - 1])
    }
  }

  return (
    <div
      className="nowcast-timeline"
      data-playing={playing}
      tabIndex={0}
      onKeyDown={handleKeyDown}
      aria-label="降水时间轴"
    >
      <header className="nowcast-timeline-toolbar">
        <div className="timeline-playback">
          <button type="button" disabled={fixedWindow || activeIndex === 0} aria-label="上一帧" onClick={() => move(-1)}><span aria-hidden="true">◀</span></button>
          <button
            type="button"
            className="timeline-play-button"
            disabled={fixedWindow || assets.length < 2}
            aria-pressed={playing}
            aria-label={playing ? '暂停时间轴' : '播放时间轴'}
            onClick={() => setPlaying((value) => !value)}
          >
            <span aria-hidden="true">{playing ? 'Ⅱ' : '▶'}</span>{playing ? '暂停' : '播放'}
          </button>
          <button type="button" disabled={fixedWindow || activeIndex >= assets.length - 1} aria-label="下一帧" onClick={() => move(1)}><span aria-hidden="true">▶</span></button>
        </div>
        <div className="timeline-active-state">
          <span>{playing ? <i aria-hidden="true" /> : null}{fixedWindow
            ? analysisMode ? '1 帧 · 周期 T0' : productLabel
            : `${assets.length} 帧 · 5 分钟间隔`}</span>
          <strong>{analysisMode
            ? `T0 · ${formatUtc(selectedAsset?.valid_time)}`
            : `T+${activeLead} · ${formatUtc(selectedAsset?.valid_time)}`}</strong>
        </div>
      </header>

      <div className="timeline-context-band" aria-hidden="true">
        <span>{analysisMode ? `分析 ${formatUtc(issueTime)}` : `起报 ${formatUtc(issueTime)}`}</span>
        <strong>{analysisMode ? '雷达实况 T0' : fixedWindow ? productLabel : '未来 0–2 小时'}</strong>
        <span>{analysisMode ? `有效 ${formatUtc(selectedAsset?.valid_time)}` : formatUtc(lastAsset?.valid_time)}</span>
      </div>

      <div
        className="nowcast-timeline-rail"
        ref={railRef}
        onPointerDown={(event) => {
          if (fixedWindow || assets.length < 2) return
          scrubbingRef.current = true
          event.currentTarget.setPointerCapture?.(event.pointerId)
          scrubTo(event.clientX)
        }}
        onPointerMove={(event) => {
          if (scrubbingRef.current) scrubTo(event.clientX)
        }}
        onPointerUp={endScrub}
        onPointerCancel={endScrub}
      >
        {assets.map((asset) => {
          const lead = asset.lead_time_minutes ?? 0
          const active = asset.asset_id === selectedAsset?.asset_id
          const major = analysisMode || lead === 60 || lead === 120
          return (
            <button
              type="button"
              key={asset.asset_id}
              className={active ? 'active' : ''}
              aria-current={active ? 'step' : undefined}
              aria-label={analysisMode
                ? `实况 T0，${formatCst(asset.valid_time, true)}，${formatUtc(asset.valid_time)}`
                : `T+${lead}，${formatUtc(asset.valid_time)}`}
              disabled={fixedWindow}
              data-major={major}
              data-frame-kind={analysisMode ? 'observation' : 'forecast'}
              onClick={() => onSelect(asset)}
            >
              <i />
              <span>{analysisMode ? 'T0' : `T+${lead}`}</span>
            </button>
          )
        })}
      </div>

      <footer className="timeline-footer">
        <span><i />当前时效</span>
        <span>{analysisMode ? `分析周期 ${formatUtc(issueTime)} · 实况为 T0` : `起报 ${formatUtc(issueTime)} · ← → 键逐帧查看`}</span>
      </footer>
    </div>
  )
}
