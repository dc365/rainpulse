import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { useComposite, useCompositeTimeline } from './CompositeMap'

afterEach(() => vi.unstubAllGlobals())

it('pins detail lookup to the time axis series and keeps an absent frame absent', async () => {
  const fetcher = vi.fn<(url: string) => Promise<Response>>(async () => new Response(JSON.stringify({ items: [], series: [], selected_series_id: 'a'.repeat(64) })))
  vi.stubGlobal('fetch', fetcher)
  const { result } = renderHook(() => useComposite('2026-08-28T00:24:00Z', 0, 'a'.repeat(64)))
  await waitFor(() => expect(result.current).toBeDefined())
  expect(result.current?.data).toBeUndefined()
  expect(fetcher).toHaveBeenCalledTimes(1)
  expect(String(fetcher.mock.calls[0][0])).toContain(`series_id=${'a'.repeat(64)}`)
})

it('invalidates the prior series immediately when switching or waiting for the axis', async () => {
  const fetcher = vi.fn(async (url: string) => new Response(JSON.stringify(url.includes('radar-composites/')
    ? { result_id: 'old', manifest: { comparison: { products: [] } } }
    : { items: [{ result_id: 'old', analysis_time: '2026-08-28T00:24:00Z', series_id: 'a'.repeat(64) }], series: [], selected_series_id: 'a'.repeat(64) })))
  vi.stubGlobal('fetch', fetcher)
  const { result, rerender } = renderHook(({ series }: { series: string | undefined }) => useComposite('2026-08-28T00:24:00Z', 0, series), { initialProps: { series: 'a'.repeat(64) as string | undefined } })
  await waitFor(() => expect(result.current?.data?.result_id).toBe('old'))
  const calls = fetcher.mock.calls.length
  act(() => rerender({ series: undefined }))
  expect(result.current).toBeUndefined()
  expect(fetcher).toHaveBeenCalledTimes(calls)
})

it('returns the selected day series and requests an explicitly chosen series', async () => {
  const selected = 'b'.repeat(64)
  const fetcher = vi.fn<(url: string) => Promise<Response>>(async () => new Response(JSON.stringify({ selected_series_id: selected, series: [{ series_id: selected, product_id: 'v2' }], items: [{ analysis_time: '2026-08-28T00:12:00Z' }] })))
  vi.stubGlobal('fetch', fetcher)
  const { result } = renderHook(() => useCompositeTimeline('2026-08-28', 0, selected))
  await waitFor(() => expect(result.current?.times).toEqual(['2026-08-28T00:12:00.000Z']))
  expect(result.current?.seriesID).toBe(selected)
  expect(String(fetcher.mock.calls[0][0])).toContain(`series_id=${selected}`)
})

it('automatically prefers the widest declared station scope and pins its axis before rendering', async () => {
  const partial = 'a'.repeat(64), full = 'b'.repeat(64)
  const series = [
    { series_id: partial, requested_radars: ['z9591','zf101'] },
    { series_id: full, requested_radars: ['z9591','z9593','z9598','z9599','zf101'] },
    { series_id: 'c'.repeat(64), requested_radars: Array(10).fill('z9591') },
  ]
  const fetcher = vi.fn(async (url: string) => {
    const chosen = new URL(url, 'http://localhost').searchParams.get('series_id') || partial
    return new Response(JSON.stringify({ selected_series_id: chosen, series, items: [{ analysis_time: chosen === full ? '2026-08-28T00:12:00Z' : '2026-08-28T00:30:00Z' }] }))
  })
  vi.stubGlobal('fetch', fetcher)
  const { result } = renderHook(() => useCompositeTimeline('2026-08-28', 0))
  await waitFor(() => expect(result.current?.seriesID).toBe(full))
  expect(result.current?.times).toEqual(['2026-08-28T00:12:00.000Z'])
  expect(fetcher).toHaveBeenCalledTimes(2)
  expect(fetcher.mock.calls[1][0]).toContain(`series_id=${full}`)
})

it('keeps explicitly chosen partial scopes and latest order for equally wide scopes', async () => {
  const partial = 'a'.repeat(64), full = 'b'.repeat(64), older = 'c'.repeat(64)
  const series = [
    { series_id: partial, requested_radars: ['z9591'] },
    { series_id: full, requested_radars: ['z9591','z9593'] },
    { series_id: older, requested_radars: ['z9591','z9593'] },
  ]
  const fetcher = vi.fn(async (url: string) => new Response(JSON.stringify({ selected_series_id: new URL(url, 'http://localhost').searchParams.get('series_id') || partial, series, items: [] })))
  vi.stubGlobal('fetch', fetcher)
  const { result, rerender } = renderHook(({ requested }) => useCompositeTimeline('2026-08-28', 0, requested), { initialProps: { requested: partial } })
  await waitFor(() => expect(result.current?.seriesID).toBe(partial))
  expect(fetcher).toHaveBeenCalledTimes(1)
  rerender({ requested: '' })
  await waitFor(() => expect(result.current?.seriesID).toBe(full))
})

it('does not infer a full network from unknown historical scopes or borrow absent frames', async () => {
  const selected = 'a'.repeat(64)
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ selected_series_id: selected, series: [{series_id:selected}, {series_id:'b'.repeat(64),requested_radars:[]}], items: [] })))
  vi.stubGlobal('fetch', fetcher)
  const { result } = renderHook(() => useCompositeTimeline('2026-08-28', 0))
  await waitFor(() => expect(result.current?.seriesID).toBe(selected))
  expect(result.current?.times).toEqual([])
  expect(fetcher).toHaveBeenCalledTimes(1)
})

it('locates the widest network at the requested time and reuses the day catalog across times', async () => {
  const newest='a'.repeat(64), at18='b'.repeat(64), at12='c'.repeat(64)
  const scope=['z9591','z9595','zf101']
  const series=[newest,at18,at12].map(series_id=>({series_id,product_id:'full',requested_radars:scope}))
  const fetcher=vi.fn(async (url:string)=>{
    const q=new URL(url,'http://localhost').searchParams
    const narrow=Date.parse(q.get('end')!)-Date.parse(q.get('start')!)<86400000
    const selected=q.get('series_id') || (narrow ? q.get('start')?.includes('00:18')?at18:at12 : newest)
    const time=selected===newest?'00:30':selected===at18?'00:18':'00:12'
    return new Response(JSON.stringify({selected_series_id:selected,series:narrow?series.filter(s=>s.series_id===selected):series,items:[{result_id:selected,series_id:selected,analysis_time:`2026-08-28T${time}:00Z`}]}))
  })
  vi.stubGlobal('fetch',fetcher)
  const {result,rerender}=renderHook(({time})=>useCompositeTimeline('2026-08-28',0,'',time),{initialProps:{time:'2026-08-28T00:18:00Z'}})
  await waitFor(()=>expect(result.current?.seriesID).toBe(at18))
  expect(result.current?.times).toEqual(['2026-08-28T00:18:00.000Z'])
  rerender({time:'2026-08-28T00:12:00Z'})
  expect(result.current).toBeUndefined()
  await waitFor(()=>expect(result.current?.seriesID).toBe(at12))
  expect(fetcher.mock.calls.filter(([url])=>{
    const q=new URL(url,'http://localhost').searchParams
    return !q.has('series_id') && Date.parse(q.get('end')!)-Date.parse(q.get('start')!)===86400000
  })).toHaveLength(1)
})

it('does not substitute another station set or a smaller network at the target time', async () => {
  const full='a'.repeat(64), wrong='b'.repeat(64), partial='c'.repeat(64)
  const fetcher=vi.fn(async(url:string)=>{
    const q=new URL(url,'http://localhost').searchParams
    const narrow=Date.parse(q.get('end')!)-Date.parse(q.get('start')!)<86400000
    return new Response(JSON.stringify({selected_series_id:narrow?wrong:full,series:narrow?[
      {series_id:wrong,product_id:'full',requested_radars:['s1','x2']},
      {series_id:partial,product_id:'full',requested_radars:['s1']},
    ]:[{series_id:full,product_id:'full',requested_radars:['s1','x1']}],items:[]}))
  })
  vi.stubGlobal('fetch',fetcher)
  const {result}=renderHook(()=>useCompositeTimeline('2026-08-28',0,'','2026-08-28T00:18:00Z'))
  await waitFor(()=>expect(result.current?.seriesID).toBe(full))
  expect(fetcher.mock.calls.some(([url])=>url.includes('00%3A18'))).toBe(true)
  expect(result.current?.times).toEqual([])
})

it('never resolves a different series for a fixed historical URL', async () => {
  const fixed='a'.repeat(64)
  const fetcher=vi.fn(async()=>new Response(JSON.stringify({selected_series_id:fixed,series:[],items:[]})))
  vi.stubGlobal('fetch',fetcher)
  const {result}=renderHook(()=>useCompositeTimeline('2026-08-28',0,fixed,'2026-08-28T00:18:00Z'))
  await waitFor(()=>expect(result.current?.seriesID).toBe(fixed))
  expect(fetcher).toHaveBeenCalledTimes(1)
})
