import { useState } from 'react';
import type { FormEvent } from 'react';
import { failure } from './api';
import { bytes, duration, localInput, metricLabel, timestamp } from './model';
import type { Navigate } from './model';
import { useAdminQuery } from './useAdminQuery';
import { Download, Empty, Notice } from './components';
import { kindLabel, windowQuery } from './monitoring';
import type { PerformanceReport } from './monitoring';

export function Performance({ token, navigate }: { token: string; navigate: Navigate }) {
  const [start, setStart] = useState(() => localInput(new Date(Date.now() - 24 * 3600000))), [end, setEnd] = useState(() => localInput(new Date()));
  const [kind, setKind] = useState(''), [fingerprint, setFingerprint] = useState(''), [path, setPath] = useState<string | null>(null), [error, setError] = useState('');
  const query = useAdminQuery<PerformanceReport>(token, path, 0);
  const submit = (e: FormEvent) => { e.preventDefault(); try { const window = windowQuery(start, end, 168); if (fingerprint && !/^[a-f0-9]{64}$/.test(fingerprint)) throw new Error('执行身份应为64位完整摘要'); setPath('/performance?' + new URLSearchParams({ ...window, kind, fingerprint })); query.refresh(); setError(''); } catch (e) { setError(failure(e)); } };
  return <><div className="ops-heading"><div><h1>性能分析</h1><p>按真实执行尝试统计，定位排队、算法执行与产物发布的开销。</p></div>{query.data && <Download value={query.data} name="management-performance.json" label="导出统计口径与样本" />}</div>
    <form className="ops-panel ops-form" onSubmit={submit}><div className="ops-form-row"><label>尝试开始（UTC+8，含）<input type="datetime-local" required value={start} onChange={e => setStart(e.target.value)} /></label><label>结束（不含）<input type="datetime-local" required value={end} onChange={e => setEnd(e.target.value)} /></label><label>阶段<select value={kind} onChange={e => setKind(e.target.value)}><option value="">全部</option><option value="qc">单站 QC</option><option value="render">对照图</option><option value="diagnostics">区域诊断</option></select></label><label>执行身份<input value={fingerprint} onChange={e => setFingerprint(e.target.value.trim())} placeholder="可选，完整 SHA256" maxLength={64} /></label><button className="ops-primary" disabled={query.loading}>查询</button></div></form>
    <Notice>范围：管理执行池，按尝试开始时间筛选。成功尝试才参与耗时分位数，失败、取消和未完成数量单列；旧自动链路不混入。最多7天、10000次尝试，超限会拒绝并要求缩小范围，不偷偷抽样。</Notice>
    {(error || query.error) && <Notice error>{error || query.error}</Notice>}
    {query.data && <><p className="ops-caption">共 {query.data.attempts} 次尝试 · 采样 {timestamp(query.data.sampled_at)} · 排队从真实入队时间到领取；未记录时间不会反推。分位数使用 nearest-rank。</p>
      <section className="ops-panel"><div className="ops-toolbar"><h2>阶段与版本对比</h2></div><div className="ops-table-wrap"><table><thead><tr><th>阶段／执行身份</th><th>尝试与结果</th><th>排队 P95</th><th>原生执行 P95</th><th>产物发布 P95</th><th>领取至登记 P95</th></tr></thead><tbody>{query.data.groups.map(g => <tr key={g.kind + g.fingerprint}><td><b>{kindLabel(g.kind)}</b><code title={g.fingerprint}>{g.fingerprint.slice(0, 16)}…</code><small>{g.workers.length} 个 Worker</small></td><td>{g.attempts} 次<small>成功 {g.states.SUCCEEDED ?? 0} · 失败 {g.states.FAILED ?? 0} · 受阻 {g.states.BLOCKED ?? 0}</small><small>其他 {g.attempts - (g.states.SUCCEEDED ?? 0) - (g.states.FAILED ?? 0) - (g.states.BLOCKED ?? 0)}</small></td>{['queue_ms', 'compute_wall_ms', 'publication_wall_ms', 'attempt_elapsed_ms'].map(k => <td key={k}>{duration(g.metrics[k]?.p95)}<small>有效样本 {g.metrics[k]?.samples ?? 0}</small></td>)}</tr>)}</tbody></table></div>{!query.data.groups.length && <Empty>此范围没有管理执行尝试；不表示系统计算耗时为零。</Empty>}</section>
      {query.data.groups.map(g => <details key={g.kind + g.fingerprint} className="ops-panel ops-performance-detail"><summary>{kindLabel(g.kind)} · {g.fingerprint.slice(0, 16)}… · 展开阶段统计</summary><p className="ops-caption">同执行身份不保证输入规模、缓存冷热、节点或配额相同；此页不自动给出“新版本更快”的结论。原生总耗时包含内部阶段，不能与它们相加。</p><code className="ops-long">{g.fingerprint}</code><div className="ops-table-wrap"><table><thead><tr><th>指标</th><th>样本数</th><th>P50</th><th>P95</th><th>最大</th></tr></thead><tbody>{Object.entries(g.metrics).map(([key, d]) => { const format = key.endsWith('_bytes') ? bytes : duration; return <tr key={key}><td>{metricLabel(key)}</td><td>{d.samples}{d.samples > 0 && d.samples < 20 && <small>样本较少</small>}</td><td>{format(d.p50)}</td><td>{format(d.p95)}</td><td>{format(d.maximum)}</td></tr>; })}</tbody></table></div></details>)}
      <section className="ops-panel"><div className="ops-toolbar"><h2>耗时较长的成功尝试</h2><small>按领取至结果登记排序，最多20条</small></div><div className="ops-table-wrap"><table><thead><tr><th>阶段／开始时间</th><th>Worker</th><th>总耗时</th><th>输入规模</th><th>操作</th></tr></thead><tbody>{query.data.slow.map(a => <tr key={a.id}><td>{kindLabel(a.kind)}<small>{timestamp(a.started_at)}</small></td><td><code>{a.worker_id}</code></td><td>{duration(a.metrics.attempt_elapsed_ms)}</td><td>{bytes(a.metrics.input_bytes)}</td><td><button className="ops-link" onClick={() => navigate({ page: 'tasks', run: a.run_id, task: a.task_id })}>查看尝试与日志 →</button></td></tr>)}</tbody></table></div></section>
    </>}
    <Notice>内存仅展示执行窗口的进程采样峰值，不是任务独占峰值或整机监控；未采集显示“未采集”。恢复登记的尝试不伪造核心计算指标。</Notice>
  </>;
}
