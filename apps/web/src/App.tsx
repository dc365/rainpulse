import { QCReviewWorkspace } from './workspace/QCReviewWorkspace'
import { AdminWorkspace } from './workspace/AdminWorkspace'
import { MainWorkspace } from './workspace/MainWorkspace'
import { PipelineInspector } from './workspace/PipelineInspector'
import './styles.css'
import './workspace.css'
import './workspace-runtime.css'


export default function App() {
  if (window.location.pathname === '/qc-review') return <QCReviewWorkspace />
  return window.location.pathname.startsWith('/admin')
    ? <><AdminWorkspace /><PipelineInspector /></>
    : <MainWorkspace />
}
