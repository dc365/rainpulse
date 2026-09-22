import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { read, failure } from './api';
import { useAdminQuery } from './useAdminQuery';
import { localInput, mergeEvents, parseSelection, timestamp } from './model';
import type { JournalEvent, JournalPage } from './model';
import { Notice, Empty, Download } from './components';
export function Journal(props: { token: string; scope: string }) {
    return <ScopedJournal key={props.scope} {...props} />;
}
function ScopedJournal({ token, scope }: {
    token: string;
    scope: string;
}) {
    const [events, setEvents] = useState<JournalEvent[]>([]), [error, setError] = useState(''), [follow, setFollow] = useState(true), [level, setLevel] = useState(''), [attempt, setAttempt] = useState(''), [more, setMore] = useState(false), [revision, setRevision] = useState(0);
    const cursor = useRef(0), viewport = useRef<HTMLDivElement>(null);
    useEffect(() => { let stopped = false; let timer: ReturnType<typeof setTimeout> | undefined; const controller = new AbortController(); const load = async () => { try {
        const page = await read<JournalPage>(token, `/${scope}/events?after=${cursor.current}&limit=200`, controller.signal);
        if (stopped)
            return;
        cursor.current = page.next_after;
        setEvents(old => mergeEvents(old, page.items));
        setMore(page.has_more);
        setError('');
    }
    catch (e) {
        if (!stopped)
            setError(failure(e));
    }
    finally {
        if (!stopped && follow)
            timer = setTimeout(() => { if (document.visibilityState === 'visible')
                void load(); }, 5000);
    } }; const visible = () => { if (follow && document.visibilityState === 'visible')
        setRevision(n => n + 1); }; document.addEventListener('visibilitychange', visible); void load(); return () => { stopped = true; controller.abort(); clearTimeout(timer); document.removeEventListener('visibilitychange', visible); }; }, [token, scope, follow, revision]);
    useEffect(() => { if (follow)
        viewport.current?.scrollTo({ top: viewport.current.scrollHeight }); }, [events, follow]);
    const shown = events.filter(e => (!level || e.level === level) && (!attempt || e.attempt_id === attempt));
    const attempts = [...new Set(events.map(e => e.attempt_id).filter((v): v is string => !!v))];
    return <div className="ops-journal"><div className="ops-toolbar"><div className="ops-actions"><button aria-pressed={follow} onClick={() => setFollow(!follow)}>{follow ? '暂停跟随' : '恢复跟随'}</button><button onClick={() => setRevision(n => n + 1)}>{more ? '继续读取后续记录' : '刷新'}</button><select aria-label="日志级别" value={level} onChange={e => setLevel(e.target.value)}><option value="">全部级别</option><option value="error">错误</option><option value="warning">警告</option><option value="info">信息</option><option value="debug">调试</option></select><select aria-label="执行尝试" value={attempt} onChange={e => setAttempt(e.target.value)}><option value="">全部尝试</option>{attempts.map(a => <option key={a} value={a}>{a.slice(0, 8)}</option>)}</select></div><Download value={shown.map(e => `${e.at} ${e.level} ${e.event} ${e.message}`).join('\n')} name="task-log-visible.txt" label="导出已加载记录"/></div>{error && <Notice error>日志读取失败：{error}。已有记录保留。</Notice>}<p className="ops-caption">服务端按尝试去重保存，最多2000条／尝试；本页保留最近2000条已加载记录，过滤与导出仅作用于这些记录。时间为接收时间。</p><div className="ops-log-lines" ref={viewport} tabIndex={0} aria-label="任务日志">{shown.length ? shown.map(e => <div key={e.id} className={`ops-log-line ${e.level}`}><time>{timestamp(e.at)}</time><b>{e.level.toUpperCase()}</b><span><small>{e.event}{e.attempt_id && ` · ${e.attempt_id.slice(0, 8)}`}</small>{e.message}</span></div>) : <Empty>{error ? '未能读取日志' : '尚无符合条件的已接收日志；未接入前的历史日志不会自动补齐。'}</Empty>}</div></div>;
}
export function SystemLogs({ token, initialJob }: {
    token: string;
    initialJob?: string;
}) {
    const [job, setJob] = useState(initialJob ?? ''), [start, setStart] = useState(() => localInput(new Date(Date.now() - 3600000))), [end, setEnd] = useState(() => localInput(new Date())), [path, setPath] = useState<string | null>(null), [error, setError] = useState('');
    const query = useAdminQuery<{
        status: string;
        message: string;
        items: {
            timestamp_ns: string;
            message: string;
        }[];
        possibly_truncated?: boolean;
    }>(token, path, 0);
    const submit = (e: FormEvent) => { e.preventDefault(); setError(''); try {
        const selection = parseSelection('diagnostics', start, end, '', job, '');
        if (Date.parse(selection.end) - Date.parse(selection.start) > 6 * 3600000 || Date.parse(selection.end) <= Date.parse(selection.start))
            throw new Error('日志查询请选择最多6小时');
        setPath('/logs?' + new URLSearchParams({ job_id: job, start: selection.start, end: selection.end }));
    }
    catch (e) {
        setError(failure(e));
    } };
    return <><div className="ops-heading"><div><h1>系统日志</h1><p>查询已接入 Loki 的自动链路／系统日志。管理重算的尝试日志在任务详情内，无须 Loki。</p></div></div><form className="ops-panel ops-form" onSubmit={submit}><div className="ops-form-row"><label>任务 ID<input required value={job} onChange={e => setJob(e.target.value)} placeholder="完整 UUID"/></label><label>开始（UTC+8）<input type="datetime-local" value={start} onChange={e => setStart(e.target.value)} required/></label><label>结束（UTC+8）<input type="datetime-local" value={end} onChange={e => setEnd(e.target.value)} required/></label><button className="ops-primary" disabled={query.loading}>查询</button></div></form>{(error || query.error) && <Notice error>{error || query.error}</Notice>}{query.data && <section className="ops-panel"><Notice>{query.data.message || '本次查询已返回'}{query.data.possibly_truncated && '；结果可能截断，请缩小时间范围。'}</Notice>{query.data.status === 'available' && <><div className="ops-toolbar"><span>最多200条，本次查询范围内</span><Download value={query.data.items} name="system-log-window.json" label="导出本次查询"/></div><pre className="ops-system-log">{query.data.items.map(v => `${v.timestamp_ns} ${v.message}`).join('\n') || '此时间窗口未查询到匹配日志；不代表历史上没有发生错误。'}</pre></>}</section>}</>;
}
