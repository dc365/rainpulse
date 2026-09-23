import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { failure } from './api';
import { useAdminQuery } from './useAdminQuery';
import { localInput, parseSelection, timestamp } from './model';
import type { JournalPage } from './model';
import { Notice, Empty, Download } from './components';

export function Journal({ token, scope }: { token: string; scope: string }) {
  return <JournalFilter key={scope} token={token} scope={scope} />;
}
function JournalFilter({ token, scope }: { token: string; scope: string }) {
  const [level, setLevel] = useState(''), [attempt, setAttempt] = useState(''), [search, setSearch] = useState(''), [filter, setFilter] = useState(''), [error, setError] = useState('');
  const apply = (e: FormEvent) => { e.preventDefault(); if (attempt && !/^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(attempt.trim())) { setError('尝试ID须为完整UUID，可从执行详情复制'); return; } setFilter(new URLSearchParams({ level, attempt: attempt.trim(), q: search }).toString()); setError(''); };
  return <><form className="ops-journal-filter" onSubmit={apply}><label>级别<select value={level} onChange={e => setLevel(e.target.value)}><option value="">全部</option><option value="error">错误</option><option value="warning">警告</option><option value="info">信息</option><option value="debug">调试</option></select></label><label>尝试 ID<input className="ops-attempt-filter" value={attempt} onChange={e => setAttempt(e.target.value)} placeholder="可选，完整 UUID" /></label><label>日志关键字<input value={search} onChange={e => setSearch(e.target.value)} maxLength={128} placeholder="服务端搜索全部已保留记录" /></label><button>应用过滤</button></form>{error && <Notice error>{error}</Notice>}<JournalWindow key={filter} token={token} scope={scope} filter={filter} /></>;
}
function JournalWindow({ token, scope, filter }: { token: string; scope: string; filter: string }) {
  const [before, setBefore] = useState(0), [stack, setStack] = useState<number[]>([]), [follow, setFollow] = useState(true);
  const viewport = useRef<HTMLDivElement>(null);
  const path = `/${scope}/events?direction=backward&limit=200&before=${before}&${filter}`;
  const query = useAdminQuery<JournalPage>(token, path, follow && before === 0 ? 5000 : 0);
  const events = query.data?.items ?? [];
  useEffect(() => { if (follow && before === 0) viewport.current?.scrollTo({ top: viewport.current.scrollHeight }); }, [query.data, follow, before]);
  const latest = () => { setBefore(0); setStack([]); setFollow(true); query.refresh(); };
  return <div className="ops-journal"><div className="ops-toolbar"><div className="ops-actions"><button aria-pressed={follow} onClick={() => follow ? setFollow(false) : latest()}>{follow ? '暂停跟随' : '返回最新并跟随'}</button><button onClick={query.refresh}>刷新当前窗口</button><button disabled={!stack.length || query.loading} onClick={() => { setBefore(stack[stack.length - 1]); setStack(s => s.slice(0, -1)); }}>较新记录</button><button disabled={!query.data?.has_more || query.loading} onClick={() => { if (!query.data?.next_before) return; setStack(s => [...s, before]); setBefore(query.data.next_before); setFollow(false); }}>更早记录</button></div><Download value={events.map(e => `${e.at} ${e.level} ${e.event} ${e.message}`).join('\n')} name="task-log-window.txt" label="导出当前窗口" /></div>
    {query.error && <Notice error>日志读取失败：{query.error}。已显示的窗口可能过期。</Notice>}<p className="ops-caption">级别、尝试和关键字均在服务端过滤后分页；每窗最多200条。跟随显示最新窗口，查看更早记录时暂停跟随。保留上限仍为2000条／尝试；时间为服务端接收时间。</p>
    <div className="ops-log-lines" ref={viewport} tabIndex={0} aria-label="任务日志">{events.length ? events.map(e => <div key={e.id} className={`ops-log-line ${e.level}`}><time>{timestamp(e.at)}</time><b>{e.level.toUpperCase()}</b><span><small>{e.event}{e.attempt_id && ` · ${e.attempt_id.slice(0, 8)}`}</small>{e.message}</span></div>) : <Empty>{query.loading ? '读取日志窗口…' : query.error ? '日志查询未完成。' : '没有匹配的已保留日志；不代表从未发生错误。'}</Empty>}</div>
  </div>;
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
