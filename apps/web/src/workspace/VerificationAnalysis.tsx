import { useEffect, useRef, useState } from 'react'
import type { CycleSummary, WorkspaceCycleDetail } from './model'
import { algorithmNames, metricNames, localStamp, batchCycleIDs, curvePoints, terminalJob,
  type AnalysisJob, type JobInput, type Spectrum } from './verificationAnalysisModel'

const api='/api/v1/workspace/verification'
const colors:Record<string,string>={lk:'var(--rp-teal)',steps:'var(--rp-ink)',nowcastnet:'var(--rp-muted)',qpe:'var(--rp-ink)'}
const number=(v:number|null|undefined)=>v==null?'不适用':v.toFixed(3)

function useJob() {
  const [job,setJob]=useState<AnalysisJob|null>(null)
  const [error,setError]=useState('')
  const [starting,setStarting]=useState(false)
  const sequence=useRef(0)
  const load=async(id:string)=>{
    const response=await fetch(`${api}/jobs/${id}`)
    if(!response.ok)throw Error('读取检验任务失败')
    return await response.json() as AnalysisJob
  }
  useEffect(()=>{
    if(!job || terminalJob(job.status))return
    const controller=new AbortController()
    let timer:ReturnType<typeof setTimeout>
    const poll=async()=>{
      try {const response=await fetch(`${api}/jobs/${job.id}`,{signal:controller.signal});if(!response.ok)throw Error('读取任务进度失败');const next=await response.json();if(!controller.signal.aborted){setJob(next);setError('')}}
      catch(e){if(!controller.signal.aborted)setError(String((e as Error).message))}
      if(!controller.signal.aborted)timer=setTimeout(poll,1500)
    }
    timer=setTimeout(poll,1500)
    return()=>{controller.abort();clearTimeout(timer)}
  },[job?.id,job?.status])
  return {job,error,starting,
    restore:async(id:string)=>{try{setJob(await load(id));setError('')}catch(e){setError(String((e as Error).message))}},
    start:async(input:JobInput)=>{
      const version=++sequence.current;setStarting(true);setError('')
      try{const response=await fetch(`${api}/jobs`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(input)});const data=await response.json()
        if(!response.ok)throw Error(data.error || '无法启动检验任务')
        if(version===sequence.current)setJob(data)
      }catch(e){if(version===sequence.current)setError(String((e as Error).message))}
      finally{if(version===sequence.current)setStarting(false)}
    },cancel:async()=>{try{if(job){const response=await fetch(`${api}/jobs/${job.id}/cancel`,{method:'POST'});if(!response.ok)throw Error('取消请求失败')}}catch(e){setError(String((e as Error).message))}}}
}

type Series={name:string;points:{x:number;y:number|null}[]}
function AnalysisChart({series,xLabel,yLabel,log=false,onPick,selected}:{series:Series[];xLabel:string;yLabel:string;log?:boolean;onPick?:(x:number,name:string)=>void;selected?:number}) {
  const values=series.flatMap(s=>s.points).filter(p=>p.y!=null && (!log || p.y>0))
  if(!values.length)return <p role="status">暂无可绘制数值（无事件、零谱功率或有效样本不足）。</p>
  const tx=(x:number)=>log?Math.log10(x):x
  const ty=(y:number)=>log?Math.log10(y):y
  const minX=log?Math.min(...values.map(p=>tx(p.x))):0,maxX=log?Math.max(...values.map(p=>tx(p.x))):120
  const minY=log?Math.min(...values.map(p=>ty(p.y!))):0,maxY=log?Math.max(...values.map(p=>ty(p.y!))):Math.max(1,...values.map(p=>p.y!))
  const x=(v:number)=>52+(tx(v)-minX)/(maxX-minX||1)*650
  const y=(v:number)=>170-(ty(v)-minY)/(maxY-minY||1)*145
  return <>
    <svg viewBox="0 0 740 208" className="verification-chart" aria-label={`${yLabel}随${xLabel}变化`}>
      {[0,.5,1].map(f=><g key={f}><line x1="52" x2="702" y1={170-f*145} y2={170-f*145} stroke="var(--rp-line)"/><text x="45" y={174-f*145} textAnchor="end">{log?Math.pow(10,minY+f*(maxY-minY)).toExponential(1):(f*maxY).toFixed(2)}</text></g>)}
      <text x="52" y="14">{yLabel}</text><text x="702" y="204" textAnchor="end">{xLabel}{log?'（对数）':''}</text>
      {(log?[Math.pow(10,minX),Math.pow(10,(minX+maxX)/2),Math.pow(10,maxX)]:[0,30,60,90,120]).map(v=><text key={v} x={x(v)} y="188" textAnchor="middle">{log?v.toFixed(1):`+${v}`}</text>)}
      {!log&&selected!=null&&<line x1={x(selected)} x2={x(selected)} y1="22" y2="172" stroke="var(--rp-teal)" strokeDasharray="2 3"/>}
      {series.map((s,index)=>{let path='';let drawing=false;for(const p of s.points){if(p.y==null||(log&&p.y<=0)){drawing=false;continue}path+=`${drawing?'L':'M'}${x(p.x)},${y(p.y)} `;drawing=true}
        return <g key={s.name} style={{color:colors[s.name]}}><path d={path} fill="none" stroke="currentColor" strokeWidth="2" strokeDasharray={index===1?'6 3':index===2?'2 3':undefined}/>
          {s.points.filter(p=>p.y!=null&&(!log||p.y>0)).map(p=><circle key={p.x} cx={x(p.x)} cy={y(p.y!)} r={onPick?4:2} fill="currentColor"
            role={onPick?'button':undefined} tabIndex={onPick?0:undefined} aria-label={`${algorithmNames[s.name]} +${p.x} 分钟 ${yLabel} ${number(p.y)}`}
            onClick={()=>onPick?.(p.x,s.name)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();onPick?.(p.x,s.name)}}}>
            <title>{algorithmNames[s.name]} · {log?p.x.toFixed(1):`+${p.x}`} · {number(p.y)}</title></circle>)}</g>})}
    </svg>
    <div className="analysis-legend">{series.map((s,i)=><span key={s.name} style={{color:colors[s.name]}}>{i===1?'┄':i===2?'┈':'━'} {algorithmNames[s.name]}</span>)}</div>
  </>
}

function JobProgress({job,onCancel}:{job:AnalysisJob|null;onCancel:()=>void}) {
  if(!job)return null
  const names:Record<string,string>={running:'计算中',complete:'已完成',failed:'未完成',cancelled:'已取消',interrupted:'已中断'}
  return <div className="analysis-progress" role="status"><span>{names[job.status]} · {job.completed}/{job.total} 组时效</span>
    <progress max={job.total} value={job.completed}/>{job.status==='running'&&<button onClick={onCancel}>取消计算</button>}
    <small>任务阈值 &gt;{job.input.threshold} mm/h · 邻域 {job.input.window_km} km · {job.input.algorithms.map(a=>algorithmNames[a]).join(' / ')} · {localStamp(job.started_at).replace('T',' ')} 北京时间</small>
    {(job.error||job.persistence_error)&&<span>{job.error||job.persistence_error}</span>}
  </div>
}

export function VerificationAnalysis({detail,cycles,validTime,threshold,windowKM,onThresholdChange,onWindowChange,onNavigate}:{
  detail:WorkspaceCycleDetail;cycles:CycleSummary[];validTime:string|null;threshold:number;windowKM:number;
  onThresholdChange:(v:number)=>void;onWindowChange:(v:number)=>void;onNavigate:(id:string,lead:number,algorithm:string)=>void
}) {
  const [open,setOpen]=useState(false)
  const [tab,setTab]=useState('curve')
  const [algorithms,setAlgorithms]=useState(['lk','steps','nowcastnet'])
  const [metric,setMetric]=useState('fss')
  const [from,setFrom]=useState(localStamp(detail.issue_time).slice(0,10)+'T00:00')
  const [to,setTo]=useState(localStamp(detail.issue_time).slice(0,10)+'T23:59')
  const curve=useJob(),batch=useJob()
  const [saved,setSaved]=useState<AnalysisJob[]>([])
  const [psd,setPSD]=useState<{key:string;value?:Spectrum;error?:string}|null>(null)
  const [psdRevision,setPSDRevision]=useState(0)
  const lead=validTime?Math.round((Date.parse(validTime)-Date.parse(detail.issue_time))/60000):0
  const signature=JSON.stringify([detail.cycle_id,algorithms,detail.panels.map(p=>p.frames.map(f=>[f.image_url,f.sha256]))])
  const psdKey=JSON.stringify([signature,lead,psdRevision])
  const requestedCurve=useRef('')
  const interval=algorithms.includes('nowcastnet')?10:5
  const leads=Array.from({length:120/interval},(_,i)=>(i+1)*interval)
  const curveInput:JobInput={cycle_ids:[detail.cycle_id],algorithms,leads,threshold,window_km:windowKM}
  const curveKey=JSON.stringify([signature,threshold,windowKM])
  useEffect(()=>{
    if(open&&tab==='curve'&&requestedCurve.current!==curveKey&&algorithms.length){requestedCurve.current=curveKey;void curve.start(curveInput)}
    // Inputs are serialized; do not restart on polling or metric display changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[open,tab,curveKey])
  useEffect(()=>{
    if(!open||tab!=='psd'||lead<=0||!algorithms.length)return
    const controller=new AbortController()
    void fetch(`${api}/compare`,{method:'POST',headers:{'Content-Type':'application/json'},signal:controller.signal,
      body:JSON.stringify({cycle_id:detail.cycle_id,algorithms,lead_minutes:lead,psd:true})}).then(async r=>{
        if(!r.ok)throw Error('PSD 服务暂不可用');const data=await r.json()
        if(data.cycle_id!==detail.cycle_id||data.lead_minutes!==lead||JSON.stringify(data.algorithms)!==JSON.stringify(algorithms))throw Error('PSD 结果时效不匹配')
        if(data.status!=='ready')throw Error(data.reason||'无可用 PSD')
        if(!controller.signal.aborted)setPSD({key:psdKey,value:data.psd})
      }).catch(e=>{if(!controller.signal.aborted)setPSD({key:psdKey,error:String(e.message)})})
    return()=>controller.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[open,tab,psdKey])
  useEffect(()=>{
    if(!open||tab!=='batch')return
    const controller=new AbortController()
    void fetch(`${api}/jobs`,{signal:controller.signal}).then(r=>r.json()).then(data=>{if(!controller.signal.aborted)setSaved(data.items||[])}).catch(()=>{})
    return()=>controller.abort()
  },[open,tab,batch.job?.status])
  const currentPSD=psd?.key===psdKey?psd:null
  const ids=batchCycleIDs(cycles,from,to)
  const busy=curve.starting||batch.starting||curve.job?.status==='running'||batch.job?.status==='running'
  const curveMatches=curve.job?.input.cycle_ids.length===1&&curve.job.input.cycle_ids[0]===detail.cycle_id
  const curveJob=curveMatches?curve.job:null
  return <section className={`verification-analysis${open?' expanded':''}`} aria-label="检验分析">
    <button className="analysis-toggle" aria-expanded={open} onClick={()=>setOpen(v=>!v)}>{open?'收起':'展开'}检验分析 <small>时效曲线 · 空间频谱 · 案例统计</small></button>
    {open&&<div className="analysis-body">
      <div role="tablist" aria-label="检验分析类型" className="analysis-tabs">{[['curve','时效曲线'],['psd','空间频谱 PSD'],['batch','案例统计']].map(([id,label])=><button role="tab" key={id} aria-selected={tab===id} onClick={()=>setTab(id)}>{label}</button>)}</div>
      <div className="analysis-controls"><span>共同域比较</span>{['lk','steps','nowcastnet'].map(a=><label key={a}><input type="checkbox" checked={algorithms.includes(a)} disabled={busy} onChange={()=>setAlgorithms(v=>v.includes(a)?v.filter(x=>x!==a):[...v,a])}/>{algorithmNames[a]}</label>)}
        {tab!=='psd'&&<><label>阈值 <select aria-label="分析雨强阈值" value={threshold} disabled={busy} onChange={e=>onThresholdChange(Number(e.target.value))}>{[1,5,10,20,50].map(v=><option key={v} value={v}>&gt;{v} mm/h</option>)}</select></label>
          <label>邻域 <select aria-label="分析邻域" value={windowKM} disabled={busy} onChange={e=>onWindowChange(Number(e.target.value))}>{[1,5,10,20,40].map(v=><option key={v} value={v}>{v} km</option>)}</select></label>
          <label>指标 <select aria-label="曲线指标" value={metric} onChange={e=>setMetric(e.target.value)}>{Object.entries(metricNames).map(([v,label])=><option key={v} value={v}>{label}</option>)}</select></label></>}
      </div>
      {!algorithms.length&&<p>请至少选择一个算法。</p>}
      {tab==='curve'&&<>
        <JobProgress job={curve.job} onCancel={()=>void curve.cancel()}/>{curve.error&&<p role="status">{curve.error}</p>}
        <button disabled={busy||!algorithms.length} onClick={()=>void curve.start(curveInput)}>重新计算当前起报曲线</button>
        {curveJob&&<AnalysisChart series={curveJob.input.algorithms.map(a=>({name:a,points:curvePoints(curveJob,a,metric).map(p=>({x:p.lead,y:p.value}))}))}
          xLabel="预报时效 / 分钟" yLabel={metricNames[metric]} selected={lead} onPick={(l,a)=>onNavigate(curveJob.input.cycle_ids[0],l,a)}/>}
        <small>点击曲线点联动原时间轴及地图。选中 NowcastNet 时使用原生 10 分钟共同节点；空缺不连线。共同域得分可能与上方单算法配对得分不同。</small>
      </>}
      {tab==='psd'&&<>
        <div className="analysis-controls"><strong>当前时效 +{lead} 分钟</strong><button disabled={!algorithms.length} onClick={()=>setPSDRevision(v=>v+1)}>重新计算 PSD</button><small>不受雨强阈值、邻域影响</small></div>
        {lead<=0?<p>请选择未来原生预报时效。</p>:!currentPSD?<p role="status">正在计算共同区域 PSD…</p>:currentPSD.error||currentPSD.value?.status!=='ready'?<p role="status">{currentPSD.error||currentPSD.value?.reason}</p>:<>
          <AnalysisChart log xLabel="空间尺度 / km" yLabel="PSD / (mm/h)² km²" series={Object.entries(currentPSD.value.series||{}).map(([name,ys])=>({name,points:ys.map((y,i)=>({x:currentPSD.value!.scale_km![i],y}))}))}/>
          <small>分析区域 {currentPSD.value.shape?.join(' × ')} 格，占全域 {((currentPSD.value.coverage||0)*100).toFixed(1)}%；边界 {currentPSD.value.bounds?.map(v=>v.toFixed(3)).join(', ')}。同域去均值、Hann 窗；不填补缺测。零功率不显示在对数轴，PSD 高低不是优劣排名。</small>
        </>}
      </>}
      {tab==='batch'&&<>
        <div className="analysis-controls"><label>起报开始（北京时间）<input aria-label="统计起报开始" type="datetime-local" value={from} onChange={e=>setFrom(e.target.value)}/></label>
          <label>结束<input aria-label="统计起报结束" type="datetime-local" value={to} onChange={e=>setTo(e.target.value)}/></label>
          <span>{ids.length} 个起报 · {leads.length} 个时效/起报</span><button disabled={busy||ids.length===0||ids.length>128||!algorithms.length} onClick={()=>void batch.start({...curveInput,cycle_ids:ids})}>开始统计</button>
        </div>
        <div className="analysis-controls"><span>最近报告</span>{saved.slice(0,8).map(j=><button key={j.id} onClick={()=>void batch.restore(j.id)}>{localStamp(j.started_at).replace('T',' ')} · {j.input.cycle_ids.length} 起报</button>)}</div>
        <JobProgress job={batch.job} onCancel={()=>void batch.cancel()}/>{batch.error&&<p role="status">{batch.error}</p>}
        {batch.job?.summary&&<>
          <p>有效配对 {batch.job.summary.matched_records} 组 · 跳过 {batch.job.summary.skipped_records} 组。各指标使用所选算法共同可评分样本，按起报/时效等权平均。</p>
          <div className="analysis-table"><table><thead><tr><th>算法</th>{['csi','fss','neighborhood_csi','mae','rmse','coverage'].map(m=><th key={m}>{metricNames[m]}</th>)}</tr></thead><tbody>
            {batch.job.input.algorithms.map(a=><tr key={a}><th>{algorithmNames[a]}</th>{['csi','fss','neighborhood_csi','mae','rmse','coverage'].map(m=><td key={m}>{number(batch.job!.summary!.overall[a][m].mean)}<small>N={batch.job!.summary!.overall[a][m].n}</small></td>)}</tr>)}</tbody></table></div>
          <AnalysisChart xLabel="预报时效 / 分钟" yLabel={`平均 ${metricNames[metric]}`} series={batch.job.input.algorithms.map(a=>({name:a,points:batch.job!.summary!.by_lead.map(row=>({x:row.lead_minutes,y:row.algorithms[a][metric]?.mean??null}))}))}/>
        </>}
        {batch.job&&<details><summary>起报明细与跳过原因（点击回放）</summary><div className="analysis-records">{batch.job.records.map(r=><button key={`${r.cycle_id}/${r.lead_minutes}`} onClick={()=>onNavigate(r.cycle_id,r.lead_minutes,batch.job!.input.algorithms[0])}>
          {r.issue_time?localStamp(r.issue_time).replace('T',' '):'周期不可用'} +{r.lead_minutes} · {r.status==='ready'?'已检验':r.reason}</button>)}</div></details>}
        <small>报告记录计算时间和源指纹，源产品更新后需重新统计。同样设置覆盖原报告，最多保留 8 份。重叠起报并非独立天气过程，不据此宣称显著优劣。</small>
      </>}
    </div>}
  </section>
}
