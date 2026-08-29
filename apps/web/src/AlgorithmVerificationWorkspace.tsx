import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import type { components } from './api/generated/schema'
import { VerificationMapMatrix } from './VerificationMapMatrix'

type RunSummary = components['schemas']['AlgorithmVerificationRunSummary']
type RunDetail = components['schemas']['AlgorithmVerificationRunDetail']
type VerificationCase = components['schemas']['AlgorithmVerificationCase']
type VerificationMetric = components['schemas']['AlgorithmVerificationMetric']
type SkillComparison = components['schemas']['AlgorithmVerificationSkillComparison']
type VerificationMapFrame = components['schemas']['AlgorithmVerificationMapFrame']
type FSSScale = components['schemas']['AlgorithmVerificationFSSScale']

const modelLabels: Record<string, string> = {
  lk: 'pySTEPS-LK',
  persistence: '持续性',
  translation: '基于 LK 的整场平移',
  phase_correlation: '独立相位相关平移',
}

const statusLabels: Record<string, string> = {
  lk_supported: '通过本轮工程门槛',
  translation_baseline_retained: '尚未稳定超过平移基线',
  skill_not_demonstrated: '尚未证明稳定增益',
  insufficient_evidence: '证据不足',
}

const truthLabels: Record<string, string> = {
  observed_mrms_10min: 'MRMS 10 分钟实况',
}

interface AlgorithmVerificationWorkspaceProps {
  refreshToken: number
}

interface LeadRow {
  lead: number
  lk?: VerificationMetric
  baseline?: VerificationMetric
  delta: number | null
}

export function AlgorithmVerificationWorkspace({ refreshToken }: AlgorithmVerificationWorkspaceProps) {
  const initialQuery = useMemo(() => readVerificationQuery(), [])
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [selectedRunKey, setSelectedRunKey] = useState(initialQuery.run)
  const [detail, setDetail] = useState<RunDetail | null>(null)
  const [selectedCaseID, setSelectedCaseID] = useState(initialQuery.caseID)
  const [selectedIssueTime, setSelectedIssueTime] = useState(initialQuery.issueTime)
  const [threshold, setThreshold] = useState(initialQuery.threshold ?? 5)
  const [windowPixels, setWindowPixels] = useState(initialQuery.windowPixels ?? 11)
  const [baseline, setBaseline] = useState(initialQuery.baseline || 'translation')
  const [selectedLeadMinutes, setSelectedLeadMinutes] = useState(initialQuery.leadMinutes ?? 60)
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
        if (!isAbortError(requestError)) {
          setError(requestError instanceof Error ? requestError.message : '验证目录读取失败')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingRuns(false)
      })
    return () => controller.abort()
  }, [refreshToken])

  const selectedRun = useMemo(
    () => runs.find((run) => runKey(run) === selectedRunKey) ?? null,
    [runs, selectedRunKey],
  )
  const activeDetail = detail && selectedRun && runKey(detail.run) === selectedRunKey
    ? detail
    : null

  useEffect(() => {
    if (!selectedRun) {
      setDetail(null)
      return
    }
    const controller = new AbortController()
    setLoadingDetail(true)
    setDetail(null)
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
        if (!isAbortError(requestError)) {
          setError(requestError instanceof Error ? requestError.message : '验证运行读取失败')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingDetail(false)
      })
    return () => controller.abort()
    // The selected case/issue is intentionally reused only when the next run contains it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedRun])

  useEffect(() => {
    if (!selectedRun || !activeDetail || !selectedCaseID || !selectedIssueTime) {
      setMetrics([])
      return
    }
    const controller = new AbortController()
    const query = new URLSearchParams({
      case_id: selectedCaseID,
      issue_time: selectedIssueTime,
      threshold_mm_h: String(threshold),
      window_pixels: String(windowPixels),
    })
    setLoadingMetrics(true)
    setMetrics([])
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
        if (!isAbortError(requestError)) {
          setError(requestError instanceof Error ? requestError.message : '验证指标读取失败')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingMetrics(false)
      })
    return () => controller.abort()
  }, [activeDetail, selectedCaseID, selectedIssueTime, selectedRun, threshold, windowPixels])

  useEffect(() => {
    if (!selectedRun || !activeDetail || !selectedRun.maps_available || !selectedCaseID || !selectedIssueTime) {
      setMapFrame(null)
      return
    }
    const controller = new AbortController()
    const query = new URLSearchParams({
      case_id: selectedCaseID,
      issue_time: selectedIssueTime,
      lead_minutes: String(selectedLeadMinutes),
    })
    setLoadingMap(true)
    setMapFrame(null)
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
        if (!isAbortError(requestError)) {
          setMapError(requestError instanceof Error ? requestError.message : '验证地图读取失败')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingMap(false)
      })
    return () => controller.abort()
  }, [activeDetail, selectedCaseID, selectedIssueTime, selectedLeadMinutes, selectedRun])

  const leadMinutes = useMemo(() => activeDetail?.filters.lead_minutes ?? [], [activeDetail])
  useEffect(() => {
    if (!playing || leadMinutes.length < 2) return
    const timer = window.setInterval(() => {
      setSelectedLeadMinutes((current) => {
        const index = leadMinutes.indexOf(current)
        return leadMinutes[(index + 1 + leadMinutes.length) % leadMinutes.length]
      })
    }, 1800)
    return () => window.clearInterval(timer)
  }, [leadMinutes, playing])

  useEffect(() => {
    if (!activeDetail || !selectedRunKey || !selectedCaseID || !selectedIssueTime) return
    const query = new URLSearchParams(window.location.search)
    query.set('view', 'verification')
    query.set('run', selectedRunKey)
    query.set('case', selectedCaseID)
    query.set('issue', selectedIssueTime)
    query.set('lead', String(selectedLeadMinutes))
    query.set('baseline', baseline)
    query.set('threshold', String(threshold))
    query.set('window', String(windowPixels))
    window.history.replaceState(null, '', `${window.location.pathname}?${query}${window.location.hash}`)
  }, [activeDetail, baseline, selectedCaseID, selectedIssueTime, selectedLeadMinutes, selectedRunKey, threshold, windowPixels])

  const activeCase = activeDetail?.cases.find((item) => item.case_id === selectedCaseID) ?? null
  const selectedFSSScale = activeDetail?.filters.fss_scales.find((item) => item.window_pixels === windowPixels)
  const selectedRows = useMemo(
    () => buildLeadRows(metrics, activeDetail?.filters.lead_minutes ?? [], baseline),
    [activeDetail?.filters.lead_minutes, baseline, metrics],
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
    <section className="verification-page verification-page-rp017" aria-labelledby="verification-title">
      <header className="page-heading verification-heading verification-heading-rp017">
        <div>
          <p className="section-kicker">算法验证 · RP-017</p>
          <h1 id="verification-title">算法离线验证</h1>
          <p>先看结论，再看空间差异，最后展开评分明细。当前数据为美国 MRMS，仅用于工程验证。</p>
        </div>
        <span className="verification-boundary">工程证据 · 非福建业务验收</span>
      </header>

      {error ? <div className="error-banner" role="alert"><strong>验证数据读取失败</strong><span>{error}</span></div> : null}
      {loadingRuns ? <p className="verification-empty">正在读取离线验证目录…</p> : null}
      {!loadingRuns && runs.length === 0 ? (
        <p className="verification-empty">尚未挂载算法验证报告。运行产物保持在服务器，只通过控制面读取。</p>
      ) : null}

      {selectedRun ? (
        <>
          <VerificationConclusion run={selectedRun} detail={activeDetail} />

          {loadingDetail || !activeDetail ? (
            <p className="verification-empty">正在建立验证索引…</p>
          ) : (
            <section className="verification-workbench" aria-live="polite">
              <VerificationSelectionBar
                runs={runs}
                selectedRunKey={selectedRunKey}
                onRunChange={(value) => {
                  setPlaying(false)
                  setSelectedRunKey(value)
                }}
                cases={activeDetail.cases}
                selectedCaseID={selectedCaseID}
                onCaseChange={(caseID) => {
                  const nextCase = activeDetail.cases.find((item) => item.case_id === caseID) ?? null
                  setPlaying(false)
                  setSelectedCaseID(caseID)
                  setSelectedIssueTime(nextIssue(nextCase, ''))
                }}
                activeCase={activeCase}
                selectedIssueTime={selectedIssueTime}
                onIssueChange={(value) => {
                  setPlaying(false)
                  setSelectedIssueTime(value)
                }}
                models={activeDetail.filters.models}
                baseline={baseline}
                onBaselineChange={setBaseline}
                thresholds={activeDetail.filters.thresholds_mm_h}
                threshold={threshold}
                onThresholdChange={setThreshold}
                fssScales={activeDetail.filters.fss_scales}
                windowPixels={windowPixels}
                onWindowChange={setWindowPixels}
              />

              <VerificationMapMatrix
                frame={activeMapFrame}
                baseline={baseline}
                lkMetric={selectedLeadRow?.lk}
                baselineMetric={selectedLeadRow?.baseline}
                loading={loadingMap}
                error={mapError}
                mapsAvailable={activeDetail.run.maps_available}
              />

              <LeadTimeline
                rows={selectedRows}
                loading={loadingMetrics}
                selectedLead={selectedLeadMinutes}
                playing={playing}
                onTogglePlaying={() => setPlaying((value) => !value)}
                onSelect={(lead) => {
                  setPlaying(false)
                  setSelectedLeadMinutes(lead)
                }}
              />

              <CurrentMetricStrip row={selectedLeadRow} baseline={baseline} />

              <div className="verification-evidence-grid verification-evidence-grid-rp017">
                <FSSChart rows={selectedRows} baseline={baseline} selectedLead={selectedLeadMinutes} />
                <CurrentSliceCard
                  verificationCase={activeCase}
                  issueTime={selectedIssueTime}
                  threshold={threshold}
                  fssScale={selectedFSSScale}
                  truthKind={activeDetail.run.primary_truth_kind}
                  fixedTruthDomain={usesFixedTruthDomain(activeDetail.run.schema_version)}
                  row={selectedLeadRow}
                />
              </div>

              <details className="verification-details-panel">
                <summary><span>展开通过门槛</span><small>案例级 FSS 增益与 95% 区间</small></summary>
                <SkillGateMatrix comparisons={activeDetail.skill_summary.comparisons} />
              </details>

              <details className="verification-details-panel">
                <summary><span>展开完整指标</span><small>当前案例、起报、阈值和邻域</small></summary>
                <MetricTable rows={selectedRows} baseline={baseline} loading={loadingMetrics} />
              </details>

              <details className="verification-details-panel">
                <summary><span>展开运行与数据溯源</span><small>版本、运行 ID 与证据边界</small></summary>
                <VerificationProvenance run={activeDetail.run} activeCase={activeCase} />
              </details>
            </section>
          )}
        </>
      ) : null}
    </section>
  )
}

function VerificationConclusion({ run, detail }: { run: RunSummary; detail: RunDetail | null }) {
  const preferred = detail?.skill_summary.comparisons.find(
    (item) => item.baseline === 'translation' && item.threshold_mm_h === 5,
  ) ?? detail?.skill_summary.comparisons.find((item) => item.baseline === 'translation')
  return (
    <section className={`verification-conclusion ${run.skill_status === 'lk_supported' ? 'supported' : 'caution'}`} aria-label="验证结论">
      <div className="verification-conclusion-copy">
        <span>当前结论</span>
        <strong>{statusLabels[run.skill_status] ?? run.skill_status}</strong>
        <p>{conclusionSentence(run.skill_status)}</p>
      </div>
      <div className="verification-conclusion-metrics">
        <ConclusionMetric label="完成起报" value={`${run.completed_issue_count}/${run.completed_issue_count + run.failed_issue_count}`} note={`${run.failed_issue_count} 个失败`} />
        <ConclusionMetric label="对平移基线" value={formatSigned(preferred?.mean_fss_difference)} note="平均 FSS 差值" />
        <ConclusionMetric label="运动回退" value={`${run.motion_fallback_issue_count}`} note="全部显式记录" />
        <ConclusionMetric label="空间证据" value={`${run.map_bundle_count}`} note={`${run.map_layer_count.toLocaleString('zh-CN')} 个图层`} />
      </div>
      <p className="verification-conclusion-boundary">MRMS 结果不能替代福建极坐标质控、QI 拼图、QPE 标定和本地业务检验。</p>
    </section>
  )
}

function ConclusionMetric({ label, value, note }: { label: string; value: string; note: string }) {
  return <div><span>{label}</span><strong>{value}</strong><small>{note}</small></div>
}

function VerificationSelectionBar({
  runs,
  selectedRunKey,
  onRunChange,
  cases,
  selectedCaseID,
  onCaseChange,
  activeCase,
  selectedIssueTime,
  onIssueChange,
  models,
  baseline,
  onBaselineChange,
  thresholds,
  threshold,
  onThresholdChange,
  fssScales,
  windowPixels,
  onWindowChange,
}: {
  runs: RunSummary[]
  selectedRunKey: string
  onRunChange: (value: string) => void
  cases: VerificationCase[]
  selectedCaseID: string
  onCaseChange: (value: string) => void
  activeCase: VerificationCase | null
  selectedIssueTime: string
  onIssueChange: (value: string) => void
  models: string[]
  baseline: string
  onBaselineChange: (value: string) => void
  thresholds: number[]
  threshold: number
  onThresholdChange: (value: number) => void
  fssScales: FSSScale[]
  windowPixels: number
  onWindowChange: (value: number) => void
}) {
  return (
    <section className="verification-filterbar" aria-label="验证筛选">
      <label>
        <span>验证运行</span>
        <select aria-label="验证运行" value={selectedRunKey} onChange={(event) => onRunChange(event.target.value)}>
          {runs.map((run) => <option key={runKey(run)} value={runKey(run)}>{run.run_id} · {run.profile_version}</option>)}
        </select>
      </label>
      <label>
        <span>典型案例</span>
        <select aria-label="典型案例" value={selectedCaseID} onChange={(event) => onCaseChange(event.target.value)}>
          {cases.map((item) => <option key={item.case_id} value={item.case_id}>{formatCaseName(item.case_id)} · {item.issue_times.length} 起报</option>)}
        </select>
      </label>
      <label>
        <span>起报时间</span>
        <select aria-label="起报时间" value={selectedIssueTime} onChange={(event) => onIssueChange(event.target.value)}>
          {activeCase?.issue_times.map((value) => <option key={value} value={value}>{formatUTC(value)}</option>)}
        </select>
      </label>
      <ControlGroup label="比较基线">
        {models.filter((model) => model !== 'lk').map((model) => (
          <ChoiceButton key={model} active={baseline === model} onClick={() => onBaselineChange(model)}>
            {modelLabels[model] ?? model}
          </ChoiceButton>
        ))}
      </ControlGroup>
      <ControlGroup label="降水阈值">
        {thresholds.map((value) => (
          <ChoiceButton key={value} active={threshold === value} onClick={() => onThresholdChange(value)}>
            {value} mm/h
          </ChoiceButton>
        ))}
      </ControlGroup>
      <details className="verification-advanced-filter">
        <summary>高级设置</summary>
        <ControlGroup label="FSS 邻域尺度">
          {fssScales.map((scale) => (
            <ChoiceButton
              key={scale.window_pixels}
              active={windowPixels === scale.window_pixels}
              onClick={() => onWindowChange(scale.window_pixels)}
              title={`${formatFSSScale(scale.target_km)}，内部使用 ${scale.window_pixels}×${scale.window_pixels} 网格窗口；当前报告实际覆盖 ${formatFSSRange(scale)}`}
            >
              {formatFSSScale(scale.target_km)}
            </ChoiceButton>
          ))}
        </ControlGroup>
        <p className="verification-fss-scale-note">
          目标物理尺度，内部按 {windowPixels}×{windowPixels} 网格窗口计算
        </p>
      </details>
    </section>
  )
}

function ControlGroup({ label, children }: { label: string; children: ReactNode }) {
  return <div className="verification-control-group verification-control-group-rp017"><span>{label}</span><div>{children}</div></div>
}

function ChoiceButton({ active, onClick, children, title }: { active: boolean; onClick: () => void; children: ReactNode; title?: string }) {
  return <button type="button" className={active ? 'active' : ''} aria-pressed={active} onClick={onClick} title={title}>{children}</button>
}

function LeadTimeline({
  rows,
  loading,
  selectedLead,
  playing,
  onTogglePlaying,
  onSelect,
}: {
  rows: LeadRow[]
  loading: boolean
  selectedLead: number
  playing: boolean
  onTogglePlaying: () => void
  onSelect: (lead: number) => void
}) {
  return (
    <section className="verification-timeline" aria-label="预报时效">
      <header>
        <div><span>预报时效</span><strong>{loading ? '正在读取评分…' : '选择地图与评分时效'}</strong></div>
        <button type="button" className={playing ? 'active' : ''} aria-pressed={playing} onClick={onTogglePlaying}>{playing ? '暂停播放' : '播放时效'}</button>
      </header>
      <div>
        {rows.map((row) => (
          <button
            type="button"
            className={`${row.delta == null ? 'unknown' : row.delta >= 0 ? 'positive' : 'negative'} ${selectedLead === row.lead ? 'active' : ''}`}
            aria-pressed={selectedLead === row.lead}
            onClick={() => onSelect(row.lead)}
            key={row.lead}
          >
            <strong>+{row.lead}</strong>
            <small>{formatSigned(row.delta)}</small>
          </button>
        ))}
      </div>
    </section>
  )
}

function CurrentMetricStrip({ row, baseline }: { row?: LeadRow; baseline: string }) {
  return (
    <section className="verification-current-metrics" aria-label="当前时效核心指标">
      <CurrentMetric label="LK FSS" value={formatMetric(row?.lk?.fss)} note="越高越好" />
      <CurrentMetric label={`${modelLabels[baseline] ?? baseline} FSS`} value={formatMetric(row?.baseline?.fss)} note="同一评分域" />
      <CurrentMetric label="FSS 增益" value={formatSigned(row?.delta)} note="LK − 基线" tone={row?.delta == null ? 'neutral' : row.delta >= 0 ? 'positive' : 'negative'} />
      <CurrentMetric label="CSI / POD / FAR" value={`${formatMetric(row?.lk?.csi)} / ${formatMetric(row?.lk?.pod)} / ${formatMetric(row?.lk?.far)}`} note="当前阈值" />
      <CurrentMetric label="共同覆盖" value={formatPercent(row?.lk?.common_coverage)} note={`模型覆盖 ${formatPercent(row?.lk?.forecast_coverage)}`} />
    </section>
  )
}

function CurrentMetric({ label, value, note, tone = 'neutral' }: { label: string; value: string; note: string; tone?: 'neutral' | 'positive' | 'negative' }) {
  return <div className={tone}><span>{label}</span><strong>{value}</strong><small>{note}</small></div>
}

function FSSChart({ rows, baseline, selectedLead }: { rows: LeadRow[]; baseline: string; selectedLead: number }) {
  const width = 720
  const height = 250
  const left = 48
  const right = 18
  const top = 20
  const bottom = 40
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
    <figure className="verification-chart verification-chart-rp017">
      <figcaption><div><span>逐时效</span><strong>FSS 对比曲线</strong></div><small>0–1，越高越好</small></figcaption>
      {hasValues ? (
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="LK 与基线逐时效 FSS 折线图">
          {[0, .25, .5, .75, 1].map((tick) => {
            const y = top + (1 - tick) * plotHeight
            return <g key={tick}><line x1={left} x2={width - right} y1={y} y2={y} /><text x={left - 8} y={y + 4} textAnchor="end">{tick.toFixed(2)}</text></g>
          })}
          {rows.map((row, index) => row.lead === selectedLead ? <line className="selected-lead-line" key={`selected-${row.lead}`} x1={point(index, 0).x} x2={point(index, 0).x} y1={top} y2={height - bottom} /> : null)}
          {series.map((item) => {
            const points = item.values.flatMap((value, index) => value == null ? [] : [point(index, value)])
            return (
              <g className={item.className} key={item.key}>
                {points.length > 1 ? <polyline points={points.map(({ x, y }) => `${x},${y}`).join(' ')} /> : null}
                {item.values.map((value, index) => value == null ? null : <circle key={rows[index].lead} cx={point(index, value).x} cy={point(index, value).y} r={rows[index].lead === selectedLead ? '4.6' : '3.2'} />)}
              </g>
            )
          })}
          {rows.map((row, index) => <text key={row.lead} x={point(index, 0).x} y={height - 13} textAnchor="middle">+{row.lead}</text>)}
        </svg>
      ) : <div className="verification-chart-empty">该阈值下没有可计算的降水事件，FSS 保持为空，未被改写为 0。</div>}
      <div className="verification-chart-legend"><span><i className="lk" />pySTEPS-LK</span><span><i className="baseline" />{modelLabels[baseline] ?? baseline}</span></div>
    </figure>
  )
}

function CurrentSliceCard({
  verificationCase,
  issueTime,
  threshold,
  fssScale,
  truthKind,
  fixedTruthDomain,
  row,
}: {
  verificationCase: VerificationCase | null
  issueTime: string
  threshold: number
  fssScale?: FSSScale
  truthKind: string
  fixedTruthDomain: boolean
  row?: LeadRow
}) {
  return (
    <aside className="verification-slice-card">
      <header><span>当前评分切片</span><strong>{verificationCase ? formatCaseName(verificationCase.case_id) : '—'}</strong></header>
      <dl>
        <div><dt>起报</dt><dd>{issueTime ? formatUTC(issueTime) : '—'}</dd></div>
        <div><dt>预报时效</dt><dd>{row ? `+${row.lead} 分钟` : '—'}</dd></div>
        <div><dt>真值</dt><dd>{truthLabels[truthKind] ?? truthKind}</dd></div>
        <div><dt>阈值</dt><dd>{threshold} mm/h</dd></div>
        <div>
          <dt>FSS 邻域尺度</dt>
          <dd>{fssScale ? `${formatFSSScale(fssScale.target_km)} · 实际 ${formatFSSActualKM(row, fssScale)} · ${fssScale.window_pixels}×${fssScale.window_pixels} 网格` : '—'}</dd>
        </div>
        <div><dt>有效域</dt><dd>{fixedTruthDomain ? '实况固定有效域（模型缺测按无预报）' : '实况与全部模型共同有效域'}</dd></div>
      </dl>
      <p>当前结论只验证算法适配和确定性技巧，不验证福建极坐标质控、RQI、QPE 标定或生产就绪。</p>
    </aside>
  )
}

function SkillGateMatrix({ comparisons }: { comparisons: SkillComparison[] }) {
  const baselines = Array.from(new Set(comparisons.map((item) => item.baseline)))
  const thresholds = Array.from(new Set(comparisons.map((item) => item.threshold_mm_h))).sort((a, b) => a - b)
  return (
    <section className="skill-gate-panel skill-gate-panel-rp017" aria-labelledby="skill-gate-title">
      <header>
        <div><span>冻结门槛</span><h2 id="skill-gate-title">LK 相对基线的 FSS 技巧</h2></div>
        <small>湿案例 · +10～+60 分钟 · 案例级配对比较</small>
      </header>
      <div className="skill-gate-table-wrap">
        <table>
          <thead><tr><th>比较基线</th>{thresholds.map((value) => <th key={value}>{value} mm/h</th>)}</tr></thead>
          <tbody>
            {baselines.map((candidate) => (
              <tr key={candidate}>
                <th>{modelLabels[candidate] ?? candidate}</th>
                {thresholds.map((value) => {
                  const item = comparisons.find((comparison) => comparison.baseline === candidate && comparison.threshold_mm_h === value)
                  return <td className={item?.passes_case_gate ? 'passed' : 'failed'} key={value}><strong>{formatSigned(item?.mean_fss_difference)}</strong><small>{item ? `${item.positive_case_count}/${item.total_wet_case_count} 案例` : '无数据'}</small><span>{formatInterval(item?.mean_difference_95pct_interval)}</span></td>
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function MetricTable({ rows, baseline, loading }: { rows: LeadRow[]; baseline: string; loading: boolean }) {
  return (
    <section className="verification-metric-table verification-metric-table-rp017" aria-labelledby="verification-metric-title">
      <header><div><span>完整评分</span><h2 id="verification-metric-title">当前起报指标</h2></div><small>{loading ? '正在读取…' : `${rows.length} 个实际 10 分钟时效`}</small></header>
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

function VerificationProvenance({ run, activeCase }: { run: RunSummary; activeCase: VerificationCase | null }) {
  return (
    <section className="verification-provenance-rp017">
      <dl>
        <div><dt>验证配置</dt><dd>{run.profile_version}</dd></div>
        <div><dt>运行 ID</dt><dd>{run.run_id}</dd></div>
        <div><dt>真值</dt><dd>{truthLabels[run.primary_truth_kind] ?? run.primary_truth_kind}</dd></div>
        <div><dt>当前案例</dt><dd>{activeCase?.case_id ?? '—'}</dd></div>
        <div><dt>地图渲染器</dt><dd>{run.map_renderer_version ?? '无地图产物'}</dd></div>
        <div><dt>更新时间</dt><dd>{formatUTC(run.modified_at)}</dd></div>
      </dl>
      <p>验证运行不可直接转为业务产品；福建落地仍需经过原始雷达质控、Hybrid Scan、QI 拼图、QPE 和本地实况评分。</p>
    </section>
  )
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

function usesFixedTruthDomain(schemaVersion: string) {
  const [major, minor] = schemaVersion.split('.', 2).map(Number)
  return Number.isInteger(major) && Number.isInteger(minor)
    && (major > 1 || (major === 1 && minor >= 2))
}

function readVerificationQuery() {
  const query = new URLSearchParams(window.location.search)
  const parseNumber = (name: string) => {
    const value = Number(query.get(name))
    return Number.isFinite(value) && value > 0 ? value : null
  }
  return {
    run: query.get('run') ?? '',
    caseID: query.get('case') ?? '',
    issueTime: query.get('issue') ?? '',
    leadMinutes: parseNumber('lead'),
    threshold: parseNumber('threshold'),
    windowPixels: parseNumber('window'),
    baseline: query.get('baseline') ?? '',
  }
}

function isAbortError(value: unknown) {
  return value instanceof DOMException && value.name === 'AbortError'
}

function conclusionSentence(status: string) {
  if (status === 'lk_supported') return '在当前冻结 MRMS 案例、阈值和近时效门槛下，pySTEPS-LK 获得正向工程证据。'
  if (status === 'translation_baseline_retained') return 'pySTEPS-LK 已超过持续性，但尚未稳定超过整场平移基线。'
  if (status === 'skill_not_demonstrated') return '当前案例尚不足以证明 pySTEPS-LK 相对强基线存在稳定增益。'
  return '当前可评价案例不足，暂不形成算法技巧结论。'
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

function formatFSSScale(value: number) {
  return `${new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 }).format(value)} km`
}

function formatFSSRange(scale: FSSScale) {
  const minimum = formatFSSScale(scale.actual_km_min)
  const maximum = formatFSSScale(scale.actual_km_max)
  return minimum === maximum ? minimum : `${minimum}–${maximum}`
}

function formatFSSActualKM(row: LeadRow | undefined, scale: FSSScale) {
  const actual = row?.lk?.window_km ?? row?.baseline?.window_km
  return actual == null ? formatFSSRange(scale) : formatFSSScale(actual)
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
