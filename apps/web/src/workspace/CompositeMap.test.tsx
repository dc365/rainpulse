import { renderHook, waitFor, cleanup } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { useComposite, useCompositeTimeline } from './CompositeMap'
afterEach(()=>{cleanup();vi.unstubAllGlobals()})
it('matches equivalent UTC representations and clears stale results when time changes',async()=>{
 const time='2026-08-28T00:06:00Z'
 vi.stubGlobal('fetch',vi.fn(async(input:string)=>({ok:true,json:async()=>input.includes('?')?{items:input.includes('00%3A06')?[{result_id:'result',analysis_time:time}]:[]}:{result_id:'result',manifest:{analysis_time:time,comparison:{products:[]}}}})))
 const {result,rerender}=renderHook(({time})=>useComposite(time,0),{initialProps:{time}})
 await waitFor(()=>expect(result.current?.data?.result_id).toBe('result'))
 rerender({time:'2026-08-28T00:12:00Z'})
 expect(result.current?.data).toBeUndefined()
 await waitFor(()=>expect(result.current).toBeDefined())
 expect(result.current?.data).toBeUndefined()
})

it('loads every six-minute composite in a Beijing day and clears the previous day',async()=>{
 const start=Date.parse('2026-08-27T16:00:00Z')
 const times=Array.from({length:240},(_,i)=>new Date(start+i*360000).toISOString())
 const fetcher=vi.fn(async(_input:string)=>({ok:true,json:async()=>({items:[...times].reverse().map(analysis_time=>({analysis_time,result_id:analysis_time}))})}))
 vi.stubGlobal('fetch',fetcher)
 const {result,rerender}=renderHook(({day})=>useCompositeTimeline(day,0),{initialProps:{day:'2026-08-28'}})
 await waitFor(()=>expect(result.current?.times).toEqual(times))
 const url=new URL(fetcher.mock.calls[0][0], 'http://localhost')
 expect(url.searchParams.get('start')).toBe('2026-08-27T16:00:00.000Z')
 expect(url.searchParams.get('end')).toBe('2026-08-28T16:00:00.000Z')
 rerender({day:'2026-08-29'})
 expect(result.current).toBeUndefined()
})
