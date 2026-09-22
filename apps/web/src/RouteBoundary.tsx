import { Component } from 'react'
import type { ReactNode } from 'react'

/** Recover from an unavailable code chunk without an automatic reload loop. */
export class RouteBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() {
    if (this.state.failed) return <main role="alert" className="route-feedback">
      <h1>页面暂时无法打开</h1>
      <p>可能是网络中断或页面版本已更新。重新加载不会提交重算或改变后台任务。</p>
      <button type="button" onClick={() => window.location.reload()}>重新加载页面</button>
    </main>
    return this.props.children
  }
}
