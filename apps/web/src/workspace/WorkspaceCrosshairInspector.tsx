import { useEffect, useState } from 'react'
import type { MapProbeDetail } from './mapProbe'
import { formatValidTime } from './model'

import { readExactSample, type ExactSample } from './sample'

export function WorkspaceCrosshairInspector({ probe }: { probe: MapProbeDetail | null }) {
  const [result, setResult] = useState<{ key: string; sample?: ExactSample; error?: string } | null>(null)
  const key = probe ? `${probe.assetUrl}:${probe.longitude}:${probe.latitude}` : ''
  useEffect(() => {
    if (!probe) return
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      void readExactSample(probe.assetUrl, probe.longitude, probe.latitude, controller.signal)
        .then(sample => {
          if (!controller.signal.aborted) setResult({ key, sample })
        }).catch((error: unknown) => {
          if (!controller.signal.aborted) setResult({ key, error: error instanceof Error ? error.message : '数值查询失败' })
        })
    }, 110)
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [key, probe])
  if (!probe) return null
  const current = result?.key === key ? result : null
  const sample = current?.sample
  return <aside className="workspace-exact-probe" aria-live="polite">
    <header><strong>{probe.panelLabel}</strong><small>{probe.longitude.toFixed(4)}°E · {probe.latitude.toFixed(4)}°N</small></header>
    <div>
      <strong>{sample ? sample.valid && sample.value != null ? `${sample.value.toFixed(2)} ${sample.unit}` : '缺测 / 无有效覆盖'
        : current?.error ?? '读取真实格点…'}</strong>
      {sample && <span>格点 {sample.grid_longitude.toFixed(3)}°E · {sample.grid_latitude.toFixed(3)}°N</span>}
      <small>{formatValidTime(probe.validTime)} · {sample?.frame_kind === 'derived' || probe.frameKind === 'derived' ? '派生帧' : sample?.frame_kind === 'analysis' ? '雷达分析' : '原生帧'}
        {sample?.confidence == null ? '' : ` · 技术质量 ${sample.confidence.toFixed(2)}（非降雨概率）`}</small>
    </div>
  </aside>
}
