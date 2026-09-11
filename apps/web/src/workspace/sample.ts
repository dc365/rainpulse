export type ExactSample = {
  longitude: number; latitude: number; grid_longitude: number; grid_latitude: number
  value?: number; confidence?: number; valid: boolean; unit: string
  lead_time_minutes: number; valid_time?: string; frame_kind: string
  derivation?: string; source: string
}

export async function readExactSample(assetUrl: string, longitude: number, latitude: number, signal: AbortSignal) {
  const query = new URLSearchParams({ asset_url: assetUrl, longitude: String(longitude), latitude: String(latitude) })
  const response = await fetch(`/api/v1/workspace/sample?${query}`, { signal })
  if (response.status === 422) throw new Error('该位置超出产品范围')
  if (response.status === 410) throw new Error('累计缓存已失效，请重新计算')
  if (!response.ok) throw new Error(response.status === 404 ? '该产品暂未提供数值查询' : '数值服务暂不可用')
  const sample = await response.json() as ExactSample
  if (typeof sample.valid !== 'boolean' || typeof sample.unit !== 'string'
    || !Number.isFinite(sample.grid_longitude) || !Number.isFinite(sample.grid_latitude)
    || (sample.valid && !Number.isFinite(sample.value))) throw new Error('数值响应格式不正确')
  return sample
}
