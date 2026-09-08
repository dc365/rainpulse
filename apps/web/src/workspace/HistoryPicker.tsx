import { useEffect, useRef, useState } from 'react'
import type { CycleSummary } from './model'

export function localCaseDate(value: string) {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(value))
}

export function HistoryPicker({ cycles, selectedID, onSelect }: {
  cycles: CycleSummary[]; selectedID: string; onSelect: (cycle: CycleSummary) => void
}) {
  const [open, setOpen] = useState(false)
  const [date, setDate] = useState('')
  const root = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const selected = cycles.find(c => c.cycle_id === selectedID)
  const dates = [...new Set(cycles.map(c => localCaseDate(c.issue_time)))].sort().reverse()
  const activeDate = dates.includes(date) ? date : selected ? localCaseDate(selected.issue_time) : dates[0]
  const options = cycles.filter(c => localCaseDate(c.issue_time) === activeDate).sort((a, b) => Date.parse(a.issue_time) - Date.parse(b.issue_time))
  const index = options.findIndex(c => c.cycle_id === selectedID)
  const time = (c: CycleSummary) => new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(c.issue_time))
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
      <button aria-label="上一起报时次" disabled={index <= 0} onClick={() => onSelect(options[index - 1])}>‹</button>
      <button aria-label="下一起报时次" disabled={index < 0 || index >= options.length - 1} onClick={() => onSelect(options[index + 1])}>›</button>
    </div>
    {open && <section id="history-case-options" className="case-popover" aria-label="历史案例选择">
      <header><strong>选择历史案例</strong><button aria-label="关闭案例选择" onClick={() => { setOpen(false); trigger.current?.focus() }}>×</button></header>
      <div className="case-dates" aria-label="案例日期">{dates.map(d => <button key={d} aria-pressed={d === activeDate} onClick={() => setDate(d)}>{d}</button>)}</div>
      <p>起报时次 <span>北京时间（UTC+8） · {options.length} 个可用时次</span></p>
      <div className="case-times">{options.map(c => <button key={c.cycle_id} aria-pressed={c.cycle_id === selectedID} title={`可用：${Object.entries(c.capabilities).filter(([, ready]) => ready).map(([name]) => name === 'radar' ? 'QPE' : name.toUpperCase()).join(' / ')}`} onClick={() => { onSelect(c); setOpen(false); trigger.current?.focus() }}>{time(c)}</button>)}</div>
      <footer>仅列出已有结果的时次；下方时间轴用于查看预报时效。</footer>
    </section>}
  </div>
}
