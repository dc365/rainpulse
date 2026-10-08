export type NativeProbeSource={radar_id:string;scan_id:string;band:string;sweep_number:number;ray:number;gate:number;asset_sha256?:string}
export function CompositeSourceDetail({source,status}:{source?:NativeProbeSource;status?:string}){
 if(source&&status==='resolved')return <p title={source.asset_sha256??''}>来源 {source.radar_id.toUpperCase()} · {source.band} · 体扫 {source.scan_id} · 切面 {source.sweep_number} · 原生行 {source.ray} / 门 {source.gate}</p>
 if(status==='legacy_unresolved')return <p>旧结果未提供可核验的原生来源合同；数值可读，来源不猜测。</p>
 if(status==='no_echo_winner')return <p>此处没有有限回波赢家；无回波、未定和缺测状态见数值字段。</p>
 return null
}
