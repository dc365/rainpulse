import type { CycleList, CycleSummary } from './model'
import { assertCycleList } from './workspaceState'

// Share the same complete catalog in the workbench and regeneration panel.
// Keep the previous UI snapshot on any page failure, never silently show a prefix.
export async function readCycleCatalog(signal: AbortSignal): Promise<CycleList> {
  const seen = new Set<string>()
  const items = new Map<string, CycleSummary>()
  const degraded = new Set<string>()
  let cursor = ''
  for (;;) {
    const response = await fetch(`/api/v1/workspace/cycles?limit=200${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`, { signal })
    if (!response.ok) throw new Error(`历史目录读取失败（${response.status}）`)
    const page: unknown = await response.json()
    assertCycleList(page)
    for (const item of page.items) if (!items.has(item.cycle_id)) items.set(item.cycle_id, item)
    for (const source of page.degraded_sources ?? []) degraded.add(source)
    if (!page.next_cursor) return { ...page, items: [...items.values()], degraded_sources: [...degraded], next_cursor: null }
    if (typeof page.next_cursor !== 'string' || seen.has(page.next_cursor)) throw new Error('历史目录分页异常，请重试')
    seen.add(page.next_cursor)
    cursor = page.next_cursor
  }
}
