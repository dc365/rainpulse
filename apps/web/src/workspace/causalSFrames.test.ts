import { expect, it } from 'vitest'
import { causalSources, resolveCausalSFrames } from './causalSFrames'
import type { CompositeResult } from './CompositeMap'
import type { CycleSummary } from './model'
const time='2026-08-28T02:48:00Z'
const source={band:'S',radar_id:'z9591',scan_id:'scan-1042',asset_sha256:'qc-content',volume_start:'2026-08-28T02:37:49Z',volume_end:'2026-08-28T02:43:16Z'}
const result={result_id:'composite-1048',manifest:{analysis_time:time,sources:[source],comparison:{products:[]}}} as unknown as CompositeResult
const cycles=['02:42','02:48'].map((t,i)=>({cycle_id:`c${i}`,analysis_id:`a${i}`,issue_time:`2026-08-28T${t}:00Z`})) as CycleSummary[]
function layers(scan=source.scan_id,hash=source.asset_sha256) {return ['DBZH_RAW','DBZH_QC'].map(field=>({scope:'polar',field,radar_id:'z9591',scan_id:scan,qc_content_sha256:hash,sweep_number:0,image_url:`/${scan}/${field}.png`,layer_id:field}))}
it('uses the composite exact scan, not the nominal 10:48 native scan',async()=>{
 const r=await resolveCausalSFrames(result,time,cycles,async path=>({layers:path.includes('a1')?layers('scan-1048'):layers()}))
 expect(r.panels).toHaveLength(2)
 expect(r.panels.every(p=>p.frames[0].valid_time===time && p.frames[0].scan_id===source.scan_id)).toBe(true)
 expect(r.panels[0].frames[0].observation_time).toBe(source.volume_end)
})
it('never matches a different rebuild with the same scan',async()=>{
 const r=await resolveCausalSFrames(result,time,cycles,async()=>({layers:layers(source.scan_id,'old-content')}))
 expect(r.panels).toEqual([])
})
it('requires a same-sweep RAW/QC pair',async()=>{
 const r=await resolveCausalSFrames(result,time,cycles,async()=>({layers:[layers()[0],{...layers()[1],sweep_number:2}]}))
 expect(r.panels).toEqual([])
})
it('rejects future volumes and expired volumes',()=>{
 for(const volume_end of ['2026-08-28T02:48:55Z','2026-08-28T02:35:59Z'])
  expect(()=>causalSources({...result,manifest:{...result.manifest,sources:[{...source,volume_end}]}},time)).toThrow()
})
it('rejects conflicting station identity and wrong analysis time',()=>{
 expect(()=>causalSources({...result,manifest:{...result.manifest,sources:[source,{...source,scan_id:'other'}]}},time)).toThrow()
 expect(()=>causalSources(result,'2026-08-28T02:42:00Z')).toThrow()
})
it('bounds neighbouring metadata reads, never searches an entire historical catalogue',async()=>{
 let reads=0
 const long=Array.from({length:200},(_,i)=>({...cycles[0],analysis_id:`a${i}`,issue_time:new Date(Date.parse(time)+i*60000).toISOString()}))
 await resolveCausalSFrames(result,time,long,async()=>{reads++;return {layers:[]}})
 expect(reads).toBeLessThanOrEqual(7)
})

it('matches exact polar identity across differing downstream mosaic grids',async()=>{
 const composite={...result,manifest:{...result.manifest,grid_id:'full-horizontal-grid'}}
 const nativeCycles=cycles.map(c=>({...c,grid_id:'trusted-qpe-grid'}))
 const r=await resolveCausalSFrames(composite,time,nativeCycles,async()=>({layers:layers()}))
 expect(r.panels).toHaveLength(2)
})
