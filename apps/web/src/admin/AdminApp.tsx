import { useCallback, useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { failure, read } from './api';
import { useAdminQuery } from './useAdminQuery';
import { timestamp, viewFromSearch, viewURL } from './model';
import type { Health, Navigate, View } from './model';
import { Notice } from './components';
import { TaskCenter, RunDetail, LegacyDetail } from './TaskCenter';
import { NewRun } from './NewRun';
import { DataInventory } from './DataInventory';
import { Overview } from './Overview';
import { System } from './System';
import './admin.css';
export default function AdminApp() {
    const [token, setToken] = useState(() => sessionStorage.getItem('rainpulse.ops.adminToken') ?? '');
    const [authMode, setAuthMode] = useState<'checking' | 'credential' | 'validation'>('checking');
    const { view, navigate } = useNavigation();
    useEffect(() => {
        let active = true;
        void fetch('/api/v1/admin/ops/auth-mode', { cache: 'no-store' }).then(async response => {
            if (!response.ok)
                throw new Error('无法读取管理认证模式');
            const value = await response.json() as { mode?: string };
            if (!active)
                return;
            if (value.mode === 'validation') {
                sessionStorage.removeItem('rainpulse.ops.adminToken');
                setToken('');
                setAuthMode('validation');
                return;
            }
            setAuthMode('credential');
        }).catch(() => {
            if (active)
                setAuthMode('credential');
        });
        return () => { active = false; };
    }, []);
    if (authMode === 'checking')
        return <main className="ops-login"><p>正在读取后台认证模式…</p></main>;
    if (authMode === 'credential' && !token)
        return <Login onLogin={value => { sessionStorage.setItem('rainpulse.ops.adminToken', value); setToken(value); }}/>;
    return <AdminShell token={token} validationMode={authMode === 'validation'} view={view} navigate={navigate} logout={() => { sessionStorage.removeItem('rainpulse.ops.adminToken'); setToken(''); }}/>;
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
    return <main className="ops-login"><form onSubmit={e => void submit(e)}><span className="ops-brand-mark" aria-hidden="true">R</span><h1>RainPulse 值班台</h1><p>看状态 · 找问题 · 重算</p><label>管理凭据<input autoFocus type="password" autoComplete="off" value={token} onChange={e => setToken(e.target.value)} required/></label><p className="ops-caption">使用现有管理 Token，仅保存在本标签页会话；操作记录为共享管理员身份。</p>{error && <Notice error>{error}</Notice>}<button className="ops-primary" disabled={busy}>{busy ? '正在验证…' : '进入后台'}</button><a href="/">返回天气工作台</a></form></main>;
}
function AdminShell({ token, validationMode, view, navigate, logout }: {
    token: string;
    validationMode: boolean;
    view: View;
    navigate: Navigate;
    logout: () => void;
}) {
    const health = useAdminQuery<Health>(token, '/status', 15000);
    const attention = (health.data?.counts.FAILED ?? 0) + (health.data?.counts.PARTIAL_SUCCESS ?? 0);
    const title = { overview: '总览', tasks: view.run || view.legacy ? '任务详情' : '任务中心', new: '发起重算', data: '数据台账', system: '系统运维' }[view.page];
    return <div className="ops-app"><aside className="ops-sidebar">
        <a className="ops-brand" href="/admin"><span className="ops-brand-mark" aria-hidden="true">R</span><div>RainPulse<small>值班台</small></div></a>
        <nav aria-label="后台导航">{([['overview', '总览'], ['tasks', '任务'], ['new', '重算'], ['data', '数据'], ['system', '系统']] as const).map(([page, label]) => <button key={page} className={view.page === page ? 'selected' : ''} onClick={() => navigate({ page })}>{label}{page === 'tasks' && attention > 0 && <span className="ops-nav-badge" title={`${attention} 个作业需要处理`}>{attention}</span>}</button>)}</nav>
        <div className="ops-sidebar-bottom"><p>候选计算 · 不覆盖业务结果</p><a href="/">← 天气工作台</a>{validationMode ? <span>免凭据验证模式</span> : <button onClick={logout}>退出管理会话</button>}</div>
    </aside>
    <div className="ops-main"><header className="ops-topbar"><span className="ops-crumb">运行管理 / <b>{title}</b></span><div><span className="ops-dot" aria-hidden="true"/>{health.data ? `采样 ${timestamp(health.data.sampled_at)}` : '未采样'}<span className="ops-timezone">UTC+8</span><button className="ops-quiet" onClick={health.refresh}>更新状态</button></div></header>
    <main className="ops-content">
        {validationMode && <Notice><strong>验证模式：</strong>管理接口免凭据，仅限验证环境使用。</Notice>}
        {health.error && <Notice error>管理状态读取失败：{health.error}。已显示的快照不代表服务已恢复。{!validationMode && <button onClick={logout}>重新验证凭据</button>}</Notice>}
        {view.page === 'overview' && <Overview token={token} navigate={navigate} health={health.data} healthError={health.error}/>}
        {view.page === 'tasks' && (view.run ? <RunDetail token={token} id={view.run} taskID={view.task} navigate={navigate}/> : view.legacy ? <LegacyDetail token={token} id={view.legacy} navigate={navigate}/> : <TaskCenter token={token} navigate={navigate} health={health.data}/>)}
        {view.page === 'new' && <NewRun key={[view.job, view.radar, view.start, view.end, view.preset].join(':')} token={token} navigate={navigate} initialJob={view.job} initial={view}/>}
        {view.page === 'data' && <DataInventory token={token} navigate={navigate} scanID={view.scan}/>}
        {view.page === 'system' && <System token={token} navigate={navigate} tab={view.tab} initialJob={view.job}/>}
    </main><footer className="ops-footer">状态以服务端记录为准 · 空值表示未记录，不表示零</footer></div></div>;
}
function useNavigation() { const [view, setView] = useState(() => viewFromSearch(window.location.search)); useEffect(() => { const f = () => setView(viewFromSearch(window.location.search)); window.addEventListener('popstate', f); return () => window.removeEventListener('popstate', f); }, []); const navigate = useCallback((v: View) => { window.history.pushState({}, '', viewURL(v)); setView(v); }, []); return { view, navigate }; }
