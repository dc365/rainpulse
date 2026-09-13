import { useState } from 'react'
import './qc-review.css'

type Fields = Record<string, (number | null)[]>
type Radial = { ray_index: number; azimuth_deg: number; range_m: number[]; fields: Fields; variants?: Record<string, Fields> }
type Sweep = { sweep: string; shape: number[]; observed_gates: number; label_status: string; methods: Record<string, { mask_semantics: string; marked_observed_gates: number; measurement_metrics?: Record<string, unknown>; quarantined_observed_gates?: number; quantitative_eligible_gates?: number; retained_candidate_maximum_continuous_m?: number }>; radials: Radial[]; spike_native_reference?: { status?: string; granularity?: string } }
type Case = { case_id: string; radar_id: string; scan_id?: string; input_sha256: string; partition: string; process_id: string; observation_time_utc?: string; elapsed_ms: Record<string, number>; profiles?: Record<string, { profile_version: string; pipeline_version: string; parameters_hash: string }>; sweeps: Sweep[] }
type Report = { schema_version: 'rainpulse.qc-review.v1'; operational_eligible: false; cases: Case[]; limitations: string[] }
const actions = ['保留', '降权', '拒绝', '原始缺测']
const risks = ['非径向候选', '结构候选', '疑似隔离 · 不用于 QPE', '确认径向污染']
const reasons: Record<number, string> = { 1: '原始无效', 2: '非气象联合证据', 4: '径向与极化', 8: '静态杂波与极化', 16: '弱小对象与噪声', 32: '存在天气但测量污染', 64: '天气支持保留弱候选', 128: '单一证据族候选', 256: '能力不完整', 512: '低信噪比', 1024: '高风险隔离', 2048: '过去资料辅助确认', 4096: '有界残余确认', 8192: '原始极化支持确认' }
function reasonText(value?: number | null) { return value == null ? '—' : Object.entries(reasons).filter(([bit]) => (value & Number(bit)) !== 0).map(([, text]) => text).join('；') || '无拒绝原因' }
const methods: Record<string, string> = { pyart_raw_filter: 'Py-ART 原始候选', wradlib_raw_filter: 'wradlib 原始候选', open_source_fusion: '开源分类型决策', experimental_local_rfi: '局部门段增强（实验）', legacy: '旧引擎对照', rfi_objects_v2: '径向对象引擎 v2（候选）', rfi_multivariate_v3: '联合判定与外围复核 v3（候选）' }

const v3Paths = ['搜索范围外', '仅结构候选', '核心：低相关联合证据', '核心：相位与 ZDR 联合', '过去证据辅助确认', '有界外围确认', '未决隔离', '联合天气保护', '其它质控原因拒绝']
const v3Blockers: Record<number, string> = { 1: '原始缺测', 2: '未形成结构对象', 4: '原始极化矩不足', 8: '无实测 SNR', 16: 'SNR 不可靠', 32: '相位连续邻域不足', 64: '无相位/ZDR 联合异常', 128: '联合天气保护', 256: '局部强证据不足', 512: '结构核心外', 1024: '过去有效票数不足', 2048: '未从核心到达', 4096: '隔离而非确认' }
function blockersText(value?: number | null) { return value == null ? '—' : Object.entries(v3Blockers).filter(([bit]) => (value & Number(bit)) !== 0).map(([, text]) => text).join('；') || '无阻断项' }

function checkedReport(value: unknown): Report {
  const report = value as Report
  if (!report || report.schema_version !== 'rainpulse.qc-review.v1' || report.operational_eligible !== false || !Array.isArray(report.cases) || report.cases.length > 500 || !report.cases.length || !Array.isArray(report.limitations)) throw new Error('不是受支持的 QC 对照报告')
  for (const item of report.cases) {
    if (typeof item.case_id !== 'string' || typeof item.input_sha256 !== 'string' || !Array.isArray(item.sweeps) || item.sweeps.length > 32) throw new Error('报告缺少案例身份或有效仰角')
    for (const sweep of item.sweeps) {
      if (!sweep.methods || !Array.isArray(sweep.radials) || sweep.radials.length > 12) throw new Error('径向检查数据超出范围')
      for (const ray of sweep.radials) {
        if (!Array.isArray(ray.range_m) || ray.range_m.length > 5000 || !ray.fields || !ray.range_m.every(Number.isFinite) || !Object.values(ray.fields).every(field => Array.isArray(field) && field.length === ray.range_m.length && field.every(x => x === null || (typeof x === 'number' && Number.isFinite(x))))) throw new Error('径向字段或距离坐标不一致')
        if (ray.variants && !Object.values(ray.variants).every(fields => fields && Object.values(fields).every(field => Array.isArray(field) && field.length === ray.range_m.length && field.every(x => x === null || (typeof x === 'number' && Number.isFinite(x)))))) throw new Error('候选径向字段或距离坐标不一致')
      }
    }
  }
  return report
}

function curve(ray: Radial, name: string) {
  const values = ray.fields[name] ?? []
  const maximum = ray.range_m.at(-1) || 1
  let connected = false
  return values.map((value, index) => {
    if (value == null) { connected = false; return '' }
    const prefix = connected ? 'L' : 'M'
    connected = true
    return `${prefix}${(ray.range_m[index] / maximum * 900).toFixed(2)},${(180 - (value + 32) / 112 * 180).toFixed(2)}`
  }).join(' ')
}

function numeric(value: unknown, digits = 2) { return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—' }
function percent(value: unknown) { return typeof value === 'number' ? `${(100 * value).toFixed(2)}%` : '无标签样本' }

export function QCReviewWorkspace() {
  const [report, setReport] = useState<Report | null>(null)
  const [error, setError] = useState('')
  const [caseIndex, setCaseIndex] = useState(0)
  const [sweepIndex, setSweepIndex] = useState(0)
  const [rayIndex, setRayIndex] = useState(0)
  const [page, setPage] = useState(0)
  const [method, setMethod] = useState('rfi_multivariate_v3')
  const item = report?.cases[caseIndex]
  const sweep = item?.sweeps[sweepIndex]
  const baseRay = sweep?.radials[rayIndex]
  const selectedMethod = baseRay?.variants?.[method] ? method : baseRay?.variants?.rfi_objects_v2 ? 'rfi_objects_v2' : 'open_source_fusion'
  const ray = baseRay ? { ...baseRay, fields: baseRay.variants?.[selectedMethod] ?? baseRay.fields } : undefined
  const identity = item?.profiles?.[selectedMethod]
  const loadFile = async (file?: File) => {
    if (!file) return
    try {
      if (file.size > 50 * 1024 * 1024) throw new Error('报告超过 50 MiB，请减少案例或导出的径向数')
      const next = checkedReport(JSON.parse(await file.text()))
      setReport(next); setCaseIndex(0); setSweepIndex(0); setRayIndex(0); setPage(0); setError('')
    } catch (cause) { setError(cause instanceof Error ? cause.message : '报告读取失败') }
  }
  return <main className="qc-review">
    <header><a href="/">← 工作台</a><h1>雷达质控对照与径向诊断</h1><label>打开本地报告 <input type="file" accept=".json,application/json" aria-label="打开 QC 对照报告" onChange={event => void loadFile(event.target.files?.[0])} /></label></header>
    <p className="qc-review-notice">这里读取冻结输入生成的本地报告，不修改线上配置或产品。原始候选与最终拒绝分开显示；没有标签不计算精确率或召回率。</p>
    {error ? <p role="alert">{error}</p> : null}
    {!report ? <section><h2>先生成同输入对照报告</h2><pre>python -m rainpulse_algo.radar.qc_engine.review --manifest cases.json --output qc-review.json --inspect-ray 120</pre><p>报告包含输入摘要、库版本、分项证据和选定径向的真实数值。缺测保持为空，不通过连线或插值补齐。</p></section> : null}
    {report && item ? <>
      <section className="qc-review-selectors">
        <label>案例 <select value={caseIndex} onChange={e => { setCaseIndex(Number(e.target.value)); setSweepIndex(0); setRayIndex(0); setPage(0) }}>{report.cases.map((entry, i) => <option key={entry.case_id} value={i}>{entry.case_id} · {entry.radar_id}</option>)}</select></label>
        <label>原始仰角组 <select value={sweepIndex} onChange={e => { setSweepIndex(Number(e.target.value)); setRayIndex(0); setPage(0) }}>{item.sweeps.map((entry, i) => <option key={entry.sweep} value={i}>{entry.sweep}</option>)}</select></label>
        <p>源体扫 {item.scan_id || '未提供'} · {item.observation_time_utc || '时间未提供'} · {item.partition} · {item.process_id}</p><code title={item.input_sha256}>输入 SHA-256 {item.input_sha256}</code>
      </section>
      {sweep ? <section><h2>同一输入的候选与决策</h2><div className="qc-review-scroll"><table><thead><tr><th>方法</th><th>语义</th><th>命中有效门</th><th>未决隔离门</th><th>定量可用门</th><th>干扰召回</th><th>可信降水误拒</th><th>≥35 dBZ 保留</th></tr></thead><tbody>{Object.entries(sweep.methods).map(([method, value]) => <tr key={method}><th>{methods[method] || method}</th><td>{value.mask_semantics === 'final_reject' ? '最终拒绝' : '原始候选，非最终拒绝'}</td><td>{value.marked_observed_gates} / {sweep.observed_gates}</td><td>{value.quarantined_observed_gates ?? '—'}</td><td>{value.quantitative_eligible_gates ?? '—'}</td><td>{percent(value.measurement_metrics?.interference_recall)}</td><td>{percent(value.measurement_metrics?.trusted_weather_false_reject_rate)}</td><td>{percent(value.measurement_metrics?.strong_weather_retention)}</td></tr>)}</tbody></table></div><p>SPIKE：{sweep.spike_native_reference?.granularity ? `${sweep.spike_native_reference.granularity} 级原生参考` : '没有原生执行参考，不视为已运行'}。门级评分不使用整射线 QI 充当标签。</p></section> : null}
      {ray && sweep ? <section><h2>原始径向与候选结果</h2><label>检查算法 <select aria-label="检查算法" value={selectedMethod} onChange={e => { setMethod(e.target.value); setPage(0) }}>{Object.keys(baseRay?.variants ?? { open_source_fusion: {} }).map(key => <option key={key} value={key}>{methods[key] || key}</option>)}</select></label>{identity ? <p>当前检查：{identity.profile_version} · {identity.pipeline_version} <code title={identity.parameters_hash}>参数摘要 {identity.parameters_hash}</code></p> : null}<p>隔离表示测量不确定且不能用于定量降水，不计作确认污染；保留原始值供复核。V3 搜索范围不是删除范围；相位异常占比不是干扰概率，未确认原因可能重叠。</p><label>径向 <select value={rayIndex} onChange={e => { setRayIndex(Number(e.target.value)); setPage(0) }}>{sweep.radials.map((entry, i) => <option key={entry.ray_index} value={i}>#{entry.ray_index} · {numeric(entry.azimuth_deg)}°</option>)}</select></label><p>灰线：原始反射率；绿线：定量可用值。纵轴 −32～80 dBZ；横轴 0～{numeric(ray.range_m.at(-1)! / 1000, 1)} km。空段不插值。</p><svg className="qc-review-curve" role="img" aria-label="原始与可用反射率径向剖面" viewBox="0 0 900 180"><path className="raw" d={curve(ray, 'DBZH_RAW')} /><path className="usable" d={curve(ray, 'DBZH_USABLE')} /></svg>
        <div className="qc-review-pages"><button disabled={page === 0} onClick={() => setPage(page - 1)}>上一页</button><span>门 {page * 80 + 1}～{Math.min((page + 1) * 80, ray.range_m.length)}</span><button disabled={(page + 1) * 80 >= ray.range_m.length} onClick={() => setPage(page + 1)}>下一页</button></div>
        <div className="qc-review-scroll"><table><thead><tr><th>距离 km</th><th>原始 dBZ</th><th>可用 dBZ</th><th>RHOHV</th><th>ZDR dB</th><th>PHIDP °</th><th>气象隶属分</th><th>动作</th><th>径向状态</th><th>对象</th><th>极化矩 原始/沿线/二维</th><th>原因</th><th>V3 核心/外围/搜索</th><th>相位异常占比</th><th>V3 判定路径</th><th>V3 未确认原因（可重叠）</th></tr></thead><tbody>{ray.range_m.slice(page * 80, (page + 1) * 80).map((range, i) => { const j = page * 80 + i; return <tr key={j}><td>{numeric(range / 1000)}</td>{['DBZH_RAW', 'DBZH_USABLE', 'RHOHV_RAW', 'ZDR_RAW', 'PHIDP_RAW', 'METEO_SCORE'].map(field => <td key={field}>{numeric(ray.fields[field]?.[j])}</td>)}<td>{actions[ray.fields.QC_ACTION?.[j] ?? 3] || '未知'}</td><td>{risks[ray.fields.RFI_RISK_STATE?.[j] ?? -1] ?? '—'}</td><td>{ray.fields.RFI_OBJECT_ID?.[j] || '—'}</td><td>{['OS_POL_RAW_MOMENT_COUNT', 'OS_POL_AXIAL_MOMENT_COUNT', 'OS_POL_TEXTURE_MOMENT_COUNT'].map(key => ray.fields[key]?.[j] ?? '—').join(' / ')}</td><td title={String(ray.fields.QC_DECISION_REASON?.[j] ?? '')}>{reasonText(ray.fields.QC_DECISION_REASON?.[j])}</td><td>{['RFI_CORE_SEED_MASK', 'RFI_PERIPHERY_MASK', 'RFI_SEARCH_MASK'].map(key => ray.fields[key]?.[j] ?? '—').join(' / ')}</td><td>{numeric(ray.fields.RFI_PHASE_NOISE_FRACTION?.[j])}</td><td>{v3Paths[ray.fields.RFI_V3_DECISION_PATH?.[j] ?? -1] ?? '—'}</td><td>{blockersText(ray.fields.RFI_V3_BLOCKER_BITS?.[j])}</td></tr> })}</tbody></table></div>
      </section> : null}
      <details><summary>证据边界与限制</summary>{report.limitations.map(text => <p key={text}>{text}</p>)}<p>原因位码按代码中的 DecisionReason 解码；气象隶属分不是校准后的降水概率。</p></details>
    </> : null}
  </main>
}
