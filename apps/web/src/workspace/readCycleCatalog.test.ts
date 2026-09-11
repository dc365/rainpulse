import { afterEach, expect, it, vi } from 'vitest'
import { readCycleCatalog } from './readCycleCatalog'

afterEach(() => vi.unstubAllGlobals())
const cycle = (id: string) => ({ cycle_id: id, issue_time: '2026-08-28T00:10:00Z', capabilities: {}, freshness_seconds: 1 })
const page = (ids: string[], cursor?: string) => new Response(JSON.stringify({ schema_version: '1.0', items: ids.map(cycle), next_cursor: cursor }))
it('follows all pages and collapses duplicate identities', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(page(['latest'], 'older')).mockResolvedValueOnce(page(['latest', 'morning']))
  vi.stubGlobal('fetch', fetcher)
  const result = await readCycleCatalog(new AbortController().signal)
  expect(result.items.map(c => c.cycle_id)).toEqual(['latest', 'morning'])
  expect(fetcher.mock.calls[1][0]).toContain('cursor=older')
})
it('rejects a repeated cursor and a later-page failure instead of returning partial history', async () => {
  vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve(page(['latest'], 'loop'))))
  await expect(readCycleCatalog(new AbortController().signal)).rejects.toThrow('分页异常')
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(page(['latest'], 'older')).mockResolvedValueOnce(new Response('', {status:503})))
  await expect(readCycleCatalog(new AbortController().signal)).rejects.toThrow('503')
})
