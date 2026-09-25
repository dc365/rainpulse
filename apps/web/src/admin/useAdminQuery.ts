import { useCallback, useEffect, useState } from 'react';
import { failure, read } from './api';
export function useAdminQuery<T>(token: string, path: string | null, interval = 10000) {
    const [snapshot, setSnapshot] = useState<{
        path: string;
        data: T;
    } | null>(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);
    const [revision, setRevision] = useState(0);
    const refresh = useCallback(() => setRevision(n => n + 1), []);
    useEffect(() => {
        if (!path)
            return;
        const controller = new AbortController();
        let timer: ReturnType<typeof setTimeout> | undefined;
        const load = async () => { setLoading(true); try {
            const data = await read<T>(token, path, controller.signal);
            if (!controller.signal.aborted) {
                setSnapshot({ path, data });
                setError('');
            }
        }
        catch (e) {
            if (!controller.signal.aborted)
                setError(failure(e));
        }
        finally {
            if (!controller.signal.aborted) {
                setLoading(false);
                if (interval > 0)
                    timer = setTimeout(() => { if (document.visibilityState === 'visible')
                        void Promise.resolve().then(() => { if (!controller.signal.aborted) return load(); }); }, interval);
            }
        } };
        const visible = () => { if (document.visibilityState === 'visible')
            refresh(); };
        document.addEventListener('visibilitychange', visible);
        void Promise.resolve().then(() => { if (!controller.signal.aborted) return load(); });
        return () => { controller.abort(); clearTimeout(timer); document.removeEventListener('visibilitychange', visible); };
    }, [token, path, interval, revision, refresh]);
    return { data: snapshot?.path === path ? snapshot.data : null, error, loading, refresh };
}
export function useClock() { const [now, setNow] = useState(() => Date.now()); useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 10000); return () => clearInterval(timer); }, []); return now; }
