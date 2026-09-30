const DEFAULT_BACKENDS = {
  python: {
    id: 'python',
    label: 'Python',
    baseUrl: runtimeConfig().apiUrl || runtimeConfig().pythonApiUrl || import.meta.env.VITE_PYTHON_API_URL || '/api/python',
    port: '8000'
  },
}

export function createInitialSettings() {
  const saved = readSettings()
  return {
    backend: 'python',
    userId: saved.userId || 'u1001',
    conversationId: saved.conversationId || '',
    caseId: saved.caseId || '',
    endpoints: {
      python: saved.endpoints?.python || DEFAULT_BACKENDS.python.baseUrl
    }
  }
}

export function saveSettings(settings) {
  localStorage.setItem('concord.frontend.settings', JSON.stringify(settings))
}

export function backendMeta(type, settings) {
  const meta = DEFAULT_BACKENDS.python
  return {
    ...meta,
    baseUrl: normalizeBaseUrl(settings.endpoints[type] || meta.baseUrl)
  }
}

export async function requestHealth(type, settings) {
  return requestJson(backendMeta(type, settings).baseUrl, '/health')
}

export async function requestMonitor(type, settings) {
  return requestJson(backendMeta(type, settings).baseUrl, '/monitor')
}

export async function requestKnowledgeStats(type, settings) {
  return requestJson(backendMeta(type, settings).baseUrl, '/knowledge/stats')
}

export async function requestSearch(type, settings, query, topK = 5) {
  const params = new URLSearchParams({ query, top_k: String(topK) })
  return requestJson(backendMeta(type, settings).baseUrl, `/search?${params}`, { method: 'POST' })
}

export async function requestChat(type, settings, message, attachments = [], confirmationGranted = false) {
  const meta = backendMeta(type, settings)
  const payload = buildChatPayload(type, settings, message, attachments, confirmationGranted)
  const raw = await requestJson(meta.baseUrl, '/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })
  return normalizeChatResponse(type, raw)
}

export async function requestToolTrace(type, settings, caseId) {
  if (!caseId) return null
  const params = identityParams(settings)
  const raw = await requestJson(backendMeta(type, settings).baseUrl, `/trace/tool/${encodeURIComponent(caseId)}?${params}`)
  return normalizeToolTraceResponse(raw)
}

export async function requestCaseConsole(type, settings, caseId) {
  if (!caseId) return null
  const params = identityParams(settings)
  return requestJson(
    backendMeta(type, settings).baseUrl,
    `/cases/${encodeURIComponent(caseId)}/console?${params}`
  )
}

export async function controlCase(type, settings, caseId, action, reason = '') {
  return requestJson(
    backendMeta(type, settings).baseUrl,
    `/cases/${encodeURIComponent(caseId)}/control`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action,
        reason,
        user_id: settings.userId || 'anonymous',
        tenant_id: 'local'
      })
    }
  )
}

export async function updateCasePreferences(type, settings, caseId, preferences) {
  return requestJson(
    backendMeta(type, settings).baseUrl,
    `/cases/${encodeURIComponent(caseId)}/preferences`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        preferences,
        user_id: settings.userId || 'anonymous',
        tenant_id: 'local'
      })
    }
  )
}

export async function addKnowledge(type, settings, documents) {
  return requestJson(backendMeta(type, settings).baseUrl, '/knowledge/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ documents })
  })
}

export async function uploadKnowledge(type, settings, file) {
  const form = new FormData()
  form.append('file', file)
  return requestJson(backendMeta(type, settings).baseUrl, '/knowledge/upload', {
    method: 'POST',
    body: form
  })
}

function buildChatPayload(type, settings, message, attachments = [], confirmationGranted = false) {
  return {
    message,
    user_id: settings.userId || 'anonymous',
    conv_id: settings.conversationId || undefined,
    case_id: settings.caseId || undefined,
    attachments,
    confirmation_granted: confirmationGranted
  }
}

function normalizeChatResponse(type, raw) {
  return {
    backend: type,
    conversationId: raw.conversation_id || raw.conversationId || raw.conv_id || '',
    requestId: raw.request_id || raw.requestId || '',
    caseId: raw.case_id || raw.caseId || '',
    casePhase: raw.case_phase || raw.casePhase || '',
    caseRevision: Number(raw.case_revision ?? raw.caseRevision ?? 0),
    requestGeneration: Number(raw.request_generation ?? raw.requestGeneration ?? 0),
    response: raw.response || '',
    responseDetails: raw.response_details || raw.responseDetails || '',
    caseStatus: raw.case_status || raw.caseStatus || '',
    evidenceSufficiency: raw.evidence_sufficiency || raw.evidenceSufficiency || '',
    interactionAction: raw.interaction_action || raw.interactionAction || '',
    resolutionStatus: raw.resolution_status || raw.resolutionStatus || '',
    m3Status: raw.m3_status || raw.m3Status || '',
    m3Agents: raw.m3_agents || raw.m3Agents || [],
    latencyMs: Number(raw.latency_ms ?? raw.latencyMs ?? 0),
    stageLatencyMs: raw.stage_latency_ms || raw.stageLatencyMs || {},
    verified: raw.verified,
    grounded: raw.grounded,
    humanCollaboration: raw.human_collaboration || raw.humanCollaboration || {},
    evidenceArtifacts: raw.evidence_artifacts || raw.evidenceArtifacts || [],
    visibleProgress: raw.visible_progress || raw.visibleProgress || [],
    raw
  }
}

function normalizeToolTraceResponse(raw) {
  const trace = raw?.trace || {}
  return {
    requestId: raw?.request_id || raw?.requestId || '',
    found: Boolean(raw?.found),
    trace: {
      ...trace,
      toolsUsed: trace.tools_used || trace.toolsUsed || [],
      toolCalls: trace.tool_calls || trace.toolCalls || []
    },
    raw
  }
}

async function requestJson(baseUrl, path, options = {}) {
  const url = `${normalizeBaseUrl(baseUrl)}${path}`
  const response = await fetch(url, options)
  const text = await response.text()
  let data = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = text
  }
  if (!response.ok) {
    const detail = typeof data === 'string' ? data : JSON.stringify(data)
    throw new Error(`${response.status} ${response.statusText}: ${detail}`)
  }
  return data
}

function normalizeBaseUrl(value) {
  return String(value || '').replace(/\/+$/, '')
}

function readSettings() {
  try {
    return JSON.parse(
      localStorage.getItem('concord.frontend.settings') ||
      localStorage.getItem('concord.frontend.settings') ||
      '{}'
    )
  } catch {
    return {}
  }
}

function identityParams(settings) {
  return new URLSearchParams({
    user_id: settings.userId || 'anonymous',
    tenant_id: 'local'
  })
}

function runtimeConfig() {
  if (typeof window === 'undefined') return {}
  return window.__CONCORD_CONFIG__ || {}
}
