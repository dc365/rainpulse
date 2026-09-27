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
