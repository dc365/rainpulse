import { useEffect, useMemo, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'

import type { components } from './api/generated/schema'
import { VerificationMapMatrix } from './VerificationMapMatrix'

type RunSummary = components['schemas']['AlgorithmVerificationRunSummary']
type RunDetail = components['schemas']['AlgorithmVerificationRunDetail']
type VerificationCase = components['schemas']['AlgorithmVerificationCase']
type VerificationMetric = components['schemas']['AlgorithmVerificationMetric']
type SkillComparison = components['schemas']['AlgorithmVerificationSkillComparison']
type VerificationMapFrame = components['schemas']['AlgorithmVerificationMapFrame']

const modelLabels: Record<string, string> = {
  lk: 'pySTEPS-LK',
  persistence: '持续性',
  translation: '平移基线',
}

const statusLabels: Record<string, string> = {
  lk_supported: 'LK 获得支持',
  translation_baseline_retained: '保留平移基线',
  skill_not_demonstrated: '尚未证明技能',
  insufficient_evidence: '证据不足',
}

const truthLabels: Record<string, string> = {
  observed_mrms_10min: 'MRMS 10 分钟实况',
}

interface AlgorithmVerificationWorkspaceProps {
  refreshToken: number
}

export function AlgorithmVerificationWorkspace({ refreshToken }: AlgorithmVerificationWorkspaceProps) {
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [selectedRunKey, setSelectedRunKey] = useState('')
  const [detail, setDetail] = useState<RunDetail | null>(null)
  const [selectedCaseID, setSelectedCaseID] = useState('')
  const [selectedIssueTime, setSelectedIssueTime] = useState('')
  const [threshold, setThreshold] = useState(5)
  const [windowPixels, setWindowPixels] = useState(11)
  const [baseline, setBaseline] = useState('translation')
  const [selectedLeadMinutes, setSelectedLeadMinutes] = useState(60)
  const [metrics, setMetrics] = useState<VerificationMetric[]>([])
  const [mapFrame, setMapFrame] = useState<VerificationMapFrame | null>(null)
  const [mapError, setMapError] = useState<string | null>(null)
  const [loadingMap, setLoadingMap] = useState(false)
  const [playing, setPlaying] = useState(false)
  const [loadingRuns, setLoadingRuns] = useState(true)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [loadingMetrics, setLoadingMetrics] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    const kickoff = window.setTimeout(() => {
      setLoadingRuns(true)
      void fetch('/api/v1/algorithm-verification/runs', { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`验证目录响应异常（${response.status}）`)
        return response.json() as Promise<components['schemas']['AlgorithmVerificationRunList']>
      })
      .then((payload) => {
        setRuns(payload.items)
        setSelectedRunKey((current) => {
          if (current && payload.items.some((run) => runKey(run) === current)) return current
          return payload.items[0] ? runKey(payload.items[0]) : ''
        })
        setError(null)
      })
      .catch((requestError: unknown) => {
        if (!(requestError instanceof DOMException && requestError.name === 'AbortError')) {
          setError(requestError instanceof Error ? requestError.message : '验证目录读取失败')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingRuns(false)
      })
    }, 0)
    return () => {
      controller.abort()
      window.clearTimeout(kickoff)
    }
  }, [refreshToken])

  const selectedRun = useMemo(
    () => runs.find((run) => runKey(run) === selectedRunKey) ?? null,
    [runs, selectedRunKey],
  )

  useEffect(() => {
    if (!selectedRun) return
    const controller = new AbortController()
    const kickoff = window.setTimeout(() => {
      setLoadingDetail(true)
      const url = `/api/v1/algorithm-verification/runs/${encodeURIComponent(selectedRun.profile_version)}/${encodeURIComponent(selectedRun.run_id)}`
      void fetch(url, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`验证运行响应异常（${response.status}）`)
        return response.json() as Promise<RunDetail>
      })
      .then((payload) => {
        setDetail(payload)
        const nextCase = pickCase(payload.cases, selectedCaseID)
        setSelectedCaseID(nextCase?.case_id ?? '')
        setSelectedIssueTime(nextIssue(nextCase, selectedIssueTime))
        setThreshold((current) => pickNumber(payload.filters.thresholds_mm_h, current, 5))
        setWindowPixels((current) => pickNumber(payload.filters.windows_pixels, current, 11))
        setSelectedLeadMinutes((current) => pickNumber(payload.filters.lead_minutes, current, 60))
        setBaseline((current) => payload.filters.models.includes(current)
          ? current
          : payload.filters.models.find((model) => model !== 'lk') ?? 'persistence')
        setError(null)
      })
      .catch((requestError: unknown) => {
        if (!(requestError instanceof DOMException && requestError.name === 'AbortError')) {
          setDetail(null)
          setError(requestError instanceof Error ? requestError.message : '验证运行读取失败')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingDetail(false)
      })
    }, 0)
    return () => {
      controller.abort()
      window.clearTimeout(kickoff)
    }
    // Selection is intentionally preserved only when it exists in the next run.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedRun])

  useEffect(() => {
    if (!selectedRun || !selectedCaseID || !selectedIssueTime) return
    const controller = new AbortController()
    const kickoff = window.setTimeout(() => {
      const query = new URLSearchParams({
        case_id: selectedCaseID,
        issue_time: selectedIssueTime,
        threshold_mm_h: String(threshold),
        window_pixels: String(windowPixels),
      })
      setLoadingMetrics(true)
      void fetch(
        `/api/v1/algorithm-verification/runs/${encodeURIComponent(selectedRun.profile_version)}/${encodeURIComponent(selectedRun.run_id)}/metrics?${query}`,
        { signal: controller.signal },
      )
      .then(async (response) => {
        if (!response.ok) throw new Error(`验证指标响应异常（${response.status}）`)
        return response.json() as Promise<components['schemas']['AlgorithmVerificationMetricList']>
      })
      .then((payload) => {
        setMetrics(payload.items)
        setError(null)
      })
      .catch((requestError: unknown) => {
        if (!(requestError instanceof DOMException && requestError.name === 'AbortError')) {
          setMetrics([])
          setError(requestError instanceof Error ? requestError.message : '验证指标读取失败')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingMetrics(false)
      })
    }, 0)
    return () => {
      controller.abort()
      window.clearTimeout(kickoff)
    }
  }, [selectedCaseID, selectedIssueTime, selectedRun, threshold, windowPixels])

  useEffect(() => {
    if (!selectedRun || !selectedRun.maps_available || !selectedCaseID || !selectedIssueTime) {
      return
    }
    const controller = new AbortController()
    const kickoff = window.setTimeout(() => {
      const query = new URLSearchParams({
        case_id: selectedCaseID,
        issue_time: selectedIssueTime,
        lead_minutes: String(selectedLeadMinutes),
      })
      setLoadingMap(true)
      setMapError(null)
      void fetch(
        `/api/v1/algorithm-verification/runs/${encodeURIComponent(selectedRun.profile_version)}/${encodeURIComponent(selectedRun.run_id)}/map-frame?${query}`,
        { signal: controller.signal },
      )
      .then(async (response) => {
        if (!response.ok) throw new Error(`验证地图响应异常（${response.status}）`)
        return response.json() as Promise<VerificationMapFrame>
      })
      .then((payload) => {
        setMapFrame(payload)
        setMapError(null)
      })
      .catch((requestError: unknown) => {
        if (!(requestError instanceof DOMException && requestError.name === 'AbortError')) {
          setMapFrame(null)
          setMapError(requestError instanceof Error ? requestError.message : '验证地图读取失败')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingMap(false)
      })
    }, 0)
    return () => {
      controller.abort()
      window.clearTimeout(kickoff)
    }
  }, [selectedCaseID, selectedIssueTime, selectedLeadMinutes, selectedRun])

  const leadMinutes = useMemo(() => detail?.filters.lead_minutes ?? [], [detail])
  useEffect(() => {
    if (!playing || leadMinutes.length < 2) return
    const timer = window.setInterval(() => {
      setSelectedLeadMinutes((current) => {
        const index = leadMinutes.indexOf(current)
        return leadMinutes[(index + 1 + leadMinutes.length) % leadMinutes.length]
      })
    }, 1400)
    return () => window.clearInterval(timer)
  }, [leadMinutes, playing])

  const activeCase = detail?.cases.find((item) => item.case_id === selectedCaseID) ?? null
  const selectedRows = useMemo(
    () => buildLeadRows(metrics, detail?.filters.lead_minutes ?? [], baseline),
    [baseline, detail?.filters.lead_minutes, metrics],
  )
  const selectedLeadRow = selectedRows.find((row) => row.lead === selectedLeadMinutes)
  const activeMapFrame = mapFrame
    && selectedRun?.maps_available
    && mapFrame.profile_version === selectedRun.profile_version
    && mapFrame.run_id === selectedRun.run_id
    && mapFrame.case_id === selectedCaseID
    && mapFrame.issue_time === selectedIssueTime
    && mapFrame.lead_minutes === selectedLeadMinutes
    ? mapFrame
    : null

  return (
    <section className="verification-page" aria-labelledby="verification-title">
      <header className="page-heading verification-heading">
        <div>
          <p className="section-kicker">Algorithm evidence / RP-016</p>
          <h1 id="verification-title">算法离线验证</h1>
          <p>用冻结实况与强基线核对通用短临算法；当前数据集为美国 MRMS，后续福建案例沿用同一指标契约。</p>
        </div>
        <span className="verification-boundary">工程证据 · 非业务验收</span>
      </header>

      {error ? <div className="error-banner" role="alert"><strong>验证数据读取失败</strong><span>{error}</span></div> : null}

      {selectedRun ? (
        <section className="verification-ledger" aria-label="验证运行摘要">
          <LedgerItem label="完成起报" value={`${selectedRun.completed_issue_count}`} note={`${selectedRun.failed_issue_count} 个失败`} />
          <LedgerItem label="评分记录" value={selectedRun.metric_row_count.toLocaleString('zh-CN')} note="共同有效域" />
          <LedgerItem label="运动回退" value={`${selectedRun.motion_fallback_issue_count}`} note="可追踪而非隐藏" tone={selectedRun.motion_fallback_issue_count > 0 ? 'caution' : 'neutral'} />
          <LedgerItem label="技能门禁" value={statusLabels[selectedRun.skill_status] ?? selectedRun.skill_status} note="LK 对两个基线" tone={selectedRun.skill_status === 'lk_supported' ? 'supported' : 'caution'} />
        </section>
      ) : null}

      {loadingRuns ? <p className="verification-empty">正在读取离线验证目录…</p> : null}
      {!loadingRuns && runs.length === 0 ? (
        <p className="verification-empty">尚未挂载算法验证报告。运行产物保持在服务器，只通过控制面读取。</p>
      ) : null}

      {selectedRun ? (
        <div className="verification-layout">
          <VerificationNavigator
            runs={runs}
            selectedRunKey={selectedRunKey}
            cases={detail?.cases ?? []}
            selectedCaseID={selectedCaseID}
            onRunChange={(key) => {
              setPlaying(false)
              setSelectedRunKey(key)
            }}
            onCaseChange={(item) => {
              setPlaying(false)
              setSelectedCaseID(item.case_id)
              setSelectedIssueTime(nextIssue(item, ''))
            }}
          />

          <section className="verification-console" aria-live="polite">
            {loadingDetail || !detail ? (
              <p className="verification-empty">正在建立验证索引…</p>
            ) : (
              <>
                <header className="verification-console-heading">
                  <div>
                    <span>{truthLabels[detail.run.primary_truth_kind] ?? detail.run.primary_truth_kind}</span>
                    <strong>{detail.run.profile_version}</strong>
                    <small>{detail.run.run_id} · {formatUTC(detail.run.modified_at)}</small>
                  </div>
                  <span className={`verification-status ${detail.run.skill_status === 'lk_supported' ? 'supported' : 'caution'}`}>
                    {statusLabels[detail.run.skill_status] ?? detail.run.skill_status}
                  </span>
                </header>

                <SkillGateMatrix
                  comparisons={detail.skill_summary.comparisons}
                  selectedBaseline={baseline}
                  selectedThreshold={threshold}
                  onSelect={(nextBaseline, nextThreshold) => {
                    setBaseline(nextBaseline)
                    setThreshold(nextThreshold)
                  }}
                />

                <div className="verification-controls">
                  <ControlGroup label="比较基线">
                    {detail.filters.models.filter((model) => model !== 'lk').map((model) => (
                      <ChoiceButton key={model} active={baseline === model} onClick={() => setBaseline(model)}>
                        {modelLabels[model] ?? model}
                      </ChoiceButton>
                    ))}
                  </ControlGroup>
                  <ControlGroup label="降水阈值">
                    {detail.filters.thresholds_mm_h.map((value) => (
                      <ChoiceButton key={value} active={threshold === value} onClick={() => setThreshold(value)}>
                        {value} mm/h
                      </ChoiceButton>
                    ))}
                  </ControlGroup>
                  <ControlGroup label="FSS 邻域">
                    {detail.filters.windows_pixels.map((value) => (
                      <ChoiceButton key={value} active={windowPixels === value} onClick={() => setWindowPixels(value)}>
                        {value} px
                      </ChoiceButton>
                    ))}
                  </ControlGroup>
                </div>

                <IssueRail
                  verificationCase={activeCase}
                  selectedIssueTime={selectedIssueTime}
                  onSelect={(value) => {
                    setPlaying(false)
                    setSelectedIssueTime(value)
                  }}
                />

                <LeadEvidenceRail
                  rows={selectedRows}
                  loading={loadingMetrics}
                  selectedLead={selectedLeadMinutes}
                  onSelect={setSelectedLeadMinutes}
                />

                <VerificationMapMatrix
                  frame={activeMapFrame}
                  baseline={baseline}
                  lkMetric={selectedLeadRow?.lk}
                  baselineMetric={selectedLeadRow?.baseline}
                  loading={loadingMap}
                  error={mapError}
                  mapsAvailable={detail.run.maps_available}
                  playing={playing}
                  onTogglePlaying={() => setPlaying((value) => !value)}
                />

                <div className="verification-evidence-grid">
                  <FSSChart rows={selectedRows} baseline={baseline} />
                  <EvidenceNotes
                    verificationCase={activeCase}
                    issueTime={selectedIssueTime}
                    threshold={threshold}
                    windowPixels={windowPixels}
                    truthKind={detail.run.primary_truth_kind}
                  />
                </div>

                <MetricTable rows={selectedRows} baseline={baseline} loading={loadingMetrics} />
              </>
            )}
          </section>
        </div>
      ) : null}
    </section>
  )
}

function VerificationNavigator({
  runs,
  selectedRunKey,
  cases,
  selectedCaseID,
  onRunChange,
  onCaseChange,
}: {
  runs: RunSummary[]
  selectedRunKey: string
  cases: VerificationCase[]
  selectedCaseID: string
  onRunChange: (key: string) => void
  onCaseChange: (item: VerificationCase) => void
}) {
  return (
    <aside className="verification-navigator" aria-label="验证运行与案例">
      <div className="verification-nav-section">
        <div className="verification-nav-heading"><span>Runs</span><strong>验证运行</strong></div>
        <div className="verification-run-list">
          {runs.map((run) => (
            <button
              type="button"
              key={runKey(run)}
              className={selectedRunKey === runKey(run) ? 'selected' : ''}
              aria-pressed={selectedRunKey === runKey(run)}
              onClick={() => onRunChange(runKey(run))}
            >
              <span className={`verification-run-mark ${run.skill_status === 'lk_supported' ? 'supported' : 'caution'}`} />
              <span><strong>{run.run_id}</strong><small>{run.profile_version}</small></span>
              <em>{run.completed_issue_count}</em>
            </button>
          ))}
        </div>
      </div>
      <div className="verification-nav-section case-section">
        <div className="verification-nav-heading"><span>Frozen cases</span><strong>冻结案例</strong></div>
        <div className="verification-case-list">
          {cases.map((item) => (
            <button
              type="button"
              key={item.case_id}
              className={selectedCaseID === item.case_id ? 'selected' : ''}
              aria-pressed={selectedCaseID === item.case_id}
              onClick={() => onCaseChange(item)}
            >
              <span><strong>{formatCaseName(item.case_id)}</strong><small>{item.case_id}</small></span>
              <em>{item.issue_times.length}</em>
            </button>
          ))}
        </div>
      </div>
      <p className="verification-scope-note">当前结论只验证算法适配、因果重采样、LK 执行和确定性技巧，不验证福建雷达质控与业务可用性。</p>
    </aside>
  )
}

function SkillGateMatrix({
  comparisons,
  selectedBaseline,
  selectedThreshold,
  onSelect,
}: {
  comparisons: SkillComparison[]
  selectedBaseline: string
  selectedThreshold: number
  onSelect: (baseline: string, threshold: number) => void
}) {
  const baselines = ['persistence', 'translation'].filter((baseline) =>
    comparisons.some((comparison) => comparison.baseline === baseline))
  const thresholds = Array.from(new Set(comparisons.map((comparison) => comparison.threshold_mm_h))).sort((a, b) => a - b)
  return (
    <section className="skill-gate-panel" aria-labelledby="skill-gate-title">
      <header>
        <div><span>Frozen acceptance gate</span><h2 id="skill-gate-title">LK 相对基线的 FSS 技巧</h2></div>
        <small>湿案例 · +10～+60 分钟 · 11 px · 95% 区间</small>
      </header>
      <div className="skill-gate-grid" style={{ '--threshold-count': thresholds.length } as CSSProperties}>
        <span className="skill-grid-corner">基线</span>
        {thresholds.map((value) => <strong className="skill-grid-head" key={value}>{value} mm/h</strong>)}
        {baselines.flatMap((baseline) => [
          <strong className="skill-grid-baseline" key={`${baseline}-label`}>{modelLabels[baseline] ?? baseline}</strong>,
          ...thresholds.map((value) => {
            const comparison = comparisons.find((item) => item.baseline === baseline && item.threshold_mm_h === value)
            const active = selectedBaseline === baseline && selectedThreshold === value
            return (
              <button
                type="button"
                key={`${baseline}-${value}`}
                className={`${comparison?.passes_case_gate ? 'passed' : 'failed'} ${active ? 'active' : ''}`}
                aria-pressed={active}
                onClick={() => onSelect(baseline, value)}
              >
                <strong>{formatSigned(comparison?.mean_fss_difference)}</strong>
                <small>{comparison ? `${comparison.positive_case_count}/${comparison.total_wet_case_count} 案例` : '无数据'}</small>
                <span>{formatInterval(comparison?.mean_difference_95pct_interval)}</span>
              </button>
            )
          }),
        ])}
      </div>
    </section>
  )
}

function ControlGroup({ label, children }: { label: string; children: ReactNode }) {
  return <div className="verification-control-group"><span>{label}</span><div>{children}</div></div>
}

function ChoiceButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return <button type="button" className={active ? 'active' : ''} aria-pressed={active} onClick={onClick}>{children}</button>
}

function IssueRail({
  verificationCase,
  selectedIssueTime,
  onSelect,
}: {
  verificationCase: VerificationCase | null
  selectedIssueTime: string
  onSelect: (value: string) => void
}) {
  return (
    <section className="verification-issue-rail" aria-label="起报时间">
      <header><span>Issue time / UTC</span><strong>{verificationCase ? formatCaseName(verificationCase.case_id) : '—'}</strong></header>
      <div>
        {verificationCase?.issue_times.map((issueTime) => (
          <button
            type="button"
            key={issueTime}
            className={selectedIssueTime === issueTime ? 'active' : ''}
            aria-pressed={selectedIssueTime === issueTime}
            onClick={() => onSelect(issueTime)}
          >
            <i /><strong>{formatIssueHour(issueTime)}</strong><small>{formatIssueDate(issueTime)}</small>
          </button>
        ))}
      </div>
    </section>
  )
}

interface LeadRow {
  lead: number
  lk?: VerificationMetric
  baseline?: VerificationMetric
  delta: number | null
}

function LeadEvidenceRail({
  rows,
  loading,
  selectedLead,
  onSelect,
}: {
  rows: LeadRow[]
  loading: boolean
  selectedLead: number
  onSelect: (lead: number) => void
}) {
  return (
    <section className="lead-evidence" aria-label="时效证据带">
      <header><span>Lead evidence</span><strong>{loading ? '正在筛选…' : 'LK − 基线 FSS'}</strong></header>
      <div>
        {rows.map((row) => (
          <button
            type="button"
            className={`${row.delta == null ? 'unknown' : row.delta >= 0 ? 'positive' : 'negative'} ${selectedLead === row.lead ? 'active' : ''}`}
            aria-pressed={selectedLead === row.lead}
            onClick={() => onSelect(row.lead)}
            key={row.lead}
          >
            <i /><strong>+{row.lead}</strong><small>{formatSigned(row.delta)}</small>
          </button>
        ))}
      </div>
    </section>
  )
}

function FSSChart({ rows, baseline }: { rows: LeadRow[]; baseline: string }) {
  const width = 720
  const height = 230
  const left = 44
  const right = 16
  const top = 18
  const bottom = 36
  const plotWidth = width - left - right
  const plotHeight = height - top - bottom
  const point = (index: number, value: number) => ({
    x: left + (rows.length <= 1 ? 0 : index * plotWidth / (rows.length - 1)),
    y: top + (1 - Math.max(0, Math.min(1, value))) * plotHeight,
  })
  const series = [
    { key: 'lk', label: 'pySTEPS-LK', className: 'lk', values: rows.map((row) => row.lk?.fss ?? null) },
    { key: baseline, label: modelLabels[baseline] ?? baseline, className: 'baseline', values: rows.map((row) => row.baseline?.fss ?? null) },
  ]
  const hasValues = series.some((item) => item.values.some((value) => value != null))
  return (
    <figure className="verification-chart">
      <figcaption><div><span>Fractions skill score</span><strong>逐时效 FSS</strong></div><small>0–1，越高越好</small></figcaption>
      {hasValues ? (
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="LK 与基线逐时效 FSS 折线图">
          {[0, .25, .5, .75, 1].map((tick) => {
            const y = top + (1 - tick) * plotHeight
            return <g key={tick}><line x1={left} x2={width - right} y1={y} y2={y} /><text x={left - 8} y={y + 3} textAnchor="end">{tick.toFixed(2)}</text></g>
          })}
          {series.map((item) => {
            const points = item.values.flatMap((value, index) => value == null ? [] : [point(index, value)])
            return (
              <g className={item.className} key={item.key}>
                {points.length > 1 ? <polyline points={points.map(({ x, y }) => `${x},${y}`).join(' ')} /> : null}
                {item.values.map((value, index) => value == null ? null : <circle key={rows[index].lead} cx={point(index, value).x} cy={point(index, value).y} r="3.2" />)}
              </g>
            )
          })}
          {rows.map((row, index) => <text key={row.lead} x={point(index, 0).x} y={height - 12} textAnchor="middle">+{row.lead}</text>)}
        </svg>
      ) : <div className="verification-chart-empty">该阈值下没有可计算的降水事件，FSS 保持为空，未被改写为 0。</div>}
      <div className="verification-chart-legend"><span><i className="lk" />pySTEPS-LK</span><span><i className="baseline" />{modelLabels[baseline] ?? baseline}</span></div>
    </figure>
  )
}

function EvidenceNotes({
  verificationCase,
  issueTime,
  threshold,
  windowPixels,
  truthKind,
}: {
  verificationCase: VerificationCase | null
  issueTime: string
  threshold: number
  windowPixels: number
  truthKind: string
}) {
  return (
    <aside className="verification-notes">
      <header><span>Evidence identity</span><strong>当前评分切片</strong></header>
      <dl>
        <div><dt>案例</dt><dd>{verificationCase?.case_id ?? '—'}</dd></div>
        <div><dt>起报</dt><dd>{issueTime ? formatUTC(issueTime) : '—'}</dd></div>
        <div><dt>真值</dt><dd>{truthLabels[truthKind] ?? truthKind}</dd></div>
        <div><dt>阈值</dt><dd>{threshold} mm/h</dd></div>
        <div><dt>邻域</dt><dd>{windowPixels} px</dd></div>
        <div><dt>有效域</dt><dd>truth 与三模型共同掩膜</dd></div>
      </dl>
      <p>中性质量 1.0 只用于 MRMS 有效像素；本页不能证明极坐标质控、RQI、QPE 标定或福建业务迁移效果。</p>
    </aside>
  )
}

function MetricTable({ rows, baseline, loading }: { rows: LeadRow[]; baseline: string; loading: boolean }) {
  return (
    <section className="verification-metric-table" aria-labelledby="verification-metric-title">
      <header><div><span>Common-mask metrics</span><h2 id="verification-metric-title">当前 issue 指标</h2></div><small>{loading ? '正在读取…' : `${rows.length} 个实际 10 分钟时效`}</small></header>
      <div className="verification-table-scroll">
        <table>
          <thead><tr><th>时效</th><th>LK FSS</th><th>{modelLabels[baseline] ?? baseline} FSS</th><th>Δ FSS</th><th>LK CSI</th><th>LK POD</th><th>LK FAR</th><th>LK MAE</th><th>共同覆盖率</th></tr></thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.lead}>
                <th>+{row.lead} min</th>
                <td>{formatMetric(row.lk?.fss)}</td>
                <td>{formatMetric(row.baseline?.fss)}</td>
                <td className={row.delta == null ? '' : row.delta >= 0 ? 'positive' : 'negative'}>{formatSigned(row.delta)}</td>
                <td>{formatMetric(row.lk?.csi)}</td>
                <td>{formatMetric(row.lk?.pod)}</td>
                <td>{formatMetric(row.lk?.far)}</td>
                <td>{formatRate(row.lk?.mae_mm_h)}</td>
                <td>{formatPercent(row.lk?.common_coverage)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function LedgerItem({ label, value, note, tone = 'neutral' }: { label: string; value: string; note: string; tone?: 'neutral' | 'supported' | 'caution' }) {
  return <div className={tone}><span>{label}</span><strong>{value}</strong><small>{note}</small></div>
}

function buildLeadRows(metrics: VerificationMetric[], leads: number[], baseline: string): LeadRow[] {
  return leads.map((lead) => {
    const lk = metrics.find((item) => item.model === 'lk' && item.lead_minutes === lead)
    const baselineMetric = metrics.find((item) => item.model === baseline && item.lead_minutes === lead)
    const delta = lk?.fss != null && baselineMetric?.fss != null ? lk.fss - baselineMetric.fss : null
    return { lead, lk, baseline: baselineMetric, delta }
  })
}

function pickCase(cases: VerificationCase[], current: string) {
  return cases.find((item) => item.case_id === current)
    ?? cases.find((item) => item.category === 'wet')
    ?? cases[0]
    ?? null
}

function nextIssue(item: VerificationCase | null | undefined, current: string) {
  if (!item) return ''
  return item.issue_times.includes(current) ? current : item.issue_times[0] ?? ''
}

function pickNumber(values: number[], current: number, preferred: number) {
  if (values.includes(current)) return current
  if (values.includes(preferred)) return preferred
  return values[0] ?? preferred
}

function runKey(run: RunSummary) {
  return `${run.profile_version}/${run.run_id}`
}

function formatCaseName(caseID: string) {
  return caseID
    .replace(/_20\d{6}$/, '')
    .split('_')
    .map((part) => part === 'socal' ? 'Southern California' : part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function formatUTC(value: string) {
  return `${new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'UTC', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(value))} UTC`
}

function formatIssueHour(value: string) {
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'UTC', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value))
}

function formatIssueDate(value: string) {
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'UTC', month: '2-digit', day: '2-digit' }).format(new Date(value))
}

function formatMetric(value?: number | null) {
  return value == null || !Number.isFinite(value) ? '—' : value.toFixed(3)
}

function formatSigned(value?: number | null) {
  if (value == null || !Number.isFinite(value)) return '—'
  return `${value >= 0 ? '+' : ''}${value.toFixed(4)}`
}

function formatInterval(value?: (number | null)[]) {
  if (!value || value.length !== 2 || value.some((item) => item == null)) return '95% CI —'
  return `CI ${formatSigned(value[0])}…${formatSigned(value[1])}`
}

function formatRate(value?: number | null) {
  return value == null || !Number.isFinite(value) ? '—' : `${value.toFixed(2)} mm/h`
}

function formatPercent(value?: number | null) {
  return value == null || !Number.isFinite(value) ? '—' : `${(value * 100).toFixed(1)}%`
}
