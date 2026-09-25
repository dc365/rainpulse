import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { XQCWorkspace } from './XQCWorkspace'

afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.history.replaceState({}, '', '/') })
const stationPage = {items:[{radar_id:'zf101',display_name:'ZF101',band:'X',registered:2,normalized:2,qc_ready:1},{radar_id:'zf505',display_name:'ZF505',band:'X',registered:1,normalized:1,qc_ready:0}],start:'2026-08-27T16:00:00Z',end:'2026-08-28T16:00:00Z',available_range:{start:'2026-08-28T00:00:00Z',end:'2026-08-28T00:06:00Z'},next_cursor:''}
const scans = {items:[{scan_id:'scan-b',radar_id:'zf101',volume_start:'2026-08-28T00:06:00Z',volume_end:'2026-08-28T00:07:00Z',state:'NORMALIZED',qc_status:'WAITING_QC',results:[]},{scan_id:'scan-a',radar_id:'zf101',volume_start:'2026-08-28T00:00:00Z',volume_end:'2026-08-28T00:01:00Z',state:'NORMALIZED',qc_status:'READY',results:[{result_id:'result-a',version:'candidate-v1',finished_at:'2026-09-25T10:00:00Z'}]}],next_cursor:''}
const detail = {result_id:'result-a',radar_id:'zf101',scan_id:'scan-a',volume_start:'2026-08-28T00:00:00Z',volume_end:'2026-08-28T00:01:00Z',candidate_only:true,operational_eligible:false,sweeps:[{sweep_number:4,sequence:2,elevation_deg:1.5,raw:'/raw4.png',qc:'/qc4.png',flags:'/flags4.png'},{sweep_number:0,sequence:1,elevation_deg:0.9,raw:'/raw0.png',qc:'/qc0.png',flags:'/flags0.png'}]}
function setup() {
 vi.stubGlobal('fetch',vi.fn(async (url: string) => ({ok:true,json:async()=>url.includes('radar-stations')?stationPage:url.includes('radar-scans')?scans:detail,blob:async()=>new Blob(['png'],{type:'image/png'})})))
 URL.createObjectURL=vi.fn(()=> 'blob:comparison');URL.revokeObjectURL=vi.fn()
}
it('discovers X independently, opens a successful historical scan and preserves native layer numbers', async()=>{
 setup();render(<XQCWorkspace />)
 await screen.findByRole('img',{name:'原始反射率 PPI'})
 expect((screen.getByRole('combobox',{name:'仰角层'}) as HTMLSelectElement).value).toBe('0')
 expect((screen.getByLabelText('资料日期') as HTMLInputElement).value).toBe('2026-08-28')
 expect(window.location.search).toContain('scan=scan-a')
 expect(screen.getByRole('region',{name:'统一有效时间轴'})).toBeTruthy()
 expect(screen.queryByText('未来 0–1 小时')).toBeNull()
 expect(screen.queryByRole('img',{name:/起报时刻/})).toBeNull()
 expect(screen.getByRole('option',{name:/ZF505/})).toBeTruthy()
 fireEvent.change(screen.getByRole('combobox',{name:'仰角层'}),{target:{value:'4'}})
 await waitFor(()=>expect(fetch).toHaveBeenCalledWith('/qc4.png',expect.anything()))
 expect(window.location.search).toContain('sweep=4')
 expect(vi.mocked(fetch).mock.calls.every(c=>!String(c[0]).includes('/cycles'))).toBe(true)
})
it('keeps a scan without QC visible and removes the previous image pair',async()=>{
 setup();render(<XQCWorkspace />);await screen.findByRole('img',{name:'原始反射率 PPI'})
 fireEvent.click(screen.getByRole('button',{name:/08:06 北京时间/}))
 await screen.findByText('该体扫已解码，等待基础质控。')
 expect(screen.queryByRole('img',{name:'原始反射率 PPI'})).toBeNull()
})
it('does not show a half-loaded comparison after one image fails',async()=>{
 setup()
 const original=vi.mocked(fetch).getMockImplementation()!
 vi.mocked(fetch).mockImplementation(async(...args)=>String(args[0])==='/qc0.png' ? {ok:false,status:410} as Response : original(...args))
 render(<XQCWorkspace />)
 await screen.findByRole('alert')
 expect(screen.queryByRole('img',{name:'原始反射率 PPI'})).toBeNull()
 expect(screen.queryByRole('img',{name:'基础质控后 PPI'})).toBeNull()
})
it('honours a shared non-contiguous sweep identity and fixed result',async()=>{
 setup();window.history.replaceState({},'', '/?preset=qc&band=X&station=zf101&scan=scan-a&sweep=4&result=result-a&date=2026-08-28')
 render(<XQCWorkspace />);await screen.findByRole('img',{name:'基础质控后 PPI'})
 expect((screen.getByLabelText('仰角层') as HTMLSelectElement).value).toBe('4')
 expect(fetch).toHaveBeenCalledWith('/qc4.png',expect.anything())
})
