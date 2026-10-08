import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { RadarProbePanel } from './RadarProbePanel'
afterEach(()=>{cleanup();vi.unstubAllGlobals()})
it('keeps one request for equivalent layers and hides stale values when point changes',async()=>{
 const fetcher=vi.fn().mockResolvedValue({ok:true,json:async()=>({items:[{id:'zf101',status:'available',state:'no_echo',values:{DBZH_RAW:-8.25,DBZH_QC:null}}]})})
 vi.stubGlobal('fetch',fetcher)
 const props={point:{longitude:119,latitude:26},layers:[{id:'zf101',result_id:'one',x:.4,y:.5}]}
 const view=render(<RadarProbePanel {...props}/>);
 await screen.findByText('有效无回波',{exact:false});expect(screen.getByText('-8.25')).toBeTruthy()
 view.rerender(<RadarProbePanel point={{...props.point}} layers={props.layers.map(x=>({...x}))}/>);
 expect(fetcher).toHaveBeenCalledTimes(1)
 fetcher.mockImplementation(()=>new Promise(()=>{}))
 view.rerender(<RadarProbePanel point={{longitude:120,latitude:26}} layers={[{...props.layers[0],x:.9}]}/>);
 await waitFor(()=>expect(fetcher).toHaveBeenCalledTimes(2));expect(screen.queryByText('-8.25')).toBeNull();expect(screen.getByText('读取中…')).toBeTruthy()
})
it('shows the immutable winner QC identity instead of guessing from visible stations',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,json:async()=>({items:[{id:'sx_composite',status:'available',values:{CR_DBZH:35,WINNER_SOURCE:2},winning_source:{radar_id:'zf101',scan_id:'actual-scan',sweep_number:3,qc_version:'actual-qc',qc_identity:{parameters_sha256:'a'.repeat(64),implementation_revision:'actual-producer'}}}]})}))
 render(<RadarProbePanel point={{longitude:119,latitude:26}} layers={[{id:'sx_composite',result_id:'frozen',product_id:'sx_composite',x:.4,y:.5}]}/> )
 expect(await screen.findByText('来源 ZF101 · 扫层 3')).toBeTruthy()
 expect(screen.getByText('体扫 actual-scan')).toBeTruthy()
 expect(screen.getByText('QC actual-qc · 参数 aaaaaaaaaaaa')).toBeTruthy()
 expect(screen.getByText('实现 actual-producer')).toBeTruthy()
})
