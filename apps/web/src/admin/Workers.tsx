import { useAdminQuery, useClock } from './useAdminQuery';
import { workerState, timestamp } from './model';
import type { Page, Worker } from './model';
import { Badge, Notice, Empty, JSONView } from './components';
export function Workers({ token }: {
    token: string;
}) {
    const query = useAdminQuery<Page<Worker>>(token, '/workers', 10000);
    const now = useClock();
    return <><div className="ops-heading"><div><h1>执行资源</h1><p>只展示已登记的管理 Worker；未登记的自动链路、主机和 GPU 不会伪装成已监控。</p></div><button onClick={query.refresh}>刷新</button></div>{query.error && <Notice error>{query.error}</Notice>}<Notice>身份包括运行代码和实际挂载配置摘要。副本、CPU／内存限额由部署清单控制；首版不在网页提供任意 Docker 或主机命令。</Notice><div className="ops-worker-grid">{query.data?.items.map(w => <section className="ops-panel ops-worker" key={w.id}><div className="ops-toolbar"><h2>{w.identity.kind === 'qc' ? '单站QC' : w.identity.kind === 'render' ? '对照图' : '区域诊断'}</h2><Badge state={workerState(w, now) === '可接单' ? 'PASS' : workerState(w, now) === '正在执行' ? 'RUNNING' : 'WARN'}>{workerState(w, now)}</Badge></div><code>{w.id}</code><p>最后心跳：{timestamp(w.seen_at)}</p>{w.current_task && <p>当前任务 <code>{w.current_task}</code></p>}<dl className="ops-fields">{Object.entries(w.identity.versions).map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl><JSONView value={w.identity} label="展开完整代码／配置身份"/></section>)}</div>{!query.data?.items.length && !query.error && <Empty>没有已登记的管理 Worker。请按部署说明启动相应计算池，再生成重算计划。</Empty>}</>;
}
