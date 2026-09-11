import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { SpatialVerification } from './SpatialVerification'

afterEach(()=>{cleanup();vi.unstubAllGlobals()})
const props = {cycleID:'cycle',algorithm:'lk',lead:10,sourceKey:'source',threshold:5,enabled:true}
const metrics = {valid_cells:100,total_cells:200,coverage:.5,mae:1,rmse:2,rows:[
  {threshold:5,window_km:10,actual_km:[9,11],neighborhood_cells:50,csi:.2,fss:.7,neighborhood_csi:.4},
  {threshold:5,window_km:20,actual_km:[19,21],neighborhood_cells:0,csi:.2,fss:null,neighborhood_csi:null}]}
it('loads whole-field metrics without a map click; local window changes do not refetch',async()=>{
  const fetcher=vi.fn().mockResolvedValue({ok:true,json:async()=>({cycle_id:'cycle',algorithm:'lk',lead_minutes:10,status:'ready',metrics})})
  vi.stubGlobal('fetch',fetcher)
  render(<SpatialVerification {...props} />)
  expect(screen.getByText('正在计算整场检验…')).toBeTruthy()
  await screen.findByText('0.700')
  fireEvent.change(screen.getByLabelText('检验邻域公里'),{target:{value:'20'}})
  expect(screen.getAllByText('不适用')).toHaveLength(2)
  expect(fetcher).toHaveBeenCalledTimes(1)
})
it('rejects a mismatched identity and does not request unsupported frames',async()=>{
  const fetcher=vi.fn().mockResolvedValue({ok:true,json:async()=>({cycle_id:'other'})})
  vi.stubGlobal('fetch',fetcher)
  const {rerender}=render(<SpatialVerification {...props} enabled={false} />)
  expect(fetcher).not.toHaveBeenCalled()
  rerender(<SpatialVerification {...props} />)
  await screen.findByText('检验结果与所选时效不一致')
  fireEvent.click(screen.getByRole('button',{name:'重新检验'}))
  await waitFor(()=>expect(fetcher).toHaveBeenCalledTimes(2))
})
