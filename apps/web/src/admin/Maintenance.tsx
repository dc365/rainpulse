import { useState } from 'react';
import { post, read, failure } from './api';
import { useAdminQuery, useClock } from './useAdminQuery';
import { Notice, Empty, JSONView } from './components';
import { timestamp } from './model';
import type { Navigate } from './model';
import { kindLabel } from './monitoring';
import { storageBytes, reportState, releaseLabel, releaseTitle, downloadCleanupPlan, cleanupState } from './storageModel';
import type { ReleaseCatalog, Release, Channel, StorageStatus, CleanupPlan, RetentionPin } from './storageModel';

export function Maintenance({ token, navigate }: { token: string; navigate: Navigate }) {
  const releases = useAdminQuery<ReleaseCatalog>(token, '/releases', 15000);
  const storage = useAdminQuery<StorageStatus>(token, '/storage', 15000);
  const pins = useAdminQuery<{ items: RetentionPin[] }>(token, '/storage/pins', 0);
  const [tab, setTab] = useState<'versions' | 'storage'>('versions');
  const [history, setHistory] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [reason, setReason] = useState(''), [choice, setChoice] = useState<{ item: Release; channel: Channel } | null>(null);
  const [keep, setKeep] = useState(1), [age, setAge] = useState(24), [limit, setLimit] = useState(5);
  const [plan, setPlan] = useState<CleanupPlan | null>(null);
  const [pinID, setPinID] = useState(''), [pinReason, setPinReason] = useState('');
  const now = useClock();
  const refresh = () => { releases.refresh(); storage.refresh(); pins.refresh(); };
  const perform = async (action: () => Promise<void>) => { setBusy(true); setError(''); try { await action(); refresh(); } catch (e) { setError(failure(e)); } finally { setBusy(false); } };
  const all = releases.data?.items ?? [], channels = releases.data?.channels ?? [];
  const visible = history ? all : all.filter(r => r.fresh_workers > 0 || releaseLabel(r, channels) !== '历史／未选定');
  const control = storage.data?.control;
  return <>
    <div className="ops-heading"><div><h1>算法与存储</h1><p>收敛运行版本，减少重复候选；保留原始资料与当前业务结果。</p></div><button onClick={refresh}>刷新</button></div>
    <div className="ops-toolbar" role="tablist" aria-label="维护范围"><button role="tab" aria-selected={tab === 'versions'} onClick={() => setTab('versions')}>算法版本</button><button role="tab" aria-selected={tab === 'storage'} onClick={() => setTab('storage')}>存储与清理</button></div>
    {(error || releases.error || storage.error || pins.error) && <Notice error>{error || releases.error || storage.error || pins.error}</Notice>}
    {tab === 'versions' && <>
      <Notice>“当前默认”仅约束新管理重算计划，不会部署镜像、改动 YAML 或提升业务资格。已冻结的任务仍使用原身份。版本号不是排序依据，完整代码与配置摘要仍保留。</Notice>
      <section className="ops-panel"><div className="ops-toolbar"><h2>当前与上一版</h2><label><input type="checkbox" checked={history} onChange={e => setHistory(e.target.checked)} />显示历史／未选定身份</label></div>
        <div className="ops-table-wrap"><table><thead><tr><th>阶段／算法版本</th><th>用途</th><th>代码与配置身份</th><th>就绪 Worker</th><th>未完成任务</th><th>操作</th></tr></thead><tbody>{visible.map(r => {
          const channel = channels.find(c => c.kind === r.kind);
          return <tr key={r.kind + r.fingerprint}><td>{kindLabel(r.kind)}<br /><b>{releaseTitle(r)}</b></td><td>{releaseLabel(r, channels)}</td><td><code title={r.fingerprint}>{r.fingerprint.slice(0, 16)}</code><JSONView label="完整身份" value={r.identity} /></td><td>{r.fresh_workers}</td><td>{r.pending_tasks}</td><td><button disabled={busy || !channel || !r.fresh_workers || channel.current_fingerprint === r.fingerprint} onClick={() => { if (channel) { setChoice({ item: r, channel }); setReason(''); } }}>设为管理默认</button></td></tr>;
        })}</tbody></table></div>{!visible.length && <Empty>尚无当前或就绪身份。部署并登记 Worker 后可选择；不从文件名猜测“最新版”。</Empty>}
        <p className="ops-caption">历史列表最多返回最近 200 个已使用／登记身份，界面不会列出全部实验 YAML。源码和配置继续由 Git 保存。</p>
      </section>
    </>}
    {tab === 'storage' && <>
      {control?.session_id && <Notice error>清理会话 {control.session_id} 尚未结束。新管理操作已暂停；请恢复同一份清理计划，勿手动删除数据库记录。自动业务链路不受该门禁影响。</Notice>}
      <section className="ops-panel"><h2>主机文件系统</h2><p className="ops-caption">采样来自主机固定路径，不把 MinIO 对象数当作 inode 数。未配置采集不显示为正常。</p>
        <div className="ops-table-wrap"><table><thead><tr><th>主机／挂载点</th><th>inode 已用</th><th>剩余 inode</th><th>可用容量</th><th>采样</th><th>新任务保护</th></tr></thead><tbody>{storage.data?.reports.map(({ report: r }) => <tr key={r.id}><td>{r.host} · {r.label}<br /><code>{r.path}</code></td><td>{r.inode_used_percent == null ? '未知' : `${r.inode_used_percent.toFixed(1)}%`}</td><td>{r.inodes_total ? r.inodes_free.toLocaleString() : '未知'}</td><td>{storageBytes(r.available_bytes)} / {storageBytes(r.total_bytes)}</td><td>{reportState(r, now, control?.report_max_age_seconds)}<br />{timestamp(r.sampled_at)}</td><td>{control?.pressure_report === r.id ? '门禁采样源' : <button disabled={busy || !control || reportState(r, now) === '采样过期'} onClick={() => void perform(async () => { if (control) await post(token, '/storage/pressure', { report_id: r.id, inode_stop_percent: 95, minimum_free_bytes: 1024 ** 3, report_max_age_seconds: 180, expected_revision: control.revision, reason: '管理员将此真实挂载点设为管理任务存储门禁' }); })}>使用此挂载点保护新任务</button>}</td></tr>)}</tbody></table></div>
        {!storage.data?.reports.length && <Empty>尚无主机存储采样。在真实数据挂载点运行 scripts/storage_ops.py inspect 并上传；不要只采容器临时目录。</Empty>}
        <p>门禁：{control?.pressure_report || '未启用'}。启用后 inode ≥95%、可用容量＜1 GiB，或采样超过180秒未更新，会阻止新管理计算；已有心跳与结果提交不受影响。门禁仅适用于其采样所代表的管理存储。</p>
        {control?.pressure_report && <button disabled={busy} onClick={() => void perform(async () => { await post(token, '/storage/pressure', { report_id: '', inode_stop_percent: control.inode_stop_percent, minimum_free_bytes: control.minimum_free_bytes, report_max_age_seconds: control.report_max_age_seconds, expected_revision: control.revision, reason: '管理员停用可选存储门禁，继续显示采样' }); })}>停用门禁（保留采样）</button>}
      </section>
      <section className="ops-panel"><h2>重复候选保留策略</h2><Notice>只管理独立 operations 候选目录，按站点／时次／产品保留最新成功结果，不是全站只留一帧。当前默认版本、上一版的48小时缓冲、人工保留和仍被引用的资产受保护；原始、标准化和当前业务产品不清理。物理删除不可回滚。</Notice>
        <form className="ops-toolbar" onSubmit={e => { e.preventDefault(); void perform(async () => { setPlan(await post<CleanupPlan>(token, '/storage/cleanup/plans', { keep_latest: keep, minimum_age_hours: age, limit })); }); }}><label>每组成功结果至少保留<input type="number" min="1" max="5" value={keep} onChange={e => setKeep(Number(e.target.value))} /></label><label>最短缓冲（小时）<input type="number" min="24" max="8760" value={age} onChange={e => setAge(Number(e.target.value))} /></label><label>单批作业数<input type="number" min="1" max="20" value={limit} onChange={e => setLimit(Number(e.target.value))} /></label><button className="ops-primary" disabled={busy}>生成清理预览</button></form>
        <p className="ops-caption">失败／取消作业至少保留7天。一个作业中任意结果需保留，则整个作业保留，避免只删掉QC而留下依赖图件。</p>
        {plan && <><h3>本次预览：{plan.targets.length} 个作业</h3><div className="ops-table-wrap"><table><thead><tr><th>候选作业</th><th>更新时间</th><th>已登记逻辑字节</th><th>尝试目录</th><th>保护</th></tr></thead><tbody>{plan.targets.map(t => <tr key={t.run_id}><td><button onClick={() => navigate({ page: 'tasks', run: t.run_id })}>{t.name}</button></td><td>{timestamp(t.updated_at)}</td><td>{storageBytes(t.logical_bytes)}</td><td>{t.attempts.length}</td><td><button onClick={() => { setPinID(t.run_id); setPinReason('保留用于结果对照'); }}>保留此作业</button></td></tr>)}</tbody></table></div><p>有效至 {timestamp(plan.expires_at)}。字节数不是实际可回收空间或 inode 数；需先在服务器执行 S3 版本清点。</p><button disabled={!plan.targets.length} onClick={() => downloadCleanupPlan(plan)}>下载冻结清理计划</button><details><summary>服务器执行方式</summary><pre className="ops-storage-code">{`python scripts/storage_ops.py inventory --plan cleanup-${plan.id}.json --output inventory.json\n# 排空并停止管理 Worker；核对上面的清点与保护范围后，显式执行：\npython scripts/storage_ops.py purge --plan cleanup-${plan.id}.json \\\n  --confirm ${plan.digest} --workers-stopped --receipt cleanup-receipt.json`}</pre><p>删除权限只交给服务器上的限定前缀凭据，不交给 Web。S3 版本会按精确 version_id 删除，不修改 MinIO 数据目录。</p></details></>}
      </section>
      <section className="ops-panel"><h2>人工保留</h2><form className="ops-toolbar" onSubmit={e => { e.preventDefault(); void perform(async () => { await post(token, `/storage/runs/${pinID}/pin`, { pin: true, reason: pinReason }); setPinID(''); setPinReason(''); setPlan(null); }); }}><label>作业 ID<input required value={pinID} onChange={e => setPinID(e.target.value)} placeholder="从任务详情复制作业ID" /></label><label>保留原因<input required maxLength={256} value={pinReason} onChange={e => setPinReason(e.target.value)} /></label><button disabled={busy}>保留数据</button></form>{pins.data?.items.map(pin => <div className="ops-toolbar" key={pin.run_id}><button onClick={() => navigate({ page: 'tasks', run: pin.run_id })}>{pin.name}</button><span>{pin.reason}</span><button disabled={busy} onClick={() => void perform(async () => { await post(token, `/storage/runs/${pin.run_id}/pin`, { pin: false, reason: '管理员取消人工保留；仍须重新预览并显式清理' }); setPlan(null); })}>取消人工保留</button></div>)}</section>
      <section className="ops-panel"><h2>清理记录</h2><div className="ops-table-wrap"><table><thead><tr><th>计划</th><th>状态</th><th>作业数</th><th>创建时间</th><th>计划与恢复</th></tr></thead><tbody>{storage.data?.plans.map(p => <tr key={p.id}><td><code>{p.id.slice(0, 12)}</code></td><td>{cleanupState(p.state)}</td><td>{p.targets}</td><td>{timestamp(p.created_at)}</td><td><button disabled={busy} onClick={() => void perform(async () => { const value = await read<{ plan: CleanupPlan }>(token, `/storage/cleanup/plans/${p.id}`); downloadCleanupPlan(value.plan); })}>下载原计划</button></td></tr>)}</tbody></table></div><p className="ops-caption">完成回执来自服务器维护脚本的 S3 全版本复查；仍需重新采样主机 inode，不能把“已退役”当成“空间已释放”。</p></section>
    </>}
    {choice && <div className="ops-modal-backdrop"><form className="ops-modal" role="dialog" aria-modal="true" aria-label="设定管理默认算法版本" onSubmit={e => { e.preventDefault(); void perform(async () => { await post(token, `/releases/${choice.item.kind}/select`, { fingerprint: choice.item.fingerprint, expected_revision: choice.channel.revision, reason }); setChoice(null); setPlan(null); }); }}><h2>选择 {releaseTitle(choice.item)}</h2><p>只影响新的管理重算。不会热切换 Worker，不会改变已冻结任务或业务产品。上一版暂保留48小时供对照。</p><label>选择原因<textarea autoFocus required maxLength={256} value={reason} onChange={e => setReason(e.target.value)} /></label><div className="ops-actions"><button type="button" disabled={busy} onClick={() => setChoice(null)}>返回</button><button className="ops-primary" disabled={busy || !reason.trim()}>确认默认版本</button></div></form></div>}
  </>;
}
