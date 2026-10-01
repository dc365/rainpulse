import { afterEach, expect, it, vi } from 'vitest'
import { readRadarCatalog } from './readRadarCatalog'
const url = '/api/v1/workspace/radar-scans?radar_id=zf701&start=day'
const page = (ids: string[], cursor: string | null = '') => new Response(JSON.stringify({items: ids.map(scan_id => ({scan_id})), next_cursor: cursor, start: 'day'}))
afterEach(() => vi.unstubAllGlobals())
it('reads over 100 scans, preserves first metadata and deduplicates identities', async () => {
  const ids = Array.from({length:100}, (_, i) => String(i))
  const fetcher = vi.fn().mockResolvedValueOnce(page(ids, 'older/+')).mockResolvedValueOnce(page(['99', '100']))
  vi.stubGlobal('fetch', fetcher)
  const signal = new AbortController().signal
  const result = await readRadarCatalog(url, signal)
  expect(result.items).toHaveLength(101)
  expect(result.next_cursor).toBe('')
  expect(fetcher.mock.calls[1]).toEqual([`${url}&cursor=older%2F%2B`, {signal,cache:'no-store'}])
})
it('rejects loops and later errors without returning a partial day', async () => {
  vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve(page(['1'], 'loop'))))
  await expect(readRadarCatalog(url, new AbortController().signal)).rejects.toThrow('分页异常')
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(page(['1'], 'older')).mockResolvedValueOnce(new Response('', {status:503})))
  await expect(readRadarCatalog(url, new AbortController().signal)).rejects.toThrow('503')
})
it('does not fetch an aborted catalog', async () => {
  const fetcher = vi.fn()
  vi.stubGlobal('fetch', fetcher)
  const controller = new AbortController(); controller.abort()
  await expect(readRadarCatalog(url, controller.signal)).rejects.toThrow()
  expect(fetcher).not.toHaveBeenCalled()
})
it('rejects invalid identities instead of silently losing scans', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({items:[{radar_id:'zf701'}],next_cursor:''}))))
  await expect(readRadarCatalog(url, new AbortController().signal)).rejects.toThrow('身份异常')
})
