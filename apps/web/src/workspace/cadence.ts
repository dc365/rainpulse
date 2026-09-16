// Unified workbench cadence: the shared timeline is one slot every six minutes.
// The QC timeline additionally keeps only slots that sit on the same six-minute
// clock as freshly generated analyses, so imported off-grid slots never mix in.
export const TIMELINE_STEP_MINUTES = 6

export function isTimelineGridTime(value: string) {
  const date = new Date(value)
  return Number.isFinite(date.getTime())
    && date.getUTCMinutes() % TIMELINE_STEP_MINUTES === 0
    && date.getUTCSeconds() === 0
}
