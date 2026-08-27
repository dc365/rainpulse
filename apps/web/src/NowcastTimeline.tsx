import { useCallback, useEffect, useRef, useState } from 'react'

import type { components } from './api/generated/schema'

type ProductAsset = components['schemas']['ProductAsset']

interface NowcastTimelineProps {
  assets: ProductAsset[]
  selectedAsset: ProductAsset | null
  issueTime?: string | null
  fixedWindow: boolean
  productLabel: string
  onSelect: (asset: ProductAsset) => void
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

export function NowcastTimeline({
  assets,
  selectedAsset,
  issueTime,
  fixedWindow,
  productLabel,
  onSelect,
}: NowcastTimelineProps) {
  const [playing, setPlaying] = useState(false)
  const railRef = useRef<HTMLDivElement>(null)
  const activeIndex = Math.max(0, assets.findIndex((asset) => asset.asset_id === selectedAsset?.asset_id))
  const activeLead = selectedAsset?.lead_time_minutes ?? 0

  useEffect(() => {
    if (!playing || fixedWindow || assets.length < 2) return
    const timer = window.setInterval(() => {
      onSelect(assets[activeIndex >= assets.length - 1 ? 0 : activeIndex + 1])
    }, 900)
    return () => window.clearInterval(timer)
  }, [activeIndex, assets, fixedWindow, onSelect, playing])

  useEffect(() => {
    const active = railRef.current?.querySelector<HTMLElement>('[aria-current="step"]')
    if (typeof active?.scrollIntoView === 'function') {
      active.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' })
    }
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
      aria-label="五分钟预报时间轴"
    >
      <header className="nowcast-timeline-toolbar">
        <div className="timeline-playback">
          <button type="button" disabled={fixedWindow || activeIndex === 0} aria-label="上一个时效" onClick={() => move(-1)}><span aria-hidden="true">◀</span></button>
          <button
            type="button"
            className="timeline-play-button"
            disabled={fixedWindow || assets.length < 2}
            aria-pressed={playing}
            aria-label={playing ? '暂停时效播放' : '播放全部时效'}
            onClick={() => setPlaying((value) => !value)}
          >
            <span aria-hidden="true">{playing ? 'Ⅱ' : '▶'}</span>{playing ? '暂停' : '播放'}
          </button>
          <button type="button" disabled={fixedWindow || activeIndex >= assets.length - 1} aria-label="下一个时效" onClick={() => move(1)}><span aria-hidden="true">▶</span></button>
        </div>
        <div className="timeline-active-state">
          <span>{playing ? <i aria-hidden="true" /> : null}{fixedWindow ? productLabel : `${assets.length} 帧 · 5 分钟间隔`}</span>
          <strong>T+{activeLead} · {formatUtc(selectedAsset?.valid_time)}</strong>
        </div>
      </header>

      {!fixedWindow ? (
        <div className="timeline-hour-bands" aria-hidden="true">
          <span>0–1 小时</span>
          <span>1–2 小时</span>
        </div>
      ) : null}

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
          return (
            <button
              type="button"
              key={asset.asset_id}
              className={active ? 'active' : ''}
              aria-current={active ? 'step' : undefined}
              aria-label={`T+${lead}，${formatUtc(asset.valid_time)}`}
              disabled={fixedWindow}
              data-major={lead === 60 || lead === 120}
              onClick={() => onSelect(asset)}
            >
              <i />
              <span>T+{lead}</span>
            </button>
          )
        })}
      </div>

      <footer className="timeline-footer">
        <span><i />当前时效</span>
        <span>起报 {formatUtc(issueTime)} · ← → 键逐帧查看</span>
      </footer>
    </div>
  )
}
