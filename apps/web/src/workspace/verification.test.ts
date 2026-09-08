import { describe, expect, it } from 'vitest'
import type { ExactSample } from './sample'
import { comparePoint, nativeFrameAt } from './verification'
import type { WorkspacePanel } from './model'
const time = '2026-09-08T01:05:00Z'
const sample = (value: number): ExactSample => ({ value, valid: true, longitude:118, latitude:25,
  grid_longitude:118, grid_latitude:25, unit:'mm/h', valid_time:time, frame_kind:'native', lead_time_minutes:5, source:'fixture' })
describe('paired point verification', () => {
  it.each([[30,40,'命中'],[30,0,'漏报'],[0,30,'空报'],[20,20,'正确无事件']])('uses strict exceedance, not >=', (truth,forecast,event) => {
    expect(comparePoint(sample(Number(truth)),sample(Number(forecast)),time,20)).toMatchObject({event,sampleCount:1})
  })
  it('reports the signed difference', () => expect(comparePoint(sample(10), sample(7.5), time, 20).error).toBe(-2.5))
  it.each([
    { valid:false }, {value:NaN}, {unit:'mm'}, {unit:'%'}, {frame_kind:'derived'},
    {valid_time:'2026-09-08T01:10:00Z'}, {grid_longitude:118.01},
  ])('refuses non-comparable numerical data', change => {
    expect(() => comparePoint(sample(10), {...sample(10),...change},time,20)).toThrow()
  })
  it('never replaces missing truth with a nearby reference frame', () => {
    const panel = {frames:[{valid_time:time,reference_observation:true},{valid_time:time,frame_kind:'derived'}]} as WorkspacePanel
    expect(nativeFrameAt(panel,time)).toBeUndefined()
  })
})
