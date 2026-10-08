import { useEffect, useState } from 'react'
import type { CompositeCoverageManifest } from './CompositeCoverage'
import type { GISMapExtent } from '../RasterGISMap'
import { exactComposite, assertCompositeIdentity } from './compositeIdentity'
export type SourceCounts = { requested_station_count: number|null; input_station_count: number; qualified_station_count: number|null; native_qualified_station_count: number|null; winner_station_count: number; source_entry_count: number; source_cut_count: number|null }
export type SourceScope = {contract:string;counts:SourceCounts;requested_radars:string[]|null;sources:Record<string,unknown>[];source_indices:number[]}

export type CompositeProduct = { unit?: string; legend?: {minimum?:number;label:string;color:string}[]; product_id: string; label: string; status: string; reason?: string; contributing_bands?: string[]; echo_contributing_bands?: string[]; provenance?: SourceScope; method?: string; method_label?: string; map?: { bounds: GISMapExtent; object_path: string } }
export type CompositeResult = { result_id: string; series_id?: string; series_legacy?: boolean; manifest: CompositeCoverageManifest & { source_scope?: SourceScope; source_contract?: string; method_label?: string; display_warning?: string; analysis_time: string; grid_id: string; method: string; comparison: { comparison_group: string; products: CompositeProduct[] } } }
type Result = CompositeResult
export type CompositeSeries = { series_id: string; product_id: string; network_release?: string; fingerprint?: string; frame_count?: number|null; count_complete?: boolean; requested_radars?: string[]; legacy: boolean }
type CompositePage = { items: { result_id: string; analysis_time: string; series_id: string }[]; series: CompositeSeries[]; selected_series_id: string; contract?: string; truncated?: boolean }
export function useComposite(time: string, revision: number, seriesID: string | undefined) {
  const [state,setState]=useState<{key:string; data?:Result; error?:string}>()
  const key=`${time}/${revision}/${seriesID ?? 'pending'}`
  useEffect(()=>{
    if(!time || seriesID === undefined)return
    const controller=new AbortController()
    async function read<T>(path:string):Promise<T>{const response=await fetch(path,{signal:controller.signal});if(!response.ok)throw new Error(`组合产品读取失败（${response.status}）`);return response.json()}
    void (async()=>{
      const start=new Date(Date.parse(time)).toISOString()
      const end=new Date(Date.parse(start)+60000).toISOString()
      const page=await read<CompositePage>(`/api/v1/workspace/radar-composites?series_mode=1&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}${seriesID ? `&series_id=${encodeURIComponent(seriesID)}` : ''}`)
      const item=exactComposite(page.items,time,seriesID)
      const data=item?await read<Result>(`/api/v1/workspace/radar-composites/${encodeURIComponent(item.result_id)}`):undefined
      if(data&&item&&page.contract==='rainpulse.sx-series-v1')assertCompositeIdentity(data,item,seriesID)
      if(!controller.signal.aborted)setState({key,data})
    })().catch(error=>{if(!controller.signal.aborted)setState({key,error:String(error)})})
    return()=>controller.abort()
  },[key,time,seriesID])
  return state?.key===key?state:undefined
}

export function useCompositeTimeline(day: string, revision: number, requestedSeriesID = '', automaticTime = '') {
  const key=`${day}/${revision}/${requestedSeriesID}`
  const [state,setState]=useState<{key:string;times:string[];seriesID:string;series:CompositeSeries[];error?:string}>()
  useEffect(()=>{
    if(!/^\d{4}-\d{2}-\d{2}$/.test(day))return
    const controller=new AbortController()
    const start=new Date(`${day}T00:00:00+08:00`).toISOString()
    const end=new Date(Date.parse(start)+86400000).toISOString()
    void fetch(`/api/v1/workspace/radar-composites?series_mode=1&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}${requestedSeriesID ? `&series_id=${encodeURIComponent(requestedSeriesID)}` : ''}`,{signal:controller.signal,cache:'no-store'})
      .then(async response=>{
        if(!response.ok)throw new Error(`组合时间轴读取失败（${response.status}）`)
        let page=await response.json() as CompositePage
        if(!requestedSeriesID){
          // The catalog is newest first. A newer local experiment must not
          // silently replace a wider network series in the default view.
          const widest=(page.series??[]).reduce<CompositeSeries|undefined>((best,s)=>
            new Set(s.requested_radars??[]).size>new Set(best?.requested_radars??[]).size?s:best,undefined)
          if(widest&&widest.series_id!==page.selected_series_id){
            const scoped=await fetch(`/api/v1/workspace/radar-composites?series_mode=1&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}&series_id=${encodeURIComponent(widest.series_id)}`,{signal:controller.signal,cache:'no-store'})
            if(!scoped.ok)throw new Error(`组合时间轴读取失败（${scoped.status}）`)
            page=await scoped.json() as CompositePage
            if(page.selected_series_id!==widest.series_id)throw new Error('组合产品系列不匹配')
          }
        }
        const times=[...new Set(page.items.map(item=>new Date(item.analysis_time).toISOString()))].sort()
        if(!controller.signal.aborted)setState({key,times,seriesID:page.selected_series_id ?? '',series:page.series ?? []})
      }).catch(error=>{if(!controller.signal.aborted)setState({key,times:[],seriesID:requestedSeriesID,series:[],error:String(error)})})
    return()=>controller.abort()
  },[day,key,requestedSeriesID])
  const base=state?.key===key?state:undefined
  const scope=base?.series.find(s=>s.series_id===base.seriesID)
  const at=Date.parse(automaticTime)
  const resolveTime=!requestedSeriesID && Number.isFinite(at) && Boolean(scope?.requested_radars?.length) && !base?.error
  const frameKey=`${key}/${Number.isFinite(at)?at:''}/${base?.seriesID??'pending'}`
  const [frame,setFrame]=useState<typeof state>()
  useEffect(()=>{
    if(!resolveTime || !base || !scope)return
    const controller=new AbortController()
    const stationSet=(s:CompositeSeries)=>JSON.stringify([...new Set(s.requested_radars??[])].sort())
    async function read(start:string,end:string,seriesID='') {
      const response=await fetch(`/api/v1/workspace/radar-composites?series_mode=1&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}${seriesID?`&series_id=${encodeURIComponent(seriesID)}`:''}`,{signal:controller.signal,cache:'no-store'})
      if(!response.ok)throw new Error(`组合时间轴读取失败（${response.status}）`)
      return response.json() as Promise<CompositePage>
    }
    void (async()=>{
      const start=new Date(at).toISOString()
      const page=await read(start,new Date(Date.parse(start)+60000).toISOString())
      // Resolve a frame, never merge the identities of independent series.
      // Same-size experiments with different stations are not this network.
      const candidate=page.series?.find(s=>s.product_id===scope.product_id && stationSet(s)===stationSet(scope))
      let resolved=base
      if(candidate && candidate.series_id!==base.seriesID){
        const dayStart=new Date(`${day}T00:00:00+08:00`).toISOString()
        const scoped=await read(dayStart,new Date(Date.parse(dayStart)+86400000).toISOString(),candidate.series_id)
        if(scoped.selected_series_id!==candidate.series_id)throw new Error('组合产品系列不匹配')
        resolved={key,times:[...new Set(scoped.items.map(i=>new Date(i.analysis_time).toISOString()))].sort(),seriesID:candidate.series_id,series:scoped.series??base.series}
      }
      if(!controller.signal.aborted)setFrame({...resolved,key:frameKey})
    })().catch(error=>{if(!controller.signal.aborted)setFrame({...base,key:frameKey,error:String(error)})})
    return()=>controller.abort()
  },[resolveTime,base,scope,frameKey,at,day,key])
  return resolveTime ? frame?.key===frameKey?frame:undefined : base
}
