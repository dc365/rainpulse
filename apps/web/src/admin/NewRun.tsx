import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { read, post, failure } from './api';
import { useAdminQuery, useClock } from './useAdminQuery';
import { localInput, parseSelection, timestamp, stateLabel } from './model';
import type { Inventory, Page, Plan, Navigate } from './model';
import { Badge, Notice, Empty, Download, Copy } from './components';
export function NewRun({ token, navigate, initialJob, initial }: {
    token: string;
    navigate: Navigate;
    initialJob?: string;
    initial?: { radar?: string; start?: string; end?: string; preset?: string };
}) {
    const [preset, setPreset] = useState(initialJob ? 'diagnostics' : initial?.preset ?? 'qc_preview'), [start, setStart] = useState(() => localInput(new Date(initial?.start ?? Date.now() - 30 * 60000))), [end, setEnd] = useState(() => localInput(new Date(initial?.end ?? Date.now()))), [radars, setRadars] = useState(initial?.radar ?? ''), [jobs, setJobs] = useState(initialJob ?? ''), [name, setName] = useState(''), [plan, setPlan] = useState<Plan | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState(''), [inventoryPath, setInventoryPath] = useState<string | null>(null);
    const [productID, setProductID] = useState('');
    const multiband = preset === 'x_qc' || preset === 'sx_composite';
    const inventory = useAdminQuery<Page<Inventory>>(token, inventoryPath, 0);
    const now = useClock();
    const planRevision = useRef(0);
    useEffect(() => { const id = sessionStorage.getItem('rainpulse.ops.pendingPlan'); if (!id || initialJob || initial?.radar)
        return; const c = new AbortController(); void read<Plan>(token, `/plans/${id}`, c.signal).then(p => { if (!c.signal.aborted && planRevision.current === 0) {
        setPlan(p);
        setPreset(p.selection.preset);
        setProductID(p.selection.product_id ?? '');
        setName(p.selection.name);
        setRadars(p.selection.radar_ids?.join(',') ?? '');
        setJobs(p.selection.source_job_ids?.join(',') ?? '');
        if (p.selection.preset !== 'diagnostics') {
            setStart(localInput(new Date(p.selection.start)));
            setEnd(localInput(new Date(p.selection.end)));
        }
    } }).catch(() => { }); return () => c.abort(); }, [token, initialJob, initial?.radar]);
    const clearPlan = () => { planRevision.current++; setPlan(null); sessionStorage.removeItem('rainpulse.ops.pendingPlan'); };
    const check = async (e: FormEvent) => { e.preventDefault(); setBusy(true); setError(''); clearPlan(); try {
        const selection = parseSelection(preset, start, end, radars, jobs, name, productID.trim());
        const requestedRevision = planRevision.current;
        const p = await post<Plan>(token, '/plans', selection);
        if (requestedRevision !== planRevision.current) {
            setError('检查期间选择范围发生变化，请重新生成计划。');
            return;
        }
        setPlan(p);
        sessionStorage.setItem('rainpulse.ops.pendingPlan', p.id);
    }
    catch (e) {
        setError(failure(e));
    }
    finally {
        setBusy(false);
    } };
    const submit = async () => { if (!plan)
        return; setBusy(true); setError(''); try {
        const result = await post<{
            run_id: string;
        }>(token, `/plans/${plan.id}/submit`, { idempotency_key: `ops-plan-${plan.id}` });
        sessionStorage.removeItem('rainpulse.ops.pendingPlan');
        navigate({ page: 'tasks', run: result.run_id });
    }
    catch (e) {
        setError(failure(e) + '；提交响应丢失时可按原计划再次提交，服务端会返回同一作业。');
    }
    finally {
        setBusy(false);
    } };
    const listInputs = () => { setError(''); try {
        const s = parseSelection('qc_preview', start, end, radars, jobs, name);
        const q = new URLSearchParams({ start: s.start, end: s.end, radar: s.radar_ids.length === 1 ? s.radar_ids[0] : '', limit: '100' });
        setInventoryPath(`/inventory?${q}`);
    }
    catch (e) {
        setError(failure(e));
    } };
    return <><div className="ops-heading"><div><p className="ops-eyebrow">PLAN BEFORE EXECUTION</p><h1>新建重算</h1><p>先检查资料、配置和 Worker，再提交不可变的执行计划。</p></div></div><Notice>支持既有 QC／图件重算，以及 X 单站质控、S/X 一分钟组合反射率候选。多波段需要单独启用网络配置与 Worker；不修改现有 QPE、预报和默认展示。</Notice><div className="ops-plan-layout"><form className="ops-panel ops-form" onSubmit={e => void check(e)}><h2>1. 选择范围</h2><label>重算预设<select value={preset} disabled={busy} onChange={e => { setPreset(e.target.value); clearPlan(); }}><option value="qc_preview">单站 QC 与原始／质控对照图</option><option value="render_only">仅重建已有 QC 的对照图</option><option value="diagnostics">重建已有区域诊断任务</option><option value="x_qc">X 波段单站 QC 与诊断</option><option value="sx_composite">S/X 一分钟融合组合反射率</option></select></label>{multiband && <label>产品 ID<input value={productID} onChange={e => { setProductID(e.target.value); clearPlan(); }} placeholder="网络配置只有一个产品时可留空" maxLength={96}/><small>站点、网格、高度层和衰减策略由已冻结的网络发布文件决定。</small></label>}<label>作业名称<input placeholder="例如：近站杂波案例复核" maxLength={120} value={name} onChange={e => { setName(e.target.value); clearPlan(); }}/></label>{preset === 'diagnostics' ? <label>源区域诊断任务 ID<textarea rows={4} placeholder="从自动链路任务详情复制；最多32个，以逗号分隔" value={jobs} onChange={e => { setJobs(e.target.value); clearPlan(); }}/></label> : <><label>雷达站号<input placeholder="例如 z9591,z9598" value={radars} onChange={e => { setRadars(e.target.value); clearPlan(); }} required/></label><div className="ops-form-row"><label>开始时间（UTC+8，含）<input type="datetime-local" value={start} onChange={e => { setStart(e.target.value); clearPlan(); }} required/></label><label>结束时间（UTC+8，不含）<input type="datetime-local" value={end} onChange={e => { setEnd(e.target.value); clearPlan(); }} required/></label></div><p className="ops-caption">{multiband ? '多波段每次不超过1小时、64个任务；S 使用已有QC，X 使用已解码极坐标资料。这里创建限定时段任务，不是自动实时跟随开关。' : '单次不超过24小时、64个体扫。目录读取不依赖成功的预报产品；大范围请分段执行。'}</p><button type="button" disabled={busy} onClick={listInputs}>查看此范围数据台账</button></>}{error && <Notice error>{error}</Notice>}<button className="ops-primary" disabled={busy}>{busy ? '检查／提交处理中…' : '2. 生成预检查计划'}</button></form><section className="ops-panel ops-plan"><h2>2. 预检查与影响</h2>{plan ? <><p><code>{plan.id}</code><Copy value={plan.id}/></p><Notice>{plan.impact}</Notice><p className="ops-caption">计划中的选择：{plan.selection.name || '未命名'} · {plan.selection.preset} · {plan.tasks.length} 个阶段任务。表单变化后必须重新检查。</p><ul className="ops-checklist">{plan.checks.map((c, i) => <li key={`${c.code}-${i}`}><Badge state={c.state}/><div>{c.message}{c.target && <code>{c.target}</code>}</div></li>)}</ul><details><summary>将要执行的任务与依赖</summary>{plan.tasks.map(t => <p key={t.id}>{t.parent_id ? '↳ ' : ''}{t.name}</p>)}</details><p>计划有效至 {timestamp(plan.expires_at)} {Date.parse(plan.expires_at) < now && <Badge state="BLOCK">已过期，请重新检查</Badge>}</p><Download value={plan} name="rerun-plan.json" label="导出冻结计划"/><button className="ops-primary" disabled={busy || !plan.submittable || Date.parse(plan.expires_at) < now} onClick={() => void submit()}>3. 确认并提交此计划</button><button disabled={busy} onClick={clearPlan}>丢弃此计划</button></> : <Empty>选择范围后生成计划。缺资料、没有匹配 Worker 或配置漂移会明确阻止提交，不会悄悄跳过。</Empty>}</section></div>
 {inventoryPath && <section className="ops-panel"><div className="ops-toolbar"><h2>数据台账</h2><small>仅目录记录，实际对象检查以预检查为准；多站查询展示范围内全部站点。</small></div>{inventory.error && <Notice error>{inventory.error}</Notice>}<div className="ops-table-wrap"><table><thead><tr><th>站点／时次</th><th>链路状态</th><th>标准化</th><th>QC</th><th>格点</th></tr></thead><tbody>{inventory.data?.items.map(s => <tr key={s.id}><td>{s.radar_id.toUpperCase()} · {timestamp(s.observed_at)}<code>{s.id}</code></td><td>{stateLabel(s.state)}</td><td>{s.normalized_uri ? '有登记' : '缺少'}</td><td>{s.qc_uri ? '有登记' : '缺少'}</td><td>{s.grid_uri ? '有登记' : '缺少'}</td></tr>)}</tbody></table></div>{!inventory.data?.items.length && !inventory.loading && !inventory.error && <Empty>此范围没有已登记体扫。</Empty>}<div className="ops-pagination"><span>每页最多100个体扫</span><button disabled={!inventory.data?.next_cursor} onClick={() => { const u = new URL('http://local' + inventoryPath); u.searchParams.set('cursor', inventory.data!.next_cursor!); setInventoryPath(u.pathname + u.search); }}>下一页</button></div></section>}</>;
}
