import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react'
import {afterEach,expect,it,vi} from 'vitest'
import {VerificationAnalysis} from './VerificationAnalysis'
import type {WorkspaceCycleDetail} from './model'
afterEach(()=>{cleanup();vi.unstubAllGlobals()})
it('stays quiet while collapsed, starts curve on expansion, and keeps metric changes local',async()=>{
  const fetcher=vi.fn().mockImplementation(async(_url:string,options:{body:string})=>{
    const input=JSON.parse(options.body)
    return {ok:true,json:async()=>({id:'job',status:'complete',input,started_at:'2026-09-10T00:00:00Z',completed:12,total:12,
      records:[{cycle_id:'case',lead_minutes:10,status:'ready',metrics:{lk:{fss:.7},steps:{fss:.5},nowcastnet:{fss:.6}}}]})}
  });vi.stubGlobal('fetch',fetcher)
  const navigate=vi.fn()
  const detail={cycle_id:'case',issue_time:'2026-08-28T08:30:00Z',panels:[]} as unknown as WorkspaceCycleDetail
  render(<VerificationAnalysis detail={detail} cycles={[]} validTime="2026-08-28T08:40:00Z" threshold={5} windowKM={10} onThresholdChange={vi.fn()} onWindowChange={vi.fn()} onNavigate={navigate}/>)
  expect(fetcher).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button',{name:/展开检验分析/}))
  await screen.findByRole('button',{name:'LK +10 分钟 FSS 0.700'})
  fireEvent.click(screen.getByRole('button',{name:'LK +10 分钟 FSS 0.700'}))
  expect(navigate).toHaveBeenCalledWith('case',10,'lk')
  fireEvent.change(screen.getByLabelText('曲线指标'),{target:{value:'csi'}})
  await waitFor(()=>expect(fetcher).toHaveBeenCalledTimes(1))
})
