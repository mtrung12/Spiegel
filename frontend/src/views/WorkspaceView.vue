<template>
  <div class="workspace-view">
    <!-- Header -->
    <header class="app-header">
      <div class="header-left">
        <!-- The workspace is opened from a report, and used to have no way back
             to it: the wordmark dropped the user all the way out to the project
             list, losing the report they were reading. -->
        <button type="button" class="back-btn" :aria-label="backLabel" :title="backLabel" @click="goBack">
          <span class="back-arrow" aria-hidden="true">←</span>
          <span class="back-label">{{ backLabel }}</span>
        </button>

        <span class="header-divider" aria-hidden="true"></span>

        <button type="button" class="brand" @click="router.push('/')">
          <span class="sr-only">{{ $t('a11y.backToProjectList') }}</span>
          <span aria-hidden="true">SPIEGEL</span>
        </button>
      </div>

      <div class="header-center">
        <span class="project-name">{{ project?.name || $t('workspace.title') }}</span>
      </div>

      <div class="header-right">
        <LanguageSwitcher />
        <div class="header-divider"></div>
        <button class="header-btn" :disabled="!project?.graph_id" @click="goToGraph">
          {{ $t('workspace.openGraph') }}
        </button>
        <div class="header-divider"></div>
        <span class="status-indicator" :class="loading ? 'processing' : 'ready'">
          <span class="dot"></span>
          {{ loading ? $t('workspace.loading') : $t('workspace.ready') }}
        </span>
      </div>
    </header>

    <main id="main-content" class="content-area">
      <!-- SIDEBAR -->
      <aside class="sidebar">
        <div class="sidebar-block">
          <div class="sidebar-label">{{ $t('workspace.project') }}</div>
          <p class="project-requirement">
            {{ project?.simulation_requirement || $t('workspace.noRequirement') }}
          </p>
        </div>

        <div class="sidebar-block">
          <div class="sidebar-label">
            {{ $t('workspace.simulations') }}
            <span class="count">{{ simulations.length }}</span>
          </div>
          <div v-if="simulations.length === 0" class="sidebar-empty">{{ $t('workspace.noSimulations') }}</div>
          <div
            v-for="sim in simulations"
            :key="sim.simulation_id"
            class="sim-group"
          >
            <button class="sim-row" @click="goToSimulation(sim)">
              <span class="sim-id mono">{{ shortId(sim.simulation_id) }}</span>
              <span class="sim-status" :class="sim.status">{{ sim.status }}</span>
            </button>

            <div class="report-list">
              <button
                v-for="report in reportsBySimulation[sim.simulation_id] || []"
                :key="report.report_id"
                class="report-row"
                :class="{ active: report.report_id === selectedReportId }"
                @click="selectReport(report.report_id)"
              >
                <span class="report-dot" :class="report.status"></span>
                <span class="report-title">{{ report.outline?.title || shortId(report.report_id) }}</span>
              </button>
              <div
                v-if="!(reportsBySimulation[sim.simulation_id] || []).length"
                class="report-empty"
              >
                {{ $t('workspace.noReports') }}
              </div>
            </div>
          </div>
        </div>
      </aside>

      <!-- REPORT -->
      <section class="report-pane">
        <div v-if="!selectedReportId" class="pane-empty">
          <span>{{ $t('workspace.selectReport') }}</span>
        </div>

        <div v-else class="report-content-wrapper">
          <div class="report-header-block">
            <div class="report-meta">
              <span class="report-tag">{{ $t('workspace.reportTag') }}</span>
              <span class="report-id mono">{{ selectedReportId }}</span>
            </div>
            <h1 class="main-title">{{ selectedReport?.outline?.title || $t('workspace.untitledReport') }}</h1>
            <p v-if="selectedReport?.outline?.summary" class="sub-title">{{ selectedReport.outline.summary }}</p>
            <div class="title-divider"></div>
          </div>

          <div v-if="reportLoading" class="pane-empty">
            <span>{{ $t('workspace.loadingReport') }}</span>
          </div>

          <div v-else class="sections-list">
            <div
              v-for="(section, idx) in outlineSections"
              :key="idx"
              class="report-section-item"
            >
              <div class="section-header-row">
                <span class="section-number mono">{{ String(idx + 1).padStart(2, '0') }}</span>
                <h3 class="section-title">{{ section.title }}</h3>
              </div>
              <div
                v-if="sectionContent[idx + 1]"
                class="generated-content"
                v-html="renderMarkdown(sectionContent[idx + 1])"
              ></div>
              <div v-else class="section-pending">{{ $t('workspace.sectionPending') }}</div>
            </div>
            <div v-if="outlineSections.length === 0" class="pane-empty">
              <span>{{ $t('workspace.emptyReport') }}</span>
            </div>
          </div>
        </div>
      </section>

      <!-- CHAT -->
      <section class="chat-pane">
        <div class="chat-header">
          <div class="chat-avatar">R</div>
          <div class="chat-header-text">
            <span class="chat-title">{{ $t('workspace.chatTitle') }}</span>
            <span class="chat-subtitle mono">
              {{ selectedSimulationId ? shortId(selectedSimulationId) : $t('workspace.chatNoTarget') }}
            </span>
          </div>
          <button
            class="chat-clear"
            :disabled="!chatHistory.length"
            @click="clearChat"
          >{{ $t('workspace.clearChat') }}</button>
        </div>

        <div class="chat-messages" ref="chatMessagesRef">
          <div v-if="chatHistory.length === 0" class="chat-empty">
            <p>{{ $t('workspace.chatEmpty') }}</p>
          </div>
          <div
            v-for="(msg, idx) in chatHistory"
            :key="idx"
            class="chat-message"
            :class="msg.role"
          >
            <div class="message-avatar">{{ msg.role === 'user' ? 'U' : 'R' }}</div>
            <div class="message-content">
              <div class="message-text" v-html="renderMarkdown(msg.content)"></div>
            </div>
          </div>
          <div v-if="isSending" class="chat-message assistant">
            <div class="message-avatar">R</div>
            <div class="message-content">
              <div class="typing-indicator"><span></span><span></span><span></span></div>
            </div>
          </div>
        </div>

        <div class="chat-input-area">
          <textarea
            v-model="chatInput"
            class="chat-input"
            rows="1"
            :placeholder="$t('workspace.chatPlaceholder')"
            :disabled="!selectedSimulationId || isSending"
            @keydown.enter.exact.prevent="sendMessage"
          ></textarea>
          <button
            class="send-btn"
            :disabled="!chatInput.trim() || !selectedSimulationId || isSending"
            @click="sendMessage"
          >
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="22" y1="2" x2="11" y2="13"></line>
              <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
            </svg>
          </button>
        </div>
      </section>
    </main>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import LanguageSwitcher from '../components/LanguageSwitcher.vue'
import { setProjectTitle } from '../utils/pageTitle'
import { renderMarkdown } from '../utils/markdown'
import { getProject } from '../api/graph'
import { listSimulations } from '../api/simulation'
import { listReports, getReport, getReportSections, chatWithReport } from '../api/report'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const projectId = route.params.projectId

const project = ref(null)
const simulations = ref([])
const reports = ref([])
const loading = ref(false)

const selectedReportId = ref(null)
const selectedReport = ref(null)
const sectionContent = ref({})
const reportLoading = ref(false)

// Chat, per report. Keeps a report's thread when switching away and back.
const chatByReport = ref({})
const chatInput = ref('')
const isSending = ref(false)
const chatMessagesRef = ref(null)

const shortId = (id) => (id ? String(id).slice(0, 18) : '')

const reportsBySimulation = computed(() => {
  const grouped = {}
  for (const report of reports.value) {
    if (!grouped[report.simulation_id]) grouped[report.simulation_id] = []
    grouped[report.simulation_id].push(report)
  }
  return grouped
})

const outlineSections = computed(() => selectedReport.value?.outline?.sections || [])
const selectedSimulationId = computed(() => selectedReport.value?.simulation_id || null)
const chatHistory = computed(() => chatByReport.value[selectedReportId.value] || [])

// Where this workspace was opened from, stamped into the query by the report
// and interview views. A workspace reached any other way - a bookmark, a pasted
// link - falls back to the project's step 1, which is always loadable.
const FROM_ROUTES = { Report: 'reportId', Interaction: 'reportId' }

const backTarget = computed(() => {
  const { from, fromId } = route.query
  if (from && fromId && FROM_ROUTES[from]) {
    return { name: from, params: { [FROM_ROUTES[from]]: fromId } }
  }
  if (project.value?.project_id) {
    return { name: 'Process', params: { projectId: project.value.project_id } }
  }
  return '/'
})

const backLabel = computed(() => {
  const { from } = route.query
  if (from === 'Report') return t('nav.backToStep', { step: t('main.stepNames[3]') })
  if (from === 'Interaction') return t('nav.backToStep', { step: t('main.stepNames[4]') })
  if (project.value?.project_id) return t('nav.backToStep', { step: t('main.stepNames[0]') })
  return t('nav.projects')
})

const goBack = () => router.push(backTarget.value)

watch(() => project.value?.name, (name) => setProjectTitle(name), { immediate: true })

const goToGraph = () => {
  if (project.value?.project_id) {
    router.push({ name: 'Process', params: { projectId: project.value.project_id } })
  }
}

const goToSimulation = (sim) => {
  router.push({ name: 'Simulation', params: { simulationId: sim.simulation_id } })
}

const selectReport = async (reportId) => {
  if (reportId === selectedReportId.value) return

  selectedReportId.value = reportId
  selectedReport.value = null
  sectionContent.value = {}
  reportLoading.value = true

  try {
    const [detail, sections] = await Promise.all([
      getReport(reportId),
      getReportSections(reportId)
    ])

    if (detail.success) selectedReport.value = detail.data

    if (sections.success) {
      const map = {}
      for (const section of sections.data.sections || []) {
        map[section.section_index] = section.content
      }
      sectionContent.value = map
    }
  } finally {
    reportLoading.value = false
  }
}

const clearChat = () => {
  if (selectedReportId.value) {
    chatByReport.value = { ...chatByReport.value, [selectedReportId.value]: [] }
  }
}

const pushMessage = (role, content) => {
  const reportId = selectedReportId.value
  const thread = [...(chatByReport.value[reportId] || []), { role, content }]
  chatByReport.value = { ...chatByReport.value, [reportId]: thread }
  nextTick(() => {
    if (chatMessagesRef.value) {
      chatMessagesRef.value.scrollTop = chatMessagesRef.value.scrollHeight
    }
  })
}

const sendMessage = async () => {
  const message = chatInput.value.trim()
  if (!message || isSending.value || !selectedSimulationId.value) return

  chatInput.value = ''
  // The history the agent sees excludes the message being asked right now.
  const historyForApi = chatHistory.value.slice(-10)
  pushMessage('user', message)
  isSending.value = true

  try {
    const res = await chatWithReport({
      simulation_id: selectedSimulationId.value,
      message,
      chat_history: historyForApi
    })

    if (res.success && res.data) {
      pushMessage('assistant', res.data.response || res.data.answer || t('workspace.noResponse'))
    } else {
      throw new Error(res.error || t('workspace.requestFailed'))
    }
  } catch (err) {
    pushMessage('assistant', t('workspace.chatError', { error: err.message }))
  } finally {
    isSending.value = false
  }
}

onMounted(async () => {
  loading.value = true
  try {
    const [projectRes, simsRes, reportsRes] = await Promise.all([
      getProject(projectId),
      listSimulations(projectId),
      listReports()
    ])

    if (projectRes.success) project.value = projectRes.data
    if (simsRes.success) simulations.value = simsRes.data || []

    // /api/report/list has no project filter, so keep only this project's reports.
    if (reportsRes.success) {
      const simIds = new Set(simulations.value.map(s => s.simulation_id))
      reports.value = (reportsRes.data || []).filter(r => simIds.has(r.simulation_id))
    }

    // Open the newest report so the workspace lands on content, not an empty pane.
    if (reports.value.length > 0) {
      await selectReport(reports.value[0].report_id)
    }
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.workspace-view {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: var(--white);
  overflow: hidden;
  font-family: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
}

.mono {
  font-family: 'JetBrains Mono', 'SF Mono', Monaco, Consolas, monospace;
}

/* Header */
.app-header {
  height: 60px;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  background: var(--white);
  color: var(--ink);
  position: relative;
  z-index: 100;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

/* Matches the back control in AppHeader so the affordance is the same shape
   wherever the user meets it. */
.back-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--border-strong);
  background: var(--white);
  padding: 6px 12px;
  font-family: var(--font-sans);
  font-size: 12px;
  font-weight: 600;
  color: var(--ink);
  border-radius: 4px;
  white-space: nowrap;
  flex-shrink: 0;
  transition: background 0.2s, border-color 0.2s;
}

.back-btn:hover {
  background: var(--surface-2);
  border-color: var(--ink);
}

.back-arrow {
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 1;
}

.brand {
  background: none;
  border: none;
  padding: 4px;
  color: inherit;
  font-family: 'JetBrains Mono', monospace;
  font-weight: 800;
  font-size: 18px;
  letter-spacing: 1px;
  cursor: pointer;
}

@media (max-width: 600px) {
  .back-label {
    display: none;
  }
}

.header-center {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
}

.project-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--muted);
}

.header-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.header-divider {
  width: 1px;
  height: 14px;
  background: var(--border);
}

.header-btn {
  border: 1px solid var(--border);
  background: var(--white);
  border-radius: 6px;
  padding: 6px 14px;
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-2);
  cursor: pointer;
  transition: all 0.2s;
}

.header-btn:hover:not(:disabled) {
  background: var(--surface);
  border-color: var(--border-strong);
}

.header-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.status-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--muted);
  font-weight: 500;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--border-strong);
}

.status-indicator.ready .dot { background: #4CAF50; }
.status-indicator.processing .dot { background: #FF9800; animation: pulse 1s infinite; }

@keyframes pulse { 50% { opacity: 0.5; } }

/* Layout */
.content-area {
  flex: 1;
  display: flex;
  overflow: hidden;
}

/* Sidebar */
.sidebar {
  width: 260px;
  min-width: 260px;
  border-right: 1px solid var(--border);
  background: var(--surface);
  overflow-y: auto;
  padding: 20px 16px;
  display: flex;
  flex-direction: column;
  gap: 28px;
}

.sidebar-label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  font-weight: 700;
  color: var(--muted-soft);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 10px;
}

.count {
  background: var(--border);
  color: var(--muted);
  border-radius: 10px;
  padding: 1px 7px;
  font-size: 12px;
}

.project-requirement {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ink-3);
}

.sidebar-empty,
.report-empty {
  font-size: 12px;
  color: var(--muted-soft);
  padding: 6px 0;
}

.sim-group {
  margin-bottom: 14px;
}

.sim-row {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 10px;
  background: var(--white);
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.2s;
}

.sim-row:hover {
  border-color: var(--border-strong);
}

.sim-id {
  font-size: 12px;
  color: var(--ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sim-status {
  font-size: 12px;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.03em;
  flex-shrink: 0;
}

.sim-status.completed { color: #4CAF50; }
.sim-status.running { color: #FF9800; }
.sim-status.failed { color: #F44336; }

.report-list {
  padding-left: 10px;
  margin-top: 4px;
  border-left: 1px solid var(--border);
}

.report-row {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 8px;
  background: transparent;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  text-align: left;
  transition: background 0.15s;
}

.report-row:hover {
  background: var(--surface-3);
}

.report-row.active {
  background: var(--ink);
}

.report-row.active .report-title {
  color: var(--white);
}

.report-dot {
  width: 6px;
  height: 6px;
  min-width: 6px;
  border-radius: 50%;
  background: var(--border-strong);
}

.report-dot.completed { background: #4CAF50; }
.report-dot.generating { background: #FF9800; }
.report-dot.failed { background: #F44336; }

.report-title {
  font-size: 12px;
  color: var(--ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Report pane */
.report-pane {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  background: var(--white);
  padding: 30px 50px 60px;
}

.report-content-wrapper {
  max-width: 800px;
  margin: 0 auto;
}

.report-header-block {
  margin-bottom: 30px;
}

.report-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
}

.report-tag {
  background: var(--black);
  color: var(--white);
  font-size: 12px;
  font-weight: 700;
  padding: 4px 8px;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}

.report-id {
  font-size: 12px;
  color: var(--muted-soft);
}

.main-title {
  font-family: 'Times New Roman', Times, serif;
  font-size: 36px;
  font-weight: 700;
  color: var(--black);
  line-height: 1.2;
  margin: 0 0 16px 0;
  letter-spacing: -0.02em;
}

.sub-title {
  font-family: 'Times New Roman', Times, serif;
  font-size: 16px;
  color: var(--muted);
  font-style: italic;
  line-height: 1.6;
  margin: 0 0 24px 0;
}

.title-divider {
  height: 1px;
  background: var(--border);
}

.sections-list {
  display: flex;
  flex-direction: column;
  gap: 32px;
}

.section-header-row {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 12px;
}

.section-number {
  font-size: 16px;
  color: var(--muted-soft);
  font-weight: 500;
}

.section-title {
  font-family: 'Times New Roman', Times, serif;
  font-size: 24px;
  font-weight: 600;
  color: var(--black);
  margin: 0;
}

.section-pending {
  padding-left: 28px;
  font-size: 13px;
  color: var(--muted-soft);
}

.generated-content {
  padding-left: 28px;
  font-family: 'Inter', 'Noto Sans SC', system-ui, sans-serif;
  font-size: 14px;
  line-height: 1.8;
  color: var(--ink-2);
}

.generated-content :deep(.md-h2),
.generated-content :deep(.md-h3),
.generated-content :deep(.md-h4) {
  font-family: 'Times New Roman', Times, serif;
  color: var(--black);
  margin: 1.5em 0 0.8em;
  font-weight: 700;
}

.generated-content :deep(.md-h2) { font-size: 20px; }
.generated-content :deep(.md-h3) { font-size: 18px; }
.generated-content :deep(.md-h4) { font-size: 16px; }

.generated-content :deep(.md-ul),
.generated-content :deep(.md-ol) {
  padding-left: 20px;
  margin-bottom: 1em;
}

.generated-content :deep(.md-li) { margin-bottom: 0.5em; }

.generated-content :deep(.md-quote) {
  border-left: 3px solid var(--border);
  padding-left: 16px;
  margin: 1.5em 0;
  color: var(--muted);
  font-style: italic;
}

.generated-content :deep(.code-block) {
  background: var(--surface);
  padding: 12px;
  border-radius: 6px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  overflow-x: auto;
  border: 1px solid var(--border);
}

.generated-content :deep(strong) {
  font-weight: 600;
  color: var(--black);
}

.pane-empty {
  height: 100%;
  min-height: 160px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--muted-soft);
  font-size: 14px;
}

/* Below this the three columns stop fitting; the shell scrolls as one column. */
@media (max-width: 1100px) {
  .workspace-view {
    height: auto;
    min-height: 100vh;
    overflow: visible;
  }

  .content-area {
    flex-direction: column;
    overflow: visible;
  }

  .sidebar,
  .chat-pane {
    width: 100%;
    min-width: 0;
    border-right: none;
    border-left: none;
    border-bottom: 1px solid var(--border);
  }

  .chat-pane {
    border-top: 1px solid var(--border);
    border-bottom: none;
    height: 70vh;
  }

  .report-pane {
    padding: 24px 20px 40px;
  }
}

/* Chat pane */
.chat-pane {
  width: 380px;
  min-width: 320px;
  border-left: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  background: var(--white);
  overflow: hidden;
}

.chat-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--border);
  background: linear-gradient(180deg, var(--white) 0%, #FAFBFC 100%);
}

.chat-avatar {
  width: 36px;
  height: 36px;
  min-width: 36px;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--ink) 0%, var(--ink-2) 100%);
  color: var(--white);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 600;
}

.chat-header-text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.chat-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}

.chat-subtitle {
  font-size: 12px;
  color: var(--muted-soft);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat-clear {
  border: 1px solid var(--border);
  background: var(--white);
  border-radius: 4px;
  padding: 4px 10px;
  font-size: 12px;
  color: var(--muted);
  cursor: pointer;
}

.chat-clear:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 18px 16px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.chat-empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--muted-soft);
  font-size: 13px;
  text-align: center;
  line-height: 1.6;
}

.chat-message {
  display: flex;
  gap: 10px;
}

.message-avatar {
  width: 28px;
  height: 28px;
  min-width: 28px;
  border-radius: 50%;
  background: var(--surface-3);
  color: var(--ink-2);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
}

.chat-message.user .message-avatar {
  background: var(--ink);
  color: var(--white);
}

.message-content {
  flex: 1;
  min-width: 0;
}

.message-text {
  font-size: 13px;
  line-height: 1.7;
  color: var(--ink-2);
  word-break: break-word;
}

.message-text :deep(.md-p) { margin: 0 0 0.6em; }
.message-text :deep(.md-ul),
.message-text :deep(.md-ol) { padding-left: 18px; margin: 0 0 0.6em; }
.message-text :deep(.inline-code) {
  background: var(--surface-2);
  padding: 1px 4px;
  border-radius: 3px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
}

.typing-indicator {
  display: flex;
  gap: 4px;
  padding-top: 8px;
}

.typing-indicator span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--border-strong);
  animation: blink 1.2s infinite;
}

.typing-indicator span:nth-child(2) { animation-delay: 0.2s; }
.typing-indicator span:nth-child(3) { animation-delay: 0.4s; }

@keyframes blink {
  0%, 60%, 100% { opacity: 0.3; }
  30% { opacity: 1; }
}

.chat-input-area {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid var(--border);
}

.chat-input {
  flex: 1;
  resize: none;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 13px;
  font-family: inherit;
  line-height: 1.5;
  max-height: 120px;
  outline: none;
}

.chat-input:focus {
  border-color: var(--ink);
}

.chat-input:disabled {
  background: var(--surface);
  cursor: not-allowed;
}

.send-btn {
  width: 38px;
  height: 38px;
  border: none;
  border-radius: 8px;
  background: var(--ink);
  color: var(--white);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  flex-shrink: 0;
}

.send-btn:disabled {
  background: var(--border);
  color: var(--muted-soft);
  cursor: not-allowed;
}
</style>
