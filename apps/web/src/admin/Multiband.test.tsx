import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { NewRun } from './NewRun';

vi.mock('./useAdminQuery', () => ({
  useClock: () => Date.parse('2026-09-23T01:00:00Z'),
  useAdminQuery: () => ({ data: null, error: null, loading: false, refresh: vi.fn() }),
}));
const post = vi.hoisted(() => vi.fn());
vi.mock('./api', () => ({ read: vi.fn(), post, failure: (e: unknown) => String(e) }));
afterEach(() => { cleanup(); sessionStorage.clear(); post.mockReset(); });
it('shows separate band-aware presets without submitting on selection', () => {
  render(<NewRun token="test" navigate={vi.fn()} />);
  const select = screen.getByLabelText('重算预设');
  fireEvent.change(select, { target: { value: 'sx_composite' } });
  expect(screen.getByPlaceholderText('网络配置只有一个产品时可留空')).toBeTruthy();
  expect(screen.getByText(/这里创建历史候选任务/)).toBeTruthy();
  expect(post).not.toHaveBeenCalled();
});
it('checks selected product through the existing plan endpoint', async () => {
  post.mockRejectedValue(new Error('fixture stop after checking request'));
  render(<NewRun token="test" navigate={vi.fn()} initial={{ preset: 'sx_composite', radar: 's1,x1', start: '2026-09-23T00:00:00Z', end: '2026-09-23T00:30:00Z' }} />);
  fireEvent.change(screen.getByPlaceholderText('网络配置只有一个产品时可留空'), { target: { value: 'local' } });
  fireEvent.click(screen.getByRole('button', { name: '2. 生成预检查计划' }));
  await screen.findByText(/fixture stop/);
  expect(post).toHaveBeenCalledWith('test', '/plans', expect.objectContaining({ preset: 'sx_composite', product_id: 'local', radar_ids: ['s1','x1'] }));
});
it('plans standalone X QC without asking for a spatial product', async () => {
  post.mockRejectedValue(new Error('fixture stop after checking request'));
  render(<NewRun token="test" navigate={vi.fn()} initial={{ preset: 'x_qc', radar: 'zf101', start: '2026-08-28T00:00:00Z', end: '2026-08-28T00:06:00Z' }} />);
  expect(screen.queryByLabelText('产品 ID')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '2. 生成预检查计划' }));
  await screen.findByText(/fixture stop/);
  expect(post).toHaveBeenCalledWith('test', '/plans', expect.objectContaining({ preset: 'x_qc', radar_ids: ['zf101'] }));
  expect(post.mock.calls[0][2]).not.toHaveProperty('product_id');
});
