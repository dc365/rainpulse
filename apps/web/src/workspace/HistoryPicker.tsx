import { useEffect, useRef, useState } from 'react'
import { TIMELINE_STEP_MINUTES } from './cadence'
import type { CycleSummary } from './model'

export function localCaseDate(value: string) {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(value))
}

// The case panel works on the same six-minute clock as the shared timeline: one
// row per slot. A legacy five-minute result is filed into its six-minute slot
// but keeps its real issue time as a sub-label, so a row never pretends the
// cycle starts at the slot and selecting it still loads the real cycle.
const dayStart = '00:00'
const dayEnd = '23:59'

const minutesOfClock = (value: string) => {
  const [hour, minute] = value.split(':').map(Number)
  return Number.isFinite(hour) && Number.isFinite(minute) ? hour * 60 + minute : null
}
const clockLabel = (total: number) => `${String(Math.floor(total / 60) % 24).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`

export function caseSlot(value: string, step = TIMELINE_STEP_MINUTES) {
  const minutes = minutesOfClock(value)
  if (minutes == null) return value
  const snapped = Math.ceil(minutes / step) * step
  // Already on the clock, or the slot would cross midnight: keep the real time.
  if (snapped === minutes || snapped >= 24 * 60) return value
  return clockLabel(snapped)
}

// One case per six-minute slot; when a slot holds several five-minute results,
// the one closest to the slot (the latest) represents it.
export function caseSlots(options: CycleSummary[], time: (cycle: CycleSummary) => string) {
  const buckets = new Map<string, CycleSummary>()
  for (const cycle of options) {
    const slot = caseSlot(time(cycle))
    const current = buckets.get(slot)
    if (!current || time(cycle) > time(current)) buckets.set(slot, cycle)
  }
  return [...buckets.entries()].sort(([left], [right]) => left.localeCompare(right))
}

export function HistoryPicker({ cycles, selectedID, onSelect }: {
  cycles: CycleSummary[]; selectedID: string; onSelect: (cycle: CycleSummary) => void
}) {
  const [open, setOpen] = useState(false)
  const [date, setDate] = useState('')
  const [startTime, setStartTime] = useState(dayStart)
  const [endTime, setEndTime] = useState(dayEnd)
  const root = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const selected = cycles.find(c => c.cycle_id === selectedID)
  const dates = [...new Set(cycles.map(c => localCaseDate(c.issue_time)))].sort().reverse()
  const activeDate = dates.includes(date) ? date : selected ? localCaseDate(selected.issue_time) : dates[0]
  const time = (c: CycleSummary) => new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(c.issue_time))
  const dayOptions = cycles.filter(c => localCaseDate(c.issue_time) === activeDate).sort((a, b) => Date.parse(a.issue_time) - Date.parse(b.issue_time))
  const invalidRange = !startTime || !endTime || startTime > endTime
  const slots = invalidRange ? [] : caseSlots(dayOptions.filter(c => {
    const slot = caseSlot(time(c))
    return slot >= startTime && slot <= endTime
  }), time)
  const daySlotCount = caseSlots(dayOptions, time).length
  const index = slots.findIndex(([, cycle]) => cycle.cycle_id === selectedID)
  const resetRange = () => { setStartTime(dayStart); setEndTime(dayEnd) }
  useEffect(() => {
    if (!open) return
    const close = (e: PointerEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('pointerdown', close)
    return () => document.removeEventListener('pointerdown', close)
  }, [open])
  return <div className="case-picker" ref={root} onKeyDown={e => {
    if (e.key === 'Escape') { setOpen(false); trigger.current?.focus() }
  }}>
    <button ref={trigger} className="case-trigger" aria-expanded={open} aria-controls="history-case-options" disabled={!cycles.length} onClick={() => {
      if (!open) setDate(selected ? localCaseDate(selected.issue_time) : dates[0] ?? '')
      setOpen(!open)
    }}><span><small>历史案例 · 北京时间</small><strong>{selected ? `${localCaseDate(selected.issue_time)} / ${time(selected)} 起报` : '选择案例日期与时次'}</strong></span><span aria-hidden="true">⌄</span></button>
    <div className="case-step" aria-label="切换起报时次">
      <button aria-label="上一起报时次" disabled={index <= 0} onClick={() => onSelect(slots[index - 1][1])}>‹</button>
      <button aria-label="下一起报时次" disabled={index < 0 || index >= slots.length - 1} onClick={() => onSelect(slots[index + 1][1])}>›</button>
    </div>
    {open && <section id="history-case-options" className="case-popover" aria-label="历史案例选择">
      <header><strong>选择历史案例</strong><button aria-label="关闭案例选择" onClick={() => { setOpen(false); trigger.current?.focus() }}>×</button></header>
      <div className="case-dates" aria-label="案例日期">{dates.map(d => <button key={d} aria-pressed={d === activeDate} onClick={() => { setDate(d); resetRange() }}>{d}</button>)}</div>
      <div className="case-range" role="group" aria-label="起报时间范围（北京时间）">
        <label>开始时间<input type="time" step={TIMELINE_STEP_MINUTES * 60} value={startTime} aria-invalid={invalidRange} onChange={e => setStartTime(caseSlot(e.target.value))} /></label>
        <label>结束时间<input type="time" step={TIMELINE_STEP_MINUTES * 60} value={endTime} aria-invalid={invalidRange} onChange={e => setEndTime(caseSlot(e.target.value))} /></label>
        <button onClick={resetRange} aria-pressed={startTime === dayStart && endTime === dayEnd}>全天</button>
      </div>
      <p className="case-range-hint">{TIMELINE_STEP_MINUTES} 分钟档位；每档取一个已有案例，小字为实际起报时间。</p>
      {invalidRange && <p role="alert" className="case-range-error">{!startTime || !endTime ? '请填写完整的开始和结束时间。' : '开始时间不能晚于结束时间；跨日请切换案例日期。'}</p>}
      <p>已有结果 <span>北京时间（UTC+8） · {slots.length} / {daySlotCount} 个档位</span></p>
      {dayOptions.length > 0 && <p className="case-coverage">当天可用范围：{time(dayOptions[0])} 至 {time(dayOptions[dayOptions.length - 1])}</p>}
      <div className="case-times">{slots.map(([slot, cycle]) => {
        const actual = time(cycle)
        const capabilities = Object.entries(cycle.capabilities).filter(([, ready]) => ready).map(([name]) => name === 'radar' ? 'QPE' : name.toUpperCase()).join(' / ')
        return <button key={cycle.cycle_id} aria-pressed={cycle.cycle_id === selectedID}
          aria-label={actual === slot ? slot : `${slot}（实际 ${actual}）`}
          title={`${actual === slot ? '' : `实际起报 ${actual} · `}可用：${capabilities}`}
          onClick={() => { onSelect(cycle); setOpen(false); trigger.current?.focus() }}>
          <span>{slot}</span>{actual !== slot && <small>{actual}</small>}
        </button>
      })}</div>
      {!invalidRange && !slots.length && <p role="status">该时段暂无可用结果，不代表没有雷达源数据。</p>}
      <footer>这里选择起报时次，下方时间轴查看预报时效。筛选不会启动计算；源数据已有但尚未处理的时次，需完成处理后才会列出。</footer>
    </section>}
  </div>
}
