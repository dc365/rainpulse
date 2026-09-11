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
  const [startTime, setStartTime] = useState('00:00')
  const [endTime, setEndTime] = useState('23:59')
  const root = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const selected = cycles.find(c => c.cycle_id === selectedID)
  const dates = [...new Set(cycles.map(c => localCaseDate(c.issue_time)))].sort().reverse()
  const activeDate = dates.includes(date) ? date : selected ? localCaseDate(selected.issue_time) : dates[0]
  const time = (c: CycleSummary) => new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(c.issue_time))
  const dayOptions = cycles.filter(c => localCaseDate(c.issue_time) === activeDate).sort((a, b) => Date.parse(a.issue_time) - Date.parse(b.issue_time))
  const invalidRange = !startTime || !endTime || startTime > endTime
  const options = invalidRange ? [] : dayOptions.filter(c => time(c) >= startTime && time(c) <= endTime)
  const index = options.findIndex(c => c.cycle_id === selectedID)
  const resetRange = () => { setStartTime('00:00'); setEndTime('23:59') }
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
      <div className="case-dates" aria-label="案例日期">{dates.map(d => <button key={d} aria-pressed={d === activeDate} onClick={() => { setDate(d); resetRange() }}>{d}</button>)}</div>
      <div className="case-range" role="group" aria-label="起报时间范围（北京时间）">
        <label>开始时间<input type="time" value={startTime} aria-invalid={invalidRange} onChange={e => setStartTime(e.target.value)} /></label>
        <label>结束时间<input type="time" value={endTime} aria-invalid={invalidRange} onChange={e => setEndTime(e.target.value)} /></label>
        <button onClick={resetRange} aria-pressed={startTime === '00:00' && endTime === '23:59'}>全天</button>
      </div>
      {invalidRange && <p role="alert" className="case-range-error">{!startTime || !endTime ? '请填写完整的开始和结束时间。' : '开始时间不能晚于结束时间；跨日请切换案例日期。'}</p>}
      <p>已有结果 <span>北京时间（UTC+8） · {options.length} / {dayOptions.length} 个时次</span></p>
      {dayOptions.length > 0 && <p className="case-coverage">当天可用范围：{time(dayOptions[0])} 至 {time(dayOptions[dayOptions.length - 1])}</p>}
      <div className="case-times">{options.map(c => <button key={c.cycle_id} aria-pressed={c.cycle_id === selectedID} title={`可用：${Object.entries(c.capabilities).filter(([, ready]) => ready).map(([name]) => name === 'radar' ? 'QPE' : name.toUpperCase()).join(' / ')}`} onClick={() => { onSelect(c); setOpen(false); trigger.current?.focus() }}>{time(c)}</button>)}</div>
      {!invalidRange && !options.length && <p role="status">该时段暂无可用结果，不代表没有雷达源数据。</p>}
      <footer>这里选择起报时次，下方时间轴查看预报时效。筛选不会启动计算；源数据已有但尚未处理的时次，需完成处理后才会列出。</footer>
    </section>}
  </div>
}
