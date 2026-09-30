import type { Navigate, SystemTab } from './model';
import { Workers } from './Workers';
import { Performance } from './Performance';
import { SystemLogs } from './LogViews';
import { Maintenance } from './Maintenance';
import { Basemaps } from './Basemaps';

const tabs: readonly [SystemTab, string][] = [['workers', '执行资源'], ['performance', '性能分析'], ['logs', '系统日志'], ['storage', '算法与存储'], ['basemap', '底图配置']];

export function System({ token, navigate, tab, initialJob }: {
    token: string;
    navigate: Navigate;
    tab?: SystemTab;
    initialJob?: string;
}) {
    const current = tab ?? (initialJob ? 'logs' : 'workers');
    return <>
    <div className="ops-tabs duty-system-tabs" role="tablist" aria-label="系统运维">
        {tabs.map(([value, label]) => <button key={value} role="tab" aria-selected={current === value} onClick={() => navigate({ page: 'system', tab: value })}>{label}</button>)}
    </div>
    {current === 'workers' && <Workers token={token}/>}
    {current === 'performance' && <Performance token={token} navigate={navigate}/>}
    {current === 'logs' && <SystemLogs token={token} initialJob={initialJob}/>}
    {current === 'storage' && <Maintenance token={token} navigate={navigate}/>}
    {current === 'basemap' && <Basemaps token={token}/>}
    </>;
}
