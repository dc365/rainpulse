import { useEffect, useState } from 'react'
import { AdminError, failure, post, read } from './api'
import { Empty } from './components'

type BasemapSource = {
    key: string
    label: string
    url: string
    overlay?: string
}
type BasemapConfig = {
    default: string
    sources: BasemapSource[]
}

const emptyRow: BasemapSource = { key: '', label: '', url: 'http://', overlay: '' }

export function Basemaps({ token }: { token: string }) {
    const [state, setState] = useState<{ loading: boolean; error?: string; config?: BasemapConfig }>({ loading: true })
    const [draft, setDraft] = useState<BasemapConfig>()
    const [saving, setSaving] = useState(false)
    const [note, setNote] = useState('')

    useEffect(() => {
        const controller = new AbortController()
        void read<BasemapConfig>(token, '/basemap-config', controller.signal)
            .then(config => { setState({ loading: false }); setDraft(config) })
            .catch(error => { if (!controller.signal.aborted) setState({ loading: false, error: failure(error) }) })
        return () => controller.abort()
    }, [token])

    const run = async (action: () => Promise<BasemapConfig>, message: string) => {
        setSaving(true); setNote('')
        try {
            const config = await action()
            setDraft(config)
            setNote(message)
        } catch (error) {
            setNote(error instanceof AdminError ? error.message : '保存失败，请重试')
        } finally {
            setSaving(false)
        }
    }

    if (state.loading) return <div className="ops-heading"><h1>底图配置</h1><p>正在读取底图配置…</p></div>
    if (state.error) return <div className="ops-heading"><h1>底图配置</h1><p role="alert">{state.error}</p></div>
    if (!draft) return <Empty>暂无配置</Empty>

    const update = (index: number, patch: Partial<BasemapSource>) => {
        setDraft({ ...draft, sources: draft.sources.map((row, i) => i === index ? { ...row, ...patch } : row) })
    }
    const save = () => void run(() => post<BasemapConfig>(token, '/basemap-config', draft), '已保存；前台地图刷新后生效')
    const reset = () => void run(() => post<BasemapConfig>(token, '/basemap-config/reset', {}), '已恢复默认（内网行政/地形/卫星 + 天地图）')

    return <>
    <div className="ops-heading"><div><h1>底图配置</h1><p>瓦片统一经本服务代理，浏览器只访问本站；地址模板需含 &#123;z&#125;、&#123;x&#125;、&#123;y&#125; 占位符，天地图 token 由服务端以 &#123;token&#125; 占位符填写。保存后前台新会话生效。</p></div><button onClick={reset} disabled={saving}>恢复默认</button></div>
    <section className="ops-panel" aria-label="底图配置">
        <div className="ops-toolbar"><h2>底图瓦片源</h2><small>代理路径 <code>/api/v1/workspace/basemap-tiles/&#123;源&#125;/&#123;z&#125;/&#123;x&#125;/&#123;y&#125;.png</code></small></div>
        <div className="ops-table-wrap"><table>
            <thead><tr><th>默认</th><th>Key</th><th>名称</th><th>瓦片地址</th><th>注记地址（可选）</th><th></th></tr></thead>
            <tbody>
                {draft.sources.map((row, index) => <tr key={index}>
                    <td><input type="radio" name="basemap-default" aria-label={`默认 ${row.key || index}`} checked={draft.default === row.key} onChange={() => setDraft({ ...draft, default: row.key })} /></td>
                    <td><input aria-label="Key" value={row.key} onChange={e => update(index, { key: e.target.value.trim() })} /></td>
                    <td><input aria-label="名称" value={row.label} onChange={e => update(index, { label: e.target.value })} /></td>
                    <td><input aria-label="瓦片地址" value={row.url} onChange={e => update(index, { url: e.target.value.trim() })} /></td>
                    <td><input aria-label="注记地址" value={row.overlay ?? ''} onChange={e => update(index, { overlay: e.target.value.trim() })} /></td>
                    <td><button onClick={() => setDraft({ ...draft, sources: draft.sources.filter((_, i) => i !== index) })} disabled={draft.sources.length <= 1}>删除</button></td>
                </tr>)}
            </tbody>
        </table></div>
        <div className="ops-toolbar">
            <button onClick={() => setDraft({ ...draft, sources: [...draft.sources, { ...emptyRow }] })}>添加源</button>
            <button className="ops-primary" onClick={save} disabled={saving}>{saving ? '正在保存…' : '保存'}</button>
            {note && <span role="status">{note}</span>}
        </div>
    </section>
    </>
}
