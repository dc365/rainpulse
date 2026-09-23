import { useState } from 'react';
import type { FormEvent } from 'react';
import { failure, post } from './api';
import { useAdminQuery, useClock } from './useAdminQuery';
import { workerState, timestamp } from './model';
import type { Page, Worker } from './model';
import { Badge, Notice, Empty, JSONView } from './components';
import { kindLabel, poolState } from './monitoring';
import type { Pool, PoolEvent } from './monitoring';

export function Workers({ token }: { token: string }) {
  const query = useAdminQuery<Page<Worker>>(token, '/workers', 10000), pools = useAdminQuery<Page<Pool>>(token, '/pools', 10000);
  const [before, setBefore] = useState(0);
  const events = useAdminQuery<{ items: PoolEvent[]; next_before: number }>(token, `/pools/events?before=${before}`, 10000);
  const [selected, setSelected] = useState<Pool | null>(null), [reason, setReason] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const now = useClock();
  const refresh = () => { query.refresh(); pools.refresh(); events.refresh(); };
  const act = async (e: FormEvent) => { e.preventDefault(); if (!selected) return; setBusy(true); setError(''); try { await post(token, `/pools/${selected.kind}/action`, { action: selected.mode === 'ACCEPTING' ? 'drain' : 'resume', expected_revision: selected.revision, reason }); setSelected(null); setReason(''); setBefore(0); refresh(); } catch (e) { setError(failure(e)); refresh(); } finally { setBusy(false); } };
  return <><div className="ops-heading"><div><h1>执行资源</h1><p>管理执行池的接单、排空与版本。状态来自数据库和 Worker 心跳。</p></div><button onClick={refresh}>刷新</button></div>
    {(error || query.error || pools.error || events.error) && <Notice error>{error || query.error || pools.error || events.error}</Notice>}
    <Notice>排空仅阻止此类管理 Worker 领取新任务，保留当前计算、心跳和结果登记；不影响实时自动链路。策略按阶段持久化，新注册的同类 Worker 也遵守。这里不会执行任意 Docker／主机命令。</Notice>
    <div className="ops-worker-grid">{pools.data?.items.map(p => <section className="ops-panel ops-worker" key={p.kind}><div className="ops-toolbar"><h2>{kindLabel(p.kind)}池</h2><Badge state={p.mode === 'DRAINING' ? 'WARN' : p.ready ? 'PASS' : 'WARN'}>{poolState(p)}</Badge></div><dl className="ops-fields"><dt>执行／提交</dt><dd>{p.active}{p.stalled > 0 && `（${p.stalled} 个租约已过期，未当作已退出）`}</dd><dt>活动作业排队</dt><dd>{p.queued}</dd><dt>新鲜且配置就绪</dt><dd>{p.ready}／{p.fresh}</dd><dt>最早已知入队</dt><dd>{timestamp(p.oldest_queued_at)}</dd></dl><p className="ops-caption">策略版本 {p.revision} · 更新 {timestamp(p.updated_at)}{p.reason && ` · ${p.reason}`}</p><button className={p.mode === 'ACCEPTING' ? '' : 'ops-primary'} onClick={() => { setSelected(p); setReason(''); setError(''); }}>{p.mode === 'ACCEPTING' ? '暂停接单并排空' : '恢复接单'}</button></section>)}</div>
    <h2>已登记的管理 Worker</h2><div className="ops-worker-grid">{query.data?.items.map(w => <section className="ops-panel ops-worker" key={w.id}><div className="ops-toolbar"><h3>{kindLabel(w.identity.kind)}</h3><Badge state={workerState(w, now) === '可接单' ? 'PASS' : workerState(w, now) === '正在执行' ? 'RUNNING' : 'WARN'}>{workerState(w, now)}</Badge></div><code>{w.id}</code><p>最后心跳：{timestamp(w.seen_at)}</p>{w.current_task && <p>当前任务 <code>{w.current_task}</code></p>}<dl className="ops-fields">{Object.entries(w.identity.versions).map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl><JSONView value={w.identity} label="展开代码／配置身份" /></section>)}</div>
    {!query.data?.items.length && !query.error && <Empty>没有已登记的管理 Worker。请先完成部署配置。</Empty>}
    <section className="ops-panel"><div className="ops-toolbar"><h2>资源操作记录</h2><small>共享管理员身份，不伪造个人身份</small></div><div className="ops-table-wrap"><table><thead><tr><th>时间</th><th>执行池</th><th>操作</th><th>版本</th><th>原因</th></tr></thead><tbody>{events.data?.items.map(e => <tr key={e.id}><td>{timestamp(e.at)}</td><td>{kindLabel(e.kind)}</td><td>{e.action === 'drain' ? '暂停接单／排空' : '恢复接单'}</td><td>{e.revision}</td><td>{e.reason}</td></tr>)}</tbody></table></div><div className="ops-pagination"><button disabled={!before} onClick={() => setBefore(0)}>最新记录</button><button disabled={!events.data?.next_before} onClick={() => setBefore(events.data!.next_before)}>更早记录</button></div></section>
    {selected && <div className="ops-modal-backdrop"><form onSubmit={e => void act(e)} className="ops-modal" role="dialog" aria-modal="true" aria-label="修改执行池接单策略"><h2>{kindLabel(selected.kind)} · {selected.mode === 'ACCEPTING' ? '暂停接单并排空' : '恢复接单'}</h2><p>{selected.mode === 'ACCEPTING' ? '新领取会被阻止；运行中的计算继续。存在失联任务时不能把排空状态当成进程已退出。' : '恢复该阶段的新任务领取，冻结版本和输入校验仍然执行。'}</p><label>操作原因<textarea autoFocus required maxLength={256} value={reason} onChange={e => setReason(e.target.value)} /></label>{error && <Notice error>{error}</Notice>}<div className="ops-actions"><button type="button" disabled={busy} onClick={() => setSelected(null)}>返回</button><button className="ops-primary" disabled={busy || !reason.trim()}>{busy ? '正在提交…' : '确认'}</button></div></form></div>}
  </>;
}
