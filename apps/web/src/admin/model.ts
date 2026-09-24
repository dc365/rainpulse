export type Identity = {
    kind: string;
    fingerprint: string;
    files: Record<string, string>;
    versions: Record<string, string>;
    code_sha256: string;
};
export type Asset = {
    uri: string;
    sha256: string;
    marker_sha256: string;
    size_bytes: number;
    verification: string;
};
export type Candidate = {
    asset: Asset;
    candidate_only: boolean;
    started_at: string;
    finished_at: string;
    runtime_ms: number;
    summary?: unknown;
};
export type Spec = {
    id: string;
    parent_id?: string;
    kind: string;
    name: string;
    source_job_id?: string;
    inputs: Asset[] | null;
    identity: Identity;
    request: unknown;
};
export type Attempt = {
    id: string;
    number: number;
    state: string;
    stage: string;
    worker_id: string;
    started_at: string;
    queued_at?: string | null;
    heartbeat_at: string;
    lease_until: string;
    finished_at: string | null;
    metrics: Record<string, number>;
    request: unknown;
    result?: Candidate | null;
};
export type Task = {
    storage_state?: "AVAILABLE" | "RETIRED" | "DELETED";
    id: string;
    run_id: string;
    spec: Spec;
    state: string;
    attempt_no: number;
    current_attempt?: string;
    error_code: string;
    error_message: string;
    created_at: string;
    dispatched_at: string | null;
    queued_at?: string | null;
    updated_at: string;
    attempts?: Attempt[];
    result?: Candidate | null;
    actions?: string[];
    stalled: boolean;
};
export type Run = {
    storage_state?: "AVAILABLE" | "RETIRED" | "DELETED";
    id: string;
    plan_id: string;
    name: string;
    mode: string;
    state: string;
    actor: string;
    impact: string;
    counts: Record<string, number> | null;
    created_at: string;
    updated_at: string;
    tasks?: Task[];
    actions: string[];
    stalled: boolean;
};
export type Worker = {
    id: string;
    identity: Identity;
    seen_at: string;
    ready: boolean;
    busy: boolean;
    current_task?: string;
    pool_mode?: string;
};
export type Health = {
    database_ready: boolean;
    worker_auth_configured: boolean;
    system_logs: string;
    workers: Worker[];
    counts: Record<string, number>;
    sampled_at: string;
    mode: string;
};
export type Selection = {
    preset: string;
    product_id?: string;
    start: string;
    end: string;
    radar_ids: string[];
    source_job_ids?: string[];
    name: string;
};
export type Plan = {
    id: string;
    run_id: string;
    selection: Selection;
    checks: {
        code: string;
        state: string;
        message: string;
        target?: string;
    }[];
    tasks: Spec[];
    digest: string;
    submittable: boolean;
    created_at: string;
    expires_at: string;
    impact: string;
};
export type Page<T> = {
    items: T[];
    next_cursor?: string;
    sampled_at?: string;
};
export type Legacy = {
    id: string;
    run_id: string;
    trace_id: string;
    kind: string;
    state: string;
    config_version: string;
    created_at: string;
    dispatch_legacy_at: string | null;
    finished_at: string | null;
    runtime_ms: number | null;
    error_code: string | null;
    error_message: string | null;
    request?: unknown;
    attempts?: unknown[];
    outbox?: unknown[];
};
export type Inventory = {
    id: string;
    radar_id: string;
    observed_at: string;
    state: string;
    normalized_uri: string | null;
    qc_uri: string | null;
    grid_uri: string | null;
    config_version: string;
};
export type JournalEvent = {
    id: number;
    at: string;
    task_id?: string;
    attempt_id?: string;
    level: string;
    event: string;
    message: string;
};
export type JournalPage = {
    items: JournalEvent[];
    next_after: number;
    next_before?: number;
    direction?: string;
    has_more: boolean;
};
export type View = {
    page: 'tasks' | 'new' | 'workers' | 'logs' | 'data' | 'performance' | 'storage';
    scan?: string;
    radar?: string;
    start?: string;
    end?: string;
    preset?: string;
    run?: string;
    legacy?: string;
    task?: string;
    job?: string;
};
const names: Record<string, string> = { QUEUED: '排队中', WAITING: '等待上游', RUNNING: '执行中', COMMITTING: '提交产物', SUCCEEDED: '已完成', FAILED: '失败', BLOCKED: '受阻', PARTIAL_SUCCESS: '部分成功', PAUSED: '已暂停接单', CANCELLING: '取消中', CANCELLED: '已取消', SUPERSEDED: '执行权已撤销', PENDING: '待派发', SKIPPED: '已跳过', ATTENTION: '需要处理', VERIFY_INPUT: '核验输入', COMPUTE: '原生阶段执行', UPLOAD: '上传产物', COMMIT: '登记结果', RECOVER: '恢复登记', PASS: '通过', WARN: '提醒', BLOCK: '阻止提交' };
export function stateLabel(state: string) { return names[state] ?? state; }
export function tone(state: string) { return ['FAILED', 'BLOCK', 'BLOCKED'].includes(state) ? 'danger' : ['PARTIAL_SUCCESS', 'WARN', 'ATTENTION', 'CANCELLING', 'SUPERSEDED'].includes(state) ? 'warn' : ['SUCCEEDED', 'PASS'].includes(state) ? 'success' : ['RUNNING', 'COMMITTING', 'COMPUTE', 'UPLOAD'].includes(state) ? 'active' : 'muted'; }
export function timestamp(value?: string | null) { if (!value || !Number.isFinite(Date.parse(value)))
    return '未记录'; return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date(value)); }
export function duration(value?: number | null) { if (value == null || !Number.isFinite(value) || value < 0)
    return '未采集'; if (value < 1000)
    return `${Math.round(value)} ms`; if (value < 60000)
    return `${(value / 1000).toFixed(1)} s`; return `${(value / 60000).toFixed(1)} min`; }
export function bytes(value?: number | null) { if (value == null || !Number.isFinite(value) || value < 0)
    return '未采集'; if (value < 1024)
    return `${value} B`; if (value < 1024 ** 2)
    return `${(value / 1024).toFixed(1)} KiB`; return `${(value / 1024 ** 2).toFixed(1)} MiB`; }
export function toUTC(value: string) { if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value))
    throw new Error('时间格式不正确'); const result = new Date(`${value}:00+08:00`); if (!Number.isFinite(result.getTime()) || localInput(result) !== value)
    throw new Error('时间不可解析'); return result.toISOString(); }
export function localInput(date: Date) { return new Date(date.getTime() + 8 * 3600000).toISOString().slice(0, 16); }
export function parseSelection(preset: string, start: string, end: string, radars: string, jobs: string, name: string, productID = ''): Selection { if (!['qc_preview','render_only','diagnostics','x_qc','sx_composite'].includes(preset)) throw new Error('未知重算预设'); const multi = preset === 'x_qc' || preset === 'sx_composite'; if (multi && productID && !/^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95}$/.test(productID)) throw new Error('产品ID不正确'); const s: Selection = { preset, start: toUTC(start), end: toUTC(end), radar_ids: radars.split(/[\s,，]+/).filter(Boolean).map(x => x.toLowerCase()), source_job_ids: jobs.split(/[\s,，]+/).filter(Boolean), name: name.trim(), ...(multi && productID ? { product_id: productID } : {}) }; if (preset !== 'diagnostics') {
    const span = Date.parse(s.end) - Date.parse(s.start);
    if (multi && span > 3600000) throw new Error('多波段首版每次不超过1小时');
    if (span <= 0 || span > 86400000)
        throw new Error('请选择不超过24小时的时间范围');
    if (!s.radar_ids.length || s.radar_ids.length > 16 || new Set(s.radar_ids).size !== s.radar_ids.length || s.radar_ids.some(x => !/^[a-z0-9_.-]{1,96}$/.test(x)))
        throw new Error('请输入1–16个不重复的有效站号');
}
else if (!s.source_job_ids?.length || s.source_job_ids.length > 32 || new Set(s.source_job_ids).size !== s.source_job_ids.length || s.source_job_ids.some(x => !/^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(x)))
    throw new Error('请输入1–32个有效诊断任务ID'); return s; }
export function viewFromSearch(search: string): View { const q = new URLSearchParams(search); const page = q.get('view'); return { page: page === 'new' || page === 'workers' || page === 'logs' || page === 'data' || page === 'performance' || page === 'storage' ? page : 'tasks', run: validViewID(q.get('run')), legacy: validViewID(q.get('legacy')), task: validViewID(q.get('task')), job: validViewID(q.get('job')), scan: validViewID(q.get('scan')), radar: /^[a-zA-Z0-9_.-]{1,96}$/.test(q.get('radar') ?? '') ? q.get('radar')! : undefined, start: validViewTime(q.get('start')), end: validViewTime(q.get('end')), preset: ['qc_preview','render_only','x_qc','sx_composite'].includes(q.get('preset') ?? '') ? q.get('preset')! : undefined }; }
export function viewURL(v: View) { const q = new URLSearchParams({ view: v.page }); for (const key of ['run', 'legacy', 'task', 'job', 'scan', 'radar', 'start', 'end', 'preset'] as const)
    if (v[key])
        q.set(key, v[key]!); return `/admin?${q}`; }
export function mergeEvents(old: JournalEvent[], next: JournalEvent[], limit = 2000) { const merged = new Map(old.map(e => [e.id, e])); for (const event of next)
    merged.set(event.id, event); return [...merged.values()].sort((a, b) => a.id - b.id).slice(-limit); }
export function taskTiming(t: Task, now = Date.now()) {
  const a = t.attempts?.find(x => x.id === t.current_attempt);
  const span = (from?: string | null, to?: string | null) => { if (!from) return null; const n = (to ? Date.parse(to) : now) - Date.parse(from); return Number.isFinite(n) && n >= 0 ? n : null; };
  return { queue: a ? span(a.queued_at, a.started_at) : t.state === 'QUEUED' ? span(t.queued_at) : null, elapsed: a ? span(a.started_at, a.finished_at) : null, attempt: a };
}
export function workerState(w: Worker, now: number) { const age = now - Date.parse(w.seen_at); return !Number.isFinite(age) || age < 0 ? '心跳时间异常' : age > 75000 ? '心跳过期' : !w.ready ? '配置漂移／不可接单' : w.busy ? '正在执行' : w.pool_mode === 'DRAINING' ? '执行池暂停接单' : '可接单'; }
export type Navigate = (view: View) => void;

export function metricLabel(key:string){const names:Record<string,string>={queue_ms:'入队至领取',attempt_elapsed_ms:'领取至结果登记',input_read_ms:'输入读取',station_qc_ms:'单站适配与质控',fusion_ms:'多波段组合',resident_input_bytes:'解码输入大小（非进程峰值）',decoded_cache_bytes:'解码缓存保留字节',decoded_cache_hits:'进程累计解码缓存命中',decoded_cache_misses:'进程累计解码缓存未命中',context_ms:'上下文准备',qc_core_ms:'QC 核心计算',serialize_validate_ms:'编码与校验',compute_wall_ms:'原生阶段总耗时（含准备）',publication_wall_ms:'产物发布 I/O',process_current_rss_bytes:'进程当前常驻内存',process_lifetime_peak_rss_bytes:'进程生命周期内存峰值',process_rss_sampled_peak_bytes:'本次执行窗口进程采样峰值',input_bytes:'输入字节数',output_bytes:'输出字节数',object_count:'对象数量',cache_hit:'缓存命中',cache_miss:'缓存未命中'};return names[key]??key}

function validViewID(value:string|null){return value&&/^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value)?value:undefined}

function validViewTime(value: string | null) { return value && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value)) ? value : undefined; }
