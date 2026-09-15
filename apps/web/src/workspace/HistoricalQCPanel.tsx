import { useEffect, useState } from 'react'
import type { CycleSummary } from './model'
export const beijingDate = (time: string) => new Date(new Date(time).getTime() + 8 * 3600_000).toISOString().slice(0, 10)
// Kept for callers that need to inspect legacy forecast coverage.
export function historyScope(cycles: CycleSummary[], date: string) {
 const selected = cycles.filter(c => beijingDate(c.issue_time) === date)
 return { runs: [...new Map(selected.filter(c => c.run_id).map(c => [c.run_id!, c])).values()].sort((a,b)=>a.issue_time.localeCompare(b.issue_time)), unavailable: selected.filter(c => !c.run_id) }
}
type Item = {kind: string,id:string,time:string,radar:string,status:string,error:string}
type Batch = {request_id:string,status:string,items:Item[]}
const labels: Record<string,string> = {PENDING:'等待计算',RUNNING:'计算中',SUCCEEDED:'完成',FAILED:'失败',SKIPPED:'已停止'}
async function request(date:string,submit=false):Promise<Batch|null> {
 const r=await fetch(`/api/v1/admin/qc-batches${submit?'':`?date=${date}`}`,submit?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({date})}:{cache:'no-store'})
 const data=await r.json();if(!r.ok)throw new Error(data.message??`请求失败 ${r.status}`);return data
}
export function HistoricalQCPanel({cycles}:{cycles:CycleSummary[]}) {
 const dates=[...new Set(cycles.map(c=>beijingDate(c.issue_time)))].sort().reverse()
 const [date,setDate]=useState(''),[batch,setBatch]=useState<Batch|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false)
 const chosen=dates.includes(date)?date:dates[0]??''
 useEffect(()=>{let alive=true;let timer:ReturnType<typeof setTimeout>;setBatch(null)
 async function poll(){try{const b=await request(chosen);if(alive){setBatch(b);setError('')}}catch(e){if(alive)setError(String(e))}finally{if(alive)timer=setTimeout(poll,5000)}}
 if(chosen)void poll();return()=>{alive=false;clearTimeout(timer)}
 },[chosen])
 const running=!!batch&&!['SUCCEEDED','FAILED'].includes(batch.status)
 async function submit(){setBusy(true);setError('');try{setBatch(await request(chosen,true))}catch(e){setError(String(e))}finally{setBusy(false)}}
 const count=(kind:string)=>{const items=batch?.items.filter(i=>i.kind===kind)??[];return `${items.filter(i=>i.status==='SUCCEEDED').length} / ${items.length}`}
 const failed=batch?.items.filter(i=>['FAILED','SKIPPED'].includes(i.status)).length??0
 return <section className="workspace-panel history-qc-panel">
 <header><small>Historical radar QC</small><h2>历史案例 · 雷达质控重算</h2></header>
 <div className="history-qc-controls"><label>历史案例（北京时间）<select value={chosen} onChange={e=>setDate(e.target.value)} disabled={busy}>{dates.map(d=><option key={d}>{d}</option>)}</select></label>
 <p>仅重算全部雷达站质控及网页质控对照图。体扫自动去重；不重算格点、QPE、LK 和其他预报。</p>
 <button disabled={!chosen||busy||running} onClick={()=>void submit()}>{busy?'提交中…':running?'后台正在重算':'重算当日全部雷达质控'}</button></div>
 <p>任务一次受理后可关闭页面，进度保存在服务器。每个对照图所需质控完成后即更新；其他产品保留原结果，原始观测保留。</p>
 {error&&<p role="alert">{error}</p>}
 {batch&&<><h3>{chosen}：质控 {count('qc')}；对照图 {count('display')}；失败或停止 {failed}</h3>
 <p>{running?'后台处理中':batch.status==='SUCCEEDED'?'本次质控与对照图更新完成':'批次结束，存在失败，请查看具体原因；可重新提交重算。'}</p>
 <progress max={batch.items.length||1} value={batch.items.filter(i=>i.status==='SUCCEEDED').length}/>
 <div className="history-qc-table"><table><thead><tr><th>北京时间</th><th>雷达 / 阶段</th><th>状态</th><th>原因</th></tr></thead><tbody>{batch.items.map(i=><tr key={`${i.kind}-${i.id}`}><td>{new Date(i.time).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}</td><td>{i.kind==='qc'?`${i.radar} 质控`:'网页对照图'}</td><td>{labels[i.status]??i.status}</td><td>{i.error||'—'}</td></tr>)}</tbody></table></div></>}
 </section>
}
