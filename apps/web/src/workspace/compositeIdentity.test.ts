import { describe, expect, it } from 'vitest'
import { assertCompositeIdentity, exactComposite } from './compositeIdentity'
describe('frozen composite identity',()=>{
 const time='2026-08-28T00:06:00Z'
 const items=[{result_id:'a',analysis_time:time,series_id:'s'},{result_id:'b',analysis_time:time,series_id:'x'}]
 it('selects the exact series',()=>expect(exactComposite(items,time,'x')?.result_id).toBe('b'))
 it('does not fall back to another series',()=>expect(exactComposite(items,time,'absent')).toBeUndefined())
 it('does not relabel a six-minute frame as a new minute',()=>expect(exactComposite(items,'2026-08-28T00:07:00Z')).toBeUndefined())
 it('rejects invalid query time',()=>expect(()=>exactComposite(items,'bad')).toThrow())
 const result={result_id:'a',series_id:'s',manifest:{analysis_time:time}}
 it('accepts the frozen result',()=>expect(()=>assertCompositeIdentity(result,items[0],'s')).not.toThrow())
 it('rejects a mismatched result',()=>expect(()=>assertCompositeIdentity({...result,result_id:'b'},items[0],'s')).toThrow())
 it('rejects a different observation time',()=>expect(()=>assertCompositeIdentity({...result,manifest:{analysis_time:'2026-08-28T00:12:00Z'}},items[0],'s')).toThrow())
 it('rejects a different processing series',()=>expect(()=>assertCompositeIdentity({...result,series_id:'x'},items[0],'s')).toThrow())
})
