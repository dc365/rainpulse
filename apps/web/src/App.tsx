import { AdminWorkspace } from './workspace/AdminWorkspace'
import { MainWorkspace } from './workspace/MainWorkspace'
import './styles.css'
import './workspace.css'

export default function App() {
  return window.location.pathname.startsWith('/admin')
    ? <AdminWorkspace />
    : <MainWorkspace />
}
