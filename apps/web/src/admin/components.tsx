import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { stateLabel, tone } from './model';
export const actionNames: Record<string, string> = { pause: '暂停接单', resume: '恢复接单', cancel: '取消作业', retry_failed: '仅重试失败项', wake: '重新投递排队引用', recover: '恢复结果登记', abandon: '撤销失联执行权' };
export function Badge({ state, children }: {
    state: string;
    children?: ReactNode;
}) { return <span className={`ops-badge ${tone(state)}`}>{children ?? stateLabel(state)}</span>; }
export function Notice({ children, error = false }: {
    children: ReactNode;
    error?: boolean;
}) { return <div className={`ops-notice ${error ? 'error' : ''}`} role={error ? 'alert' : undefined}>{children}</div>; }
export function Empty({ children }: {
    children: ReactNode;
}) { return <div className="ops-empty">{children}</div>; }
export function JSONView({ value, label = '详细信息' }: {
    value: unknown;
    label?: string;
}) { return <details className="ops-json"><summary>{label}</summary><pre>{JSON.stringify(value, null, 2)}</pre></details>; }
export function Copy({ value }: {
    value: string;
}) { const [message, setMessage] = useState('复制'); return <button className="ops-quiet" title={value} onClick={() => { if (!navigator.clipboard) { setMessage('请手动复制'); return; } void navigator.clipboard.writeText(value).then(() => setMessage('已复制')).catch(() => setMessage('请手动复制')); }}>{message}</button>; }
export function Download({ value, name, label }: {
    value: unknown;
    name: string;
    label: string;
}) { return <button className="ops-quiet" onClick={() => { const b = new Blob([typeof value === 'string' ? value : JSON.stringify(value, null, 2)], { type: 'text/plain;charset=utf-8' }); const url = URL.createObjectURL(b); const a = document.createElement('a'); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }}>{label}</button>; }
export function Confirm({ action, busy, onCancel, onConfirm }: {
    action: string;
    busy: boolean;
    onCancel: () => void;
    onConfirm: () => void;
}) {
    const [ack, setAck] = useState(false);
    useEffect(() => { const f = (e: KeyboardEvent) => { if (e.key === 'Escape' && !busy)
        onCancel(); }; document.addEventListener('keydown', f); return () => document.removeEventListener('keydown', f); }, [busy, onCancel]);
    const messages: Record<string, string> = { retry_failed: '只恢复失败和依赖受阻的任务，成功结果继续复用。输入与配置保持冻结；如果它们已经变化，服务端会拒绝执行。', pause: '停止领取后续任务。已经执行的任务可完成当前阶段并保存候选结果，不会立即杀死进程。', resume: '核对冻结配置和输入后恢复领取。此操作不改变默认发布。', cancel: '阻止新任务，执行中任务在安全检查点退出。取消完成前将显示“取消中”，不会伪装成已经停止。', recover: '仅检查此尝试的完成标记并恢复登记，不重跑算法。没有完整标记或身份不一致时拒绝恢复。', abandon: '这不是终止进程按钮。必须先在主机上停止原Worker，且心跳与租约均过期。确认后撤销旧提交权，后续可重试失败项。', wake: '重新投递尚未领取的引用，用于消息保留期届满等情况。不会新建第二次执行或重复计算已领取的任务。' };
    return <div className="ops-modal-backdrop"><section role="dialog" aria-modal="true" aria-labelledby="ops-confirm-title" className="ops-modal"><h2 id="ops-confirm-title">{actionNames[action]}</h2><p>{messages[action]}</p>{action === 'abandon' && <label className="ops-check"><input type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)}/>我已核实并停止原 Worker，允许撤销其执行权</label>}<div className="ops-actions"><button disabled={busy} onClick={onCancel}>返回</button><button className={action === 'cancel' || action === 'abandon' ? 'ops-danger' : 'ops-primary'} disabled={busy || (action === 'abandon' && !ack)} onClick={onConfirm}>{busy ? '服务端处理中…' : '确认操作'}</button></div></section></div>;
}
