import { useCallback, useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { failure, read } from './api';
import { useAdminQuery } from './useAdminQuery';
import { timestamp, viewFromSearch, viewURL } from './model';
import type { Health, Navigate, View } from './model';
import { Notice } from './components';
import { TaskCenter, RunDetail, LegacyDetail } from './TaskCenter';
import { NewRun } from './NewRun';
import { Workers } from './Workers';
import { SystemLogs } from './LogViews';
import './admin.css';
export default function AdminApp() {
    const [token, setToken] = useState(() => sessionStorage.getItem('rainpulse.ops.adminToken') ?? '');
    const { view, navigate } = useNavigation();
    if (!token)
        return <Login onLogin={value => { sessionStorage.setItem('rainpulse.ops.adminToken', value); setToken(value); }}/>;
    return <AdminShell token={token} view={view} navigate={navigate} logout={() => { sessionStorage.removeItem('rainpulse.ops.adminToken'); setToken(''); }}/>;
}
function Login({ onLogin }: {
    onLogin: (token: string) => void;
}) {
    const [token, setToken] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false);
    const submit = async (e: FormEvent) => { e.preventDefault(); setBusy(true); setError(''); try {
        await read<Health>(token.trim(), '/status');
        onLogin(token.trim());
    }
    catch (e) {
        setError(failure(e));
    }
    finally {
        setBusy(false);
    } };
    return <main className="ops-login"><form onSubmit={e => void submit(e)}><span className="ops-brand-mark">R</span><h1>RainPulse 运行管理</h1><p>任务、日志与可靠重算</p><label>管理凭据<input autoFocus type="password" autoComplete="off" value={token} onChange={e => setToken(e.target.value)} required/></label><p className="ops-caption">使用现有管理 Token，仅保存在本标签页会话。首版使用共享管理员身份，操作记录不会伪造个人身份。</p>{error && <Notice error>{error}</Notice>}<button className="ops-primary" disabled={busy}>{busy ? '正在验证…' : '进入后台'}</button><a href="/">返回天气工作台</a></form></main>;
}
function AdminShell({ token, view, navigate, logout }: {
    token: string;
    view: View;
    navigate: Navigate;
    logout: () => void;
}) {
    const health = useAdminQuery<Health>(token, '/status', 15000);
    const title = { tasks: '任务中心', new: '新建重算', workers: '执行资源', logs: '系统日志' }[view.page];
    return <div className="ops-app"><aside className="ops-sidebar"><a className="ops-brand" href="/admin"><span className="ops-brand-mark">R</span><div>RainPulse<small>运行管理平台</small></div></a><nav aria-label="后台导航">{([['tasks', '任务中心', '01'], ['new', '新建重算', '02'], ['workers', '执行资源', '03'], ['logs', '系统日志', '04']] as const).map(([page, label, n]) => <button key={page} className={view.page === page ? 'selected' : ''} onClick={() => navigate({ page })}><span>{n}</span>{label}</button>)}</nav><div className="ops-sidebar-bottom"><p>候选计算 · 不覆盖业务结果</p><a href="/">← 天气工作台</a><button onClick={logout}>退出管理会话</button></div></aside>
 <div className="ops-main"><header className="ops-topbar"><span>运行管理 / <b>{title}</b></span><div><span className="ops-dot"/>同一数据底座<span className="ops-timezone">UTC+8</span><button className="ops-quiet" onClick={health.refresh}>更新状态</button></div></header><main className="ops-content">
 {health.error && <Notice error>管理状态读取失败：{health.error}。已显示的快照不代表服务已恢复。<button onClick={logout}>重新验证凭据</button></Notice>}
 {health.data && !health.data.database_ready && <Notice error>管理表尚未初始化。请按部署说明运行 operations/schema.sql；天气工作台不受影响。</Notice>}
 {health.data && !health.data.worker_auth_configured && <Notice error>尚未配置管理 Worker 凭据，不能领取新重算。请完成部署检查后再提交。</Notice>}
 {view.page === 'tasks' && (view.run ? <RunDetail token={token} id={view.run} taskID={view.task} navigate={navigate}/> : view.legacy ? <LegacyDetail token={token} id={view.legacy} navigate={navigate}/> : <TaskCenter token={token} navigate={navigate} health={health.data}/>)}
 {view.page === 'new' && <NewRun token={token} navigate={navigate} initialJob={view.job}/>}
 {view.page === 'workers' && <Workers token={token}/>}
 {view.page === 'logs' && <SystemLogs token={token} initialJob={view.job}/>}
 </main><footer className="ops-footer">任务状态以服务端记录为准。空值表示未记录或未采集，不表示零。{health.data && <span>状态采样：{timestamp(health.data.sampled_at)}</span>}</footer></div></div>;
}
function useNavigation() { const [view, setView] = useState(() => viewFromSearch(window.location.search)); useEffect(() => { const f = () => setView(viewFromSearch(window.location.search)); window.addEventListener('popstate', f); return () => window.removeEventListener('popstate', f); }, []); const navigate = useCallback((v: View) => { window.history.pushState({}, '', viewURL(v)); setView(v); }, []); return { view, navigate }; }
