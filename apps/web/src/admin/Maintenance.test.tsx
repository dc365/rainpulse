import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { Maintenance } from './Maintenance';
vi.mock('./api', () => ({ post: vi.fn(), read: vi.fn(), failure: (e: Error) => e.message }));
vi.mock('./useAdminQuery', () => ({ useClock: () => Date.parse('2026-09-23T00:00:00Z'), useAdminQuery: (_token: string,path: string) => ({ refresh: vi.fn(),error: '', data: path==='/releases'?{channels:[],items:[]}:path==='/storage'?{control:{session_id:null,pressure_report:'',revision:1},reports:[],plans:[]}:{items:[]} }) }));
afterEach(cleanup);
it('does not fabricate an active version when no worker is registered',()=>{render(<Maintenance token="token" navigate={vi.fn()}/>);expect(screen.getByText(/尚无当前或就绪身份/)).toBeTruthy();});
it('shows missing host telemetry and explicit preview without a delete-all action',()=>{render(<Maintenance token="token" navigate={vi.fn()}/>);fireEvent.click(screen.getByRole('tab',{name:'存储与清理'}));expect(screen.getByText(/尚无主机存储采样/)).toBeTruthy();expect(screen.getByRole('button',{name:'生成清理预览'})).toBeTruthy();expect(screen.queryByRole('button',{name:'删除全部'})).toBeNull();});
