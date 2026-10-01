type Catalog = { items: unknown[]; next_cursor: string | null }

// A partial day must not look like a complete observation history.
export async function readRadarCatalog<T extends Catalog>(url: string, signal: AbortSignal): Promise<T> {
  const identity = url.includes('/radar-scans?') ? 'scan_id' : 'radar_id'
  const items = new Map<string, unknown>()
  const seen = new Set<string>()
  let first: T | undefined
  let cursor = ''
  for (let index = 0; index < 100; index++) {
    signal.throwIfAborted()
    const response = await fetch(`${url}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`, { signal, cache: 'no-store' })
    if (!response.ok) throw new Error(`雷达目录读取失败（${response.status}）`)
    const page: unknown = await response.json()
    if (!page || typeof page !== 'object' || !('items' in page) || !Array.isArray(page.items)) throw new Error('雷达目录格式异常')
    if (!('next_cursor' in page) || (page.next_cursor !== null && typeof page.next_cursor !== 'string')) throw new Error('雷达目录分页异常')
    first ??= page as T
    for (const item of page.items) {
      if (!item || typeof item !== 'object' || !(identity in item) || typeof item[identity] !== 'string' || !item[identity]) throw new Error('雷达目录身份异常')
      if (!items.has(item[identity])) items.set(item[identity], item)
    }
    if (!page.next_cursor) return { ...first, items: [...items.values()], next_cursor: '' } as T
    if (seen.has(page.next_cursor)) throw new Error('雷达目录分页异常')
    seen.add(page.next_cursor)
    cursor = page.next_cursor
  }
  throw new Error('雷达目录超过分页上限，请缩小日期范围')
}
