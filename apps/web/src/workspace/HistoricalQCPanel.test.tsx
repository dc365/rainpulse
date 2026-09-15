import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { HistoricalQCPanel } from './HistoricalQCPanel'
import type { CycleSummary } from './model'
afterEach(()=>vi.unstubAllGlobals())
it('submits one durable QC-only batch without forecast requests',async()=>{
 const fetcher=vi.fn().mockResolvedValue({ok:true,json:async()=>null});vi.stubGlobal('fetch',fetcher)
 render(<HistoricalQCPanel cycles={[{issue_time:'2026-08-28T00:40:00Z'} as CycleSummary]}/> )
 fireEvent.click(screen.getByText('重算当日全部雷达质控'))
 await waitFor(()=>expect(fetcher).toHaveBeenCalledWith('/api/v1/admin/qc-batches',expect.objectContaining({method:'POST',body:'{"date":"2026-08-28"}'})))
 expect(fetcher.mock.calls.some(c=>String(c[0]).includes('/rerun'))).toBe(false)
})
it('shows QC failures and display progress independently',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,json:async()=>({request_id:'x',status:'QC_RUNNING',items:[{kind:'qc',id:'1',time:'2026-08-28T00:40:00Z',radar:'Z9591',status:'FAILED',error:'缺少解码数据'}]})}))
 render(<HistoricalQCPanel cycles={[{issue_time:'2026-08-28T00:40:00Z'} as CycleSummary]}/> )
 expect(await screen.findByText('缺少解码数据')).toBeTruthy()
 expect((screen.getByText('后台正在重算') as HTMLButtonElement).disabled).toBe(true)
})
