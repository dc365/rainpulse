import { useEffect, useState } from 'react'

type Metrics = { valid_cells: number; total_cells: number; coverage: number; mae: number | null; rmse: number | null;
  rows: {threshold: number; window_km: number; actual_km: number[]; neighborhood_cells: number;
    csi: number | null; fss: number | null; neighborhood_csi: number | null}[] }
export function SpatialVerification({ cycleID, algorithm, lead, sourceKey, threshold, enabled, windowKM, onWindowChange }: {
  cycleID: string; algorithm: string; lead: number; sourceKey: string; threshold: number; enabled: boolean
  windowKM?: number; onWindowChange?: (value:number)=>void
}) {
  const [localKM, setKM] = useState(10)
  const km = windowKM ?? localKM
  const [revision, setRevision] = useState(0)
  const key = JSON.stringify([cycleID,algorithm,lead,sourceKey,revision])
  const [result, setResult] = useState<{key: string; metrics?: Metrics; error?: string} | null>(null)
  useEffect(() => {
    if (!enabled) return
    const controller = new AbortController()
    void fetch('/api/v1/workspace/verification', {method:'POST', headers:{'Content-Type':'application/json'}, signal:controller.signal,
      body:JSON.stringify({cycle_id:cycleID,algorithm,lead_minutes:lead})})
      .then(async response => {
        if (!response.ok) throw Error('整场检验服务暂不可用，请重试')
        const data = await response.json()
        if (data.cycle_id !== cycleID || data.algorithm !== algorithm || data.lead_minutes !== lead) throw Error('检验结果与所选时效不一致')
        if (data.status !== 'ready') throw Error(data.reason || '尚无整场检验结果')
        if (!Array.isArray(data.metrics?.rows)) throw Error('检验结果格式不正确')
        if (!controller.signal.aborted) setResult({key,metrics:data.metrics})
      }).catch(error => {if (!controller.signal.aborted) setResult({key,error:String(error.message)})})
    return () => controller.abort()
  }, [key,enabled,cycleID,algorithm,lead])
  if (!enabled) return null
  const current = result?.key === key ? result : null
  const metrics = current?.metrics
  const row = metrics?.rows.find(r => r.threshold===threshold && r.window_km===km)
  const score = (v: number | null | undefined) => v == null ? '不适用' : v.toFixed(3)
  return <div aria-label="整场空间检验">
    <div className="verification-context"><strong>整场检验</strong>
      <label>邻域宽度 <select aria-label="检验邻域公里" value={km} onChange={e=>{setKM(Number(e.target.value));onWindowChange?.(Number(e.target.value))}}>
        {[1,5,10,20,40].map(v=><option key={v} value={v}>{v} km</option>)}</select></label>
      <button type="button" onClick={()=>setRevision(v=>v+1)} disabled={!current}>重新检验</button>
      {metrics && <span>共同有效 {metrics.valid_cells.toLocaleString()} 格 · {(metrics.coverage*100).toFixed(1)}%</span>}
    </div>
    {!current ? <p role="status">正在计算整场检验…</p> : current.error ? <p role="status">{current.error}</p> : <>
      <div className="verification-values">
        <span>CSI <strong>{score(row?.csi)}</strong></span><span>FSS <strong>{score(row?.fss)}</strong></span>
        <span>邻域 CSI <strong>{score(row?.neighborhood_csi)}</strong></span>
        <span>MAE <strong>{score(metrics?.mae)} mm/h</strong></span><span>RMSE <strong>{score(metrics?.rmse)} mm/h</strong></span>
      </div>
      <small>邻域共同完整支持 {row?.neighborhood_cells.toLocaleString() ?? 0} 格；实际宽度 {row?.actual_km.map(v=>v.toFixed(1)).join(' × ')} km。
        邻域 CSI 按窗口内有无事件计算；无事件或有效支持不足时不评分。不同算法覆盖可能不同，不宜直接排名。</small>
    </>}
  </div>
}
