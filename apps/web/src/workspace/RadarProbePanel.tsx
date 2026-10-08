import { useEffect,useState } from 'react'
import { CompositeSourceDetail, type NativeProbeSource } from './CompositeSourceDetail'
import type { MapCoordinate } from '../RasterGISMap'
export type ProbeLayer={id:string;asset_url?:string;result_id?:string;sweep_number?:number;product_id?:string;x:number;y:number}
type WinningSource={radar_id?:string;scan_id?:string;sweep_number?:number;qc_version?:string|null;qc_identity?:{parameters_sha256?:string|null;implementation_revision?:string|null}}
type Item={source?:NativeProbeSource;provenance_status?:string;state?:string;id:string;status:string;reason?:string;values?:Record<string,number|null>;identity?:Record<string,unknown>;winning_source?:WinningSource;source_identity_status?:string}
const labels:Record<string,string>={DBZH_RAW:'原始 dBZ',DBZH_QC:'QC dBZ',QUALITY_INDEX:'QI',QC_FLAGS:'标记',QC_ACTION:'动作',DISPLAY_VALID:'显示有效',SOURCE_RAY:'射线',SOURCE_GATE:'距离门',NO_ECHO_MASK:'无回波',CR_DBZH:'组合 dBZ',CR_DBZH_S_ONLY:'S 组合 dBZ',CR_DBZH_X_ONLY:'X 组合 dBZ',WINNER_SOURCE:'来源编号',WINNER_SWEEP_NUMBER:'扫层',WINNER_RAY:'原生射线',WINNER_GATE:'原生距离门',WINNER_AGE_SECONDS:'年龄 s',WINNER_HEIGHT_MSL_M:'高度 m',WINNER_QUALITY_SCORE:'质量',DBZH_SX_MINUS_S:'差值 dBZ'}
export function RadarProbePanel({point,layers}:{point:MapCoordinate|null;layers:ProbeLayer[]}){
 const key=JSON.stringify({point,layers})
 const [state,setState]=useState<{key:string;items?:Item[];error?:string}>()
 useEffect(()=>{const {point: selectedPoint,layers: selectedLayers}=JSON.parse(key) as {point:MapCoordinate|null;layers:ProbeLayer[]};if(!selectedPoint||!selectedLayers.length)return;const controller=new AbortController();void fetch('/api/v1/workspace/radar-layer-probes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({layers:selectedLayers}),signal:controller.signal}).then(async response=>{if(!response.ok)throw new Error(`点查失败（${response.status}）`);return response.json() as Promise<{items:Item[]}>}).then(data=>{if(!controller.signal.aborted)setState({key,items:data.items})}).catch(error=>{if(!controller.signal.aborted)setState({key,error:String(error)})});return()=>controller.abort()},[key])
 if(!point)return <p className="radar-probe-hint">点击地图查看各站源数值</p>
 if(!layers.length)return <p className="radar-probe-hint">该位置没有可点查的已选图层</p>
 const current=state?.key===key?state:undefined
 return <section className="radar-probe-panel" aria-label="源数值点查"><header><strong>源数值</strong> {point.longitude.toFixed(4)}°E · {point.latitude.toFixed(4)}°N</header><small>显示像素对应的原生采样值；透明叠图不会改变点查结果。未显示的 QC 值保留用于复核。</small>{current?.error?<p role="alert">{current.error}</p>:!current?<p>读取中…</p>:current.items?.map(item=><details key={item.id} open><summary>{item.id.toUpperCase()} · {item.status==='available'?({missing:'缺测',no_echo:'有效无回波',low_quality:'低质量',rejected:'显示已剔除',valid:'原始采样'}[item.state??'valid']??'原始采样'):item.status==='outside'?'覆盖范围外':item.reason??'无数值索引'}</summary>
 {item.winning_source&&<div><strong>来源 {item.winning_source.radar_id?.toUpperCase()??'未记录'} · 扫层 {item.winning_source.sweep_number??'未记录'}</strong><small>体扫 {item.winning_source.scan_id??'未记录'}</small><small title={item.winning_source.qc_identity?.parameters_sha256??undefined}>QC {item.winning_source.qc_version??'未记录'} · 参数 {item.winning_source.qc_identity?.parameters_sha256?.slice(0,12)??'未记录'}</small><small>实现 {item.winning_source.qc_identity?.implementation_revision??'未记录'}</small></div>}
 {item.source_identity_status==='unreported'&&<small>旧结果未记录来源身份</small>}
 <CompositeSourceDetail source={item.source} status={item.provenance_status}/>{item.values&&<dl>{Object.entries(item.values).map(([name,value])=><div key={name}><dt>{labels[name]??name}</dt><dd>{value===null?'缺测':Number.isInteger(value)?value:value.toFixed(2)}</dd></div>)}</dl>}</details>)}</section>
}
