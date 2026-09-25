import { useEffect, useState } from 'react'
import { asset, failure } from './api'
import { Notice } from './components'

type Layer = { object_path: string; title: string; field?: string; sweep_number?: number }
type ComparisonSweep = { sweep_number: number; sequence?: number; raw: string; qc: string; flags: string; elevation_deg: number }
type RegionalProduct = { product_id: string; band: string; label: string; status: string; object_path: string | null; valid_echo_cells?: number; valid_cells?: number; source_count?: number; reason?: string }
export function Preview(props: { token: string; taskID: string }) {
  return <TaskPreview key={props.taskID} {...props} />
}
function TaskPreview({ token, taskID }: { token: string; taskID: string }) {
  const [layers, setLayers] = useState<Layer[]>([])
  const [key, setKey] = useState('')
  const [picture, setPicture] = useState<{key: string; url: string} | null>(null)
  const [comparison, setComparison] = useState<ComparisonSweep[]>([])
  const [regionalProducts, setRegionalProducts] = useState<RegionalProduct[]>([])
  const [regionalURLs, setRegionalURLs] = useState<Record<string, string>>({})
  const [selectedSweep, setSelectedSweep] = useState<number | null>(null)
  const [compareURLs, setCompareURLs] = useState<{raw: string; qc: string} | null>(null)
  const [showFlags, setShowFlags] = useState(false)
  const [problem, setProblem] = useState<{key: string; message: string} | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    void asset(token, taskID, 'manifest.json', controller.signal).then(b => b.text()).then(text => {
      if (controller.signal.aborted) return
      const value = JSON.parse(text) as {layers?: Layer[]; comparison?: {sweeps?: ComparisonSweep[]; products?: RegionalProduct[]; display_note?: string}; flag_legend?: {label: string; color: string}[]}
      if (!Array.isArray(value.layers) || value.layers.some(l => typeof l.object_path !== 'string' || typeof l.title !== 'string')) {
        throw new Error('图件清单缺少有效图层')
      }
      setLayers(value.layers)
      setKey(value.layers[0]?.object_path ?? '')
      const pairs = value.comparison?.sweeps ?? []
      setComparison(pairs)
      setSelectedSweep(pairs[0]?.sweep_number ?? null)
      setRegionalProducts(value.comparison?.products ?? [])
    }).catch(e => { if (!controller.signal.aborted) setProblem({key:'manifest', message:failure(e)}) })
    return () => controller.abort()
  }, [token, taskID])
  useEffect(() => {
    const controller = new AbortController()
    const urls: Record<string, string> = {}
    const products = regionalProducts.filter(product => product.object_path)
    void Promise.all(products.map(async product => {
      const blob = await asset(token, taskID, product.object_path!, controller.signal)
      if (controller.signal.aborted) return
      urls[product.product_id] = URL.createObjectURL(blob)
      setRegionalURLs(current => ({...current, [product.product_id]: urls[product.product_id]}))
    })).catch(e => { if (!controller.signal.aborted) setProblem({key:'regional_comparison', message:failure(e)}) })
    return () => {
      controller.abort()
      Object.values(urls).forEach(URL.revokeObjectURL)
    }
  }, [token, taskID, regionalProducts])
  useEffect(() => {
    if (!key) return
    const controller = new AbortController()
    let objectURL = ''
    void asset(token, taskID, key, controller.signal).then(blob => {
      if (controller.signal.aborted) return
      objectURL = URL.createObjectURL(blob)
      setPicture({key, url:objectURL})
      setProblem(null)
    }).catch(e => { if (!controller.signal.aborted) setProblem({key, message:failure(e)}) })
    return () => { controller.abort(); if (objectURL) URL.revokeObjectURL(objectURL) }
  }, [token, taskID, key])
  const selectedComparison = comparison.find(s => s.sweep_number === selectedSweep)
  useEffect(() => {
    if (!selectedComparison) return
    const controller = new AbortController()
    let rawURL = '', qcURL = ''
    void Promise.all([
      asset(token, taskID, selectedComparison.raw, controller.signal),
      asset(token, taskID, selectedComparison.qc, controller.signal),
    ]).then(([raw, qc]) => {
      if (controller.signal.aborted) return
      rawURL = URL.createObjectURL(raw); qcURL = URL.createObjectURL(qc)
      setCompareURLs({raw: rawURL, qc: qcURL}); setProblem(null)
    }).catch(e => { if (!controller.signal.aborted) setProblem({key:'comparison', message:failure(e)}) })
    return () => { controller.abort(); if (rawURL) URL.revokeObjectURL(rawURL); if (qcURL) URL.revokeObjectURL(qcURL) }
  }, [token, taskID, selectedComparison])
  const message = problem?.key === key || problem?.key === 'manifest' ? problem.message : ''
  return <section className="ops-preview"><h3>{comparison.length ? 'X 波段质控对照' : regionalProducts.length ? 'S/X 六分钟区域对照' : '已校验图件预览'}</h3>
    {regionalProducts.length > 0 && <section className="ops-regional-preview" aria-label="S波段、X波段与融合候选对照">
      <div className="ops-regional-grid">{regionalProducts.map(product => <figure key={product.product_id}>
        <figcaption>{product.label}<small>{product.status === 'available' ? `${(product.valid_echo_cells ?? product.valid_cells ?? 0).toLocaleString()} 个有效像元` : product.reason ?? '本时次无合格像元'}</small></figcaption>
        {product.object_path && regionalURLs[product.product_id] ? <img src={regionalURLs[product.product_id]} alt={`${product.label}候选网格快视图`}/>
          : product.object_path ? <p role="status">正在读取 {product.label}…</p> : <p className="ops-regional-empty">{product.reason ?? '该波段本时次无输入。'}</p>}
      </figure>)}</div>
      <p className="ops-caption">差值仅在 S 与 X 均有有效回波的像元计算；透明区为缺测。网格快视图遵循同一投影网格行序，不是经纬度瓦片。</p>
    </section>}
    {comparison.length > 0 && <div className="ops-preview-controls"><label>仰角层<select value={selectedSweep ?? ''} onChange={e => { const next = Number(e.target.value); setSelectedSweep(next); setCompareURLs(null); if (showFlags) setKey(comparison.find(s => s.sweep_number === next)?.flags ?? ''); }}>
      {comparison.map((s, index) => <option key={s.sweep_number} value={s.sweep_number}>{s.elevation_deg.toFixed(2)}° · 第 {s.sequence ?? index + 1} 层 · 编号 {s.sweep_number}</option>)}
    </select></label><button type="button" aria-pressed={showFlags} onClick={() => { setShowFlags(!showFlags); if (!showFlags && selectedComparison) setKey(selectedComparison.flags); else if (selectedComparison) setKey(selectedComparison.raw) }}>{showFlags ? '返回前后对照' : '查看质控标记'}</button></div>}
    {(message || problem?.key === 'comparison') && <Notice error>{problem?.key === 'comparison' ? problem.message : message}</Notice>}
    {selectedComparison && !showFlags && <div className="ops-preview-compare" aria-label="X波段质控前后对比">
      <figure><figcaption>原始反射率</figcaption>{compareURLs ? <img src={compareURLs.raw} alt="原始反射率PPI"/> : <p role="status">正在读取原始扫层…</p>}</figure>
      <figure><figcaption>基础质控后 <small>琥珀色表示未决门</small></figcaption>{compareURLs ? <img src={compareURLs.qc} alt="质控后反射率PPI，琥珀色为未决门"/> : <p role="status">正在读取质控扫层…</p>}</figure>
    </div>}
    {comparison.length > 0 && showFlags && <select aria-label="选择质控标记图层" value={key} onChange={e => setKey(e.target.value)}>
      {layers.map(l => <option key={l.object_path} value={l.object_path}>{l.title} {l.sweep_number != null ? `· 仰角层 ${l.sweep_number}` : ''}</option>)}
    </select>}
    {comparison.length > 0 && showFlags && <ul className="ops-preview-legend">{[{label:'确认无效/污染', color:'#bf3930'}, {label:'未决，待复核', color:'#eea028'}].map(item => <li key={item.label}><i style={{backgroundColor:item.color}}/>{item.label}</li>)}</ul>}
    {comparison.length === 0 && layers.length > 0 && <select aria-label="选择图层" value={key} onChange={e => setKey(e.target.value)}>
      {layers.map(l => <option key={l.object_path} value={l.object_path}>{l.title} {l.sweep_number != null ? `· 仰角层 ${l.sweep_number}` : ''}</option>)}
    </select>}
    {(comparison.length === 0 || showFlags) && picture?.key === key && <img src={picture.url} alt={layers.find(l => l.object_path === key)?.title ?? '候选对照图'} />}
    {comparison.length > 0 && <p className="ops-caption">站心极坐标 PPI · 原图上的确认无效/污染门已从显示场移除；琥珀色回波保留为待核，不代表确认剔除。缺测保持透明。</p>}
    {key && picture?.key !== key && !message && <p role="status">正在读取并核对所选图层…</p>}
  </section>
}
