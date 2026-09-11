export const algorithmNames:Record<string,string> = {lk:'LK',steps:'STEPS P50',nowcastnet:'NowcastNet',qpe:'雷达 QPE'}
export const metricNames:Record<string,string> = {csi:'CSI',fss:'FSS',neighborhood_csi:'邻域 CSI',mae:'MAE',rmse:'RMSE',coverage:'覆盖率'}
export type MetricName = keyof typeof metricNames
export type CompactMetrics = Record<string,number|null>
export type AnalysisRecord = {cycle_id:string;issue_time:string;lead_minutes:number;status:string;reason?:string;metrics?:Record<string,CompactMetrics>}
export type JobInput = {cycle_ids:string[];algorithms:string[];leads:number[];threshold:number;window_km:number}
export type Statistics = Record<string,Record<string,{mean:number|null;n:number}>>
export type AnalysisJob = {id:string;status:string;input:JobInput;started_at:string;completed:number;total:number;
  records:AnalysisRecord[];error?:string;persistence_error?:string;summary?:{overall:Statistics;by_lead:{lead_minutes:number;algorithms:Statistics}[];matched_records:number;skipped_records:number}}
export type Spectrum = {status:string;reason?:string;scale_km?:number[];series?:Record<string,number[]>;bounds?:number[];shape?:number[];coverage?:number}
export const terminalJob = (status:string) => status !== 'running'
export const localStamp = (date:string) => new Date(Date.parse(date)+8*3600000).toISOString().slice(0,16)
export function verificationValidTime(issue:string,lead:number,timeline:string[]=[]) {
  const target=Date.parse(issue)+lead*60000
  return timeline.find(time=>Date.parse(time)===target) ?? new Date(target).toISOString().replace('.000Z','Z')
}
export function batchCycleIDs(cycles:{cycle_id:string;issue_time:string}[],from:string,to:string) {
  return cycles.filter(c=>localStamp(c.issue_time)>=from && localStamp(c.issue_time)<=to).map(c=>c.cycle_id)
}
export function curvePoints(job:AnalysisJob,algorithm:string,metric:string) {
  return job.input.leads.map(lead=>({lead,value:job.records.find(r=>r.lead_minutes===lead)?.metrics?.[algorithm]?.[metric] ?? null}))
}
