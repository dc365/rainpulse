export type XQCCompletion = { status: string; mode: string }

export function XQCStatus({ completion }: { completion?: XQCCompletion }) {
  if (!completion || (completion.status === 'EVALUATED' && completion.mode !== 'audit')) return null
  return <div className="radar-qc-alert" role="alert">{completion.mode === 'audit'
    ? '此结果为审计模式，未执行增强质控清除。'
    : `此层质控未完整完成（${completion.status}），仍可能残留径向或扇形污染，请勿作为验收通过的结果。`}</div>
}
