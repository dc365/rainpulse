import { expect,it } from 'vitest'
import { RadarImagePool } from './radarImagePool'
it('bounds 15-station raw/QC loads to four and evicts unpinned decoded images',async()=>{
 let active=0,peak=0,disposed=0
 const pool=new RadarImagePool(async(url)=>{active++;peak=Math.max(peak,active);await new Promise(resolve=>setTimeout(resolve,1));active--;return {url,bytes:720*720*4,dispose:()=>{disposed++}}})
 const frames=Array.from({length:30},(_,i)=>pool.acquire(`first/${i}`))
 await Promise.all(frames.map(f=>f.promise));expect(peak).toBe(4);expect(pool.stats().bytes).toBeLessThanOrEqual(128*1024*1024)
 frames.forEach(f=>f.release())
 for(let t=0;t<6;t++){const next=Array.from({length:30},(_,i)=>pool.acquire(`${t}/${i}`));await Promise.all(next.map(f=>f.promise));next.forEach(f=>f.release());expect(pool.stats().bytes).toBeLessThanOrEqual(pool.budget)}
 expect(disposed).toBeGreaterThan(0);pool.clearIdle();expect(pool.stats().bytes).toBe(0)
})
it('shares images and rejects over-budget pinned residency',async()=>{
 const pool=new RadarImagePool(async(url)=>({url,bytes:64,dispose:()=>{}}),100)
 const a=pool.acquire('a'),b=pool.acquire('a');await Promise.all([a.promise,b.promise]);expect(pool.stats().bytes).toBe(64)
 const c=pool.acquire('b');await expect(c.promise).rejects.toThrow('128 MiB');a.release();b.release();pool.clearIdle();expect(pool.stats().bytes).toBe(0)
})
