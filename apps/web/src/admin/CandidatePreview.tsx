import { useEffect, useState } from 'react'
import { asset, failure } from './api'
import { Notice } from './components'

type Layer = { object_path: string; title: string; field?: string; sweep_number?: number }
export function Preview(props: { token: string; taskID: string }) {
  return <TaskPreview key={props.taskID} {...props} />
}
function TaskPreview({ token, taskID }: { token: string; taskID: string }) {
  const [layers, setLayers] = useState<Layer[]>([])
  const [key, setKey] = useState('')
  const [picture, setPicture] = useState<{key: string; url: string} | null>(null)
  const [problem, setProblem] = useState<{key: string; message: string} | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    void asset(token, taskID, 'manifest.json', controller.signal).then(b => b.text()).then(text => {
      if (controller.signal.aborted) return
      const value = JSON.parse(text) as {layers?: Layer[]}
      if (!Array.isArray(value.layers) || value.layers.some(l => typeof l.object_path !== 'string' || typeof l.title !== 'string')) {
        throw new Error('图件清单缺少有效图层')
      }
      setLayers(value.layers)
      setKey(value.layers[0]?.object_path ?? '')
    }).catch(e => { if (!controller.signal.aborted) setProblem({key:'manifest', message:failure(e)}) })
    return () => controller.abort()
  }, [token, taskID])
  useEffect(() => {
    if (!key) return
    const controller = new AbortController()
    let objectURL = ''
    void asset(token, taskID, key, controller.signal).then(blob => {
      if (controller.signal.aborted) return
      objectURL = URL.createObjectURL(blob)
      setPicture({key, url:objectURL})
      setProblem(null)
    }).catch(e => { if (!controller.signal.aborted) setProblem({key, message:failure(e)}) })
    return () => { controller.abort(); if (objectURL) URL.revokeObjectURL(objectURL) }
  }, [token, taskID, key])
  const message = problem?.key === key || problem?.key === 'manifest' ? problem.message : ''
  return <section className="ops-preview"><h3>已校验图件预览</h3>
    {message && <Notice error>{message}</Notice>}
    {layers.length > 0 && <select aria-label="选择图层" value={key} onChange={e => setKey(e.target.value)}>
      {layers.map(l => <option key={l.object_path} value={l.object_path}>{l.title} {l.sweep_number != null ? `· 仰角层 ${l.sweep_number}` : ''}</option>)}
    </select>}
    {picture?.key === key && <img src={picture.url} alt={layers.find(l => l.object_path === key)?.title ?? '候选对照图'} />}
    {key && picture?.key !== key && !message && <p role="status">正在读取并核对所选图层…</p>}
  </section>
}
