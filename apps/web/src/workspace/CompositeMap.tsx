import { useEffect, useState } from 'react'
import type { CompositeCoverageManifest } from './CompositeCoverage'
import type { GISMapExtent } from '../RasterGISMap'
type Product = { unit?: string; legend?: {minimum?:number;label:string;color:string}[]; product_id: string; label: string; status: string; reason?: string; contributing_bands?: string[]; map?: { bounds: GISMapExtent; object_path: string } }
type Result = { result_id: string; manifest: CompositeCoverageManifest & { display_warning?: string; analysis_time: string; grid_id: string; method: string; comparison: { comparison_group: string; products: Product[] } } }
export function useComposite(time: string, revision: number) {
  const [state,setState]=useState<{key:string; data?:Result; error?:string}>()
  const key=`${time}/${revision}`
  useEffect(()=>{
    if(!time)return
    const controller=new AbortController()
    async function read<T>(path:string):Promise<T>{const response=await fetch(path,{signal:controller.signal});if(!response.ok)throw new Error(`组合产品读取失败（${response.status}）`);return response.json()}
    void (async()=>{
      const start=new Date(Math.floor(Date.parse(time)/360000)*360000).toISOString()
      const end=new Date(Date.parse(start)+360000).toISOString()
      const page=await read<{items:{result_id:string;analysis_time:string}[]}>(`/api/v1/workspace/radar-composites?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`)
      const item=page.items.find(i=>Date.parse(i.analysis_time)===Date.parse(start))
      const data=item?await read<Result>(`/api/v1/workspace/radar-composites/${encodeURIComponent(item.result_id)}`):undefined
      if(!controller.signal.aborted)setState({key,data})
    })().catch(error=>{if(!controller.signal.aborted)setState({key,error:String(error)})})
    return()=>controller.abort()
  },[key,time])
  return state?.key===key?state:undefined
}

export function useCompositeTimeline(day: string, revision: number) {
  const key=`${day}/${revision}`
  const [state,setState]=useState<{key:string;times:string[];error?:string}>()
  useEffect(()=>{
    if(!/^\d{4}-\d{2}-\d{2}$/.test(day))return
    const controller=new AbortController()
    const start=new Date(`${day}T00:00:00+08:00`).toISOString()
    const end=new Date(Date.parse(start)+86400000).toISOString()
    void fetch(`/api/v1/workspace/radar-composites?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`,{signal:controller.signal,cache:'no-store'})
      .then(async response=>{
        if(!response.ok)throw new Error(`组合时间轴读取失败（${response.status}）`)
        const page=await response.json() as {items:{analysis_time:string}[]}
        const times=[...new Set(page.items.map(item=>new Date(item.analysis_time).toISOString()))].sort()
        if(!controller.signal.aborted)setState({key,times})
      }).catch(error=>{if(!controller.signal.aborted)setState({key,times:[],error:String(error)})})
    return()=>controller.abort()
  },[day,key])
  return state?.key===key?state:undefined
}
