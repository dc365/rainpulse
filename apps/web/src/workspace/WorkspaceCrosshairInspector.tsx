import { useEffect, useState } from 'react'

import { readExactSample, type ExactSample } from './sample'

export type WorkspaceProbeEntry = {
  key: string
  assetUrl: string
  panelID: string
  panelLabel: string
  validTime: string
  frameKind: string
  longitude: number
  latitude: number
}

function formatTime(value: string) {
  const time = Date.parse(value)
  if (!Number.isFinite(time)) return value
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Taipei', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).format(new Date(time))
}

// One hovered grid point, every panel that covers it: the inspector fans the
// single probe out into a per-panel readout so cross-algorithm comparison no
// longer requires hovering each map in turn.
export function WorkspaceCrosshairInspector({ probes, coordinate }: {
  probes: WorkspaceProbeEntry[]
  coordinate: { longitude: number; latitude: number } | null
}) {
  const [results, setResults] = useState<Record<string, { sample?: ExactSample; error?: string }>>({})
  const signature = probes.map(probe => probe.key).join('|')

  useEffect(() => {
    if (!probes.length) return
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      void Promise.all(probes.map(probe =>
        readExactSample(probe.assetUrl, probe.longitude, probe.latitude, controller.signal)
          .then(sample => ({ key: probe.key, sample }))
          .catch((error: unknown) => ({ key: probe.key, error: error instanceof Error ? error.message : '数值查询失败' })),
      )).then(entries => {
        if (controller.signal.aborted) return
        const next: Record<string, { sample?: ExactSample; error?: string }> = {}
        for (const entry of entries) next[entry.key] = { sample: 'sample' in entry ? entry.sample : undefined, error: 'error' in entry ? entry.error : undefined }
        setResults(next)
      })
    }, 110)
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [signature]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!coordinate || !probes.length) return null
  return <aside className="workspace-exact-probe" aria-live="polite">
    <header>
      <strong>同格点对比</strong>
      <small>{coordinate.longitude.toFixed(4)}°E · {coordinate.latitude.toFixed(4)}°N</small>
    </header>
    <ul className="workspace-probe-list">
      {probes.map(probe => {
        const result = results[probe.key]
        const sample = result?.sample
        return <li key={probe.key}>
          <span className="workspace-probe-panel">{probe.panelLabel}</span>
          <strong>{sample
            ? sample.valid && sample.value != null ? `${sample.value.toFixed(2)} ${sample.unit}` : '缺测 / 无有效覆盖'
            : result?.error ?? '读取真实格点…'}</strong>
          <small>{formatTime(probe.validTime)} · {sample?.frame_kind === 'analysis' ? '雷达分析' : sample?.frame_kind === 'derived' || probe.frameKind === 'derived' ? '派生帧' : '原生帧'}</small>
        </li>
      })}
    </ul>
    <footer>空白不代表无雨；数值直接读取产品文件，不从图片颜色反推。</footer>
  </aside>
}
