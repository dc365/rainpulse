import { useState } from 'react';
import { failure, post } from './api';
import { useAdminQuery } from './useAdminQuery';
import { bytes, duration, metricLabel, stateLabel, taskTiming, timestamp } from './model';
import type { Health, Legacy, Page, Run, Task, Navigate } from './model';
import { Badge, Notice, Empty, JSONView, Copy, Confirm, actionNames } from './components';
import { Journal } from './LogViews';
import { Preview } from './CandidatePreview';
export function TaskCenter({ token, navigate, health }: {
    token: string;
    navigate: Navigate;
    health: Health | null;
}) {
    const [legacy, setLegacy] = useState(false), [state, setState] = useState(''), [input, setInput] = useState(''), [q, setQ] = useState(''), [cursor, setCursor] = useState('');
    const path = `/${legacy ? 'legacy' : 'runs'}?limit=50&state=${encodeURIComponent(state)}&q=${encodeURIComponent(q)}&cursor=${encodeURIComponent(cursor)}`;
    const query = useAdminQuery<Page<Run | Legacy>>(token, path);
    const switchFilter = (value: string) => { setState(value); setCursor(''); };
    return <><div className="ops-heading"><div><p className="ops-eyebrow">OPERATIONS</p><h1>任务中心</h1><p>查看执行进展，定位失败，并从正确的位置恢复。</p></div><button className="ops-primary" onClick={() => navigate({ page: 'new' })}>＋ 新建重算</button></div>
 <div className="ops-stat-row">{[['RUNNING', '执行中'], ['QUEUED', '排队中'], ['PARTIAL_SUCCESS', '部分成功'], ['FAILED', '失败']].map(([key, label]) => <button key={key} onClick={() => { setLegacy(false); switchFilter(key); }}><span>{label}</span><strong>{health ? health.counts[key] ?? 0 : '—'}</strong><small>管理作业</small></button>)}</div>
 <section className="ops-panel"><div className="ops-toolbar"><div className="ops-tabs" role="tablist"><button role="tab" aria-selected={!legacy} onClick={() => { setLegacy(false); switchFilter(''); }}>管理重算</button><button role="tab" aria-selected={legacy} onClick={() => { setLegacy(true); switchFilter(''); }}>自动链路／旧任务</button></div><button onClick={query.refresh} disabled={query.loading}>{query.loading ? '更新中…' : '刷新'}</button></div>
 <div className="ops-toolbar"><div className="ops-filter">{[['', '全部'], ...(!legacy ? [['ATTENTION', '需要处理']] : []), ['RUNNING', legacy ? '已派发／运行' : '执行中'], [legacy ? 'PENDING' : 'QUEUED', '排队中'], ['FAILED', '失败']].map(([v, label]) => <button key={v} aria-pressed={state === v} onClick={() => switchFilter(v)}>{label}</button>)}</div><form className="ops-search" onSubmit={e => { e.preventDefault(); setQ(input.trim()); setCursor(''); }}><input aria-label="搜索任务" placeholder={legacy ? '任务ID / 阶段 / 配置版本' : '作业名称 / 作业ID'} maxLength={128} value={input} onChange={e => setInput(e.target.value)}/><button>搜索</button></form></div>
 {legacy && <Notice>自动链路与旧任务只读纳管。旧 RUNNING 可能包含排队，不能用其 started_at 推断实际开始计算。区域诊断任务可从详情创建独立重算。</Notice>}
 {query.error && <Notice error>{query.error}；未将读取失败视为没有任务。</Notice>}
 <div className="ops-table-wrap"><table><thead><tr><th>名称／任务</th><th>状态</th><th>{legacy ? '配置版本' : '完成情况'}</th><th>创建时间</th><th>操作</th></tr></thead><tbody>{query.data?.items.map(item => legacy ? <tr key={item.id}><td><b>{(item as Legacy).kind}</b><code>{item.id}</code></td><td><Badge state={item.state}>{item.state === 'RUNNING' ? '已派发／执行未分离' : stateLabel(item.state)}</Badge></td><td>{(item as Legacy).config_version}</td><td>{timestamp(item.created_at)}</td><td><button className="ops-link" onClick={() => navigate({ page: 'tasks', legacy: item.id })}>查看任务 →</button></td></tr> : <tr key={item.id}><td><b>{(item as Run).name || '未命名重算'}</b><code>{item.id}</code></td><td><Badge state={item.state}/>{(item as Run).stalled && <Badge state="ATTENTION">心跳超时</Badge>}</td><td>{Object.entries((item as Run).counts ?? {}).map(([k, n]) => <span key={k} className="ops-count">{stateLabel(k)} {n}</span>)}</td><td>{timestamp(item.created_at)}<small>更新 {timestamp((item as Run).updated_at)}</small></td><td><button className="ops-link" onClick={() => navigate({ page: 'tasks', run: item.id })}>查看作业 →</button></td></tr>)}</tbody></table></div>
 {!query.data?.items.length && !query.loading && !query.error && <Empty>此条件下没有任务。可在“新建重算”中先检查实际资料。</Empty>}
 <div className="ops-pagination"><small>每页最多50条 · 服务端分页{query.data?.sampled_at && ` · ${timestamp(query.data.sampled_at)}`}</small><button disabled={!cursor} onClick={() => setCursor('')}>返回第一页</button><button disabled={!query.data?.next_cursor} onClick={() => setCursor(query.data!.next_cursor!)}>下一页</button></div></section></>;
}
export function RunDetail({ token, id, taskID, navigate }: {
    token: string;
    id: string;
    taskID?: string;
    navigate: Navigate;
}) {
    const query = useAdminQuery<Run>(token, `/runs/${id}`, 5000), [action, setAction] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState(''), [tab, setTab] = useState('tasks');
    const perform = async () => { setBusy(true); setError(''); try {
        await post(token, `/runs/${id}/action`, { action });
        setAction('');
        query.refresh();
    }
    catch (e) {
        setError(failure(e));
    }
    finally {
        setBusy(false);
    } };
    const run = query.data;
    return <><button className="ops-link" onClick={() => navigate({ page: 'tasks' })}>← 任务中心</button><div className="ops-heading"><div><h1>{run?.name || '作业详情'}</h1><p><code>{id}</code> <Copy value={id}/></p></div><div className="ops-actions"><button onClick={query.refresh}>刷新</button>{run?.actions.map(a => <button key={a} className={a === 'retry_failed' ? 'ops-primary' : ''} onClick={() => setAction(a)}>{actionNames[a]}</button>)}{run?.mode === 'ACTIVE' && Boolean(run.counts?.QUEUED) && <button onClick={() => setAction('wake')}>修复未领取引用</button>}</div></div>
 {(error || query.error) && <Notice error>{error || query.error}</Notice>}
 {run && <><div className="ops-run-summary"><Badge state={run.state}/>{run.stalled && <Badge state="ATTENTION">存在心跳超时任务，需检查日志</Badge>}<span>创建 {timestamp(run.created_at)}</span><span>操作身份：共享管理员</span><div className="ops-counts">{Object.entries(run.counts ?? {}).map(([k, n]) => <span key={k}><b>{n}</b>{stateLabel(k)}</span>)}</div></div><Notice>{run.impact}</Notice><section className="ops-panel"><div className="ops-tabs"><button aria-selected={tab === 'tasks'} onClick={() => setTab('tasks')}>执行流程</button><button aria-selected={tab === 'audit'} onClick={() => setTab('audit')}>作业日志与操作记录</button></div>{tab === 'tasks' ? <div className="ops-table-wrap"><table><thead><tr><th>任务／依赖</th><th>状态</th><th>尝试</th><th>错误或产物</th><th>操作</th></tr></thead><tbody>{run.tasks?.map(t => <tr key={t.id} className={taskID === t.id ? 'ops-selected-row' : ''}><td><b>{t.spec.parent_id ? '↳ ' : ''}{t.spec.name}</b>{t.spec.parent_id && <small>依赖：{run.tasks?.find(p => p.id === t.spec.parent_id)?.spec.name ?? t.spec.parent_id}</small>}<code>{t.id}</code></td><td><Badge state={t.state}/></td><td>{t.attempt_no === 0 ? '未领取' : `${t.attempt_no} 次`}</td><td>{t.error_message || (t.result ? '候选结果已保存，未切换默认展示' : '—')}</td><td><button className="ops-link" onClick={() => navigate({ page: 'tasks', run: id, task: t.id })}>日志／详情 →</button></td></tr>)}</tbody></table></div> : <Journal token={token} scope={`runs/${id}`}/>}</section></>}
 {taskID && <TaskDrawer key={taskID} token={token} id={taskID} onClose={() => navigate({ page: 'tasks', run: id })} onChange={query.refresh}/>}
 {action && <Confirm action={action} busy={busy} onCancel={() => setAction('')} onConfirm={() => void perform()}/>}
 </>;
}
function TaskDrawer({ token, id, onClose, onChange }: {
    token: string;
    id: string;
    onClose: () => void;
    onChange: () => void;
}) {
    const query = useAdminQuery<Task>(token, `/tasks/${id}`, 5000), [tab, setTab] = useState('logs'), [action, setAction] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
    const perform = async () => { setBusy(true); setError(''); try {
        await post(token, `/tasks/${id}/${action}`, action === 'abandon' ? { worker_stopped: true } : {});
        setAction('');
        query.refresh();
        onChange();
    }
    catch (e) {
        setError(failure(e));
    }
    finally {
        setBusy(false);
    } };
    const task = query.data, timing = task ? taskTiming(task) : null;
    return <div className="ops-drawer-backdrop"><aside className="ops-drawer" role="dialog" aria-modal="true" aria-label="任务详情"><div className="ops-drawer-head"><div><h2>{task?.spec.name ?? '任务详情'}</h2><code>{id}</code> <Copy value={id}/></div><button aria-label="关闭任务详情" onClick={onClose}>×</button></div>
 {(query.error || error) && <Notice error>{query.error || error}</Notice>}
 {task && <><div className="ops-toolbar"><Badge state={task.state}/>{task.stalled && <Badge state="ATTENTION">心跳超时，非自动判定失败</Badge>}<div className="ops-actions">{task.actions?.map(a => <button key={a} onClick={() => setAction(a)}>{actionNames[a]}</button>)}</div></div>
 {task.error_code && <Notice error><b>{task.error_code}</b>：{task.error_message}</Notice>}
 <div className="ops-tabs">{[['logs', '日志'], ['attempts', '执行与耗时'], ['inputs', '输入与版本'], ['results', '结果预览']].map(([v, label]) => <button key={v} aria-selected={tab === v} onClick={() => setTab(v)}>{label}</button>)}</div>
 {tab === 'logs' && <Journal token={token} scope={`tasks/${id}`}/>}
 {tab === 'attempts' && <div className="ops-drawer-body"><div className="ops-stat-row small"><div><span>本次排队</span><strong>{duration(timing?.queue)}</strong></div><div><span>本次已用时</span><strong>{duration(timing?.elapsed)}</strong></div><div><span>当前阶段</span><strong>{stateLabel(timing?.attempt?.stage ?? '未领取')}</strong></div></div><Notice>排队从真实入队到领取，旧记录缺少入队时间时显示未采集。心跳表示进程可响应，不是算法进度。内存是进程采样值／生命周期峰值，不宣称任务独占峰值。</Notice>{!(task.attempts?.length) && <Empty>任务尚未被 Worker 领取。</Empty>}{task.attempts?.map(a => <section key={a.id} className="ops-attempt"><h3>第 {a.number} 次尝试 <Badge state={a.state}/></h3><dl className="ops-fields"><dt>Worker</dt><dd>{a.worker_id}</dd><dt>开始</dt><dd>{timestamp(a.started_at)}</dd><dt>心跳</dt><dd>{timestamp(a.heartbeat_at)}</dd><dt>结束</dt><dd>{timestamp(a.finished_at)}</dd></dl><div className="ops-metrics">{Object.entries(a.metrics ?? {}).map(([key, value]) => <div key={key}><span title={key}>{metricLabel(key)}</span><b>{key.endsWith('_ms') ? duration(value) : key.endsWith('_bytes') ? bytes(value) : value.toLocaleString()}</b></div>)}</div><code>{a.id}</code></section>)}</div>}
 {tab === 'inputs' && <div className="ops-drawer-body"><h3>冻结的 Worker 身份</h3><code className="ops-long">{task.spec.identity.fingerprint}</code><dl className="ops-fields">{Object.entries(task.spec.identity.versions ?? {}).map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl><JSONView value={task.spec.identity.files} label="配置文件摘要"/><h3>输入资产</h3>{task.spec.parent_id && <Notice>此任务读取上游成功任务的候选输出。真实使用的 URI 固定在执行尝试请求中。</Notice>}{task.spec.inputs?.map(v => <div className="ops-asset" key={v.uri}><code className="ops-long">{v.uri}</code><p>{bytes(v.size_bytes)} · 预检范围：完成标记身份，非全体数据重读</p><JSONView value={v} label="校验身份"/></div>)}<JSONView value={timing?.attempt?.request ?? task.spec.request} label="冻结执行请求（实际尝试优先）"/></div>}
 {tab === 'results' && <div className="ops-drawer-body">{task.result ? <><Notice>候选产物已登记；没有替换当前天气工作台产品，也没有提升业务资格。</Notice><code className="ops-long">{task.result.asset.uri}</code><p>{bytes(task.result.asset.size_bytes)} · 计算至编码口径 {duration(task.result.runtime_ms)}</p><JSONView value={task.result} label="结果与来源摘要"/>{task.spec.kind !== 'qc' ? <Preview token={token} taskID={id}/> : <Notice>QC 数值资产保持原有 Zarr 契约；对照图在下游“对照图”任务中查看。</Notice>}</> : <Empty>尚无已登记产物。提交阶段中断时可先尝试“恢复结果登记”，不要直接重跑算法。</Empty>}</div>}
 </>}
 </aside>{action && <Confirm action={action} busy={busy} onCancel={() => setAction('')} onConfirm={() => void perform()}/>}</div>;
}
export function LegacyDetail({ token, id, navigate }: {
    token: string;
    id: string;
    navigate: Navigate;
}) {
    const query = useAdminQuery<Legacy>(token, `/legacy/${id}`, 10000);
    return <><button className="ops-link" onClick={() => navigate({ page: 'tasks' })}>← 任务中心</button><div className="ops-heading"><div><h1>{query.data?.kind ?? '自动链路任务'}</h1><code>{id}</code> <Copy value={id}/></div><div className="ops-actions"><button onClick={() => navigate({ page: 'logs', job: id })}>查询系统日志</button>{query.data?.kind === 'analysis.diagnostics' && <button className="ops-primary" onClick={() => navigate({ page: 'new', job: id })}>预检查并重建诊断</button>}</div></div>{query.error && <Notice error>{query.error}</Notice>}<Notice>此记录来自旧自动链路，不接受通用“重置状态”操作。RUNNING 表示旧链路已派发／运行，真实执行开始时间未单独采集。QC 重算请从数据范围新建。</Notice>{query.data && <section className="ops-panel ops-drawer-body"><Badge state={query.data.state}/><dl className="ops-fields"><dt>配置版本</dt><dd>{query.data.config_version}</dd><dt>创建时间</dt><dd>{timestamp(query.data.created_at)}</dd><dt>旧派发口径</dt><dd>{timestamp(query.data.dispatch_legacy_at)}</dd><dt>结束</dt><dd>{timestamp(query.data.finished_at)}</dd><dt>尝试记录耗时</dt><dd>{duration(query.data.runtime_ms)}</dd></dl>{query.data.error_message && <Notice error>{query.data.error_code}：{query.data.error_message}</Notice>}<JSONView value={query.data.attempts} label="原有执行尝试与错误记录"/><JSONView value={query.data.outbox} label="消息派发证据"/><JSONView value={query.data.request} label="原有请求与输入来源"/></section>}</>;
}
