import { expect, it } from 'vitest'
import { batchCycleIDs, curvePoints, verificationValidTime, type AnalysisJob } from './verificationAnalysisModel'

it('uses timeline timestamp spelling so clicking a curve is accepted by the time reducer',()=>{
  const issue='2026-08-28T08:30:00Z'
  expect(verificationValidTime(issue,30)).toBe('2026-08-28T09:00:00Z')
  expect(verificationValidTime(issue,30,['2026-08-28T09:00:00+00:00'])).toBe('2026-08-28T09:00:00+00:00')
})

it('filters inclusive Beijing issue times, not UTC days',()=>{
  const cycles=[{cycle_id:'a',issue_time:'2026-08-27T16:00:00Z'},{cycle_id:'b',issue_time:'2026-08-28T16:00:00Z'}]
  expect(batchCycleIDs(cycles,'2026-08-28T00:00','2026-08-28T23:59')).toEqual(['a'])
})
it('retains gaps as null instead of zero or a bridged curve',()=>{
  const job={input:{leads:[10,20,30]},records:[{lead_minutes:10,metrics:{lk:{fss:.5}}},{lead_minutes:20,status:'unavailable'},{lead_minutes:30,metrics:{lk:{fss:0}}}]} as AnalysisJob
  expect(curvePoints(job,'lk','fss')).toEqual([{lead:10,value:.5},{lead:20,value:null},{lead:30,value:0}])
})
