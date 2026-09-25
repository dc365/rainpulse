import { lazy, Suspense } from 'react'
import { RouteBoundary } from './RouteBoundary'
import './styles.css'
import './workspace.css'
import './workspace-runtime.css'
import './route-feedback.css'

const MainWorkspace = lazy(() => import('./workspace/MainWorkspace').then(m => ({ default: m.MainWorkspace })))
const QCReviewWorkspace = lazy(() => import('./workspace/QCReviewWorkspace').then(m => ({ default: m.QCReviewWorkspace })))
const RadarQCWorkspace = lazy(() => import('./workspace/RadarQCWorkspace').then(m => ({ default: m.RadarQCWorkspace })))
const AdminRoute = lazy(() => import('./admin/AdminApp'))

export default function App() {
  // Preserve the existing path contract; no router migration in batch 3.
  const content = window.location.pathname === '/qc-review' ? <QCReviewWorkspace />
    : window.location.pathname.startsWith('/admin') ? <AdminRoute />
    : new URLSearchParams(window.location.search).get('preset') === 'qc' ? <RadarQCWorkspace /> : <MainWorkspace />
  return <RouteBoundary>
    <Suspense fallback={<main role="status" aria-live="polite" className="route-feedback">正在加载工作台…</main>}>
      {content}
    </Suspense>
  </RouteBoundary>
}
