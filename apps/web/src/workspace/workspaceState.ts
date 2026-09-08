import type { CycleList, CycleSummary, WorkspaceCycleDetail } from './model'

export type DataMode = 'follow' | 'history'
export type WorkspaceDataState = {
  cycles: CycleSummary[]
  detail: WorkspaceCycleDetail | null
  selectedTime: string | null
  requestedID: string
  requestedTime: string | null
  mode: DataMode
  initialized: boolean
  loading: boolean
  catalogError: string | null
  detailError: string | null
  stale: boolean
}

export const initialWorkspaceState: WorkspaceDataState = {
  cycles: [], detail: null, selectedTime: null, requestedID: '', requestedTime: null,
  mode: 'follow', initialized: false, loading: true,
  catalogError: null, detailError: null, stale: false,
}

export type WorkspaceAction =
  | { type: 'catalog'; payload: CycleList }
  | { type: 'catalog-error'; message: string }
  | { type: 'pick'; cycle: CycleSummary; time?: string }
  | { type: 'follow' }
  | { type: 'pin' }
  | { type: 'loaded'; id: string; detail: WorkspaceCycleDetail; stale: boolean }
  | { type: 'failed'; id: string; message: string }
  | { type: 'time'; time: string | null }

export function isLiveCycle(cycle: CycleSummary | null | undefined) {
  return cycle != null && ['operational', 'realtime_shadow'].includes(cycle.execution_mode)
}

export function cycleAgeSeconds(cycle: CycleSummary | null | undefined, now: number) {
  if (!cycle) return Number.POSITIVE_INFINITY
  const age = (now - Date.parse(cycle.issue_time)) / 1000
  return Number.isFinite(age) && age >= 0 ? Math.max(age, cycle.freshness_seconds) : Number.POSITIVE_INFINITY
}

export function workspaceReducer(state: WorkspaceDataState, action: WorkspaceAction): WorkspaceDataState {
  switch (action.type) {
    case 'catalog': {
      const cycles = action.payload.items
      const latestLive = cycles.find(isLiveCycle)
      // Only the first archive-only catalog chooses history automatically. A
      // later outage cannot change the operator's intent to follow live data.
      const mode = !state.initialized && cycles.length > 0
        ? latestLive ? 'follow' : 'history'
        : state.mode
      const target = mode === 'follow' && latestLive
        ? latestLive
        : !state.requestedID ? cycles[0] : undefined
      const changed = target != null && target.cycle_id !== state.requestedID
      return {
        ...state, cycles, mode, initialized: state.initialized || cycles.length > 0,
        requestedID: target?.cycle_id ?? state.requestedID,
        requestedTime: changed ? target?.issue_time ?? null : state.requestedTime,
        loading: changed ? true : cycles.length === 0 && !state.detail ? false : state.loading,
        catalogError: action.payload.degraded_sources?.length
          ? `部分数据源暂不可用：${action.payload.degraded_sources.join('、')}` : null,
      }
    }
    case 'catalog-error':
      return { ...state, catalogError: action.message, loading: state.detail ? state.loading : false }
    case 'pick': {
      const time = action.time ?? action.cycle.issue_time
      const same = state.detail?.cycle_id === action.cycle.cycle_id
      return { ...state, initialized: true, mode: 'history', requestedID: action.cycle.cycle_id,
        requestedTime: same ? null : time, selectedTime: same ? time : state.selectedTime,
        loading: !same, detailError: null }
    }
    case 'follow': {
      const latest = state.cycles.find(isLiveCycle)
      const same = latest?.cycle_id === state.detail?.cycle_id
      return { ...state, initialized: true, mode: 'follow', detailError: null,
        requestedID: latest?.cycle_id ?? state.requestedID,
        requestedTime: latest && !same ? latest.issue_time : null,
        selectedTime: latest && same ? latest.issue_time : state.selectedTime,
        loading: latest ? !same : false }
    }
    case 'pin':
      return { ...state, initialized: true, mode: 'history', requestedID: state.detail?.cycle_id ?? state.requestedID,
        requestedTime: null, loading: false }
    case 'loaded': {
      if (action.id !== state.requestedID || action.detail.cycle_id !== action.id) return state
      const preferred = state.requestedTime ?? state.selectedTime
      const selectedTime = preferred && action.detail.timeline.includes(preferred)
        ? preferred : action.detail.issue_time
      return { ...state, detail: action.detail, selectedTime, requestedTime: null,
        loading: false, detailError: null, stale: action.stale }
    }
    case 'failed':
      if (action.id !== state.requestedID) return state
      // The displayed cycle, valid time and images remain one committed snapshot.
      return { ...state, loading: false, detailError: action.message }
    case 'time':
      if (action.time && !state.detail?.timeline.includes(action.time)) return state
      return { ...state, selectedTime: action.time }
  }
}

export function assertCycleList(value: unknown): asserts value is CycleList {
  const data = value as Partial<CycleList> | null
  if (!data || data.schema_version !== '1.0' || !Array.isArray(data.items)
    || data.items.some(c => !c || typeof c.cycle_id !== 'string' || !Number.isFinite(Date.parse(c.issue_time))
      || !c.capabilities || !Number.isFinite(c.freshness_seconds))) {
    throw new Error('周期目录格式不正确')
  }
}

export function assertCycleDetail(value: unknown, expectedID: string): asserts value is WorkspaceCycleDetail {
  const data = value as Partial<WorkspaceCycleDetail> | null
  if (!data || data.schema_version !== '1.0' || data.cycle_id !== expectedID
    || !data.grid || !data.quality || !Array.isArray(data.radars) || !Array.isArray(data.timeline)
    || !Array.isArray(data.panels) || !data.capabilities || !Number.isFinite(Date.parse(data.issue_time ?? ''))
    || data.timeline.some(t => !Number.isFinite(Date.parse(t)))
    || data.panels.some(p => !p || !Array.isArray(p.frames))) {
    throw new Error('工作台数据身份或格式不正确')
  }
}
