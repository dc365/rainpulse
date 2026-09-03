import { AdminWorkspace } from './workspace/AdminWorkspace'
import { MainWorkspace } from './workspace/MainWorkspace'
import { PipelineInspector } from './workspace/PipelineInspector'
import { WorkspaceCrosshairInspector } from './workspace/WorkspaceCrosshairInspector'
import { WorkspaceLiveBridge } from './workspace/WorkspaceLiveBridge'
import { installMapProbeBridge } from './workspace/mapProbeBridge'
import './styles.css'
import './workspace.css'
import './workspace-runtime.css'

installMapProbeBridge()

export default function App() {
  return window.location.pathname.startsWith('/admin')
    ? <><AdminWorkspace /><PipelineInspector /></>
    : <><MainWorkspace /><WorkspaceCrosshairInspector /><WorkspaceLiveBridge /></>
}
