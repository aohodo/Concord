const PHASE_LABELS = {
  formulation: '理解问题',
  resolution: '处理中',
  collaboration: '协作中',
  verification: '验证中',
  waiting_for_user: '等待补充',
  waiting_for_external: '等待外部系统',
  resolved: '已解决',
  paused: '已暂停',
  cancelled: '已取消',
  human_required: '需要人工',
  timed_out: '已超时',
  error: '异常'
}

const ACTION_LABELS = {
  pause: '暂停',
  resume: '继续',
  cancel: '取消',
  reopen: '重新打开'
}

const STAGE_LABELS = {
  memory_read: '记忆读取',
  vision_extraction: '视觉取证',
  m1_formulation: 'M1 问题表征',
  m2_resolution: 'M2 解决规划',
  presentation_and_accounting: '呈现与负担计量',
  memory_write: '记忆写入'
}

export function phaseLabel(value) {
  return PHASE_LABELS[value] || value || '准备中'
}

export function actionLabel(value) {
  return ACTION_LABELS[value] || value
}

export function stageLabel(value) {
  return STAGE_LABELS[value] || value
}

export function createCaseId() {
  const id = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`
  return `case-${id}`
}

export function createConversationId() {
  return globalThis.crypto?.randomUUID?.() || `conv-${Date.now()}`
}

export function formatPercent(value) {
  const number = Number(value || 0)
  return `${(number <= 1 ? number * 100 : number).toFixed(1)}%`
}

export function formatJson(value) {
  try {
    return JSON.stringify(value ?? {}, null, 2)
  } catch {
    return String(value ?? '')
  }
}
