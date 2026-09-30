<template>
  <main :class="['app-shell', `app-shell-${activeView}`]">
    <header class="topbar">
      <a class="brand" href="#" aria-label="Concord 首页" @click.prevent="activeView = 'chat'">
        <span class="brand-mark">C</span>
        <span class="brand-name">Concord</span>
      </a>

      <nav class="view-nav" aria-label="工作区">
        <button :class="{ active: activeView === 'chat' }" @click="activeView = 'chat'">对话</button>
        <button :class="{ active: activeView === 'knowledge' }" @click="activeView = 'knowledge'">知识库</button>
      </nav>

      <div class="topbar-tools">
        <span class="environment-pill">
          <i :class="healthOk ? 'online' : 'offline'"></i>
          {{ currentBackend.label }}
        </span>
        <a class="docs-link" :href="docsUrl" target="_blank" rel="noreferrer">API 文档</a>
        <button class="avatar-button" title="当前用户">{{ userInitial }}</button>
      </div>
    </header>

    <div v-if="toast" class="toast" role="status">{{ toast }}</div>

    <section v-if="activeView === 'chat'" class="page page-chat">
      <div class="page-heading">
        <div class="heading-copy">
          <span class="kicker">Adaptive case workspace</span>
          <h1>和实施工程师 Agent 一起解决问题</h1>
          <p>目标、证据、行动和进度始终围绕同一个 Case 收敛。</p>
        </div>
        <div class="heading-actions">
          <span class="session-label">{{ settings.conversationId || '新会话' }}</span>
          <button class="quiet-button" @click="clearConversation">清空</button>
        </div>
      </div>

      <div class="chat-layout">
        <section class="chat-stage">
          <div class="stage-bar">
            <div class="stage-context">
              <span class="context-dot"></span>
              <span>{{ currentBackend.baseUrl }}</span>
            </div>
            <span>{{ caseConsole ? `${phaseLabel(caseConsole.phase)} · r${caseConsole.revision}` : `${messages.length} 条消息` }}</span>
          </div>

          <div class="messages" ref="messageList">
            <article v-for="item in messages" :key="item.id" :class="['message', item.role]">
              <div class="message-meta">
                <span>{{ item.role === 'user' ? '你' : currentBackend.label + ' Agent' }}</span>
                <small v-if="item.meta">{{ item.meta }}</small>
              </div>
              <p>{{ item.content }}</p>
              <details v-if="item.details" class="response-details">
                <summary>展开技术细节</summary>
                <p>{{ item.details }}</p>
              </details>
              <div v-if="item.trace" class="message-trace">
                <div class="trace-head">
                  <span>工具调用</span>
                  <small v-if="item.trace.requestId">#{{ item.trace.requestId }}</small>
                </div>
                <div v-if="item.trace.toolCalls?.length" class="trace-calls">
                  <details v-for="(call, index) in item.trace.toolCalls" :key="`${item.id}-${index}`" open>
                    <summary>
                      <strong>{{ call.tool_name || 'unknown_tool' }}</strong>
                      <span>{{ call.success ? '成功' : '失败' }}</span>
                    </summary>
                    <pre>{{ formatJson(call.input || {}) }}</pre>
                  </details>
                </div>
                <div v-else class="trace-empty-block">
                  <p>本次请求已生成 trace，但没有可展示的工具输入。</p>
                  <p v-if="item.trace.toolsUsed?.length" class="trace-note">已调用：{{ item.trace.toolsUsed.join(' · ') }}</p>
                </div>
              </div>
            </article>

            <div v-if="messages.length === 0" class="empty-state">
              <div class="empty-symbol">✦</div>
              <h2>从一个真实问题开始</h2>
              <p>下面的快捷问题只是起点，你也可以直接输入自己的测试用例。</p>
              <div class="starter-prompts">
                <button @click="usePrompt('VPN 报 691，今天改过域密码，我只想先恢复使用。')">访问故障</button>
                <button @click="usePrompt('部署后接口偶发超时，我已经重启过两次。')">技术排查</button>
                <button @click="usePrompt('数据对不上，但我暂时不知道应该从哪里开始查。')">问题澄清</button>
              </div>
            </div>
          </div>

          <form class="composer" @submit.prevent="sendMessage">
            <textarea
              v-model="draft"
              rows="3"
              placeholder="输入消息..."
              @keydown.meta.enter.prevent="sendMessage"
              @keydown.ctrl.enter.prevent="sendMessage"
            ></textarea>
            <div v-if="attachments.length" class="attachment-list">
              <span v-for="(item, index) in attachments" :key="`${item.name}-${index}`">
                {{ item.name }}
                <button type="button" aria-label="移除附件" @click="attachments.splice(index, 1)">×</button>
              </span>
            </div>
            <div class="composer-bottom">
              <label class="attachment-button">
                添加截图 / 日志 / 文件
                <input type="file" multiple accept="image/*,audio/*,.txt,.log,.md,.json,.csv" @change="prepareAttachments" />
              </label>
              <span>{{ liveNotice || '⌘ / Ctrl + Enter 发送' }}</span>
              <button type="submit" :disabled="!draft.trim() && !attachments.length">{{ chatRequestsInFlight ? `处理中 ${chatRequestsInFlight}` : '发送' }}</button>
            </div>
          </form>
        </section>

        <aside class="chat-sidebar" ref="sidebarRef">
          <div class="chat-sidebar-scroll">
            <section class="side-card session-card">
              <div class="card-heading">
                <div>
                  <span class="kicker">Session</span>
                  <h2>会话信息</h2>
                </div>
                <span class="status-copy muted">{{ settings.conversationId ? '已启用' : '新会话' }}</span>
              </div>
              <div class="session-grid">
                <div>
                  <span>会话 ID</span>
                  <strong>{{ settings.conversationId || '自动生成' }}</strong>
                </div>
                <div>
                  <span>用户 ID</span>
                  <strong>{{ settings.userId || 'anonymous' }}</strong>
                </div>
              </div>
            </section>

            <section class="side-card case-console-card">
              <div class="card-heading">
                <div>
                  <span class="kicker">Case console</span>
                  <h2>问题主线</h2>
                </div>
                <span class="status-copy" :class="caseConsole?.terminal ? 'success' : 'muted'">
                  {{ caseConsole ? phaseLabel(caseConsole.phase) : '等待建立' }}
                </span>
              </div>

              <template v-if="caseConsole">
                <div class="case-identity">
                  <code>{{ caseConsole.case_id }}</code>
                  <span>revision {{ caseConsole.revision }}</span>
                </div>
                <p class="case-goal">{{ caseGoal }}</p>

                <div class="case-actions">
                  <button
                    v-if="caseConsole.confirmation_required"
                    type="button"
                    class="quiet-button"
                    @click="confirmNextAction"
                  >确认执行下一步</button>
                  <button
                    v-for="action in caseConsole.allowed_actions || []"
                    :key="action"
                    type="button"
                    class="quiet-button"
                    @click="performCaseAction(action)"
                  >{{ actionLabel(action) }}</button>
                </div>

                <div class="preference-grid">
                  <label>
                    <span>回答深度</span>
                    <select v-model="casePreferences.expression_depth" @change="saveCasePreferences">
                      <option value="minimal">只看结果</option>
                      <option value="guided">引导式</option>
                      <option value="expert">专业细节</option>
                    </select>
                  </label>
                  <label>
                    <span>进度频率</span>
                    <select v-model="casePreferences.progress_cadence" @change="saveCasePreferences">
                      <option value="blockers_only">仅阻塞</option>
                      <option value="milestones">里程碑</option>
                      <option value="every_step">每一步</option>
                    </select>
                  </label>
                  <label>
                    <span>协作方式</span>
                    <select v-model="casePreferences.initiative_mode" @change="saveCasePreferences">
                      <option value="agent_led">系统先处理</option>
                      <option value="shared">共同决定</option>
                      <option value="step_by_step">逐步确认</option>
                    </select>
                  </label>
                  <label class="preference-check">
                    <span>呈现顺序</span>
                    <input v-model="casePreferences.result_first" type="checkbox" @change="saveCasePreferences" />
                    <small>结果优先</small>
                  </label>
                </div>
                <button type="button" class="quiet-button" @click="resetCasePreferences">恢复自动适配</button>

                <div class="case-facts">
                  <div><span>确认事实</span><strong>{{ caseConsole.confirmed_facts?.length || 0 }}</strong></div>
                  <div><span>待补证据</span><strong>{{ caseConsole.open_evidence?.length || 0 }}</strong></div>
                  <div><span>附件</span><strong>{{ caseConsole.evidence_artifacts?.length || 0 }}</strong></div>
                </div>

                <div class="case-summary">
                  <p><span>最新结果</span>{{ caseConsole.latest_result || '尚无' }}</p>
                  <p><span>下一步</span>{{ caseConsole.next_request || '系统正在继续处理' }}</p>
                  <p><span>验证</span>{{ caseConsole.verification_status || '尚未验证' }}</p>
                  <p v-if="caseConsole.current_blocker"><span>当前阻塞</span>{{ caseConsole.current_blocker }}</p>
                </div>

                <div class="progress-timeline">
                  <div v-for="event in recentProgress" :key="event.sequence" class="progress-row">
                    <i></i>
                    <div><strong>{{ event.summary }}</strong><small>{{ phaseLabel(event.phase) }}</small></div>
                  </div>
                </div>
              </template>
              <p v-else class="side-empty">发送问题后，这里会持续显示目标、证据和真实执行进度。</p>
            </section>

            <section class="side-card connection-card">
              <div class="card-heading">
                <div>
                  <span class="kicker">Connection</span>
                  <h2>连接配置</h2>
                </div>
                <span class="status-copy" :class="healthOk ? 'success' : 'muted'">{{ healthLabel }}</span>
              </div>

              <p class="connection-runtime">Python Agent Runtime · {{ currentBackend.baseUrl }}</p>

              <label>
                <span>用户 ID</span>
                <input v-model="settings.userId" @change="persist" placeholder="u1001" />
              </label>
              <label>
                <span>会话 ID</span>
                <input v-model="settings.conversationId" @change="persist" placeholder="自动生成" />
              </label>
              <div class="side-actions">
                <button @click="checkHealth">检查连接</button>
                <button class="quiet-button" @click="refreshConsole">刷新</button>
              </div>
            </section>

            <section class="side-card trace-card">
              <div class="card-heading">
                <div>
                  <span class="kicker">Last trace</span>
                  <h2>最近一次请求</h2>
                </div>
                <span class="trace-status" :class="lastResponse ? 'has-data' : ''"></span>
              </div>

              <div v-if="lastResponse" class="trace-body">
                <div class="latency">
                  <span>响应耗时</span>
                  <strong>{{ lastResponse.latencyMs || '-' }}<small> ms</small></strong>
                </div>
                <dl class="detail-list">
                  <div><dt>Case 阶段</dt><dd>{{ lastResponse.casePhase || '-' }}</dd></div>
                  <div><dt>M1 动作</dt><dd>{{ lastResponse.interactionAction || '-' }}</dd></div>
                  <div><dt>证据充分度</dt><dd>{{ lastResponse.evidenceSufficiency || '-' }}</dd></div>
                  <div><dt>M2 状态</dt><dd>{{ lastResponse.resolutionStatus || '-' }}</dd></div>
                  <div><dt>M3 状态</dt><dd>{{ lastResponse.m3Status || '未触发' }}</dd></div>
                </dl>
                <dl v-if="Object.keys(lastResponse.stageLatencyMs || {}).length" class="detail-list">
                  <div v-for="(milliseconds, stage) in lastResponse.stageLatencyMs" :key="stage">
                    <dt>{{ stageLabel(stage) }}</dt><dd>{{ milliseconds }} ms</dd>
                  </div>
                </dl>
                <div v-if="lastTrace?.trace" class="trace-call-list">
                  <div class="trace-call-title">工具调用</div>
                  <div v-for="(call, index) in lastTrace.trace.toolCalls" :key="`${call.tool_use_id || index}`" class="trace-call-item">
                    <div class="trace-call-meta">
                      <strong>{{ call.tool_name || 'unknown_tool' }}</strong>
                      <span>{{ call.latency_ms || 0 }} ms</span>
                    </div>
                    <pre>{{ formatJson(call.input || {}) }}</pre>
                  </div>
                  <div v-if="!lastTrace.trace.toolCalls?.length" class="trace-empty-block">
                    <p>这次 trace 没有记录到工具输入。</p>
                    <p v-if="lastTrace.trace.toolsUsed?.length" class="trace-note">已调用：{{ lastTrace.trace.toolsUsed.join(' · ') }}</p>
                  </div>
                </div>
              </div>
              <p v-else class="side-empty">发送消息后，这里会显示 Case 阶段、控制动作和耗时。</p>
            </section>

            <section class="side-card monitor-card">
              <div class="card-heading">
                <div>
                  <span class="kicker">Runtime</span>
                  <h2>运行状态</h2>
                </div>
                <button class="link-button" @click="loadMonitor">刷新</button>
              </div>
              <div class="mini-stats">
                <div><strong>{{ totalRequests }}</strong><span>请求</span></div>
                <div><strong>{{ agentCount }}</strong><span>Agent</span></div>
                <div><strong>{{ activeAlerts.length }}</strong><span>告警</span></div>
              </div>
              <div v-if="activeAlerts.length" class="alert-note">{{ activeAlerts[0].detail || activeAlerts[0].title }}</div>
              <p v-else class="healthy-note">当前没有活跃告警。</p>
            </section>
          </div>
        </aside>
      </div>
    </section>

    <section v-else-if="activeView === 'knowledge'" class="page page-knowledge">
      <div class="page-heading">
        <div class="heading-copy">
          <span class="kicker">Knowledge operations</span>
          <h1>知识库</h1>
          <p>搜索、补充和维护实施协作 Agent 使用的知识片段。</p>
        </div>
        <div class="count-display"><strong>{{ knowledgeCount }}</strong><span>chunks</span></div>
      </div>

      <div class="knowledge-layout">
        <section class="workspace-card search-workspace">
          <div class="card-heading">
            <div><span class="kicker">Retrieval</span><h2>检索知识</h2></div>
            <code>POST /search</code>
          </div>
          <div class="search-line">
            <input v-model="searchQuery" placeholder="例如：认证失败排查" @keydown.enter="searchKnowledge" />
            <button @click="searchKnowledge" :disabled="busy || !searchQuery.trim()">搜索</button>
          </div>
          <div v-if="searchResults.length" class="result-list">
            <article v-for="(item, index) in searchResults" :key="item.id || item.title || index" class="result-item">
              <span class="result-number">{{ String(index + 1).padStart(2, '0') }}</span>
              <div>
                <div class="result-title"><strong>{{ item.title || '未命名文档' }}</strong><small>score {{ item.score ?? '-' }}</small></div>
                <p>{{ item.content }}</p>
              </div>
            </article>
          </div>
          <div v-else class="workspace-empty">输入问题或证据线索开始搜索。</div>
        </section>

        <section class="workspace-card import-workspace">
          <div class="card-heading">
            <div><span class="kicker">Ingestion</span><h2>添加知识</h2></div>
            <code>SQLite FTS</code>
          </div>
          <label><span>标题</span><input v-model="docTitle" placeholder="认证故障排查规范" /></label>
          <label><span>内容</span><textarea v-model="docContent" rows="7" placeholder="输入实施规范、产品说明或排障流程"></textarea></label>
          <div class="side-actions">
            <button @click="submitKnowledge" :disabled="busy || !docTitle.trim() || !docContent.trim()">添加文档</button>
            <label class="upload-button">上传文件<input type="file" accept=".txt,.md,.json" @change="handleUpload" /></label>
          </div>
        </section>
      </div>

    </section>
  </main>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import {
  addKnowledge,
  backendMeta,
  controlCase as requestCaseControl,
  createInitialSettings,
  requestChat,
  requestCaseConsole,
  requestHealth,
  requestKnowledgeStats,
  requestMonitor,
  requestSearch,
  requestToolTrace,
  saveSettings,
  updateCasePreferences,
  uploadKnowledge
} from './lib/backends'
import {
  actionLabel,
  createCaseId,
  createConversationId,
  formatJson,
  phaseLabel,
  stageLabel
} from './lib/presentation'

const settings = reactive(createInitialSettings())
const activeView = ref('chat')
const messages = ref([])
const draft = ref('')
const attachments = ref([])
const busy = ref(false)
const chatRequestsInFlight = ref(0)
const liveNotice = ref('')
const healthOk = ref(false)
const healthLabel = ref('未检查')
const statusText = ref('')
const knowledgeCount = ref('-')
const searchQuery = ref('认证失败如何排查')
const searchResults = ref([])
const docTitle = ref('认证故障排查规范')
const docContent = ref('先确认错误码、影响范围和最近变更，再选择低风险检查。')
const messageList = ref(null)
const sidebarRef = ref(null)
const monitorData = ref({ agent_stats: {}, tool_stats: {}, active_alerts: [], suggestions: [] })
const lastResponse = ref(null)
const lastTrace = ref(null)
const caseConsole = ref(null)
const casePreferences = reactive({
  expression_depth: 'guided',
  progress_cadence: 'milestones',
  initiative_mode: 'shared',
  result_first: false
})
const toast = ref('')
let toastTimer
let messageSequence = 0
let sidebarObserver
let casePollTimer

const currentBackend = computed(() => backendMeta(settings.backend, settings))
const docsUrl = computed(() => `${currentBackend.value.baseUrl}/docs`)
const userInitial = computed(() => (settings.userId || 'U').slice(0, 1).toUpperCase())
const activeAlerts = computed(() => monitorData.value.active_alerts || [])
const agentCount = computed(() => Object.keys(monitorData.value.agent_stats || {}).length)
const totalRequests = computed(() => Object.values(monitorData.value.agent_stats || {}).reduce((sum, item) => sum + Number(item.total || 0), 0))
const recentProgress = computed(() => (caseConsole.value?.progress || []).slice(-8).reverse())
const caseGoal = computed(() => {
  const goal = caseConsole.value?.goal || {}
  return goal.current_outcome || goal.explicit_goal || goal.inferred_goal || '正在确认希望达到的结果'
})

watch(() => settings.conversationId, persist)
onMounted(() => {
  refreshConsole()
  if (settings.caseId) startCasePolling()
  updateSidebarHeight()
  if (typeof ResizeObserver !== 'undefined') {
    sidebarObserver = new ResizeObserver(updateSidebarHeight)
    if (sidebarRef.value) sidebarObserver.observe(sidebarRef.value)
  }
  window.addEventListener('resize', updateSidebarHeight)
})

onBeforeUnmount(() => {
  sidebarObserver?.disconnect?.()
  window.removeEventListener('resize', updateSidebarHeight)
  clearInterval(casePollTimer)
})

function persist() { saveSettings(settings) }

function updateSidebarHeight() {
  const sidebar = sidebarRef.value
  if (!sidebar) return
  const rect = sidebar.getBoundingClientRect()
  const height = Math.max(320, Math.floor(rect.height))
  sidebar.style.setProperty('--sidebar-height', `${height}px`)
}

async function refreshConsole() {
  await Promise.allSettled([checkHealth(), loadStats(), loadMonitor(), loadCaseConsole()])
}

async function checkHealth() {
  try {
    const data = await requestHealth(settings.backend, settings)
    healthOk.value = data.status === 'ok'
    healthLabel.value = data.status || 'ok'
    statusText.value = JSON.stringify(data, null, 2)
  } catch (error) {
    healthOk.value = false
    healthLabel.value = '不可用'
    statusText.value = error.message
  }
}

async function loadStats() {
  try {
    const data = await requestKnowledgeStats(settings.backend, settings)
    knowledgeCount.value = data.total_chunks ?? data.totalChunks ?? '-'
  } catch {
    knowledgeCount.value = '-'
  }
}

async function loadMonitor() {
  try {
    monitorData.value = await requestMonitor(settings.backend, settings)
  } catch {
    monitorData.value = { agent_stats: {}, tool_stats: {}, active_alerts: [], suggestions: [] }
  }
}


async function sendMessage() {
  const typedContent = draft.value.trim()
  const submittedAttachments = [...attachments.value]
  if (!typedContent && !submittedAttachments.length) return
  const content = typedContent || '请分析我提供的附件，并告诉我当前最重要的结果或下一步。'
  messages.value.push({ id: createMessageId(), role: 'user', content: typedContent || `[提交了 ${submittedAttachments.length} 个附件]` })
  draft.value = ''
  attachments.value = []
  chatRequestsInFlight.value += 1
  liveNotice.value = '已收到，正在更新问题表征…'
  if (!settings.caseId) settings.caseId = createCaseId()
  if (!settings.conversationId) settings.conversationId = createConversationId()
  persist()
  startCasePolling()
  try {
    const response = await requestChat(settings.backend, settings, content, submittedAttachments)
    if (response.conversationId && !settings.conversationId) {
      settings.conversationId = response.conversationId
      persist()
    }
    if (response.caseId) settings.caseId = response.caseId
    lastResponse.value = response
    lastTrace.value = await loadToolTrace(response.caseId)
    const meta = [response.casePhase, response.interactionAction, response.resolutionStatus, response.m3Status].filter(Boolean).join(' · ')
    messages.value.push({ id: createMessageId(), role: 'assistant', content: response.response, details: response.responseDetails, meta, trace: lastTrace.value?.trace || null })
    await Promise.allSettled([loadMonitor(), loadCaseConsole()])
  } catch (error) {
    const stale = String(error.message).startsWith('409')
    messages.value.push({
      id: createMessageId(),
      role: 'assistant',
      content: stale ? '这轮处理已被你后续补充的信息替代。' : error.message,
      meta: stale ? '已采用较新输入' : '请求失败'
    })
  } finally {
    chatRequestsInFlight.value = Math.max(0, chatRequestsInFlight.value - 1)
    liveNotice.value = chatRequestsInFlight.value ? `仍有 ${chatRequestsInFlight.value} 轮正在处理…` : ''
    await nextTick()
    messageList.value?.scrollTo({ top: messageList.value.scrollHeight, behavior: 'smooth' })
  }
}

async function confirmNextAction() {
  if (!settings.caseId) return
  const content = '我确认执行当前已经说明的下一步。'
  messages.value.push({ id: createMessageId(), role: 'user', content })
  chatRequestsInFlight.value += 1
  liveNotice.value = '已确认，正在继续执行并验证…'
  startCasePolling()
  try {
    const response = await requestChat(
      settings.backend,
      settings,
      content,
      [],
      true
    )
    lastResponse.value = response
    lastTrace.value = await loadToolTrace(response.caseId)
    messages.value.push({
      id: createMessageId(),
      role: 'assistant',
      content: response.response,
      details: response.responseDetails,
      meta: '已确认执行'
    })
    await Promise.allSettled([loadMonitor(), loadCaseConsole()])
  } catch (error) {
    messages.value.push({ id: createMessageId(), role: 'assistant', content: error.message, meta: '确认失败' })
  } finally {
    chatRequestsInFlight.value = Math.max(0, chatRequestsInFlight.value - 1)
    liveNotice.value = chatRequestsInFlight.value ? `仍有 ${chatRequestsInFlight.value} 轮正在处理…` : ''
  }
}

function usePrompt(prompt) { draft.value = prompt }

function clearConversation() {
  messages.value = []
  lastResponse.value = null
  lastTrace.value = null
  settings.conversationId = ''
  settings.caseId = ''
  caseConsole.value = null
  attachments.value = []
  clearInterval(casePollTimer)
  persist()
}

async function searchKnowledge() {
  busy.value = true
  try {
    const data = await requestSearch(settings.backend, settings, searchQuery.value, 5)
    searchResults.value = data.results || []
    showToast(`检索完成，返回 ${searchResults.value.length} 条结果`)
  } catch (error) {
    statusText.value = error.message
    showToast('检索失败，请检查连接')
  } finally { busy.value = false }
}

async function submitKnowledge() {
  busy.value = true
  try {
    const data = await addKnowledge(settings.backend, settings, [{ title: docTitle.value.trim(), content: docContent.value.trim() }])
    statusText.value = JSON.stringify(data, null, 2)
    await loadStats()
    showToast('文档已添加')
  } catch (error) {
    statusText.value = error.message
    showToast('文档导入失败')
  } finally { busy.value = false }
}

async function handleUpload(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  busy.value = true
  try {
    const data = await uploadKnowledge(settings.backend, settings, file)
    statusText.value = JSON.stringify(data, null, 2)
    await loadStats()
    showToast(`${file.name} 导入成功`)
  } catch (error) {
    statusText.value = error.message
    showToast('文件导入失败')
  } finally { busy.value = false }
}

async function loadToolTrace(requestId) {
  try {
    return await requestToolTrace(settings.backend, settings, requestId)
  } catch {
    return null
  }
}

async function loadCaseConsole() {
  if (!settings.caseId) return
  try {
    const data = await requestCaseConsole(settings.backend, settings, settings.caseId)
    caseConsole.value = data
    const human = data?.human_collaboration || {}
    if (human.expression_depth) casePreferences.expression_depth = human.expression_depth
    if (human.progress_cadence) casePreferences.progress_cadence = human.progress_cadence
    if (human.initiative_mode) casePreferences.initiative_mode = human.initiative_mode
    casePreferences.result_first = Boolean(human.result_first)
    if (data?.terminal) clearInterval(casePollTimer)
  } catch (error) {
    if (!String(error.message).startsWith('404')) statusText.value = error.message
  }
}

function startCasePolling() {
  clearInterval(casePollTimer)
  loadCaseConsole()
  casePollTimer = setInterval(loadCaseConsole, 1600)
}

async function performCaseAction(action) {
  if (!settings.caseId) return
  try {
    await requestCaseControl(settings.backend, settings, settings.caseId, action)
    await loadCaseConsole()
    if (['resume', 'reopen'].includes(action)) startCasePolling()
    showToast(`Case 已${actionLabel(action)}`)
  } catch (error) {
    statusText.value = error.message
    showToast('Case 操作失败')
  }
}

async function saveCasePreferences() {
  if (!settings.caseId) return
  try {
    await updateCasePreferences(settings.backend, settings, settings.caseId, {
      expression_depth: casePreferences.expression_depth,
      progress_cadence: casePreferences.progress_cadence,
      initiative_mode: casePreferences.initiative_mode,
      result_first: casePreferences.result_first
    })
    await loadCaseConsole()
    showToast('当前 Case 的交互偏好已更新')
  } catch (error) {
    statusText.value = error.message
    showToast('偏好更新失败')
  }
}

async function resetCasePreferences() {
  if (!settings.caseId) return
  try {
    await updateCasePreferences(settings.backend, settings, settings.caseId, {
      expression_depth: null,
      progress_cadence: null,
      initiative_mode: null,
      result_first: null
    })
    await loadCaseConsole()
    showToast('已恢复按当前用户状态自动适配')
  } catch (error) {
    statusText.value = error.message
    showToast('恢复自动适配失败')
  }
}

async function prepareAttachments(event) {
  const files = [...(event.target.files || [])]
  event.target.value = ''
  for (const file of files.slice(0, 8 - attachments.value.length)) {
    if (file.size > 10_000_000) {
      showToast(`${file.name} 超过 10MB，未添加`)
      continue
    }
    if (file.type.startsWith('image/')) {
      attachments.value.push({
        kind: 'image',
        name: file.name,
        media_type: file.type,
        size: file.size,
        url: await readFile(file, 'data')
      })
    } else if (file.type.startsWith('audio/')) {
      attachments.value.push({
        kind: 'audio',
        name: file.name,
        media_type: file.type,
        size: file.size
      })
    } else {
      attachments.value.push({
        kind: file.name.toLowerCase().endsWith('.log') ? 'log' : 'file',
        name: file.name,
        media_type: file.type || 'text/plain',
        size: file.size,
        content: await readFile(file, 'text')
      })
    }
  }
}

function readFile(file, mode) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(reader.error)
    if (mode === 'data') reader.readAsDataURL(file)
    else reader.readAsText(file)
  })
}

function createMessageId() {
  messageSequence += 1
  return `message-${Date.now()}-${messageSequence}`
}

function showToast(message) {
  toast.value = message
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toast.value = '' }, 2600)
}
</script>
