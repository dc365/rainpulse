import { useAdminQuery, useClock } from './useAdminQuery';
import { stateLabel, timestamp, workerState } from './model';
import type { Health, Navigate, Page, Run } from './model';
import { Badge, Empty, Hint, Notice } from './components';
import { kindLabel, poolState } from './monitoring';
import type { Pool } from './monitoring';

const stageOrder = ['qc', 'render', 'diagnostics', 'multiband'];

export function Overview({ token, navigate, health, healthError }: {
    token: string;
    navigate: Navigate;
    health: Health | null;
    healthError: string;
}) {
    const runs = useAdminQuery<Page<Run>>(token, '/runs?limit=50', 10000);
    const pools = useAdminQuery<Page<Pool>>(token, '/pools', 10000);
    const now = useClock();
    const counts = health?.counts ?? {};
    const failed = counts.FAILED ?? 0, partial = counts.PARTIAL_SUCCESS ?? 0, running = counts.RUNNING ?? 0, queued = counts.QUEUED ?? 0;
    const attention = failed + partial;
    const items = runs.data?.items ?? [];
    const attentionRuns = items.filter(r => r.state === 'FAILED' || r.state === 'PARTIAL_SUCCESS' || r.stalled);
    const activeRuns = items.filter(r => r.state === 'RUNNING' || r.state === 'QUEUED');
    const workers = health?.workers ?? [];
    const abnormal = workers.filter(w => !['可接单', '正在执行', '执行池暂停接单'].includes(workerState(w, now)));
    const verdict = healthError ? { tone: 'error' as const, title: '状态读取失败', detail: '显示的是最近一次成功采样，不代表服务当前正常。' }
        : attention > 0 ? { tone: 'warn' as const, title: `${attention} 个作业需要处理`, detail: `失败 ${failed} · 部分成功 ${partial}${running > 0 ? ` · 另有 ${running} 个执行中` : ''}` }
        : running > 0 ? { tone: 'ok' as const, title: '链路正常运行', detail: `${running} 个作业执行中 · ${queued} 个排队中` }
        : queued > 0 ? { tone: 'ok' as const, title: '链路正常运行', detail: `${queued} 个作业排队等待执行` }
        : { tone: 'ok' as const, title: '值班正常', detail: '当前没有进行中的管理作业' };
    const stages = stageOrder.map(kind => pools.data?.items.find(p => p.kind === kind) ?? null);
    return <>
    <section className={`duty-verdict ${verdict.tone}`} role="status">
        <div className="duty-verdict-main">
            <span className="duty-dot" aria-hidden="true"/>
            <div>
                <h1>{verdict.title}</h1>
                <p>{verdict.detail}{health && <span> · 采样 {timestamp(health.sampled_at)}</span>}</p>
            </div>
        </div>
        <div className="duty-verdict-quick">
            <button className="ops-primary" onClick={() => navigate({ page: 'new' })}>发起重算</button>
            <button onClick={() => navigate({ page: 'tasks' })}>查看任务</button>
            <button onClick={() => navigate({ page: 'data' })}>查数据</button>
        </div>
    </section>
    {health && !health.database_ready && <Notice error>管理表尚未初始化或未升级，后台功能不可用；天气工作台不受影响。</Notice>}
    {health && !health.worker_auth_configured && <Notice error>尚未配置管理 Worker 凭据，新重算提交后不会被领取。</Notice>}
    <section className="ops-panel duty-stage-panel">
        <div className="ops-toolbar"><h2>链路节拍</h2><small>各阶段执行池 · 六分钟一次的作业节拍</small></div>
        {pools.error && <Notice error>执行池状态读取失败，稍后自动重试。</Notice>}
        {pools.data && <>
        <div className="stage-rail" role="list">
            {stages.map((pool, i) => <div className={`stage ${pool ? '' : 'absent'}`} role="listitem" key={i}>
                <span className="stage-node" aria-hidden="true"/>
                <div className="stage-head"><b>{kindLabel(pool?.kind ?? stageOrder[i])}</b>{pool && <Badge state={pool.mode === 'DRAINING' ? 'WARN' : pool.ready ? 'PASS' : 'WARN'}>{poolState(pool)}</Badge>}</div>
                {pool ? <div className="stage-counts">
                    <span><b>{pool.active}</b>执行</span>
                    <span><b>{pool.queued}</b>排队</span>
                    <span><b>{pool.ready}</b>就绪</span>
                    {pool.stalled > 0 && <span className="stage-warn"><b>{pool.stalled}</b>租约过期</span>}
                </div> : <div className="stage-counts"><span className="stage-warn">未登记</span></div>}
            </div>)}
        </div>
        {abnormal.length > 0 && <div className="duty-worker-note"><span>{abnormal.length} 个 Worker 心跳异常</span>{abnormal.slice(0, 3).map(w => <code key={w.id} title={w.id}>{w.id.slice(0, 8)} · {workerState(w, now)}</code>)}<button className="ops-link" onClick={() => navigate({ page: 'system', tab: 'workers' })}>查看执行资源 →</button></div>}
        </>}
        {!pools.data && !pools.error && <Empty>正在读取执行池状态…</Empty>}
    </section>
    <div className="duty-grid">
        <section className="ops-panel">
            <div className="ops-toolbar"><h2>需要处理</h2><small>失败、部分成功或心跳超时的作业</small></div>
            {runs.error && <Notice error>{runs.error}；未把读取失败当作没有作业。</Notice>}
            {attentionRuns.length > 0 ? <div className="ops-table-wrap"><table><thead><tr><th>作业</th><th>状态</th><th>更新</th><th></th></tr></thead><tbody>{attentionRuns.map(r => <tr key={r.id}><td><b>{r.name || '未命名重算'}</b><code>{r.id}</code></td><td><Badge state={r.state}/>{r.stalled && <Badge state="ATTENTION">心跳超时</Badge>}</td><td>{timestamp(r.updated_at)}</td><td><button className="ops-link" onClick={() => navigate({ page: 'tasks', run: r.id })}>处理 →</button></td></tr>)}</tbody></table></div>
            : !runs.error && !runs.loading && <Empty>{attention === 0 ? '没有需要处理的作业。' : '需要处理的作业不在最近 50 条内，请到任务中心按状态筛选。'}</Empty>}
        </section>
        <section className="ops-panel">
            <div className="ops-toolbar"><h2>执行与排队</h2><small>最近 50 条管理作业</small></div>
            {activeRuns.length > 0 ? <div className="ops-table-wrap"><table><thead><tr><th>作业</th><th>进度</th><th>更新</th><th></th></tr></thead><tbody>{activeRuns.map(r => <tr key={r.id}><td><b>{r.name || '未命名重算'}</b></td><td><Badge state={r.state}/><span className="ops-counts-inline">{Object.entries(r.counts ?? {}).map(([k, n]) => <span key={k}>{n} {stateLabel(k)}</span>)}</span></td><td>{timestamp(r.updated_at)}</td><td><button className="ops-link" onClick={() => navigate({ page: 'tasks', run: r.id })}>查看 →</button></td></tr>)}</tbody></table></div>
            : !runs.error && <Empty>当前没有执行中或排队的作业。</Empty>}
        </section>
    </div>
    <Hint title="这个页面看什么"><p>总览回答三个问题：链路是否正常、有没有需要处理的失败、现在在跑什么。所有状态来自服务端采样（{health ? timestamp(health.sampled_at) : '未采样'}），后台重算产生候选结果，不覆盖天气工作台的业务产品；空值表示未记录，不表示零。</p></Hint>
    </>;
}
