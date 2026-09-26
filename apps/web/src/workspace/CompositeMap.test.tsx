import { renderHook, waitFor, cleanup } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { useComposite } from './CompositeMap'
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
