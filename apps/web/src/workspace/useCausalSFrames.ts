import { useEffect, useState } from 'react'
import type { CompositeResult } from './CompositeMap'
import type { CycleSummary } from './model'
import { resolveCausalSFrames, type CausalFrames } from './causalSFrames'

export function useCausalSFrames(result: CompositeResult | undefined, time: string, cycles: CycleSummary[], revision: number) {
  const key = `${result?.result_id ?? ''}/${time}/${revision}`
  const catalogKey = JSON.stringify(cycles.map(c=>[c.cycle_id,c.issue_time,c.analysis_id]))
  const [state,setState] = useState<{key:string;data?:CausalFrames;error?:string}>()
  useEffect(()=>{
    if(!result?.manifest.sources?.some(s=>'scan_id' in s)) return
    const controller = new AbortController()
    void resolveCausalSFrames(result,time,cycles,async path=>{
      const response=await fetch(path,{signal:controller.signal,cache:'no-store'})
      if(!response.ok)throw new Error(`因果来源图件读取失败（${response.status}）`)
      return response.json()
    }).then(data=>{if(!controller.signal.aborted)setState({key,data})})
      .catch(error=>{if(!controller.signal.aborted)setState({key,error:String(error)})})
    return()=>controller.abort()
    // The catalogue identity excludes fresh render-time array instances.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[key,catalogKey])
  return state?.key===key?state:undefined
}
