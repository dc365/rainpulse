import { useState } from 'react';
import type { FormEvent } from 'react';
import { failure, post } from './api';
import { localInput, timestamp, bytes } from './model';
import type { Navigate, Page } from './model';
import { useAdminQuery } from './useAdminQuery';
import { Badge, Copy, Download, Empty, JSONView, Notice } from './components';
import { currentCheck, scanWindow, windowQuery } from './monitoring';
import type { AssetCheck, Lineage, Scan, StationSummary } from './monitoring';

export function DataInventory({ token, navigate, scanID }: { token: string; navigate: Navigate; scanID?: string }) {
  const [start, setStart] = useState(() => localInput(new Date(Date.now() - 6 * 3600000)));
  const [end, setEnd] = useState(() => localInput(new Date()));
  const [radar, setRadar] = useState(''), [stage, setStage] = useState('all'), [filter, setFilter] = useState(''), [cursor, setCursor] = useState('');
  const [error, setError] = useState('');
  const data = useAdminQuery<Page<Scan>>(token, filter ? `/data/scans?${filter}&limit=50&cursor=${encodeURIComponent(cursor)}` : null, 0);
  const summary = useAdminQuery<Page<StationSummary>>(token, filter ? `/data/summary?${filter}` : null, 0);
  const submit = (e: FormEvent) => { e.preventDefault(); try { const times = windowQuery(start, end, 24); if (radar.trim() && !/^[a-zA-Z0-9_.-]{1,96}$/.test(radar.trim())) throw new Error('请输入有效站号'); setFilter(new URLSearchParams({ ...times, radar: radar.trim(), stage }).toString()); setCursor(''); setError(''); data.refresh(); summary.refresh(); } catch (e) { setError(failure(e)); } };
  return <><div className="ops-heading"><div><h1>数据台账</h1><p>先查实际体扫，再看阶段资产与来源。没有预报产品的资料也可管理。</p></div></div>
    <form className="ops-panel ops-form" onSubmit={submit}><div className="ops-form-row"><label>开始（UTC+8，含）<input type="datetime-local" value={start} onChange={e => setStart(e.target.value)} required /></label><label>结束（UTC+8，不含）<input type="datetime-local" value={end} onChange={e => setEnd(e.target.value)} required /></label><label>站号<input value={radar} onChange={e => setRadar(e.target.value)} placeholder="留空查询全部站点" /></label><label>阶段<select value={stage} onChange={e => setStage(e.target.value)}><option value="all">全部</option><option value="normalized_missing">无标准化登记</option><option value="qc_missing">无 QC 登记</option><option value="grid_missing">无格点登记</option><option value="failed">流程失败</option></select></label><button className="ops-primary" disabled={data.loading}>查询</button></div></form>
    <Notice>一次查询不超过24小时。表中“有登记”不等于对象仍存在或全部数据通过校验；站点采集周期未冻结时，不推算缺报数量。</Notice>
    {(error || data.error || summary.error) && <Notice error>{error || data.error || summary.error}</Notice>}
    {summary.data && <section className="ops-panel"><div className="ops-toolbar"><h2>范围内站点概况</h2><small>不受阶段过滤影响 · {timestamp(summary.data.sampled_at)}</small></div><div className="ops-table-wrap"><table><thead><tr><th>站点</th><th>已登记体扫</th><th>标准化</th><th>QC</th><th>格点</th><th>流程失败</th><th>最新观测</th></tr></thead><tbody>{summary.data.items.map(s => <tr key={s.radar_id}><td>{s.radar_id.toUpperCase()}</td><td>{s.registered}</td><td>{s.normalized}</td><td>{s.qc}</td><td>{s.grid}</td><td>{s.failed}</td><td>{s.registered ? timestamp(s.latest_observation) : '该范围没有登记'}</td></tr>)}</tbody></table></div></section>}
    <section className="ops-panel"><div className="ops-toolbar"><h2>体扫与处理阶段</h2>{data.data && <Download value={data.data.items} name="scan-catalog-page.json" label="导出当前页" />}</div><div className="ops-table-wrap"><table><thead><tr><th>站点／观测</th><th>流程状态</th><th>标准化</th><th>QC</th><th>格点</th><th>操作</th></tr></thead><tbody>{data.data?.items.map(s => <tr key={s.id}><td><b>{s.radar_id.toUpperCase()}</b><small>{timestamp(s.observed_at)}</small></td><td>{s.state}</td><td>{s.normalized_uri ? '有登记' : '无登记'}</td><td>{s.qc_uri ? '有登记' : '无登记'}</td><td>{s.grid_uri ? '有登记' : '无登记'}</td><td><button className="ops-link" onClick={() => navigate({ page: 'data', scan: s.id })}>来源／检查／重算 →</button></td></tr>)}</tbody></table></div>{!data.data?.items.length && !data.loading && <Empty>{data.error ? '目录查询未完成，不能判断此范围是否有资料。' : filter ? '此范围或过滤条件下没有体扫记录。' : '选择时间范围后查询，不会自动扫描整个对象存储。'}</Empty>}<div className="ops-pagination"><span>最多50条／页 · {data.data && timestamp(data.data.sampled_at)}</span><button disabled={!cursor} onClick={() => setCursor('')}>第一页</button><button disabled={!data.data?.next_cursor} onClick={() => setCursor(data.data!.next_cursor!)}>下一页</button></div></section>
    {scanID && <ScanDetail key={scanID} token={token} id={scanID} navigate={navigate} />}
  </>;
}
function ScanDetail({ token, id, navigate }: { token: string; id: string; navigate: Navigate }) {
  const query = useAdminQuery<Lineage>(token, `/data/scans/${id}`, 0), [busy, setBusy] = useState(''), [error, setError] = useState('');
  const [receipt, setReceipt] = useState<AssetCheck | null>(null);
  const probe = async (stage: string) => { setBusy(stage); setError(''); try { setReceipt(await post<AssetCheck>(token, `/data/scans/${id}/probe`, { stage })); query.refresh(); } catch (e) { setError(failure(e)); } finally { setBusy(''); } };
  const d = query.data;
  return <div className="ops-drawer-backdrop"><aside className="ops-drawer" role="dialog" aria-modal="true" aria-label="数据来源详情"><div className="ops-drawer-head"><div><h2>{d?.scan.radar_id.toUpperCase() ?? '体扫'} · 来源详情</h2><code>{id}</code><Copy value={id} /></div><button aria-label="关闭数据详情" onClick={() => navigate({ page: 'data' })}>×</button></div><div className="ops-drawer-body">
    {(error || query.error) && <Notice error>{error || query.error}</Notice>}{d && <><p>观测 {timestamp(d.scan.observed_at)} · 接收 {timestamp(d.scan.received_at)}</p><p>流程：{d.scan.state} · 站点配置：{d.scan.config_version ?? '未登记'}</p>
      <div className="ops-actions"><button onClick={query.refresh}>刷新来源</button><button disabled={!d.scan.normalized_uri} className="ops-primary" onClick={() => navigate({ page: 'new', radar: d.scan.radar_id, preset: 'qc_preview', ...scanWindow(d.scan) })}>预检查 QC＋对照图</button><button disabled={!d.scan.qc_uri} onClick={() => navigate({ page: 'new', radar: d.scan.radar_id, preset: 'render_only', ...scanWindow(d.scan) })}>仅重建对照图</button></div><p className="ops-caption">跳转携带此体扫所在的一分钟范围；需在预检查计划中确认实际任务，跳转本身不会提交计算。</p>
      <h3>当前阶段资产</h3>{(['normalized', 'qc', 'grid'] as const).map(stage => { const uri = d.scan[`${stage}_uri`], check = currentCheck(d.checks, stage, uri); return <section className="ops-asset" key={stage}><div className="ops-toolbar"><b>{stage === 'normalized' ? '标准化体扫' : stage === 'qc' ? 'QC 体扫' : '单站格点'}</b><button disabled={!uri || !!busy} onClick={() => void probe(stage)}>{busy === stage ? '检查中…' : '检查完成标记'}</button></div><code className="ops-long">{uri || '没有资产登记'}</code>{check ? <><p><Badge state={check.state === 'marker_checked' ? 'PASS' : 'WARN'}>{check.state === 'marker_checked' ? '标记已核验' : '尚未验证'}</Badge> {timestamp(check.checked_at)}</p><p>{check.message}</p>{check.asset && <p>{bytes(check.asset.size_bytes)}（清单声明）<code className="ops-long">{check.asset.sha256}</code></p>}</> : <p className="ops-caption">{uri ? '此资产引用尚无检查记录；旧引用的检查结果不会沿用。' : '不会猜测缺失对象的路径。'}</p>}</section>; })}
      {receipt && <Notice>{receipt.message}</Notice>}
      <h3>自动链路任务</h3>{d.automatic_tasks.length ? d.automatic_tasks.map(t => <p key={t.id}><button className="ops-link" onClick={() => navigate({ page: 'tasks', legacy: t.id })}>{t.kind} · {t.state} →</button> <small>{t.config_version}</small></p>) : <Empty>没有关联的自动任务。</Empty>}
      <h3>候选重算</h3>{d.candidate_tasks.length ? d.candidate_tasks.map(t => <p key={t.id}><button className="ops-link" onClick={() => navigate({ page: 'tasks', run: t.run_id, task: t.id })}>{t.name || t.kind} · {t.state} →</button></p>) : <Empty>没有关联的候选任务。</Empty>}
      <h3>下游分析引用</h3><Notice>以下为数据库记录的体扫关联，不代表下游使用了最近的候选 QC；候选结果没有自动替换业务输入。</Notice>{d.downstream_analyses.map(a => <section className="ops-asset" key={a.id}><b>{timestamp(a.analysis_time)} · {a.grid_id}</b><p>{a.state} · 贡献状态 {a.contribution_state}</p><code className="ops-long">{a.id}</code><JSONView value={a} label="分析版本与资产路径" /></section>)}{!d.downstream_analyses.length && <Empty>尚无下游分析引用。</Empty>}{Object.values(d.truncated).some(Boolean) && <Notice>关联列表各取最新100条，部分已截断；未将当前列表当成完整删除影响分析。</Notice>}<Download value={d} name="scan-lineage.json" label="导出当前来源记录" />
    </>}</div></aside></div>;
}
