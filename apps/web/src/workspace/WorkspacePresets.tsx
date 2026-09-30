export type WorkspacePresetID = 'forecast' | 'qc' | 'verification'

const presets: { id: WorkspacePresetID; label: string }[] = [
  { id: 'forecast', label: '预报对比' },
  { id: 'qc', label: '质控排查' },
  { id: 'verification', label: '检验回放' },
]

// Shared topbar preset tabs. Same markup on every workspace page so the
// header reads identically; in-page switching is opt-in via onSelect.
export function WorkspacePresets({ active, onSelect }: { active: WorkspacePresetID; onSelect?: (id: WorkspacePresetID) => void }) {
  const choose = onSelect ?? ((id: WorkspacePresetID) => { window.location.href = `/?preset=${id}` })
  return <nav className="radar-qc-presets" aria-label="工作台预设">
    {presets.map(p => <button key={p.id} type="button" role="tab" aria-selected={p.id === active} className={p.id === active ? 'active' : ''} onClick={() => { if (p.id !== active) choose(p.id) }}>{p.label}</button>)}
  </nav>
}
