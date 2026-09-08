import { useEffect, useRef, useState } from 'react'

import type { MapProbeDetail } from './mapProbeBridge'

type ExactSample = {
  longitude: number
  latitude: number
  grid_longitude: number
  grid_latitude: number
  value?: number
  confidence?: number
  valid: boolean
  unit: string
  lead_time_minutes: number
  valid_time?: string
  frame_kind: string
  derivation?: string
  source: string
}

type SampleError = {
  code?: string
  message?: string
}

export function WorkspaceCrosshairInspector() {
  const [probe, setProbe] = useState<MapProbeDetail | null>(null)
  const [sample, setSample] = useState<ExactSample | null>(null)
  const [status, setStatus] = useState<'idle' | 'loading' | 'ready' | 'unavailable'>('idle')
  const [unavailableMessage, setUnavailableMessage] = useState('')
  const sequence = useRef(0)

  useEffect(() => {
    const shell = document.querySelector<HTMLElement>('.workspace-shell')
    const onProbe = (event: Event) => {
      const detail = (event as CustomEvent<MapProbeDetail>).detail
      if (!detail) return
      setProbe(detail)
      setSample(null)
      setUnavailableMessage(detail.assetUrl ? '' : '当前图层没有可查询的数值资产')
      setStatus(detail.assetUrl ? 'idle' : 'unavailable')
      shell?.style.setProperty('--rp-crosshair-x', `${detail.xRatio * 100}%`)
      shell?.style.setProperty('--rp-crosshair-y', `${detail.yRatio * 100}%`)
      shell?.setAttribute('data-crosshair-visible', 'true')
    }
    const onClear = () => {
      sequence.current += 1
      setProbe(null)
      setSample(null)
      setUnavailableMessage('')
      setStatus('idle')
      shell?.removeAttribute('data-crosshair-visible')
    }
    window.addEventListener('rainpulse:map-probe', onProbe)
    window.addEventListener('rainpulse:map-probe-clear', onClear)
    return () => {
      window.removeEventListener('rainpulse:map-probe', onProbe)
      window.removeEventListener('rainpulse:map-probe-clear', onClear)
      shell?.removeAttribute('data-crosshair-visible')
    }
  }, [])

  useEffect(() => {
    if (!probe?.assetUrl) return

    const current = ++sequence.current
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      setStatus('loading')
      setUnavailableMessage('')
      const query = new URLSearchParams({
        asset_url: probe.assetUrl,
        longitude: String(probe.longitude),
        latitude: String(probe.latitude),
      })
      void fetch(`/api/v1/workspace/sample?${query}`, { signal: controller.signal })
        .then(async (response) => {
          if (current !== sequence.current) return
          if (!response.ok) {
            const payload = await response.json().catch(() => null) as SampleError | null
            setSample(null)
            setUnavailableMessage(sampleErrorLabel(payload?.code))
            setStatus('unavailable')
            return
          }
          setSample(await response.json() as ExactSample)
          setStatus('ready')
        })
        .catch((error: unknown) => {
          if (current === sequence.current
            && !(error instanceof DOMException && error.name === 'AbortError')) {
            setSample(null)
            setUnavailableMessage('精确格点服务暂不可用')
            setStatus('unavailable')
          }
        })
    }, 110)
    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [probe])

  if (!probe) return null
  const coordinate = `${probe.longitude.toFixed(4)}°E · ${probe.latitude.toFixed(4)}°N`
  return (
    <aside className="workspace-exact-probe" aria-live="polite">
      <header><strong>{probe.panelLabel || '同步格点'}</strong><small>{coordinate}</small></header>
      {status === 'ready' && sample ? (
        <div>
          <strong>{sample.valid && sample.value != null ? `${formatValue(sample.value)} ${sample.unit}` : '缺测'}</strong>
          <span>格点 {sample.grid_longitude.toFixed(3)}°E · {sample.grid_latitude.toFixed(3)}°N</span>
          <small>
            {sample.frame_kind === 'derived' ? '5分钟派生帧' : sample.frame_kind === 'analysis' ? '雷达分析' : '模型原生帧'}
            {sample.confidence == null ? '' : ` · 质量 ${sample.confidence.toFixed(2)}`}
          </small>
        </div>
      ) : (
        <div>
          <strong>{status === 'loading' ? '读取精确格点…' : '精确值暂不可用'}</strong>
          <span>{status === 'unavailable' ? unavailableMessage : '等待图层'}</span>
        </div>
      )}
    </aside>
  )
}

function sampleErrorLabel(code?: string) {
  if (code === 'exact_sample_unavailable') return '当前产品未发布可查询的精确格点值'
  if (code === 'sample_source_not_found') return '当前数值源暂不可用'
  if (code === 'invalid_sample_source') return '当前数值源校验失败'
  return '精确格点服务暂不可用'
}

function formatValue(value: number) {
  if (Math.abs(value) >= 100) return value.toFixed(0)
  if (Math.abs(value) >= 10) return value.toFixed(1)
  return value.toFixed(2)
}
